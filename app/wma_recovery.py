from __future__ import annotations
from pathlib import Path
import json, tempfile
from app.external import _decode_s32_file, raw_file_to_flac, decode_to_raw_file, canonical_pcm_profile
from app.publication import publish_or_preview_with_manifest
from app.utils import sha256_file
from app.version import APP_VERSION

DERIVATION_KIND='RECOVERED_WMA_CONVERGED_SUFFIX_LOSSLESS'
MATERIALIZATION='WMA_CANONICAL_DECODER_POST_GAP_CONVERGED_SUFFIX'
POLICY='WMA_PROVEN_CONVERGED_SUFFIX_RECOVERY_V1'
COVERAGE='PROVEN_DECODER_CONVERGED_SUFFIX_NO_FULL_TIMELINE_CLAIM'
_ALLOWED_FORMAT_TAGS={0x0160,0x0161}
_ALLOWED_ISSUE_CODES={'ASF_WMA_DEMUX_TIMELINE_DISCONTINUITY'}


def _public_assessment(a:dict):
    return {k:v for k,v in a.items() if not k.startswith('_')}


def assess(source:Path, demux_decoder:dict, convergence:dict, metadata:dict, issue_codes:set[str], playability:str):
    """Autoriza un derivado WMA acotado para una secuencia ausente demostrada.

    No afirma una línea de tiempo PCM original absoluta. Publica únicamente el
    sufijo que converge tras un objeto de contexto usando la misma build canónica.
    La pérdida comprimida probada puede justificar preservación, pero no reparación.
    """
    tag=metadata.get('format_tag')
    base={
        'schema':1,'policy':POLICY,'derivation_kind':DERIVATION_KIND,'materialization':MATERIALIZATION,
        'coverage_claim':COVERAGE,'repair_authority':'NONE','publication_authority':'CONVERGED_SUFFIX_ONLY',
        'full_source_pcm_timeline_claim':False,'synthesized_missing_span':'NONE','context_media_object_count':1,
        'source_playability':playability,'eligible':False,'regions':[],
    }
    if not issue_codes and playability=='PLAYABLE':
        return {**base,'pcm_class':'NOT_REQUIRED','reason':'el stream ASF/WMA reproducible y conforme no requiere un derivado de preservación'}
    if tag not in _ALLOWED_FORMAT_TAGS:
        return {**base,'pcm_class':'WMA_RECOVERY_BLOCKED','reason':'la autoridad de recuperación vigente está limitada a WMA1/WMA2 (tags de formato 0x0160/0x0161)'}
    extra=set(issue_codes)-_ALLOWED_ISSUE_CODES
    if extra:
        return {**base,'pcm_class':'WMA_RECOVERY_BLOCKED','reason':'existe un hallazgo fuera del alcance inicial de recuperación de objetos multimedia ausentes demostrados','blocking_issue_codes':sorted(extra)}
    if not (demux_decoder.get('one_to_one_complete_media_object_mapping') and demux_decoder.get('all_packet_hashes_equal') and
            demux_decoder.get('all_packet_sizes_equal') and demux_decoder.get('all_pts_match_media_object_presentation_minus_preroll')):
        return {**base,'pcm_class':'WMA_RECOVERY_BLOCKED','reason':'la identidad completa entre objetos multimedia y paquetes demux canónicos no está totalmente demostrada'}
    candidates=convergence.get('candidates') or []
    if convergence.get('candidate_count')!=1 or convergence.get('validated_candidate_count')!=1 or len(candidates)!=1:
        return {**base,'pcm_class':'WMA_RECOVERY_BLOCKED','reason':'la política vigente requiere exactamente una secuencia demostrada de objetos multimedia ausentes con un límite de convergencia validado'}
    c=candidates[0]
    required=(c.get('validated') and c.get('seek_decode_matches_full_decode_suffix') and c.get('sample_frame_aligned') and
              c.get('one_surviving_packet_context_observed') and c.get('status')=='VALIDATED_DETERMINISTIC_CONVERGENCE_EVIDENCE_ONLY')
    if not required:
        return {**base,'pcm_class':'WMA_RECOVERY_BLOCKED','reason':'la prueba de convergencia del decodificador posterior a la brecha está incompleta'}
    frame_values=demux_decoder.get('decoded_frame_nb_samples_values') or []
    frame_len=frame_values[0] if len(frame_values)==1 and frame_values[0] else None
    rows=demux_decoder.get('mapping_rows') or []
    first_i=c.get('expected_first_candidate_demux_packet_index')
    context_i=c.get('context_demux_packet_index')
    if frame_len is None or first_i is None or context_i is None or not (0<=first_i<len(rows)) or not (0<=context_i<len(rows)):
        return {**base,'pcm_class':'WMA_RECOVERY_BLOCKED','reason':'la geometría constante de frames del decodificador o la asignación de paquetes candidatos/contexto no está disponible'}
    sr=metadata.get('sample_rate');channels=metadata.get('channels')
    suffix_start=c.get('full_decode_suffix_start_sample_frame')
    dec_total=demux_decoder.get('decoder_output_sample_frames')
    if not sr or not channels or suffix_start is None or dec_total is None or int(dec_total)<=int(suffix_start):
        return {**base,'pcm_class':'WMA_RECOVERY_BLOCKED','reason':'el perfil PCM nativo o la cantidad de muestras del sufijo convergente no está disponible'}
    selected=rows[first_i:]
    if not selected or not all(r.get('hash_equal') and r.get('size_equal') and r.get('pts_preroll_equal') for r in selected):
        return {**base,'pcm_class':'WMA_RECOVERY_BLOCKED','reason':'uno o más objetos multimedia comprimidos del sufijo publicable no cumplen la procedencia de bytes o timestamps'}
    plan={
        'region_index':1,'origin':'POST_PROVEN_MEDIA_OBJECT_GAP','gap_after_media_object_number':c.get('previous_media_object_number'),
        'missing_media_object_count':c.get('missing_media_object_count'),'missing_media_object_numbers':c.get('missing_media_object_numbers') or [],
        'context_media_object_number':c.get('context_media_object_number'),'context_demux_packet_index':context_i,
        'first_published_media_object_number':c.get('expected_first_candidate_media_object_number'),
        'first_published_demux_packet_index':first_i,'first_published_demux_pts_ms':rows[first_i].get('demux_packet_pts_ms'),
        'seek_from_missing_interval_ms':c.get('seek_from_missing_interval_ms'),'damaged_decode_suffix_start_sample_frame':int(suffix_start),
        'output_sample_frames':int(dec_total)-int(suffix_start),'sample_rate':int(sr),'channels':int(channels),
        'decoder_frame_length_samples':int(frame_len),'context_media_object_count':1,'context_audio_published':False,
        'seek_decode_pcm_sha256':c.get('seek_decode_pcm_sha256'),'full_decode_suffix_pcm_sha256':c.get('full_decode_suffix_pcm_sha256'),
        'selected_media_object_count':len(selected),'selected_media_object_numbers':[r.get('media_object_number') for r in selected],
        'selected_media_object_sha256':[r.get('media_object_sha256') for r in selected],
        'selected_demux_packet_sha256':[r.get('demux_packet_sha256') for r in selected],
        'source_timeline_boundary_kind':'ASF_MEDIA_OBJECT_NUMBER_PLUS_DEMUX_PTS_NOT_ABSOLUTE_PCM',
    }
    if not plan['seek_decode_pcm_sha256'] or plan['seek_decode_pcm_sha256']!=plan['full_decode_suffix_pcm_sha256']:
        return {**base,'pcm_class':'WMA_RECOVERY_BLOCKED','reason':'los hashes de la decodificación con búsqueda convergente y del sufijo de decodificación completa no coinciden'}
    return {**base,'pcm_class':'WMA_CONVERGED_SUFFIX','eligible':True,
            'reason':'se demuestra una secuencia de objetos multimedia ASF/WMA ausentes; se consume un objeto superviviente como contexto del decodificador y el siguiente comienza un sufijo convergente de decodificación canónica idéntico en bytes; no se sintetiza PCM ausente ni se afirma una línea de tiempo PCM de la fuente completa',
            'sample_rate':int(sr),'channels':int(channels),'format_tag':int(tag),'codec_name':metadata.get('codec_name'),
            'regions':[plan], '_private_plan':plan}


def _manifest_matches(m:dict, source_sha:str, p:dict):
    return (m.get('producer')=='LossyDoctor' and m.get('producer_version')==APP_VERSION and
            m.get('derivation_kind')==DERIVATION_KIND and m.get('materialization')==MATERIALIZATION and
            m.get('validation_result')=='PASS' and m.get('source_sha256')==source_sha and
            m.get('gap_after_media_object_number')==p.get('gap_after_media_object_number') and
            m.get('missing_media_object_count')==p.get('missing_media_object_count') and
            m.get('context_media_object_number')==p.get('context_media_object_number') and
            m.get('first_published_media_object_number')==p.get('first_published_media_object_number') and
            m.get('output_sample_frames')==p.get('output_sample_frames') and
            m.get('region_pcm_sha256')==p.get('seek_decode_pcm_sha256'))


def _reuse(source:Path, source_sha:str, p:dict):
    for side in source.parent.glob('*.lossydoctor-manifest.json'):
        try:m=json.loads(side.read_text(encoding='utf-8'))
        except Exception:continue
        if not _manifest_matches(m,source_sha,p):continue
        op=Path(m.get('output_path',''))
        if op.exists() and sha256_file(op)==m.get('output_sha256'):
            return {'status':'REUSED','output_path':str(op),'manifest_path':str(side),'manifest':m}
    return None


def export(source:Path, source_sha:str, ffmpeg:str, assessment:dict, publish:bool=True, timeout:int=300):
    if not assessment.get('eligible'):
        return {'status':'NOT_ELIGIBLE','assessment':_public_assessment(assessment),'outputs':[]}
    p=assessment.get('_private_plan') or (assessment.get('regions') or [None])[0]
    if not p:return {'status':'REJECTED','reason':'el plan de recuperación no está disponible','assessment':_public_assessment(assessment),'outputs':[]}
    reused=_reuse(source,source_sha,p)
    if reused:return {'status':'REUSED','assessment':_public_assessment(assessment),'outputs':[reused]}
    sr=int(assessment.get('sample_rate') or 0);channels=int(assessment.get('channels') or 0)
    if sr<=0 or channels<=0:return {'status':'REJECTED','reason':'el perfil PCM nativo no está disponible','assessment':_public_assessment(assessment),'outputs':[]}
    with tempfile.TemporaryDirectory(prefix='lossydoctor-wma-v40-') as td:
        td=Path(td);raw=td/'suffix.s32le'
        seek_ms=p.get('seek_from_missing_interval_ms')
        dec=_decode_s32_file(source,raw,ffmpeg,timeout,(float(seek_ms)/1000.0) if seek_ms is not None else None)
        frame_bytes=channels*4;expected_frames=int(p['output_sample_frames']);expected_bytes=expected_frames*frame_bytes
        if not dec.get('passed') or not raw.exists() or raw.stat().st_size!=expected_bytes:
            return {'status':'REJECTED','reason':'no coincide el tamaño de la decodificación canónica posterior a la brecha','detail':{'decode':dec,'expected_bytes':expected_bytes,'actual_bytes':raw.stat().st_size if raw.exists() else None},'assessment':_public_assessment(assessment),'outputs':[]}
        pcm_sha=sha256_file(raw)
        if pcm_sha!=p.get('seek_decode_pcm_sha256') or pcm_sha!=p.get('full_decode_suffix_pcm_sha256'):
            return {'status':'REJECTED','reason':'el hash PCM convergente posterior a la brecha cambió desde el análisis','assessment':_public_assessment(assessment),'outputs':[]}
        cand=td/'recovered.flac';enc=raw_file_to_flac(raw,cand,ffmpeg,sr,channels,timeout)
        if not enc.get('passed'):
            return {'status':'REJECTED','reason':'falló la codificación FLAC','detail':enc,'assessment':_public_assessment(assessment),'outputs':[]}
        back=td/'back.s32le';bd=decode_to_raw_file(cand,back,ffmpeg,timeout)
        if not bd.get('passed') or not back.exists() or back.stat().st_size!=expected_bytes or sha256_file(back)!=pcm_sha:
            return {'status':'REJECTED','reason':'la verificación round-trip del PCM FLAC no coincide','assessment':_public_assessment(assessment),'outputs':[]}
        desired=source.with_name(source.stem+' [recovered-wma-converged-suffix].flac')
        out=cand
        profile=canonical_pcm_profile(ffmpeg,sr,channels)
        man={
            'schema_version':3,'producer':'LossyDoctor','producer_version':APP_VERSION,'derivation_schema':1,
            'derivation_kind':DERIVATION_KIND,'materialization':MATERIALIZATION,'recovery_policy':POLICY,
            'coverage_claim':COVERAGE,'source_path':str(source),'source_sha256':source_sha,
            'output_path':str(out),'output_sha256':sha256_file(out),'canonical_pcm_profile':profile,
            'sample_rate':sr,'channels':channels,'format_tag':assessment.get('format_tag'),'codec_name':assessment.get('codec_name'),
            'gap_after_media_object_number':p.get('gap_after_media_object_number'),'missing_media_object_count':p.get('missing_media_object_count'),
            'missing_media_object_numbers':p.get('missing_media_object_numbers') or [],'context_media_object_number':p.get('context_media_object_number'),
            'context_demux_packet_index':p.get('context_demux_packet_index'),'context_media_object_count':1,'context_audio_published':False,
            'first_published_media_object_number':p.get('first_published_media_object_number'),'first_published_demux_packet_index':p.get('first_published_demux_packet_index'),
            'first_published_demux_pts_ms':p.get('first_published_demux_pts_ms'),'seek_from_missing_interval_ms':p.get('seek_from_missing_interval_ms'),
            'damaged_decode_suffix_start_sample_frame':p.get('damaged_decode_suffix_start_sample_frame'),'output_sample_frames':expected_frames,
            'decoder_frame_length_samples':p.get('decoder_frame_length_samples'),'selected_media_object_count':p.get('selected_media_object_count'),
            'selected_media_object_numbers':p.get('selected_media_object_numbers'),'selected_media_object_sha256':p.get('selected_media_object_sha256'),
            'selected_demux_packet_sha256':p.get('selected_demux_packet_sha256'),'source_timeline_boundary_kind':p.get('source_timeline_boundary_kind'),
            'full_source_pcm_timeline_claim':False,'synthesized_missing_span':'NONE','resampling':'NONE','channel_remix':'NONE',
            'wma_media_object_bytes_modified':False,'source_modified':False,'temporary_seek_decode_used':True,
            'region_pcm_sha256':pcm_sha,'flac_decoded_pcm_sha256':pcm_sha,'validation_result':'PASS','audio_recoding':'LOSSLESS_FLAC_ONLY'
        }
        out,side,man,publication_status=publish_or_preview_with_manifest(cand,desired,man,publish)
        return {'status':publication_status,'assessment':_public_assessment(assessment),'outputs':[{'status':publication_status,'output_path':str(out) if out is not None else None,'manifest_path':str(side) if side else None,'manifest':man}]}
