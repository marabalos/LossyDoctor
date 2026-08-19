from __future__ import annotations
from pathlib import Path
import subprocess, json, hashlib, tempfile, os
from functools import lru_cache

@lru_cache(maxsize=16)
def version(exe):
    try:
        r=subprocess.run([exe,'-version'],capture_output=True,text=True,timeout=10);return (r.stdout or r.stderr).splitlines()[0]
    except Exception:return 'unknown'

@lru_cache(maxsize=16)
def binary_sha256(exe):
    try:
        p=Path(exe).resolve(); h=hashlib.sha256()
        with p.open('rb') as f:
            for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
        return h.hexdigest()
    except Exception:return None

def canonical_pcm_profile(exe,sample_rate=None,channels=None):
    return {
        'decoder':'ffmpeg','decoder_version':version(exe),'decoder_binary_sha256':binary_sha256(exe),
        'sample_format':'s32le','output_sample_format':'s32le','bits_per_sample':32,
        'sample_rate':sample_rate,'sample_rate_policy':'native','channels':channels,'channel_policy':'native','channel_layout_policy':'preserve',
        'presentation_window':'canonical','filters':'none','normalization':'none','gain_policy':'none','replaygain':'disabled','resampling':'none','dither':'none'
    }

def ffprobe(path:Path,exe:str,timeout=300):
    cmd=[exe,'-v','error','-show_format','-show_streams','-of','json',str(path)]
    try:
        r=subprocess.run(cmd,capture_output=True,text=True,timeout=timeout)
        data=json.loads(r.stdout) if r.returncode==0 and r.stdout.strip() else {}
        aud=[s for s in data.get('streams',[]) if s.get('codec_type')=='audio']
        return {'passed':r.returncode==0,'return_code':r.returncode,'stderr_lines':r.stderr.splitlines()[-50:],'raw':data,'audio_streams':aud}
    except Exception as e:return {'passed':False,'return_code':None,'stderr_lines':[str(e)],'raw':{},'audio_streams':[]}

def decode(path:Path,exe:str,mode='PLAYBACK_DECODE',timeout=300):
    cmd=[exe,'-hide_banner','-nostdin','-v','error']
    if mode=='STRICT_DECODE':cmd+=['-err_detect','explode']
    elif mode=='SALVAGE_DECODE':cmd+=['-err_detect','ignore_err']
    cmd+=['-i',str(path),'-map','0:a:0','-f','null','-']
    try:
        r=subprocess.run(cmd,capture_output=True,text=True,timeout=timeout);lines=r.stderr.splitlines()[-80:]
        completed=r.returncode==0; passed=completed and (mode!='STRICT_DECODE' or not lines)
        return {'attempted':True,'mode':mode,'passed':passed,'completed':completed,'error_output_present':bool(lines),'return_code':r.returncode,'stderr_lines':lines}
    except subprocess.TimeoutExpired:return {'attempted':True,'mode':mode,'passed':False,'completed':False,'error_output_present':True,'return_code':None,'stderr_lines':['timeout']}

def decode_raw(path:Path,exe:str,sr:int|None=None,channels:int|None=None,timeout=300):
    cmd=[exe,'-hide_banner','-nostdin','-v','error','-i',str(path),'-map','0:a:0','-f','s32le','-acodec','pcm_s32le','-']
    r=subprocess.run(cmd,capture_output=True,timeout=timeout)
    return {'passed':r.returncode==0,'return_code':r.returncode,'stderr':r.stderr.decode('utf-8','replace'),'raw':r.stdout}

def raw_to_flac(raw:bytes,out:Path,exe:str,sr:int,channels:int,timeout=300):
    cmd=[exe,'-y','-hide_banner','-nostdin','-v','error','-f','s32le','-ar',str(sr),'-ac',str(channels),'-i','-','-c:a','flac','-bits_per_raw_sample','32','-strict','experimental',str(out)]
    r=subprocess.run(cmd,input=raw,capture_output=True,timeout=timeout)
    return {'passed':r.returncode==0,'return_code':r.returncode,'stderr':r.stderr.decode('utf-8','replace')}

def decode_to_raw_file(path:Path,out:Path,exe:str,timeout=300,skip_manual=False):
    cmd=[exe,'-y','-hide_banner','-nostdin','-v','error']
    if skip_manual: cmd += ['-flags2','+skip_manual']
    cmd += ['-i',str(path),'-map','0:a:0','-f','s32le','-acodec','pcm_s32le',str(out)]
    r=subprocess.run(cmd,capture_output=True,timeout=timeout)
    return {'passed':r.returncode==0,'return_code':r.returncode,'stderr':r.stderr.decode('utf-8','replace')}

def raw_file_to_flac(raw:Path,out:Path,exe:str,sr:int,channels:int,timeout=300):
    cmd=[exe,'-y','-hide_banner','-nostdin','-v','error','-f','s32le','-ar',str(sr),'-ac',str(channels),'-i',str(raw),'-c:a','flac','-bits_per_raw_sample','32','-strict','experimental',str(out)]
    r=subprocess.run(cmd,capture_output=True,timeout=timeout)
    return {'passed':r.returncode==0,'return_code':r.returncode,'stderr':r.stderr.decode('utf-8','replace')}


@lru_cache(maxsize=16)
def mpg123_version(exe):
    try:
        r=subprocess.run([exe,'--version'],capture_output=True,text=True,timeout=10)
        lines=(r.stdout or r.stderr).splitlines()
        return lines[0].strip() if lines else 'unknown'
    except Exception:return 'unknown'

def _decode_hash_to_temp(cmd,timeout=300):
    """Decodifica a un stream crudo temporal sin retener PCM en RAM."""
    try:
        with tempfile.TemporaryFile() as out:
            r=subprocess.run(cmd,stdout=out,stderr=subprocess.PIPE,timeout=timeout)
            out.flush(); size=out.tell(); out.seek(0); h=hashlib.sha256()
            for b in iter(lambda:out.read(1024*1024),b''):h.update(b)
        lines=r.stderr.decode('utf-8','replace').splitlines()[-80:]
        return {'completed':r.returncode==0,'passed':r.returncode==0,'return_code':r.returncode,'stderr_lines':lines,'output_bytes':size,'pcm_sha256':h.hexdigest() if size else None}
    except subprocess.TimeoutExpired:
        return {'completed':False,'passed':False,'return_code':None,'stderr_lines':['timeout'],'output_bytes':0,'pcm_sha256':None}
    except Exception as e:
        return {'completed':False,'passed':False,'return_code':None,'stderr_lines':[f'{type(e).__name__}: {e}'],'output_bytes':0,'pcm_sha256':None}

def ffmpeg_evidence_decode(path:Path,exe:str,channels:int|None,timeout=300):
    cmd=[exe,'-hide_banner','-nostdin','-v','error','-i',str(path),'-map','0:a:0','-f','s32le','-acodec','pcm_s32le','-']
    q=_decode_hash_to_temp(cmd,timeout);q.update({'attempted':True,'decoder':'ffmpeg','decoder_version':version(exe),'decoder_binary_sha256':binary_sha256(exe),'encoding':'s32le','bytes_per_sample':4})
    q['sample_frames']=(q['output_bytes']//(4*channels)) if channels and q['output_bytes']%(4*channels)==0 else None
    return q

def mpg123_evidence_decode(path:Path,exe:str|None,channels:int|None,timeout=300,trust='PINNED_SHA256'):
    if not exe:
        return {'attempted':False,'available':False,'decoder':'mpg123','passed':False,'completed':False,'reason':'el ejecutable mpg123 no está configurado','supply_chain_trust':trust}
    # mpg123 -s emite PCM crudo en el orden nativo; Windows x64 usa little-endian.
    cmd=[exe,'-q','-s','-e','s32','--',str(path)]
    q=_decode_hash_to_temp(cmd,timeout);q.update({'attempted':True,'available':True,'decoder':'mpg123','decoder_version':mpg123_version(exe),'decoder_binary_sha256':binary_sha256(exe),'encoding':'s32le','bytes_per_sample':4,'supply_chain_trust':trust})
    q['sample_frames']=(q['output_bytes']//(4*channels)) if channels and q['output_bytes']%(4*channels)==0 else None
    return q

def independent_decoder_evidence(path:Path,ffmpeg_exe:str,mpg123_exe:str|None,channels:int|None,timeout=300,mpg123_trust='PINNED_SHA256'):
    ff=ffmpeg_evidence_decode(path,ffmpeg_exe,channels,timeout)
    mp=mpg123_evidence_decode(path,mpg123_exe,channels,timeout,mpg123_trust)
    both=bool(ff.get('completed') and mp.get('completed'))
    return {
        'policy':'EVIDENCE_ONLY_NONCANONICAL',
        'canonical_decoder':'ffmpeg',
        'independent_decoder':'mpg123',
        'ffmpeg':ff,'mpg123':mp,
        'agreement':{
            'both_completed':both,
            'completion_equal':bool(ff.get('completed'))==bool(mp.get('completed')) if mp.get('attempted') else None,
            'sample_frame_count_equal':(ff.get('sample_frames')==mp.get('sample_frames')) if both and ff.get('sample_frames') is not None and mp.get('sample_frames') is not None else None,
            'raw_s32_pcm_sha256_equal':(ff.get('pcm_sha256')==mp.get('pcm_sha256')) if both and ff.get('pcm_sha256') and mp.get('pcm_sha256') else None,
            'pcm_hash_interpretation':'informational_only_decoder_synthesis_may_differ',
        }
    }

def _safe_ms(value):
    try:return round(float(value)*1000,6)
    except Exception:return None

def asf_wma_demux_decoder_evidence(path:Path,ffprobe_exe:str,ffmpeg_exe:str,media_objects:dict,preroll_ms:int|float|None,sample_rate:int|None,channels:int|None,timeout=300):
    """Contrasta objetos multimedia ASF/WMA completos con paquetes demux y cuadros decodificados.

    Sólo aporta evidencia. Las marcas temporales del contenedor u objeto nunca se convierten aquí
    en límites de recuperación exactos por muestra. Los hashes comparan la identidad de bytes entre
    el objeto comprimido entregado por el demuxer ASF de FFmpeg y el reconstruido desde payloads ASF autenticados.
    """
    base={
        'policy':'ASF_WMA_DEMUX_DECODER_TIMELINE_EVIDENCE_ONLY',
        'publication_enabled':False,'pcm_sample_exact_claim':False,
        'demuxer':'ffprobe/ffmpeg','decoder':'ffmpeg',
        'packet_probe_passed':False,'frame_probe_passed':False,
        'one_to_one_complete_media_object_mapping':False,
        'all_packet_hashes_equal':False,'all_packet_sizes_equal':False,
        'all_pts_match_media_object_presentation_minus_preroll':False,
        'demux_pts_monotonic':None,'demux_timeline_discontinuities':[],
        'mapping_rows':[],
    }
    packet_cmd=[ffprobe_exe,'-v','error','-select_streams','a:0','-show_packets','-show_data_hash','sha256',
                '-show_entries','packet=pts,pts_time,duration,duration_time,size,pos,flags,data_hash','-of','json',str(path)]
    frame_cmd=[ffprobe_exe,'-v','error','-select_streams','a:0','-show_frames',
               '-show_entries','frame=pts,pts_time,nb_samples,pkt_pos,pkt_size','-of','json',str(path)]
    packets=[];frames=[]
    try:
        r=subprocess.run(packet_cmd,capture_output=True,text=True,timeout=timeout)
        if r.returncode==0 and r.stdout.strip():
            packets=json.loads(r.stdout).get('packets',[]) or [];base['packet_probe_passed']=True
        else:base['packet_probe_stderr']=(r.stderr or '').splitlines()[-50:]
    except Exception as e:base['packet_probe_stderr']=[f'{type(e).__name__}: {e}']
    try:
        r=subprocess.run(frame_cmd,capture_output=True,text=True,timeout=timeout)
        if r.returncode==0 and r.stdout.strip():
            frames=json.loads(r.stdout).get('frames',[]) or [];base['frame_probe_passed']=True
        else:base['frame_probe_stderr']=(r.stderr or '').splitlines()[-50:]
    except Exception as e:base['frame_probe_stderr']=[f'{type(e).__name__}: {e}']

    complete=[x for x in (media_objects.get('media_objects') or []) if x.get('complete') and x.get('assembled_sha256')]
    complete=sorted(complete,key=lambda x:(min(x.get('packet_indices') or [2**63]),x.get('media_object_number') if x.get('media_object_number') is not None else 2**63))
    rows=[]
    for i,(obj,pkt) in enumerate(zip(complete,packets)):
        dh=(pkt.get('data_hash') or '')
        if ':' in dh:dh=dh.split(':',1)[1]
        pts_ms=_safe_ms(pkt.get('pts_time'))
        dur_ms=_safe_ms(pkt.get('duration_time'))
        expected_pts=(obj.get('presentation_time_ms')-(preroll_ms or 0)) if obj.get('presentation_time_ms') is not None and preroll_ms is not None else None
        size=int(pkt['size']) if str(pkt.get('size','')).isdigit() else None
        row={'mapping_index':i,'media_object_number':obj.get('media_object_number'),'media_object_size':obj.get('declared_size'),
             'media_object_sha256':obj.get('assembled_sha256'),'media_object_presentation_time_ms':obj.get('presentation_time_ms'),
             'media_object_number_size_bytes':obj.get('media_object_number_size_bytes'),
             'header_preroll_ms':preroll_ms,'expected_demux_pts_ms':expected_pts,
             'demux_packet_index':i,'demux_packet_pts_ms':pts_ms,'demux_packet_duration_ms':dur_ms,
             'demux_packet_size':size,'demux_packet_pos':int(pkt['pos']) if str(pkt.get('pos','')).lstrip('-').isdigit() else None,
             'demux_packet_sha256':dh or None,
             'size_equal':size==obj.get('declared_size') if size is not None else False,
             'hash_equal':bool(dh and dh==obj.get('assembled_sha256')),
             'pts_preroll_equal':abs(pts_ms-expected_pts)<0.001 if pts_ms is not None and expected_pts is not None else None}
        rows.append(row)
    base['mapping_rows']=rows
    base['complete_media_object_count']=len(complete);base['demux_packet_count']=len(packets)
    base['mapped_row_count']=len(rows)
    base['one_to_one_complete_media_object_mapping']=bool(len(complete)==len(packets) and len(rows)==len(complete) and all(r['hash_equal'] and r['size_equal'] for r in rows))
    base['all_packet_hashes_equal']=bool(rows and len(rows)==len(complete)==len(packets) and all(r['hash_equal'] for r in rows))
    base['all_packet_sizes_equal']=bool(rows and len(rows)==len(complete)==len(packets) and all(r['size_equal'] for r in rows))
    comparable=[r for r in rows if r['pts_preroll_equal'] is not None]
    base['all_pts_match_media_object_presentation_minus_preroll']=bool(comparable and len(comparable)==len(rows) and all(r['pts_preroll_equal'] for r in comparable))

    ppts=[_safe_ms(x.get('pts_time')) for x in packets]
    pdur=[_safe_ms(x.get('duration_time')) for x in packets]
    disc=[];nonmono=[]
    for i in range(len(ppts)-1):
        if ppts[i] is None or ppts[i+1] is None:continue
        step=round(ppts[i+1]-ppts[i],6)
        if step<0:nonmono.append(i+1)
        d=pdur[i]
        # Aquí las marcas ASF/WMA tienen granularidad de milisegundos; se admite un milisegundo de cuantización.
        if d is not None and (step < d-1.001 or step > d+1.001):
            disc.append({'after_demux_packet_index':i,'next_demux_packet_index':i+1,'pts_step_ms':step,'prior_packet_duration_ms':d,'excess_or_deficit_ms':round(step-d,6)})
    base['demux_pts_monotonic']=not bool(nonmono) if ppts else None
    base['demux_pts_nonmonotonic_positions']=nonmono
    base['demux_timeline_discontinuities']=disc
    base['demux_pts_start_ms']=next((x for x in ppts if x is not None),None)
    base['demux_pts_end_ms']=next((x for x in reversed(ppts) if x is not None),None)
    base['demux_duration_min_ms']=min((x for x in pdur if x is not None),default=None)
    base['demux_duration_max_ms']=max((x for x in pdur if x is not None),default=None)

    fpts=[_safe_ms(x.get('pts_time')) for x in frames]
    ns=[]
    pos_to_packet_index={}
    for i,pkt in enumerate(packets):
        pos=pkt.get('pos')
        if pos is not None:pos_to_packet_index[str(pos)]=i
    frame_rows=[]
    for fi,x in enumerate(frames):
        try:nsv=int(x.get('nb_samples'));ns.append(nsv)
        except Exception:nsv=None
        ppos=x.get('pkt_pos')
        frame_rows.append({'frame_index':fi,'pts_ms':_safe_ms(x.get('pts_time')),'nb_samples':nsv,
                           'pkt_pos':int(ppos) if str(ppos or '').lstrip('-').isdigit() else None,
                           'mapped_demux_packet_index':pos_to_packet_index.get(str(ppos)) if ppos is not None else None})
    base['decoded_frame_rows']=frame_rows
    base['decoded_frame_count']=len(frames);base['decoded_sample_frames_from_ffprobe']=sum(ns) if ns else 0
    base['decoded_frame_nb_samples_values']=sorted(set(ns))
    base['timestamped_decoded_frame_count']=sum(x is not None for x in fpts)
    base['untimestamped_flush_frame_count']=sum(x is None for x in fpts)
    base['first_decoded_frame_pts_ms']=next((x for x in fpts if x is not None),None)
    base['last_decoded_frame_pts_ms']=next((x for x in reversed(fpts) if x is not None),None)
    first_pts=base['first_decoded_frame_pts_ms']
    first_idx=next((i for i,x in enumerate(ppts) if x is not None and first_pts is not None and abs(x-first_pts)<0.001),None)
    base['first_timestamped_decoder_output_demux_packet_index']=first_idx
    base['observed_startup_demux_packets_before_first_timestamped_output']=first_idx
    # Cuenta cuadros del decodificador asociados a una misma posición de paquete. La reutilización al final
    # es evidencia del vaciado del decodificador y deliberadamente no se trata como corrupción.
    pos_counts={}
    for x in frames:
        pos=x.get('pkt_pos')
        if pos is not None:pos_counts[str(pos)]=pos_counts.get(str(pos),0)+1
    base['decoded_frame_packet_position_reuse_count']=sum(1 for c in pos_counts.values() if c>1)
    base['decoded_frame_max_frames_per_packet_position']=max(pos_counts.values(),default=0)

    dec=ffmpeg_evidence_decode(path,ffmpeg_exe,channels,timeout)
    base['decoder_evidence']=dec
    base['decoder_output_sample_frames']=dec.get('sample_frames')
    if sample_rate and dec.get('sample_frames') is not None:
        base['decoder_output_duration_ms']=round(dec['sample_frames']*1000/sample_rate,9)
    else:base['decoder_output_duration_ms']=None
    if sample_rate and base['decoded_sample_frames_from_ffprobe']:
        base['ffprobe_frame_sample_count_matches_raw_decode']=base['decoded_sample_frames_from_ffprobe']==dec.get('sample_frames')
    else:base['ffprobe_frame_sample_count_matches_raw_decode']=None
    base['interpretation']='sólo evidencia de identidad de bytes entre demux/objeto multimedia y de tiempos del decodificador; la política vigente no promueve ningún timestamp ASF ni límite de frame del decodificador a autoridad de recuperación exacta por muestra'
    return base


def _sha256_file_range(path:Path,start:int=0,length:int|None=None):
    h=hashlib.sha256();remaining=length
    with Path(path).open('rb') as f:
        if start:f.seek(start)
        while True:
            if remaining is not None and remaining<=0:break
            want=1024*1024 if remaining is None else min(1024*1024,remaining)
            b=f.read(want)
            if not b:break
            h.update(b)
            if remaining is not None:remaining-=len(b)
    return h.hexdigest()

def _decode_s32_file(path:Path,out:Path,exe:str,timeout=300,seek_seconds:float|None=None):
    cmd=[exe,'-y','-hide_banner','-nostdin','-v','error']
    if seek_seconds is not None:cmd += ['-ss',f'{seek_seconds:.9f}']
    cmd += ['-i',str(path),'-map','0:a:0','-f','s32le','-acodec','pcm_s32le',str(out)]
    try:
        r=subprocess.run(cmd,capture_output=True,timeout=timeout)
        return {'passed':r.returncode==0,'return_code':r.returncode,'stderr_lines':r.stderr.decode('utf-8','replace').splitlines()[-50:],
                'output_bytes':out.stat().st_size if out.exists() else 0}
    except subprocess.TimeoutExpired:
        return {'passed':False,'return_code':None,'stderr_lines':['timeout'],'output_bytes':0}
    except Exception as e:
        return {'passed':False,'return_code':None,'stderr_lines':[f'{type(e).__name__}: {e}'],'output_bytes':0}

def _media_object_forward_gap(prev_row:dict,next_row:dict):
    a=prev_row.get('media_object_number');b=next_row.get('media_object_number')
    sa=prev_row.get('media_object_number_size_bytes');sb=next_row.get('media_object_number_size_bytes')
    if a is None or b is None or not sa or sa!=sb or sa not in (1,2,4):return None
    mod=1<<(8*sa);delta=(int(b)-int(a))%mod
    # delta==1 es normal y delta==0 indica duplicado o reutilización. Un delta modular enorme es
    # más compatible con un salto regresivo o corrupto que con una secuencia física breve ausente.
    if delta<=1 or delta>min(1024,mod//2):return None
    return {'previous_media_object_number':int(a),'next_media_object_number':int(b),
            'media_object_number_size_bytes':sa,'missing_media_object_count':delta-1,
            'missing_media_object_numbers':[((int(a)+i)%mod) for i in range(1,delta)] if delta<=33 else []}

def asf_wma_decoder_convergence_evidence(path:Path,ffmpeg_exe:str,demux_decoder:dict,channels:int|None,sample_rate:int|None,timeout=300):
    """Observa convergencia determinista tras una secuencia ausente de objetos WMA.

    Es sólo evidencia. El candidato requiere mapeo uno a uno con paquetes demux,
    hashes, tamaños y relación PTS/preroll intactos, además de numeración que pruebe
    objetos ausentes. El primer objeto superviviente se usa como contexto. Una
    decodificación nueva debe coincidir con el sufijo canónico desde el segundo
    paquete superviviente. Esto no convierte timestamps ASF en coordenadas PCM
    exactas ni concede autoridad de publicación.
    """
    base={'policy':'ASF_WMA_DECODER_CONVERGENCE_EVIDENCE_ONLY','publication_enabled':False,
          'pcm_sample_exact_claim':False,'repair_authority':'NONE','pcm_recovery_authority':'NONE',
          'reference_equivalence_is_test_only':True,'candidate_count':0,'validated_candidate_count':0,
          'media_object_number_discontinuities':[],'candidates':[],
          'interpretation':'sólo evidencia de convergencia determinista del decodificador; la equivalencia PCM con referencia sana es evidencia de pruebas de aceptación y no se presupone para fuentes arbitrarias'}
    rows=demux_decoder.get('mapping_rows') or []
    if not (demux_decoder.get('one_to_one_complete_media_object_mapping') and demux_decoder.get('all_packet_hashes_equal') and
            demux_decoder.get('all_packet_sizes_equal') and demux_decoder.get('all_pts_match_media_object_presentation_minus_preroll')):
        base['eligibility']='BLOCKED_DEMUX_OR_MEDIA_OBJECT_MAPPING_NOT_PROVEN';return base
    discs=[]
    for i in range(len(rows)-1):
        g=_media_object_forward_gap(rows[i],rows[i+1])
        if not g:continue
        g.update({'after_demux_packet_index':i,'next_surviving_demux_packet_index':i+1,
                  'previous_demux_pts_ms':rows[i].get('demux_packet_pts_ms'),'previous_demux_duration_ms':rows[i].get('demux_packet_duration_ms'),
                  'next_demux_pts_ms':rows[i+1].get('demux_packet_pts_ms')})
        discs.append(g)
    base['media_object_number_discontinuities']=discs;base['candidate_count']=len(discs)
    if not discs:
        base['eligibility']='NOT_REQUIRED_OR_NO_PROVEN_MISSING_MEDIA_OBJECT_RUN';return base
    if not channels or not sample_rate:
        base['eligibility']='BLOCKED_NATIVE_PCM_PROFILE_UNKNOWN';return base
    frame_values=demux_decoder.get('decoded_frame_nb_samples_values') or []
    frame_len=frame_values[0] if len(frame_values)==1 and frame_values[0] else None
    base['observed_decoder_frame_len_samples']=frame_len
    bytes_per_sample_frame=4*channels
    with tempfile.TemporaryDirectory(prefix='lossydoctor-wma-v39-') as td:
        td=Path(td);full=td/'full.s32le';fq=_decode_s32_file(path,full,ffmpeg_exe,timeout)
        base['full_decode_passed']=fq.get('passed');base['full_decode_output_bytes']=fq.get('output_bytes')
        if not fq.get('passed'):
            base['eligibility']='BLOCKED_FULL_CANONICAL_DECODE_FAILED';return base
        full_size=fq['output_bytes'];full_bytes=full.read_bytes();frame_rows=demux_decoder.get('decoded_frame_rows') or []
        for ci,g in enumerate(discs,1):
            prev_i=g['after_demux_packet_index'];next_i=g['next_surviving_demux_packet_index']
            prev=rows[prev_i];nxt=rows[next_i]
            seek_ms=None
            if prev.get('demux_packet_pts_ms') is not None and prev.get('demux_packet_duration_ms') is not None:
                seek_ms=prev['demux_packet_pts_ms']+prev['demux_packet_duration_ms']
            expected_candidate_i=next_i+1 if next_i+1<len(rows) else None
            c={**g,'candidate_index':ci,'seek_from_missing_interval_ms':seek_ms,
               'context_demux_packet_index':next_i,'context_media_object_number':nxt.get('media_object_number'),
               'expected_first_candidate_demux_packet_index':expected_candidate_i,
               'expected_first_candidate_media_object_number':rows[expected_candidate_i].get('media_object_number') if expected_candidate_i is not None else None,
               'required_surviving_context_media_objects':1,'synthesized_missing_span':'NONE','validated':False}
            if seek_ms is None or expected_candidate_i is None:
                c['status']='INSUFFICIENT_POST_GAP_CONTEXT_OR_TIMING_EVIDENCE';base['candidates'].append(c);continue
            seek=td/f'seek-{ci}.s32le';sq=_decode_s32_file(path,seek,ffmpeg_exe,timeout,seek_ms/1000.0)
            c['seek_decode_passed']=sq.get('passed');c['seek_decode_output_bytes']=sq.get('output_bytes')
            if not sq.get('passed') or sq.get('output_bytes',0)>full_size:
                c['status']='SEEK_DECODE_FAILED_OR_NOT_SUFFIX_SIZED';base['candidates'].append(c);continue
            off=full_size-sq['output_bytes'];c['full_decode_suffix_byte_offset']=off
            aligned=off%bytes_per_sample_frame==0;c['sample_frame_aligned']=aligned
            sample_off=off//bytes_per_sample_frame if aligned else None;c['full_decode_suffix_start_sample_frame']=sample_off
            seek_hash=_sha256_file_range(seek,0);tail_hash=_sha256_file_range(full,off)
            c['seek_decode_pcm_sha256']=seek_hash;c['full_decode_suffix_pcm_sha256']=tail_hash
            c['seek_decode_matches_full_decode_suffix']=seek_hash==tail_hash
            suffix_frame_index=None
            if aligned and frame_len and sample_off is not None and sample_off%frame_len==0:
                suffix_frame_index=sample_off//frame_len
            c['full_decode_suffix_start_decoded_frame_index']=suffix_frame_index
            observed_packet=None
            if suffix_frame_index is not None and 0<=suffix_frame_index<len(frame_rows):
                observed_packet=frame_rows[suffix_frame_index].get('mapped_demux_packet_index')
            c['observed_suffix_start_demux_packet_index']=observed_packet
            c['one_surviving_packet_context_observed']=observed_packet==expected_candidate_i
            c['validated']=bool(c['seek_decode_matches_full_decode_suffix'] and c['sample_frame_aligned'] and c['one_surviving_packet_context_observed'])
            c['status']='VALIDATED_DETERMINISTIC_CONVERGENCE_EVIDENCE_ONLY' if c['validated'] else 'CONVERGENCE_NOT_PROVEN'
            base['candidates'].append(c)
    base['validated_candidate_count']=sum(1 for c in base['candidates'] if c.get('validated'))
    base['all_candidates_validated']=bool(base['candidates']) and base['validated_candidate_count']==len(base['candidates'])
    # Si cada secuencia ausente converge de forma independiente, se calculan hashes
    # de regiones limpias no superpuestas de la misma decodificación canónica. El
    # primer objeto superviviente sigue siendo sólo contexto. Estos hashes son
    # evidencia; la publicación tiene otra puerta y una verificación nueva.
    clean_regions=[]
    if base['all_candidates_validated'] and frame_len and full_size % bytes_per_sample_frame == 0:
        total_samples=full_size//bytes_per_sample_frame
        start_sample=0
        ordered=sorted(base['candidates'], key=lambda c:c.get('after_demux_packet_index',-1))
        for idx,c in enumerate(ordered):
            suffix_start=c.get('full_decode_suffix_start_sample_frame')
            if suffix_start is None:
                clean_regions=[];break
            context_start=int(suffix_start)-int(frame_len)
            if context_start<start_sample:
                clean_regions=[];break
            if context_start>start_sample:
                bs=start_sample*bytes_per_sample_frame;be=context_start*bytes_per_sample_frame
                clean_regions.append({'region_index':len(clean_regions)+1,'decoded_sample_start':start_sample,
                    'decoded_sample_end':context_start,'sample_count':context_start-start_sample,
                    'pcm_sha256':hashlib.sha256(full_bytes[bs:be]).hexdigest(),'left_gap_candidate_index':idx if start_sample else None,
                    'right_gap_candidate_index':idx+1,'boundary_start':'CANONICAL_DECODE_START' if start_sample==0 else 'POST_GAP_CONVERGED_AFTER_ONE_CONTEXT_OBJECT',
                    'boundary_end':'BEFORE_NEXT_GAP_CONTEXT_FRAME'})
            start_sample=int(suffix_start)
        if clean_regions is not None and start_sample<total_samples:
            bs=start_sample*bytes_per_sample_frame
            clean_regions.append({'region_index':len(clean_regions)+1,'decoded_sample_start':start_sample,
                'decoded_sample_end':total_samples,'sample_count':total_samples-start_sample,
                'pcm_sha256':hashlib.sha256(full_bytes[bs:full_size]).hexdigest(),'left_gap_candidate_index':len(ordered),
                'right_gap_candidate_index':None,'boundary_start':'POST_GAP_CONVERGED_AFTER_ONE_CONTEXT_OBJECT',
                'boundary_end':'CANONICAL_DECODE_END'})
        # Asocia procedencia de objetos comprimidos a cada región siguiendo el mapeo ya observado
        # de cuadro decodificado a paquete demux. El cuadro final de vaciado puede compartir el último paquete.
        for r in clean_regions:
            if r['decoded_sample_start']%frame_len or r['decoded_sample_end']%frame_len:
                r['provenance_complete']=False;continue
            fs=r['decoded_sample_start']//frame_len;fe=r['decoded_sample_end']//frame_len
            fr=frame_rows[fs:fe] if fe<=len(frame_rows) else []
            pis=[]
            for x in fr:
                pi=x.get('mapped_demux_packet_index')
                if pi is not None and pi not in pis:pis.append(pi)
            sel=[rows[i] for i in pis if 0<=i<len(rows)]
            r['decoded_frame_start_index']=fs;r['decoded_frame_end_index_exclusive']=fe
            r['selected_demux_packet_indices']=pis
            r['selected_media_object_numbers']=[x.get('media_object_number') for x in sel]
            r['selected_media_object_sha256']=[x.get('media_object_sha256') for x in sel]
            r['selected_demux_packet_sha256']=[x.get('demux_packet_sha256') for x in sel]
            r['provenance_complete']=bool(fr and len(sel)==len(pis) and all(x.get('hash_equal') and x.get('size_equal') and x.get('pts_preroll_equal') for x in sel))
    base['clean_region_candidates']=clean_regions
    base['clean_region_candidate_count']=len(clean_regions)
    base['eligibility']='VALIDATED_EVIDENCE_ONLY_NO_PUBLICATION' if base['all_candidates_validated'] else 'PARTIAL_OR_UNPROVEN_CONVERGENCE_EVIDENCE'
    return base

def aac_adts_demux_evidence(path:Path,ffprobe_exe:str,frames:list[dict],timeout=300):
    """Compara el enmarcado ADTS directo con paquetes demux de FFmpeg; sólo aporta evidencia."""
    base={'policy':'AAC_ADTS_DEMUX_EVIDENCE_ONLY','packet_probe_passed':False,
          'frame_count_equal':False,'positions_equal':False,'sizes_equal':False,'all_equal':False,
          'direct_complete_frame_count':len(frames),'ffprobe_packet_count':0,'rows':[]}
    cmd=[ffprobe_exe,'-v','error','-select_streams','a:0','-show_packets',
         '-show_entries','packet=pts_time,duration_time,size,pos,flags','-of','json',str(path)]
    try:
        r=subprocess.run(cmd,capture_output=True,text=True,timeout=timeout)
        if r.returncode==0 and r.stdout.strip():
            packets=json.loads(r.stdout).get('packets',[]) or [];base['packet_probe_passed']=True
        else:
            base['stderr_lines']=(r.stderr or '').splitlines()[-50:];packets=[]
    except Exception as e:
        packets=[];base['stderr_lines']=[f'{type(e).__name__}: {e}']
    rows=[]
    for i,(fr,pkt) in enumerate(zip(frames,packets)):
        try:pos=int(pkt.get('pos'))
        except Exception:pos=None
        try:size=int(pkt.get('size'))
        except Exception:size=None
        peq=pos==fr.get('byte_start') if pos is not None else False
        seq=size==fr.get('frame_length') if size is not None else False
        rows.append({'index':i,'direct_byte_start':fr.get('byte_start'),'direct_frame_length':fr.get('frame_length'),
                     'ffprobe_pos':pos,'ffprobe_size':size,'position_equal':peq,'size_equal':seq,
                     'pts_time':pkt.get('pts_time'),'duration_time':pkt.get('duration_time')})
    base['rows']=rows;base['ffprobe_packet_count']=len(packets)
    base['frame_count_equal']=len(frames)==len(packets)
    base['positions_equal']=bool(rows and len(rows)==len(frames)==len(packets) and all(x['position_equal'] for x in rows))
    base['sizes_equal']=bool(rows and len(rows)==len(frames)==len(packets) and all(x['size_equal'] for x in rows))
    base['all_equal']=base['frame_count_equal'] and base['positions_equal'] and base['sizes_equal']
    base['interpretation']='la posición/tamaño del paquete demux FFmpeg se compara con los límites directos de frames ADTS; esto es sólo evidencia estructural y no otorga autoridad de reparación ni recuperación PCM.'
    return base


def mp4_aac_demux_evidence(path:Path,ffprobe_exe:str,access_units:list[dict],media_timescale:int|None,timeout=300):
    """Contrasta muestras AAC de MP4 mapeadas directamente con paquetes de FFprobe; sólo aporta evidencia."""
    base={'policy':'MP4_AAC_DEMUX_ACCESS_UNIT_EVIDENCE_ONLY','packet_probe_passed':False,
          'direct_access_unit_count':len(access_units),'ffprobe_packet_count':0,'stream_time_base':None,
          'count_equal':False,'positions_equal':False,'sizes_equal':False,'hashes_equal':False,
          'durations_equal':False,'all_boundaries_and_hashes_equal':False,'constant_dts_shift_media_units':None,
          'rows':[]}
    cmd=[ffprobe_exe,'-v','error','-select_streams','a:0','-show_packets','-show_streams','-show_data_hash','sha256',
         '-show_entries','stream=time_base:packet=dts,duration,size,pos,data_hash','-of','json',str(path)]
    try:
        r=subprocess.run(cmd,capture_output=True,text=True,timeout=timeout)
        if r.returncode==0 and r.stdout.strip():
            payload=json.loads(r.stdout);packets=payload.get('packets',[]) or [];streams=payload.get('streams',[]) or []
            base['packet_probe_passed']=True;base['stream_time_base']=(streams[0].get('time_base') if streams else None)
        else:
            packets=[];base['stderr_lines']=(r.stderr or '').splitlines()[-50:]
    except Exception as e:
        packets=[];base['stderr_lines']=[f'{type(e).__name__}: {e}']
    try:
        numerator,denominator=(int(x) for x in str(base['stream_time_base']).split('/',1))
        if numerator<=0 or denominator<=0:raise ValueError
    except Exception:numerator=denominator=None
    rows=[];shifts=[]
    for index,(direct,packet) in enumerate(zip(access_units,packets)):
        try:position=int(packet.get('pos'))
        except Exception:position=None
        try:size=int(packet.get('size'))
        except Exception:size=None
        try:duration=int(packet.get('duration'))
        except Exception:duration=None
        try:dts=int(packet.get('dts'))
        except Exception:dts=None
        data_hash=packet.get('data_hash') or ''
        if ':' in data_hash:data_hash=data_hash.split(':',1)[1]
        duration_equal=None;demux_dts_media_units=None
        if numerator is not None and media_timescale and duration is not None and direct.get('duration_units') is not None:
            duration_equal=duration*numerator*media_timescale==direct['duration_units']*denominator
        if numerator is not None and media_timescale and dts is not None:
            scaled=dts*numerator*media_timescale
            if scaled%denominator==0:demux_dts_media_units=scaled//denominator
        if demux_dts_media_units is not None and direct.get('decode_time_units') is not None:
            shifts.append(demux_dts_media_units-direct['decode_time_units'])
        rows.append({'index':index,'direct_byte_start':direct.get('byte_start'),'direct_size':direct.get('size'),
            'direct_sha256':direct.get('sha256'),'direct_decode_time_units':direct.get('decode_time_units'),
            'direct_duration_units':direct.get('duration_units'),'ffprobe_pos':position,'ffprobe_size':size,
            'ffprobe_sha256':data_hash or None,'ffprobe_dts':dts,'ffprobe_duration':duration,
            'ffprobe_dts_media_units':demux_dts_media_units,'position_equal':position==direct.get('byte_start'),
            'size_equal':size==direct.get('size'),'hash_equal':bool(data_hash and data_hash==direct.get('sha256')),
            'duration_equal':duration_equal})
    base['rows']=rows;base['ffprobe_packet_count']=len(packets);base['count_equal']=len(access_units)==len(packets)
    exact_count=bool(rows and len(rows)==len(access_units)==len(packets))
    base['positions_equal']=bool(exact_count and all(x['position_equal'] for x in rows))
    base['sizes_equal']=bool(exact_count and all(x['size_equal'] for x in rows))
    base['hashes_equal']=bool(exact_count and all(x['hash_equal'] for x in rows))
    comparable=[x for x in rows if x['duration_equal'] is not None]
    base['durations_equal']=bool(exact_count and len(comparable)==len(rows) and all(x['duration_equal'] for x in comparable))
    base['all_boundaries_and_hashes_equal']=base['count_equal'] and base['positions_equal'] and base['sizes_equal'] and base['hashes_equal']
    base['constant_dts_shift_media_units']=shifts[0] if shifts and len(shifts)==len(rows) and len(set(shifts))==1 else None
    base['dts_shift_is_constant']=bool(shifts and len(shifts)==len(rows) and len(set(shifts))==1)
    base['interpretation']='la asignación directa de muestras stsc/stsz/stco se compara byte a byte con paquetes demux de FFprobe. Se registra un desplazamiento temporal constante del demux, pero no se interpreta como ventana de presentación canónica hasta auditar la semántica de la lista de edición MP4.'
    return base
