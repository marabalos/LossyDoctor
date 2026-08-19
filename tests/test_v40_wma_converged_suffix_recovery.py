from __future__ import annotations
import copy,hashlib,json,os,shutil,subprocess,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
from app.config import load_config
from app.pipeline import analyze_file
from app.version import APP_VERSION,POLICY_VERSION
from formats.asf_wma import analyze
from reporting.markdown_report import write_md

BASE=ROOT/'samples/asf_wma_recovery_v40'
OLD=ROOT/'samples/asf_wma_decoder_convergence_v39'
MAN=json.loads((ROOT/'samples/asf_wma_recovery_v40_manifest.json').read_text(encoding='utf-8'))
CFG=load_config(ROOT/'config.toml'); CFG_AUDIT=copy.deepcopy(CFG); CFG_AUDIT['lossless_recovery']['enabled']=False
FFMPEG=os.environ.get('LOSSYDOCTOR_FFMPEG') or shutil.which('ffmpeg'); FFPROBE=os.environ.get('LOSSYDOCTOR_FFPROBE') or shutil.which('ffprobe')
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def decode_raw(src:Path,dst:Path):
    r=subprocess.run([FFMPEG,'-v','error','-i',str(src),'-map','0:a:0','-f','s32le','-acodec','pcm_s32le','-y',str(dst)],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    if r.returncode: raise AssertionError(r.stderr.decode(errors='replace'))

def le_output(a):
    if not a.lossless_export:return None
    outs=a.lossless_export[0].get('outputs') or []
    return outs[0] if outs else None

@unittest.skipUnless(FFMPEG and FFPROBE,'ffmpeg/ffprobe required')
class WmaConvergedSuffixRecoveryV40(unittest.TestCase):
    def test_version_manifest_hashes_and_binary_distinctness(self):
        self.assertGreaterEqual(tuple(map(int,APP_VERSION.split('.'))),(0,40,0));self.assertEqual(MAN['app_version'],'0.40.0');self.assertEqual(MAN['policy'],'0.40-wma-converged-suffix-recovery-1')
        seen=set();old={sha(p) for p in OLD.glob('*.wma')}
        for n,c in MAN['cases'].items():
            p=BASE/n;h=sha(p);self.assertEqual(h,c['sha256'],n);self.assertNotIn(h,seen,n);self.assertNotIn(h,old,n);seen.add(h)
            q=analyze(p);self.assertEqual([i.code for i in q['issues']],c['expected_parser_issues'],n)
        self.assertEqual(len(seen),9)

    def test_assessment_controls_positive_plans_and_blocked_negatives(self):
        positive={
          '02_wmav2_single_mid_gap_recover.wma':(1,13,14,2048),
          '03_wmav1_single_mid_gap_recover.wma':(1,13,14,1024),
          '04_wmav2_double_mid_gap_recover.wma':(2,14,15,2048),
          '05_wmav1_double_mid_gap_recover.wma':(2,14,15,1024),
          '06_wmav2_late_gap_recover.wma':(1,41,42,2048),
        }
        for n in MAN['cases']:
            a=analyze_file(BASE/n,CFG_AUDIT,ROOT,FFMPEG,FFPROBE);r=a.format_facts['wma_recovery_assessment']
            if n in positive:
                miss,ctx,first,flen=positive[n];self.assertTrue(r['eligible'],n);self.assertEqual(r['pcm_class'],'WMA_CONVERGED_SUFFIX',n)
                p=r['regions'][0];self.assertEqual((p['missing_media_object_count'],p['context_media_object_number'],p['first_published_media_object_number'],p['decoder_frame_length_samples']),(miss,ctx,first,flen),n)
                self.assertFalse(p['context_audio_published'],n);self.assertFalse(r['full_source_pcm_timeline_claim'],n);self.assertEqual(r['synthesized_missing_span'],'NONE',n)
            else:
                self.assertFalse(r['eligible'],n)
        self.assertEqual(analyze_file(BASE/'00_healthy_wmav2_recovery_control.wma',CFG_AUDIT,ROOT,FFMPEG,FFPROBE).pcm_recovery_class,'NOT_REQUIRED')
        self.assertEqual(analyze_file(BASE/'07_timestamp_jump_report_only.wma',CFG_AUDIT,ROOT,FFMPEG,FFPROBE).pcm_recovery_class,'WMA_RECOVERY_BLOCKED')
        self.assertEqual(analyze_file(BASE/'08_incomplete_media_object_blocked.wma',CFG_AUDIT,ROOT,FFMPEG,FFPROBE).pcm_recovery_class,'WMA_RECOVERY_BLOCKED')

    def test_positive_exports_roundtrip_and_manifest_invariants(self):
        for n in list(MAN['cases'])[2:7]:
            with tempfile.TemporaryDirectory() as td:
                p=Path(td)/n;shutil.copy2(BASE/n,p);a=analyze_file(p,CFG,ROOT,FFMPEG,FFPROBE);o=le_output(a);self.assertIsNotNone(o,n)
                m=o['manifest'];self.assertEqual(m['derivation_kind'],'RECOVERED_WMA_CONVERGED_SUFFIX_LOSSLESS',n);self.assertEqual(m['validation_result'],'PASS',n)
                self.assertEqual(m['region_pcm_sha256'],m['flac_decoded_pcm_sha256'],n);self.assertFalse(m['context_audio_published'],n);self.assertFalse(m['full_source_pcm_timeline_claim'],n)
                self.assertEqual((m['synthesized_missing_span'],m['resampling'],m['channel_remix']),('NONE','NONE','NONE'),n);self.assertFalse(m['source_modified'],n);self.assertFalse(m['wma_media_object_bytes_modified'],n)

    def test_recovered_pcm_equals_control_slice(self):
        cases={
          '02_wmav2_single_mid_gap_recover.wma':('00_healthy_wmav2_recovery_control.wma',11,2048,2),
          '03_wmav1_single_mid_gap_recover.wma':('01_healthy_wmav1_recovery_control.wma',11,1024,1),
          '04_wmav2_double_mid_gap_recover.wma':('00_healthy_wmav2_recovery_control.wma',12,2048,2),
          '05_wmav1_double_mid_gap_recover.wma':('01_healthy_wmav1_recovery_control.wma',12,1024,1),
          '06_wmav2_late_gap_recover.wma':('00_healthy_wmav2_recovery_control.wma',39,2048,2),
        }
        with tempfile.TemporaryDirectory() as td:
            td=Path(td);raw_cache={}
            for n,(healthy,frame,flen,ch) in cases.items():
                if healthy not in raw_cache:
                    r=td/(healthy+'.raw');decode_raw(BASE/healthy,r);raw_cache[healthy]=r.read_bytes()
                p=td/n;shutil.copy2(BASE/n,p);a=analyze_file(p,CFG,ROOT,FFMPEG,FFPROBE);o=le_output(a);rr=td/(n+'.raw');decode_raw(Path(o['output_path']),rr)
                expect=raw_cache[healthy][frame*flen*ch*4:];self.assertEqual(rr.read_bytes(),expect,n)

    def test_report_only_cases_publish_nothing(self):
        for n in ('00_healthy_wmav2_recovery_control.wma','01_healthy_wmav1_recovery_control.wma','07_timestamp_jump_report_only.wma','08_incomplete_media_object_blocked.wma'):
            with tempfile.TemporaryDirectory() as td:
                p=Path(td)/n;shutil.copy2(BASE/n,p);a=analyze_file(p,CFG,ROOT,FFMPEG,FFPROBE);self.assertEqual(a.lossless_export,[],n)

    def test_markdown_exposes_recovery_scope(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'x.wma';shutil.copy2(BASE/'02_wmav2_single_mid_gap_recover.wma',p);a=analyze_file(p,CFG,ROOT,FFMPEG,FFPROBE).to_dict()
            run={'run_id':'v40','started_at':'2026-08-17T14:00:00-03:00','summary':{'discovered':1,'processed':1,'ok':0,'with_findings':1,'skipped':0,'failed':0,'repaired_outputs_created':0,'lossless_outputs_created':1,'outputs_reused':0,'candidates_rejected':0},'files':[a]}
            md=Path(td)/'r.md';write_md(md,run);txt=md.read_text(encoding='utf-8')
            self.assertIn('Evaluación de recuperación demostrada de sufijo convergente ASF/WMA',txt);self.assertIn('RECOVERED_WMA_CONVERGED_SUFFIX_LOSSLESS',txt);self.assertIn('WMA_ASF_CONVERGED_SUFFIX_RECOVERY_AUTHORITY',txt);self.assertIn('afirmación de línea de tiempo PCM de la fuente completa `False`',txt)

    def test_two_run_creation_then_reuse_exactly_five(self):
        with tempfile.TemporaryDirectory() as td:
            td=Path(td)
            for p in BASE.glob('*.wma'):shutil.copy2(p,td/p.name)
            def run_once():return [analyze_file(td/n,CFG,ROOT,FFMPEG,FFPROBE) for n in sorted(MAN['cases'])]
            r1=run_once();r2=run_once()
            self.assertEqual((sum(a.run_status=='SUCCESS' for a in r1),sum(bool(a.issues) for a in r1)),(2,7))
            self.assertEqual(sum((x.get('status')=='CREATED') for a in r1 for x in a.lossless_export),5)
            self.assertEqual(sum((x.get('status')=='REUSED') for a in r2 for x in a.lossless_export),5)
            self.assertEqual(len(list(td.glob('*[[]recovered-wma-converged-suffix[]].flac'))),5);self.assertEqual(len(list(td.glob('*.lossydoctor-manifest.json'))),5)

if __name__=='__main__':unittest.main()
