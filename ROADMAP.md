# Plan de evolución

## Baseline de publicación

LossyDoctor V1.1.2 consolida la línea V1 estable. `V1_BASELINE.md` conserva la evidencia del baseline V1.1.0; V1.1.1 fue una corrección documental y de distribución, y V1.1.2 incorpora hardening correctivo sobre la autoridad V1 existente, sin agregar familias de formatos, schemas ni dependencias runtime.

## Estado

No queda hardening funcional obligatorio identificado para la línea V1 antes de avanzar hacia V2. El contrato funcional vigente continúa definido por `PRODUCT.md`.

La reproducibilidad de la suite multimedia completa desde un clone limpio sigue pendiente porque el corpus binario local no se publica actualmente en Git. Debe resolverse como gate técnico separado antes de comenzar P0, sin debilitar el ground truth independiente ni incorporar el corpus al ZIP de distribución.

## Próximo objetivo

V2 comienza por cerrar completamente la autoridad incompleta heredada de V1 antes de incorporar nuevos formatos.

Prioridad:

1. P0: AAC/ADTS; MP4/AAC; ASF/WMA; barrido de `DEFERRED`, `AUDIT_ONLY`, `EVIDENCE_ONLY` o autoridad reconocida pero incompleta; revalidación MPEG y Ogg contra el DoD V2.
2. P1: WavPack lossy/hybrid.
3. P2: AC-3 y E-AC-3 audio-only; E-AC-3 Atmos/JOC queda fuera de V2 y pendiente para V3.
4. P3: Microsoft ADPCM e IMA/DVI ADPCM.
5. P4: otras familias ADPCM de circulación real que puedan cumplir el DoD V2.
6. P5: aceptación integral V2, incluyendo positivos, negativos/falsos positivos, casos frontera, adversariales, regresiones, idempotencia, preservación del source y verificación objetiva de outputs.

No hay una decisión de producto bloqueante para comenzar P0. La distribución de P0-P5 en releases públicos intermedios permanece deliberadamente pendiente.
