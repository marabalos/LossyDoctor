# LossyDoctor

## Propósito

LossyDoctor audita archivos de audio lossy (con pérdida) para detectar anomalías estructurales, de bitstream, línea de tiempo y decodificación que puedan demostrarse objetivamente.

Cuando existe una única corrección suficientemente probada que no requiere recodificación lossy, puede publicar un archivo reparado e independientemente verificado. Cuando el bitstream no puede repararse con seguridad pero el PCM genuino puede establecerse exactamente, puede publicar un derivado de preservación lossless (sin pérdida). Ante incertidumbre, informa el límite y no adivina ni reconstruye audio.

## Flujo y entradas

La aplicación portátil para Windows x86-64 procesa archivos individuales o directorios, incluida la exploración recursiva cuando está habilitada. La identidad por contenido prevalece sobre la extensión. El contenido no soportado se omite; los enlaces no se siguen salvo configuración explícita. Las carpetas propias de ejecución, estado, reportes y artefactos verificados de LossyDoctor no se reprocesan como entradas.

Cada archivo se analiza de forma independiente: un fallo no interrumpe la colección. El proceso distingue identificación, consistencia estructural, evidencia de decodificadores, playability (capacidad de reproducción), anomalías, posibilidad de reparación, posibilidad de preservar PCM genuino y verificación final.

## Salidas y seguridad

LossyDoctor puede producir reportes, reparaciones de bitstream o contenedor, copias byte-idénticas con extensión correcta, derivaciones FLAC de PCM genuino y manifiestos de publicación. El original nunca se modifica, renombra, trunca, borra ni sobrescribe. Ningún derivado sobrescribe un archivo ajeno; la reutilización sólo es válida si artefacto y contrato de publicación coinciden exactamente.

La publicación distingue creación, reutilización exacta, rechazo, bloqueo/no aplicabilidad y análisis sin intervención. Crear un archivo no demuestra que sea válido: toda intervención se vuelve a analizar y debe cumplir sus verificaciones aplicables, incluida la inmutabilidad del original.

## Principios de preservación

- Aplicar fail closed (no intervenir ante ambigüedad) cuando existan explicaciones o reparaciones materialmente distintas.
- Priorizar una reparación que conserve la esencia comprimida antes de exportar PCM.
- No realizar una nueva codificación lossy como reparación ni reconstruir audio aproximado.
- Usar PCM cero sólo como marcador explícito de una línea de tiempo demostrada; nunca como audio original recuperado.
- Mantener regiones de PCM genuino como partes separadas cuando su ubicación temporal completa no esté probada.
- Conservar evidencia suficiente para distinguir bytes originales, PCM genuino, silencio explícitamente sintetizado e intervalos no disponibles.

## Alcance V1

V1 admite MPEG Layer II y III (versiones 1, 2 y 2.5), Ogg/Opus, Ogg/Vorbis, ASF/WMA reconocido por el parser, MP4 con exactamente una pista AAC autenticada y AAC/ADTS. La autoridad de reparación o recuperación es siempre más estrecha que la detección y sólo cubre los casos V1 probados.

Quedan fuera de alcance MPEG Layer I, otros códecs Ogg, MP4 multipista o no AAC, recodificación lossy, reconstrucción aproximada, recuperación MP4 basada sólo en conjeturas de decodificador y recuperación ADTS parcial no implementada. Detectar una firma no concede autoridad de intervención.

## Verificación, escala y portabilidad

Las verificaciones aplicables incluyen hash del original, reanálisis estructural, resolución de la anomalía objetivo, ausencia de anomalías materiales nuevas, decodificación/demux, hashes de esencia y PCM, conteos de muestras, igualdad FLAC-PCM y coincidencia del manifiesto. Un decodificador que produzca audio no basta por sí solo.

El procesamiento y los reportes de colecciones son incrementales. V1 cuenta con evidencia de orquestación para aproximadamente 100.000 candidatos y 1.000.000 de entradas recorridas, sin promesa de tiempo de ejecución ni límite total de bytes.

El producto es portátil en Windows 10 o posterior x86-64. El bootstrap prepara localmente el toolchain fijado, verifica sus hashes y lo reutiliza sin requerir instalaciones administradas del sistema.

## Configuración, reportes y licencia

La configuración es estricta y versionada; claves desconocidas fallan cuando podrían ocultar un error. `audit_only` nunca publica reparaciones o recuperaciones. Los reportes conservan identificación, hallazgos, evidencia, decisiones, verificaciones, resultados y fallos por archivo.

El código fuente propio se distribuye bajo GPL-3.0-or-later. El nombre y las marcas LossyDoctor se rigen por separado. Los avisos y licencias de terceros deben conservarse.
