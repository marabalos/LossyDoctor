from __future__ import annotations
from pathlib import Path
import tempfile, os, hashlib, json, shutil
from app.external import decode_to_raw_file, raw_file_to_flac, canonical_pcm_profile
from app.publication import combined_publication_status, publish_or_preview_with_manifest
from app.utils import sha256_file, sha256_bytes
from app.version import APP_VERSION


def _hash_range(path:Path,start:int,length:int,chunk=1024*1024):
    h=hashlib.sha256(); left=length
    with path.open('rb') as f:
        f.seek(start)
        while left:
            b=f.read(min(chunk,left))
            if not b:break
            h.update(b);left-=len(b)
    if left: raise RuntimeError('RAW_RANGE_SHORT_READ')
    return h.hexdigest()

def _all_zero(path:Path,start:int,length:int,chunk=1024*1024):
    left=length
    with path.open('rb') as f:
        f.seek(start)
        while left:
            b=f.read(min(chunk,left))
            if not b:return False
            if any(b):return False
            left-=len(b)
    return True

def _group_clean(frames):
    fs=[f for f in frames if not f['is_vbr_header'] and f['clean']]
    groups=[]
    for f in fs:
        li=f.get('logical_audio_index')
        if not groups:
            groups.append([f]);continue
        prev=groups[-1][-1]; pli=prev.get('logical_audio_index')
        logical_cont=(li is not None and pli is not None and li==pli+1)
        byte_cont=(f['byte_start']==prev['byte_end'])
        if byte_cont and (logical_cont or (li is None and pli is None)):groups[-1].append(f)
        else:groups.append([f])
    return groups

def _write_mpeg_fragment(path:Path,data:bytes,start:int,end:int):
    path.write_bytes(data[start:end])

def _decode_fragment(data:bytes,start:int,end:int,ffmpeg:str,tmp:Path,name:str,ch:int):
    mp3=tmp/f'{name}.mp3'; raw=tmp/f'{name}.raw'
    _write_mpeg_fragment(mp3,data,start,end)
    dr=decode_to_raw_file(mp3,raw,ffmpeg)
    if not dr['passed']:
        return {'passed':False,'error':dr['stderr']}
    frame_bytes=ch*4
    if raw.stat().st_size%frame_bytes:
        return {'passed':False,'error':'la cantidad de bytes crudos decodificados no está alineada con canales/muestras'}
    return {'passed':True,'raw_path':raw,'sample_count':raw.stat().st_size//frame_bytes}

def _prior_gap(group,mpeg):
    before=[g for g in mpeg['gaps'] if g['byte_end']<=group[0]['byte_start']]
    return max(before,key=lambda g:g['byte_end']) if before else None

def _context_frames(group,mpeg):
    gap=_prior_gap(group,mpeg)
    if gap is None:return [],None
    fs=[f for f in mpeg['frames'] if not f['is_vbr_header'] and f['byte_start']>=gap['byte_end'] and f['byte_start']<group[0]['byte_start']]
    return fs,gap

def _decode_prefix_region(group,mpeg,ffmpeg,tmp,ch,cpw):
    data=mpeg['data']; facts=mpeg['facts']; start=facts['first_audio_offset']; end=group[-1]['byte_end']
    d=_decode_fragment(data,start,end,ffmpeg,tmp,'prefix',ch)
    if not d['passed']:return d
    samples=d['sample_count']; source_start=0 if cpw.get('determined') else None
    source_end=min(samples,cpw['logical_sample_count']) if cpw.get('determined') else None
    if source_end is not None:samples=source_end
    return {'passed':True,'raw_path':d['raw_path'],'raw_byte_start':0,'raw_byte_length':samples*ch*4,'sample_count':samples,
            'pcm_sha256':_hash_range(d['raw_path'],0,samples*ch*4),'source_start_sample':source_start,'source_end_sample':source_end,
            'logical_frame_start':group[0].get('logical_audio_index'),'logical_frame_end_exclusive':None if group[-1].get('logical_audio_index') is None else group[-1]['logical_audio_index']+1,
            'source_byte_start':group[0]['byte_start'],'source_byte_end':group[-1]['byte_end'],'decode_context_byte_start':start,'discarded_context_samples':0}

def _calibrate_fragment_mapping(prefix_group,mpeg,ffmpeg,tmp,ch,spf,cpw,prefix_region):
    """Calibra la salida de fragmentos MPEG crudos contra muestras de presentación canónicas.

    Se deriva del PCM limpio del mismo archivo y decodificador, sin fijar una constante
    de demora del decodificador MP3.
    """
    if not cpw.get('determined') or prefix_region.get('source_start_sample')!=0:return None
    clean=[f for f in prefix_group if f.get('logical_audio_index') is not None]
    if len(clean)<12:return None
    cal_start=min(5,max(1,len(clean)//4))
    cal=clean[cal_start:min(len(clean),cal_start+16)]
    if len(cal)<6:return None
    d=_decode_fragment(mpeg['data'],cal[0]['byte_start'],cal[-1]['byte_end'],ffmpeg,tmp,'calibration',ch)
    if not d['passed']:return None
    raw=d['raw_path']; raw_samples=d['sample_count']; win=min(512,max(128,spf//2))
    probe=min(2*spf,max(0,raw_samples-win))
    if probe+win>raw_samples:return None
    with raw.open('rb') as f:
        f.seek(probe*ch*4); needle=f.read(win*ch*4)
    ppath=Path(prefix_region['raw_path']); psamples=prefix_region['sample_count']
    expected=cal[0]['logical_audio_index']*spf-cpw['encoder_delay_samples']+probe
    lo=max(0,expected-spf); hi=min(psamples-win,expected+spf)
    if hi<lo:return None
    matches=[]
    with ppath.open('rb') as f:
        pdata=f.read()
    for pos in range(lo,hi+1):
        a=pos*ch*4
        if pdata[a:a+len(needle)]==needle:matches.append(pos)
    if not matches:return None
    best=min(matches,key=lambda x:abs(x-expected))
    # Varias coincidencias igualmente cercanas son ambiguas, algo común en silencios digitales largos.
    dist=abs(best-expected)
    if sum(1 for x in matches if abs(x-expected)==dist)>1:return None
    constant=best-probe-cal[0]['logical_audio_index']*spf
    return {'constant_samples':constant,'calibration_logical_start':cal[0]['logical_audio_index'],'probe_fragment_sample':probe,'matched_source_sample':best,'match_count':len(matches)}

def _decode_suffix_region(group,mpeg,ffmpeg,tmp,ch,spf,cpw,mapping,idx):
    ctx,gap=_context_frames(group,mpeg)
    if gap is None:return {'passed':False,'error':'el grupo limpio del sufijo no tiene contexto de brecha anterior'}
    context_start=ctx[0]['byte_start'] if ctx else group[0]['byte_start']
    logical_context_start=ctx[0].get('logical_audio_index') if ctx else group[0].get('logical_audio_index')
    d=_decode_fragment(mpeg['data'],context_start,group[-1]['byte_end'],ffmpeg,tmp,f'suffix_{idx:02d}',ch)
    if not d['passed']:return d
    # Entrega al decodificador los frames contaminados posteriores a la brecha y el primer frame limpio.
    # Su PCM se descarta deliberadamente; sólo la salida posterior puede considerarse genuina.
    pre_frames=len(ctx)
    discard=(pre_frames+1)*spf
    if d['sample_count']<=discard:return {'passed':False,'error':'no queda PCM después de descartar conservadoramente el contexto del decodificador'}
    raw_start=discard*ch*4; samples=d['sample_count']-discard
    source_start=source_end=None
    if cpw.get('determined') and mapping and logical_context_start is not None:
        source_start=discard+logical_context_start*spf+mapping['constant_samples']
        if source_start<0:
            shift=-source_start; raw_start+=shift*ch*4; samples-=shift; source_start=0
        source_end=min(cpw['logical_sample_count'],source_start+samples)
        samples=max(0,source_end-source_start)
        if samples<=0:return {'passed':False,'error':'el sufijo asignado queda fuera de la ventana de presentación canónica'}
    byte_len=samples*ch*4
    return {'passed':True,'raw_path':d['raw_path'],'raw_byte_start':raw_start,'raw_byte_length':byte_len,'sample_count':samples,
            'pcm_sha256':_hash_range(d['raw_path'],raw_start,byte_len),'source_start_sample':source_start,'source_end_sample':source_end,
            'logical_frame_start':group[0].get('logical_audio_index'),'logical_frame_end_exclusive':None if group[-1].get('logical_audio_index') is None else group[-1]['logical_audio_index']+1,
            'source_byte_start':group[0]['byte_start'],'source_byte_end':group[-1]['byte_end'],'decode_context_byte_start':context_start,
            'discarded_context_samples':discard,'context_frame_count':pre_frames,'mapping_calibration':mapping}

def _extract_regions(mpeg,ffmpeg,tmp,ch,spf,cpw):
    groups=_group_clean(mpeg['frames'])
    if not groups:return {'passed':False,'error':'no clean MPEG groups','regions':[],'mapped':False}
    regs=[]; prefix=None; mapping=None
    for idx,g in enumerate(groups):
        gap=_prior_gap(g,mpeg)
        if gap is None:
            r=_decode_prefix_region(g,mpeg,ffmpeg,tmp,ch,cpw)
            if not r['passed']:return {'passed':False,'error':r.get('error'),'regions':[],'mapped':False}
            regs.append(r); prefix=r
            if cpw.get('determined'):mapping=_calibrate_fragment_mapping(g,mpeg,ffmpeg,tmp,ch,spf,cpw,r)
        else:
            r=_decode_suffix_region(g,mpeg,ffmpeg,tmp,ch,spf,cpw,mapping,idx)
            if not r['passed']:continue
            regs.append(r)
    mapped=bool(cpw.get('determined')) and bool(regs) and all(r.get('source_start_sample') is not None for r in regs)
    return {'passed':bool(regs),'regions':regs,'mapped':mapped,'mapping_calibration':mapping}

def _segment_recovery_eligibility(mpeg:dict,playability:str):
    facts=mpeg.get('facts') or {}; params=facts.get('parameter_segments') or {}; segments=params.get('segments') or []
    hard=int(params.get('hard_profile_transition_count') or 0)
    if hard<=0 or len(segments)<2:
        return {'eligible':False,'reason':'no hard MPEG parameter transitions'}
    if playability!='UNPLAYABLE':
        return {'eligible':False,'reason':'el stream heterogéneo sigue siendo reproducible; el derivado automático de preservación es sólo una alternativa'}
    if facts.get('gaps') or facts.get('truncated_final_frame') or params.get('parameter_change_after_resync_count'):
        return {'eligible':False,'reason':'el subconjunto vigente sensible a segmentos requiere concatenación coherente contigua sin brechas ni truncamiento'}
    if int(params.get('coherent_concatenation_transition_count') or 0)!=hard:
        return {'eligible':False,'reason':'no todas las transiciones fuertes de parámetros son una concatenación coherente contigua'}
    checked=[]
    for seg in segments:
        fs=mpeg['frames'][int(seg['frame_start_index']):int(seg['frame_end_index'])+1]
        audio=[f for f in fs if not f.get('is_vbr_header')]
        if not audio or not all(f.get('clean') for f in audio):
            return {'eligible':False,'reason':f"segment {seg.get('index')} contiene frames de audio contaminados o no demostrables"}
        first=audio[0]
        if int((seg.get('profile') or {}).get('layer') or 0)==3 and int(first.get('main_data_begin') or 0)!=0:
            return {'eligible':False,'reason':f"segment {seg.get('index')} no comienza en un límite independiente del reservorio Layer III"}
        checked.append({'segment_index':seg.get('index'),'first_audio_frame_index':first.get('index'),'independent_reservoir_start':int(first.get('main_data_begin') or 0)==0})
    return {'eligible':True,'reason':'todas las transiciones fuertes son segmentos contiguos y coherentes de perfil nativo con inicios decodificables independientemente','segments':checked}



def _clean_groups_for_audio(audio:list[dict]):
    groups=[]
    for f in audio:
        if not f.get('clean'):
            continue
        if groups and groups[-1][-1]['byte_end']==f['byte_start'] and groups[-1][-1]['index']+1==f['index']:
            groups[-1].append(f)
        else:
            groups.append([f])
    return groups

def _segmented_partial_plan(mpeg:dict):
    """Planifica regiones limpias de perfil nativo decodificables alrededor de brechas reales."""
    facts=mpeg.get('facts') or {}; params=facts.get('parameter_segments') or {}
    segments=params.get('segments') or []; gaps=facts.get('gaps') or []
    plans=[]
    for seg in segments:
        fs=mpeg['frames'][int(seg['frame_start_index']):int(seg['frame_end_index'])+1]
        audio=[f for f in fs if not f.get('is_vbr_header')]
        if not audio:
            continue
        groups=_clean_groups_for_audio(audio)
        if len(groups)!=1:
            continue
        group=groups[0]; first_audio=audio[0]
        gap_before=next((g for g in gaps if int(g.get('byte_end',-1))==int(seg.get('byte_start',-2))),None)
        gap_after=next((g for g in gaps if int(g.get('byte_start',-1))==int(seg.get('byte_end',-2))),None)
        pr=seg.get('profile') or {}; layer=int(pr.get('layer') or 0); spf=int(pr.get('samples_per_frame') or 0)
        if spf<=0:
            continue
        if gap_before is None:
            if group[0]['index']!=first_audio['index']:
                continue
            if layer==3 and int(first_audio.get('main_data_begin') or 0)!=0:
                continue
            retained=group; preclean=0; warmup=0; discard=0; context_start=int(seg['byte_start'])
        else:
            preclean=sum(1 for f in audio if f['index']<group[0]['index'])
            warmup=1
            if len(group)<=warmup:
                continue
            retained=group[warmup:]
            if len(retained)<2:
                continue
            discard=(preclean+warmup)*spf; context_start=int(seg['byte_start'])
        if len(retained)<2:
            continue
        plans.append({
            'region_index':len(plans)+1,'parameter_segment_index':int(seg['index'])+1,
            'structural_segment_id':int(seg.get('structural_segment_id') or 0),
            'native_profile':pr,'decode_context_byte_start':context_start,
            'decode_context_byte_end':int(group[-1]['byte_end']),
            'source_byte_start':int(retained[0]['byte_start']),'source_byte_end':int(retained[-1]['byte_end']),
            'source_frame_start_index':int(retained[0]['index']),'source_frame_end_index':int(retained[-1]['index']),
            'retained_frame_count':len(retained),'preclean_tainted_frame_count':preclean,
            'warmup_clean_frame_count':warmup,'discarded_context_samples':discard,
            'gap_before_index':None if gap_before is None else int(gap_before['index']),
            'gap_after_index':None if gap_after is None else int(gap_after['index']),
            'logical_frame_start':retained[0].get('logical_audio_index'),
            'logical_frame_end_exclusive':None if retained[-1].get('logical_audio_index') is None else int(retained[-1]['logical_audio_index'])+1,
        })
    return plans

def _segment_partial_recovery_eligibility(mpeg:dict,playability:str):
    facts=mpeg.get('facts') or {}; params=facts.get('parameter_segments') or {}; gaps=facts.get('gaps') or []
    if int(params.get('hard_profile_transition_count') or 0)<=0:
        return {'eligible':False,'reason':'la recuperación vigente de segmentos dañados requiere al menos una transición fuerte de perfil MPEG'}
    if not gaps:
        return {'eligible':False,'reason':'no hay brecha estructural de resincronización; se usa recuperación segmentada coherente cuando corresponde'}
    if playability!='UNPLAYABLE':
        return {'eligible':False,'reason':'el stream heterogéneo dañado sigue siendo reproducible; el derivado de preservación es sólo una alternativa'}
    if facts.get('truncated_final_frame'):
        return {'eligible':False,'reason':'el subconjunto vigente de segmentos dañados no incluye frames finales truncados'}
    crc=(facts.get('crc_protection') or {})
    if int(crc.get('mismatch_count') or 0)>0:
        return {'eligible':False,'reason':'la inconsistencia CRC no está suficientemente localizada para preservar automáticamente regiones limpias'}
    reservoir=(facts.get('bit_reservoir') or {})
    if reservoir.get('main_data_overrun_frame_indices'):
        return {'eligible':False,'reason':'el exceso de datos principales Layer III queda fuera del subconjunto vigente de recuperación sólo de brechas'}
    if any(i.code=='BIT_RESERVOIR_BACKPOINTER_UNAVAILABLE' for i in (mpeg.get('issues') or [])):
        return {'eligible':False,'reason':'existe una dependencia de reservorio no disponible antes de cualquier segmento de resincronización demostrado'}
    plans=_segmented_partial_plan(mpeg)
    if len(plans)<2:
        return {'eligible':False,'reason':'quedan menos de dos regiones limpias de perfil nativo recuperables independientemente después de la preparación conservadora'}
    uncovered=[]
    for g in gaps:
        before=any(int(r['source_byte_end'])<=int(g['byte_start']) for r in plans)
        after=any(int(r['source_byte_start'])>=int(g['byte_end']) for r in plans)
        if not (before and after):uncovered.append(int(g['index']))
    if uncovered:
        return {'eligible':False,'reason':f'gap(s) {uncovered} no están delimitadas por regiones limpias recuperables independientemente'}
    return {'eligible':True,'reason':'todas las brechas reales están delimitadas por regiones limpias de perfil nativo decodificables independientemente; no se sintetiza ningún intervalo dañado','regions':plans}

def _segment_open_partial_recovery_eligibility(mpeg:dict,playability:str):
    """Retiene regiones de perfil nativo demostrables aunque el daño sea terminal.

    Es una alternativa posterior a recuperaciones de mayor autoridad. Nunca
    sintetiza el intervalo ausente ni afirma cobertura completa de la fuente.
    """
    facts=mpeg.get('facts') or {}; params=facts.get('parameter_segments') or {}; gaps=facts.get('gaps') or []
    if int(params.get('hard_profile_transition_count') or 0)<=0:
        return {'eligible':False,'reason':'el subconjunto vigente de extremo abierto requiere actualmente un stream MPEG heterogéneo'}
    if playability!='UNPLAYABLE':
        return {'eligible':False,'reason':'el stream dañado de extremo abierto sigue siendo reproducible; el derivado de preservación es sólo una alternativa'}
    crc=facts.get('crc_protection') or {}
    if int(crc.get('mismatch_count') or 0)>0:
        return {'eligible':False,'reason':'la inconsistencia CRC no está suficientemente localizada para la preservación automática de extremo abierto'}
    reservoir=facts.get('bit_reservoir') or {}
    if reservoir.get('main_data_overrun_frame_indices'):
        return {'eligible':False,'reason':'el exceso de datos principales Layer III queda fuera del subconjunto de recuperación de extremo abierto'}
    if any(i.code in ('BIT_RESERVOIR_BACKPOINTER_UNAVAILABLE','BIT_RESERVOIR_BACKPOINTER_IMPOSSIBLE') for i in (mpeg.get('issues') or [])):
        return {'eligible':False,'reason':'existe una dependencia de reservorio no disponible o imposible antes de una región independiente demostrada'}
    plans=_segmented_partial_plan(mpeg)
    if not plans:
        return {'eligible':False,'reason':'no queda ninguna región limpia de perfil nativo recuperable de forma independiente'}
    unbracketed=[]
    for g in gaps:
        before=any(int(r['source_byte_end'])<=int(g['byte_start']) for r in plans)
        after=any(int(r['source_byte_start'])>=int(g['byte_end']) for r in plans)
        if not (before and after):unbracketed.append(int(g['index']))
    truncated=bool(facts.get('truncated_final_frame'))
    if not truncated and not unbracketed:
        return {'eligible':False,'reason':'todas las brechas están delimitadas y no existe truncamiento terminal; el subconjunto vigente tiene prioridad'}
    terminal_issue=next((i for i in (mpeg.get('issues') or []) if i.code=='TRUNCATED_MPEG_FRAME'),None)
    terminal_damage=None
    if terminal_issue is not None:
        terminal_damage={'byte_start':terminal_issue.byte_start,'byte_end':terminal_issue.byte_end}
    return {
        'eligible':True,
        'reason':'queda una o más regiones de perfil nativo decodificables independientemente aunque el daño sea terminal o no esté totalmente delimitado; los intervalos ausentes se omiten y nunca se sintetizan',
        'regions':plans,
        'open_end_mode':'TERMINAL_TRUNCATION_AND_OR_UNBRACKETED_DAMAGE',
        'truncated_final_frame':truncated,
        'terminal_damage':terminal_damage,
        'unbracketed_gap_indices':unbracketed,
        'coverage_claim':'PROVEN_REGIONS_ONLY_NO_FULL_TIMELINE_CLAIM',
    }

def _reuse_segmented_partial(source:Path,source_sha:str,plans:list[dict]):
    found=[]
    for side in source.parent.glob('*.lossydoctor-manifest.json'):
        try:d=json.loads(side.read_text(encoding='utf-8'))
        except Exception:continue
        if d.get('producer')!='LossyDoctor' or d.get('producer_version')!=APP_VERSION or d.get('derivation_schema')!=5 or d.get('source_sha256')!=source_sha or d.get('derivation_kind')!='RECOVERED_SEGMENTED_PARTIAL_LOSSLESS' or d.get('validation_result')!='PASS':continue
        op=Path(d.get('output_path',''))
        if op.exists() and sha256_file(op)==d.get('output_sha256'):found.append({'status':'REUSED','output_path':str(op),'manifest_path':str(side),'manifest':d})
    by={x['manifest'].get('part_index'):x for x in found};expected=set(range(1,len(plans)+1))
    if set(by)!=expected:return []
    for idx,plan in enumerate(plans,1):
        m=by[idx]['manifest']
        for k in ('native_profile','source_byte_start','source_byte_end','decode_context_byte_start','decode_context_byte_end','discarded_context_samples','gap_before_index'):
            if m.get(k)!=plan.get(k):return []
    return [by[i] for i in sorted(expected)]

def _export_segmented_partial(source:Path,source_sha:str,mpeg:dict,ffmpeg:str,assessment:dict,publish:bool):
    gate=assessment.get('segment_partial_recovery_gate') or {};plans=gate.get('regions') or []
    reused=_reuse_segmented_partial(source,source_sha,plans)
    if reused:return {'status':'REUSED','assessment':assessment,'outputs':reused}
    prepared=[]
    with tempfile.TemporaryDirectory(prefix='lossydoctor-damaged-segments-') as td:
        tmp=Path(td)
        for idx,plan in enumerate(plans,1):
            pr=plan.get('native_profile') or {};sr=int(pr.get('sample_rate') or 0);ch=int(pr.get('channels') or 0)
            if sr<=0 or ch<=0:return {'status':'REJECTED','reason':f'region {idx} la geometría PCM nativa no está disponible','assessment':assessment,'outputs':[]}
            d=_decode_fragment(mpeg['data'],int(plan['decode_context_byte_start']),int(plan['decode_context_byte_end']),ffmpeg,tmp,f'damaged_region_{idx:02d}',ch)
            if not d.get('passed'):return {'status':'REJECTED','reason':f'region {idx} falló la decodificación de contexto','detail':d,'assessment':assessment,'outputs':[]}
            discard=int(plan.get('discarded_context_samples') or 0);frame_bytes=ch*4
            if d['sample_count']<=discard:return {'status':'REJECTED','reason':f'region {idx} no tiene PCM después de descartar conservadoramente el contexto','assessment':assessment,'outputs':[]}
            rawpart=tmp/f'damaged_region_{idx:02d}_clean.raw';raw_start=discard*frame_bytes;raw_len=d['raw_path'].stat().st_size-raw_start
            if raw_len<=0 or raw_len%frame_bytes:return {'status':'REJECTED','reason':f'region {idx} la geometría PCM cruda no es válida después de descartar el contexto','assessment':assessment,'outputs':[]}
            with rawpart.open('wb') as o:
                with Path(d['raw_path']).open('rb') as f:f.seek(raw_start);o.write(f.read(raw_len))
            pcm_sha=sha256_file(rawpart);sample_count=raw_len//frame_bytes;profile=canonical_pcm_profile(ffmpeg,sr,ch)
            if not profile.get('decoder_binary_sha256') or profile.get('decoder_version')=='unknown':return {'status':'REJECTED','reason':'la identidad del decodificador canónico no está disponible','assessment':assessment,'outputs':[]}
            cand=tmp/f'damaged_region_{idx:02d}.flac';enc=raw_file_to_flac(rawpart,cand,ffmpeg,sr,ch)
            if not enc.get('passed'):return {'status':'REJECTED','reason':f'region {idx} falló la codificación FLAC','detail':enc,'assessment':assessment,'outputs':[]}
            back=tmp/f'damaged_region_{idx:02d}_back.raw';dec=decode_to_raw_file(cand,back,ffmpeg)
            if not dec.get('passed') or sha256_file(back)!=pcm_sha:return {'status':'REJECTED','reason':f'el hash PCM FLAC no coincide en la región {idx}','assessment':assessment,'outputs':[]}
            prepared.append((idx,plan,pr,profile,cand,pcm_sha,sample_count,sha256_file(back)))
        outputs=[]
        for idx,plan,pr,profile,cand,pcm_sha,sample_count,back_sha in prepared:
            desired=source.with_name(source.stem+f' [recovered-segmented-partial-lossless part{idx:02d}].flac')
            out=cand
            man={'schema_version':3,'producer':'LossyDoctor','producer_version':APP_VERSION,'derivation_schema':5,'derivation_kind':'RECOVERED_SEGMENTED_PARTIAL_LOSSLESS','materialization':'INDEPENDENT_NATIVE_PROFILE_CLEAN_REGION','part_index':idx,'part_count':len(prepared),'source_path':str(source),'source_sha256':source_sha,'output_path':str(out),'output_sha256':sha256_file(out),'native_profile':pr,'canonical_pcm_profile':profile,'source_byte_start':plan['source_byte_start'],'source_byte_end':plan['source_byte_end'],'source_frame_start_index':plan['source_frame_start_index'],'source_frame_end_index':plan['source_frame_end_index'],'decode_context_byte_start':plan['decode_context_byte_start'],'decode_context_byte_end':plan['decode_context_byte_end'],'preclean_tainted_frame_count':plan['preclean_tainted_frame_count'],'warmup_clean_frame_count':plan['warmup_clean_frame_count'],'discarded_context_samples':plan['discarded_context_samples'],'gap_before_index':plan.get('gap_before_index'),'gap_after_index':plan.get('gap_after_index'),'logical_frame_start':plan.get('logical_frame_start'),'logical_frame_end_exclusive':plan.get('logical_frame_end_exclusive'),'sample_count':sample_count,'source_region_pcm_sha256':pcm_sha,'flac_decoded_pcm_sha256':back_sha,'resampling':'NONE','channel_remix':'NONE','synthesized_gap_silence':[],'timeline_relation':'ORDERED_SOURCE_REGION_NO_SYNTHESIZED_DAMAGE_SPAN','validation_result':'PASS','source_modified':False,'audio_recoding':'LOSSLESS_FLAC_ONLY'}
            out,side,man,publication_status=publish_or_preview_with_manifest(cand,desired,man,publish);outputs.append({'status':publication_status,'output_path':str(out) if out is not None else None,'manifest_path':str(side) if side else None,'manifest':man})
        return {'status':combined_publication_status(outputs),'assessment':assessment,'outputs':outputs}

def _reuse_segmented_open_partial(source:Path,source_sha:str,plans:list[dict],gate:dict):
    found=[]
    for side in source.parent.glob('*.lossydoctor-manifest.json'):
        try:d=json.loads(side.read_text(encoding='utf-8'))
        except Exception:continue
        if d.get('producer')!='LossyDoctor' or d.get('producer_version')!=APP_VERSION or d.get('derivation_schema')!=6 or d.get('source_sha256')!=source_sha or d.get('derivation_kind')!='RECOVERED_SEGMENTED_OPEN_PARTIAL_LOSSLESS' or d.get('validation_result')!='PASS':continue
        op=Path(d.get('output_path',''))
        if op.exists() and sha256_file(op)==d.get('output_sha256'):found.append({'status':'REUSED','output_path':str(op),'manifest_path':str(side),'manifest':d})
    by={x['manifest'].get('part_index'):x for x in found};expected=set(range(1,len(plans)+1))
    if set(by)!=expected:return []
    for idx,plan in enumerate(plans,1):
        m=by[idx]['manifest']
        for k in ('native_profile','source_byte_start','source_byte_end','decode_context_byte_start','decode_context_byte_end','discarded_context_samples','gap_before_index'):
            if m.get(k)!=plan.get(k):return []
        if m.get('unbracketed_gap_indices')!=(gate.get('unbracketed_gap_indices') or []):return []
        if bool(m.get('truncated_final_frame'))!=bool(gate.get('truncated_final_frame')):return []
    return [by[i] for i in sorted(expected)]


def _export_segmented_open_partial(source:Path,source_sha:str,mpeg:dict,ffmpeg:str,assessment:dict,publish:bool):
    gate=assessment.get('segment_open_partial_recovery_gate') or {};plans=gate.get('regions') or []
    reused=_reuse_segmented_open_partial(source,source_sha,plans,gate)
    if reused:return {'status':'REUSED','assessment':assessment,'outputs':reused}
    prepared=[]
    with tempfile.TemporaryDirectory(prefix='lossydoctor-open-segments-') as td:
        tmp=Path(td)
        for idx,plan in enumerate(plans,1):
            pr=plan.get('native_profile') or {};sr=int(pr.get('sample_rate') or 0);ch=int(pr.get('channels') or 0)
            if sr<=0 or ch<=0:return {'status':'REJECTED','reason':f'region {idx} la geometría PCM nativa no está disponible','assessment':assessment,'outputs':[]}
            d=_decode_fragment(mpeg['data'],int(plan['decode_context_byte_start']),int(plan['decode_context_byte_end']),ffmpeg,tmp,f'open_region_{idx:02d}',ch)
            if not d.get('passed'):return {'status':'REJECTED','reason':f'region {idx} falló la decodificación de contexto','detail':d,'assessment':assessment,'outputs':[]}
            discard=int(plan.get('discarded_context_samples') or 0);frame_bytes=ch*4
            if d['sample_count']<=discard:return {'status':'REJECTED','reason':f'region {idx} no tiene PCM después de descartar conservadoramente el contexto','assessment':assessment,'outputs':[]}
            rawpart=tmp/f'open_region_{idx:02d}_proven.raw';raw_start=discard*frame_bytes;raw_len=d['raw_path'].stat().st_size-raw_start
            if raw_len<=0 or raw_len%frame_bytes:return {'status':'REJECTED','reason':f'region {idx} la geometría PCM cruda no es válida después de descartar el contexto','assessment':assessment,'outputs':[]}
            with rawpart.open('wb') as o:
                with Path(d['raw_path']).open('rb') as f:f.seek(raw_start);o.write(f.read(raw_len))
            if rawpart.stat().st_size<=0:return {'status':'REJECTED','reason':f'region {idx} produced empty PCM','assessment':assessment,'outputs':[]}
            pcm_sha=sha256_file(rawpart);sample_count=raw_len//frame_bytes;profile=canonical_pcm_profile(ffmpeg,sr,ch)
            if not profile.get('decoder_binary_sha256') or profile.get('decoder_version')=='unknown':return {'status':'REJECTED','reason':'la identidad del decodificador canónico no está disponible','assessment':assessment,'outputs':[]}
            cand=tmp/f'open_region_{idx:02d}.flac';enc=raw_file_to_flac(rawpart,cand,ffmpeg,sr,ch)
            if not enc.get('passed'):return {'status':'REJECTED','reason':f'region {idx} falló la codificación FLAC','detail':enc,'assessment':assessment,'outputs':[]}
            back=tmp/f'open_region_{idx:02d}_back.raw';dec=decode_to_raw_file(cand,back,ffmpeg)
            if not dec.get('passed') or back.stat().st_size!=rawpart.stat().st_size or sha256_file(back)!=pcm_sha:return {'status':'REJECTED','reason':f'el hash o tamaño PCM FLAC no coincide en la región {idx}','assessment':assessment,'outputs':[]}
            prepared.append((idx,plan,pr,profile,cand,pcm_sha,sample_count,sha256_file(back)))
        outputs=[]
        for idx,plan,pr,profile,cand,pcm_sha,sample_count,back_sha in prepared:
            desired=source.with_name(source.stem+f' [recovered-segmented-open-partial-lossless part{idx:02d}].flac')
            out=cand
            man={'schema_version':3,'producer':'LossyDoctor','producer_version':APP_VERSION,'derivation_schema':6,'derivation_kind':'RECOVERED_SEGMENTED_OPEN_PARTIAL_LOSSLESS','materialization':'INDEPENDENT_NATIVE_PROFILE_PROVEN_REGION','part_index':idx,'part_count':len(prepared),'source_path':str(source),'source_sha256':source_sha,'output_path':str(out),'output_sha256':sha256_file(out),'native_profile':pr,'canonical_pcm_profile':profile,'source_byte_start':plan['source_byte_start'],'source_byte_end':plan['source_byte_end'],'source_frame_start_index':plan['source_frame_start_index'],'source_frame_end_index':plan['source_frame_end_index'],'decode_context_byte_start':plan['decode_context_byte_start'],'decode_context_byte_end':plan['decode_context_byte_end'],'preclean_tainted_frame_count':plan['preclean_tainted_frame_count'],'warmup_clean_frame_count':plan['warmup_clean_frame_count'],'discarded_context_samples':plan['discarded_context_samples'],'gap_before_index':plan.get('gap_before_index'),'gap_after_index':plan.get('gap_after_index'),'logical_frame_start':plan.get('logical_frame_start'),'logical_frame_end_exclusive':plan.get('logical_frame_end_exclusive'),'sample_count':sample_count,'source_region_pcm_sha256':pcm_sha,'flac_decoded_pcm_sha256':back_sha,'resampling':'NONE','channel_remix':'NONE','synthesized_gap_silence':[],'timeline_relation':'OPEN_ENDED_SOURCE_REGIONS_NO_SYNTHESIZED_MISSING_SPAN','coverage_claim':gate.get('coverage_claim'),'truncated_final_frame':bool(gate.get('truncated_final_frame')),'terminal_damage':gate.get('terminal_damage'),'unbracketed_gap_indices':gate.get('unbracketed_gap_indices') or [],'validation_result':'PASS','source_modified':False,'audio_recoding':'LOSSLESS_FLAC_ONLY'}
            out,side,man,publication_status=publish_or_preview_with_manifest(cand,desired,man,publish);outputs.append({'status':publication_status,'output_path':str(out) if out is not None else None,'manifest_path':str(side) if side else None,'manifest':man})
        return {'status':combined_publication_status(outputs),'assessment':assessment,'outputs':outputs}



def _homogeneous_open_plan(mpeg:dict):
    """Planifica regiones demostrables para un único perfil MPEG.

    Las brechas separan regiones. Para una región posterior se entrega al
    decodificador el contexto posterior a la resincronización más un frame
    limpio, y ese PCM se descarta antes de publicar. Nunca se sintetiza el
    intervalo ausente.
    """
    facts=mpeg.get('facts') or {}; params=facts.get('parameter_segments') or {}
    if int(params.get('hard_profile_transition_count') or 0)!=0:
        return []
    audio=[f for f in mpeg.get('frames',[]) if not f.get('is_vbr_header')]
    groups=_group_clean(mpeg.get('frames',[])); gaps=facts.get('gaps') or []
    if not audio or not groups:return []
    pr={'mpeg_version':facts.get('mpeg_version'),'layer':facts.get('layer'),'sample_rate':facts.get('sample_rate'),'channels':facts.get('channels'),'samples_per_frame':facts.get('samples_per_frame')}
    spf=int(pr.get('samples_per_frame') or 0)
    if spf<=0:return []
    plans=[]
    for group in groups:
        gap=_prior_gap(group,mpeg)
        if gap is None:
            retained=group;ctx=[];warmup=0;discard=0;context_start=int(facts.get('first_audio_offset') or group[0]['byte_start'])
        else:
            ctx,_=_context_frames(group,mpeg);warmup=1
            if len(group)<=warmup:continue
            retained=group[warmup:]
            if len(retained)<2:continue
            discard=(len(ctx)+warmup)*spf
            context_start=int(ctx[0]['byte_start']) if ctx else int(group[0]['byte_start'])
        if len(retained)<2:continue
        gap_before=None if gap is None else int(gap['index'])
        gap_after=next((int(g['index']) for g in gaps if int(g.get('byte_start',-1))==int(group[-1]['byte_end'])),None)
        plans.append({
            'region_index':len(plans)+1,'native_profile':pr,
            'decode_context_byte_start':context_start,'decode_context_byte_end':int(group[-1]['byte_end']),
            'source_byte_start':int(retained[0]['byte_start']),'source_byte_end':int(retained[-1]['byte_end']),
            'source_frame_start_index':int(retained[0]['index']),'source_frame_end_index':int(retained[-1]['index']),
            'retained_frame_count':len(retained),'preclean_tainted_frame_count':len(ctx),
            'warmup_clean_frame_count':warmup,'discarded_context_samples':discard,
            'gap_before_index':gap_before,'gap_after_index':gap_after,
            'logical_frame_start':retained[0].get('logical_audio_index'),
            'logical_frame_end_exclusive':None if retained[-1].get('logical_audio_index') is None else int(retained[-1]['logical_audio_index'])+1,
        })
    return plans


def _homogeneous_open_recovery_eligibility(mpeg:dict,playability:str):
    facts=mpeg.get('facts') or {};params=facts.get('parameter_segments') or {};gaps=facts.get('gaps') or []
    if int(params.get('hard_profile_transition_count') or 0)!=0:
        return {'eligible':False,'reason':'la recuperación homogénea vigente requiere un único perfil MPEG estable'}
    if not gaps and not facts.get('truncated_final_frame'):
        return {'eligible':False,'reason':'ningún truncamiento ni brecha estructural requiere recuperación homogénea abierta'}
    if playability!='UNPLAYABLE':
        return {'eligible':False,'reason':'el stream homogéneo dañado sigue siendo reproducible; el derivado de preservación es sólo una alternativa'}
    cpw=facts.get('canonical_presentation_window') or {}
    if gaps and not facts.get('truncated_final_frame') and cpw.get('determined') and all(g.get('timeline_known') for g in gaps):
        return {'eligible':False,'reason':'la línea de tiempo exacta de brechas homogéneas ya está cubierta por la recuperación parcial establecida que preserva la línea de tiempo'}
    crc=facts.get('crc_protection') or {}
    if int(crc.get('mismatch_count') or 0)>0:
        return {'eligible':False,'reason':'la inconsistencia CRC no está suficientemente localizada para la preservación automática homogénea'}
    reservoir=facts.get('bit_reservoir') or {}
    if reservoir.get('main_data_overrun_frame_indices'):
        return {'eligible':False,'reason':'el exceso de datos principales Layer III queda fuera de la recuperación homogénea abierta'}
    if any(i.code in ('BIT_RESERVOIR_BACKPOINTER_UNAVAILABLE','BIT_RESERVOIR_BACKPOINTER_IMPOSSIBLE') for i in (mpeg.get('issues') or [])):
        return {'eligible':False,'reason':'una dependencia de reservorio no disponible o imposible impide una región independiente demostrada'}
    plans=_homogeneous_open_plan(mpeg)
    if not plans:return {'eligible':False,'reason':'no queda ninguna región homogénea demostrada decodificable de forma independiente'}
    terminal_issue=next((i for i in (mpeg.get('issues') or []) if i.code=='TRUNCATED_MPEG_FRAME'),None)
    terminal_damage=None if terminal_issue is None else {'byte_start':terminal_issue.byte_start,'byte_end':terminal_issue.byte_end}
    unbracketed=[]
    for g in gaps:
        before=any(int(r['source_byte_end'])<=int(g['byte_start']) for r in plans)
        after=any(int(r['source_byte_start'])>=int(g['byte_end']) for r in plans)
        if not (before and after):unbracketed.append(int(g['index']))
    return {'eligible':True,'reason':'una o más regiones homogéneas de perfil nativo son demostrables independientemente; la reparación verificada del bitstream conserva prioridad y los intervalos ausentes se omiten','regions':plans,
            'coverage_claim':'PROVEN_HOMOGENEOUS_REGIONS_ONLY_NO_FULL_TIMELINE_CLAIM','truncated_final_frame':bool(facts.get('truncated_final_frame')),
            'terminal_damage':terminal_damage,'unbracketed_gap_indices':unbracketed,'repair_priority':'VERIFIED_BITSTREAM_REPAIR_PRECEDES_PCM'}


def _reuse_homogeneous_open(source:Path,source_sha:str,plans:list[dict]):
    found=[]
    for side in source.parent.glob('*.lossydoctor-manifest.json'):
        try:d=json.loads(side.read_text(encoding='utf-8'))
        except Exception:continue
        if d.get('producer')!='LossyDoctor' or d.get('producer_version')!=APP_VERSION or d.get('derivation_schema')!=7 or d.get('source_sha256')!=source_sha or d.get('derivation_kind')!='RECOVERED_HOMOGENEOUS_OPEN_PARTIAL_LOSSLESS' or d.get('validation_result')!='PASS':continue
        op=Path(d.get('output_path',''))
        if op.exists() and sha256_file(op)==d.get('output_sha256'):found.append({'status':'REUSED','output_path':str(op),'manifest_path':str(side),'manifest':d})
    by={x['manifest'].get('part_index'):x for x in found};expected=set(range(1,len(plans)+1))
    if set(by)!=expected:return []
    for idx,plan in enumerate(plans,1):
        m=by[idx]['manifest']
        for k in ('native_profile','source_byte_start','source_byte_end','decode_context_byte_start','decode_context_byte_end','discarded_context_samples','gap_before_index','gap_after_index'):
            if m.get(k)!=plan.get(k):return []
    return [by[i] for i in sorted(expected)]


def _export_homogeneous_open(source:Path,source_sha:str,mpeg:dict,ffmpeg:str,assessment:dict,publish:bool):
    gate=assessment.get('homogeneous_open_recovery_gate') or {};plans=gate.get('regions') or []
    reused=_reuse_homogeneous_open(source,source_sha,plans)
    if reused:return {'status':'REUSED','assessment':assessment,'outputs':reused}
    prepared=[]
    with tempfile.TemporaryDirectory(prefix='lossydoctor-homogeneous-open-') as td:
        tmp=Path(td)
        for idx,plan in enumerate(plans,1):
            pr=plan.get('native_profile') or {};sr=int(pr.get('sample_rate') or 0);ch=int(pr.get('channels') or 0)
            if sr<=0 or ch<=0:return {'status':'REJECTED','reason':f'region {idx} la geometría PCM nativa no está disponible','assessment':assessment,'outputs':[]}
            d=_decode_fragment(mpeg['data'],int(plan['decode_context_byte_start']),int(plan['decode_context_byte_end']),ffmpeg,tmp,f'homogeneous_open_{idx:02d}',ch)
            if not d.get('passed'):return {'status':'REJECTED','reason':f'region {idx} falló la decodificación de contexto','detail':d,'assessment':assessment,'outputs':[]}
            discard=int(plan.get('discarded_context_samples') or 0);frame_bytes=ch*4;raw_start=discard*frame_bytes;raw_len=Path(d['raw_path']).stat().st_size-raw_start
            if raw_len<=0 or raw_len%frame_bytes:return {'status':'REJECTED','reason':f'region {idx} tiene PCM no válido o vacío después de descartar el contexto','assessment':assessment,'outputs':[]}
            rawpart=tmp/f'homogeneous_open_{idx:02d}_proven.raw'
            with Path(d['raw_path']).open('rb') as f,rawpart.open('wb') as o:f.seek(raw_start);shutil.copyfileobj(f,o)
            if rawpart.stat().st_size!=raw_len or rawpart.stat().st_size<=0:return {'status':'REJECTED','reason':f'region {idx} PCM extraction short/empty','assessment':assessment,'outputs':[]}
            pcm_sha=sha256_file(rawpart);sample_count=raw_len//frame_bytes;profile=canonical_pcm_profile(ffmpeg,sr,ch)
            if not profile.get('decoder_binary_sha256') or profile.get('decoder_version')=='unknown':return {'status':'REJECTED','reason':'la identidad del decodificador canónico no está disponible','assessment':assessment,'outputs':[]}
            cand=tmp/f'homogeneous_open_{idx:02d}.flac';enc=raw_file_to_flac(rawpart,cand,ffmpeg,sr,ch)
            if not enc.get('passed'):return {'status':'REJECTED','reason':f'region {idx} falló la codificación FLAC','detail':enc,'assessment':assessment,'outputs':[]}
            back=tmp/f'homogeneous_open_{idx:02d}_back.raw';dec=decode_to_raw_file(cand,back,ffmpeg);back_sha=sha256_file(back) if back.exists() else None
            if not dec.get('passed') or back.stat().st_size<=0 or back_sha!=pcm_sha:return {'status':'REJECTED','reason':f'la verificación round-trip del PCM FLAC no coincide o está vacía en la región {idx}','assessment':assessment,'outputs':[]}
            prepared.append((idx,plan,cand,pcm_sha,back_sha,sample_count,profile))
        outputs=[]
        for idx,plan,cand,pcm_sha,back_sha,sample_count,profile in prepared:
            desired=source.with_name(source.stem+f' [recovered-homogeneous-open-partial-lossless part{idx:02d}].flac');out=cand
            pr=plan.get('native_profile') or {}
            man={'schema_version':3,'producer':'LossyDoctor','producer_version':APP_VERSION,'derivation_schema':7,'derivation_kind':'RECOVERED_HOMOGENEOUS_OPEN_PARTIAL_LOSSLESS','materialization':'INDEPENDENT_HOMOGENEOUS_PROVEN_REGION','part_index':idx,'part_count':len(prepared),'source_path':str(source),'source_sha256':source_sha,'output_path':str(out),'output_sha256':sha256_file(out),'native_profile':pr,'canonical_pcm_profile':profile,'source_byte_start':plan['source_byte_start'],'source_byte_end':plan['source_byte_end'],'source_frame_start_index':plan['source_frame_start_index'],'source_frame_end_index':plan['source_frame_end_index'],'decode_context_byte_start':plan['decode_context_byte_start'],'decode_context_byte_end':plan['decode_context_byte_end'],'preclean_tainted_frame_count':plan['preclean_tainted_frame_count'],'warmup_clean_frame_count':plan['warmup_clean_frame_count'],'discarded_context_samples':plan['discarded_context_samples'],'gap_before_index':plan.get('gap_before_index'),'gap_after_index':plan.get('gap_after_index'),'sample_count':sample_count,'source_region_pcm_sha256':pcm_sha,'flac_decoded_pcm_sha256':back_sha,'coverage_claim':gate.get('coverage_claim'),'repair_priority':gate.get('repair_priority'),'truncated_final_frame':bool(gate.get('truncated_final_frame')),'terminal_damage':gate.get('terminal_damage'),'unbracketed_gap_indices':gate.get('unbracketed_gap_indices') or [],'timeline_relation':'HOMOGENEOUS_PROVEN_REGIONS_NO_SYNTHESIZED_MISSING_SPAN','resampling':'NONE','channel_remix':'NONE','synthesized_gap_silence':[],'validation_result':'PASS','source_modified':False,'audio_recoding':'LOSSLESS_FLAC_ONLY'}
            out,side,man,publication_status=publish_or_preview_with_manifest(cand,desired,man,publish);outputs.append({'status':publication_status,'output_path':str(out) if out is not None else None,'manifest_path':str(side) if side else None,'manifest':man})
        return {'status':combined_publication_status(outputs),'assessment':assessment,'outputs':outputs}

def assess(mpeg:dict,playability:str):
    frames=mpeg['frames']; groups=_group_clean(frames); audio=[f for f in frames if not f['is_vbr_header']]
    clean=sum(1 for f in audio if f['clean']); cpw=mpeg['facts']['canonical_presentation_window']
    params=(mpeg.get('facts') or {}).get('parameter_segments') or {}
    if params.get('hard_profile_transition_count',0)>0:
        elig=_segment_recovery_eligibility(mpeg,playability)
        partial=_segment_partial_recovery_eligibility(mpeg,playability)
        open_partial=_segment_open_partial_recovery_eligibility(mpeg,playability)
        if elig.get('eligible'):reason=elig.get('reason')
        elif partial.get('eligible'):reason=partial.get('reason')
        elif open_partial.get('eligible'):reason=open_partial.get('reason')
        else:reason=partial.get('reason') if (mpeg.get('facts') or {}).get('gaps') else (open_partial.get('reason') if (mpeg.get('facts') or {}).get('truncated_final_frame') else elig.get('reason') or partial.get('reason') or open_partial.get('reason'))
        return {'pcm_class':'HETEROGENEOUS_STREAM','clean_groups':len(groups),'eligible_partial':False,'eligible_complete':False,
                'eligible_segmented':bool(elig.get('eligible')),'segment_recovery_gate':elig,
                'eligible_segmented_partial':bool(partial.get('eligible')),'segment_partial_recovery_gate':partial,
                'eligible_segmented_open_partial':bool(open_partial.get('eligible')),'segment_open_partial_recovery_gate':open_partial,
                'reason':reason or 'las transiciones fuertes de parámetros MPEG requieren recuperación PCM sensible a segmentos'}
    homogeneous_open=_homogeneous_open_recovery_eligibility(mpeg,playability)
    if not audio or not groups:return {'pcm_class':'UNAVAILABLE','clean_groups':0,'eligible_partial':False,'eligible_complete':False,'eligible_segmented':False,'eligible_homogeneous_open_partial':False,'homogeneous_open_recovery_gate':homogeneous_open}
    if clean==len(audio) and cpw.get('determined') and not homogeneous_open.get('eligible'):
        return {'pcm_class':'COMPLETE_CLEAN','clean_groups':len(groups),'eligible_complete':True,'eligible_partial':False,'eligible_segmented':False,'eligible_homogeneous_open_partial':False,'homogeneous_open_recovery_gate':homogeneous_open}
    return {'pcm_class':'PARTIAL_CLEAN','clean_groups':len(groups),'eligible_complete':False,'eligible_partial':playability=='UNPLAYABLE','eligible_segmented':False,'eligible_homogeneous_open_partial':bool(homogeneous_open.get('eligible')),'homogeneous_open_recovery_gate':homogeneous_open,'reason':homogeneous_open.get('reason') if homogeneous_open.get('eligible') else None}

def _public_region(r:dict):
    keep=('sample_count','pcm_sha256','source_start_sample','source_end_sample','logical_frame_start','logical_frame_end_exclusive','source_byte_start','source_byte_end','output_start_sample','output_end_sample','type','decode_context_byte_start','discarded_context_samples','context_frame_count')
    return {k:r.get(k) for k in keep if k in r}

def _reuse_all(source:Path,source_sha:str,kind:str,canonical_profile:dict):
    found=[]
    for side in source.parent.glob('*.lossydoctor-manifest.json'):
        try:d=json.loads(side.read_text(encoding='utf-8'))
        except Exception:continue
        if d.get('producer')!='LossyDoctor' or d.get('producer_version')!=APP_VERSION or d.get('derivation_schema')!=3 or d.get('source_sha256')!=source_sha or d.get('derivation_kind')!=kind or d.get('validation_result')!='PASS' or d.get('canonical_pcm_profile')!=canonical_profile:continue
        op=Path(d.get('output_path',''))
        if op.exists() and sha256_file(op)==d.get('output_sha256'):
            found.append({'status':'REUSED','output_path':str(op),'manifest_path':str(side),'manifest':d})
    found.sort(key=lambda x:(x['manifest'].get('part_index') or 0,x['output_path'].casefold()))
    return found

def _valid_reuse_set(reused:list[dict],kind:str,cls:str,mapped:bool,group_count:int):
    if not reused:return []
    if kind=='RECOVERED_LOSSLESS':
        q=[x for x in reused if x['manifest'].get('derivation_kind')=='RECOVERED_LOSSLESS']
        return q[:1] if len(q)==1 else []
    timeline=[x for x in reused if x['manifest'].get('materialization')=='TIMELINE_PRESERVED_ZERO_GAPS']
    if len(timeline)==1:return timeline
    parts=[x for x in reused if x['manifest'].get('materialization')=='INDEPENDENT_PART_UNKNOWN_TIMELINE']
    by={x['manifest'].get('part_index'):x for x in parts}
    expected=set(range(1,group_count+1))
    return [by[i] for i in sorted(expected)] if set(by)==expected else []


def _reuse_segmented(source:Path,source_sha:str,segments:list[dict]):
    found=[]
    for side in source.parent.glob('*.lossydoctor-manifest.json'):
        try:d=json.loads(side.read_text(encoding='utf-8'))
        except Exception:continue
        if d.get('producer')!='LossyDoctor' or d.get('producer_version')!=APP_VERSION or d.get('derivation_schema')!=4 or d.get('source_sha256')!=source_sha or d.get('derivation_kind')!='RECOVERED_SEGMENTED_LOSSLESS' or d.get('validation_result')!='PASS':continue
        op=Path(d.get('output_path',''))
        if op.exists() and sha256_file(op)==d.get('output_sha256'):found.append({'status':'REUSED','output_path':str(op),'manifest_path':str(side),'manifest':d})
    by={x['manifest'].get('segment_index'):x for x in found}
    expected=set(range(1,len(segments)+1))
    if set(by)!=expected:return []
    for idx,seg in enumerate(segments,1):
        m=by[idx]['manifest']; pr=seg.get('profile') or {}
        if m.get('native_profile')!=pr or m.get('source_byte_start')!=seg.get('byte_start') or m.get('source_byte_end')!=seg.get('byte_end'):return []
    return [by[i] for i in sorted(expected)]

def _export_segmented(source:Path,source_sha:str,mpeg:dict,ffmpeg:str,assessment:dict,publish:bool):
    segments=((mpeg.get('facts') or {}).get('parameter_segments') or {}).get('segments') or []
    reused=_reuse_segmented(source,source_sha,segments)
    if reused:return {'status':'REUSED','assessment':assessment,'outputs':reused}
    outputs=[]
    with tempfile.TemporaryDirectory(prefix='lossydoctor-segmented-') as td:
        tmp=Path(td)
        for idx,seg in enumerate(segments,1):
            pr=seg.get('profile') or {}; sr=int(pr.get('sample_rate') or 0); ch=int(pr.get('channels') or 0)
            if sr<=0 or ch<=0:return {'status':'REJECTED','reason':f'segment {idx} la geometría PCM nativa no está disponible','assessment':assessment,'outputs':[]}
            d=_decode_fragment(mpeg['data'],int(seg['byte_start']),int(seg['byte_end']),ffmpeg,tmp,f'segment_{idx:02d}',ch)
            if not d.get('passed'):return {'status':'REJECTED','reason':f'segment {idx} falló la decodificación independiente','detail':d,'assessment':assessment,'outputs':[]}
            raw=Path(d['raw_path']); pcm_sha=sha256_file(raw); profile=canonical_pcm_profile(ffmpeg,sr,ch)
            if not profile.get('decoder_binary_sha256') or profile.get('decoder_version')=='unknown':return {'status':'REJECTED','reason':'la identidad del decodificador canónico no está disponible','assessment':assessment,'outputs':[]}
            cand=tmp/f'segment_{idx:02d}.flac'; enc=raw_file_to_flac(raw,cand,ffmpeg,sr,ch)
            if not enc.get('passed'):return {'status':'REJECTED','reason':f'segment {idx} falló la codificación FLAC','detail':enc,'assessment':assessment,'outputs':[]}
            back=tmp/f'segment_{idx:02d}_back.raw'; dec=decode_to_raw_file(cand,back,ffmpeg)
            if not dec.get('passed') or sha256_file(back)!=pcm_sha:return {'status':'REJECTED','reason':f'el hash PCM FLAC no coincide en el segmento {idx}','assessment':assessment,'outputs':[]}
            desired=source.with_name(source.stem+f' [recovered-segmented-lossless seg{idx:02d}].flac')
            out=cand
            man={'schema_version':3,'producer':'LossyDoctor','producer_version':APP_VERSION,'derivation_schema':4,'derivation_kind':'RECOVERED_SEGMENTED_LOSSLESS','materialization':'INDEPENDENT_NATIVE_PROFILE_SEGMENT','segment_index':idx,'segment_count':len(segments),'source_pcm_recovery_class':'HETEROGENEOUS_STREAM','source_path':str(source),'source_sha256':source_sha,'source_byte_start':seg.get('byte_start'),'source_byte_end':seg.get('byte_end'),'source_frame_start_index':seg.get('frame_start_index'),'source_frame_end_index':seg.get('frame_end_index'),'native_profile':pr,'canonical_pcm_profile':profile,'sample_count':d.get('sample_count'),'source_segment_pcm_sha256':pcm_sha,'flac_decoded_pcm_sha256':sha256_file(back),'output_path':str(out),'output_sha256':sha256_file(out),'timeline_relation':'ORDERED_SOURCE_SEGMENT_NO_CROSS_PROFILE_SAMPLE_AXIS','resampling':'NONE','channel_remix':'NONE','synthesized_gap_silence':[],'validation_result':'PASS','source_modified':False,'audio_recoding':'LOSSLESS_FLAC_ONLY'}
            out,side,man,publication_status=publish_or_preview_with_manifest(cand,desired,man,publish)
            outputs.append({'status':publication_status,'output_path':str(out) if out is not None else None,'manifest_path':str(side) if side else None,'manifest':man})
    return {'status':combined_publication_status(outputs),'assessment':assessment,'outputs':outputs}

def export(source:Path,source_sha:str,mpeg:dict,ffmpeg:str,playability:str,publish=True):
    assessment=assess(mpeg,playability); cls=assessment['pcm_class']
    if cls=='HETEROGENEOUS_STREAM':
        if assessment.get('eligible_segmented'):
            return _export_segmented(source,source_sha,mpeg,ffmpeg,assessment,publish)
        if assessment.get('eligible_segmented_partial'):
            return _export_segmented_partial(source,source_sha,mpeg,ffmpeg,assessment,publish)
        if assessment.get('eligible_segmented_open_partial'):
            return _export_segmented_open_partial(source,source_sha,mpeg,ffmpeg,assessment,publish)
        return {'status':'POLICY_BLOCKED','reason':assessment.get('reason'),'assessment':assessment,'outputs':[]}
    if cls not in ('COMPLETE_CLEAN','PARTIAL_CLEAN'):return {'status':'NOT_ELIGIBLE','assessment':assessment,'outputs':[]}
    if assessment.get('eligible_homogeneous_open_partial'):
        return _export_homogeneous_open(source,source_sha,mpeg,ffmpeg,assessment,publish)
    if cls=='PARTIAL_CLEAN' and playability!='UNPLAYABLE':return {'status':'POLICY_BLOCKED','reason':'la fuente PARTIAL_CLEAN es PLAYABLE; el audio permanece sin cambios','assessment':assessment,'outputs':[]}
    kind='RECOVERED_LOSSLESS' if cls=='COMPLETE_CLEAN' else 'RECOVERED_PARTIAL_LOSSLESS'
    facts=mpeg['facts']; cpw=facts['canonical_presentation_window']; sr=facts['sample_rate'];ch=facts['channels'];spf=facts['samples_per_frame'];groups=_group_clean(mpeg['frames']); profile=canonical_pcm_profile(ffmpeg,sr,ch)
    if not profile.get('decoder_binary_sha256') or profile.get('decoder_version')=='unknown':return {'status':'REJECTED','reason':'la identidad del decodificador canónico no está disponible','assessment':assessment,'outputs':[]}
    # La reutilización necesita conocer la forma esperada. La cantidad de partes surge de
    # los grupos estructurales; preservar la línea temporal sólo es válido si todos tienen
    # índices lógicos estables.
    expected_mapped=bool(cpw.get('determined')) and all(g[0].get('logical_audio_index') is not None and g[-1].get('logical_audio_index') is not None for g in groups)
    reused=_valid_reuse_set(_reuse_all(source,source_sha,kind,profile),kind,cls,expected_mapped,len(groups))
    if reused:return {'status':'REUSED','assessment':assessment,'outputs':reused}
    with tempfile.TemporaryDirectory(prefix='lossydoctor-lossless-') as td:
        tmp=Path(td)
        if cls=='COMPLETE_CLEAN':
            # Para una recuperación limpia completa se decodifica todo el stream elemental MPEG,
            # incluido su frame Xing/Info, para que el recorte gapless sea canónico.
            whole=_decode_fragment(mpeg['data'],facts['first_audio_offset'],facts['scan_end_offset'],ffmpeg,tmp,'complete_clean',ch)
            if not whole['passed']:return {'status':'REJECTED','reason':'falló la decodificación MPEG completa y limpia','detail':whole,'assessment':assessment,'outputs':[]}
            total=cpw['logical_sample_count']
            if whole['sample_count']!=total:return {'status':'REJECTED','reason':f'cantidad de muestras completa y limpia {whole["sample_count"]} != canonical {total}','assessment':assessment,'outputs':[]}
            regs=[{'passed':True,'raw_path':whole['raw_path'],'raw_byte_start':0,'raw_byte_length':total*ch*4,'sample_count':total,'pcm_sha256':sha256_file(whole['raw_path']),'source_start_sample':0,'source_end_sample':total,'output_start_sample':0,'output_end_sample':total,'type':'CLEAN_ORIGINAL_PCM','logical_frame_start':0,'logical_frame_end_exclusive':cpw.get('audio_frame_count'),'source_byte_start':facts['first_audio_offset'],'source_byte_end':facts['scan_end_offset'],'decode_context_byte_start':facts['first_audio_offset'],'discarded_context_samples':0}]
            mapped=True; extraction={'mapping_calibration':None}
        else:
            extraction=_extract_regions(mpeg,ffmpeg,tmp,ch,spf,cpw)
            if not extraction['passed']:return {'status':'REJECTED','reason':'falló la verificación de decodificación de la región limpia','detail':extraction,'assessment':assessment,'outputs':[]}
            regs=extraction['regions']; mapped=extraction['mapped']
        # COMPLETE_CLEAN: concatena regiones canónicas, normalmente uno o más grupos contiguos.
        if cls=='COMPLETE_CLEAN':
            total=cpw['logical_sample_count']; frame_bytes=ch*4; raw=tmp/'canonical.raw'
            with raw.open('wb') as o:
                cursor=0
                for r in sorted(regs,key=lambda z:z['source_start_sample']):
                    if r['source_start_sample']!=cursor:return {'status':'REJECTED','reason':'las regiones completas y limpias no son contiguas','assessment':assessment,'outputs':[]}
                    with Path(r['raw_path']).open('rb') as f:f.seek(r['raw_byte_start']);o.write(f.read(r['raw_byte_length']))
                    cursor=r['source_end_sample']
                if cursor!=total:return {'status':'REJECTED','reason':'las regiones completas y limpias no cubren la ventana canónica','assessment':assessment,'outputs':[]}
            candidate=tmp/'candidate.flac'; enc=raw_file_to_flac(raw,candidate,ffmpeg,sr,ch)
            if not enc['passed']:return {'status':'REJECTED','reason':'falló la codificación FLAC','detail':enc,'assessment':assessment,'outputs':[]}
            back=tmp/'back.raw'; dec=decode_to_raw_file(candidate,back,ffmpeg)
            if not dec['passed'] or sha256_file(raw)!=sha256_file(back):return {'status':'REJECTED','reason':'el hash PCM FLAC no coincide','assessment':assessment,'outputs':[]}
            desired=source.with_name(source.stem+' [recovered-lossless].flac')
            out=candidate
            man={'schema_version':3,'producer':'LossyDoctor','producer_version':APP_VERSION,'derivation_schema':3,'derivation_kind':kind,'source_pcm_recovery_class':cls,'source_path':str(source),'source_sha256':source_sha,'output_path':str(out),'output_sha256':sha256_file(out),'canonical_pcm_profile':profile,'canonical_presentation_window':cpw,'sample_count':total,'source_canonical_pcm_sha256':sha256_file(raw),'flac_canonical_pcm_sha256':sha256_file(back),'flac_decoded_pcm_sha256':sha256_file(back),'regions':[_public_region(r) for r in regs],'synthesized_gap_silence':[],'validation_result':'PASS','source_modified':False,'audio_recoding':'LOSSLESS_FLAC_ONLY'}
            out,side,man,publication_status=publish_or_preview_with_manifest(candidate,desired,man,publish)
            return {'status':publication_status,'assessment':assessment,'outputs':[{'status':publication_status,'output_path':str(out) if out is not None else None,'manifest_path':str(side) if side else None,'manifest':man}]}
        # PARTIAL_CLEAN: prefiere un único FLAC con línea temporal sólo si ésta se conoce por completo y todas las regiones están mapeadas.
        outputs=[]
        if mapped:
            total=cpw['logical_sample_count']; frame_bytes=ch*4; raw=tmp/'partial_timeline.raw'
            with raw.open('wb') as o:
                zero=b'\0'*(1024*1024); remaining=total*frame_bytes
                while remaining:
                    b=zero[:min(len(zero),remaining)];o.write(b);remaining-=len(b)
            with raw.open('r+b') as o:
                for r in regs:
                    o.seek(r['source_start_sample']*frame_bytes)
                    with Path(r['raw_path']).open('rb') as f:f.seek(r['raw_byte_start']);o.write(f.read(r['raw_byte_length']))
            # Deriva intervalos de brecha como complemento de las regiones genuinas.
            gaps=[];cur=0
            for r in sorted(regs,key=lambda z:z['source_start_sample']):
                if r['source_start_sample']>cur:gaps.append({'type':'SYNTHESIZED_GAP_SILENCE','output_start_sample':cur,'output_end_sample':r['source_start_sample'],'sample_count':r['source_start_sample']-cur})
                cur=max(cur,r['source_end_sample'])
            if cur<total:gaps.append({'type':'SYNTHESIZED_GAP_SILENCE','output_start_sample':cur,'output_end_sample':total,'sample_count':total-cur})
            cand=tmp/'partial.flac'; enc=raw_file_to_flac(raw,cand,ffmpeg,sr,ch)
            if not enc['passed']:return {'status':'REJECTED','reason':'falló la codificación FLAC parcial','detail':enc,'assessment':assessment,'outputs':[]}
            back=tmp/'partial_back.raw';dec=decode_to_raw_file(cand,back,ffmpeg)
            if not dec['passed'] or back.stat().st_size!=raw.stat().st_size:return {'status':'REJECTED','reason':'falló la verificación de decodificación o tamaño del FLAC parcial','assessment':assessment,'outputs':[]}
            for r in regs:
                off=r['source_start_sample']*frame_bytes
                if _hash_range(back,off,r['raw_byte_length'])!=r['pcm_sha256']:return {'status':'REJECTED','reason':'el hash PCM regional no coincide después de FLAC','assessment':assessment,'outputs':[]}
                r['output_start_sample']=r['source_start_sample'];r['output_end_sample']=r['source_end_sample'];r['type']='CLEAN_ORIGINAL_PCM'
            for g in gaps:
                if not _all_zero(back,g['output_start_sample']*frame_bytes,g['sample_count']*frame_bytes):return {'status':'REJECTED','reason':'la brecha sintetizada no es PCM cero exacto','assessment':assessment,'outputs':[]}
            desired=source.with_name(source.stem+' [recovered-partial-lossless].flac')
            out=cand
            man={'schema_version':3,'producer':'LossyDoctor','producer_version':APP_VERSION,'derivation_schema':3,'derivation_kind':kind,'materialization':'TIMELINE_PRESERVED_ZERO_GAPS','source_path':str(source),'source_sha256':source_sha,'output_path':str(out),'output_sha256':sha256_file(out),'canonical_pcm_profile':profile,'canonical_presentation_window':cpw,'mapping_calibration':extraction.get('mapping_calibration'),'regions':[_public_region(r) for r in regs],'synthesized_gap_silence':gaps,'validation_result':'PASS','source_modified':False,'audio_recoding':'LOSSLESS_FLAC_ONLY'}
            out,side,man,publication_status=publish_or_preview_with_manifest(cand,desired,man,publish);outputs.append({'status':publication_status,'output_path':str(out) if out is not None else None,'manifest_path':str(side) if side else None,'manifest':man})
        else:
            # Línea temporal desconocida: un FLAC independiente por grupo limpio, sin inventar separaciones.
            for idx,r in enumerate(regs,1):
                rawpart=tmp/f'part{idx:02d}.raw'
                with rawpart.open('wb') as o:
                    with Path(r['raw_path']).open('rb') as f:f.seek(r['raw_byte_start']);o.write(f.read(r['raw_byte_length']))
                cand=tmp/f'part{idx:02d}.flac';enc=raw_file_to_flac(rawpart,cand,ffmpeg,sr,ch)
                if not enc['passed']:return {'status':'REJECTED','reason':f'part {idx} falló la codificación FLAC','detail':enc,'assessment':assessment,'outputs':[]}
                back=tmp/f'part{idx:02d}_back.raw';dec=decode_to_raw_file(cand,back,ffmpeg)
                if not dec['passed'] or sha256_file(rawpart)!=sha256_file(back):return {'status':'REJECTED','reason':f'El hash PCM de la parte {idx} no coincide','assessment':assessment,'outputs':[]}
                desired=source.with_name(source.stem+f' [recovered-partial-lossless part{idx:02d}].flac')
                out=cand
                rr=dict(r);rr['type']='CLEAN_ORIGINAL_PCM';rr['output_start_sample']=0;rr['output_end_sample']=r['sample_count']
                man={'schema_version':3,'producer':'LossyDoctor','producer_version':APP_VERSION,'derivation_schema':3,'derivation_kind':kind,'materialization':'INDEPENDENT_PART_UNKNOWN_TIMELINE','part_index':idx,'source_path':str(source),'source_sha256':source_sha,'output_path':str(out),'output_sha256':sha256_file(out),'canonical_pcm_profile':profile,'regions':[_public_region(rr)],'synthesized_gap_silence':[],'external_mapping':{'source_byte_start':r['source_byte_start'],'source_byte_end':r['source_byte_end'],'logical_frame_start':r['logical_frame_start'],'logical_frame_end_exclusive':r['logical_frame_end_exclusive'],'timeline_position_known':r['source_start_sample'] is not None},'validation_result':'PASS','source_modified':False,'audio_recoding':'LOSSLESS_FLAC_ONLY'}
                out,side,man,publication_status=publish_or_preview_with_manifest(cand,desired,man,publish);outputs.append({'status':publication_status,'output_path':str(out) if out is not None else None,'manifest_path':str(side) if side else None,'manifest':man})
        return {'status':combined_publication_status(outputs),'assessment':assessment,'outputs':outputs}
