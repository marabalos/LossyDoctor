from __future__ import annotations
from pathlib import Path
from datetime import datetime
import hashlib, json, os, re


def local_now(now=None):
    """Devuelve fecha y hora con zona local del sistema operativo."""
    dt = now if now is not None else datetime.now().astimezone()
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt


def local_iso(now=None): return local_now(now).isoformat()
def utc_now(): return local_iso()  # compatibility alias retained for older call sites
def run_id(now=None): return local_now(now).strftime('%Y%m%d_%H%M%S_%f%z')

def sha256_file(path:Path,chunk=1024*1024):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(chunk),b''): h.update(b)
    return h.hexdigest()

def sha256_bytes(b:bytes): return hashlib.sha256(b).hexdigest()
def event(kind,result,**kw): return {'type':kind,'result':result,'time':local_iso(),**kw}

def json_write(path:Path,obj):
    path.parent.mkdir(parents=True,exist_ok=True); tmp=path.with_suffix(path.suffix+'.part')
    tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2,sort_keys=True),encoding='utf-8'); os.replace(tmp,path)

def collision_path(path:Path):
    if not path.exists(): return path
    stem=path.stem; suf=path.suffix
    m=re.match(r'^(.*)\[([^\[\]]+)\]$',stem)
    for n in range(2,10000):
        candidate=f'{m.group(1)}[{m.group(2)} {n}]{suf}' if m else f'{stem} {n}{suf}'
        p=path.with_name(candidate)
        if not p.exists(): return p
    raise RuntimeError('OUTPUT_COLLISION_EXHAUSTED')

def _collision_variants(path:Path):
    stem=path.stem; suf=path.suffix; m=re.match(r'^(.*)\[([^\[\]]+)\]$',stem)
    yield path
    for n in range(2,10000):
        candidate=f'{m.group(1)}[{m.group(2)} {n}]{suf}' if m else f'{stem} {n}{suf}'
        yield path.with_name(candidate)

def json_write_exclusive(path:Path,obj):
    path.parent.mkdir(parents=True,exist_ok=True)
    payload=json.dumps(obj,ensure_ascii=False,indent=2,sort_keys=True).encode('utf-8')
    with path.open('xb') as f:
        f.write(payload);f.flush();os.fsync(f.fileno())
