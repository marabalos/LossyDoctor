from __future__ import annotations
from pathlib import Path
import json,tempfile,hashlib
from app.external import _decode_s32_file,raw_file_to_flac,decode_to_raw_file,canonical_pcm_profile
from app.publication import publish_or_preview_with_manifest
from app.utils import sha256_file
from app.version import APP_VERSION

DERIVATION_KIND='RECOVERED_WMA_PROVEN_REGION_LOSSLESS'
MATERIALIZATION='WMA_CANONICAL_DECODER_PROVEN_CLEAN_REGION'
POLICY='WMA_PROVEN_MULTI_REGION_RECOVERY_V1'
COVERAGE='PROVEN_DECODER_CLEAN_REGIONS_NO_FULL_TIMELINE_CLAIM'
_ALLOWED_FORMAT_TAGS={0x0160,0x0161}
_ALLOWED_ISSUE_CODES={'ASF_WMA_DEMUX_TIMELINE_DISCONTINUITY'}


def _public(a:dict):return {k:v for k,v in a.items() if not k.startswith('_')}

def assess(source:Path,demux_decoder:dict,convergence:dict,metadata:dict,issue_codes:set[str],playability:str):
    """Publica regiones limpias separadas alrededor de dos o más brechas WMA probadas.

    No reemplaza la política de una sola brecha. En varias secuencias ausentes se
    excluye un frame completo asociado al primer objeto de contexto posterior.
    Nunca se sintetizan datos, se concatenan regiones ni se afirma una línea de
    tiempo PCM completa de la fuente.
    """
    tag=metadata.get('format_tag');base={
        'schema':1,'policy':POLICY,'derivation_kind':DERIVATION_KIND,'materialization':MATERIALIZATION,
        'coverage_claim':COVERAGE,'repair_authority':'NONE','publication_authority':'SEPARATE_PROVEN_REGIONS_ONLY',
        'full_source_pcm_timeline_claim':False,'synthesized_missing_span':'NONE','regions_concatenated':False,
        'source_playability':playability,'eligible':False,'regions':[],'gap_count':convergence.get('candidate_count') or 0,
    }
    if not issue_codes and playability=='PLAYABLE':return {**base,'pcm_class':'NOT_REQUIRED','reason':'el stream ASF/WMA reproducible y conforme no requiere un derivado de preservación'}
    if tag not in _ALLOWED_FORMAT_TAGS:return {**base,'pcm_class':'WMA_MULTI_REGION_BLOCKED','reason':'la recuperación multirregión vigente está limitada a WMA1/WMA2'}
    extra=set(issue_codes)-_ALLOWED_ISSUE_CODES
    if extra:return {**base,'pcm_class':'WMA_MULTI_REGION_BLOCKED','reason':'existe un hallazgo fuera de las brechas demostradas de objetos multimedia ausentes en la línea de tiempo','blocking_issue_codes':sorted(extra)}
    if not (demux_decoder.get('one_to_one_complete_media_object_mapping') and demux_decoder.get('all_packet_hashes_equal') and demux_decoder.get('all_packet_sizes_equal') and demux_decoder.get('all_pts_match_media_object_presentation_minus_preroll')):
        return {**base,'pcm_class':'WMA_MULTI_REGION_BLOCKED','reason':'la identidad completa entre objetos multimedia y paquetes demux canónicos no está totalmente demostrada'}
    candidates=convergence.get('candidates') or []
    if len(candidates)<2 or convergence.get('candidate_count')!=len(candidates):
        return {**base,'pcm_class':'WMA_MULTI_REGION_NOT_APPLICABLE','reason':'la autoridad multirregión vigente requiere al menos dos secuencias independientes demostradas de objetos multimedia ausentes; la recuperación de una sola brecha conserva su política vigente'}
    if convergence.get('validated_candidate_count')!=len(candidates) or not convergence.get('all_candidates_validated'):
        return {**base,'pcm_class':'WMA_MULTI_REGION_BLOCKED','reason':'no todas las secuencias de objetos multimedia ausentes tienen un límite de convergencia determinista validado'}
    for c in candidates:
        if not (c.get('validated') and c.get('seek_decode_matches_full_decode_suffix') and c.get('sample_frame_aligned') and c.get('one_surviving_packet_context_observed') and c.get('status')=='VALIDATED_DETERMINISTIC_CONVERGENCE_EVIDENCE_ONLY'):
            return {**base,'pcm_class':'WMA_MULTI_REGION_BLOCKED','reason':'al menos una prueba de convergencia posterior a la brecha está incompleta'}
    frame_values=demux_decoder.get('decoded_frame_nb_samples_values') or []
    frame_len=frame_values[0] if len(frame_values)==1 and frame_values[0] else None
    sr=metadata.get('sample_rate');ch=metadata.get('channels');total=demux_decoder.get('decoder_output_sample_frames')
    regions=convergence.get('clean_region_candidates') or []
    if not frame_len or not sr or not ch or not total or len(regions)!=len(candidates)+1:
        return {**base,'pcm_class':'WMA_MULTI_REGION_BLOCKED','reason':'la geometría de regiones limpias está incompleta o no delimita todas las brechas demostradas'}
    plans=[];prev_end=0
    for ri,r in enumerate(regions,1):
        st=r.get('decoded_sample_start');en=r.get('decoded_sample_end');h=r.get('pcm_sha256')
        if st is None or en is None or int(st)<prev_end or int(en)<=int(st) or int(st)%int(frame_len) or int(en)%int(frame_len) or not h or not r.get('provenance_complete'):
            return {**base,'pcm_class':'WMA_MULTI_REGION_BLOCKED','reason':'una región limpia propuesta carece de procedencia alineada y no superpuesta de PCM, hash y datos comprimidos'}
        p=dict(r);p['region_index']=ri;p['output_sample_frames']=int(en)-int(st);p['sample_rate']=int(sr);p['channels']=int(ch);p['decoder_frame_length_samples']=int(frame_len)
        p['context_audio_published']=False;p['source_timeline_boundary_kind']='DAMAGED_CANONICAL_DECODE_REGION_NOT_ABSOLUTE_ORIGINAL_PCM'
        plans.append(p);prev_end=int(en)
    # Demuestra que el intervalo omitido alrededor de cada brecha equivale a un cuadro superviviente
    # de contexto en coordenadas dañadas. Los objetos ausentes no producen PCM sintético.
    excluded=[]
    for i in range(len(plans)-1):
        a=plans[i]['decoded_sample_end'];b=plans[i+1]['decoded_sample_start']
        if int(b)-int(a)!=int(frame_len):
            return {**base,'pcm_class':'WMA_MULTI_REGION_BLOCKED','reason':'el intervalo de contexto excluido posterior a la brecha no equivale exactamente a un frame observado del decodificador'}
        c=candidates[i]
        excluded.append({'gap_index':i+1,'decoded_sample_start':int(a),'decoded_sample_end':int(b),'sample_count':int(frame_len),
                         'context_media_object_number':c.get('context_media_object_number'),'missing_media_object_count':c.get('missing_media_object_count'),
                         'missing_media_object_numbers':c.get('missing_media_object_numbers') or [],'context_audio_published':False})
    return {**base,'pcm_class':'WMA_PROVEN_MULTI_REGION','eligible':True,
            'reason':'se demuestran varias secuencias de objetos multimedia ASF/WMA ausentes y cada brecha converge independientemente después de exactamente un objeto de contexto superviviente; la decodificación canónica dañada se divide en regiones limpias separadas con hash, mientras todos los frames de contexto y todos los intervalos ausentes permanecen sin publicar',
            'sample_rate':int(sr),'channels':int(ch),'format_tag':int(tag),'codec_name':metadata.get('codec_name'),
            'decoder_frame_length_samples':int(frame_len),'gap_count':len(candidates),'region_count':len(plans),'excluded_context_intervals':excluded,
            'regions':plans,'_private_plans':plans}


def _match(m:dict,source_sha:str,p:dict):
    return (m.get('producer')=='LossyDoctor' and m.get('producer_version')==APP_VERSION and m.get('derivation_kind')==DERIVATION_KIND and
            m.get('materialization')==MATERIALIZATION and m.get('validation_result')=='PASS' and m.get('source_sha256')==source_sha and
            m.get('region_index')==p.get('region_index') and m.get('decoded_sample_start')==p.get('decoded_sample_start') and
            m.get('decoded_sample_end')==p.get('decoded_sample_end') and m.get('output_sample_frames')==p.get('output_sample_frames') and
            m.get('region_pcm_sha256')==p.get('pcm_sha256'))

def _reuse(source:Path,source_sha:str,p:dict):
    for side in source.parent.glob('*.lossydoctor-manifest.json'):
        try:m=json.loads(side.read_text(encoding='utf-8'))
        except Exception:continue
        if not _match(m,source_sha,p):continue
        op=Path(m.get('output_path',''))
        if op.exists() and sha256_file(op)==m.get('output_sha256'):
            return {'status':'REUSED','output_path':str(op),'manifest_path':str(side),'manifest':m}
    return None

def _hash_bytes(data:bytes):return hashlib.sha256(data).hexdigest()

def export(source:Path,source_sha:str,ffmpeg:str,assessment:dict,publish:bool=True,timeout:int=300):
    if not assessment.get('eligible'):return {'status':'NOT_ELIGIBLE','assessment':_public(assessment),'outputs':[]}
    plans=assessment.get('_private_plans') or assessment.get('regions') or []
    if not plans:return {'status':'REJECTED','reason':'los planes de recuperación multirregión no están disponibles','assessment':_public(assessment),'outputs':[]}
    sr=int(assessment.get('sample_rate') or 0);ch=int(assessment.get('channels') or 0);frame_bytes=ch*4
    if sr<=0 or ch<=0:return {'status':'REJECTED','reason':'el perfil PCM nativo no está disponible','assessment':_public(assessment),'outputs':[]}
    existing={p['region_index']:_reuse(source,source_sha,p) for p in plans}
    if all(existing.values()):return {'status':'REUSED','assessment':_public(assessment),'outputs':[existing[p['region_index']] for p in plans]}
    with tempfile.TemporaryDirectory(prefix='lossydoctor-wma-v41-') as td:
        td=Path(td);full=td/'full.s32le';dec=_decode_s32_file(source,full,ffmpeg,timeout)
        if not dec.get('passed') or not full.exists():return {'status':'REJECTED','reason':'falló una nueva decodificación canónica completa','detail':dec,'assessment':_public(assessment),'outputs':[]}
        raw=full.read_bytes();outputs=[]
        for p in plans:
            reused=existing.get(p['region_index'])
            if reused:outputs.append(reused);continue
            st=int(p['decoded_sample_start']);en=int(p['decoded_sample_end']);bs=st*frame_bytes;be=en*frame_bytes
            if bs<0 or be>len(raw) or be<=bs:return {'status':'REJECTED','reason':'la nueva decodificación canónica ya no contiene los límites de región planificados','assessment':_public(assessment),'outputs':[]}
            region=raw[bs:be];pcm_sha=_hash_bytes(region)
            if pcm_sha!=p.get('pcm_sha256'):
                return {'status':'REJECTED','reason':'el hash PCM de la región limpia cambió desde el análisis de convergencia','detail':{'region_index':p['region_index'],'expected':p.get('pcm_sha256'),'actual':pcm_sha},'assessment':_public(assessment),'outputs':[]}
            rr=td/f"region-{p['region_index']:02d}.s32le";rr.write_bytes(region);cand=td/f"region-{p['region_index']:02d}.flac"
            enc=raw_file_to_flac(rr,cand,ffmpeg,sr,ch,timeout)
            if not enc.get('passed'):return {'status':'REJECTED','reason':'falló la codificación FLAC','detail':enc,'assessment':_public(assessment),'outputs':[]}
            back=td/f"back-{p['region_index']:02d}.s32le";bd=decode_to_raw_file(cand,back,ffmpeg,timeout)
            if not bd.get('passed') or not back.exists() or back.read_bytes()!=region:
                return {'status':'REJECTED','reason':'la verificación round-trip del PCM FLAC no coincide','detail':{'region_index':p['region_index']},'assessment':_public(assessment),'outputs':[]}
            desired=source.with_name(source.stem+f" [recovered-wma-region-{p['region_index']:02d}].flac")
            out=cand;profile=canonical_pcm_profile(ffmpeg,sr,ch)
            man={'schema_version':3,'producer':'LossyDoctor','producer_version':APP_VERSION,'derivation_schema':1,
                 'derivation_kind':DERIVATION_KIND,'materialization':MATERIALIZATION,'recovery_policy':POLICY,'coverage_claim':COVERAGE,
                 'source_path':str(source),'source_sha256':source_sha,'output_path':str(out),'output_sha256':sha256_file(out),'canonical_pcm_profile':profile,
                 'sample_rate':sr,'channels':ch,'format_tag':assessment.get('format_tag'),'codec_name':assessment.get('codec_name'),
                 'region_index':p['region_index'],'region_count':len(plans),'gap_count':assessment.get('gap_count'),
                 'decoded_sample_start':st,'decoded_sample_end':en,'output_sample_frames':en-st,'decoder_frame_length_samples':p.get('decoder_frame_length_samples'),
                 'boundary_start':p.get('boundary_start'),'boundary_end':p.get('boundary_end'),'left_gap_candidate_index':p.get('left_gap_candidate_index'),'right_gap_candidate_index':p.get('right_gap_candidate_index'),
                 'selected_demux_packet_indices':p.get('selected_demux_packet_indices'),'selected_media_object_numbers':p.get('selected_media_object_numbers'),
                 'selected_media_object_sha256':p.get('selected_media_object_sha256'),'selected_demux_packet_sha256':p.get('selected_demux_packet_sha256'),
                 'source_timeline_boundary_kind':p.get('source_timeline_boundary_kind'),'context_audio_published':False,'regions_concatenated':False,
                 'full_source_pcm_timeline_claim':False,'synthesized_missing_span':'NONE','resampling':'NONE','channel_remix':'NONE','wma_media_object_bytes_modified':False,'source_modified':False,
                 'region_pcm_sha256':pcm_sha,'flac_decoded_pcm_sha256':pcm_sha,'validation_result':'PASS','audio_recoding':'LOSSLESS_FLAC_ONLY'}
            out,side,man,publication_status=publish_or_preview_with_manifest(cand,desired,man,publish)
            outputs.append({'status':publication_status,'output_path':str(out) if out is not None else None,'manifest_path':str(side) if side else None,'manifest':man})
        stset={o.get('status') for o in outputs};status='CREATED' if stset=={'CREATED'} else ('REUSED' if stset=={'REUSED'} else 'MIXED_CREATED_REUSED')
        return {'status':status,'assessment':_public(assessment),'outputs':outputs}
