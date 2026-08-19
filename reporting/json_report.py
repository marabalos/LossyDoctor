from __future__ import annotations

from pathlib import Path
import json

from app.utils import json_write_exclusive


def write_json_report(path:Path,run:dict):
    json_write_exclusive(path,run)


class JsonLinesWriter:
    """Agrega registros por archivo sin conservar la colección completa en RAM."""
    def __init__(self,path:Path):
        self.path=path;self._output=path.open('x',encoding='utf-8')
    def write(self,row:dict):
        self._output.write(json.dumps(row,ensure_ascii=False,sort_keys=True)+'\n');self._output.flush()
    def close(self):
        if not self._output.closed:self._output.close()
