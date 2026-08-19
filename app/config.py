from __future__ import annotations
from pathlib import Path
import tomllib
DEFAULT={
'app':{'mode':'repair_safe_verified','recursive':True,'follow_symlinks':False,'max_resync_scan_bytes':262144,'external_timeout_seconds':300},
'analysis':{'canonical_decoder':'ffmpeg','canonical_pcm_sample_format':'s32le','sha256_chunk_size':1048576},
'repair':{'enabled':True,'publish_verified':True},
'lossless_recovery':{'enabled':True,'publish_verified':True,'flac_bits_per_sample':32},
'reports':{'root':'reports','json':True,'markdown':True},
'schemas':{'config':3,'analysis':3,'report':3,'manifest':3}}
ALLOWED={k:set(v) for k,v in DEFAULT.items()}

def _config_error(key:str,reason:str):raise ValueError(f'CONFIG_ERROR no válido {key}: {reason}')

def _exact_bool(value,key):
    if type(value) is not bool:_config_error(key,'se esperaba un valor booleano')

def _positive_int(value,key):
    if type(value) is not int or value<=0:_config_error(key,'se esperaba un entero positivo')

def _validate(out:dict):
    if out['app']['mode'] not in ('repair_safe_verified','audit_only'):_config_error('app.mode','se esperaba repair_safe_verified o audit_only')
    for key in ('recursive','follow_symlinks'):_exact_bool(out['app'][key],f'app.{key}')
    for key in ('max_resync_scan_bytes','external_timeout_seconds'):_positive_int(out['app'][key],f'app.{key}')
    if out['analysis']['canonical_decoder']!='ffmpeg':_config_error('analysis.canonical_decoder','sólo se admite ffmpeg')
    if out['analysis']['canonical_pcm_sample_format']!='s32le':_config_error('analysis.canonical_pcm_sample_format','sólo se admite s32le')
    _positive_int(out['analysis']['sha256_chunk_size'],'analysis.sha256_chunk_size')
    for section in ('repair','lossless_recovery'):
        for key in ('enabled','publish_verified'):_exact_bool(out[section][key],f'{section}.{key}')
    if type(out['lossless_recovery']['flac_bits_per_sample']) is not int or out['lossless_recovery']['flac_bits_per_sample']!=32:
        _config_error('lossless_recovery.flac_bits_per_sample','sólo se admite el perfil canónico de 32 bits')
    report_root=out['reports']['root']
    if type(report_root) is not str or not report_root.strip():_config_error('reports.root','se esperaba una ruta relativa no vacía')
    report_path=Path(report_root)
    if report_path.is_absolute() or report_path.drive or '..' in report_path.parts:_config_error('reports.root','debe permanecer dentro de la raíz de la aplicación portable')
    for key in ('json','markdown'):_exact_bool(out['reports'][key],f'reports.{key}')
    if not out['reports']['json'] and not out['reports']['markdown']:_config_error('reports','debe habilitarse al menos un formato de reporte')
    for key,expected in DEFAULT['schemas'].items():
        if type(out['schemas'][key]) is not int or out['schemas'][key]!=expected:_config_error(f'schemas.{key}',f'se esperaba {expected}')

def load_config(path:Path):
    out={k:dict(v) for k,v in DEFAULT.items()}
    if path.exists():
        d=tomllib.loads(path.read_text(encoding='utf-8'))
        for sec,vals in d.items():
            if sec not in ALLOWED or not isinstance(vals,dict): raise ValueError(f'CONFIG_ERROR sección desconocida {sec}')
            for k,v in vals.items():
                if k not in ALLOWED[sec]: raise ValueError(f'CONFIG_ERROR clave desconocida {sec}.{k}')
                out[sec][k]=v
    _validate(out)
    return out
