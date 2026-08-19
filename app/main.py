from __future__ import annotations
import argparse,sys,os,json
from pathlib import Path
from app.version import *
from app.config import load_config
from app.discovery import discover,iter_discover
from app.pipeline import analyze_file
from app.publication import PublicationRecoveryError,recover_interrupted_publications
from app.utils import run_id,local_iso
from reporting.markdown_report import write_md
from reporting.json_report import JsonLinesWriter,write_json_report
from reporting.collection_index import write_collection_index


def _create_report_directory(reports_root:Path,rid:str):
    for number in range(1,10000):
        actual=rid if number==1 else f'{rid} {number}'
        candidate=reports_root/actual
        try:
            candidate.mkdir(parents=True,exist_ok=False)
            return candidate,actual
        except FileExistsError:
            continue
    raise RuntimeError('REPORT_DIRECTORY_COLLISION_EXHAUSTED')

def main(argv=None):
    ap=argparse.ArgumentParser();ap.add_argument('--config');ap.add_argument('--ffmpeg');ap.add_argument('--ffprobe');ap.add_argument('--mpg123');ap.add_argument('inputs',nargs='*');args=ap.parse_args(argv)
    root=Path(__file__).resolve().parents[1];cfg=load_config(Path(args.config) if args.config else root/'config.toml')
    try:publication_recovery=recover_interrupted_publications(root/'state'/'publication_journal')
    except PublicationRecoveryError as e:
        print(f'LossyDoctor: recuperación de publicación bloqueada: {e}');return 1
    ffmpeg=args.ffmpeg or os.environ.get('LOSSYDOCTOR_FFMPEG') or 'ffmpeg';ffprobe=args.ffprobe or os.environ.get('LOSSYDOCTOR_FFPROBE') or 'ffprobe';mpg123=args.mpg123 or os.environ.get('LOSSYDOCTOR_MPG123');mpg123_trust=os.environ.get('LOSSYDOCTOR_MPG123_TRUST','PINNED_SHA256')
    if not args.inputs:
        print('LossyDoctor: arrastre archivos/carpetas sobre LossyDoctor.bat');return 2
    print('LossyDoctor: preparando el recorrido de archivos...',flush=True)
    reports_root=root/cfg['reports']['root'];outdir=reports_root
    stream=None;events=None
    progress_active=False
    try:
        rid=run_id();started_at=local_iso();outdir,actual_rid=_create_report_directory(reports_root,rid)
        stream=JsonLinesWriter(outdir/f'LossyDoctor_{actual_rid}.files.jsonl')
        events=JsonLinesWriter(outdir/f'LossyDoctor_{actual_rid}.events.jsonl');events.write({'type':'run_started','time':started_at,'run_id':actual_rid})
        discskip=[];metrics={};summary={'discovered':0,'entries_scanned':0,'processed':0,'ok':0,'with_findings':0,'skipped':0,'failed':0,'repaired_outputs_created':0,'lossless_outputs_created':0,'outputs_reused':0,'candidates_rejected':0}
        def progress(discovery):
            if discovery['entries_scanned']%10000==0:
                events.write({'type':'discovery_progress','time':local_iso(),'entries_scanned':discovery['entries_scanned'],'candidates_discovered':summary['discovered'],'processed':summary['processed'],'failed':summary['failed']})
                print(f'LossyDoctor: progreso entradas={discovery["entries_scanned"]} candidatos={summary["discovered"]} procesados={summary["processed"]} fallidos={summary["failed"]}',flush=True)
        for number,p in enumerate(iter_discover(args.inputs,root,cfg['app']['recursive'],cfg['app']['follow_symlinks'],discskip,metrics,progress),1):
            summary['discovered']+=1
            try:row=analyze_file(p,cfg,root,ffmpeg,ffprobe,mpg123,mpg123_trust).to_dict()
            except Exception as e:
                from app.models import Analysis
                a=Analysis(str(p),p.name,{});a.run_status='FAILED';a.error=f'{type(e).__name__}: {e}';a.final_status=['ANALYSIS_FAILED'];row=a.to_dict()
            stream.write(row)
            events.write({'type':'file_finished','time':local_iso(),'input_path':row['input_path'],'run_status':row['run_status'],'final_status':row.get('final_status',[])})
            if row['run_status']!='SKIPPED_UNSUPPORTED':
                summary['processed']+=1;summary['failed']+=row['run_status']=='FAILED';summary['ok']+=row.get('final_status')==['OK'];summary['with_findings']+=row['run_status'] not in ('SUCCESS','FAILED') and row.get('final_status')!=['OK']
                summary['repaired_outputs_created']+=sum(e.get('status')=='CREATED' for e in row.get('repair_execution',[]));summary['outputs_reused']+=sum(e.get('status')=='REUSED' for e in row.get('repair_execution',[]));summary['candidates_rejected']+=sum(e.get('status')=='REJECTED' for e in row.get('repair_execution',[]))
                for export in row.get('lossless_export',[]):
                    summary['lossless_outputs_created']+=sum(o.get('status')=='CREATED' for o in export.get('outputs',[]));summary['outputs_reused']+=sum(o.get('status')=='REUSED' for o in export.get('outputs',[]));summary['candidates_rejected']+=export.get('status')=='REJECTED'
            else:summary['skipped']+=1
            print(f'\rLossyDoctor: procesados={summary["processed"]} hallazgos={summary["with_findings"]} fallidos={summary["failed"]}',end='',flush=True)
            progress_active=True
        stream.close();summary['entries_scanned']=metrics.get('entries_scanned',summary['discovered']);summary['discovered']+=len(discskip);summary['skipped']+=len(discskip);events.write({'type':'run_finished','time':local_iso(),'run_id':actual_rid,'summary':summary});events.close()
        run={'app':'LossyDoctor','app_version':APP_VERSION,'analysis_schema':ANALYSIS_SCHEMA,'config_schema':CONFIG_SCHEMA,'report_schema':REPORT_SCHEMA,'run_id':actual_rid,'started_at':started_at,'configuration':cfg,'publication_recovery':publication_recovery,'summary':summary,'discovery_skipped':discskip,'files':[],'file_details_ndjson':f'LossyDoctor_{actual_rid}.files.jsonl','event_log_ndjson':f'LossyDoctor_{actual_rid}.events.jsonl'}
        if cfg['reports']['json']:write_json_report(outdir/f'LossyDoctor_{actual_rid}.json',run)
        if cfg['reports']['markdown']:write_md(outdir/f'LossyDoctor_{actual_rid}.md',run)
        write_collection_index(outdir/'README.md',run)
    except OSError as e:
        print(f'LossyDoctor: no se pudieron publicar los reportes en {outdir}: {type(e).__name__}: {e}')
        return 1
    finally:
        if events is not None:events.close()
        if stream is not None:stream.close()
    if progress_active:print()
    print(f'LossyDoctor {APP_VERSION}: procesados={summary["processed"]} correctos={summary["ok"]} con_hallazgos={summary["with_findings"]} fallidos={summary["failed"]} reparados={summary["repaired_outputs_created"]} sin_pérdida={summary["lossless_outputs_created"]} reutilizados={summary["outputs_reused"]}')
    print(f'Informe: {outdir}')
    return 0 if summary['failed']==0 else 1
if __name__=='__main__':raise SystemExit(main())
