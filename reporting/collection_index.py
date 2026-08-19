from __future__ import annotations

from pathlib import Path


def write_collection_index(path:Path,run:dict):
    summary=run['summary'];lines=[
        f'# LossyDoctor {run["app_version"]} — índice de corrida',
        '',
        f'- Run ID: `{run["run_id"]}`',
        f'- Iniciada: `{run["started_at"]}`',
        f'- Entradas recorridas: **{summary["entries_scanned"]}**',
        f'- Candidatos descubiertos: **{summary["discovered"]}**',
        f'- Procesados: **{summary["processed"]}**',
        f'- Fallidos: **{summary["failed"]}**',
        '',
        '## Archivos incrementales',
        '',
        f'- Detalle por archivo (JSON Lines): `{run["file_details_ndjson"]}`',
        f'- Eventos de ejecución (JSON Lines): `{run["event_log_ndjson"]}`',
        '',
        'Cada línea JSON representa un registro independiente; estos archivos pueden inspeccionarse mientras la corrida está en curso sin cargar toda la colección en memoria.',
        '',
    ]
    with path.open('x',encoding='utf-8') as output:output.write('\n'.join(lines))
