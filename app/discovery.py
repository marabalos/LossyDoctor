from __future__ import annotations
from pathlib import Path
import json
import os
PERSIST={'runtime','cache','reports','logs','state','temp','.git','.agents','.codex'}

def _under_app_owned(p:Path,root:Path):
    try: rel=p.resolve().relative_to(root.resolve())
    except Exception:return False
    return bool(rel.parts and rel.parts[0].lower() in PERSIST)

def _generated_output(p:Path):
    if p.name.endswith('.lossydoctor-manifest.json'): return True
    side=Path(str(p)+'.lossydoctor-manifest.json')
    if not side.exists(): return False
    try:
        d=json.loads(side.read_text(encoding='utf-8'))
        return d.get('producer')=='LossyDoctor' and Path(d.get('output_path','')).name==p.name
    except Exception:return False

def iter_discover(inputs:list[str],root:Path,recursive=True,follow_symlinks=False,skipped:list|None=None,metrics:dict|None=None,on_entry=None):
    skipped=[] if skipped is None else skipped;metrics={} if metrics is None else metrics;metrics.setdefault('entries_scanned',0);seen=set()
    for raw in inputs:
        p=Path(raw)
        if not p.exists(): skipped.append({'path':str(p),'reason':'INPUT_NOT_FOUND'});continue
        if p.is_file():
            candidates=[p]
        elif _under_app_owned(p,root):
            continue
        else:
            candidates=_iter_directory_entries(p,root,recursive,follow_symlinks)
        for f in candidates:
            try:
                metrics['entries_scanned']+=1
                if on_entry:on_entry(metrics)
                if not f.is_file() or (f.is_symlink() and not follow_symlinks):continue
                if _under_app_owned(f,root) or _generated_output(f):continue
                k=str(f.resolve()).casefold()
                if k in seen:continue
                seen.add(k);yield f
            except OSError:continue

def _iter_directory_entries(start:Path,root:Path,recursive:bool,follow_symlinks:bool):
    """Entrega entradas incrementalmente sin descender en el estado propio de la aplicación."""
    pending=[start]
    while pending:
        current=pending.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    path=Path(entry.path)
                    try:
                        if entry.is_symlink() and not follow_symlinks:
                            yield path
                            continue
                        if entry.is_dir(follow_symlinks=follow_symlinks):
                            if recursive and not _under_app_owned(path,root):pending.append(path)
                            yield path
                        else:
                            yield path
                    except OSError:
                        continue
        except OSError:
            continue

def discover(inputs:list[str],root:Path,recursive=True,follow_symlinks=False):
    skipped=[];files=list(iter_discover(inputs,root,recursive,follow_symlinks,skipped))
    files.sort(key=lambda x:str(x).casefold());return files,skipped
