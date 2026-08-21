# Registro de cambios

## 1.1.2

- Endurecimiento correctivo de la autoridad V1 ante casos adversariales, evitando intervenciones cuando la evidencia no permite determinar una reparación única.
- Correcciones de auditoría y clasificación en MPEG, AAC/ADTS, MP4/AAC, Ogg y ASF/WMA, incluido el tratamiento de checksums, estructura degradada y dominios de presentación.
- Corrección de la clasificación residual cuando una reparación segura coexiste con anomalías no resueltas y del tratamiento de `EXTENSION_FIXED`.
- Coherencia de bootstrap, documentación y packaging restaurada para la publicación.
- Sin nuevas familias de formatos, schemas ni dependencias runtime.

## 1.1.1

- Actualización de la documentación pública y READMEs multilingües.
- Limpieza del paquete de distribución para separar archivos de usuario de material de desarrollo.
- Sin cambios en capacidades de auditoría, reparación, recuperación, policy ni schemas.

## 1.1.0

- Primera publicación de LossyDoctor.
- Se normalizan versión, documentación, textos e identificadores internos sin ampliar capacidades.
- La verificación con publicación deshabilitada informa `VERIFIED_NOT_PUBLISHED` y no expone rutas temporales.
- La jerarquía AAC/ADTS refleja únicamente reparación verificada, recuperación completa sin pérdida y reporte.
