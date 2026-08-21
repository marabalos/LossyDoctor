# LossyDoctor 1.1.2

## Autoridad de validación V1

Antes de una publicación formal deben aprobarse la suite automatizada, los checksums de `PACKAGE_SHA256SUMS.txt` y una prueba del paquete portátil limpio.
La validación manual final en Windows 10 x64 o superior confirma preparación, lanzamiento y análisis de una copia de audio de prueba; nunca autoriza modificar un original.

## Suite completa

Ejecutar con el entorno portátil preparado:

```powershell
runtime\python\cpython-3.12.14-windows-x86_64-none\python.exe tests\run_tests.py
```

El runner valida los SHA-256 declarados para el corpus antes y después de la suite. Los derivados y sidecars junto al corpus no son entradas. Toda prueba que materialice salidas usa copias o temporales.

Los fixtures fuente congelados requeridos por la suite de aceptación están versionados en Git y, según la decisión de provenance del proyecto, son material propio o sintético. Los manifests aportan ground truth SHA-256 independiente, verificado antes y después de la suite; los outputs generados por LossyDoctor permanecen sin trackear. Un clone limpio puede reproducir la suite multimedia una vez preparado el toolchain fijado.

La evidencia distingue implementación, prueba automatizada, resultado esperado independiente y ejecución real. Debe cubrir detección positiva y negativa, límites, clasificación, bloqueo de intervenciones ambiguas, preservación del original, verificación posterior y reutilización idempotente.
