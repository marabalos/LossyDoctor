from __future__ import annotations
from pathlib import Path
from app.models import Analysis, Issue
from app.utils import sha256_file,event
from app.external import ffprobe,decode,canonical_pcm_profile,ffmpeg_evidence_decode,independent_decoder_evidence,asf_wma_demux_decoder_evidence,asf_wma_decoder_convergence_evidence,aac_adts_demux_evidence,mp4_aac_demux_evidence
from formats.identify import identify
from app import repairs, lossless_export, mp4_aac_recovery, mp4_aac_repair, mp4_aac_timeline, mp4_aac_timeline_export, aac_adts_timeline, aac_adts_repair, aac_adts_recovery, opus_recovery, vorbis_recovery, wma_recovery, wma_multi_region_recovery
from app.evidence_matrix import build_mpeg_evidence_matrix
from app.derived_evidence import build_causal_graph,build_pattern_analysis
from app.preservation_hierarchy import resolve as resolve_preservation_hierarchy
from app.opus_preservation_hierarchy import resolve as resolve_opus_preservation_hierarchy
from app.vorbis_preservation_hierarchy import resolve as resolve_vorbis_preservation_hierarchy
from app.wma_preservation_hierarchy import resolve as resolve_wma_preservation_hierarchy
from app.mp4_aac_preservation_hierarchy import resolve as resolve_mp4_aac_preservation_hierarchy
from app.aac_adts_preservation_hierarchy import resolve as resolve_aac_adts_preservation_hierarchy
from policy.engine import classify

EXT={'mp3':'.mp3','mp2':'.mp2','aac':'.m4a','vorbis':'.ogg','opus':'.opus','wma':'.wma'}
COMPAT_EXT={
    'mp3':{'.mp3','.mpa'},'mp2':{'.mp2','.mpa'},
    'aac':{'.m4a','.mp4','.m4b'},'vorbis':{'.ogg','.oga'},
    'opus':{'.opus','.ogg','.oga'},'wma':{'.wma','.asf'},
}


def _verified_bitstream_repair(executions:list[dict]):
    for ex in executions:
        if ex.get('status') not in ('CREATED','REUSED'):continue
        man=ex.get('manifest') or {};v=man.get('verification') or ex.get('verification') or {}
        if man.get('derivation_kind')!='REPAIRED_SAFE':continue
        if v.get('passed') and v.get('strict_decode')=='PASS' and v.get('playback_decode')=='PASS' and v.get('ffprobe')=='PASS':return True
    return False


def _derive_evidence(a):
    a.pattern_analysis=build_pattern_analysis(a.issues);a.causal_graph=build_causal_graph(a.issues,a.repair_execution)
    return a


def analyze_file(path:Path,cfg:dict,root:Path,ffmpeg:str,ffprobe_exe:str,mpg123_exe:str|None=None,mpg123_trust:str="PINNED_SHA256"):

    st=path.stat();a=Analysis(str(path),path.name,{'size':st.st_size,'mtime_ns':st.st_mtime_ns})
    a.events.append(event('analysis_started','started'))
    ident=identify(path,cfg['app']['max_resync_scan_bytes'])
    if not ident.get('supported'):
        a.run_status='SKIPPED_UNSUPPORTED';a.skipped_reason=ident.get('reason','contenido no compatible');a.events.append(event('fast_sniff','skipped',reason=a.skipped_reason));return _derive_evidence(a)
    a.detected_container=ident['container'];a.detected_codec=ident['codec'];a.format_confidence=ident.get('confidence','MEDIUM');a.expected_extension='.aac' if a.detected_container=='AAC_ADTS' else EXT.get(a.detected_codec)
    a.identity={'file_sha256':sha256_file(path,cfg['analysis']['sha256_chunk_size'])};source_sha=a.identity['file_sha256'];a.events.append(event('file_hash','success',sha256=source_sha))
    compat_ext={'.aac','.adts'} if a.detected_container=='AAC_ADTS' else COMPAT_EXT.get(a.detected_codec,{a.expected_extension})
    extension_mismatch=bool(a.expected_extension and path.suffix.lower() not in compat_ext)
    if extension_mismatch:
        a.issues.append(Issue('EXTENSION_CONTENT_MISMATCH','filesystem',f'Extension {path.suffix} no coincide con el formato detectado {a.detected_codec}',integrity='NONCONFORMANT',compatibility='POSSIBLE',repairability='SAFE_IF_VERIFIED'))
    probe=ffprobe(path,ffprobe_exe,cfg['app']['external_timeout_seconds']);strict=decode(path,ffmpeg,'STRICT_DECODE',cfg['app']['external_timeout_seconds']);play=decode(path,ffmpeg,'PLAYBACK_DECODE',cfg['app']['external_timeout_seconds'])
    a.decode_results={'STRICT_DECODE':strict,'PLAYBACK_DECODE':play};a.playability='PLAYABLE' if probe.get('audio_streams') and play.get('completed') else 'UNPLAYABLE'
    a.canonical_pcm_profile=canonical_pcm_profile(ffmpeg)
    # Corregir la extensión es independiente de reparar el codec y conserva siempre los bytes.
    ext_plan=repairs.plan_extension(path,a.detected_container,a.detected_codec,a.expected_extension,a.format_confidence,extension_mismatch)
    if ext_plan:
        a.repair_plan.append(ext_plan)
        if cfg['repair']['enabled'] and cfg['app']['mode']!='audit_only':
            ex=repairs.execute_extension(path,source_sha,ext_plan,cfg['app']['max_resync_scan_bytes'],cfg['repair']['publish_verified']) if ext_plan['status']=='ELIGIBLE' else {'repair_spec_id':ext_plan['spec']['id'],'status':ext_plan['status'],'reason':ext_plan['reason']}
            a.repair_execution.append(ex)
    if a.detected_codec=='opus' and ident.get('ogg_opus'):
        q=ident['ogg_opus'];a.metadata=q['metadata'];a.format_facts=q['facts'];a.structural_map=q['structural_map'];a.issues.extend(q['issues'])
        h=q['facts'].get('opus_head') or {};channels=h.get('channels')
        a.canonical_pcm_profile=canonical_pcm_profile(ffmpeg,48000,channels)
        a.canonical_presentation_window={'determined':bool(h.get('valid') and q['facts'].get('final_granule_position') is not None),'sample_rate':48000,'channels':channels,'pre_skip':h.get('pre_skip'),'final_granule_position':q['facts'].get('final_granule_position'),'pcm_sample_position':q['facts'].get('pcm_sample_position'),'output_gain_q7_8':h.get('output_gain_q7_8'),'output_gain_policy':'PRESERVE_UNAPPLIED_Q7_8_IN_MANIFEST'}
        timeline_codes={'OPUS_GRANULE_POSITION_MISSING','OPUS_GRANULE_POSITION_NONMONOTONIC','OPUS_GRANULE_DELTA_MISMATCH','OPUS_END_TRIM_INVALID','OPUS_HEADER_GRANULE_NONZERO'}
        container_codes={'OGG_PAGE_CRC_MISMATCH','OGG_PAGE_SEQUENCE_DISCONTINUITY','OGG_CONTINUATION_FLAG_INCONSISTENT','OGG_SYNC_LOSS','OGG_TRUNCATED_PAGE','OGG_INCOMPLETE_PACKET_AT_EOF','OGG_VERSION_UNSUPPORTED','OPUS_BOS_HEADER_PAGE_INVALID','OPUS_HEAD_PAGE_LAYOUT_INVALID'}
        codes={i.code for i in a.issues}
        a.validity_domains={'DECODE_VALIDITY':'VALID' if strict['passed'] else ('USABLE_WITH_ERRORS' if play['completed'] else 'INVALID'),'CONTAINER_VALIDITY':'VALID' if not (codes&container_codes) else 'NONCONFORMANT_OR_DAMAGED','TIMELINE_VALIDITY':'VALIDATED_GRANULES' if not (codes&timeline_codes) and q['facts'].get('ogg',{}).get('eos_present') else ('OPEN_ENDED_NO_EOS' if codes=={'OGG_OPUS_EOS_MISSING'} else 'NONCONFORMANT_GRANULES'),'SEEKABILITY_VALIDITY':'VALIDATED_GRANULES' if not (codes&timeline_codes) else 'NONCONFORMANT_GRANULES'}
        a.policy_decisions.append({'code':'OPUS_OUTPUT_GAIN_PRESERVATION','decision':'PRESERVE_UNAPPLIED_Q7_8_IN_MANIFEST','reason':'la ganancia de salida OpusHead se preserva como metadata de presentación y no se aplica al PCM canónico de preservación; la recuperación vigente neutraliza la ganancia sólo en una vista temporal de decodificación y preserva el recorte EOS autenticado y la procedencia de paquetes continuados','output_gain_q7_8':h.get('output_gain_q7_8')})
        a.format_facts['output_gain_preservation']={'policy':'PRESERVE_UNAPPLIED_Q7_8_IN_MANIFEST','canonical_pcm_gain_db':0.0,'source_output_gain_q7_8':h.get('output_gain_q7_8'),'source_output_gain_db':h.get('output_gain_db'),'baked_into_pcm':False}
        if cfg['repair']['enabled'] and cfg['app']['mode']!='audit_only':
            rr=repairs.execute_ogg_opus(path,source_sha,q,ffmpeg,ffprobe_exe,cfg['repair']['publish_verified'],cfg['app']['max_resync_scan_bytes'],cfg['app']['external_timeout_seconds'])
            a.repair_plan.extend(rr['plans']);a.repair_execution.extend(rr['executions'])
        ora=opus_recovery.assess(path,q,a.playability);a.pcm_recovery_class=ora.get('pcm_class','OPUS_RECOVERY_BLOCKED');a.recovery_assessment={k:v for k,v in ora.items() if not k.startswith('_')}
        preservational_bitstream_repair=_verified_bitstream_repair(a.repair_execution)
        if cfg['lossless_recovery']['enabled'] and cfg['app']['mode']!='audit_only' and ora.get('eligible'):
            if preservational_bitstream_repair:
                a.policy_decisions.append({'code':'BITSTREAM_REPAIR_PRECEDES_PCM_DERIVATION','decision':'LOSSLESS_PCM_EXPORT_SUPPRESSED','reason':'una reparación verificada del bitstream Ogg resuelve el defecto estructural; la recuperación de regiones PCM Opus demostradas permanece sólo como alternativa'})
            else:
                le=opus_recovery.export(path,source_sha,q,ffmpeg,ora,cfg['lossless_recovery']['publish_verified'])
                if le.get('status') not in ('NOT_ELIGIBLE','POLICY_BLOCKED'):a.lossless_export.append(le)
        opus_hierarchy=resolve_opus_preservation_hierarchy(a.repair_execution,a.lossless_export,a.recovery_assessment,a.playability,{i.code for i in a.issues})
        a.format_facts['opus_preservation_hierarchy']=opus_hierarchy
        if opus_hierarchy.get('policy_violation'):
            a.error=opus_hierarchy['policy_violation'];a.run_status='FAILED';a.final_status=['ANALYSIS_FAILED'];return _derive_evidence(a)
        if sha256_file(path,cfg['analysis']['sha256_chunk_size'])!=source_sha:
            a.error='INPUT_CHANGED_DURING_PROCESS';a.run_status='FAILED';a.final_status=['ANALYSIS_FAILED'];return _derive_evidence(a)
    elif a.detected_codec=='vorbis' and ident.get('ogg_vorbis'):
        q=ident['ogg_vorbis'];a.metadata=q['metadata'];a.format_facts=q['facts'];a.structural_map=q['structural_map'];a.issues.extend(q['issues'])
        vi=q['facts'].get('vorbis_identification') or {};sr=vi.get('sample_rate');channels=vi.get('channels')
        a.canonical_pcm_profile=canonical_pcm_profile(ffmpeg,sr,channels)
        final_gp=q['facts'].get('final_granule_position')
        a.canonical_presentation_window={'determined':bool(vi.get('valid') and final_gp is not None and q['facts'].get('ogg',{}).get('eos_present')),'sample_rate':sr,'channels':channels,'final_granule_position':final_gp,'pcm_sample_position':final_gp,'presentation_model':'VORBIS_PCM_GRANULE_POSITION'}
        timeline_codes={'VORBIS_GRANULE_POSITION_MISSING','VORBIS_GRANULE_POSITION_NONMONOTONIC','VORBIS_GRANULE_DELTA_MISMATCH','VORBIS_EOS_TRIM_INVALID','VORBIS_HEADER_GRANULE_NONZERO'}
        container_codes={'OGG_PAGE_CRC_MISMATCH','OGG_PAGE_SEQUENCE_DISCONTINUITY','OGG_CONTINUATION_FLAG_INCONSISTENT','OGG_SYNC_LOSS','OGG_TRUNCATED_PAGE','OGG_INCOMPLETE_PACKET_AT_EOF','OGG_VERSION_UNSUPPORTED','VORBIS_BOS_HEADER_PAGE_INVALID','VORBIS_IDENTIFICATION_PAGE_LAYOUT_INVALID','VORBIS_SETUP_PAGE_LAYOUT_INVALID'}
        header_codes={'VORBIS_IDENTIFICATION_HEADER_INVALID','VORBIS_COMMENT_HEADER_INVALID','VORBIS_SETUP_HEADER_INVALID','VORBIS_HEADER_ORDER_INVALID','VORBIS_AUDIO_PACKET_HEADER_INVALID'}
        codes={i.code for i in a.issues};eos=q['facts'].get('ogg',{}).get('eos_present')
        if not (codes&timeline_codes) and eos: timeline='VALIDATED_GRANULES_AND_BLOCKSIZES'
        elif codes=={'OGG_VORBIS_EOS_MISSING'}: timeline='OPEN_ENDED_NO_EOS'
        else: timeline='NONCONFORMANT_GRANULES_OR_BLOCKSIZES'
        a.validity_domains={'DECODE_VALIDITY':'VALID' if strict['passed'] else ('USABLE_WITH_ERRORS' if play['completed'] else 'INVALID'),'CONTAINER_VALIDITY':'VALID' if not (codes&container_codes) else 'NONCONFORMANT_OR_DAMAGED','CODEC_HEADER_VALIDITY':'VALID' if not (codes&header_codes) else 'NONCONFORMANT_OR_DAMAGED','TIMELINE_VALIDITY':timeline,'SEEKABILITY_VALIDITY':'VALIDATED_GRANULES_AND_BLOCKSIZES' if timeline=='VALIDATED_GRANULES_AND_BLOCKSIZES' else timeline}
        if cfg['repair']['enabled'] and cfg['app']['mode']!='audit_only':
            rr=repairs.execute_ogg_vorbis(path,source_sha,q,ffmpeg,ffprobe_exe,cfg['repair']['publish_verified'],cfg['app']['max_resync_scan_bytes'],cfg['app']['external_timeout_seconds'])
            a.repair_plan.extend(rr['plans']);a.repair_execution.extend(rr['executions'])
        vra=vorbis_recovery.assess(path,q,a.playability)
        a.pcm_recovery_class=vra.get('pcm_class','VORBIS_RECOVERY_BLOCKED');a.recovery_assessment={k:v for k,v in vra.items() if not k.startswith('_')}
        preservational_bitstream_repair=_verified_bitstream_repair(a.repair_execution)
        if cfg['lossless_recovery']['enabled'] and cfg['app']['mode']!='audit_only' and vra.get('eligible'):
            if preservational_bitstream_repair:
                a.policy_decisions.append({'code':'BITSTREAM_REPAIR_PRECEDES_PCM_DERIVATION','decision':'LOSSLESS_PCM_EXPORT_SUPPRESSED','reason':'una recaptura verificada de páginas Ogg/Vorbis resuelve el defecto estructural sin cambiar ninguna página Ogg ni paquete Vorbis retenido; la recuperación de regiones PCM demostradas permanece sólo como alternativa'})
            else:
                le=vorbis_recovery.export(path,source_sha,q,ffmpeg,vra,cfg['lossless_recovery']['publish_verified'])
                if le.get('status') not in ('NOT_ELIGIBLE','POLICY_BLOCKED'):a.lossless_export.append(le)
        a.policy_decisions.append({'code':'VORBIS_PRESERVATION_HIERARCHY_AUTHORITY','decision':'STRICT_PRESERVATION_HIERARCHY_NO_NEW_AUTHORITY','reason':'la política vigente formaliza la precedencia de preservación Vorbis sin agregar autoridad: recaptura de páginas Ogg autenticadas por CRC, recuperación de regiones probadas con EOS autenticado, recuperación abierta o truncada y finalmente sólo reporte'})
        vorbis_hierarchy=resolve_vorbis_preservation_hierarchy(a.repair_execution,a.lossless_export,a.recovery_assessment,a.playability,{i.code for i in a.issues})
        a.format_facts['vorbis_preservation_hierarchy']=vorbis_hierarchy
        if vorbis_hierarchy.get('policy_violation'):
            a.error=vorbis_hierarchy['policy_violation'];a.run_status='FAILED';a.final_status=['ANALYSIS_FAILED'];return _derive_evidence(a)
        if sha256_file(path,cfg['analysis']['sha256_chunk_size'])!=source_sha:
            a.error='INPUT_CHANGED_DURING_PROCESS';a.run_status='FAILED';a.final_status=['ANALYSIS_FAILED'];return _derive_evidence(a)
    elif a.detected_container=='MP4' and a.detected_codec=='aac' and ident.get('mp4_aac'):
        q=ident['mp4_aac'];a.metadata=q['metadata'];a.format_facts=q['facts'];a.format_facts['ffprobe']=probe;a.structural_map=q['structural_map'];a.issues.extend(q['issues'])
        tracks=q['facts'].get('tracks') or [];audio=next((x for x in tracks if x.get('handler_type')=='soun'),{});descriptions=audio.get('sample_descriptions') or [];description=descriptions[0] if descriptions else {};aac_config=description.get('aac_config') or {}
        provenance=audio.get('access_unit_provenance') or {};access_units=provenance.get('access_units') or []
        chunk_table=(audio.get('sample_tables') or {}).get('stco') or (audio.get('sample_tables') or {}).get('co64') or {};no_addressable_audio=bool((chunk_table.get('entry_count') and chunk_table.get('valid_offset_count')==0) or (provenance.get('mapped_sample_count') and provenance.get('hashed_sample_count')==0))
        if no_addressable_audio:a.playability='UNPLAYABLE'
        sr=description.get('sample_rate') or aac_config.get('sample_rate');channels=description.get('channels')
        a.canonical_pcm_profile=canonical_pcm_profile(ffmpeg,sr,channels)
        presentation=audio.get('presentation_window') or {};a.canonical_presentation_window={**presentation,'channels':channels,'mapped_access_unit_count':provenance.get('mapped_sample_count'),'decode_end_units':provenance.get('decode_end_units')}
        dem=mp4_aac_demux_evidence(path,ffprobe_exe,access_units,audio.get('media_timescale'),cfg['app']['external_timeout_seconds']);a.format_facts['mp4_aac_demux_evidence']=dem
        presentation_decode=ffmpeg_evidence_decode(path,ffmpeg,channels,cfg['app']['external_timeout_seconds']);a.format_facts['mp4_aac_presentation_decoder_evidence']=presentation_decode
        no_demux_audio=bool(dem.get('packet_probe_passed') and provenance.get('mapped_sample_count') and dem.get('ffprobe_packet_count')==0)
        if no_demux_audio:a.playability='UNPLAYABLE'
        simple_presentation=presentation.get('presentation_model')=='SINGLE_NORMAL_RATE_MEDIA_EDIT'
        multi_edit_presentation=presentation.get('presentation_model')=='MULTI_EDIT_PRESENTATION'
        fragmented_presentation=presentation.get('presentation_model') in ('FRAGMENTED_NORMAL_RATE_MEDIA_TIMELINE','FRAGMENTED_SINGLE_NORMAL_RATE_MEDIA_EDIT','FRAGMENTED_MULTI_EDIT_PRESENTATION')
        fragmented=bool((q['facts'].get('mp4') or {}).get('fragmented'))
        complex_edit_list=presentation.get('edit_list_entry_count',0)>1
        if not complex_edit_list and provenance.get('mapping_complete') and dem.get('packet_probe_passed') and not dem.get('all_boundaries_and_hashes_equal'):
            a.issues.append(Issue('MP4_AAC_DEMUX_ACCESS_UNIT_MISMATCH','decoder_evidence','La asignación directa de la tabla de muestras MP4 no coincide con los paquetes AAC entregados por FFprobe en cantidad, posición de bytes, tamaño y SHA-256.',integrity='SUSPICIOUS',playability='POSSIBLY_AFFECTED',repairability='NONE',evidence=[{'direct_access_units':dem.get('direct_access_unit_count'),'demux_packets':dem.get('ffprobe_packet_count'),'positions_equal':dem.get('positions_equal'),'sizes_equal':dem.get('sizes_equal'),'hashes_equal':dem.get('hashes_equal')}]))
        if simple_presentation and presentation.get('determined') and dem.get('dts_shift_is_constant') and dem.get('constant_dts_shift_media_units')!=-presentation.get('media_start_units',0):
            a.issues.append(Issue('MP4_EDIT_LIST_DEMUX_SHIFT_MISMATCH','decoder_evidence','El desplazamiento temporal de los paquetes FFprobe no coincide con el inicio multimedia validado de la lista de edición MP4.',integrity='NONCONFORMANT',playability='POSSIBLY_AFFECTED',repairability='NONE',evidence=[{'edit_media_start_units':presentation.get('media_start_units'),'demux_dts_shift_media_units':dem.get('constant_dts_shift_media_units')}]))
        same_presentation_domain=simple_presentation
        if same_presentation_domain and presentation.get('determined') and presentation_decode.get('completed') and presentation.get('presentation_sample_count') is not None and presentation_decode.get('sample_frames')!=presentation.get('presentation_sample_count'):
            a.issues.append(Issue('MP4_PRESENTATION_SAMPLE_COUNT_MISMATCH','decoder_evidence','La cantidad canónica de muestras decodificadas por FFmpeg no coincide con la ventana de presentación validada de la lista de edición MP4.',integrity='NONCONFORMANT',playability='POSSIBLY_AFFECTED',repairability='NONE',evidence=[{'edit_list_presentation_samples':presentation.get('presentation_sample_count'),'decoder_output_samples':presentation_decode.get('sample_frames')}]))
        segment_audit=mp4_aac_timeline.audit(path,audio,ffmpeg,cfg['app']['external_timeout_seconds']) if complex_edit_list or fragmented_presentation else {}
        segment_provenance_valid=bool(segment_audit.get('segment_level_provenance_validated'))
        fragmented_pcm_valid=bool(fragmented_presentation and segment_provenance_valid)
        if complex_edit_list:
            a.format_facts['mp4_aac_multi_edit_audit']={**segment_audit,'structural_timeline_determined':presentation.get('determined'),
                'presentation_segment_count':presentation.get('presentation_segment_count'),
                'decoder_sample_count_matches':bool(presentation.get('determined') and presentation_decode.get('completed') and presentation_decode.get('sample_frames')==presentation.get('presentation_sample_count'))}
        canonical_export_assessment=mp4_aac_timeline_export.assess(segment_audit,presentation_decode) if complex_edit_list or fragmented_presentation else {}
        if canonical_export_assessment:a.format_facts['mp4_aac_canonical_timeline_export_assessment']=canonical_export_assessment
        codes={i.code for i in a.issues}
        container_codes={'MP4_BOX_SIZE_INVALID','MP4_TRAILING_BYTES','MP4_CONTAINER_TRAILING_BYTES','MP4_FTYP_MISSING','MP4_MOOV_MISSING','MP4_MDAT_MISSING'}
        header_codes={'MP4_SAMPLE_DESCRIPTION_TABLE_INVALID','MP4_AAC_DECODER_CONFIG_INVALID','MP4_ACCESS_UNIT_DESCRIPTION_INVALID'}
        table_codes={'MP4_SAMPLE_SIZE_TABLE_TRUNCATED','MP4_SAMPLE_COUNT_MISMATCH','MP4_CHUNK_OFFSET_OUTSIDE_MDAT','MP4_CHUNK_OFFSET_TABLE_INVALID','MP4_SAMPLE_TO_CHUNK_TABLE_INVALID','MP4_ACCESS_UNIT_MAPPING_INCOMPLETE','MP4_ACCESS_UNIT_OUTSIDE_MDAT','MP4_ACCESS_UNIT_OVERLAP'}
        timeline_codes={'MP4_MEDIA_DURATION_MISMATCH','MP4_DECODING_TIME_TABLE_INVALID','MP4_ACCESS_UNIT_TIMELINE_INCOMPLETE','MP4_MOVIE_HEADER_INVALID','MP4_EDIT_LIST_INVALID','MP4_EDIT_LIST_RATE_UNSUPPORTED','MP4_EDIT_LIST_MEDIA_RANGE_INVALID','MP4_EDIT_LIST_TIMEBASE_INEXACT','MP4_EDIT_LIST_SAMPLE_COUNT_INEXACT','MP4_MOVIE_DURATION_MISMATCH','MP4_EDIT_LIST_DEMUX_SHIFT_MISMATCH','MP4_PRESENTATION_SAMPLE_COUNT_MISMATCH'}
        decoder_sample_count_valid=bool(presentation.get('determined') and presentation_decode.get('completed') and presentation_decode.get('sample_frames')==presentation.get('presentation_sample_count'))
        simple_presentation_valid=bool(simple_presentation and decoder_sample_count_valid and dem.get('constant_dts_shift_media_units')==-presentation.get('media_start_units',0))
        multi_edit_audit_valid=bool(multi_edit_presentation and decoder_sample_count_valid)
        fragmented_timeline_valid=bool(fragmented_presentation and presentation.get('determined') and provenance.get('mapping_complete') and provenance.get('decode_timeline_complete'))
        a.validity_domains={'DECODE_VALIDITY':'INVALID_NO_ADDRESSABLE_MEDIA_CHUNKS' if no_addressable_audio else ('INVALID_NO_DEMUXED_AUDIO_PACKETS' if no_demux_audio else ('VALID' if strict['passed'] else ('USABLE_WITH_ERRORS' if play['completed'] else 'INVALID'))),
            'CONTAINER_VALIDITY':'VALIDATED_BASIC_BOX_STRUCTURE' if not (codes&container_codes) else 'NONCONFORMANT_OR_DAMAGED_MP4_BOXES',
            'CODEC_HEADER_VALIDITY':'VALIDATED_AAC_SAMPLE_DESCRIPTION' if not (codes&header_codes) else 'NONCONFORMANT_OR_DAMAGED_AAC_CONFIGURATION',
            'SAMPLE_TABLE_VALIDITY':('VALIDATED_FRAGMENT_RUN_ACCESS_UNIT_MAPPING' if fragmented else 'VALIDATED_SAMPLE_TO_CHUNK_ACCESS_UNIT_MAPPING') if provenance.get('mapping_complete') and not (codes&table_codes) else 'NONCONFORMANT_OR_DAMAGED_SAMPLE_TABLES',
            'ACCESS_UNIT_PROVENANCE_VALIDITY':'VALIDATED_BYTE_EXACT_SHA256' if provenance.get('mapping_complete') and provenance.get('all_access_units_hashed') else 'NONCONFORMANT_OR_INCOMPLETE',
            'DEMUX_BOUNDARY_VALIDITY':('VALIDATED_DIRECT_MULTI_EDIT_SEGMENT_ACCESS_UNIT_PROVENANCE' if segment_provenance_valid else 'DEFERRED_MULTI_EDIT_SEGMENT_MAPPING') if complex_edit_list else ('VALIDATED_DIRECT_SAMPLE_TO_FFPROBE_PACKET_IDENTITY' if dem.get('all_boundaries_and_hashes_equal') else ('UNAVAILABLE' if not dem.get('packet_probe_passed') else 'NONCONFORMANT_OR_INCOMPLETE')),
            'TIMELINE_VALIDITY':('VALIDATED_FRAGMENTED_PRESENTATION_PCM_PROVENANCE_AUDIT_ONLY' if fragmented_pcm_valid else ('VALIDATED_FRAGMENTED_ACCESS_UNIT_TIMELINE_AUDIT_ONLY' if fragmented_timeline_valid else ('VALIDATED_SINGLE_EDIT_PRESENTATION_AND_DECODER_SAMPLE_COUNT' if provenance.get('decode_timeline_complete') and simple_presentation_valid and not (codes&timeline_codes) else ('VALIDATED_MULTI_EDIT_SEGMENT_PROVENANCE_AUDIT_ONLY' if segment_provenance_valid else ('VALIDATED_MULTI_EDIT_STRUCTURE_AND_DECODER_SAMPLE_COUNT_AUDIT_ONLY' if provenance.get('decode_timeline_complete') and multi_edit_audit_valid and not (codes&timeline_codes) else 'NONCONFORMANT_OR_INCOMPLETE_MEDIA_PRESENTATION'))))),
            'SEEKABILITY_VALIDITY':('VALIDATED_FRAGMENT_RUN_ADDRESSING' if fragmented else 'VALIDATED_SAMPLE_TABLE_ADDRESSING') if provenance.get('mapping_complete') else 'NONCONFORMANT_OR_INCOMPLETE'}
        if fragmented:
            fragment_facts=q['facts'].get('fragmented_mp4') or {};a.format_facts['mp4_aac_fragmented_audit']={"policy":"MP4_AAC_FRAGMENTED_MP4_AUDIT_ONLY",
                "fragment_count":fragment_facts.get('fragment_count'),"fragment_sequence_numbers":fragment_facts.get('sequence_numbers'),
                "fragment_run_count":len(fragment_facts.get('runs') or []),"access_unit_count":fragment_facts.get('sample_count'),
                "access_unit_mapping_complete":fragment_facts.get('mapping_complete'),"presentation_determined":presentation.get('determined'),
                "decoder_sample_count_matches":decoder_sample_count_valid,"presentation_pcm_provenance_validated":fragmented_pcm_valid,
                "canonical_presentation_pcm_s32le_sha256":segment_audit.get('canonical_presentation_pcm_s32le_sha256'),
                "independent_decoded_media_sample_count":segment_audit.get('decoded_media_sample_count'),
                "decoded_tail_padding_samples_excluded":segment_audit.get('decoded_tail_padding_samples_excluded'),"intervention_authority":False}
        mp4_recovery=mp4_aac_recovery.assess(path,q,a.playability,ffmpeg,cfg['app']['external_timeout_seconds'])
        mp4_recovery_public={k:v for k,v in mp4_recovery.items() if not k.startswith('_')};a.format_facts['mp4_aac_recovery_assessment']=mp4_recovery_public
        active_recovery=canonical_export_assessment if canonical_export_assessment.get('eligible') else {**mp4_recovery_public,'policy':'MP4_AAC_COMPLETE_CLEAN_LOSSLESS_EXPORT','authority':'COMPLETE_CLEAN_LOSSLESS_EXPORT' if mp4_recovery.get('eligible') else 'NO_PUBLICATION_AUTHORITY','publication_enabled':bool(mp4_recovery.get('eligible')),'pcm_recovery_authority':'COMPLETE_CLEAN_LOSSLESS_EXPORT' if mp4_recovery.get('eligible') else 'NONE'}
        a.pcm_recovery_class=active_recovery.get('pcm_class','MP4_AAC_RECOVERY_BLOCKED');a.recovery_assessment=active_recovery
        if cfg['repair']['enabled'] and cfg['app']['mode']!='audit_only':
            repaired=mp4_aac_repair.execute(path,source_sha,q,mp4_recovery,ffmpeg,ffprobe_exe,cfg['repair']['publish_verified'],cfg['app']['external_timeout_seconds'])
            a.repair_plan.extend(repaired['plans']);a.repair_execution.extend(repaired['executions'])
        preservational_mp4_repair=_verified_bitstream_repair(a.repair_execution)
        if cfg['lossless_recovery']['enabled'] and cfg['app']['mode']!='audit_only' and mp4_recovery.get('eligible') and not preservational_mp4_repair:
            exported=mp4_aac_recovery.export(path,source_sha,ffmpeg,mp4_recovery,cfg['lossless_recovery']['publish_verified'],cfg['app']['external_timeout_seconds'])
            if exported.get('status') not in ('NOT_ELIGIBLE','POLICY_BLOCKED'):a.lossless_export.append(exported)
        elif cfg['lossless_recovery']['enabled'] and cfg['app']['mode']!='audit_only' and canonical_export_assessment.get('eligible') and not preservational_mp4_repair:
            exported=mp4_aac_timeline_export.export(path,source_sha,audio,ffmpeg,segment_audit,canonical_export_assessment,cfg['lossless_recovery']['publish_verified'],cfg['app']['external_timeout_seconds'])
            if exported.get('status') not in ('NOT_ELIGIBLE','POLICY_BLOCKED'):a.lossless_export.append(exported)
        elif preservational_mp4_repair and mp4_recovery.get('eligible'):
            a.policy_decisions.append({'code':'BITSTREAM_REPAIR_PRECEDES_PCM_DERIVATION','decision':'LOSSLESS_PCM_EXPORT_SUPPRESSED','reason':'la reparación verificada del offset de chunk MP4 restaura el contenedor original y preserva todos los bytes de las unidades de acceso AAC y el PCM de presentación canónico'})
        a.policy_decisions.append({'code':'MP4_AAC_STRUCTURAL_AUDIT_AUTHORITY','decision':'STRUCTURAL_AUDIT_ONLY_NO_REPAIR_OR_RECOVERY','reason':'los boxes MP4 básicos, pistas de audio, configuración del decodificador AAC, duración declarada, cantidades de muestras y offsets de fragmentos son sólo evidencia; reparación NONE y recuperación PCM NONE'})
        a.policy_decisions.append({'code':'MP4_AAC_ACCESS_UNIT_PROVENANCE_AUTHORITY','decision':'ACCESS_UNIT_PROVENANCE_AUDIT_ONLY_NO_REPAIR_OR_RECOVERY','reason':'se validan el mapeo stsc/stsz/stco o co64, el SHA-256 individual de cada unidad AAC, los tiempos de decodificación y la identidad de paquetes de FFprobe; la presentación canónica por lista de edición queda diferida y no se concede autoridad de intervención'})
        a.policy_decisions.append({'code':'MP4_AAC_SIMPLE_EDIT_PRESENTATION_AUTHORITY','decision':'SIMPLE_EDIT_LIST_PRESENTATION_AUDIT_ONLY_NO_REPAIR_OR_RECOVERY','reason':'una edición de medios a velocidad normal sólo establece la ventana audible canónica cuando coinciden exactamente la conversión temporal, los límites, el desplazamiento DTS de FFprobe y la cantidad decodificada por FFmpeg; la reparación y la recuperación PCM permanecen bloqueadas'})
        a.policy_decisions.append({'code':'MP4_AAC_MULTI_EDIT_PRESENTATION_AUTHORITY','decision':'SINGLE_TRACK_MULTI_EDIT_PRESENTATION_AUDIT_ONLY_NO_REPAIR_OR_RECOVERY','reason':'una pista AAC puede contener varias ediciones exactas a velocidad normal y ediciones vacías explícitas; se registra su presentación ordenada y se distingue PCM de origen de silencio de línea de tiempo, sin autoridad de intervención hasta validar la procedencia por segmento'})
        a.policy_decisions.append({'code':'MP4_AAC_MULTI_EDIT_PROVENANCE_AUTHORITY','decision':'MULTI_EDIT_SEGMENT_PROVENANCE_AUDIT_ONLY_NO_REPAIR_OR_RECOVERY','reason':'cada edición normal se vincula con unidades AAC autenticadas y un rango PCM decodificado independientemente; las ediciones vacías se autentican como silencio explícito y no se confía en el comportamiento de reproducción del contenedor'})
        a.policy_decisions.append({'code':'MP4_AAC_FRAGMENTED_AUDIT_AUTHORITY','decision':'SINGLE_TRACK_FRAGMENTED_MP4_AUDIT_ONLY_NO_REPAIR_OR_RECOVERY','reason':'las secuencias moof/traf/tfhd/tfdt/trun pueden establecer límites y tiempos exactos de unidades AAC para una pista; la presentación fragmentada es sólo evidencia y no concede intervención'})
        a.policy_decisions.append({'code':'MP4_AAC_FRAGMENTED_PCM_PROVENANCE_AUTHORITY','decision':'FRAGMENTED_PRESENTATION_PCM_PROVENANCE_AUDIT_ONLY_NO_REPAIR_OR_RECOVERY','reason':'las unidades AAC fragmentadas autenticadas se decodifican y recortan a la duración exacta declarada; el hash PCM canónico no concede por sí solo autoridad de intervención'})
        a.policy_decisions.append({'code':'MP4_AAC_CANONICAL_TIMELINE_EXPORT_AUTHORITY','decision':'CANONICAL_TIMELINE_LOSSLESS_EXPORT_ONLY_ON_PROVEN_PCM_DIFFERENCE','reason':'una presentación compleja o fragmentada plenamente probada sólo publica un FLAC canónico verificado si el PCM directo difiere en cantidad o hash completo; si coincide no crea archivos, nunca sobrescribe y mantiene identificado el silencio de la línea de tiempo'})
        a.policy_decisions.append({'code':'MP4_AAC_FRAGMENTED_EDIT_PROVENANCE_AUTHORITY','decision':'FRAGMENTED_EDIT_LIST_PRESENTATION_REQUIRES_EXACT_COMBINED_PROVENANCE','reason':'un MP4 fragmentado de una pista sólo aplica ediciones normales o vacías cuando el direccionamiento, la identidad AAC, los tiempos, la conversión temporal y cada rango PCM son exactos; toda ambigüedad permanece bloqueada'})
        a.policy_decisions.append({'code':'MP4_AAC_COMPLETE_CLEAN_ASSESSMENT_AUTHORITY','decision':'UNIQUE_MDAT_COMPLETE_CLEAN_ASSESSMENT_ONLY_NO_PUBLICATION','reason':'una fuente AAC-LC de una sola pista, no reproducible y con sólo un offset erróneo puede clasificarse COMPLETE_CLEAN cuando un mdat queda cubierto exactamente y una vista ADTS temporal preserva bytes y decodifica la ventana canónica exacta; la reparación y la publicación de salidas permanecen bloqueadas'})
        a.policy_decisions.append({'code':'MP4_AAC_COMPLETE_CLEAN_EXPORT_AUTHORITY','decision':'COMPLETE_CLEAN_LOSSLESS_EXPORT','reason':'una fuente no reproducible y elegible puede publicar un FLAC canónico recuperado sin pérdida sólo tras reextraer, decodificar AAC estrictamente, recortar exactamente la presentación y verificar por decodificación inversa la identidad PCM; nunca se sobrescribe y los sidecars verificados se reutilizan'})
        a.policy_decisions.append({'code':'MP4_AAC_CHUNK_OFFSET_REPAIR_AUTHORITY','decision':'SAFE_SINGLE_CHUNK_OFFSET_REPAIR_PRECEDES_PCM','reason':'un único offset stco/co64 demostrable puede reemplazarse en una copia M4A independiente sólo si pasan la diferencia exacta, el reanálisis, el hash de la esencia AAC, la identidad de demux, las decodificaciones, FFprobe y el PCM canónico; la reparación verificada suprime el FLAC de respaldo'})
        mp4_hierarchy=resolve_mp4_aac_preservation_hierarchy(a.repair_execution,a.lossless_export,a.recovery_assessment,a.playability,{i.code for i in a.issues})
        a.format_facts['mp4_aac_preservation_hierarchy']=mp4_hierarchy
        a.policy_decisions.append({'code':'MP4_AAC_PRESERVATION_HIERARCHY','decision':'MP4_AAC_PRESERVATION_HIERARCHY_STRICT_V1','reason':'la reparación M4A verificada que preserva bytes precede a la recuperación FLAC completa, y ésta precede al reporte; familias desconocidas o publicaciones competidoras fallan de forma cerrada'})
        a.policy_decisions.append({'code':'MP4_AAC_MEDIA_DURATION_REPAIR_AUTHORITY','decision':'SAFE_MDHD_DURATION_REPAIR','reason':'una duración mdhd sólo se reemplaza por la suma stts completa cuando es el único problema y mapeo, tiempos, demux, esencia AAC, decodificación, FFprobe y PCM canónico verifican exactamente'})
        a.policy_decisions.append({'code':'MP4_AAC_SAMPLE_COUNT_REPAIR_AUTHORITY','decision':'SAFE_STSZ_SAMPLE_COUNT_REPAIR','reason':'una entrada stsz omitida sólo puede restaurarse cuando existe físicamente y la cantidad de muestras stsz coincide de forma independiente con stts, la capacidad stsc y la cobertura exacta del mdat único; la copia reparada debe preservar todos los bytes AAC y reanalizarse y decodificarse exactamente'})
        a.policy_decisions.append({'code':'MP4_AAC_SAMPLE_DESCRIPTION_REPAIR_AUTHORITY','decision':'SAFE_STSC_DESCRIPTION_REFERENCE_REPAIR','reason':'una referencia stsc inválida sólo se reemplaza cuando existe exactamente una descripción AAC autenticada y tamaños, geometría, extensiones, tiempos y hashes son completos; la copia independiente debe superar reanálisis, demux y decodificación'})
        a.policy_decisions.append({'code':'MP4_AAC_PARTIAL_RECOVERY_GATE_AUTHORITY','decision':'PARTIAL_RECOVERY_REQUIRES_STRUCTURALLY_PROVEN_CHUNK_ORIGIN','reason':'la aceptación del decodificador no prueba por sí sola dónde comienza AAC dentro de mdat; los bytes extra o un desborde terminal permanecen sólo en el reporte cuando el offset dañado permite más de un origen estructural'})
        if mp4_hierarchy.get('policy_violation'):
            a.error=mp4_hierarchy['policy_violation'];a.run_status='FAILED';a.final_status=['ANALYSIS_FAILED'];return _derive_evidence(a)
        if sha256_file(path,cfg['analysis']['sha256_chunk_size'])!=source_sha:
            a.error='INPUT_CHANGED_DURING_PROCESS';a.run_status='FAILED';a.final_status=['ANALYSIS_FAILED'];return _derive_evidence(a)
    elif a.detected_codec=='aac' and ident.get('aac_adts'):
        q=ident['aac_adts'];a.metadata=q['metadata'];a.format_facts=q['facts'];a.structural_map=q['structural_map'];a.issues.extend(q['issues'])
        af=q['facts'].get('adts') or {};frames=q['facts'].get('frames') or []
        sr=(af.get('sample_rates_hz') or [None])[0] if len(af.get('sample_rates_hz') or [])==1 else None
        cc=(af.get('channel_configurations') or [None])[0] if len(af.get('channel_configurations') or [])==1 else None
        # ADTS channel_configuration es un índice sintáctico, no siempre la cantidad de canales; 0 indica PCE.
        channel_count={1:1,2:2,3:3,4:4,5:5,6:6,7:8}.get(cc)
        a.canonical_pcm_profile=canonical_pcm_profile(ffmpeg,sr,channel_count)
        dem=aac_adts_demux_evidence(path,ffprobe_exe,frames,cfg['app']['external_timeout_seconds']);a.format_facts['adts_demux_evidence']=dem
        presentation_decode=ffmpeg_evidence_decode(path,ffmpeg,channel_count,cfg['app']['external_timeout_seconds'])
        timeline=aac_adts_timeline.assess(q,dem,presentation_decode,strict);a.format_facts['adts_timeline_evidence']=timeline
        a.canonical_presentation_window={'determined':timeline.get('presentation_exact',False),'sample_rate':sr,'channels':channel_count,
            'header_sample_count_total':af.get('header_sample_count_total'),'header_duration_seconds':af.get('header_duration_seconds'),
            'presentation_sample_count':timeline.get('presentation_sample_count'),'canonical_pcm_s32le_sha256':timeline.get('canonical_pcm_s32le_sha256'),
            'presentation_model':timeline.get('presentation_model','ADTS_FRAME_SAMPLE_COUNT_NOT_PRESENTATION_WINDOW')}
        codes={i.code for i in a.issues}
        container_codes={'AAC_ADTS_SYNC_LOSS','AAC_ADTS_TRAILING_BYTES','AAC_ADTS_TRUNCATED_FRAME','AAC_ADTS_HEADER_TRUNCATED'}
        header_codes={'AAC_ADTS_LAYER_NONZERO','AAC_ADTS_SAMPLING_INDEX_INVALID','AAC_ADTS_FRAME_LENGTH_INVALID','AAC_ADTS_PARAMETER_CHANGE','AAC_ADTS_PROTECTION_MODE_CHANGE','AAC_ADTS_CRC_SYNTAX_TRUNCATED','AAC_ADTS_HEADER_CRC_MISMATCH','AAC_ADTS_RAW_DATA_BLOCK_POSITION_INVALID'}
        a.validity_domains={'DECODE_VALIDITY':'VALID' if strict['passed'] else ('USABLE_WITH_ERRORS' if play['completed'] else 'INVALID'),
            'CONTAINER_VALIDITY':'VALIDATED_CONTIGUOUS_ADTS_FRAMES' if frames and not (codes&container_codes) else 'NONCONFORMANT_OR_DAMAGED_ADTS_FRAMING',
            'CODEC_HEADER_VALIDITY':'VALIDATED_ADTS_HEADER_PARAMETERS' if frames and not (codes&header_codes) else 'NONCONFORMANT_OR_DAMAGED_ADTS_HEADERS',
            'DEMUX_BOUNDARY_VALIDITY':'VALIDATED_DIRECT_FRAME_TO_FFMPEG_PACKET_BOUNDARIES' if dem.get('all_equal') else ('UNAVAILABLE' if not dem.get('packet_probe_passed') else 'NONCONFORMANT_OR_INCOMPLETE'),
            'TIMELINE_VALIDITY':'VALIDATED_EXACT_CONTIGUOUS_ADTS_PCM_PRESENTATION' if timeline.get('presentation_exact') else 'ADTS_FRAME_SAMPLE_COUNTS_ONLY_NOT_PRESENTATION_EXACT',
            'SEEKABILITY_VALIDITY':'FRAME_BOUNDARIES_OBSERVED_NOT_DEEP_VALIDATED'}
        recovery=aac_adts_recovery.assess(path,q,ffmpeg,ffprobe_exe,cfg['app']['external_timeout_seconds']);a.format_facts['adts_recovery_assessment']=recovery
        a.pcm_recovery_class=recovery.get('pcm_class') if recovery.get('eligible') else 'AAC_ADTS_AUDIT_ONLY'
        a.recovery_assessment=recovery if recovery.get('eligible') else {'policy':'AAC_ADTS_FRAME_CRC_AUTHORITY','eligible':False,'repair_authority':'NONE','pcm_recovery_authority':'NONE','publication_enabled':False,'reason':'la política vigente agrega procedencia de frames y evidencia conservadora de CRC/protección; sólo se autentican matemáticamente los alcances CRC de header con varios RDB, mientras los alcances de RDB único y por bloque quedan diferidos hasta analizar la sintaxis raw_data_block de AAC'}
        if cfg['repair']['enabled'] and cfg['app']['mode']!='audit_only':
            repaired=aac_adts_repair.execute(path,source_sha,q,ffmpeg,ffprobe_exe,cfg['repair']['publish_verified'],cfg['app']['external_timeout_seconds'])
            a.repair_plan.extend(repaired['plans']);a.repair_execution.extend(repaired['executions'])
        adts_repaired=any(x.get('repair_spec_id')==aac_adts_repair.SPEC_ID and x.get('status') in ('CREATED','REUSED') for x in a.repair_execution)
        if cfg['lossless_recovery']['enabled'] and cfg['app']['mode']!='audit_only' and recovery.get('eligible') and not adts_repaired:
            exported=aac_adts_recovery.export(path,source_sha,q,ffmpeg,ffprobe_exe,recovery,cfg['lossless_recovery']['publish_verified'],cfg['app']['external_timeout_seconds'])
            if exported.get('status') not in ('NOT_ELIGIBLE','POLICY_BLOCKED'):a.lossless_export.append(exported)
        elif adts_repaired and recovery.get('eligible'):
            a.policy_decisions.append({'code':'AAC_ADTS_REPAIR_PRECEDES_PCM','decision':'LOSSLESS_PCM_EXPORT_SUPPRESSED','reason':'la reparación ADTS independiente y verificada restaura el stream AAC original sin recodificar, por lo que no se produce un FLAC alternativo'})
        a.policy_decisions.append({'code':'AAC_ADTS_AUDIT_AUTHORITY','decision':'AUDIT_ONLY_NO_REPAIR_OR_RECOVERY','reason':'se validan el framing ADTS, la geometría de headers, la continuidad de parámetros y los límites de paquetes de FFmpeg; los CRC sólo se observan en esta capa y las cantidades por frame no se promueven a una ventana de presentación exacta'})
        a.policy_decisions.append({'code':'AAC_ADTS_FRAME_CRC_AUTHORITY','decision':'FRAME_PROVENANCE_AND_CRC_AUDIT_ONLY_NO_REPAIR_OR_RECOVERY','reason':'se autentican hashes de frame, header y payload; sólo se validan matemáticamente los alcances CRC de header con múltiples raw_data_block, mientras los demás quedan como evidencia hasta analizar la sintaxis AAC necesaria'})
        a.policy_decisions.append({'code':'AAC_ADTS_PCM_PRESENTATION_AUTHORITY','decision':'EXACT_CONTIGUOUS_PCM_PRESENTATION_AUDIT_ONLY','reason':'un stream AAC-LC ADTS homogéneo y contiguo sólo establece cantidad y hash PCM exactos si los límites directos coinciden con demux y la decodificación estricta completa coincide; no se concede intervención'})
        a.policy_decisions.append({'code':'AAC_ADTS_SAFE_HEADER_REPAIR_AUTHORITY','decision':'SAFE_UNIQUE_SAMPLING_INDEX_REPAIR','reason':'un índice reservado de frecuencia sólo se reemplaza en una copia cuando todos los demás frames AAC-LC completos prueban un único valor, el límite es exacto, sólo cambian los bits declarados y el stream reparado se reanaliza y decodifica exactamente'})
        a.policy_decisions.append({'code':'AAC_ADTS_COMPLETE_CLEAN_RECOVERY_AUTHORITY','decision':'COMPLETE_CLEAN_FLAC_FALLBACK_AFTER_PROVEN_HEADER_REPAIR','reason':'si una reparación exacta de header prueba una presentación PCM completa pero la reparación ADTS está deshabilitada, puede preservarse en un FLAC verificado por decodificación inversa; la copia ADTS reparada siempre tiene prioridad y nunca se sobrescribe'})
        adts_hierarchy=resolve_aac_adts_preservation_hierarchy(a.repair_execution,a.lossless_export,a.recovery_assessment,a.playability,{i.code for i in a.issues})
        a.format_facts['aac_adts_preservation_hierarchy']=adts_hierarchy
        a.policy_decisions.append({'code':'AAC_ADTS_PRESERVATION_HIERARCHY','decision':'AAC_ADTS_PRESERVATION_HIERARCHY_STRICT_V1','reason':'la reparación ADTS verificada que preserva bytes precede a la recuperación FLAC completa y ésta precede al reporte; familias desconocidas, no verificadas o competidoras fallan de forma cerrada'})
        if adts_hierarchy.get('policy_violation'):
            a.error=adts_hierarchy['policy_violation'];a.run_status='FAILED';a.final_status=['ANALYSIS_FAILED'];return _derive_evidence(a)
        if sha256_file(path,cfg['analysis']['sha256_chunk_size'])!=source_sha:
            a.error='INPUT_CHANGED_DURING_PROCESS';a.run_status='FAILED';a.final_status=['ANALYSIS_FAILED'];return _derive_evidence(a)
    elif a.detected_codec=='wma' and ident.get('asf_wma'):
        q=ident['asf_wma'];a.metadata=q['metadata'];a.format_facts=q['facts'];a.structural_map=q['structural_map'];a.issues.extend(q['issues'])
        streams=q['facts'].get('streams') or [];audio=next((x for x in streams if x.get('is_audio')),{});wf=audio.get('waveformatex') or {}
        sr=wf.get('sample_rate');channels=wf.get('channels');a.canonical_pcm_profile=canonical_pcm_profile(ffmpeg,sr,channels)
        fp=q['facts'].get('asf',{}).get('file_properties') or {};play_ms=None
        if fp.get('play_duration_100ns') is not None and fp.get('preroll_ms') is not None:
            play_ms=max(0,fp.get('play_duration_100ns')/10000-fp.get('preroll_ms'))
        a.canonical_presentation_window={'determined':False,'sample_rate':sr,'channels':channels,'duration_ms_header_minus_preroll':play_ms,'presentation_model':'ASF_FILE_PROPERTIES_DURATION_NOT_SAMPLE_EXACT'}
        codes={i.code for i in a.issues}
        container_codes={'ASF_HEADER_OBJECT_INVALID','ASF_HEADER_SIZE_INVALID','ASF_HEADER_RESERVED_BYTES_INVALID','ASF_HEADER_SUBOBJECT_COUNT_MISMATCH','ASF_HEADER_SUBOBJECT_SIZE_INVALID','ASF_HEADER_BOUNDARY_MISMATCH','ASF_FILE_PROPERTIES_OBJECT_COUNT_INVALID','ASF_FILE_PROPERTIES_OBJECT_TRUNCATED','ASF_FILE_SIZE_MISMATCH','ASF_PACKET_SIZE_FIELDS_INVALID','ASF_STREAM_PROPERTIES_MISSING','ASF_STREAM_PROPERTIES_LENGTH_INVALID','ASF_DATA_OBJECT_MISSING_OR_MISPLACED','ASF_DATA_OBJECT_TRUNCATED','ASF_DATA_FILE_ID_MISMATCH','ASF_DATA_PACKET_COUNT_FIELDS_DISAGREE','ASF_DATA_PACKET_COUNT_MISMATCH','ASF_PARTIAL_DATA_PACKET_AT_END','ASF_DATA_PACKET_HEADER_INVALID'}
        header_codes={'ASF_WMA_WAVEFORMATEX_INVALID','ASF_AUDIO_STREAM_MISSING','ASF_ENCRYPTED_AUDIO_UNSUPPORTED'}
        timeline_codes={'ASF_PACKET_SEND_TIME_NONMONOTONIC','ASF_DATA_PACKET_COUNT_MISMATCH','ASF_PARTIAL_DATA_PACKET_AT_END','ASF_DATA_PACKET_HEADER_INVALID','ASF_DATA_OBJECT_TRUNCATED'}
        packet_facts=q['facts'].get('packets') or {};media_facts=q['facts'].get('media_objects') or {}
        media_valid=(media_facts.get('incomplete_media_objects')==0 and media_facts.get('compressed_payloads_unmodeled')==0)
        dd=asf_wma_demux_decoder_evidence(path,ffprobe_exe,ffmpeg,media_facts,fp.get('preroll_ms'),sr,channels,cfg['app']['external_timeout_seconds'])
        a.format_facts['demux_decoder_evidence']=dd
        conv=asf_wma_decoder_convergence_evidence(path,ffmpeg,dd,channels,sr,cfg['app']['external_timeout_seconds'])
        a.format_facts['decoder_convergence_evidence']=conv
        if media_valid and dd.get('packet_probe_passed'):
            if not dd.get('one_to_one_complete_media_object_mapping'):
                a.issues.append(Issue('ASF_WMA_DEMUX_MEDIA_OBJECT_MAPPING_MISMATCH','decoder_evidence','Los objetos multimedia ASF/WMA ordinarios y completos no se asignan uno a uno por hash de bytes y tamaño a los paquetes comprimidos entregados por el demuxer canónico de FFmpeg.',integrity='SUSPICIOUS',playability='POSSIBLY_AFFECTED',repairability='NONE',evidence=[{'complete_media_objects':dd.get('complete_media_object_count'),'demux_packets':dd.get('demux_packet_count'),'mapped_rows':dd.get('mapped_row_count'),'all_hashes_equal':dd.get('all_packet_hashes_equal'),'all_sizes_equal':dd.get('all_packet_sizes_equal')}]))
            elif not dd.get('all_pts_match_media_object_presentation_minus_preroll'):
                a.issues.append(Issue('ASF_WMA_DEMUX_PTS_PREROLL_MISMATCH','decoder_evidence','El PTS del paquete demux de FFmpeg no coincide con el tiempo de presentación del objeto multimedia ASF menos el preroll de File Properties para uno o más objetos asignados idénticos en bytes.',integrity='NONCONFORMANT',playability='POSSIBLY_AFFECTED',repairability='NONE'))
            if dd.get('demux_timeline_discontinuities'):
                a.issues.append(Issue('ASF_WMA_DEMUX_TIMELINE_DISCONTINUITY','decoder_evidence','La línea de tiempo canónica del demux ASF/WMA contiene un salto de PTS entre paquetes que excede la duración del paquete anterior más un milisegundo de tolerancia por cuantización temporal ASF.',integrity='DAMAGED',playability='POSSIBLY_AFFECTED',repairability='NONE',evidence=dd.get('demux_timeline_discontinuities')[:32]))
        codes={i.code for i in a.issues}
        dd_bad=bool(codes&{'ASF_WMA_DEMUX_MEDIA_OBJECT_MAPPING_MISMATCH','ASF_WMA_DEMUX_PTS_PREROLL_MISMATCH','ASF_WMA_DEMUX_TIMELINE_DISCONTINUITY'})
        if not media_valid or dd_bad:
            dd_validity='NONCONFORMANT_OR_INCOMPLETE_DEMUX_DECODER_EVIDENCE'
        elif not wf.get('valid') or not dd.get('frame_probe_passed'):
            dd_validity='DEMUX_MAPPING_VALID_DECODER_EVIDENCE_UNAVAILABLE'
        elif dd.get('one_to_one_complete_media_object_mapping') and dd.get('all_pts_match_media_object_presentation_minus_preroll') and dd.get('ffprobe_frame_sample_count_matches_raw_decode'):
            dd_validity='VALIDATED_DEMUX_MAPPING_AND_DECODER_OBSERVATION'
        else:
            dd_validity='PARTIAL_DEMUX_DECODER_EVIDENCE'
        conv_elig=conv.get('eligibility')
        if conv_elig=='VALIDATED_EVIDENCE_ONLY_NO_PUBLICATION': conv_validity='VALIDATED_DETERMINISTIC_POST_GAP_CONVERGENCE_EVIDENCE_ONLY'
        elif conv_elig=='NOT_REQUIRED_OR_NO_PROVEN_MISSING_MEDIA_OBJECT_RUN': conv_validity='NOT_REQUIRED_NO_PROVEN_MISSING_MEDIA_OBJECT_RUN'
        elif conv_elig=='BLOCKED_DEMUX_OR_MEDIA_OBJECT_MAPPING_NOT_PROVEN': conv_validity='BLOCKED_INCOMPLETE_OR_UNPROVEN_MEDIA_OBJECT_EVIDENCE'
        elif conv_elig in ('BLOCKED_NATIVE_PCM_PROFILE_UNKNOWN','BLOCKED_FULL_CANONICAL_DECODE_FAILED'): conv_validity='BLOCKED_DECODER_OR_PCM_PROFILE_EVIDENCE'
        else: conv_validity='PARTIAL_OR_UNPROVEN_DECODER_CONVERGENCE'
        a.validity_domains={'DECODE_VALIDITY':'VALID' if strict['passed'] else ('USABLE_WITH_ERRORS' if play['completed'] else 'INVALID'),'CONTAINER_VALIDITY':'VALID' if not (codes&container_codes) else 'NONCONFORMANT_OR_DAMAGED','CODEC_HEADER_VALIDITY':'VALID' if not (codes&header_codes) else 'NONCONFORMANT_OR_DAMAGED','PACKET_VALIDITY':'VALIDATED_FIXED_PACKET_STRUCTURE' if packet_facts.get('all_valid') and not (codes&timeline_codes) else 'NONCONFORMANT_OR_INCOMPLETE','MEDIA_OBJECT_VALIDITY':'VALIDATED_ORDINARY_MEDIA_OBJECT_REASSEMBLY' if media_valid else 'NONCONFORMANT_OR_INCOMPLETE_MEDIA_OBJECTS','TIMELINE_VALIDITY':'VALIDATED_PACKET_SEND_TIMES' if packet_facts.get('all_valid') and not (codes&timeline_codes) else 'NONCONFORMANT_OR_INCOMPLETE_PACKET_TIMELINE','DEMUX_DECODER_TIMELINE_VALIDITY':dd_validity,'DECODER_CONVERGENCE_VALIDITY':conv_validity,'SEEKABILITY_VALIDITY':'DECLARED_SEEKABLE_FIXED_PACKETS' if fp.get('seekable') and q['facts'].get('asf',{}).get('fixed_packet_size') else 'NOT_DEEP_VALIDATED'}
        a.policy_decisions.append({'code':'WMA_ASF_DECODER_CONVERGENCE_AUTHORITY','decision':'DECODER_CONVERGENCE_EVIDENCE_LAYER_RETAINED','reason':'la evidencia de convergencia sigue siendo obligatoria: se consume un objeto de contexto superviviente antes del primer límite candidato y la equivalencia con referencia sana queda limitada a tests'})
        issue_set={i.code for i in a.issues}
        wra=wma_recovery.assess(path,dd,conv,a.metadata,issue_set,a.playability)
        mra=wma_multi_region_recovery.assess(path,dd,conv,a.metadata,issue_set,a.playability)
        a.format_facts['wma_recovery_assessment']={k:v for k,v in wra.items() if not k.startswith('_')}
        a.format_facts['wma_multi_region_recovery_assessment']={k:v for k,v in mra.items() if not k.startswith('_')}
        multi_scope=(conv.get('candidate_count') or 0)>=2
        chosen=mra if multi_scope else wra
        a.pcm_recovery_class=chosen.get('pcm_class','WMA_RECOVERY_BLOCKED');a.recovery_assessment={k:v for k,v in chosen.items() if not k.startswith('_')}
        if cfg['lossless_recovery']['enabled'] and cfg['app']['mode']!='audit_only':
            if multi_scope and mra.get('eligible'):
                le=wma_multi_region_recovery.export(path,source_sha,ffmpeg,mra,cfg['lossless_recovery']['publish_verified'],cfg['app']['external_timeout_seconds'])
                if le.get('status') not in ('NOT_ELIGIBLE','POLICY_BLOCKED'):a.lossless_export.append(le)
            elif (not multi_scope) and wra.get('eligible'):
                le=wma_recovery.export(path,source_sha,ffmpeg,wra,cfg['lossless_recovery']['publish_verified'],cfg['app']['external_timeout_seconds'])
                if le.get('status') not in ('NOT_ELIGIBLE','POLICY_BLOCKED'):a.lossless_export.append(le)
        a.policy_decisions.append({'code':'WMA_ASF_CONVERGED_SUFFIX_RECOVERY_AUTHORITY','decision':'PROVEN_CONVERGED_SUFFIX_LOSSLESS_RECOVERY','reason':'para WMA1/WMA2 con una sola brecha se excluye un objeto de contexto superviviente y sólo se publica el sufijo cuya convergencia fue probada independientemente'})
        a.policy_decisions.append({'code':'WMA_ASF_MULTI_REGION_RECOVERY_AUTHORITY','decision':'PROVEN_MULTI_REGION_LOSSLESS_RECOVERY','reason':'con dos o más secuencias faltantes probadas se publican regiones limpias separadas y autenticadas; se excluye contexto después de cada brecha, no se concatenan regiones, no se sintetiza PCM y no se afirma una línea completa'})
        wma_hierarchy=resolve_wma_preservation_hierarchy(a.lossless_export,a.recovery_assessment,a.playability,{i.code for i in a.issues})
        a.format_facts['wma_preservation_hierarchy']=wma_hierarchy
        a.policy_decisions.append({'code':'WMA_ASF_PRESERVATION_HIERARCHY','decision':'WMA_PRESERVATION_HIERARCHY_STRICT_V1','reason':'la jerarquía es observacional: la recuperación multirregión verificada precede a la recuperación de sufijo convergente de una brecha y luego al reporte; familias publicadas competidoras fallan de forma cerrada'})
        if wma_hierarchy.get('policy_violation'):
            a.error=wma_hierarchy['policy_violation'];a.run_status='FAILED';a.final_status=['ANALYSIS_FAILED'];return _derive_evidence(a)
        if sha256_file(path,cfg['analysis']['sha256_chunk_size'])!=source_sha:
            a.error='INPUT_CHANGED_DURING_PROCESS';a.run_status='FAILED';a.final_status=['ANALYSIS_FAILED'];return _derive_evidence(a)
    elif a.detected_codec in ('mp3','mp2'):
        m=ident['mpeg'];a.metadata=m['metadata'];a.format_facts=m['facts'];a.structural_map=m['structural_map'];a.issues.extend(m['issues']);cpw=m['facts']['canonical_presentation_window'];a.canonical_presentation_window=cpw;a.canonical_pcm_profile=canonical_pcm_profile(ffmpeg,m['facts']['sample_rate'],m['facts']['channels'])
        de=independent_decoder_evidence(path,ffmpeg,mpg123_exe,m['facts']['channels'],cfg['app']['external_timeout_seconds'],mpg123_trust);a.format_facts['decoder_evidence']=de;a.decode_results['MPG123_EVIDENCE']=de['mpg123']
        matrix,matrix_issues=build_mpeg_evidence_matrix(m,de,strict,play);a.format_facts['evidence_consistency']=matrix;a.issues.extend(matrix_issues)
        if mpg123_exe and de['agreement'].get('completion_equal') is False and not (m['facts'].get('gaps') or m['facts'].get('truncated_final_frame')):
            a.issues.append(Issue('DECODER_COMPLETION_DISAGREEMENT','decoder_evidence','FFmpeg y mpg123 discrepan sobre si este stream MPEG estructuralmente contiguo completa la decodificación.',integrity='SUSPICIOUS',compatibility='POSSIBLE',playability='POSSIBLY_AFFECTED',repairability='NONE',evidence=[{'ffmpeg_completed':de['ffmpeg'].get('completed'),'mpg123_completed':de['mpg123'].get('completed'),'mpg123_version':de['mpg123'].get('decoder_version')}]))
        if any(i.code in ('MPEG_SYNC_LOSS','TRUNCATED_MPEG_FRAME','BIT_RESERVOIR_BACKPOINTER_IMPOSSIBLE') for i in a.issues):
            salv=decode(path,ffmpeg,'SALVAGE_DECODE',cfg['app']['external_timeout_seconds']);a.decode_results['SALVAGE_DECODE']=salv
        ra=lossless_export.assess(m,a.playability)
        if not [i for i in a.issues if i.code!='EXTENSION_CONTENT_MISMATCH'] and a.playability=='PLAYABLE':
            a.pcm_recovery_class='NOT_REQUIRED';a.recovery_assessment={'pcm_class':'NOT_REQUIRED','reason':'no se detectó ninguna anomalía relevante para recuperación'}
        else:
            a.pcm_recovery_class=ra['pcm_class'];a.recovery_assessment=ra
        xing_mismatch=next((i for i in a.issues if i.code=='XING_FRAME_COUNT_MISMATCH'),None);gaps=m['facts'].get('gaps') or []
        explained_missing=sum((g.get('missing_frame_count') or 0) for g in gaps if g.get('timeline_known'));xing_delta=(xing_mismatch.evidence[0].get('delta') if xing_mismatch and xing_mismatch.evidence else None)
        hard_parameter_transitions=((m['facts'].get('parameter_segments') or {}).get('hard_profile_transition_count') or 0)>0
        if hard_parameter_transitions:timeline_validity='SEGMENTED_PARAMETERS'
        elif cpw.get('determined') and not xing_mismatch:timeline_validity='VALID'
        elif cpw.get('determined') and gaps and all(g.get('timeline_known') for g in gaps) and xing_delta==explained_missing:timeline_validity='RECOVERABLE_EXACT'
        else:timeline_validity='USABLE_NOT_EXACT'
        a.validity_domains={'DECODE_VALIDITY':'VALID' if strict['passed'] else ('USABLE_WITH_ERRORS' if play['completed'] else 'INVALID'),'TIMELINE_VALIDITY':timeline_validity,'SEEKABILITY_VALIDITY':'NONCONFORMANT_METADATA' if any(i.layer=='seek_metadata' for i in a.issues) else ('VALIDATED_METADATA' if m['facts'].get('vbr_header') else 'NO_DEDICATED_METADATA')}
        if cfg['repair']['enabled'] and cfg['app']['mode']!='audit_only':
            rr=repairs.execute_mpeg(path,source_sha,m,ffmpeg,ffprobe_exe,cfg['repair']['publish_verified'],cfg['app']['max_resync_scan_bytes'])
            a.repair_plan.extend(rr['plans']);a.repair_execution.extend(rr['executions'])
        preservational_bitstream_repair=_verified_bitstream_repair(a.repair_execution)
        # La derivación lossless de preservación es posterior a una reparación verificada del bitstream.
        if cfg['lossless_recovery']['enabled'] and cfg['app']['mode']!='audit_only':
            should_export=((a.pcm_recovery_class in ('PARTIAL_CLEAN','COMPLETE_CLEAN') and a.playability=='UNPLAYABLE') or (a.pcm_recovery_class=='HETEROGENEOUS_STREAM' and bool(ra.get('eligible_segmented') or ra.get('eligible_segmented_partial') or ra.get('eligible_segmented_open_partial')))) and not preservational_bitstream_repair
            if preservational_bitstream_repair and a.playability=='UNPLAYABLE' and a.pcm_recovery_class in ('PARTIAL_CLEAN','COMPLETE_CLEAN'):
                a.policy_decisions.append({'code':'BITSTREAM_REPAIR_PRECEDES_PCM_DERIVATION','decision':'LOSSLESS_PCM_EXPORT_SUPPRESSED','reason':'una reparación verificada sin pérdida del bitstream resolvió el hallazgo bloqueante y superó la validación de nuevo análisis y decodificación posterior'})
            if should_export:
                le=lossless_export.export(path,source_sha,m,ffmpeg,a.playability,cfg['lossless_recovery']['publish_verified'])
                if le['status'] not in ('NOT_ELIGIBLE','POLICY_BLOCKED'):a.lossless_export.append(le)
        hierarchy=resolve_preservation_hierarchy(a.repair_execution,a.lossless_export,a.recovery_assessment,a.playability)
        a.format_facts['preservation_hierarchy']=hierarchy
        if hierarchy.get('policy_violation'):
            a.error=hierarchy['policy_violation'];a.run_status='FAILED';a.final_status=['ANALYSIS_FAILED'];return _derive_evidence(a)
        if sha256_file(path,cfg['analysis']['sha256_chunk_size'])!=source_sha:
            a.error='INPUT_CHANGED_DURING_PROCESS';a.run_status='FAILED';a.final_status=['ANALYSIS_FAILED'];return _derive_evidence(a)
    else:
        aud=probe.get('audio_streams') or []
        if aud:
            s=aud[0];a.canonical_pcm_profile=canonical_pcm_profile(ffmpeg,int(s.get('sample_rate') or 0) or None,s.get('channels'))
        a.format_facts={'ffprobe':probe};a.canonical_presentation_window={'determined':False};a.pcm_recovery_class='NOT_ASSESSED';a.validity_domains={'DECODE_VALIDITY':'VALID' if strict['passed'] else 'INVALID','TIMELINE_VALIDITY':'NOT_YET_DEEP_AUDITED','SEEKABILITY_VALIDITY':'NOT_YET_DEEP_AUDITED'}
    _derive_evidence(a);classify(a);a.events.append(event('analysis_finished',a.run_status));return a
