from __future__ import annotations
from pathlib import Path
from app.version import APP_VERSION


def _decode_status(q):return 'PASS' if q.get('passed') else ('PASS_WITH_ERRORS' if q.get('completed') else 'FAIL')


def write_md(path:Path,run:dict):
    s=run['summary'];L=[f'# LossyDoctor {APP_VERSION} — Reporte de auditoría y recuperación','',f"- ID de ejecución: `{run['run_id']}`",f"- Inicio (hora local del sistema): `{run.get('started_at')}`",'- Base temporal: `SYSTEM_LOCAL`',f"- Archivos regulares encontrados: **{s['discovered']}**",f"- Audios soportados procesados: **{s['processed']}**",f"- Técnicamente correctos: **{s['ok']}**",f"- Con observaciones o hallazgos: **{s['with_findings']}**",f"- Ignorados u omitidos: **{s['skipped']}**",f"- Fallidos: **{s['failed']}**",f"- Copias reparadas verificadas creadas: **{s['repaired_outputs_created']}**",f"- Recuperaciones sin pérdida verificadas creadas: **{s['lossless_outputs_created']}**",f"- Derivaciones verificadas reutilizadas: **{s['outputs_reused']}**",f"- Candidatos rechazados por verificación: **{s['candidates_rejected']}**",'', '> Los originales nunca se modifican. Las salidas parciales distinguen PCM genuino de marcadores explícitos de silencio en la línea de tiempo.','', '## Archivos de audio soportados','']
    for f in run['files']:
        if f['run_status']=='SKIPPED_UNSUPPORTED':continue
        L += [f"### {f['display_name']}",'',f"**Estado de ejecución:** `{f['run_status']}`",'',f"- Detectado: `{f.get('detected_container')}` / `{f.get('detected_codec')}`",f"- Playability: `{f.get('playability')}`",f"- Evaluación de recuperación PCM: `{f.get('pcm_recovery_class')}`"]
        dr=f.get('decode_results',{})
        for k in ('STRICT_DECODE','PLAYBACK_DECODE','SALVAGE_DECODE','MPG123_EVIDENCE'):
            if k in dr:L.append(f"- {k}: `{_decode_status(dr[k])}`")
        vd=f.get('validity_domains') or {}
        if vd:L+=['','**Dominios de validez**','']+[f"- {k}: `{v}`" for k,v in vd.items()]
        ff=f.get('format_facts') or {};mp4=ff.get('mp4') or {};aac=ff.get('aac') or {};mp4_tracks=ff.get('tracks') or []
        if mp4 and f.get('detected_container')=='MP4':
            brand=mp4.get('ftyp') or {}
            L+=['','**Auditoría estructural MP4/M4A + AAC**','',
                f"- Tipo de archivo: marca principal `{brand.get('major_brand')}` · marcas compatibles `{brand.get('compatible_brands')}`",
                f"- Cajas de nivel superior: `{', '.join(x.get('type','?') for x in (mp4.get('top_level_boxes') or []))}` · moov `{mp4.get('moov_count')}` · mdat `{mp4.get('mdat_count')}`",
                f"- Pistas de audio / pistas AAC autenticadas: `{aac.get('audio_track_count')}` / `{aac.get('aac_track_count')}`",
                f"- Tipos de objeto AAC: `{aac.get('audio_object_types')}` · perfiles `{aac.get('profiles')}` · frecuencias de muestreo `{aac.get('sample_rates_hz')}` Hz · configuraciones de canales `{aac.get('channel_configurations')}`"]
            for track in mp4_tracks:
                if track.get('handler_type')!='soun':continue
                tables=track.get('sample_tables') or {};stts=tables.get('stts') or {};stsz=tables.get('stsz') or {};offsets=tables.get('stco') or tables.get('co64') or {};provenance=track.get('access_unit_provenance') or {};presentation=track.get('presentation_window') or {}
                L.append(f"  - Pista `{track.get('track_id')}`: escala temporal `{track.get('media_timescale')}` · duración multimedia `{track.get('media_duration')}` · lista de edición `{track.get('edit_list_present')}` · muestras stts/stsz `{stts.get('sample_count')}` / `{stsz.get('sample_count')}` · fragmentos `{offsets.get('entry_count')}` · desplazamientos dentro de mdat `{offsets.get('all_offsets_inside_mdat')}`")
                L.append(f"    - Unidades de acceso AAC asignadas / autenticadas por SHA-256: `{provenance.get('mapped_sample_count')}` / `{provenance.get('hashed_sample_count')}` · asignación completa `{provenance.get('mapping_complete')}` · línea de tiempo de decodificación completa `{provenance.get('decode_timeline_complete')}` · fin de decodificación `{provenance.get('decode_end_units')}` unidades multimedia")
                L.append(f"    - Presentación canónica determinada `{presentation.get('determined')}` · modelo `{presentation.get('presentation_model')}` · ventana multimedia `{presentation.get('media_start_units')}–{presentation.get('media_end_units')}` · recorte inicial/final `{presentation.get('initial_media_trim_units')}` / `{presentation.get('trailing_media_trim_units')}` · muestras de presentación `{presentation.get('presentation_sample_count')}`")
                if presentation.get('presentation_segment_count',0)>1 or presentation.get('contains_empty_edits'):
                    L.append(f"    - Segmentos de presentación: `{presentation.get('presentation_segment_count')}` · media `{presentation.get('media_segment_count')}` · vacíos/silencio `{presentation.get('empty_segment_count')}` · autoridad de intervención `{presentation.get('intervention_authority')}`")
                    for segment in presentation.get('presentation_segments') or []:
                        source=(f"contenido multimedia fuente `{segment.get('media_start_units')}–{segment.get('media_end_units')}`" if segment.get('kind')=='MEDIA' else 'silencio explícito de línea de tiempo (no es PCM fuente)')
                        L.append(f"      - Edición `{segment.get('index')}` · `{segment.get('kind')}` · muestras de presentación `{segment.get('presentation_sample_start')}–{segment.get('presentation_sample_end')}` · {source}")
            demux=ff.get('mp4_aac_demux_evidence') or {}
            if demux:
                L.append(f"- Unidades de acceso directas / paquetes FFprobe: `{demux.get('direct_access_unit_count')}` / `{demux.get('ffprobe_packet_count')}` · posiciones `{demux.get('positions_equal')}` · tamaños `{demux.get('sizes_equal')}` · SHA-256 `{demux.get('hashes_equal')}` · duraciones `{demux.get('durations_equal')}`")
                L.append(f"- Desplazamiento DTS constante de FFprobe respecto del tiempo de decodificación: `{demux.get('constant_dts_shift_media_units')}` unidades multimedia · interpretación condicionada por el modelo aplicable de edición simple")
            multi_edit=ff.get('mp4_aac_multi_edit_audit') or {}
            if multi_edit:
                L.append(f"- Auditoría de múltiples ediciones: línea de tiempo estructural `{multi_edit.get('structural_timeline_determined')}` · segmentos `{multi_edit.get('presentation_segment_count')}` · coincide la cantidad de muestras decodificadas `{multi_edit.get('decoder_sample_count_matches')}` · procedencia de segmentos `{multi_edit.get('segment_level_provenance_validated')}` · intervención `{multi_edit.get('intervention_authority')}`")
                if multi_edit.get('segment_level_provenance_validated'):
                    L.append(f"  - SHA-256 del PCM canónico ordenado: `{multi_edit.get('canonical_presentation_pcm_s32le_sha256')}` · unidades AAC autenticadas `{multi_edit.get('aac_access_unit_count')}` · decodificación estricta independiente `{multi_edit.get('temporary_strict_decode')}`")
            fragmented=ff.get('mp4_aac_fragmented_audit') or {}
            if fragmented:
                L.append(f"- Auditoría de MP4 fragmentado: fragmentos `{fragmented.get('fragment_count')}` · secuencias `{fragmented.get('fragment_sequence_numbers')}` · track runs `{fragmented.get('fragment_run_count')}` · unidades AAC `{fragmented.get('access_unit_count')}` · mapeo `{fragmented.get('access_unit_mapping_complete')}` · presentación `{fragmented.get('presentation_determined')}` · intervención `{fragmented.get('intervention_authority')}`")
                if fragmented.get('presentation_pcm_provenance_validated'):
                    L.append(f"  - SHA-256 del PCM canónico fragmentado: `{fragmented.get('canonical_presentation_pcm_s32le_sha256')}` · muestras multimedia decodificadas independientemente `{fragmented.get('independent_decoded_media_sample_count')}` · muestras de presentación declaradas `{presentation.get('presentation_sample_count')}` · padding terminal excluido `{fragmented.get('decoded_tail_padding_samples_excluded')}`")
            presentation_decode=ff.get('mp4_aac_presentation_decoder_evidence') or {}
            if presentation_decode:L.append(f"- Muestras de presentación canónica de FFmpeg: `{presentation_decode.get('sample_frames')}` · completado `{presentation_decode.get('completed')}`")
            recovery=ff.get('mp4_aac_recovery_assessment') or {}
            if recovery:
                L.append(f"- Evaluación de preservación: clase `{recovery.get('pcm_class')}` · elegible `{recovery.get('eligible')}` · publicación `{recovery.get('publication_enabled')}` · motivo `{recovery.get('reason')}`")
                if recovery.get('eligible'):
                    L.append(f"  - Unidades de acceso AAC preservadas byte a byte: `{recovery.get('access_unit_count')}` / `{recovery.get('aac_access_unit_bytes')}` bytes · SHA-256 de la esencia `{recovery.get('aac_access_unit_essence_sha256')}`")
                    L.append(f"  - PCM de presentación demostrado: muestras `{recovery.get('presentation_sample_count')}` · SHA-256 s32le `{recovery.get('presentation_pcm_s32le_sha256')}` · transporte temporal `{recovery.get('temporary_transport')}`")
                if recovery.get('partial_candidate_assessed'):
                    L.append(f"  - Puerta de recuperación parcial: origen del fragmento probado estructuralmente `{recovery.get('candidate_origin_structurally_proven')}` · regiones limpias publicadas `{recovery.get('candidate_region_count')}`")
            canonical_export=ff.get('mp4_aac_canonical_timeline_export_assessment') or {}
            if canonical_export:
                L.append(f"- Exportación de línea de tiempo canónica: elegible `{canonical_export.get('eligible')}` · publicación `{canonical_export.get('publication_enabled')}` · clase `{canonical_export.get('pcm_class')}` · motivo `{canonical_export.get('reason')}`")
            mp4_hierarchy=ff.get('mp4_aac_preservation_hierarchy') or {}
            if mp4_hierarchy:
                L+=['','**Jerarquía de resolución de preservación MP4/AAC**','',
                    f"- Política: `{mp4_hierarchy.get('policy')}` · resultado exclusivo: `{mp4_hierarchy.get('exclusive_outcome')}`",
                    f"- Nivel seleccionado: `{mp4_hierarchy.get('selected_tier')}` · rango `{mp4_hierarchy.get('selected_rank')}`",
                    f"- Familias observadas: `{mp4_hierarchy.get('observed_tier_families')}` · niveles inferiores suprimidos: `{mp4_hierarchy.get('suppressed_lower_tiers')}`",
                    f"- Salidas publicadas/reutilizadas en el nivel seleccionado: `{mp4_hierarchy.get('selected_output_count')}` · creadas/reutilizadas: `{(mp4_hierarchy.get('status_counts') or {}).get('CREATED')}` / `{(mp4_hierarchy.get('status_counts') or {}).get('REUSED')}`",
                    f"- Resolución: {mp4_hierarchy.get('selection_reason')}"]
        og=(f.get('format_facts') or {}).get('ogg') or {};oh=(f.get('format_facts') or {}).get('opus_head') or {};ot=(f.get('format_facts') or {}).get('opus_tags') or {}
        if oh.get('present'):
            gp=((f.get('format_facts') or {}).get('output_gain_preservation') or {})
            L+=['','**Auditoría estructural Ogg/Opus**','',
                f"- Páginas Ogg: `{og.get('page_count')}` · streams lógicos: `{og.get('logical_stream_count')}` · serie: `{og.get('serial')}` · todos los CRC válidos: `{og.get('all_page_crc_valid')}`",
                f"- Secuencia de páginas: `{og.get('sequence_start')}–{og.get('sequence_end')}` · BOS/EOS: `{og.get('bos_present')}` / `{og.get('eos_present')}`",
                f"- OpusHead: versión `{oh.get('version')}` · canales `{oh.get('channels')}` · familia de mapeo `{oh.get('mapping_family')}` · válido `{oh.get('valid')}`",
                f"- Pre-skip: `{oh.get('pre_skip')}` muestras @48k · metadata de frecuencia de entrada: `{oh.get('input_sample_rate')}` Hz",
                f"- Ganancia de salida: `{oh.get('output_gain_q7_8')}` Q7.8 (`{oh.get('output_gain_db')}` dB) · política de preservación `{gp.get('policy') or 'PRESERVE_UNAPPLIED_Q7_8_IN_MANIFEST'}` · incorporada al PCM `{gp.get('baked_into_pcm',False)}`",
                f"- OpusTags: válido `{ot.get('valid')}` · proveedor `{ot.get('vendor')}` · comentarios `{ot.get('comment_count')}`",
                f"- Paquetes de audio: `{(f.get('format_facts') or {}).get('audio_packet_count')}` · gránulo final: `{(f.get('format_facts') or {}).get('final_granule_position')}` · posición de muestra PCM: `{(f.get('format_facts') or {}).get('pcm_sample_position')}` · segundos de reproducción: `{(f.get('format_facts') or {}).get('playback_seconds')}`"]
        ad=(f.get('format_facts') or {}).get('adts') or {}
        if ad.get('present'):
            de=(f.get('format_facts') or {}).get('adts_demux_evidence') or {}
            L+=['','**Auditoría estructural AAC/ADTS**','',
                f"- Frames ADTS completos: `{ad.get('complete_frame_count')}` · headers no válidos `{ad.get('invalid_header_count')}` · brechas de sincronización `{ad.get('sync_gap_count')}` · frame final truncado `{ad.get('truncated_final_frame')}`",
                f"- IDs MPEG / tipos de objeto: `{sorted(set((x.get('mpeg_version') for x in ((f.get('format_facts') or {}).get('frames') or []))))}` / `{ad.get('object_types')}` · perfiles `{ad.get('profile_names')}`",
                f"- Frecuencias de muestreo: `{ad.get('sample_rates_hz')}` Hz · configuraciones de canales `{ad.get('channel_configurations')}` · cantidades de bloques de datos crudos `{ad.get('raw_data_blocks_values')}`",
                f"- Muestras derivadas del header: `{ad.get('header_sample_count_total')}` · duración `{ad.get('header_duration_seconds')}` s · modelo de presentación `ADTS_FRAME_SAMPLE_COUNT_NOT_PRESENTATION_WINDOW`",
                f"- Campos CRC presentes/ausentes: `{ad.get('crc_present_frame_count')}` / `{ad.get('crc_absent_frame_count')}` · validación CRC `{ad.get('crc_validation')}`",
                f"- Frames ADTS directos / paquetes demux FFmpeg: `{de.get('direct_complete_frame_count')}` / `{de.get('ffprobe_packet_count')}` · cantidades iguales `{de.get('frame_count_equal')}` · posiciones iguales `{de.get('positions_equal')}` · tamaños iguales `{de.get('sizes_equal')}`",
                '- Autoridad de auditoría ADTS: reparación `NONE` · recuperación PCM `NONE` para evidencia estructural y de límites de demux.',
                '- Autoridad de procedencia y CRC ADTS: reparación `NONE` · recuperación PCM `NONE` fuera de intervenciones explícitamente probadas.',
                '', '**Auditoría de procedencia de cuadros AAC/ADTS y protección CRC**','',
                f"- Política: `AAC_ADTS_FRAME_PROVENANCE` · filas SHA-256 de frame/header/payload `{ad.get('frame_sha256_count')}` / `{ad.get('header_sha256_count')}` / `{ad.get('payload_sha256_count')}`",
                f"- Modos de protección: `{ad.get('protection_modes')}` · cambios de modo de protección `{ad.get('protection_mode_change_count')}`",
                f"- Algoritmo CRC para el alcance de header compatible: polinomio `{ad.get('crc_polynomial_hex')}` · valor inicial `{ad.get('crc_initial_value_hex')}` · orden de bits `{ad.get('crc_bit_order')}`",
                f"- CRC de RDB único presente/diferido: `{ad.get('single_rdb_crc_present_count')}` / `{ad.get('single_rdb_crc_authentication_deferred_count')}`",
                f"- CRC de header multi-RDB comprobado/autenticado/discrepante: `{ad.get('multi_rdb_header_crc_checked_count')}` / `{ad.get('multi_rdb_header_crc_authenticated_count')}` / `{ad.get('multi_rdb_header_crc_mismatch_count')}` · mapas de posición no válidos `{ad.get('multi_rdb_position_invalid_count')}`",
                f"- Autenticación CRC de bloques de datos crudos: `{ad.get('raw_data_block_crc_authentication')}` · ningún alcance CRC no compatible se promueve a evidencia autenticada."]
        vi=(f.get('format_facts') or {}).get('vorbis_identification') or {};vc=(f.get('format_facts') or {}).get('vorbis_comment') or {};vs=(f.get('format_facts') or {}).get('vorbis_setup') or {}
        if vi.get('present'):
            vf=f.get('format_facts') or {};vog=vf.get('ogg') or {}
            L+=['','**Auditoría estructural Ogg/Vorbis**','',
                f"- Páginas Ogg: `{vog.get('page_count')}` · streams lógicos: `{vog.get('logical_stream_count')}` · serie: `{vog.get('serial')}` · todos los CRC válidos: `{vog.get('all_page_crc_valid')}`",
                f"- Secuencia de páginas: `{vog.get('sequence_start')}–{vog.get('sequence_end')}` · BOS/EOS: `{vog.get('bos_present')}` / `{vog.get('eos_present')}`",
                f"- Identificación: versión `{vi.get('version')}` · canales `{vi.get('channels')}` · frecuencia de muestreo `{vi.get('sample_rate')}` Hz · tamaños de bloque `{vi.get('blocksize_0')}` / `{vi.get('blocksize_1')}` · válido `{vi.get('valid')}`",
                f"- Indicaciones de bitrate máx./nominal/mín.: `{vi.get('bitrate_maximum')}` / `{vi.get('bitrate_nominal')}` / `{vi.get('bitrate_minimum')}` bps",
                f"- Header de comentarios: válido `{vc.get('valid')}` · proveedor `{vc.get('vendor')}` · comentarios `{vc.get('comment_count')}`",
                f"- Header de configuración: válido `{vs.get('valid')}` · libros de códigos `{vs.get('codebook_count')}` · pisos `{vs.get('floor_count')}` · residuos `{vs.get('residue_count')}` · mapeos `{vs.get('mapping_count')}` · modos `{vs.get('mode_count')}`",
                f"- Paquetes de audio: `{vf.get('audio_packet_count')}` · cantidades por modo `{vf.get('audio_mode_counts')}` · cantidades por tamaño de bloque `{vf.get('audio_blocksize_counts')}`",
                f"- Gránulo final: `{vf.get('final_granule_position')}` · segundos de reproducción: `{vf.get('playback_seconds')}`",
                '- La jerarquía Vorbis no agrega autoridad: formaliza la precedencia entre recaptura verificada, regiones probadas y reporte.']
            ve=vf.get('vorbis_recovery_evidence') or {}
            if ve.get('schema'):
                L+=['','**Procedencia de paquetes Vorbis y evidencia de recuperación**','',
                    f"- Política: `{ve.get('policy')}` · autoridad de reparación `{ve.get('repair_authority')}` · publicación PCM `{ve.get('publication_enabled')}`",
                    f"- Paquetes de audio completos autenticados: `{ve.get('authenticated_audio_packet_count')}` · paquetes entre páginas: `{ve.get('cross_page_audio_packet_count')}` · entre páginas autenticados: `{ve.get('authenticated_cross_page_audio_packet_count')}`",
                    f"- Regiones PCM candidatas demostradas: `{ve.get('candidate_region_count')}` · el primer paquete de cada cadena es sólo preparación: `{ve.get('first_packet_of_each_chain_is_priming_only')}`"]
                for r in (ve.get('candidate_regions') or [])[:16]:
                    L.append(f"  - Región {r.get('region_index')}: PCM `{r.get('pcm_start')}–{r.get('pcm_end')}` (`{r.get('sample_count')}` muestras @ `{r.get('sample_rate')}` Hz) · paquete de preparación `{r.get('priming_packet_index')}` · primer paquete con superposición publicada `{r.get('first_published_overlap_packet_index')}` · último paquete `{r.get('last_packet_index')}` · `{r.get('boundary_start')}` → `{r.get('boundary_end')}` · EOS autenticado `{r.get('authenticated_eos_included')}`")
        af=f.get('format_facts') or {};asf=af.get('asf') or {};asf_streams=af.get('streams') or []
        if asf and f.get('detected_container')=='ASF':
            fp=asf.get('file_properties') or {};do=asf.get('data_object') or {};pk=af.get('packets') or {};wa=next((x for x in asf_streams if x.get('is_audio')),{});wf=wa.get('waveformatex') or {}
            play_ms=(fp.get('play_duration_100ns')/10000-fp.get('preroll_ms')) if fp.get('play_duration_100ns') is not None and fp.get('preroll_ms') is not None else None
            L+=['','**Auditoría estructural ASF/WMA**','',
                f"- Header Object: tamaño `{asf.get('header_object_size')}` · subobjetos declarados/analizados `{asf.get('header_object_count_declared')}` / `{asf.get('header_object_count_parsed')}` · bytes reservados `{asf.get('header_reserved_1')}` / `{asf.get('header_reserved_2')}`",
                f"- File Properties Object: tamaño declarado `{fp.get('file_size')}` · paquetes de datos `{fp.get('data_packets_count')}` · tamaño de paquete mín./máx. `{fp.get('min_packet_size')}` / `{fp.get('max_packet_size')}` · seekable `{fp.get('seekable')}` · broadcast `{fp.get('broadcast')}`",
                f"- Duración: reproducción `{fp.get('play_duration_100ns')}` ×100 ns · preroll `{fp.get('preroll_ms')}` ms · duración de presentación del header menos preroll `{play_ms}` ms",
                f"- Stream Properties Object de audio: stream `{wa.get('stream_number')}` · cifrado `{wa.get('encrypted')}` · etiqueta WAVEFORMATEX `{wf.get('format_tag_hex')}` (`{wf.get('codec_name')}`) · válido `{wf.get('valid')}`",
                f"- Formato de audio: `{wf.get('sample_rate')}` Hz · `{wf.get('channels')}` canales · promedio `{wf.get('avg_bytes_per_sec')}` B/s · nominal `{wf.get('nominal_bit_rate')}` bps · alineación de bloque `{wf.get('block_align')}` · extra `{wf.get('extra_size')}` bytes",
                f"- Data Object: tamaño `{do.get('declared_size')}` · paquetes declarados `{do.get('total_data_packets')}` · físicamente completos `{asf.get('physical_complete_packet_count')}` · remanente `{asf.get('packet_region_remainder_bytes')}` bytes · tamaño fijo de paquete `{asf.get('fixed_packet_size')}`",
                f"- Auditoría de paquetes: analizados `{pk.get('parsed_count')}` · todos válidos `{pk.get('all_valid')}` · tiempo de envío `{pk.get('send_time_start_ms')}`→`{pk.get('send_time_end_ms')}` ms · duración mín./máx. `{pk.get('duration_min_ms')}` / `{pk.get('duration_max_ms')}` ms · payloads `{pk.get('payload_count_total')}` · payloads de streams no declarados `{pk.get('undeclared_stream_payload_count')}`",
                '- Autoridad ASF/WMA: reparación estructural `NONE`; la recuperación se limita a sufijos convergentes WMA1/WMA2 validados, sin afirmar una línea de tiempo PCM completa.']
            mo=af.get('media_objects') or {}
            if mo.get('policy'):
                L+=['','**Procedencia de objetos multimedia ASF/WMA y evidencia de recuperación**','',
                    f"- Política: `{mo.get('policy')}` · publicación habilitada `{mo.get('publication_enabled')}` · afirmación PCM exacta por muestra `{mo.get('pcm_sample_exact_claim')}`",
                    f"- Objetos multimedia ordinarios observados: `{mo.get('ordinary_payload_media_objects_observed')}` · completos `{mo.get('complete_media_objects')}` · incompletos `{mo.get('incomplete_media_objects')}`",
                    f"- Objetos fragmentados: `{mo.get('fragmented_media_objects')}` · objetos de varios paquetes: `{mo.get('multi_packet_media_objects')}` · payloads comprimidos no reensamblados por la autoridad actual: `{mo.get('compressed_payloads_unmodeled')}`",
                    f"- Evidencia replicada de tiempo de presentación: `{mo.get('presentation_time_start_ms')}`→`{mo.get('presentation_time_end_ms')}` ms (evidencia del contenedor/objeto multimedia, no límites de muestras PCM)"]
                bad=[x for x in (mo.get('media_objects') or []) if not x.get('complete')]
                for x in bad[:16]:
                    L.append(f"  - Stream `{x.get('stream_number')}` objeto `{x.get('media_object_number')}`: `{x.get('completion')}` · fragmentos `{x.get('fragment_count')}` en paquetes `{x.get('packet_indices')}` · declarado/cubierto `{x.get('declared_size')}` / `{x.get('covered_unique_bytes')}` bytes · brechas `{x.get('gaps')}` · superposiciones `{x.get('overlaps')}`")
            dd=af.get('demux_decoder_evidence') or {}
            if dd.get('policy'):
                L+=['','**Evidencia de línea de tiempo de demux y decodificador ASF/WMA**','',
                    f"- Política: `{dd.get('policy')}` · publicación habilitada `{dd.get('publication_enabled')}` · afirmación PCM exacta por muestra `{dd.get('pcm_sample_exact_claim')}`",
                    f"- Objetos multimedia completos / paquetes demux FFmpeg: `{dd.get('complete_media_object_count')}` / `{dd.get('demux_packet_count')}` · asignación uno a uno por hash+tamaño `{dd.get('one_to_one_complete_media_object_mapping')}`",
                    f"- Igualdad de hashes de paquetes `{dd.get('all_packet_hashes_equal')}` · tamaños iguales `{dd.get('all_packet_sizes_equal')}` · PTS igual al tiempo de presentación del objeto menos preroll `{dd.get('all_pts_match_media_object_presentation_minus_preroll')}`",
                    f"- PTS de demux: `{dd.get('demux_pts_start_ms')}`→`{dd.get('demux_pts_end_ms')}` ms · monotónico `{dd.get('demux_pts_monotonic')}` · discontinuidades `{len(dd.get('demux_timeline_discontinuities') or [])}`",
                    f"- Cuadros decodificados: `{dd.get('decoded_frame_count')}` · cuadros de muestras `{dd.get('decoded_sample_frames_from_ffprobe')}` · valores nb_samples `{dd.get('decoded_frame_nb_samples_values')}` · coincide la cantidad de muestras de decodificación cruda `{dd.get('ffprobe_frame_sample_count_matches_raw_decode')}`",
                    f"- La primera salida del decodificador con marca temporal se asigna al índice de paquete demux `{dd.get('first_timestamped_decoder_output_demux_packet_index')}` · paquetes iniciales antes de la primera salida con marca temporal `{dd.get('observed_startup_demux_packets_before_first_timestamped_output')}` · cuadros de vaciado sin marca temporal `{dd.get('untimestamped_flush_frame_count')}` · máximo de cuadros que comparten una posición de paquete `{dd.get('decoded_frame_max_frames_per_packet_position')}`",
                    f"- Salida del decodificador: `{dd.get('decoder_output_sample_frames')}` cuadros de muestras · `{dd.get('decoder_output_duration_ms')}` ms a frecuencia nativa. Estas son observaciones, no límites de recuperación ASF exactos por muestra."]
                for d in (dd.get('demux_timeline_discontinuities') or [])[:16]:
                    L.append(f"  - Discontinuidad después del paquete demux `{d.get('after_demux_packet_index')}`: salto PTS `{d.get('pts_step_ms')}` ms vs prior duration `{d.get('prior_packet_duration_ms')}` ms (delta `{d.get('excess_or_deficit_ms')}` ms)")
            cv=af.get('decoder_convergence_evidence') or {}
            if cv.get('policy'):
                L+=['','**Convergencia del decodificador ASF/WMA y evidencia de recuperación**','',
                    f"- Política: `{cv.get('policy')}` · publicación habilitada `{cv.get('publication_enabled')}` · afirmación PCM exacta por muestra `{cv.get('pcm_sample_exact_claim')}` · autoridad de recuperación `{cv.get('pcm_recovery_authority')}`",
                    f"- Secuencias demostradas de objetos multimedia ausentes: `{cv.get('candidate_count')}` · candidatos de convergencia validados: `{cv.get('validated_candidate_count')}` · todos los candidatos validados `{cv.get('all_candidates_validated')}`",
                    f"- Longitud constante observada de cuadro del decodificador: `{cv.get('observed_decoder_frame_len_samples')}` muestras · elegibilidad `{cv.get('elegibilidad')}`",
                    '- Regla: el primer objeto multimedia superviviente después de una brecha demostrada es sólo contexto del decodificador; el siguiente objeto superviviente es el primer límite candidato. No se sintetiza PCM ausente.',
                    '- La equivalencia PCM con una referencia sana es sólo evidencia de pruebas de aceptación y no se presupone para archivos fuente arbitrarios.']
                for c in (cv.get('candidates') or [])[:16]:
                    L.append(f"  - Brecha después del objeto `{c.get('previous_media_object_number')}`: ausentes `{c.get('missing_media_object_count')}` objeto(s) · objeto de contexto `{c.get('context_media_object_number')}` / paquete `{c.get('context_demux_packet_index')}` · primer objeto candidato `{c.get('expected_first_candidate_media_object_number')}` / paquete `{c.get('expected_first_candidate_demux_packet_index')}` · frame de muestra inicial del sufijo `{c.get('full_decode_suffix_start_sample_frame')}` · frame decodificado `{c.get('full_decode_suffix_start_decoded_frame_index')}` · igualdad del hash búsqueda/sufijo `{c.get('seek_decode_matches_full_decode_suffix')}` · estado `{c.get('status')}`")
            wr=af.get('wma_recovery_assessment') or {}
            if wr.get('policy'):
                L+=['','**Evaluación de recuperación demostrada de sufijo convergente ASF/WMA**','',
                    f"- Política: `{wr.get('policy')}` · elegible `{wr.get('eligible')}` · clase PCM `{wr.get('pcm_class')}` · materialización `{wr.get('materialization')}`",
                    f"- Cobertura: `{wr.get('coverage_claim')}` · afirmación de línea de tiempo PCM de la fuente completa `{wr.get('full_source_pcm_timeline_claim')}` · intervalo ausente sintetizado `{wr.get('synthesized_missing_span')}`",
                    f"- Decisión: {wr.get('reason')}"]
                for r in (wr.get('regions') or [])[:8]:
                    L.append(f"  - Región {r.get('region_index')}: brecha después del objeto `{r.get('gap_after_media_object_number')}` · ausentes `{r.get('missing_media_object_count')}` · objeto de contexto `{r.get('context_media_object_number')}` no publicado · primer objeto publicado `{r.get('first_published_media_object_number')}` / paquete `{r.get('first_published_demux_packet_index')}` · muestra inicial del sufijo decodificado dañado `{r.get('damaged_decode_suffix_start_sample_frame')}` · salida `{r.get('output_sample_frames')}` cuadros de muestras")
            mr=af.get('wma_multi_region_recovery_assessment') or {}
            if mr.get('policy'):
                L+=['','**Evaluación de recuperación multirregión demostrada ASF/WMA**','',
                    f"- Política: `{mr.get('policy')}` · elegible `{mr.get('eligible')}` · clase PCM `{mr.get('pcm_class')}` · materialización `{mr.get('materialization')}`",
                    f"- Brechas / regiones publicadas: `{mr.get('gap_count')}` / `{mr.get('region_count')}` · regiones concatenadas `{mr.get('regions_concatenated')}`",
                    f"- Cobertura: `{mr.get('coverage_claim')}` · afirmación de línea de tiempo PCM de la fuente completa `{mr.get('full_source_pcm_timeline_claim')}` · intervalo ausente sintetizado `{mr.get('synthesized_missing_span')}`",
                    f"- Decisión: {mr.get('reason')}"]
                for r in (mr.get('regions') or [])[:16]:
                    L.append(f"  - Región {r.get('region_index')}: muestras de decodificación dañada `{r.get('decoded_sample_start')}`–`{r.get('decoded_sample_end')}` · `{r.get('sample_count')}` muestras · límites `{r.get('boundary_start')}` → `{r.get('boundary_end')}` · procedencia completa `{r.get('provenance_complete')}`")
                for x in (mr.get('excluded_context_intervals') or [])[:16]:
                    L.append(f"  - Contexto excluido después de la brecha {x.get('gap_index')}: muestras `{x.get('decoded_sample_start')}`–`{x.get('decoded_sample_end')}` · objeto de contexto `{x.get('context_media_object_number')}` · publicadas `{x.get('context_audio_published')}`")
        wma_hierarchy=((f.get('format_facts') or {}).get('wma_preservation_hierarchy') or {})
        if wma_hierarchy.get('schema'):
            L+=['','**Jerarquía de resolución de preservación ASF/WMA**','',
                f"- Política: `{wma_hierarchy.get('policy')}` · resultado exclusivo: `{wma_hierarchy.get('exclusive_outcome')}`",
                f"- Precedencia ordenada: `{' → '.join(wma_hierarchy.get('order') or [])}`",
                f"- Nivel seleccionado: `{wma_hierarchy.get('selected_tier')}`" + (f" · rank `{wma_hierarchy.get('selected_rank')}`" if wma_hierarchy.get('selected_rank') else ''),
                f"- Salidas publicadas/reutilizadas en el nivel seleccionado: `{wma_hierarchy.get('selected_output_count')}` · creadas/reutilizadas: `{(wma_hierarchy.get('status_counts') or {}).get('CREATED')}` / `{(wma_hierarchy.get('status_counts') or {}).get('REUSED')}`",
                f"- Salidas multirregión / sufijo convergente observadas: `{wma_hierarchy.get('multi_region_output_count')}` / `{wma_hierarchy.get('converged_suffix_output_count')}`",
                f"- Tipos sin pérdida observados: `{', '.join(wma_hierarchy.get('observed_lossless_derivation_kinds') or []) or 'ninguno'}`",
                f"- Selección: {wma_hierarchy.get('selection_reason')}"]
        vorbis_hierarchy=((f.get('format_facts') or {}).get('vorbis_preservation_hierarchy') or {})
        if vorbis_hierarchy.get('schema'):
            L+=['','**Jerarquía de resolución de preservación Ogg/Vorbis**','',
                f"- Política: `{vorbis_hierarchy.get('policy')}` · resultado exclusivo: `{vorbis_hierarchy.get('exclusive_outcome')}`",
                f"- Precedencia ordenada: `{' → '.join(vorbis_hierarchy.get('order') or [])}`",
                f"- Nivel seleccionado: `{vorbis_hierarchy.get('selected_tier')}`" + (f" · rank `{vorbis_hierarchy.get('selected_rank')}`" if vorbis_hierarchy.get('selected_rank') else ''),
                f"- Salidas publicadas/reutilizadas en el nivel seleccionado: `{vorbis_hierarchy.get('selected_output_count')}` · creadas/reutilizadas: `{(vorbis_hierarchy.get('status_counts') or {}).get('CREATED')}` / `{(vorbis_hierarchy.get('status_counts') or {}).get('REUSED')}`",
                f"- Recuperación con EOS autenticado presente: `{vorbis_hierarchy.get('authenticated_eos_recovery_present')}` · tipos sin pérdida observados: `{', '.join(vorbis_hierarchy.get('observed_lossless_derivation_kinds') or []) or 'ninguno'}`",
                f"- Selección: {vorbis_hierarchy.get('selection_reason')}"]
        opus_hierarchy=((f.get('format_facts') or {}).get('opus_preservation_hierarchy') or {})
        if opus_hierarchy.get('schema'):
            L+=['','**Jerarquía de resolución de preservación Ogg/Opus**','',
                f"- Política: `{opus_hierarchy.get('policy')}` · resultado exclusivo: `{opus_hierarchy.get('exclusive_outcome')}`",
                f"- Precedencia ordenada: `{' → '.join(opus_hierarchy.get('order') or [])}`",
                f"- Nivel seleccionado: `{opus_hierarchy.get('selected_tier')}`" + (f" · rank `{opus_hierarchy.get('selected_rank')}`" if opus_hierarchy.get('selected_rank') else ''),
                f"- Salidas publicadas/reutilizadas en el nivel seleccionado: `{opus_hierarchy.get('selected_output_count')}` · creadas/reutilizadas: `{(opus_hierarchy.get('status_counts') or {}).get('CREATED')}` / `{(opus_hierarchy.get('status_counts') or {}).get('REUSED')}`",
                f"- Recuperación con EOS autenticado presente: `{opus_hierarchy.get('authenticated_eos_recovery_present')}` · tipos sin pérdida observados: `{', '.join(opus_hierarchy.get('observed_lossless_derivation_kinds') or []) or 'ninguno'}`",
                f"- Selección: {opus_hierarchy.get('selection_reason')}"]
        de=((f.get('format_facts') or {}).get('decoder_evidence') or {})
        if de:
            ff=de.get('ffmpeg') or {};mp=de.get('mpg123') or {};ag=de.get('agreement') or {}
            L+=['','**Evidencia de decodificador MPEG independiente**','',f"- Política: `{de.get('policy')}` · canónico: `{de.get('canonical_decoder')}` · independiente: `{de.get('independent_decoder')}`",f"- Versión de mpg123: `{mp.get('decoder_version')}` · SHA-256 del binario: `{mp.get('decoder_binary_sha256')}` · confianza: `{mp.get('supply_chain_trust')}`",f"- FFmpeg / mpg123 completado: `{ff.get('completed')}` / `{mp.get('completed')}`",f"- FFmpeg / mpg123 cuadros de muestras decodificadas: `{ff.get('sample_frames')}` / `{mp.get('sample_frames')}`",f"- Igualdad de cantidad de cuadros de muestras: `{ag.get('sample_frame_count_equal')}` · igualdad del SHA-256 PCM s32 crudo: `{ag.get('raw_s32_pcm_sha256_equal')}` (sólo informativo)"]
        matrix=((f.get('format_facts') or {}).get('evidence_consistency') or {})
        if matrix.get('schema'):
            sg=matrix.get('signals') or {}
            L+=['','**Matriz de consistencia cruzada de evidencia MPEG**','',
                f"- Política: `{matrix.get('policy')}` · autoridad de reparación: `{matrix.get('repair_authority')}`",
                f"- Interpretation: `{matrix.get('interpretation')}`",
                f"- Dominios de evidencia activos: `{', '.join(matrix.get('active_evidence_domains') or []) or 'ninguno'}`",
                f"- Brechas/truncamiento de framing: `{sg.get('gap_count')}` / `{sg.get('truncated_final_frame')}` · reservoir sin resolver/desbordado: `{sg.get('reservoir_unresolved_frame_count')}` / `{sg.get('reservoir_overrun_frame_count')}`",
                f"- Discrepancias CRC: `{sg.get('crc_mismatch_count')}` · metadata no conforme: `{', '.join(sg.get('seek_or_timeline_nonconformance_codes') or []) or 'ninguna'}` · no conformidad terminal: `{', '.join(sg.get('terminal_nonconformance_codes') or []) or 'ninguna'}`",
                f"- Coinciden finalización/cantidad de muestras de decodificadores: `{sg.get('decoder_completion_equal')}` / `{sg.get('decoder_sample_frame_count_equal')}`" + (f" · divergencia explicada por `{sg.get('decoder_sample_count_divergence_explained_by')}`" if sg.get('decoder_sample_count_divergence_explained_by') else ''),
                '- La igualdad del hash PCM crudo entre implementaciones de decodificador diferentes es sólo informativa y no se usa como veredicto de integridad.']
        hierarchy=((f.get('format_facts') or {}).get('preservation_hierarchy') or {})
        if hierarchy.get('schema'):
            L+=['','**Jerarquía de resolución de preservación MPEG**','',
                f"- Política: `{hierarchy.get('policy')}` · resultado exclusivo: `{hierarchy.get('exclusive_outcome')}`",
                f"- Precedencia ordenada: `{' → '.join(hierarchy.get('order') or [])}`",
                f"- Nivel seleccionado: `{hierarchy.get('selected_tier')}`" + (f" · rank `{hierarchy.get('selected_rank')}`" if hierarchy.get('selected_rank') else ''),
                f"- Salidas publicadas/reutilizadas en el nivel seleccionado: `{hierarchy.get('selected_output_count')}` · creadas/reutilizadas: `{(hierarchy.get('status_counts') or {}).get('CREATED')}` / `{(hierarchy.get('status_counts') or {}).get('REUSED')}`",
                f"- Tipos observados de derivados sin pérdida: `{', '.join(hierarchy.get('observed_lossless_derivation_kinds') or []) or 'ninguno'}`",
                f"- Selección: {hierarchy.get('selection_reason')}"]
        crc=((f.get('format_facts') or {}).get('crc_protection') or {})
        if crc.get('protected_frame_count') or crc.get('checked_frame_count'):
            L+=['','**Protección CRC MPEG**','',f"- Alcance: `{crc.get('supported_scope')}` · algoritmo: `{crc.get('algorithm')}` · polinomio: `{crc.get('polynomial')}` · inicio: `{crc.get('initial_state')}`",f"- Frames protegidos/comprobados: `{crc.get('protected_frame_count')}` / `{crc.get('checked_frame_count')}`",f"- CRC válidos/discrepantes: `{crc.get('valid_frame_count')}` / `{crc.get('mismatch_count')}`",f"- Bits cubiertos: `{crc.get('coverage')}`"]
        compat=((f.get('format_facts') or {}).get('compatibility_profile') or {})
        if compat.get('schema'):
            L+=['','**Evidencia de compatibilidad y procedencia MPEG**','',
                f"- Política de evidencia: `{compat.get('evidence_policy')}`",
                f"- Versiones/capas MPEG: `{', '.join(map(str,compat.get('mpeg_versions') or []))}` / `{', '.join(map(str,compat.get('layers') or []))}` · frecuencias de muestreo: `{', '.join(map(str,compat.get('sample_rates_hz') or []))}` Hz · canales: `{', '.join(map(str,compat.get('channels') or []))}`",
                f"- Header de búsqueda: `{compat.get('dedicated_seek_header')}` · protección CRC: `{compat.get('crc_protection')}` · bitrate(s): `{', '.join(map(str,compat.get('bitrate_kbps_values') or [])) or 'formato-libre/desconocido'}` kbps",
                f"- Encoder declarado: `{compat.get('declared_encoder') or 'UNATTRIBUTED'}` · atribución: `{compat.get('encoder_attribution')}`",
                f"- Metadata: ID3v2 `{compat.get('id3v2_major') if compat.get('id3v2_major') is not None else 'ninguna'}` · ID3v1 `{compat.get('id3v1_present')}`",
                f"- Flags de variante: `{', '.join(compat.get('variant_flags') or []) or 'ninguno'}`"]
        params=((f.get('format_facts') or {}).get('parameter_segments') or {})
        if params.get('segment_count'):
            L+=['','**Segmentación multiparámetro MPEG**','',
                f"- Segmentos: `{params.get('segment_count')}` · transiciones duras de perfil: `{params.get('hard_profile_transition_count')}` · transiciones suaves de codificación: `{params.get('soft_transition_count')}`",
                f"- Concatenaciones coherentes: `{params.get('coherent_concatenation_transition_count')}` · cambios de parámetros después de resincronizar: `{params.get('parameter_change_after_resync_count')}`"]
            for seg in (params.get('segments') or [])[:16]:
                pr=seg.get('profile') or {};br=seg.get('bitrate_kbps_values') or []
                L.append(f"  - Segmento {seg.get('index')}: frames `{seg.get('frame_start_index')}–{seg.get('frame_end_index')}` · bytes `{seg.get('byte_start')}–{seg.get('byte_end')}` · MPEG `{pr.get('mpeg_version')}` Layer `{pr.get('layer')}` · `{pr.get('sample_rate')} Hz` · `{pr.get('channels')} canales` · `{pr.get('channel_mode_name')}` · CRC `{pr.get('protected_by_crc')}` · bitrate(s) `{', '.join(map(str,br)) or 'formato-libre'}`")
            for tr in (params.get('transitions') or [])[:16]:
                L.append(f"  - Transición {tr.get('from_segment')}→{tr.get('to_segment')} @ `{tr.get('byte_offset')}`: `{tr.get('interpretation')}` · modificados `{', '.join(tr.get('changed_fields') or []) or 'ninguno'}`")
        reservoir=((f.get('format_facts') or {}).get('bit_reservoir') or {})
        if reservoir.get('supported_scope')=='MPEG_LAYER_III':
            unresolved=reservoir.get('unresolved_pre_segment_frame_indices') or [];overrun=reservoir.get('main_data_overrun_frame_indices') or []
            L+=['','**Mapa de dependencias del bit reservoir Layer III**','',
                f"- Mapeo: `{reservoir.get('mapping')}` · frames mapeados/demostrables: `{reservoir.get('mapped_frame_count')}` / `{reservoir.get('fully_provable_frame_count')}`",
                f"- Frames con referencias previas: `{reservoir.get('frames_with_backreferences')}` · main_data_begin máximo: `{reservoir.get('max_main_data_begin_bytes')}` bytes",
                f"- Alcance máximo de dependencia previa: `{reservoir.get('max_dependency_backspan_frames')}` frame(s) · máximo de dependientes posteriores sobre un frame físico: `{reservoir.get('max_dependent_frame_count')}`",
                f"- Referencias sin resolver antes del segmento de resincronización: `{len(unresolved)}`" + (f" · frames `{', '.join(map(str,unresolved[:16]))}`" if unresolved else ''),
                f"- Desbordamientos de demanda de main-data: `{len(overrun)}`" + (f" · frames `{', '.join(map(str,overrun[:16]))}`" if overrun else '')]
        if f.get('issues'):
            L+=['','**Observaciones / hallazgos**','']
            for i in f['issues']:
                rng=f" bytes {i.get('byte_start')}–{i.get('byte_end')}" if i.get('byte_start') is not None else ''
                L.append(f"- `{i['code']}`{rng}: {i['description']}  ");L.append(f"  integridad `{i['integrity']}` · playability `{i['playability']}` · reparabilidad `{i['repairability']}`")
        if f.get('repair_plan'):
            L+=['','**Especificaciones de reparación**','']
            for p in f['repair_plan']:
                prefix=f"plan de pasos {int(p.get('chain_iteration',0))+1}: " if 'chain_iteration' in p else ''
                L.append(f"- {prefix}`{p['spec']['id']}`: `{p['status']}` — {p['reason']}")
        if f.get('repair_execution'):
            L+=['','**Ejecución de reparación verificada**','']
            for e in f['repair_execution']:
                L.append(f"- `{e.get('repair_spec_id')}`: `{e.get('status')}`")
                if e.get('reason') and e.get('status') in ('BLOCKED','REJECTED'):L.append(f"  Motivo: {e.get('reason')}")
                if e.get('output_path'):L.append(f"  Salida: `{e['output_path']}`")
                man=e.get('manifest') or {};vr=man.get('verification') or e.get('verification') or {}
                if man.get('derivation_kind')=='EXTENSION_FIXED':
                    L.append(f"  Fuente/salida idénticas en bytes: `{'PASS' if man.get('byte_identical_to_source') and man.get('source_sha256')==man.get('output_sha256') else 'FAIL'}` · SHA-256 `{man.get('output_sha256')}`")
                    L.append(f"  Rangos de bytes modificados: `{len(man.get('changed_byte_ranges') or [])}` · recodificación de audio: `{man.get('audio_recoding')}`")
                elif man.get('derivation_kind')=='REPAIRED_SAFE':
                    L.append(f"  Nuevo análisis posterior a la reparación: `{'PASS' if vr.get('passed') else 'FAIL'}` · nuevos hallazgos de daño: `{len(vr.get('new_damaged_issue_codes') or [])}`")
                    steps=man.get('chain_steps') or []
                    if len(steps)>1:
                        L.append(f"  Cadena causal de reparación: `{len(steps)}` paso(s) · nuevo análisis incremental: `{vr.get('incremental_rescan_after_each_step')}`")
                        L.append(f"  Issues iniciales/finales: `{', '.join(vr.get('initial_issue_codes') or []) or 'ninguno'}` → `{', '.join(vr.get('final_issue_codes') or []) or 'ninguno'}`")
                        for st in steps:L.append(f"    - Paso {st.get('step')}: `{st.get('repair_spec_id')}` → `{st.get('status')}` · resueltos `{', '.join((st.get('verification') or {}).get('resolved_issue_codes') or []) or 'ninguno'}`")
                    if any(st.get('repair_spec_id')=='REFRESH_XING_METADATA' for st in steps) or e.get('repair_spec_id')=='REFRESH_XING_METADATA':
                        if vr.get('pcm_identity_gate')=='STRUCTURAL_GAPLESS_PROOF':
                            sp=vr.get('source_structural_presentation_proof') or {}
                            L.append(f"  Prueba estructural de cantidad de frames Xing: `{'PASS' if vr.get('presentation_equivalent_independent_of_declared_frame_count') else 'FAIL'}` · PCM físico idéntico `{vr.get('physical_pcm_identical')}` · el candidato coincide con la ventana reconstruida `{vr.get('candidate_matches_structural_window')}`")
                            L.append(f"  Muestras físicas/de ventana: `{sp.get('physical_sample_count')}` → `{sp.get('window_start_sample')}–{sp.get('window_end_sample')}` = `{sp.get('logical_sample_count')}` muestras lógicas")
                            L.append(f"  SHA-256 PCM físico: `{sp.get('physical_pcm_sha256')}`")
                            L.append(f"  SHA-256 PCM de ventana estructural/canónico reparado: `{sp.get('structural_window_pcm_sha256')}` / `{vr.get('candidate_canonical_pcm_sha256')}`")
                            L.append(f"  La decodificación normal del original depende de la metadata y difiere: `{vr.get('source_normal_decode_differs_due_to_bad_frame_count')}` · SHA-256 `{vr.get('source_canonical_pcm_sha256')}`")
                        else:
                            L.append(f"  Actualización coherente Xing: PCM idéntico `{vr.get('pcm_identical')}` · payload de audio idéntico `{vr.get('audio_payload_identical')}` · seek metadata validada `{vr.get('seekability_metadata_validated')}`")
                            if vr.get('source_canonical_pcm_sha256'):L.append(f"  SHA-256 PCM canónico fuente/reparado: `{vr.get('source_canonical_pcm_sha256')}` / `{vr.get('candidate_canonical_pcm_sha256')}`")
                    if any(st.get('repair_spec_id')=='REFRESH_VBRI_METADATA' for st in steps) or e.get('repair_spec_id')=='REFRESH_VBRI_METADATA':
                        L.append(f"  Actualización coherente VBRI: PCM idéntico `{vr.get('pcm_identical')}` · payload de audio idéntico `{vr.get('audio_payload_identical')}` · cobertura de tabla validada `{vr.get('vbri_table_coverage_validated')}`")
                        if vr.get('source_canonical_pcm_sha256'):L.append(f"  SHA-256 PCM canónico fuente/reparado: `{vr.get('source_canonical_pcm_sha256')}` / `{vr.get('candidate_canonical_pcm_sha256')}`")
                    if man.get('repair_spec_id')=='OGG_RECAPTURE_VALID_PAGES_DROP_EXTRANEOUS_BYTES' and f.get('detected_codec')=='vorbis':
                        L.append(f"  Prueba de recaptura de páginas Vorbis: páginas retenidas exactas `{vr.get('retained_page_bytes_exact')}` · hashes de paquetes iguales `{vr.get('vorbis_audio_packet_hashes_equal')}` · identificación/comentario/configuración iguales `{vr.get('vorbis_identification_equal')}` / `{vr.get('vorbis_comment_equal')}` / `{vr.get('vorbis_setup_equal')}` · regiones PCM candidatas iguales `{vr.get('candidate_pcm_regions_equal')}`")
                        L.append(f"  bytes de páginas Ogg modificados: `{vr.get('page_bytes_modified')}` · bytes de paquetes Vorbis modificados: `{vr.get('vorbis_packet_bytes_modified')}`")
                    diff=man.get('changed_byte_ranges') or []
                    L.append(f"  Rangos de bytes modificados: `{len(diff)}` · recodificación de audio: `{man.get('audio_recoding')}`")
                    for r in diff:L.append(f"    - paso {r.get('step')} · {r.get('operation','CHANGE')} bytes `{r.get('byte_start')}–{r.get('byte_end')}`" + (f" · campo `{r.get('field')}`" if r.get('field') else ''))
        if f.get('lossless_export'):
            L+=['','**Exportación de preservación sin pérdida**','']
            for ex in f['lossless_export']:
                L.append(f"- Estado de exportación: `{ex.get('status')}`")
                for o in ex.get('outputs',[]):
                    man=o.get('manifest',{});L.append(f"  - `{man.get('derivation_kind')}` → `{o.get('output_path')}`")
                    if man.get('materialization'):L.append(f"    Materialización: `{man['materialization']}`")
                    if man.get('derivation_kind')=='RECOVERED_WMA_CONVERGED_SUFFIX_LOSSLESS':
                        L.append(f"    Convergencia WMA: objetos ausentes `{man.get('missing_media_object_count')}` después de `{man.get('gap_after_media_object_number')}` · objeto de contexto `{man.get('context_media_object_number')}` excluido · primer objeto publicado `{man.get('first_published_media_object_number')}`")
                        L.append(f"    Salida: `{man.get('output_sample_frames')}` frames de muestras @ `{man.get('sample_rate')}` Hz · cobertura `{man.get('coverage_claim')}` · afirmación de línea de tiempo completa `{man.get('full_source_pcm_timeline_claim')}`")
                        L.append(f"    SHA-256 PCM región/recuperado: `{man.get('region_pcm_sha256')}` / `{man.get('flac_decoded_pcm_sha256')}` · intervalo ausente sintetizado: `{man.get('synthesized_missing_span')}`")
                    if man.get('derivation_kind')=='RECOVERED_WMA_PROVEN_REGION_LOSSLESS':
                        L.append(f"    Región WMA demostrada `{man.get('region_index')}`/`{man.get('region_count')}`: muestras de decodificación dañada `{man.get('decoded_sample_start')}`–`{man.get('decoded_sample_end')}` · `{man.get('output_sample_frames')}` frames de muestras @ `{man.get('sample_rate')}` Hz")
                        L.append(f"    Límites: `{man.get('boundary_start')}` → `{man.get('boundary_end')}` · cobertura `{man.get('coverage_claim')}` · regiones concatenadas `{man.get('regions_concatenated')}` · afirmación de línea de tiempo completa `{man.get('full_source_pcm_timeline_claim')}`")
                        L.append(f"    Contexto publicado: `{man.get('context_audio_published')}` · intervalo ausente sintetizado: `{man.get('synthesized_missing_span')}` · remuestreo `{man.get('resampling')}` · remezcla `{man.get('channel_remix')}`")
                        L.append(f"    SHA-256 PCM región/recuperado: `{man.get('region_pcm_sha256')}` / `{man.get('flac_decoded_pcm_sha256')}`")
                    regs=man.get('regions') or [];gaps=man.get('synthesized_gap_silence') or [];prof=man.get('canonical_pcm_profile') or {};sr=prof.get('sample_rate') or 0
                    if regs:
                        genuine=sum(r.get('sample_count',0) for r in regs);total=(man.get('canonical_presentation_window') or {}).get('logical_sample_count')
                        L.append(f"    Regiones limpias genuinas: `{len(regs)}` · `{genuine} muestras`" + (f" · `{100*genuine/total:.3f}%` de la línea de tiempo canónica" if total else ''))
                    if gaps:L.append(f"    `SYNTHESIZED_GAP_SILENCE`: `{sum(g.get('sample_count',0) for g in gaps)} muestras` en `{len(gaps)}` brecha(s); excluidas de los hashes de regiones genuinas")
                    if man.get('derivation_kind')=='RECOVERED_LOSSLESS':L.append(f"    SHA-256 PCM canónico fuente/recuperado: `{man.get('source_canonical_pcm_sha256')}` / `{man.get('flac_canonical_pcm_sha256')}`")
                    if man.get('derivation_kind')=='RECOVERED_SEGMENTED_LOSSLESS':
                        pr=man.get('native_profile') or {}
                        L.append(f"    Perfil nativo del segmento: MPEG `{pr.get('mpeg_version')}` Layer `{pr.get('layer')}` · `{pr.get('sample_rate')} Hz` · `{pr.get('channels')} canales` · remuestreo `{man.get('resampling')}` · remezcla `{man.get('channel_remix')}`")
                        L.append(f"    Bytes fuente: `{man.get('source_byte_start')}–{man.get('source_byte_end')}` · SHA-256 PCM segmento fuente/recuperado: `{man.get('source_segment_pcm_sha256')}` / `{man.get('flac_decoded_pcm_sha256')}`")
                    if man.get('derivation_kind')=='RECOVERED_SEGMENTED_PARTIAL_LOSSLESS':
                        pr=man.get('native_profile') or {}
                        L.append(f"    Perfil nativo de la región limpia: MPEG `{pr.get('mpeg_version')}` Layer `{pr.get('layer')}` · `{pr.get('sample_rate')} Hz` · `{pr.get('channels')} canales` · remuestreo `{man.get('resampling')}` · remezcla `{man.get('channel_remix')}`")
                        L.append(f"    Bytes fuente genuinos: `{man.get('source_byte_start')}–{man.get('source_byte_end')}` · el contexto del decodificador comienza `{man.get('decode_context_byte_start')}` · preparación/contexto descartado `{man.get('discarded_context_samples')} muestras`")
                        L.append(f"    Frames contaminados/de preparación descartados: `{man.get('preclean_tainted_frame_count')}` / `{man.get('warmup_clean_frame_count')}` · brecha precedente `{man.get('gap_before_index')}`")
                        L.append(f"    SHA-256 PCM de región limpia/recuperada: `{man.get('source_region_pcm_sha256')}` / `{man.get('flac_decoded_pcm_sha256')}` · intervalo dañado sintetizado: `NONE`")
                    if man.get('derivation_kind')=='RECOVERED_SEGMENTED_OPEN_PARTIAL_LOSSLESS':
                        pr=man.get('native_profile') or {}
                        L.append(f"    Perfil nativo de la región demostrada: MPEG `{pr.get('mpeg_version')}` Layer `{pr.get('layer')}` · `{pr.get('sample_rate')} Hz` · `{pr.get('channels')} canales` · remuestreo `{man.get('resampling')}` · remezcla `{man.get('channel_remix')}`")
                        L.append(f"    Bytes fuente genuinos: `{man.get('source_byte_start')}–{man.get('source_byte_end')}` · el contexto del decodificador comienza `{man.get('decode_context_byte_start')}` · preparación/contexto descartado `{man.get('discarded_context_samples')} muestras`")
                        L.append(f"    Cobertura: `{man.get('coverage_claim')}` · truncamiento terminal `{man.get('truncated_final_frame')}` · brechas sin delimitación `{man.get('unbracketed_gap_indices')}`")
                        L.append(f"    SHA-256 PCM de región demostrada/recuperada: `{man.get('source_region_pcm_sha256')}` / `{man.get('flac_decoded_pcm_sha256')}` · intervalo ausente sintetizado: `NONE`")
                    if man.get('derivation_kind')=='RECOVERED_HOMOGENEOUS_OPEN_PARTIAL_LOSSLESS':
                        pr=man.get('native_profile') or {}
                        L.append(f"    Perfil homogéneo de la región demostrada: MPEG `{pr.get('mpeg_version')}` Layer `{pr.get('layer')}` · `{pr.get('sample_rate')} Hz` · `{pr.get('channels')} canales` · remuestreo `{man.get('resampling')}` · remezcla `{man.get('channel_remix')}`")
                        L.append(f"    Bytes fuente genuinos: `{man.get('source_byte_start')}–{man.get('source_byte_end')}` · el contexto del decodificador comienza `{man.get('decode_context_byte_start')}` · preparación/contexto descartado `{man.get('discarded_context_samples')} muestras`")
                        L.append(f"    Cobertura: `{man.get('coverage_claim')}` · prioridad de reparación `{man.get('repair_priority')}` · truncamiento terminal `{man.get('truncated_final_frame')}` · brechas sin delimitación `{man.get('unbracketed_gap_indices')}`")
                        L.append(f"    SHA-256 PCM de región demostrada/recuperada: `{man.get('source_region_pcm_sha256')}` / `{man.get('flac_decoded_pcm_sha256')}` · intervalo ausente sintetizado: `NONE`")
                    if man.get('derivation_kind')=='RECOVERED_VORBIS_PROVEN_REGION_LOSSLESS':
                        L.append(f"    Región Vorbis demostrada: PCM `{man.get('source_pcm_start')}–{man.get('source_pcm_end')}` · `{man.get('sample_count')}` muestras @ `{man.get('sample_rate')}` Hz")
                        L.append(f"    Paquete de preparación: `{man.get('priming_packet_index')}` · primer paquete con superposición publicada `{man.get('first_published_overlap_packet_index')}` · último paquete `{man.get('last_packet_index')}`")
                        L.append(f"    Procedencia de paquetes: seleccionados `{man.get('selected_packet_count')}` · entre páginas `{man.get('continued_source_packet_count')}` · páginas fuente `{man.get('source_page_sequences')}`")
                        L.append(f"    Límites: `{man.get('boundary_start')}` → `{man.get('boundary_end')}` · EOS autenticado `{man.get('includes_authenticated_eos')}` · recorte EOS temporal `{man.get('temporary_eos_trim_samples')}` muestras")
                        L.append(f"    Bytes de paquete modificados: `{man.get('vorbis_packet_bytes_modified')}` · la vista temporal de decodificación vuelve a paginar paquetes `{man.get('temporary_decode_view_repages_packets')}` · intervalo ausente sintetizado `{man.get('synthesized_missing_span')}`")
                        L.append(f"    SHA-256 PCM región/recuperado: `{man.get('region_pcm_sha256')}` / `{man.get('flac_decoded_pcm_sha256')}`")
                    if man.get('derivation_kind')=='RECOVERED_OPUS_PROVEN_REGION_LOSSLESS':
                        L.append(f"    Región Opus demostrada @48k: `{man.get('source_pcm_start_48k')}–{man.get('source_pcm_end_48k')}` · `{man.get('sample_count')} muestras` · páginas `{man.get('source_page_sequences')}`")
                        L.append(f"    Contexto: `{man.get('context_policy')}` · descartadas `{man.get('decoder_context_discard_samples_48k')}` muestras · pre-roll mínimo de búsqueda `{man.get('minimum_seek_preroll_samples_48k')}`")
                        L.append(f"    Ganancia: fuente `{man.get('source_output_gain_q7_8')}` Q7.8 · política `{man.get('output_gain_policy')}` · incorporada al PCM `{man.get('output_gain_baked_into_pcm')}` · ganancia de la vista temporal de decodificación `{man.get('temporary_decode_view_output_gain_q7_8')}`")
                        L.append(f"    Bytes de paquete modificados: `{man.get('opus_audio_packet_bytes_modified')}` · páginas autenticadas por CRC `{man.get('source_page_crc_authenticated')}` · intervalo ausente sintetizado `{man.get('synthesized_missing_span')}`")
                        if man.get('derivation_schema',1)>=2:
                            L.append(f"    Paquetes fuente continuados: `{man.get('continued_source_packet_count')}` · EOS autenticado incluido `{man.get('includes_authenticated_eos')}` · recorte final EOS exacto `{man.get('eos_end_trim_samples_48k')}` muestras")
                            L.append(f"    La vista temporal de decodificación vuelve a paginar paquetes: `{man.get('temporary_decode_view_repages_packets')}`; los bytes de payload de paquetes Opus permanecen sin cambios")
                        L.append(f"    SHA-256 PCM región/recuperado: `{man.get('region_pcm_sha256')}` / `{man.get('flac_decoded_pcm_sha256')}`")
        if f.get('policy_decisions'):
            L+=['','**Decisiones de política**','']
            for d in f['policy_decisions']:L.append(f"- `{d.get('code')}` → `{d.get('decision')}` — {d.get('reason')}")
        patterns=f.get('pattern_analysis') or {}
        if patterns:
            L+=['','**Patrones observados de hallazgos**','',f"- Alcance: `{patterns.get('scope')}` · hallazgos `{patterns.get('issue_count')}` · códigos distintos `{patterns.get('distinct_issue_code_count')}`",'- Este resumen es sólo observacional; no cambia la política de reparación.']
            for group in patterns.get('groups') or []:
                span=(f" bytes `{group.get('first_byte_start')}–{group.get('last_byte_end')}`" if group.get('known_byte_range_count') else '')
                L.append(f"  - `{group.get('issue_code')}`: `{group.get('occurrence_count')}` ocurrencia(s), `{group.get('observation')}`{span}")
        graph=f.get('causal_graph') or {}
        if graph:
            L+=['','**Evaluación causal**','',f"- Conclusión: `{graph.get('conclusion')}`"]
            if graph.get('unresolved_observed_issue_codes'):L.append(f"- Hallazgos observados sin relación causal probada: `{', '.join(graph['unresolved_observed_issue_codes'])}`")
        L+=['',f"**Clasificación final:** `{' + '.join(f.get('final_status') or [])}`",'', '---','']
    path.write_text('\n'.join(L),encoding='utf-8')
