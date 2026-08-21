# Dependencias preparadas y avisos

SPDX-License-Identifier: GPL-3.0-or-later

Este inventario cubre las dependencias que el bootstrap prepara desde sus
proveedores en una copia portátil. La publicación oficial V1 no redistribuye
sus ejecutables: la primera preparación los descarga y las ejecuciones
posteriores reutilizan la copia local. LossyDoctor no tiene paquetes Python
externos: `requirements.txt` declara sólo la biblioteca estándar.

| Componente | Versión/distribución | Licencia declarada | Compatibilidad GPL-3.0-or-later | Obligación si se redistribuye en el futuro |
|---|---|---|---|---|
| uv | 0.12.5, Astral | Apache-2.0 **o** MIT | Sí | Conservar la licencia elegida y sus avisos. |
| CPython | 3.12.14, administrado por uv | PSF-2.0 | Sí | Conservar aviso y licencia de Python al redistribuir el entorno de ejecución. |
| FFmpeg | 9.0.1, compilación Gyan essentials | GPL-3.0-or-later | Sí | Incluir el texto GPL, avisos y el código fuente correspondiente a la compilación distribuida, incluida su configuración y componentes estáticos. |
| mpg123 | 1.33.7 static x86-64 | LGPL-2.1 | Sí | Conservar `COPYING.txt`, avisos de Michael Hipp y otros autores, y facilitar el código fuente correspondiente. |

## Evidencia local verificada

- `bootstrap_manifest.json` fija versiones, URL y SHA-256 de cada descarga.
- mpg123 se valida mediante `PINNED_SHA256`, de acuerdo con el manifest y el bootstrap.
- La propia compilación distribuida de FFmpeg informa `--enable-gpl --enable-version3`
  y `ffmpeg -L` declara GPLv3 o posterior.
- El `README.txt` incluido en el archivo de Gyan identifica la compilación GPLv3 y el
  commit de FFmpeg `bf1b838f2a`.
- El `COPYING.txt` incluido en el archivo de mpg123 declara LGPL v2.1 y el
  copyright `1995-2020 Michael Hipp and others`.

## Política de publicación V1

El paquete oficial V1 distribuye el código propio de LossyDoctor y el
bootstrap, pero no los ejecutables de la tabla. El bootstrap los obtiene desde
las URL fijadas en `bootstrap_manifest.json`, verifica sus SHA-256 y los deja
en la copia portátil para reutilización local. Esta política evita presentar
como redistribución propia la descarga inicial de binarios de terceros.

## Condiciones para una eventual redistribución futura de binarios

El paquete V1.1.2 actual no redistribuye los ejecutables de terceros de la
tabla. Si una publicación futura decidiera incluir alguno, antes de distribuir
ese nuevo paquete deberán cumplirse las obligaciones aplicables: avisos,
licencias y, cuando corresponda, disponibilidad del código fuente completo de
la versión binaria efectivamente redistribuida. Para una compilación estática de
FFmpeg esto incluye sus bibliotecas externas habilitadas; el listado de
configuración de la compilación no sustituye esa obligación. Ninguna dependencia
nueva puede incorporarse sin revisar su compatibilidad y documentarla aquí.
