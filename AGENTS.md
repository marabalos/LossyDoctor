# AGENTS.md

## Autoridad

1. `PRODUCT.md` define el contrato funcional.
2. `V1_BASELINE.md` describe el baseline publicado y su evidencia.
3. `ROADMAP.md` contiene sólo trabajo futuro explícitamente aprobado.
4. El source demuestra qué está implementado.
5. Los tests demuestran cobertura, no intención de producto.

Si código, tests y documentación discrepan, exponé la contradicción. No amplíes scope ni autoridad por inferencia.

## Uso de Codex

Aplicá el gate global de costo: si una tarea puede resolverse por análisis estático, redacción, revisión, preparación de archivos o diseño de tests, devolvela a ChatGPT antes de consumir trabajo de repo/runtime.

Codex se usa sólo para la parte que requiera modificar el repo, ejecutar, testear, compilar, empaquetar o hacer operaciones Git.

Si ChatGPT entrega archivos o un patch final, integralos sin rediseñar la solución.

## Invariantes de LossyDoctor

- `KEEP IT SIMPLE`.
- El media source es inmutable.
- Ningún output sobrescribe un archivo ajeno.
- Ante ambigüedad, fail closed.
- Priorizar bitstream/essence original; después PCM genuino probado.
- No recodificación lossy ni reconstrucción aproximada.
- Silencio/PCM sintético sólo donde `PRODUCT.md` lo autorice y con provenance explícita.
- Un output generado no demuestra una reparación o recovery válida.
- No convertir reconocimiento de parser o decoder externo en nueva autoridad de formato.

## Verificación y tests

Para repair/recovery distinguir, según corresponda:

1. detección;
2. clasificación;
3. eligibility;
4. intervención;
5. verificación objetiva;
6. preservación del source.

Ground truth de corpus debe ser independiente de LossyDoctor. No modificar fixtures para hacer pasar pruebas. No reportar PASS sin ejecución real.

Ejecutar sólo validaciones proporcionales al cambio; no repetir suite completa, bootstrap o aceptación Windows para cambios puramente editoriales salvo riesgo técnico concreto.

## Producto y decisiones humanas

Detener y devolver al usuario cualquier decisión que cambie:

- supported scope;
- detection authority;
- repair/recovery policy;
- garantías de preservación;
- semántica pública;
- compatibilidad o schemas;
- roadmap.

No decidir producto mediante una implementación incidental.

## Git y release

- No descartar cambios ajenos.
- No usar reset/stash/checkout destructivo sin autorización explícita.
- No hacer merge, push, tag ni release salvo instrucción expresa.
- Un commit por etapa.
- El ZIP de release se construye por allowlist desde archivos commiteados.
- Excluir del ZIP samples/corpus, tests, generadores, caches, metadata Git y directorios operativos.
- No inyectar metadata post-commit en archivos del artefacto.

## Idioma

Prosa humana propia: español rioplatense neutral.

Conservar jerga técnica habitual en inglés cuando resulte natural (`lossy`, `lossless`, `bitstream`, `parser`, `demux`, `stream`, `frame`, `payload`, `round-trip`, `runtime`, `build`, `bootstrap`, `hash`, `sidecar`, `allowlist`, `playability`, `seekability`).

No traducir identifiers de máquina, `issue_code`, enums, claves JSON, schemas, nombres de codecs/formatos/herramientas, comandos ni textos legales oficiales.
