# LossyDoctor

[Español 🇦🇷](README.es.md) | [中文 🇨🇳](README.zh-CN.md) | [Русский 🇷🇺](README.ru.md) | [हिन्दी 🇮🇳](README.hi.md) | [English 🇺🇸](README.md)

---

## Primero: qué significa *lossy*

MP3, AAC, Vorbis, Opus y otros formatos *lossy* reducen el tamaño descartando información de audio de manera irreversible.

Por ese motivo, **un archivo lossy no debería utilizarse NUNCA como fuente maestra, formato de preservación ni intermediario de intercambio**. Recodificarlo a otro formato lossy sólo agrega una nueva generación de pérdidas. Recodificarlo simplemente a un formato lossless sólo agrega tamaño, sin recuperar los datos ya perdidos.

Sin embargo, una enorme cantidad de música, grabaciones, emisiones, bootlegs, archivos históricos y material distribuido digitalmente **sólo existe o sólo circula en formatos lossy**.

LossyDoctor existe para esos casos.

**Es una herramienta pensada para colecciones lossy, pero con mentalidad de audiófilo y una metodología conservadora: preservar al máximo lo que todavía existe, sin degradarlo nuevamente para “repararlo”.**

## Qué es LossyDoctor

LossyDoctor audita archivos de audio con pérdida para detectar corrupción, anomalías estructurales, problemas de bitstream, línea de tiempo y decodificación.

Su principio fundamental es simple:

> **Nunca recodificar con pérdida, ni siquiera para reparar.**

Si el archivo puede corregirse conservando su audio comprimido original, LossyDoctor genera una copia reparada y la verifica.

Si eso ya no es posible pero todavía puede demostrarse exactamente PCM genuino recuperable, puede preservarlo como **FLAC lossless**. El archivo resultante será más grande, pero no agrega una nueva pérdida: conserva exactamente ese PCM recuperado en un formato reproducible y apto para preservación.

El original permanece siempre intacto.

## Qué hace

- Identifica el formato por su contenido, no sólo por la extensión.
- Audita estructura, bitstream, timeline y decodificación.
- Detecta archivos corruptos aunque todavía puedan reproducirse.
- Repara únicamente cuando existe una corrección demostrable.
- Prioriza siempre la conservación del bitstream comprimido original.
- Verifica nuevamente cada archivo reparado.
- Puede preservar PCM genuino recuperable como FLAC cuando reparar el original ya no es seguro.
- Procesa archivos individuales o colecciones completas.
- Nunca modifica ni sobrescribe el archivo fuente.

La versión 1.1.1 cubre, dentro de la autoridad comprobada para cada familia, MPEG Layer II/III, AAC/ADTS, MP4/AAC de una sola pista, Ogg/Opus, Ogg/Vorbis y ASF/WMA.

## Instalación y conexión a Internet

La primera preparación de LossyDoctor necesita conexión a Internet porque `LossyDoctorBootstrap.exe` descarga las dependencias fijadas por el proyecto. Las ejecuciones posteriores reutilizan esos componentes locales y normalmente no necesitan volver a descargarlos.

Para preparar las dependencias sin iniciar un análisis, ejecutar:

`LossyDoctorBootstrap.exe --prepare-only`

## Qué NO hace

- **No recodifica MP3 a MP3, AAC a AAC ni realiza ninguna reparación mediante nueva compresión lossy.**
- No mejora la calidad sonora perdida durante la codificación original.
- No inventa ni reconstruye audio cuya existencia no pueda demostrarse.
- No considera que un archivo esté sano simplemente porque un decoder consiga reproducirlo.
- No promete reparar cualquier corrupción.
- No convierte automáticamente todo archivo problemático a FLAC: la recuperación lossless es una alternativa de preservación cuando conservar correctamente el bitstream original ya no es posible.

## ¿Qué clase de problemas puede encontrar?

Un archivo puede contener audio todavía recuperable y, sin embargo, presentar glitches, reproducción incompleta, duración incorrecta, problemas de búsqueda o incluso resultar ilegible para determinados reproductores.

Entre otros casos, LossyDoctor puede detectar:

- frames MPEG truncados o pérdida de sincronización;
- headers, índices Xing/Info o VBRI inconsistentes;
- bytes extraños o padding incorrecto;
- errores de CRC;
- páginas Ogg corruptas o fuera de secuencia;
- problemas de timestamps o continuidad;
- tablas, offsets o duraciones incorrectas en MP4/AAC;
- headers ADTS inconsistentes;
- paquetes o fragmentos ASF/WMA incompletos.

**Detectar el problema no significa automáticamente que pueda repararse.**

Cuando existe una única reparación segura, LossyDoctor puede aplicarla. Cuando el bitstream ya no puede preservarse pero existe PCM genuino demostrable, puede preservar ese audio sin introducir una nueva pérdida. Cuando ninguna de las dos cosas puede probarse, informa el daño y no fabrica una solución.

## Casos de uso

**Colecciones antiguas**  
Auditar miles de MP3, AAC, WMA, Vorbis u Opus acumulados durante años y provenientes de fuentes diversas.

**Archivos con glitches**  
Determinar si un salto, corte o fallo de reproducción corresponde a una anomalía estructural reparable o a audio realmente perdido.

**Archivos que ya no reproducen correctamente**  
Intentar conservar el bitstream original cuando existe una reparación demostrable o, en casos extremos, rescatar el PCM genuino que todavía puede establecerse con certeza.

**Preservación**  
Verificar material lossy antes de incorporarlo a una colección permanente, sin degradarlo mediante otra generación de compresión.

## Una sola herramienta para todo el proceso

Las herramientas más comunes de verificación de audio permiten detectar muchos problemas estructurales o de decodificación, pero suelen concentrarse en funciones, formatos o etapas concretas del proceso. **LossyDoctor busca centralizar ese trabajo en una sola herramienta**: realiza las comprobaciones habituales y suma verificaciones adicionales, analiza si una anomalía puede corregirse de forma segura, aplica reparaciones únicamente cuando existe un procedimiento expresamente soportado, verifica objetivamente el resultado y preserva siempre intacto el archivo original. Cuando la reparación directa no es posible pero el contenido de audio puede recuperarse de manera fiable, también contempla su recuperación a un formato lossless. El objetivo es que el usuario pueda auditar, diagnosticar y resolver estos casos mediante un flujo único y consistente, sin tener que recurrir a distintas herramientas y procesos para completar la misma tarea.

LossyDoctor no intenta ser el reparador que más archivos modifica.

Su objetivo es otro:

> **Conservar todo lo auténtico que todavía exista en un archivo lossy, sin introducir una nueva generación de pérdida. Reparar cuando puede demostrar la reparación. Recuperar a lossless cuando ésa es la única alternativa segura. Y no tocar lo que no puede determinar con certeza.**
