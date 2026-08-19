from __future__ import annotations
from app.models import Issue

_STRUCTURAL_DAMAGE_CODES={
    'MPEG_SYNC_LOSS','TRUNCATED_MPEG_FRAME','BIT_RESERVOIR_BACKPOINTER_IMPOSSIBLE',
    'BIT_RESERVOIR_BACKPOINTER_UNAVAILABLE','BIT_RESERVOIR_MAIN_DATA_OVERRUN',
    'MPEG_PARAMETER_CHANGE_AFTER_RESYNC',
}
_TERMINAL_CODES={'MPEG_TRAILING_UNKNOWN_BYTES'}
_COHERENT_HETERO_CODES={'MPEG_COHERENT_PARAMETER_CONCATENATION'}


def _issue_codes(m:dict):
    return {i.code for i in (m.get('issues') or [])}


def build_mpeg_evidence_matrix(m:dict,de:dict,strict:dict,play:dict):
    """Sintetiza evidencia MPEG independiente sin otorgar autoridad de reparación.

    La matriz separa deliberadamente los dominios de evidencia de las conclusiones.
    Una diferencia nunca se usa para reescribir payload comprimido. La igualdad de
    hashes PCM crudos entre decodificadores se excluye de las contradicciones porque
    sus implementaciones pueden diferir numéricamente de forma legítima.
    """
    facts=m.get('facts') or {}; codes=_issue_codes(m)
    reservoir=facts.get('bit_reservoir') or {}; params=facts.get('parameter_segments') or {}; crc=facts.get('crc_protection') or {}
    agreement=(de or {}).get('agreement') or {}; mp=(de or {}).get('mpg123') or {}

    gaps=len(facts.get('gaps') or [])
    truncated=bool(facts.get('truncated_final_frame'))
    reservoir_unresolved=len(reservoir.get('unresolved_pre_segment_frame_indices') or [])
    reservoir_overruns=len(reservoir.get('main_data_overrun_frame_indices') or [])
    crc_mismatches=int(crc.get('mismatch_count') or 0)
    hard_transitions=int(params.get('hard_profile_transition_count') or 0)
    coherent_concats=int(params.get('coherent_concatenation_transition_count') or 0)
    after_resync=int(params.get('parameter_change_after_resync_count') or 0)
    seek_codes=sorted(i.code for i in (m.get('issues') or []) if i.layer in ('seek_metadata','timeline') and i.code not in _COHERENT_HETERO_CODES and i.code!='MPEG_PARAMETER_CHANGE_AFTER_RESYNC')
    terminal_codes=sorted(c for c in codes if c in _TERMINAL_CODES)
    structural_codes=sorted(c for c in codes if c in _STRUCTURAL_DAMAGE_CODES)
    structure_damage=bool(gaps or truncated or reservoir_overruns or structural_codes)
    coherent_heterogeneity=bool(coherent_concats and not structure_damage)
    completion_disagreement=agreement.get('completion_equal') is False
    sample_count_disagreement=agreement.get('sample_frame_count_equal') is False
    decoder_attempted=bool(mp.get('attempted'))
    strict_error=bool(strict.get('completed') and not strict.get('passed'))
    playback_failed=bool(play.get('attempted') and not play.get('completed'))

    domains=[]
    if gaps or truncated or any(c in codes for c in ('MPEG_SYNC_LOSS','TRUNCATED_MPEG_FRAME')):domains.append('FRAMING')
    if reservoir_unresolved or reservoir_overruns or any(c.startswith('BIT_RESERVOIR_') for c in codes):domains.append('BIT_RESERVOIR')
    if crc_mismatches:domains.append('CRC')
    if seek_codes:domains.append('SEEK_OR_TIMELINE_METADATA')
    if terminal_codes:domains.append('TERMINAL_BYTES')
    if hard_transitions:domains.append('PARAMETER_SEGMENTATION')
    if strict_error or playback_failed:domains.append('FFMPEG_DECODE')
    if completion_disagreement or sample_count_disagreement:domains.append('INDEPENDENT_DECODER')
    domains=list(dict.fromkeys(domains))

    if structure_damage:
        interpretation='CORROBORATED_STRUCTURAL_DAMAGE' if len(domains)>=2 else 'STRUCTURAL_DAMAGE_SINGLE_DOMAIN'
    elif coherent_heterogeneity:
        interpretation='COHERENT_HETEROGENEITY'
    elif crc_mismatches and (seek_codes or terminal_codes):
        interpretation='MULTI_DOMAIN_NONCONFORMANCE'
    elif crc_mismatches:
        interpretation='ISOLATED_CRC_INCONSISTENCY'
    elif seek_codes:
        interpretation='ISOLATED_METADATA_NONCONFORMANCE'
    elif terminal_codes:
        interpretation='ISOLATED_TERMINAL_NONCONFORMANCE'
    elif completion_disagreement or sample_count_disagreement:
        interpretation='DECODER_EVIDENCE_DISAGREEMENT'
    else:
        interpretation='CONSISTENT'

    explained_decoder_divergence=None
    if sample_count_disagreement:
        if coherent_heterogeneity or hard_transitions:
            explained_decoder_divergence='HETEROGENEOUS_STREAM_PARAMETERS'
        elif seek_codes:
            explained_decoder_divergence='NONCONFORMANT_PRESENTATION_OR_SEEK_METADATA'
        elif structure_damage:
            explained_decoder_divergence='STRUCTURAL_DAMAGE'

    matrix={
        'schema':1,
        'policy':'CORROBORATE_DO_NOT_REPAIR_FROM_DISAGREEMENT',
        'interpretation':interpretation,
        'active_evidence_domains':domains,
        'repair_authority':'NONE',
        'raw_pcm_hash_equality_used_for_integrity':False,
        'signals':{
            'gap_count':gaps,'truncated_final_frame':truncated,
            'structural_damage_issue_codes':structural_codes,
            'reservoir_unresolved_frame_count':reservoir_unresolved,
            'reservoir_overrun_frame_count':reservoir_overruns,
            'crc_mismatch_count':crc_mismatches,
            'seek_or_timeline_nonconformance_codes':seek_codes,
            'terminal_nonconformance_codes':terminal_codes,
            'hard_parameter_transition_count':hard_transitions,
            'coherent_concatenation_transition_count':coherent_concats,
            'parameter_change_after_resync_count':after_resync,
            'strict_decode_has_errors':strict_error,
            'playback_decode_failed':playback_failed,
            'independent_decoder_attempted':decoder_attempted,
            'decoder_completion_equal':agreement.get('completion_equal'),
            'decoder_sample_frame_count_equal':agreement.get('sample_frame_count_equal'),
            'decoder_pcm_hash_equal_informational_only':agreement.get('raw_s32_pcm_sha256_equal'),
            'decoder_sample_count_divergence_explained_by':explained_decoder_divergence,
        }
    }

    added=[]
    # Una diferencia de conteo sólo es informable cuando ninguna evidencia
    # estructural, de metadatos, CRC, terminal o de stream heterogéneo la explica.
    # Nunca autoriza una reparación.
    if decoder_attempted and sample_count_disagreement and not (structure_damage or hard_transitions or crc_mismatches or seek_codes or terminal_codes):
        ff=(de or {}).get('ffmpeg') or {}
        added.append(Issue('DECODER_SAMPLE_COUNT_DISAGREEMENT','decoder_evidence',
            'FFmpeg y mpg123 completan la decodificación de este stream MPEG homogéneo y estructuralmente limpio, pero informan cantidades diferentes de frames de muestras decodificadas. La discrepancia es sólo evidencia y no identifica qué interpretación tiene autoridad.',
            integrity='SUSPICIOUS',compatibility='POSSIBLE',playability='POSSIBLY_AFFECTED',repairability='NONE',
            evidence=[{'ffmpeg_sample_frames':ff.get('sample_frames'),'mpg123_sample_frames':mp.get('sample_frames'),'mpg123_version':mp.get('decoder_version')}]))
    return matrix,added
