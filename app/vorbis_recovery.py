from __future__ import annotations
from pathlib import Path
import json, tempfile
from app.external import canonical_pcm_profile, decode_to_raw_file, raw_file_to_flac
from app.publication import combined_publication_status, publish_or_preview_with_manifest
from app.utils import sha256_file, sha256_bytes
from app.version import APP_VERSION
from formats.ogg_opus import ogg_crc
from formats.ogg_vorbis import _parse_pages_packets

DERIVATION_KIND='RECOVERED_VORBIS_PROVEN_REGION_LOSSLESS'
MATERIALIZATION='VORBIS_AUTHENTICATED_PACKET_CHAIN_PRIMED_OVERLAP_ADD'
POLICY='VORBIS_PROVEN_REGION_RECOVERY_OVERLAP_ADD_V1'

_BLOCKING_CODES={
    'VORBIS_IDENTIFICATION_HEADER_INVALID','VORBIS_COMMENT_HEADER_INVALID','VORBIS_SETUP_HEADER_INVALID',
    'VORBIS_HEADER_ORDER_INVALID','VORBIS_AUDIO_PACKET_HEADER_INVALID','OGG_VERSION_UNSUPPORTED',
    'OGG_CONTINUATION_FLAG_INCONSISTENT','VORBIS_GRANULE_POSITION_NONMONOTONIC','VORBIS_GRANULE_POSITION_MISSING',
    'VORBIS_BOS_HEADER_PAGE_INVALID','VORBIS_IDENTIFICATION_PAGE_LAYOUT_INVALID','VORBIS_SETUP_PAGE_LAYOUT_INVALID',
}
_RECOVERY_DAMAGE_CODES={
    'OGG_SYNC_LOSS','OGG_PAGE_CRC_MISMATCH','OGG_PAGE_SEQUENCE_DISCONTINUITY','OGG_TRUNCATED_PAGE',
    'OGG_INCOMPLETE_PACKET_AT_EOF','OGG_VORBIS_EOS_MISSING','VORBIS_GRANULE_DELTA_MISMATCH','VORBIS_EOS_TRIM_INVALID',
}

def _stream_packets(source:Path):
    data=source.read_bytes(); pages,byserial,packets,issues=_parse_pages_packets(data)
    serial=None;sp=[]
    for s in byserial:
        q=[x for x in packets if x['serial']==s]
        if q and q[0]['data'].startswith(b'\x01vorbis'):
            serial=s;sp=q;break
    return data,pages,sp

def _public_region(r:dict,packet_map:dict[int,dict]):
    indices=[int(x) for x in r.get('packet_indices') or []]
    seq=[]; hashes=[]; cross=[]
    for i in indices:
        m=packet_map.get(i) or {}
        hashes.append(m.get('packet_sha256'))
        if m.get('spans_pages'):cross.append(m.get('packet_sha256'))
        for s in m.get('page_sequences') or []:
            if s not in seq:seq.append(s)
    return {
        'region_index':int(r['region_index']),'pcm_start':int(r['pcm_start']),'pcm_end':int(r['pcm_end']),
        'sample_count':int(r['sample_count']),'sample_rate':int(r['sample_rate']),
        'priming_packet_index':int(r['priming_packet_index']),
        'first_published_overlap_packet_index':int(r['first_published_overlap_packet_index']),
        'last_packet_index':int(r['last_packet_index']),'packet_indices':indices,
        'boundary_start':r.get('boundary_start'),'boundary_end':r.get('boundary_end'),
        'includes_authenticated_eos':bool(r.get('authenticated_eos_included')),
        'source_page_sequences':seq,'selected_packet_count':len(indices),'selected_packet_sha256':hashes,
        'continued_source_packet_count':len(cross),'continued_source_packet_sha256':cross,
        'priming_packet_count':1,'published_overlap_packet_count':max(0,len(indices)-1),
    }

def assess(source:Path,q:dict,playability:str):
    facts=q.get('facts') or {};vi=facts.get('vorbis_identification') or {};vc=facts.get('vorbis_comment') or {};vs=facts.get('vorbis_setup') or {}
    codes={i.code for i in (q.get('issues') or [])}
    base={'schema':1,'policy':POLICY,'first_packet_of_each_chain_is_priming_only':True,
          'overlap_dependency_rule':'PCM_BETWEEN_PACKET_CENTERS_DEPENDS_ON_PREVIOUS_AND_CURRENT_BLOCK',
          'coverage_claim':'CRC_AUTHENTICATED_PACKET_CHAINS_ONLY_NO_SYNTHESIZED_MISSING_PCM'}
    if not codes and playability=='PLAYABLE':
        return {**base,'pcm_class':'NOT_REQUIRED','eligible':False,'reason':'el stream Ogg Vorbis reproducible y conforme no requiere recuperación PCM','regions':[]}
    if playability!='UNPLAYABLE':
        return {**base,'pcm_class':'POLICY_BLOCKED_PLAYABLE','eligible':False,'reason':'la recuperación PCM Vorbis automática permanece sólo como alternativa para fuentes no reproducibles','regions':[]}
    if not vi.get('valid') or not vc.get('valid') or not vs.get('valid') or int((facts.get('ogg') or {}).get('logical_stream_count') or 0)!=1:
        return {**base,'pcm_class':'VORBIS_RECOVERY_BLOCKED','eligible':False,'reason':'se requieren headers Vorbis válidos de identificación, comentarios y configuración en un único stream','regions':[]}
    if codes&_BLOCKING_CODES:
        return {**base,'pcm_class':'VORBIS_RECOVERY_BLOCKED','eligible':False,'reason':'existe una condición de header, paquete o línea de tiempo fuera del alcance vigente de regiones demostradas','blocking_issue_codes':sorted(codes&_BLOCKING_CODES),'regions':[]}
    if not (codes&_RECOVERY_DAMAGE_CODES):
        return {**base,'pcm_class':'VORBIS_RECOVERY_BLOCKED','eligible':False,'reason':'ninguna condición de pérdida o daño de páginas requiere recuperación de regiones Vorbis demostradas','regions':[]}
    evidence=facts.get('vorbis_recovery_evidence') or {};regions=evidence.get('candidate_regions') or []
    if not regions:
        return {**base,'pcm_class':'VORBIS_RECOVERY_BLOCKED','eligible':False,'reason':'no queda ninguna cadena de paquetes completos autenticada por CRC con un intervalo de salida overlap/add','regions':[]}
    data,pages,sp=_stream_packets(source)
    if len(sp)<3 or not all(sp[i].get('crc_authenticated_complete_packet') for i in range(3)):
        return {**base,'pcm_class':'VORBIS_RECOVERY_BLOCKED','eligible':False,'reason':'los tres paquetes de header Vorbis obligatorios no están disponibles desde paquetes completos autenticados por CRC','regions':[]}
    if not (sp[0]['data'].startswith(b'\x01vorbis') and sp[1]['data'].startswith(b'\x03vorbis') and sp[2]['data'].startswith(b'\x05vorbis')):
        return {**base,'pcm_class':'VORBIS_RECOVERY_BLOCKED','eligible':False,'reason':'el orden o las firmas obligatorias de los paquetes de header Vorbis no están disponibles','regions':[]}
    raw_by_index={int(x['index']):x for x in sp};mp={int(x['packet_index']):x for x in (facts.get('audio_packet_map') or [])}
    public=[];private=[]
    for r in regions:
        pub=_public_region(r,mp); selected=[];ok=True
        for i in pub['packet_indices']:
            p=raw_by_index.get(i);m=mp.get(i)
            if not p or not m or not m.get('crc_authenticated_complete_packet') or sha256_bytes(p['data'])!=m.get('packet_sha256'):
                ok=False;break
            selected.append({'packet_index':i,'data':p['data'],'packet_sha256':m['packet_sha256'],
                             'overlap_output_samples':int(m.get('overlap_output_samples') or 0),'spans_pages':bool(m.get('spans_pages'))})
        if ok and len(selected)>=2 and pub['sample_count']>0:
            # El intervalo candidato debe igualar la suma de aportes solapados terminados, salvo
            # un EOS autenticado cuyo gránulo final puede recortar la cola.
            untrimmed=sum(x['overlap_output_samples'] for x in selected[1:])
            trim=untrimmed-pub['sample_count']
            if trim<0:continue
            pub['temporary_eos_trim_samples']=trim
            public.append(pub);private.append({'plan':pub,'packets':selected})
    if not public:
        return {**base,'pcm_class':'VORBIS_RECOVERY_BLOCKED','eligible':False,'reason':'las cadenas candidatas no superaron la validación exacta de paquetes, hashes y superposición','regions':[]}
    return {**base,'pcm_class':'VORBIS_PROVEN_REGIONS','eligible':True,
            'reason':'las cadenas de paquetes Vorbis completos autenticados por CRC tienen límites PCM absolutos derivados de gránulos autenticados; cada cadena comienza con un paquete de preparación y publica sólo la salida overlap/add posterior demostrada',
            'sample_rate':int(vi['sample_rate']),'channels':int(vi['channels']),'regions':public,
            '_private_regions':private,'_headers':[sp[0]['data'],sp[1]['data'],sp[2]['data']]}

def _packet_laces(packet:bytes):
    n=len(packet); ls=[255]*(n//255); rem=n%255
    if rem or not ls:ls.append(rem)
    else:ls.append(0)
    return ls

def _page(serial:int,seq:int,gp:int,flags:int,laces:list[int],body:bytes):
    raw=bytearray(b'OggS'+bytes([0,flags])+int(gp).to_bytes(8,'little',signed=True)+int(serial).to_bytes(4,'little')+int(seq).to_bytes(4,'little')+b'\0\0\0\0'+bytes([len(laces)])+bytes(laces)+body)
    raw[22:26]=ogg_crc(raw).to_bytes(4,'little');return bytes(raw)

def _paginate_packet(packet:bytes,serial:int,seq:int,final_gp:int,*,bos=False,eos=False,header=False):
    laces=_packet_laces(packet);parts=[];body_pos=0;first=True
    while laces:
        chunk=laces[:255];laces=laces[255:];body_len=sum(chunk);body=packet[body_pos:body_pos+body_len];body_pos+=body_len
        final=not laces;flags=(2 if bos and first else 0) | (1 if not first else 0) | (4 if eos and final else 0)
        gp=0 if header else (final_gp if final else -1)
        parts.append(_page(serial,seq,gp,flags,chunk,body));seq+=1;first=False
    return parts,seq

def _build_decode_view(headers:list[bytes],packets:list[dict],sample_count:int,out:Path,serial:int):
    parts=[];seq=0
    for i,h in enumerate(headers):
        pp,seq=_paginate_packet(h,serial,seq,0,bos=(i==0),header=True);parts.extend(pp)
    gp=0
    for i,p in enumerate(packets):
        if i>0:gp+=int(p['overlap_output_samples'])
        final=i==len(packets)-1
        final_gp=int(sample_count) if final else gp
        pp,seq=_paginate_packet(p['data'],serial,seq,final_gp,eos=final,header=False);parts.extend(pp)
    out.write_bytes(b''.join(parts));return gp

def _manifest_matches(m:dict,source_sha:str,p:dict):
    return (m.get('producer')=='LossyDoctor' and m.get('producer_version')==APP_VERSION and m.get('derivation_kind')==DERIVATION_KIND and
            m.get('validation_result')=='PASS' and m.get('source_sha256')==source_sha and m.get('region_index')==p['region_index'] and
            m.get('source_pcm_start')==p['pcm_start'] and m.get('source_pcm_end')==p['pcm_end'] and m.get('sample_count')==p['sample_count'] and
            m.get('materialization')==MATERIALIZATION)

def _reuse(source:Path,source_sha:str,plans:list[dict]):
    found={}
    for side in source.parent.glob('*.lossydoctor-manifest.json'):
        try:m=json.loads(side.read_text(encoding='utf-8'))
        except Exception:continue
        for p in plans:
            if not _manifest_matches(m,source_sha,p):continue
            op=Path(m.get('output_path',''))
            if op.exists() and sha256_file(op)==m.get('output_sha256'):
                found[p['region_index']]={'status':'REUSED','output_path':str(op),'manifest_path':str(side),'manifest':m}
    if set(found)!=set(range(1,len(plans)+1)):return []
    return [found[i] for i in sorted(found)]

def export(source:Path,source_sha:str,q:dict,ffmpeg:str,assessment:dict,publish:bool=True):
    if not assessment.get('eligible'):return {'status':'NOT_ELIGIBLE','assessment':assessment,'outputs':[]}
    public=assessment.get('regions') or [];private=assessment.get('_private_regions') or [];headers=assessment.get('_headers') or []
    reused=_reuse(source,source_sha,public)
    if reused:return {'status':'REUSED','assessment':{k:v for k,v in assessment.items() if not k.startswith('_')},'outputs':reused}
    sr=int(assessment.get('sample_rate') or 0);channels=int(assessment.get('channels') or 0)
    if len(headers)!=3 or sr<=0 or channels<=0:return {'status':'REJECTED','reason':'los headers de recuperación o el perfil nativo Vorbis no están disponibles','assessment':assessment,'outputs':[]}
    prepared=[]
    with tempfile.TemporaryDirectory(prefix='lossydoctor-vorbis-regions-') as td:
        tmp=Path(td)
        for idx,(pub,prv) in enumerate(zip(public,private),1):
            view=tmp/f'region_{idx:02d}.ogg';untrimmed=_build_decode_view(headers,prv['packets'],pub['sample_count'],view,0x4c560000+idx)
            raw=tmp/f'region_{idx:02d}.raw';dec=decode_to_raw_file(view,raw,ffmpeg)
            expected=int(pub['sample_count']);frame_bytes=channels*4
            if not dec.get('passed') or not raw.exists() or raw.stat().st_size!=expected*frame_bytes:
                return {'status':'REJECTED','reason':f'no coincide el tamaño de decodificación Vorbis con preparación y superposición en la región {idx}','detail':{'decode':dec,'expected_samples':expected,'actual_bytes':raw.stat().st_size if raw.exists() else None,'untrimmed_overlap_samples':untrimmed},'assessment':assessment,'outputs':[]}
            pcm_sha=sha256_file(raw);cand=tmp/f'region_{idx:02d}.flac';enc=raw_file_to_flac(raw,cand,ffmpeg,sr,channels)
            if not enc.get('passed'):return {'status':'REJECTED','reason':f'region {idx} falló la codificación FLAC','detail':enc,'assessment':assessment,'outputs':[]}
            back=tmp/f'region_{idx:02d}_back.raw';bd=decode_to_raw_file(cand,back,ffmpeg)
            if not bd.get('passed') or back.stat().st_size!=raw.stat().st_size or sha256_file(back)!=pcm_sha:
                return {'status':'REJECTED','reason':f'la verificación round-trip del PCM FLAC no coincide en la región {idx}','assessment':assessment,'outputs':[]}
            prepared.append((idx,pub,cand,pcm_sha,canonical_pcm_profile(ffmpeg,sr,channels),untrimmed))
        outputs=[]
        for idx,pub,cand,pcm_sha,profile,untrimmed in prepared:
            desired=source.with_name(source.stem+f' [recovered-vorbis-proven-region part{idx:02d}].flac')
            out=cand
            man={'schema_version':3,'producer':'LossyDoctor','producer_version':APP_VERSION,'derivation_schema':1,
                 'derivation_kind':DERIVATION_KIND,'materialization':MATERIALIZATION,'region_index':idx,'region_count':len(prepared),
                 'source_path':str(source),'source_sha256':source_sha,'output_path':str(out),'output_sha256':sha256_file(out),
                 'canonical_pcm_profile':profile,'sample_rate':sr,'channels':channels,
                 'source_pcm_start':pub['pcm_start'],'source_pcm_end':pub['pcm_end'],'sample_count':pub['sample_count'],
                 'boundary_start':pub.get('boundary_start'),'boundary_end':pub.get('boundary_end'),
                 'priming_packet_index':pub['priming_packet_index'],'first_published_overlap_packet_index':pub['first_published_overlap_packet_index'],
                 'last_packet_index':pub['last_packet_index'],'selected_packet_count':pub['selected_packet_count'],
                 'selected_packet_sha256':pub['selected_packet_sha256'],'source_page_sequences':pub['source_page_sequences'],
                 'continued_source_packet_count':pub['continued_source_packet_count'],'continued_source_packet_sha256':pub['continued_source_packet_sha256'],
                 'priming_packet_count':1,'published_overlap_packet_count':pub['published_overlap_packet_count'],
                 'includes_authenticated_eos':pub['includes_authenticated_eos'],'temporary_eos_trim_samples':pub['temporary_eos_trim_samples'],
                 'untrimmed_overlap_samples':untrimmed,'coverage_claim':'CRC_AUTHENTICATED_PACKET_CHAINS_ONLY_NO_SYNTHESIZED_MISSING_PCM',
                 'synthesized_missing_span':'NONE','resampling':'NONE','channel_remix':'NONE','vorbis_packet_bytes_modified':False,
                 'temporary_decode_view_repages_packets':True,'source_page_crc_authenticated':True,
                 'region_pcm_sha256':pcm_sha,'flac_decoded_pcm_sha256':pcm_sha,'validation_result':'PASS','source_modified':False,'audio_recoding':'LOSSLESS_FLAC_ONLY'}
            out,side,man,publication_status=publish_or_preview_with_manifest(cand,desired,man,publish)
            outputs.append({'status':publication_status,'output_path':str(out) if out is not None else None,'manifest_path':str(side) if side else None,'manifest':man})
        return {'status':combined_publication_status(outputs),'assessment':{k:v for k,v in assessment.items() if not k.startswith('_')},'outputs':outputs}
