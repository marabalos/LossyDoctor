# Baseline descriptivo de LossyDoctor V1.1.0

Este documento describe el producto y la evidencia incluida en el código fuente. El commit, la etiqueta y el SHA-256 de cada ZIP son metadatos externos de la publicación y no se insertan durante el empaquetado.

## Versión y contratos

- Aplicación: `1.1.0`.
- Política: `1.1.0-v1-stable-1`.
- Bootstrap: `1.1.0-bootstrap.1`.
- Schemas de configuración, análisis, reporte y manifiesto: `3 / 3 / 3 / 3`.
- Plataforma: Windows 10 o posterior, x86-64, en una carpeta portátil.
- Invariantes: original inmutable, nunca sobrescribir, fail closed (no intervenir ante ambigüedad), preservar bytes o PCM genuino antes que reconstruir y no recodificar con pérdida.

## Plataforma y toolchain

El bootstrap prepara y reutiliza herramientas locales fijadas por `bootstrap_manifest.json`:

- uv `0.12.5`.
- Python `3.12.14`, biblioteca estándar únicamente.
- FFmpeg/FFprobe `9.0.1`, compilación Gyan essentials.
- mpg123 `1.33.7`, compilación estática oficial x86-64.
- Go `1.26.4` se usa sólo para compilar reproduciblemente el bootstrap propio.

El ZIP base no contiene binarios de esas dependencias. La primera ejecución los descarga de sus proveedores, verifica los SHA-256 fijados y los conserva en el árbol portátil.

## Flujo, identificación y clasificación

LossyDoctor acepta archivos o directorios, recorre incrementalmente, identifica por contenido y procesa cada candidato de forma independiente. Los formatos V1.1.0 son:

- MPEG Layer II/III, versiones MPEG 1, 2 y 2.5.
- MP4/M4A con exactamente una pista AAC autenticada.
- AAC en transporte ADTS.
- Ogg/Opus.
- Ogg/Vorbis.
- ASF con audio WMA reconocido; la recuperación efectiva se limita a WMA1/WMA2 probados.

La identificación, la validez estructural, la playability y la autoridad de intervención son conceptos separados. MP4 conserva confianza de identificación `MEDIUM`. MP4 multipista es incompatible. El contenido no soportado se omite sin detener la colección.

Los dominios de validez reportados incluyen, según el formato: decodificación, contenedor, header de codec, tablas de muestras, procedencia de unidades o paquetes, límites de demux, línea de tiempo y seekability (capacidad de navegación temporal). Los estados de playability distinguen `PLAYABLE`, `UNPLAYABLE` y evidencia insuficiente conforme al schema vigente.

## Diagnósticos e issue codes

Los diagnósticos conservan códigos de máquina estables y descripciones humanas en español. Los códigos implementados se agrupan así:

- Generales y MPEG: `EXTENSION_CONTENT_MISMATCH`, `ID3V2_MALFORMED`, `MPEG_SYNC_NOT_FOUND`, `MPEG_SYNC_LOSS`, `TRUNCATED_MPEG_FRAME`, `MPEG_TRAILING_ZERO_PADDING`, `MPEG_TRAILING_UNKNOWN_BYTES`, `MPEG_CRC_MISMATCH`, `MPEG_COHERENT_PARAMETER_CONCATENATION`, `MPEG_PARAMETER_CHANGE_AFTER_RESYNC`, `BIT_RESERVOIR_BACKPOINTER_UNAVAILABLE`, `BIT_RESERVOIR_BACKPOINTER_IMPOSSIBLE`, `BIT_RESERVOIR_MAIN_DATA_OVERRUN`, `XING_KIND_MISMATCH`, `XING_BYTE_COUNT_MISMATCH`, `XING_FRAME_COUNT_MISMATCH`, `XING_TOC_MISMATCH`, `XING_MUSIC_LENGTH_MISMATCH`, `XING_AUDIO_CRC_MISMATCH`, `XING_TAG_CRC_MISMATCH`, `VBRI_VERSION_UNSUPPORTED`, `VBRI_BYTE_COUNT_MISMATCH`, `VBRI_FRAME_COUNT_MISMATCH`, `VBRI_TOC_LAYOUT_INVALID`, `VBRI_TOC_FRAME_COVERAGE_MISMATCH`, `VBRI_TOC_MISMATCH`, `DECODER_COMPLETION_DISAGREEMENT`, `DECODER_SAMPLE_COUNT_DISAGREEMENT`.
- MP4/AAC: `MP4_FTYP_MISSING`, `MP4_MOOV_MISSING`, `MP4_MDAT_MISSING`, `MP4_BOX_SIZE_INVALID`, `MP4_CONTAINER_TRAILING_BYTES`, `MP4_TRAILING_BYTES`, `MP4_MOVIE_HEADER_INVALID`, `MP4_MOVIE_DURATION_MISMATCH`, `MP4_MEDIA_DURATION_MISMATCH`, `MP4_AAC_DECODER_CONFIG_INVALID`, `MP4_CHUNK_OFFSET_TABLE_INVALID`, `MP4_CHUNK_OFFSET_OUTSIDE_MDAT`, `MP4_SAMPLE_TO_CHUNK_TABLE_INVALID`, `MP4_SAMPLE_SIZE_TABLE_TRUNCATED`, `MP4_SAMPLE_COUNT_MISMATCH`, `MP4_SAMPLE_DESCRIPTION_TABLE_INVALID`, `MP4_DECODING_TIME_TABLE_INVALID`, `MP4_ACCESS_UNIT_MAPPING_INCOMPLETE`, `MP4_ACCESS_UNIT_OUTSIDE_MDAT`, `MP4_ACCESS_UNIT_OVERLAP`, `MP4_ACCESS_UNIT_TIMELINE_INCOMPLETE`, `MP4_ACCESS_UNIT_DESCRIPTION_INVALID`, `MP4_AAC_DEMUX_ACCESS_UNIT_MISMATCH`, `MP4_EDIT_LIST_INVALID`, `MP4_EDIT_LIST_RATE_UNSUPPORTED`, `MP4_EDIT_LIST_TIMEBASE_INEXACT`, `MP4_EDIT_LIST_MEDIA_RANGE_INVALID`, `MP4_EDIT_LIST_SAMPLE_COUNT_INEXACT`, `MP4_EDIT_LIST_DEMUX_SHIFT_MISMATCH`, `MP4_PRESENTATION_SAMPLE_COUNT_MISMATCH`, `MP4_FRAGMENT_HEADER_INVALID`, `MP4_FRAGMENT_SEQUENCE_INVALID`, `MP4_FRAGMENT_BASE_OR_TRACK_UNSUPPORTED`, `MP4_FRAGMENT_DEFAULTS_INVALID`, `MP4_FRAGMENT_TRACK_RUN_INVALID`, `MP4_FRAGMENT_DATA_OFFSET_INVALID`, `MP4_FRAGMENT_DECODE_TIME_INVALID`, `MP4_FRAGMENT_ACCESS_UNIT_MAPPING_INCOMPLETE`, `MP4_FRAGMENT_PRESENTATION_SAMPLE_COUNT_INEXACT`.
- AAC/ADTS: `AAC_ADTS_SYNC_LOSS`, `AAC_ADTS_HEADER_TRUNCATED`, `AAC_ADTS_TRUNCATED_FRAME`, `AAC_ADTS_TRAILING_BYTES`, `AAC_ADTS_LAYER_NONZERO`, `AAC_ADTS_SAMPLING_INDEX_INVALID`, `AAC_ADTS_FRAME_LENGTH_INVALID`, `AAC_ADTS_PARAMETER_CHANGE`, `AAC_ADTS_PROTECTION_MODE_CHANGE`, `AAC_ADTS_CRC_SYNTAX_TRUNCATED`, `AAC_ADTS_HEADER_CRC_MISMATCH`, `AAC_ADTS_RAW_DATA_BLOCK_POSITION_INVALID`.
- Ogg/Opus/Vorbis: `OGG_SYNC_LOSS`, `OGG_TRUNCATED_PAGE`, `OGG_VERSION_UNSUPPORTED`, `OGG_PAGE_CRC_MISMATCH`, `OGG_PAGE_SEQUENCE_DISCONTINUITY`, `OGG_CONTINUATION_FLAG_INCONSISTENT`, `OGG_INCOMPLETE_PACKET_AT_EOF`, `OPUS_HEAD_INVALID`, `OPUS_TAGS_INVALID`, `OPUS_BOS_HEADER_PAGE_INVALID`, `OPUS_HEAD_PAGE_LAYOUT_INVALID`, `OPUS_TAGS_PAGE_LAYOUT_INVALID`, `OPUS_HEADER_GRANULE_NONZERO`, `OPUS_GRANULE_POSITION_MISSING`, `OPUS_GRANULE_POSITION_NONMONOTONIC`, `OPUS_GRANULE_DELTA_MISMATCH`, `OPUS_AUDIO_PACKET_MALFORMED`, `OPUS_END_TRIM_INVALID`, `OGG_OPUS_EOS_MISSING`, `VORBIS_IDENTIFICATION_HEADER_INVALID`, `VORBIS_COMMENT_HEADER_INVALID`, `VORBIS_SETUP_HEADER_INVALID`, `VORBIS_HEADER_ORDER_INVALID`, `VORBIS_BOS_HEADER_PAGE_INVALID`, `VORBIS_IDENTIFICATION_PAGE_LAYOUT_INVALID`, `VORBIS_SETUP_PAGE_LAYOUT_INVALID`, `VORBIS_HEADER_GRANULE_NONZERO`, `VORBIS_GRANULE_POSITION_MISSING`, `VORBIS_GRANULE_POSITION_NONMONOTONIC`, `VORBIS_GRANULE_DELTA_MISMATCH`, `VORBIS_AUDIO_PACKET_HEADER_INVALID`, `VORBIS_EOS_TRIM_INVALID`, `OGG_VORBIS_EOS_MISSING`.
- ASF/WMA: `ASF_HEADER_OBJECT_INVALID`, `ASF_HEADER_SIZE_INVALID`, `ASF_HEADER_RESERVED_BYTES_INVALID`, `ASF_HEADER_SUBOBJECT_COUNT_MISMATCH`, `ASF_HEADER_SUBOBJECT_SIZE_INVALID`, `ASF_HEADER_BOUNDARY_MISMATCH`, `ASF_FILE_PROPERTIES_OBJECT_COUNT_INVALID`, `ASF_FILE_PROPERTIES_OBJECT_TRUNCATED`, `ASF_FILE_SIZE_MISMATCH`, `ASF_DATA_OBJECT_MISSING_OR_MISPLACED`, `ASF_DATA_OBJECT_TRUNCATED`, `ASF_DATA_FILE_ID_MISMATCH`, `ASF_DATA_PACKET_COUNT_FIELDS_DISAGREE`, `ASF_DATA_PACKET_COUNT_MISMATCH`, `ASF_PACKET_SIZE_FIELDS_INVALID`, `ASF_PARTIAL_DATA_PACKET_AT_END`, `ASF_DATA_PACKET_HEADER_INVALID`, `ASF_PACKET_SEND_TIME_NONMONOTONIC`, `ASF_STREAM_PROPERTIES_MISSING`, `ASF_STREAM_PROPERTIES_LENGTH_INVALID`, `ASF_AUDIO_STREAM_MISSING`, `ASF_ENCRYPTED_AUDIO_UNSUPPORTED`, `ASF_WMA_WAVEFORMATEX_INVALID`, `ASF_MEDIA_OBJECT_FRAGMENT_BOUNDS_INVALID`, `ASF_MEDIA_OBJECT_REPLICATED_DATA_MISMATCH`, `ASF_MEDIA_OBJECT_FRAGMENT_GAP`, `ASF_MEDIA_OBJECT_FRAGMENT_OVERLAP`, `ASF_MEDIA_OBJECT_INCOMPLETE`, `ASF_WMA_DEMUX_MEDIA_OBJECT_MAPPING_MISMATCH`, `ASF_WMA_DEMUX_PTS_PREROLL_MISMATCH`, `ASF_WMA_DEMUX_TIMELINE_DISCONTINUITY`.

## Reparaciones verificadas

La autoridad se limita a casos de reemplazo único y demostrable. Incluye corrección de extensión mediante identidad de alta confianza, ajustes preservacionales ID3, eliminación de padding cero terminal demostrado, actualización coherente de Xing/Info o VBRI, recaptura de páginas Ogg autenticadas, reparaciones MP4 de offset único, duración `mdhd`, cantidad `stsz` y referencia `stsc`, y reparación ADTS del índice de muestreo cuando el resto del stream prueba un único valor.

Cada candidato se escribe en una ruta nueva, se reanaliza, vuelve a decodificarse cuando corresponde y debe demostrar la resolución objetivo sin introducir anomalías materiales. Una reparación verificada que preserva el bitstream tiene precedencia sobre una derivación PCM.

## Recuperaciones sin pérdida

Cuando no existe reparación preservacional publicable pero puede probarse PCM genuino, V1.1.0 puede publicar FLAC verificado: recuperación MPEG completa, segmentada o de regiones demostradas; regiones Opus o Vorbis con procedencia autenticada; sufijos o regiones WMA convergentes; y presentaciones MP4/AAC completas o líneas de tiempo canónicas demostradas.

AAC/ADTS sólo admite recuperación completa lossless después de una reparación de header demostrada. No existe nivel de recuperación parcial ADTS: el estado entre frames impide promover una región posterior sin procedencia PCM exacta.

El silencio cero sólo representa una edición vacía demostrada de la línea de tiempo; nunca se presenta como audio original recuperado. Las regiones cuya ubicación completa no está probada se publican separadas.

## Verificación, publicación y reutilización

Los gates aplicables comprueban SHA-256 del original, diferencias exactas, reanálisis, identidad de esencia, límites y tiempos de demux, decodificación estricta, conteos y hashes PCM, round-trip FLAC y contrato del manifiesto. La mera aceptación de un decodificador no concede autoridad.

Los estados de publicación incluyen `CREATED`, `REUSED`, `REJECTED` y los estados de bloqueo/no aplicabilidad del esquema. Con `publish_verified=false`, la salida se verifica pero no se publica: se informa `VERIFIED_NOT_PUBLISHED`, `output_path` y `manifest_path` son `null`, no se persiste ninguna ruta temporal y no aumenta ningún contador de salidas creadas o reutilizadas.

La publicación usa creación exclusiva, manifiesto y registro transaccional mínimo. Una ejecución posterior reutiliza sólo una salida cuyo contenido y sidecar coinciden exactamente; tras una interrupción completa únicamente estados demostrables y aplica fail closed ante discrepancias.

## Archivos producidos

- Reportes JSON y Markdown por ejecución.
- Copias reparadas independientes con sufijos semánticos.
- Derivados FLAC lossless para PCM genuino probado.
- Sidecars JSON de manifiesto para cada salida publicada.
- Estado local mínimo para recuperar publicaciones interrumpidas.

Los directorios operativos `runtime`, `cache`, `state`, `logs`, `reports` y `temp` se crean bajo demanda y no forman parte del ZIP de código fuente.

## Configuración y dependencias

`config.toml` define modo, recorrido recursivo, symlinks, límites de resincronización y timeout externo; decodificador y formato PCM canónicos; habilitación y publicación de reparación/recuperación; ubicación y formatos de reporte; y versiones de schemas. La configuración es estricta: claves desconocidas o tipos inválidos fallan con un diagnóstico claro.

El código Python no requiere paquetes externos. Las herramientas descargadas conservan sus licencias y procedencia, documentadas en `THIRD_PARTY_NOTICES.md`. El código fuente propio usa `GPL-3.0-or-later`; nombre y marca se rigen por `TRADEMARKS.md`.

## Evidencia ejecutada para V1.1.0

- Suite completa: `482/482` pruebas, resultado `PASS`.
- Corpus: `290` archivos de prueba originales; SHA-256 verificados antes y después, resultado `PASS`.
- Bootstrap: dos compilaciones reproducibles e idénticas con Go fijado, resultado `PASS`.
- Aceptación portátil en Windows: preparación inicial, recuperación segura de preparación interrumpida, reutilización local, archivo individual, carpeta y bootstrap BAT, resultado `PASS`.
- Empaquetado: allowlist de `62` archivos, checksums internos y doble construcción byte-idéntica, resultado `PASS`.
- Regresiones específicas: MP4 permanece `MEDIUM`; ADTS no contiene un nivel parcial; `VERIFIED_NOT_PUBLISHED` no publica ni cuenta salidas; originales inmutables.

## Limitaciones vigentes

- MP4/AAC admite exactamente una pista de audio.
- MP4 conserva autoridad de identificación `MEDIUM`, por lo que no obtiene corrección automática de extensión.
- AAC/ADTS no implementa recuperación parcial.
- El reconocimiento ASF/WMA es más amplio que la recuperación, limitada a WMA1/WMA2 probados.
- No se admiten MPEG Layer I, otros codecs Ogg, MP4 no AAC, reconstrucción aproximada ni transcodificación con pérdida.
- La evidencia de escalabilidad cubre aproximadamente 100.000 candidatos y 1.000.000 de entradas recorridas; no constituye una promesa de duración de ejecución.
