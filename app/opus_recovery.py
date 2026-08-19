from __future__ import annotations
from pathlib import Path
import hashlib, json, tempfile
from app.external import canonical_pcm_profile, decode_to_raw_file, raw_file_to_flac
from app.publication import combined_publication_status, publish_or_preview_with_manifest
from app.utils import sha256_file, sha256_bytes
from app.version import APP_VERSION
from formats.ogg_opus import ogg_crc, opus_packet_samples

PRE_ROLL_SAMPLES_48K=3840
DERIVATION_KIND='RECOVERED_OPUS_PROVEN_REGION_LOSSLESS'
MATERIALIZATION='OPUS_PACKET_REGION_WITH_RFC7845_PREROLL_AND_EXACT_EOS_TRIM'
GAIN_POLICY='PRESERVE_UNAPPLIED_Q7_8_IN_MANIFEST'

_BLOCKING_CODES={
    'OPUS_HEAD_INVALID','OPUS_TAGS_INVALID','OPUS_AUDIO_PACKET_MALFORMED',
    'OPUS_HEADER_GRANULE_NONZERO','OPUS_BOS_HEADER_PAGE_INVALID',
    'OPUS_HEAD_PAGE_LAYOUT_INVALID','OPUS_TAGS_PAGE_LAYOUT_INVALID',
    'OGG_VERSION_UNSUPPORTED','OGG_CONTINUATION_FLAG_INCONSISTENT',
    'OPUS_GRANULE_POSITION_NONMONOTONIC','OPUS_END_TRIM_INVALID',
}
_RECOVERY_DAMAGE_CODES={
    'OGG_SYNC_LOSS','OGG_PAGE_CRC_MISMATCH','OGG_PAGE_SEQUENCE_DISCONTINUITY',
    'OGG_TRUNCATED_PAGE','OGG_INCOMPLETE_PACKET_AT_EOF','OGG_OPUS_EOS_MISSING',
    'OPUS_GRANULE_DELTA_MISMATCH','OPUS_GRANULE_POSITION_MISSING',
}

def _page_packets(data:bytes,pg:dict):
    """Devuelve paquetes que terminan en una página e indica si su inicio está presente."""
    body=data[int(pg['body_start']):int(pg['body_end'])]
    bp=0; cur=bytearray(); out=[]; clean_start=not bool(pg.get('continued')); start=int(pg['body_start'])
    for lace in pg.get('lacing_values') or []:
        cur.extend(body[bp:bp+lace]); bp+=lace
        if lace<255:
            out.append({'data':bytes(cur),'self_contained':clean_start,'byte_start':start,'byte_end':int(pg['body_start'])+bp})
            cur=bytearray(); clean_start=True; start=int(pg['body_start'])+bp
    return out

def _extract_headers(data:bytes,pages:list[dict]):
    head=tags=None; head_page=tags_page=None
    for pg in pages:
        if not pg.get('crc_ok') or int(pg.get('version',-1))!=0: continue
        for pkt in _page_packets(data,pg):
            if not pkt['self_contained']: continue
            b=pkt['data']
            if head is None and b.startswith(b'OpusHead'):
                head=b; head_page=pg
            elif head is not None and tags is None and b.startswith(b'OpusTags'):
                tags=b; tags_page=pg; return head,tags,head_page,tags_page
    return head,tags,head_page,tags_page

def _sync_between(issues:list,lo:int,hi:int):
    for i in issues:
        if getattr(i,'code',None)!='OGG_SYNC_LOSS':continue
        a=getattr(i,'byte_start',None);b=getattr(i,'byte_end',None)
        if a is None or b is None:continue
        if a < hi and b > lo:return True
    return False

def _trusted_page_packets(data:bytes,pg:dict):
    if not pg.get('crc_ok') or int(pg.get('version',-1))!=0 or pg.get('eos') or int(pg.get('granule_position',-1))<0:
        return []
    completed=_page_packets(data,pg)
    # Asigna límites absolutos exactos recorriendo hacia atrás desde el gránulo
    # autenticado de una página no EOS. Un paquete inicial incompleto detiene el
    # recorrido sólo después de asignar posiciones a los paquetes posteriores.
    end=int(pg['granule_position']); rev=[]
    for pkt in reversed(completed):
        dur=opus_packet_samples(pkt['data']) if pkt['self_contained'] else None
        if dur is None: break
        start=end-dur
        rev.append({**pkt,'duration_samples_48k':dur,'source_granule_start':start,'source_granule_end':end,
                    'page_index':pg['index'],'page_sequence':pg['sequence'],'page_byte_start':pg['byte_start'],'page_byte_end':pg['byte_end'],
                    'page_crc32':pg['stored_crc32']})
        end=start
    return list(reversed(rev))

def _packet_bytes(data:bytes,entry:dict):
    parts=[]
    for sp in entry.get('segment_byte_spans') or []:
        a=int(sp.get('byte_start',-1));b=int(sp.get('byte_end',-1))
        if a<0 or b<a or b>len(data):return None
        parts.append(data[a:b])
    pkt=b''.join(parts)
    return pkt if pkt and sha256_bytes(pkt)==entry.get('packet_sha256') else None

def _trusted_packet_runs(q:dict,source:Path):
    data=source.read_bytes();facts=q.get('facts') or {};entries=facts.get('audio_packet_map') or [];issues=q.get('issues') or []
    pages={int(p['index']):p for p in (q.get('structural_map') or [])}
    runs=[];current=[];prev=None
    def flush():
        nonlocal current,prev
        if current:runs.append(current)
        current=[];prev=None
    for e in sorted(entries,key=lambda x:int(x.get('packet_index',0))):
        if not e.get('crc_authenticated_complete_packet') or e.get('duration_samples_48k') is None or e.get('decoded_granule_start') is None or e.get('decoded_granule_end') is None:
            flush();continue
        pkt=_packet_bytes(data,e)
        if pkt is None:flush();continue
        x=dict(e);x['data']=pkt
        if prev is not None:
            time_ok=int(x['decoded_granule_start'])==int(prev['decoded_granule_end'])
            aseq=(prev.get('page_sequences') or [None])[-1];bseq=(x.get('page_sequences') or [None])[0]
            seq_ok=aseq is not None and bseq is not None and (int(bseq)==int(aseq) or int(bseq)==((int(aseq)+1)&0xffffffff))
            physical_ok=True
            ap=(prev.get('page_indices') or [None])[-1];bp=(x.get('page_indices') or [None])[0]
            if ap is not None and bp is not None and int(ap)!=int(bp):
                pa=pages.get(int(ap));pb=pages.get(int(bp))
                if not pa or not pb or _sync_between(issues,int(pa['byte_end']),int(pb['byte_start'])):physical_ok=False
            if not (time_ok and seq_ok and physical_ok):flush()
        current.append(x);prev=x
    flush();return runs

def assess(source:Path,q:dict,playability:str):
    facts=q.get('facts') or {};h=facts.get('opus_head') or {};tags=facts.get('opus_tags') or {};codes={i.code for i in (q.get('issues') or [])}
    base={'schema':2,'policy':'OPUS_PROVEN_REGION_RECOVERY_RFC7845_PREROLL_EOS_TRIM_V2','pre_roll_samples_48k':PRE_ROLL_SAMPLES_48K,
          'output_gain_policy':GAIN_POLICY,'output_gain_baked_into_pcm':False,'coverage_claim':'PROVEN_PACKET_REGIONS_WITH_EXACT_EOS_TRIM_NO_FULL_TIMELINE_CLAIM'}
    if not codes and playability=='PLAYABLE':return {**base,'pcm_class':'NOT_REQUIRED','eligible':False,'reason':'el stream Ogg Opus reproducible y conforme no requiere recuperación PCM','regions':[]}
    if playability!='UNPLAYABLE':return {**base,'pcm_class':'POLICY_BLOCKED_PLAYABLE','eligible':False,'reason':'la recuperación PCM automática permanece limitada a fuentes no reproducibles','regions':[]}
    if not h.get('valid') or not tags.get('valid') or int((facts.get('ogg') or {}).get('logical_stream_count') or 0)!=1:
        return {**base,'pcm_class':'OPUS_RECOVERY_BLOCKED','eligible':False,'reason':'se requieren OpusHead y OpusTags válidos en un único stream','regions':[]}
    if codes & _BLOCKING_CODES:
        return {**base,'pcm_class':'OPUS_RECOVERY_BLOCKED','eligible':False,'reason':'existe una condición de header, paquete o línea de tiempo fuera del alcance vigente de regiones demostradas','blocking_issue_codes':sorted(codes&_BLOCKING_CODES),'regions':[]}
    if not (codes&_RECOVERY_DAMAGE_CODES):
        return {**base,'pcm_class':'OPUS_RECOVERY_BLOCKED','eligible':False,'reason':'ninguna condición de pérdida o daño de páginas requiere recuperación de regiones demostradas','regions':[]}
    data=source.read_bytes();head_pkt,tags_pkt,hpg,tpg=_extract_headers(data,q.get('structural_map') or [])
    if not head_pkt or not tags_pkt or not hpg or not tpg or not hpg.get('crc_ok') or not tpg.get('crc_ok'):
        return {**base,'pcm_class':'OPUS_RECOVERY_BLOCKED','eligible':False,'reason':'los paquetes de header Opus no están disponibles desde páginas autenticadas por CRC','regions':[]}
    runs=_trusted_packet_runs(q,source);regions=[];pre_skip=int(h.get('pre_skip') or 0);channels=int(h.get('channels') or 0)
    for run in runs:
        if not run:continue
        tail_trim=int(run[-1].get('tail_trim_samples_48k') or 0) if run[-1].get('ends_on_eos_page') else 0
        presentation_end=int(run[-1].get('presentation_granule_end') if run[-1].get('presentation_granule_end') is not None else run[-1]['decoded_granule_end'])
        total=sum(int(x['duration_samples_48k']) for x in run)
        if int(run[0]['decoded_granule_start'])==0:
            expected=total-pre_skip-tail_trim
            if expected<=0:continue
            regions.append({'region_index':len(regions)+1,'origin':'STREAM_START','context_policy':'SOURCE_PRE_SKIP',
                'decoder_context_discard_samples_48k':pre_skip,'context_packet_count':0,'published_packet_count':len(run),
                'source_granule_start':pre_skip,'source_granule_end':presentation_end,
                'source_pcm_start_48k':0,'source_pcm_end_48k':presentation_end-pre_skip,
                'expected_pcm_samples_48k':expected,'eos_end_trim_samples_48k':tail_trim,
                'includes_authenticated_eos':bool(run[-1].get('ends_on_eos_page')),'packets':run})
        else:
            acc=0;ctx=0
            while ctx<len(run) and acc<PRE_ROLL_SAMPLES_48K:
                acc+=int(run[ctx]['duration_samples_48k']);ctx+=1
            if acc<PRE_ROLL_SAMPLES_48K or ctx>=len(run):continue
            pub=run[ctx:];tail_trim=int(pub[-1].get('tail_trim_samples_48k') or 0) if pub[-1].get('ends_on_eos_page') else 0
            presentation_end=int(pub[-1].get('presentation_granule_end') if pub[-1].get('presentation_granule_end') is not None else pub[-1]['decoded_granule_end'])
            expected=sum(int(x['duration_samples_48k']) for x in pub)-tail_trim
            if expected<=0:continue
            regions.append({'region_index':len(regions)+1,'origin':'POST_DAMAGE','context_policy':'RFC7845_SEEK_PREROLL_AT_LEAST_80MS',
                'decoder_context_discard_samples_48k':acc,'context_packet_count':ctx,'published_packet_count':len(pub),
                'source_granule_start':int(pub[0]['decoded_granule_start']),'source_granule_end':presentation_end,
                'source_pcm_start_48k':int(pub[0]['decoded_granule_start'])-pre_skip,'source_pcm_end_48k':presentation_end-pre_skip,
                'expected_pcm_samples_48k':expected,'eos_end_trim_samples_48k':tail_trim,
                'includes_authenticated_eos':bool(pub[-1].get('ends_on_eos_page')),'packets':run})
    public=[]
    for r in regions:
        packets=r['packets'];page_sequences=[]
        for x in packets:
            for seq in x.get('page_sequences') or []:
                if seq not in page_sequences:page_sequences.append(seq)
        public.append({k:v for k,v in r.items() if k!='packets'} | {
            'source_page_sequences':page_sequences,'selected_packet_count':len(packets),
            'selected_packet_sha256':[x['packet_sha256'] for x in packets],
            'continued_source_packet_count':sum(1 for x in packets if x.get('spans_pages')),
            'continued_source_packet_sha256':[x['packet_sha256'] for x in packets if x.get('spans_pages')],
        })
    if not regions:return {**base,'pcm_class':'OPUS_RECOVERY_BLOCKED','eligible':False,'reason':'no queda ninguna región de paquetes completos autenticada por CRC con contexto suficiente para el decodificador','regions':[]}
    return {**base,'pcm_class':'OPUS_PROVEN_REGIONS','eligible':True,'reason':'las regiones de paquetes Opus completos autenticados por CRC tienen posiciones exactas a 48 kHz, procedencia de paquetes continuados, contexto suficiente de decodificador y recorte EOS exacto cuando está autenticado','channels':channels,'source_pre_skip':pre_skip,'source_output_gain_q7_8':h.get('output_gain_q7_8'),'source_eos_end_trim_samples_48k':facts.get('eos_end_trim_samples_48k'),'regions':public,'_private_regions':regions,'_head_packet':head_pkt,'_tags_packet':tags_pkt}

def _laces(packet:bytes):
    n=len(packet);ls=[255]*(n//255);rem=n%255
    if rem or not ls:ls.append(rem)
    else:ls.append(0)
    if len(ls)>255:raise ValueError('el paquete excede el alcance de una página del constructor de recuperación vigente')
    return ls

def _build_page(serial:int,seq:int,gp:int,flags:int,packet:bytes):
    ls=_laces(packet)
    raw=bytearray(b'OggS'+bytes([0,flags])+int(gp).to_bytes(8,'little',signed=True)+int(serial).to_bytes(4,'little')+int(seq).to_bytes(4,'little')+b'\0\0\0\0'+bytes([len(ls)])+bytes(ls)+packet)
    crc=ogg_crc(raw);raw[22:26]=crc.to_bytes(4,'little');return bytes(raw)

def _build_decode_view(head_packet:bytes,tags_packet:bytes,packets:list[dict],discard:int,out:Path,serial:int,final_trim:int=0):
    h=bytearray(head_packet);h[10:12]=int(discard).to_bytes(2,'little',signed=False);h[16:18]=(0).to_bytes(2,'little',signed=True)
    seq=0;parts=[_build_page(serial,seq,0,2,bytes(h)),_build_page(serial,1,0,0,tags_packet)];seq=2;gp=0
    for idx,p in enumerate(packets):
        gp+=int(p['duration_samples_48k']);last=idx==len(packets)-1;page_gp=gp-int(final_trim) if last else gp
        parts.append(_build_page(serial,seq,page_gp,4 if last else 0,p['data']));seq+=1
    out.write_bytes(b''.join(parts));return gp-int(final_trim)

def _manifest_matches(m:dict,source_sha:str,plan:dict):
    return (m.get('producer')=='LossyDoctor' and m.get('producer_version')==APP_VERSION and m.get('derivation_kind')==DERIVATION_KIND and
        m.get('validation_result')=='PASS' and m.get('source_sha256')==source_sha and m.get('region_index')==plan['region_index'] and
        m.get('source_pcm_start_48k')==plan['source_pcm_start_48k'] and m.get('source_pcm_end_48k')==plan['source_pcm_end_48k'] and
        m.get('decoder_context_discard_samples_48k')==plan['decoder_context_discard_samples_48k'] and m.get('output_gain_policy')==GAIN_POLICY and m.get('materialization')==MATERIALIZATION and int(m.get('eos_end_trim_samples_48k') or 0)==int(plan.get('eos_end_trim_samples_48k') or 0))

def _reuse(source:Path,source_sha:str,plans:list[dict]):
    found={}
    for side in source.parent.glob('*.lossydoctor-manifest.json'):
        try:m=json.loads(side.read_text(encoding='utf-8'))
        except Exception:continue
        for p in plans:
            if not _manifest_matches(m,source_sha,p):continue
            op=Path(m.get('output_path',''))
            if op.exists() and sha256_file(op)==m.get('output_sha256'):found[p['region_index']]={'status':'REUSED','output_path':str(op),'manifest_path':str(side),'manifest':m}
    if set(found)!=set(range(1,len(plans)+1)):return []
    return [found[i] for i in sorted(found)]

def export(source:Path,source_sha:str,q:dict,ffmpeg:str,assessment:dict,publish:bool=True):
    if not assessment.get('eligible'):return {'status':'NOT_ELIGIBLE','assessment':assessment,'outputs':[]}
    public=assessment.get('regions') or [];private=assessment.get('_private_regions') or []
    reused=_reuse(source,source_sha,public)
    if reused:return {'status':'REUSED','assessment':{k:v for k,v in assessment.items() if not k.startswith('_')},'outputs':reused}
    head=assessment.get('_head_packet');tags=assessment.get('_tags_packet');channels=int(assessment.get('channels') or 0)
    if not head or not tags or channels<=0:return {'status':'REJECTED','reason':'los headers o canales de recuperación no están disponibles','assessment':assessment,'outputs':[]}
    prepared=[]
    with tempfile.TemporaryDirectory(prefix='lossydoctor-opus-regions-') as td:
        tmp=Path(td)
        for idx,(pub,prv) in enumerate(zip(public,private),1):
            discard=int(pub['decoder_context_discard_samples_48k']);view=tmp/f'region_{idx:02d}.opus'
            gp=_build_decode_view(head,tags,prv['packets'],discard,view,0x4c440000+idx,int(pub.get('eos_end_trim_samples_48k') or 0))
            raw=tmp/f'region_{idx:02d}.raw';dec=decode_to_raw_file(view,raw,ffmpeg)
            expected=int(pub['expected_pcm_samples_48k']);frame_bytes=channels*4
            if not dec.get('passed') or not raw.exists() or raw.stat().st_size!=expected*frame_bytes:
                return {'status':'REJECTED','reason':f'no coincide el tamaño de decodificación con ganancia neutra y pre-roll en la región {idx}','detail':{'decode':dec,'expected_samples':expected,'actual_bytes':raw.stat().st_size if raw.exists() else None},'assessment':assessment,'outputs':[]}
            pcm_sha=sha256_file(raw);cand=tmp/f'region_{idx:02d}.flac';enc=raw_file_to_flac(raw,cand,ffmpeg,48000,channels)
            if not enc.get('passed'):return {'status':'REJECTED','reason':f'region {idx} falló la codificación FLAC','detail':enc,'assessment':assessment,'outputs':[]}
            back=tmp/f'region_{idx:02d}_back.raw';backdec=decode_to_raw_file(cand,back,ffmpeg)
            if not backdec.get('passed') or back.stat().st_size!=raw.stat().st_size or sha256_file(back)!=pcm_sha:
                return {'status':'REJECTED','reason':f'la verificación round-trip del PCM FLAC no coincide en la región {idx}','assessment':assessment,'outputs':[]}
            profile=canonical_pcm_profile(ffmpeg,48000,channels)
            prepared.append((idx,pub,prv,cand,pcm_sha,profile,gp))
        outputs=[]
        for idx,pub,prv,cand,pcm_sha,profile,gp in prepared:
            desired=source.with_name(source.stem+f' [recovered-opus-proven-region part{idx:02d}].flac')
            out=cand
            man={'schema_version':3,'producer':'LossyDoctor','producer_version':APP_VERSION,'derivation_schema':2,'derivation_kind':DERIVATION_KIND,
                'materialization':MATERIALIZATION,'region_index':idx,'region_count':len(prepared),'source_path':str(source),'source_sha256':source_sha,
                'output_path':str(out),'output_sha256':sha256_file(out),'canonical_pcm_profile':profile,'sample_rate':48000,'channels':channels,
                'source_pcm_start_48k':pub['source_pcm_start_48k'],'source_pcm_end_48k':pub['source_pcm_end_48k'],'sample_count':pub['expected_pcm_samples_48k'],
                'source_granule_start':pub['source_granule_start'],'source_granule_end':pub['source_granule_end'],'source_page_sequences':pub['source_page_sequences'],
                'includes_authenticated_eos':pub.get('includes_authenticated_eos',False),'eos_end_trim_samples_48k':int(pub.get('eos_end_trim_samples_48k') or 0),'source_eos_end_trim_samples_48k':assessment.get('source_eos_end_trim_samples_48k'),
                'continued_source_packet_count':pub.get('continued_source_packet_count',0),'continued_source_packet_sha256':pub.get('continued_source_packet_sha256',[]),
                'selected_packet_count':pub['selected_packet_count'],'selected_packet_sha256':pub['selected_packet_sha256'],'context_policy':pub['context_policy'],
                'decoder_context_discard_samples_48k':pub['decoder_context_discard_samples_48k'],'context_packet_count':pub['context_packet_count'],
                'published_packet_count':pub['published_packet_count'],'minimum_seek_preroll_samples_48k':PRE_ROLL_SAMPLES_48K,
                'coverage_claim':'PROVEN_PACKET_REGIONS_WITH_EXACT_EOS_TRIM_NO_FULL_TIMELINE_CLAIM','synthesized_missing_span':'NONE','resampling':'NONE','channel_remix':'NONE',
                'source_output_gain_q7_8':assessment.get('source_output_gain_q7_8'),'output_gain_policy':GAIN_POLICY,'output_gain_baked_into_pcm':False,
                'temporary_decode_view_output_gain_q7_8':0,'temporary_decode_view_pre_skip':pub['decoder_context_discard_samples_48k'],
                'opus_audio_packet_bytes_modified':False,'temporary_decode_view_repages_packets':True,'source_page_crc_authenticated':True,'region_pcm_sha256':pcm_sha,'flac_decoded_pcm_sha256':pcm_sha,
                'validation_result':'PASS','source_modified':False,'audio_recoding':'LOSSLESS_FLAC_ONLY'}
            out,side,man,publication_status=publish_or_preview_with_manifest(cand,desired,man,publish)
            outputs.append({'status':publication_status,'output_path':str(out) if out is not None else None,'manifest_path':str(side) if side else None,'manifest':man})
        return {'status':combined_publication_status(outputs),'assessment':{k:v for k,v in assessment.items() if not k.startswith('_')},'outputs':outputs}
