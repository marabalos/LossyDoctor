# LossyDoctor 1.1.0

LossyDoctor audita archivos de audio lossy (con pérdida) y sólo publica una copia reparada o recuperada cuando puede demostrar que preserva los bytes o PCM genuinos. Los archivos originales nunca se modifican ni se sobrescriben.

V1 cubre MPEG Layer II/III, MP4/AAC de una sola pista, AAC/ADTS, Ogg/Opus, Ogg/Vorbis y ASF/WMA dentro de la autoridad demostrada para cada formato. Ante un caso ambiguo, informa el hallazgo y no fabrica una salida aproximada. Los archivos MP4/AAC con más de una pista de audio son incompatibles con V1.

## Uso

1. Extraé el ZIP completo en una carpeta local de Windows 10 o posterior, x86-64.
2. Arrastrá uno o más archivos o carpetas sobre `LossyDoctor.bat`.
3. LossyDoctor prepara su toolchain local si hace falta, analiza cada archivo de forma independiente y deja los reportes bajo `reports/`.
4. Cuando una reparación o recuperación supera todas las verificaciones aplicables, el derivado se publica junto al archivo original con un nombre distinto y su sidecar de manifiesto. El source permanece intacto.

También podés invocarlo desde una consola:

```bat
LossyDoctor.bat "C:\ruta\al\archivo-o-carpeta"
```

La configuración predeterminada usa `repair_safe_verified`: permite publicar únicamente reparaciones o recuperaciones verificadas. Para ejecutar sólo auditoría y reportes, configurá:

```toml
[app]
mode = "audit_only"
```

en `config.toml`.

## Primera ejecución y conectividad

La primera ejecución necesita conexión a Internet para preparar, dentro de la misma carpeta de LossyDoctor, las versiones exactas de sus componentes de apoyo. El bootstrap verifica cada descarga con el SHA-256 fijado (`PINNED_SHA256`) antes de usarla. No busca versiones `latest` ni instala componentes fuera de esa copia portátil.

Una vez preparada la carpeta, las ejecuciones posteriores reutilizan esos componentes locales y no necesitan volver a descargarlos mientras la copia se mantenga intacta. Para preparar el entorno sin analizar archivos, ejecutá:

```bat
LossyDoctorBootstrap.exe --prepare-only
```

Si la primera preparación se interrumpe o no hay conexión, volvé a ejecutar LossyDoctor normalmente cuando la conexión esté disponible. No se modifica ningún archivo de audio original durante la preparación.

## Documentación

- [PRODUCT.md](PRODUCT.md): contrato funcional vigente.
- [V1_BASELINE.md](V1_BASELINE.md): evidencia técnica y limitaciones verificadas de V1.1.0.
- [ROADMAP.md](ROADMAP.md): baseline publicado y próximos objetivos aprobados.
- [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md): dependencias y avisos de terceros.
- [LICENSE](LICENSE): licencia del código fuente propio.
- [TRADEMARKS.md](TRADEMARKS.md): condiciones aplicables al nombre y las marcas LossyDoctor.
