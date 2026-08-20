from __future__ import annotations
from pathlib import Path
import tempfile, shutil, json, hashlib
from formats.mpeg import parse_header, analyze as analyze_mpeg
from app.external import decode, ffprobe, decode_to_raw_file, canonical_pcm_profile
from app.publication import publish_or_preview_with_manifest
from app.utils import sha256_file
from app.version import APP_VERSION, MANIFEST_SCHEMA

XING_CODES={
    'XING_FRAME_COUNT_MISMATCH','XING_BYTE_COUNT_MISMATCH','XING_KIND_MISMATCH',
    'XING_TOC_MISMATCH','XING_MUSIC_LENGTH_MISMATCH','XING_AUDIO_CRC_MISMATCH',
    'XING_TAG_CRC_MISMATCH'
}
VBRI_REPAIR_CODES={'VBRI_FRAME_COUNT_MISMATCH','VBRI_BYTE_COUNT_MISMATCH','VBRI_TOC_MISMATCH'}
VBRI_BLOCK_CODES={'VBRI_VERSION_UNSUPPORTED','VBRI_TOC_LAYOUT_INVALID','VBRI_TOC_FRAME_COVERAGE_MISMATCH'}
VBRI_CODES=VBRI_REPAIR_CODES|VBRI_BLOCK_CODES
DAMAGED_CODES={'ID3V2_MALFORMED','MPEG_SYNC_LOSS','TRUNCATED_MPEG_FRAME','MPEG_SYNC_NOT_FOUND','BIT_RESERVOIR_BACKPOINTER_IMPOSSIBLE'}

SPECS={
'LOSSLESS_SINGLE_BIT_HEADER_REPAIR':{
    'id':'LOSSLESS_SINGLE_BIT_HEADER_REPAIR','class':'LOSSLESS_VERIFIED','risk':'SAFE_IF_VERIFIED','audio_recoding':False,
    'resolves':['MPEG_SYNC_LOSS']},
'DROP_TRUNCATED_TAIL_NO_VBR_METADATA':{
    'id':'DROP_TRUNCATED_TAIL_NO_VBR_METADATA','class':'LOSSLESS_VERIFIED','risk':'SAFE_IF_VERIFIED','audio_recoding':False,
    'resolves':['TRUNCATED_MPEG_FRAME']},
'REPAIR_ID3V24_SIZE_TO_VERIFIED_BOUNDARY':{
    'id':'REPAIR_ID3V24_SIZE_TO_VERIFIED_BOUNDARY','class':'LOSSLESS_VERIFIED','risk':'SAFE_IF_VERIFIED','audio_recoding':False,
    'resolves':['ID3V2_MALFORMED']},
'DROP_CONFIRMED_TERMINAL_ZERO_PADDING':{
    'id':'DROP_CONFIRMED_TERMINAL_ZERO_PADDING','class':'LOSSLESS_VERIFIED','risk':'SAFE_IF_VERIFIED','audio_recoding':False,
    'resolves':['MPEG_TRAILING_ZERO_PADDING']},
'REFRESH_XING_METADATA':{
    'id':'REFRESH_XING_METADATA','class':'LOSSLESS_VERIFIED','risk':'SAFE_IF_VERIFIED','audio_recoding':False,'implemented':True,
    'applies_to':['MPEG_LAYER_III_FFMPEG_XING_PROFILE'],
    'prerequisites':['CONTIGUOUS_MPEG_STREAM','FFMPEG_EXTENDED_XING_PROFILE','XING_COUPLED_FIELDS_RECOMPUTABLE'],
    'resolves':sorted(XING_CODES),
    'verification_required':['FULL_RESCAN','FULL_DECODE','PCM_IDENTICAL','AUDIO_PAYLOAD_IDENTICAL','FRAME_COUNT','SEEK_METADATA_CONSISTENT','SOURCE_SHA_UNCHANGED']},
'REFRESH_VBRI_METADATA':{
    'id':'REFRESH_VBRI_METADATA','class':'LOSSLESS_VERIFIED','risk':'SAFE_IF_VERIFIED','audio_recoding':False,'implemented':True,
    'applies_to':['MPEG_LAYER_III_VBRI_V1'],
    'prerequisites':['CONTIGUOUS_MPEG_STREAM','VBRI_V1_LAYOUT_VALID','VBRI_TOC_EXACTLY_REPRESENTABLE'],
    'resolves':sorted(VBRI_REPAIR_CODES),
    'verification_required':['FULL_RESCAN','FULL_DECODE','PCM_IDENTICAL','AUDIO_PAYLOAD_IDENTICAL','FRAME_COUNT','VBRI_TABLE_COVERAGE','SOURCE_SHA_UNCHANGED']},
'FIX_EXTENSION_BYTE_IDENTICAL':{
    'id':'FIX_EXTENSION_BYTE_IDENTICAL','class':'LOSSLESS_STRUCTURAL','risk':'SAFE_IF_VERIFIED','audio_recoding':False,
    'resolves':['EXTENSION_CONTENT_MISMATCH']},
'CAUSAL_REPAIR_CHAIN':{
    'id':'CAUSAL_REPAIR_CHAIN','class':'LOSSLESS_VERIFIED','risk':'SAFE_IF_VERIFIED','audio_recoding':False},
'OGG_RECAPTURE_VALID_PAGES_DROP_EXTRANEOUS_BYTES':{
    'id':'OGG_RECAPTURE_VALID_PAGES_DROP_EXTRANEOUS_BYTES','class':'LOSSLESS_STRUCTURAL','risk':'SAFE_IF_VERIFIED','audio_recoding':False,
    'resolves':['OGG_SYNC_LOSS']},
'REWRITE_OGG_PAGE_CRC':{
    'id':'REWRITE_OGG_PAGE_CRC','class':'UNAUTHORIZED_AMBIGUOUS','risk':'BLOCKED','audio_recoding':False,
    'resolves':['OGG_PAGE_CRC_MISMATCH']},
'RENUMBER_OGG_PAGE_SEQUENCE':{
    'id':'RENUMBER_OGG_PAGE_SEQUENCE','class':'UNAUTHORIZED_AMBIGUOUS','risk':'BLOCKED','audio_recoding':False,
    'resolves':['OGG_PAGE_SEQUENCE_DISCONTINUITY']}
}


def _issue_codes(mpeg:dict): return [i.code for i in mpeg.get('issues',[])]
def _damaged_codes(mpeg:dict): return {i.code for i in mpeg.get('issues',[]) if getattr(i,'integrity',None)=='DAMAGED' or i.code in DAMAGED_CODES}


def _single_bit_candidates(data:bytes,mpeg:dict):
    out=[]; facts=mpeg['facts']; sig=(facts['mpeg_version'],facts['layer'],facts['sample_rate'])
    for g in mpeg['gaps']:
        if g['byte_end']-g['byte_start']<4:continue
        off=g['byte_start']; orig=bytearray(data[off:off+4])
        for byte_i in range(4):
            for bit in range(8):
                q=bytearray(orig);q[byte_i]^=1<<bit;h=parse_header(q)
                if not h or h['free_format'] or (h['version'],h['layer'],h['sample_rate'])!=sig:continue
                if h['frame_length']!=g['byte_end']-g['byte_start']:continue
                out.append({'kind':'replace_mpeg_header','offset':off,'original_hex':bytes(orig).hex(),'replacement_hex':bytes(q).hex(),'byte_index':byte_i,'bit_index':bit})
    uniq={(x['offset'],x['replacement_hex']):x for x in out}
    return list(uniq.values())


def _to_synchsafe(v:int):
    if v<0 or v>0x0fffffff: raise ValueError('synchsafe range')
    return bytes(((v>>21)&0x7f,(v>>14)&0x7f,(v>>7)&0x7f,v&0x7f))


def _infer_id3v24_boundary(data:bytes,mpeg:dict):
    md=mpeg.get('metadata',{}).get('id3v2') or {}
    boundary=mpeg.get('facts',{}).get('first_audio_offset')
    if not md.get('present') or not md.get('malformed') or md.get('version_major')!=4 or boundary is None:return None
    if len(data)<10 or boundary<10 or boundary>len(data) or data[:3]!=b'ID3':return None
    # Alcance conservador: sin desincronización, header extendido, flag experimental ni footer.
    if data[5]!=0:return None
    pos=10; frames=0
    while pos<boundary:
        rem=data[pos:boundary]
        if not any(rem):
            padding=len(rem);pos=boundary;break
        if len(rem)<10:return None
        fid=rem[:4]
        if not all((65<=c<=90) or (48<=c<=57) for c in fid):return None
        szb=rem[4:8]
        if any(c&0x80 for c in szb):return None
        sz=(szb[0]<<21)|(szb[1]<<14)|(szb[2]<<7)|szb[3]
        # Rechaza flags de frame no admitidos en la ruta de demostración.
        if rem[8:10]!=b'\0\0':return None
        end=pos+10+sz
        if end>boundary:return None
        frames+=1;pos=end
    else:padding=0
    if pos!=boundary or frames<1:return None
    payload=boundary-10
    return {'frame_count':frames,'padding_bytes':padding,'inferred_payload_size':payload,'inferred_total_size':boundary,'mpeg_boundary':boundary,'replacement_hex':_to_synchsafe(payload).hex()}


def _xing_plan(mpeg:dict,issues:set[str]):
    if not (issues & XING_CODES):return None
    facts=mpeg.get('facts',{}); x=((facts.get('vbr_header') or {}).get('xing') or {})
    spec=SPECS['REFRESH_XING_METADATA']
    checksum_only=bool('XING_AUDIO_CRC_MISMATCH' in issues and not (issues-{'XING_AUDIO_CRC_MISMATCH','XING_TAG_CRC_MISMATCH'}))
    if 'MPEG_UNEXPECTED_STREAM_HEADER' in issues or checksum_only:
        return {'spec':spec,'status':'BLOCKED','reason':'la metadata Xing/Info global queda ambigua por otro header global posterior o por un checksum que no localiza la corrupción','actions':[]}
    if mpeg.get('codec')!='mp3' or facts.get('layer')!=3:
        return {'spec':spec,'status':'BLOCKED','reason':'la actualización Xing/Info sólo está implementada para MPEG Layer III','actions':[]}
    if not x:
        return {'spec':spec,'status':'BLOCKED','reason':'existe una discrepancia Xing/Info, pero la estructura Xing no puede modelarse de forma segura','actions':[]}
    if not x.get('ffmpeg_extended_profile'):
        return {'spec':spec,'status':'BLOCKED','reason':'la actualización coherente automática se limita a un perfil Xing/Info extendido de FFmpeg reconocible; las disposiciones de encoder desconocidas quedan sólo para reporte','actions':[]}
    if facts.get('gaps') or facts.get('truncated_final_frame') or facts.get('trailing_region') is not None:
        return {'spec':spec,'status':'BLOCKED','reason':'Xing/Info no puede reconstruirse desde un stream MPEG dañado, truncado o estructuralmente ambiguo','actions':[]}
    if 'XING_FRAME_COUNT_MISMATCH' in issues:
        delay=x.get('encoder_delay_samples'); pad=x.get('end_padding_samples'); observed=facts.get('audio_frame_count_observed'); spf=facts.get('samples_per_frame')
        # Prueba independiente con semántica gapless de FFmpeg/LAME:
        # salto inicial del demux = delay + 529 y primer descarte = frames*spf - pad + 529.
        # Exige padding final suficiente para que el descarte quede dentro del stream
        # físico decodificado y evita casos límite ambiguos con poco padding.
        if not x.get('gapless_fields_trusted') or not all(isinstance(v,int) for v in (delay,pad,observed,spf)):
            return {'spec':spec,'status':'BLOCKED','reason':'la reparación de cantidad de frames Xing requiere campos confiables de delay/padding FFmpeg/LAME y una cantidad física de frames observada','actions':[]}
        if pad < 529 or observed <= 0 or spf <= 0 or observed*spf <= delay+pad:
            return {'spec':spec,'status':'BLOCKED','reason':'la reparación de cantidad de frames Xing queda fuera del alcance de ventana sin brechas demostrable independientemente (el padding final debe ser al menos 529 muestras y la ventana reconstruida debe ser positiva)','actions':[]}

    exp=x.get('expected')
    if not exp or exp.get('toc') is None:
        return {'spec':spec,'status':'BLOCKED','reason':'los campos Xing/Info acoplados completos no pueden recalcularse de forma determinista','actions':[]}
    # Todos los campos requeridos se conocen por el mapa coherente y el perfil FFmpeg.
    needed=('kind','frames','bytes','toc','music_length','music_crc','tag_crc')
    if any(k not in exp for k in needed):
        return {'spec':spec,'status':'BLOCKED','reason':'uno o más campos Xing/Info acoplados no pueden derivarse con certeza','actions':[]}
    frame_count_proof='XING_FRAME_COUNT_MISMATCH' in issues
    reason=('el bloque Xing/Info contiguo y completo de perfil FFmpeg puede recalcularse coherentemente; corregir la cantidad de frames exige además una prueba de PCM físico independiente de metadata y de ventana estructural sin brechas' if frame_count_proof else 'el bloque Xing/Info contiguo y completo de perfil FFmpeg puede recalcularse coherentemente y verificarse contra PCM decodificado idéntico')
    return {'spec':spec,'status':'ELIGIBLE','reason':reason,'actions':[{'kind':'refresh_xing_ffmpeg_profile','expected':{k:exp[k] for k in needed},'xing_offset_in_frame':x['xing_offset_in_frame'],'first_audio_offset':facts['first_audio_offset'],'requires_structural_presentation_proof':frame_count_proof}]}


def _vbri_plan(mpeg:dict,issues:set[str]):
    if not (issues & VBRI_CODES):return None
    facts=mpeg.get('facts',{});v=((facts.get('vbr_header') or {}).get('vbri') or {})
    spec=SPECS['REFRESH_VBRI_METADATA']
    if 'MPEG_UNEXPECTED_STREAM_HEADER' in issues:
        return {'spec':spec,'status':'BLOCKED','reason':'VBRI global queda ambiguo por otro header global de stream posterior','actions':[]}
    if mpeg.get('codec')!='mp3' or facts.get('layer')!=3:
        return {'spec':spec,'status':'BLOCKED','reason':'la actualización VBRI sólo está implementada para MPEG Layer III','actions':[]}
    if not v:
        return {'spec':spec,'status':'BLOCKED','reason':'existe una anomalía VBRI, pero la estructura VBRI no puede modelarse de forma segura','actions':[]}
    if v.get('version')!=1:
        return {'spec':spec,'status':'BLOCKED','reason':'la actualización VBRI automática se limita a la versión 1','actions':[]}
    if not v.get('layout_valid') or (issues & VBRI_BLOCK_CODES):
        return {'spec':spec,'status':'BLOCKED','reason':'la disposición/cobertura de la tabla VBRI v1 no puede representarse exactamente mediante el mapa verificado de límites de frames MPEG','actions':[]}
    if facts.get('gaps') or facts.get('truncated_final_frame') or facts.get('trailing_region') is not None:
        return {'spec':spec,'status':'BLOCKED','reason':'VBRI no puede reconstruirse desde un stream MPEG dañado, truncado o estructuralmente ambiguo','actions':[]}
    exp=v.get('expected') or {}
    needed=('frames','bytes','toc')
    if not exp.get('layout_representable') or any(k not in exp for k in needed):
        return {'spec':spec,'status':'BLOCKED','reason':'los contadores o la tabla VBRI no pueden derivarse exactamente del mapa coherente de frames','actions':[]}
    toc=exp.get('toc') or [];entry_size=v.get('toc_entry_size');scale=v.get('toc_scale')
    if not isinstance(entry_size,int) or entry_size not in (1,2,3,4) or not isinstance(scale,int) or scale<=0 or len(toc)!=v.get('toc_entries'):
        return {'spec':spec,'status':'BLOCKED','reason':'las dimensiones de la tabla VBRI quedan fuera del alcance verificado de reescritura v1','actions':[]}
    lim=(1<<(8*entry_size))-1
    if any((not isinstance(x,int)) or x<0 or x>lim for x in toc):
        return {'spec':spec,'status':'BLOCKED','reason':'una o más entradas de la tabla VBRI no pueden codificarse sin pérdida con el ancho declarado','actions':[]}
    return {'spec':spec,'status':'ELIGIBLE','reason':'los contadores y la tabla de búsqueda VBRI v1 contiguos y completos pueden recalcularse exactamente desde límites verificados de frames MPEG y validarse contra PCM/payload de audio idénticos',
            'actions':[{'kind':'refresh_vbri_v1','expected':{k:exp[k] for k in needed},'vbri_offset_in_frame':v['vbri_offset_in_frame'],'first_audio_offset':facts['first_audio_offset'],'toc_entry_size':entry_size,'toc_scale':scale,'toc_entries':v['toc_entries'],'toc_frames_per_entry':v['toc_frames_per_entry']}]}


def plan(path:Path,mpeg:dict):
    issues=set(_issue_codes(mpeg)); plans=[]
    # El daño raíz de metadatos se trata antes que las reparaciones secundarias de frames.
    if 'ID3V2_MALFORMED' in issues:
        proof=_infer_id3v24_boundary(mpeg['data'],mpeg)
        if proof:
            plans.append({'spec':SPECS['REPAIR_ID3V24_SIZE_TO_VERIFIED_BOUNDARY'],'status':'ELIGIBLE','reason':'una secuencia válida de frames ID3v2.4 más padding cero termina exactamente en el límite MPEG coherente','actions':[{'kind':'replace_id3v24_size','start':6,'end':10,**proof}]})
        else:
            plans.append({'spec':SPECS['REPAIR_ID3V24_SIZE_TO_VERIFIED_BOUNDARY'],'status':'BLOCKED','reason':'el límite ID3v2.4 no puede demostrarse sin ambigüedad desde frames válidos más padding cero','actions':[]})
    if 'MPEG_TRAILING_ZERO_PADDING' in issues:
        t=mpeg.get('facts',{}).get('trailing_region') or {}
        if t.get('type')=='PADDING' and t.get('all_zero') and t.get('byte_length',0)>=16:
            plans.append({'spec':SPECS['DROP_CONFIRMED_TERMINAL_ZERO_PADDING'],'status':'ELIGIBLE','reason':'los bytes terminales están fuera de la metadata/audio reconocidos y se demostró que son padding totalmente cero','actions':[{'kind':'drop_range','start':t['byte_start'],'end':t['byte_end'],'field':'TERMINAL_ZERO_PADDING'}]})
    xp=_xing_plan(mpeg,issues)
    if xp:plans.append(xp)
    vp=_vbri_plan(mpeg,issues)
    if vp:plans.append(vp)
    if 'MPEG_SYNC_LOSS' in issues:
        c=_single_bit_candidates(mpeg['data'],mpeg)
        if len(c)==1:
            if 'ID3V2_MALFORMED' in issues:
                plans.append({'spec':SPECS['LOSSLESS_SINGLE_BIT_HEADER_REPAIR'],'status':'BLOCKED','reason':'existe un único candidato de header MPEG, pero persiste un defecto ID3v2 bloqueante independiente; la reparación debe volver a planificarse tras resolver la causa raíz y analizar nuevamente el candidato','actions':[c[0]]})
            elif (issues & (XING_CODES|VBRI_CODES)) and mpeg.get('facts',{}).get('vbr_header'):
                plans.append({'spec':SPECS['LOSSLESS_SINGLE_BIT_HEADER_REPAIR'],'status':'BLOCKED','reason':'una reparación que modifica frames no se publica mientras haya metadata Xing/Info/VBRI acoplada sin resolver; el stream debe alcanzar primero un estado de metadata coherente','actions':[c[0]]})
            elif mpeg.get('facts',{}).get('vbr_header') and mpeg.get('facts',{}).get('gaps'):
                # Conserva la jerarquía establecida para streams Xing dañados.
                plans.append({'spec':SPECS['LOSSLESS_SINGLE_BIT_HEADER_REPAIR'],'status':'BLOCKED','reason':'un stream dañado con metadata VBR permanece en el ámbito de preservación/recuperación; la plausibilidad local del header no alcanza para una reparación estructural segura','actions':[c[0]]})
            else:
                plans.append({'spec':SPECS['LOSSLESS_SINGLE_BIT_HEADER_REPAIR'],'status':'ELIGIBLE','reason':'reconstrucción única de un bit del header MPEG','actions':[c[0]]})
    if issues=={'TRUNCATED_MPEG_FRAME'} and not mpeg['facts'].get('vbr_header'):
        i=next(i for i in mpeg['issues'] if i.code=='TRUNCATED_MPEG_FRAME')
        plans.append({'spec':SPECS['DROP_TRUNCATED_TAIL_NO_VBR_METADATA'],'status':'ELIGIBLE','reason':'frame MPEG final incompleto y aislado sin metadata VBR acoplada','actions':[{'kind':'drop_range','start':i.byte_start,'end':i.byte_end,'field':'TRUNCATED_FINAL_FRAME'}]})
    return plans


def plan_extension(source:Path, detected_container:str|None, detected_codec:str|None, expected_extension:str|None, format_confidence:str, mismatch:bool=True):
    if not mismatch or not expected_extension:return None
    if format_confidence!='HIGH':
        return {'spec':SPECS['FIX_EXTENSION_BYTE_IDENTICAL'],'status':'BLOCKED','reason':f'se observó una discrepancia entre contenido y extensión, pero corregir automáticamente la extensión requiere confianza de formato HIGH (actual: {format_confidence})','actions':[]}
    return {'spec':SPECS['FIX_EXTENSION_BYTE_IDENTICAL'],'status':'ELIGIBLE','reason':'el contenido detectado identifica la extensión esperada con confianza HIGH; sólo se publica una copia idéntica en bytes','actions':[{'kind':'byte_identical_copy','expected_extension':expected_extension,'detected_container':detected_container,'detected_codec':detected_codec}]}


def _reuse_extension(source:Path,source_sha:str,expected_extension:str,detected_container:str|None,detected_codec:str|None):
    sid='FIX_EXTENSION_BYTE_IDENTICAL'
    for side in source.parent.glob('*.lossydoctor-manifest.json'):
        try:d=json.loads(side.read_text(encoding='utf-8'))
        except Exception:continue
        if not (d.get('producer')=='LossyDoctor' and d.get('producer_version')==APP_VERSION and d.get('schema_version')==MANIFEST_SCHEMA and d.get('source_sha256')==source_sha and d.get('repair_spec_id')==sid and d.get('derivation_kind')=='EXTENSION_FIXED' and d.get('validation_result')=='PASS' and str(d.get('expected_extension','')).lower()==expected_extension.lower() and d.get('detected_container')==detected_container and d.get('detected_codec')==detected_codec):continue
        op=Path(d.get('output_path',''))
        if op.exists() and op.suffix.lower()==expected_extension.lower() and sha256_file(op)==source_sha and d.get('output_sha256')==source_sha:
            return {'status':'REUSED','output_path':str(op),'manifest_path':str(side),'manifest':d}
    return None


def execute_extension(source:Path,source_sha:str,pl:dict,max_scan:int=262144,publish:bool=True):
    sid=pl['spec']['id']
    if pl['status']!='ELIGIBLE':return {'repair_spec_id':sid,'status':pl['status'],'reason':pl['reason']}
    a=pl['actions'][0];ext=a['expected_extension'].lower();dc=a.get('detected_container');dk=a.get('detected_codec')
    reused=_reuse_extension(source,source_sha,ext,dc,dk)
    if reused:return {'repair_spec_id':sid,**reused}
    from formats.identify import identify
    with tempfile.TemporaryDirectory(prefix='lossydoctor-extension-fix-') as td:
        tmp=Path(td)/('candidate'+ext);shutil.copyfile(source,tmp)
        reid=identify(tmp,max_scan)
        passed=sha256_file(tmp)==source_sha and sha256_file(source)==source_sha and reid.get('supported') and reid.get('confidence')=='HIGH' and reid.get('container')==dc and reid.get('codec')==dk
        ver={'passed':bool(passed),'source_sha256_unchanged':sha256_file(source)==source_sha,'output_sha256_equals_source':sha256_file(tmp)==source_sha,'output_extension_expected':tmp.suffix.lower()==ext,'content_reidentification_match':bool(reid.get('supported') and reid.get('confidence')=='HIGH' and reid.get('container')==dc and reid.get('codec')==dk),'reidentified_container':reid.get('container'),'reidentified_codec':reid.get('codec'),'reidentified_confidence':reid.get('confidence')}
        if not passed:return {'repair_spec_id':sid,'status':'REJECTED','verification':ver}
        desired=source.with_name(source.stem+' [extension-fixed]'+ext);out=tmp
        if sha256_file(out)!=source_sha:return {'repair_spec_id':sid,'status':'REJECTED','verification':{**ver,'published_output_sha256_equals_source':False}}
        ver['published_output_sha256_equals_source']=True
        man={'schema_version':MANIFEST_SCHEMA,'producer':'LossyDoctor','producer_version':APP_VERSION,'derivation_kind':'EXTENSION_FIXED','repair_spec_id':sid,'source_path':str(source),'source_sha256':source_sha,'source_extension':source.suffix,'detected_container':dc,'detected_codec':dk,'expected_extension':ext,'output_path':str(out),'output_sha256':source_sha,'changed_byte_ranges':[],'byte_identical_to_source':True,'validation_result':'PASS','source_modified':False,'audio_recoding':False,'verification':ver}
        out,side,man,publication_status=publish_or_preview_with_manifest(tmp,desired,man,publish)
        return {'repair_spec_id':sid,'status':publication_status,'output_path':str(out) if out is not None else None,'manifest_path':str(side) if side else None,'manifest':man,'verification':ver}


def _reuse_repaired(source:Path,source_sha:str):
    for side in sorted(source.parent.glob('*.lossydoctor-manifest.json')):
        try:d=json.loads(side.read_text(encoding='utf-8'))
        except Exception:continue
        if not (d.get('producer')=='LossyDoctor' and d.get('producer_version')==APP_VERSION and d.get('schema_version')==MANIFEST_SCHEMA and d.get('source_sha256')==source_sha and d.get('derivation_kind')=='REPAIRED_SAFE' and d.get('validation_result')=='PASS'):continue
        op=Path(d.get('output_path',''))
        if op.exists() and sha256_file(op)==d.get('output_sha256'):
            return {'status':'REUSED','repair_spec_id':d.get('repair_spec_id'),'output_path':str(op),'manifest_path':str(side),'manifest':d,'verification':d.get('verification',{})}
    return None


def _replace(candidate:bytearray,start:int,end:int,new:bytes,field:str,step:int,sid:str):
    old=bytes(candidate[start:end])
    if old==new:return None
    candidate[start:end]=new
    return {'step':step,'repair_spec_id':sid,'operation':'REPLACE','coordinate_space':'STEP_INPUT','byte_start':start,'byte_end':end,'field':field,'original_hex':old.hex(),'replacement_hex':new.hex()}


def _apply_action(data:bytes,mpeg:dict,pl:dict,step:int):
    sid=pl['spec']['id'];candidate=bytearray(data);changed=[]
    if sid=='REPAIR_ID3V24_SIZE_TO_VERIFIED_BOUNDARY':
        a=pl['actions'][0];r=_replace(candidate,6,10,bytes.fromhex(a['replacement_hex']),'ID3V2_SIZE',step,sid)
        if r:changed.append(r)
    elif sid=='LOSSLESS_SINGLE_BIT_HEADER_REPAIR':
        a=pl['actions'][0];off=a['offset'];r=_replace(candidate,off,off+4,bytes.fromhex(a['replacement_hex']),'MPEG_HEADER',step,sid)
        if r:changed.append(r)
    elif sid in ('DROP_TRUNCATED_TAIL_NO_VBR_METADATA','DROP_CONFIRMED_TERMINAL_ZERO_PADDING'):
        a=pl['actions'][0];old=bytes(candidate[a['start']:a['end']]);del candidate[a['start']:a['end']]
        changed.append({'step':step,'repair_spec_id':sid,'operation':'DELETE','coordinate_space':'STEP_INPUT','byte_start':a['start'],'byte_end':a['end'],'field':a.get('field'),'removed_bytes':len(old),'removed_sha256':hashlib.sha256(old).hexdigest()})
    elif sid=='REFRESH_XING_METADATA':
        a=pl['actions'][0];e=a['expected'];base=a['first_audio_offset']+a['xing_offset_in_frame']
        fields=[
            ('XING_KIND',base,base+4,e['kind'].encode('ascii')),
            ('XING_FRAME_COUNT',base+8,base+12,int(e['frames']).to_bytes(4,'big')),
            ('XING_BYTE_COUNT',base+12,base+16,int(e['bytes']).to_bytes(4,'big')),
            ('XING_TOC',base+16,base+116,bytes(e['toc'])),
            ('XING_MUSIC_LENGTH',base+148,base+152,int(e['music_length']).to_bytes(4,'big')),
            ('XING_MUSIC_CRC',base+152,base+154,int(e['music_crc']).to_bytes(2,'big')),
            ('XING_TAG_CRC',base+154,base+156,int(e['tag_crc']).to_bytes(2,'big')),
        ]
        for field,s,eoff,new in fields:
            r=_replace(candidate,s,eoff,new,field,step,sid)
            if r:changed.append(r)
    elif sid=='REFRESH_VBRI_METADATA':
        a=pl['actions'][0];e=a['expected'];base=a['first_audio_offset']+a['vbri_offset_in_frame'];esz=a['toc_entry_size']
        fields=[
            ('VBRI_BYTE_COUNT',base+10,base+14,int(e['bytes']).to_bytes(4,'big')),
            ('VBRI_FRAME_COUNT',base+14,base+18,int(e['frames']).to_bytes(4,'big')),
            ('VBRI_TOC',base+26,base+26+len(e['toc'])*esz,b''.join(int(v).to_bytes(esz,'big') for v in e['toc'])),
        ]
        for field,s,eoff,new in fields:
            r=_replace(candidate,s,eoff,new,field,step,sid)
            if r:changed.append(r)
    else:raise RuntimeError(f'ejecutor RepairSpec no soportado: {sid}')
    return bytes(candidate),changed


def _audio_payload_sha(mpeg:dict):
    h=hashlib.sha256();data=mpeg.get('data',b'')
    for f in mpeg.get('frames',[]):
        if not f.get('is_vbr_header'):h.update(data[f['byte_start']:f['byte_end']])
    return h.hexdigest()


def _pcm_hash(path:Path,ffmpeg:str,tmpdir:Path,label:str):
    raw=tmpdir/f'{label}.raw';r=decode_to_raw_file(path,raw,ffmpeg)
    return {'passed':bool(r.get('passed')),'sha256':sha256_file(raw) if r.get('passed') else None,'stderr':r.get('stderr','')}


def _hash_file_range(path:Path,start:int,end:int,chunk=1024*1024):
    if start<0 or end<start:return None
    h=hashlib.sha256();remain=end-start
    with path.open('rb') as f:
        f.seek(start)
        while remain:
            b=f.read(min(chunk,remain))
            if not b:return None
            h.update(b);remain-=len(b)
    return h.hexdigest()


def _structural_gapless_pcm_proof(path:Path,mpeg:dict,ffmpeg:str,tmpdir:Path,label:str):
    facts=mpeg.get('facts',{});x=((facts.get('vbr_header') or {}).get('xing') or {})
    observed=facts.get('audio_frame_count_observed');spf=facts.get('samples_per_frame');channels=facts.get('channels')
    delay=x.get('encoder_delay_samples');pad=x.get('end_padding_samples');decoder_comp=529
    base={'mode':'FFMPEG_SKIP_MANUAL_PLUS_STRUCTURAL_GAPLESS_WINDOW','decoder_compensation_samples':decoder_comp,
          'observed_audio_frame_count':observed,'samples_per_frame':spf,'channels':channels,'encoder_delay_samples':delay,'end_padding_samples':pad}
    if not x.get('ffmpeg_extended_profile') or not x.get('gapless_fields_trusted') or not all(isinstance(v,int) for v in (observed,spf,channels,delay,pad)):
        return {**base,'passed':False,'reason':'los datos estructurales/sin brechas requeridos del perfil FFmpeg no están disponibles'}
    if observed<=0 or spf<=0 or channels<=0 or pad<decoder_comp:
        return {**base,'passed':False,'reason':'ventana sin brechas fuera del alcance de prueba'}
    raw=tmpdir/f'{label}.physical.raw';r=decode_to_raw_file(path,raw,ffmpeg,skip_manual=True)
    if not r.get('passed'):
        return {**base,'passed':False,'reason':'falló la decodificación del PCM físico independiente de metadata','stderr':r.get('stderr','')}
    stride=channels*4;size=raw.stat().st_size
    if size%stride:
        return {**base,'passed':False,'reason':'la cantidad de bytes PCM físicos no está alineada con canales/muestras'}
    physical_samples=size//stride;expected_physical=observed*spf
    start=delay+decoder_comp;end=expected_physical-pad+decoder_comp
    if physical_samples!=expected_physical or start<0 or end<=start or end>physical_samples:
        return {**base,'passed':False,'reason':'la cantidad de muestras de decodificación física o la ventana estructural sin brechas es inconsistente',
                'physical_sample_count':physical_samples,'expected_physical_sample_count':expected_physical,'window_start_sample':start,'window_end_sample':end}
    return {**base,'passed':True,'physical_sample_count':physical_samples,'expected_physical_sample_count':expected_physical,
            'window_start_sample':start,'window_end_sample':end,'logical_sample_count':end-start,
            'physical_pcm_sha256':sha256_file(raw),'structural_window_pcm_sha256':_hash_file_range(raw,start*stride,end*stride)}


def _step_verification(pre:dict,post:dict,pl:dict,source:Path,source_sha:str):
    sid=pl['spec']['id'];pre_codes=set(_issue_codes(pre));post_codes=set(_issue_codes(post));resolves=set(pl['spec'].get('resolves') or [])
    target=(pre_codes & resolves) if resolves else set()
    target_resolved=bool(target) and not (post_codes & target)
    new_damaged=_damaged_codes(post)-_damaged_codes(pre)
    v={'passed':bool(target_resolved and not new_damaged and sha256_file(source)==source_sha),'target_issues':sorted(target),'target_issues_resolved':bool(target_resolved),'pre_issue_codes':sorted(pre_codes),'post_issue_codes':sorted(post_codes),'resolved_issue_codes':sorted(pre_codes-post_codes),'remaining_issue_codes':sorted(pre_codes&post_codes),'new_issue_codes':sorted(post_codes-pre_codes),'new_damaged_issue_codes':sorted(new_damaged),'source_sha256_unchanged':sha256_file(source)==source_sha}
    if sid in ('REFRESH_XING_METADATA','REFRESH_VBRI_METADATA'):
        v['audio_payload_sha256_pre']=_audio_payload_sha(pre);v['audio_payload_sha256_post']=_audio_payload_sha(post);v['audio_payload_identical']=v['audio_payload_sha256_pre']==v['audio_payload_sha256_post']
        v['frame_count_preserved']=pre.get('facts',{}).get('audio_frame_count_observed')==post.get('facts',{}).get('audio_frame_count_observed')
        if sid=='REFRESH_XING_METADATA':
            v['xing_issue_codes_remaining']=sorted(post_codes & XING_CODES);remaining=v['xing_issue_codes_remaining']
        else:
            v['vbri_issue_codes_remaining']=sorted(post_codes & VBRI_CODES);remaining=v['vbri_issue_codes_remaining']
        v['passed']=v['passed'] and v['audio_payload_identical'] and v['frame_count_preserved'] and not remaining
    return v


def execute_mpeg(source:Path,source_sha:str,initial_mpeg:dict,ffmpeg:str,ffprobe_exe:str,publish=True,max_scan=262144):
    initial_plans=plan(source,initial_mpeg)
    trace=[]
    for p in initial_plans:trace.append({**p,'chain_iteration':0})
    reused=_reuse_repaired(source,source_sha)
    if reused:
        return {'plans':trace,'executions':[{'repair_spec_id':reused['repair_spec_id'],**reused}]}
    if not any(p['status']=='ELIGIBLE' for p in initial_plans):
        return {'plans':trace,'executions':[{'repair_spec_id':p['spec']['id'],'status':'BLOCKED','reason':p['reason']} for p in initial_plans if p['status']=='BLOCKED']}
    with tempfile.TemporaryDirectory(prefix='lossydoctor-causal-repair-') as td:
        t=Path(td);working=t/('working'+source.suffix);shutil.copyfile(source,working)
        current=initial_mpeg;steps=[];all_changed=[];iteration=0;blocked_final=[]
        while iteration<8:
            plans=plan(working,current)
            if iteration>0:
                for p in plans:trace.append({**p,'chain_iteration':iteration})
            elig=next((p for p in plans if p['status']=='ELIGIBLE'),None)
            if not elig:
                blocked_final=[p for p in plans if p['status']=='BLOCKED'];break
            sid=elig['spec']['id'];before=working.read_bytes();candidate,changed=_apply_action(before,current,elig,iteration+1)
            if candidate==before:
                return {'plans':trace,'executions':[{'repair_spec_id':sid,'status':'REJECTED','reason':'RepairSpec no produjo ningún cambio de bytes'}]}
            working.write_bytes(candidate);post=analyze_mpeg(working,max_scan);sv=_step_verification(current,post,elig,source,source_sha)
            step={'step':iteration+1,'repair_spec_id':sid,'status':'PASS' if sv['passed'] else 'FAIL','input_sha256':hashlib.sha256(before).hexdigest(),'output_sha256':hashlib.sha256(candidate).hexdigest(),'changed_byte_ranges':changed,'verification':sv}
            steps.append(step);all_changed.extend(changed)
            if not sv['passed']:
                return {'plans':trace,'executions':[{'repair_spec_id':sid,'status':'REJECTED','verification':sv,'chain_steps':steps}]}
            current=post;iteration+=1
        if iteration>=8 and any(p['status']=='ELIGIBLE' for p in plan(working,current)):
            return {'plans':trace,'executions':[{'repair_spec_id':'CAUSAL_REPAIR_CHAIN','status':'REJECTED','reason':'se alcanzó el límite de iteraciones de la cadena de reparación'}]}
        # Puerta final de decodificación, sondeo e inmutabilidad del origen.
        strict=decode(working,ffmpeg,'STRICT_DECODE');play=decode(working,ffmpeg,'PLAYBACK_DECODE');probe=ffprobe(working,ffprobe_exe)
        initial_codes=set(_issue_codes(initial_mpeg));final_codes=set(_issue_codes(current));new_damaged=_damaged_codes(current)-_damaged_codes(initial_mpeg)
        finalv={'passed':bool(strict.get('passed') and play.get('passed') and probe.get('passed') and not new_damaged and sha256_file(source)==source_sha),'applied_step_count':len(steps),'incremental_rescan_after_each_step':True,'initial_issue_codes':sorted(initial_codes),'final_issue_codes':sorted(final_codes),'resolved_issue_codes':sorted(initial_codes-final_codes),'new_issue_codes':sorted(final_codes-initial_codes),'new_damaged_issue_codes':sorted(new_damaged),'strict_decode':'PASS' if strict.get('passed') else 'FAIL','playback_decode':'PASS' if play.get('passed') else 'FAIL','ffprobe':'PASS' if probe.get('passed') else 'FAIL','source_sha256_unchanged':sha256_file(source)==source_sha}
        # Prueba lossless adicional para reescritura Xing coherente. Si el conteo Xing
        # inicial es incorrecto, la decodificación normal depende de metadatos y no es
        # autoritativa. Se demuestra identidad PCM física con skip_manual,
        # reconstruye la ventana gapless de FFmpeg desde frames OBSERVED, demora y padding,
        # y exige que la decodificación reparada coincida con esa ventana independiente.
        if any(s['repair_spec_id']=='REFRESH_XING_METADATA' for s in steps):
            prof=canonical_pcm_profile(ffmpeg,initial_mpeg['facts'].get('sample_rate'),initial_mpeg['facts'].get('channels'));finalv['canonical_pcm_profile']=prof
            p0=_pcm_hash(source,ffmpeg,t,'source_pcm');p1=_pcm_hash(working,ffmpeg,t,'candidate_pcm')
            finalv['source_canonical_pcm_sha256']=p0['sha256'];finalv['candidate_canonical_pcm_sha256']=p1['sha256']
            frame_count_repaired='XING_FRAME_COUNT_MISMATCH' in initial_codes
            finalv['pcm_identity_gate']='STRUCTURAL_GAPLESS_PROOF' if frame_count_repaired else 'DIRECT_CANONICAL_DECODE_HASH'
            finalv['audio_payload_identical']=_audio_payload_sha(initial_mpeg)==_audio_payload_sha(current)
            finalv['audio_frame_count_preserved']=initial_mpeg['facts'].get('audio_frame_count_observed')==current['facts'].get('audio_frame_count_observed')
            finalv['xing_issue_codes_remaining']=sorted(final_codes & XING_CODES)
            finalv['seekability_metadata_validated']=not any(i.layer=='seek_metadata' for i in current.get('issues',[]))
            if frame_count_repaired:
                sp0=_structural_gapless_pcm_proof(source,initial_mpeg,ffmpeg,t,'source');sp1=_structural_gapless_pcm_proof(working,current,ffmpeg,t,'candidate')
                finalv['source_structural_presentation_proof']=sp0;finalv['candidate_structural_presentation_proof']=sp1
                finalv['physical_pcm_identical']=bool(sp0.get('passed') and sp1.get('passed') and sp0.get('physical_pcm_sha256')==sp1.get('physical_pcm_sha256'))
                finalv['structural_window_pcm_identical']=bool(sp0.get('passed') and sp1.get('passed') and sp0.get('structural_window_pcm_sha256')==sp1.get('structural_window_pcm_sha256'))
                finalv['candidate_matches_structural_window']=bool(p1.get('passed') and sp0.get('passed') and p1.get('sha256')==sp0.get('structural_window_pcm_sha256'))
                finalv['source_normal_decode_differs_due_to_bad_frame_count']=bool(p0.get('passed') and sp0.get('passed') and p0.get('sha256')!=sp0.get('structural_window_pcm_sha256'))
                finalv['pcm_identical']=bool(p0.get('passed') and p1.get('passed') and p0.get('sha256')==p1.get('sha256'))
                finalv['presentation_equivalent_independent_of_declared_frame_count']=bool(finalv['physical_pcm_identical'] and finalv['structural_window_pcm_identical'] and finalv['candidate_matches_structural_window'])
                pcm_gate=finalv['presentation_equivalent_independent_of_declared_frame_count']
            else:
                finalv['pcm_identical']=bool(p0['passed'] and p1['passed'] and p0['sha256']==p1['sha256']);pcm_gate=finalv['pcm_identical']
            finalv['passed']=finalv['passed'] and pcm_gate and finalv['audio_payload_identical'] and finalv['audio_frame_count_preserved'] and not finalv['xing_issue_codes_remaining'] and finalv['seekability_metadata_validated']
        if any(s['repair_spec_id']=='REFRESH_VBRI_METADATA' for s in steps):
            prof=canonical_pcm_profile(ffmpeg,initial_mpeg['facts'].get('sample_rate'),initial_mpeg['facts'].get('channels'));finalv['canonical_pcm_profile']=prof
            p0=_pcm_hash(source,ffmpeg,t,'source_vbri_pcm');p1=_pcm_hash(working,ffmpeg,t,'candidate_vbri_pcm')
            finalv['pcm_identity_gate']='DIRECT_CANONICAL_DECODE_HASH'
            finalv['source_canonical_pcm_sha256']=p0['sha256'];finalv['candidate_canonical_pcm_sha256']=p1['sha256']
            finalv['pcm_identical']=bool(p0.get('passed') and p1.get('passed') and p0.get('sha256')==p1.get('sha256'))
            finalv['audio_payload_identical']=_audio_payload_sha(initial_mpeg)==_audio_payload_sha(current)
            finalv['audio_frame_count_preserved']=initial_mpeg['facts'].get('audio_frame_count_observed')==current['facts'].get('audio_frame_count_observed')
            finalv['vbri_issue_codes_remaining']=sorted(final_codes & VBRI_CODES)
            pv=((current.get('facts',{}).get('vbr_header') or {}).get('vbri') or {});pexp=pv.get('expected') or {}
            finalv['vbri_table_coverage_validated']=bool(pv.get('layout_valid') and pexp.get('layout_representable') and pexp.get('toc')==pv.get('toc') and pexp.get('frames')==pv.get('frames') and pexp.get('bytes')==pv.get('bytes'))
            finalv['seekability_metadata_validated']=not any(i.layer=='seek_metadata' for i in current.get('issues',[]))
            finalv['passed']=finalv['passed'] and finalv['pcm_identical'] and finalv['audio_payload_identical'] and finalv['audio_frame_count_preserved'] and finalv['vbri_table_coverage_validated'] and not finalv['vbri_issue_codes_remaining'] and finalv['seekability_metadata_validated']
        if not finalv['passed']:
            return {'plans':trace,'executions':[{'repair_spec_id':'CAUSAL_REPAIR_CHAIN' if len(steps)>1 else steps[0]['repair_spec_id'],'status':'REJECTED','verification':finalv,'chain_steps':steps}]}
        # No publica bajo un estado bloqueado de metadatos VBR acoplados tras cambiar frames.
        if any(p['spec']['id'] in ('REFRESH_XING_METADATA','REFRESH_VBRI_METADATA') for p in blocked_final) and any(s['repair_spec_id']=='LOSSLESS_SINGLE_BIT_HEADER_REPAIR' for s in steps):
            return {'plans':trace,'executions':[{'repair_spec_id':'CAUSAL_REPAIR_CHAIN','status':'REJECTED','reason':'la metadata Xing/VBRI acoplada sigue bloqueada después de una reparación que modifica frames','verification':finalv,'chain_steps':steps}]}
        sid='CAUSAL_REPAIR_CHAIN' if len(steps)>1 else steps[0]['repair_spec_id'];desired=source.with_name(source.stem+' [repaired]'+source.suffix);out=working
        repair_diff={'chain_step_count':len(steps),'changed_range_count':len(all_changed),'changed_byte_ranges':all_changed}
        man={'schema_version':MANIFEST_SCHEMA,'producer':'LossyDoctor','producer_version':APP_VERSION,'derivation_kind':'REPAIRED_SAFE','repair_spec_id':sid,'causal_chain_schema':1 if len(steps)>1 else None,'source_path':str(source),'source_sha256':source_sha,'output_path':str(out),'output_sha256':sha256_file(out),'applied_repair_specs':[s['repair_spec_id'] for s in steps],'planning_trace':trace,'chain_steps':steps,'changed_byte_ranges':all_changed,'repair_diff':repair_diff,'validation_result':'PASS','source_modified':False,'audio_recoding':False,'verification':finalv}
        out,side,man,publication_status=publish_or_preview_with_manifest(working,desired,man,publish)
        ex={'repair_spec_id':sid,'status':publication_status,'output_path':str(out) if out is not None else None,'manifest_path':str(side) if side else None,'manifest':man,'verification':finalv}
        # Conserva planes finales bloqueados como ejecuciones informativas cuando corresponde.
        extras=[{'repair_spec_id':p['spec']['id'],'status':'BLOCKED','reason':p['reason']} for p in blocked_final]
        return {'plans':trace,'executions':[ex]+extras}

# Punto de compatibilidad conservado para pruebas unitarias de un único RepairSpec.
def execute(source:Path,source_sha:str,mpeg:dict,pl:dict,ffmpeg:str,ffprobe_exe:str,publish=True):
    if pl.get('status')!='ELIGIBLE':return {'repair_spec_id':pl['spec']['id'],'status':pl.get('status'),'reason':pl.get('reason')}
    rr=execute_mpeg(source,source_sha,mpeg,ffmpeg,ffprobe_exe,publish)
    for ex in rr.get('executions',[]):
        if ex.get('status') in ('CREATED','REUSED','REJECTED'):
            return ex
    return {'repair_spec_id':pl['spec']['id'],'status':'BLOCKED','reason':'RepairSpec no fue seleccionado por el planificador causal del estado actual'}



def _ogg_complement_ranges(size:int,pages:list[dict]):
    ranges=[];pos=0
    for p in sorted(pages,key=lambda x:int(x['byte_start'])):
        a=int(p['byte_start']);b=int(p['byte_end'])
        if a>pos:ranges.append((pos,a))
        pos=max(pos,b)
    if pos<size:ranges.append((pos,size))
    return [(a,b) for a,b in ranges if b>a]


def _ogg_recapture_plan(q:dict,source_size:int):
    issues=q.get('issues') or [];codes={i.code for i in issues};facts=q.get('facts') or {};og=facts.get('ogg') or {};head=facts.get('opus_head') or {};tags=facts.get('opus_tags') or {};pages=q.get('structural_map') or []
    plans=[]
    if 'OGG_PAGE_CRC_MISMATCH' in codes:
        plans.append({'spec':SPECS['REWRITE_OGG_PAGE_CRC'],'status':'BLOCKED','reason':'una discrepancia CRC Ogg demuestra inconsistencia de página, pero no identifica si es incorrecto el campo CRC o algún byte cubierto; reescribir el checksum borraría evidencia','actions':[]})
    if 'OGG_PAGE_SEQUENCE_DISCONTINUITY' in codes:
        plans.append({'spec':SPECS['RENUMBER_OGG_PAGE_SEQUENCE'],'status':'BLOCKED','reason':'una discontinuidad de secuencia no permite distinguir un campo de secuencia corrupto de una o más páginas genuinamente ausentes; renumerar automáticamente no es seguro según la evidencia','actions':[]})
    if 'OGG_SYNC_LOSS' not in codes:return plans
    blocking=codes-{'OGG_SYNC_LOSS'}
    ranges=_ogg_complement_ranges(source_size,pages)
    coherent=(bool(pages) and not blocking and og.get('logical_stream_count')==1 and og.get('all_page_crc_valid') and og.get('bos_present') and og.get('eos_present') and head.get('valid') and tags.get('valid') and bool(ranges))
    if not coherent:
        reason='la recaptura de páginas requiere un único stream lógico Opus completo cuyas páginas retenidas tengan CRC válidos, secuencia/continuación coherente, headers válidos y línea de tiempo de gránulos válida; sólo puede permanecer OGG_SYNC_LOSS'
        plans.append({'spec':SPECS['OGG_RECAPTURE_VALID_PAGES_DROP_EXTRANEOUS_BYTES'],'status':'BLOCKED','reason':reason,'actions':[]});return plans
    actions=[]
    data=q.get('_source_data')
    for a,b in ranges:
        actions.append({'kind':'drop_extraneous_ogg_bytes','start':a,'end':b,'byte_length':b-a,'sha256':hashlib.sha256(data[a:b]).hexdigest() if isinstance(data,(bytes,bytearray)) else None})
    plans.append({'spec':SPECS['OGG_RECAPTURE_VALID_PAGES_DROP_EXTRANEOUS_BYTES'],'status':'ELIGIBLE','reason':'todas las páginas Ogg retenidas están autenticadas independientemente por CRC y forman un único stream Opus completo y coherente; sólo se quitan bytes externos a esas páginas','actions':actions})
    return plans


def execute_ogg_opus(source:Path,source_sha:str,q:dict,ffmpeg:str,ffprobe_exe:str,publish=True,max_scan=262144,timeout=300):
    from formats.ogg_opus import analyze as analyze_ogg
    data=source.read_bytes();qq=dict(q);qq['_source_data']=data
    plans=_ogg_recapture_plan(qq,len(data));executions=[]
    eligible=next((p for p in plans if p['spec']['id']=='OGG_RECAPTURE_VALID_PAGES_DROP_EXTRANEOUS_BYTES' and p['status']=='ELIGIBLE'),None)
    if not eligible:
        for p in plans:
            executions.append({'repair_spec_id':p['spec']['id'],'status':p['status'],'reason':p['reason']})
        return {'plans':plans,'executions':executions}
    reused=_reuse_repaired(source,source_sha)
    if reused:
        executions.append(reused);return {'plans':plans,'executions':executions}
    pages=q.get('structural_map') or []
    candidate=b''.join(data[int(p['byte_start']):int(p['byte_end'])] for p in pages)
    removed=[]
    for a in eligible['actions']:
        removed.append({'step':1,'repair_spec_id':eligible['spec']['id'],'operation':'DELETE','coordinate_space':'SOURCE','byte_start':a['start'],'byte_end':a['end'],'field':'EXTRANEOUS_BYTES_OUTSIDE_AUTHENTICATED_OGG_PAGES','removed_bytes':a['byte_length'],'removed_sha256':a['sha256']})
    with tempfile.TemporaryDirectory(prefix='lossydoctor-ogg-recapture-') as td:
        tmp=Path(td)/'candidate.opus';tmp.write_bytes(candidate)
        post=analyze_ogg(tmp);post_codes=[i.code for i in post.get('issues',[])]
        pr=post.get('facts') or {};sr=q.get('facts') or {};poh=pr.get('opus_head') or {};soh=sr.get('opus_head') or {};pot=pr.get('opus_tags') or {};sot=sr.get('opus_tags') or {}
        probe=ffprobe(tmp,ffprobe_exe,timeout);strict=decode(tmp,ffmpeg,'STRICT_DECODE',timeout);play=decode(tmp,ffmpeg,'PLAYBACK_DECODE',timeout)
        page_bytes_exact=(candidate==b''.join(data[int(p['byte_start']):int(p['byte_end'])] for p in pages))
        semantic_equal=(pr.get('audio_packet_count')==sr.get('audio_packet_count') and pr.get('final_granule_position')==sr.get('final_granule_position') and pr.get('pcm_sample_position')==sr.get('pcm_sample_position') and poh.get('pre_skip')==soh.get('pre_skip') and poh.get('output_gain_q7_8')==soh.get('output_gain_q7_8') and poh.get('channels')==soh.get('channels') and pot.get('vendor')==sot.get('vendor') and pot.get('comments')==sot.get('comments'))
        passed=(not post_codes and bool((pr.get('ogg') or {}).get('all_page_crc_valid')) and page_bytes_exact and semantic_equal and bool(probe.get('audio_streams')) and strict.get('passed') and play.get('completed') and sha256_file(source)==source_sha)
        ver={'passed':bool(passed),'full_rescan':'PASS' if not post_codes else 'FAIL','post_issue_codes':post_codes,'ffprobe':'PASS' if probe.get('audio_streams') else 'FAIL','strict_decode':'PASS' if strict.get('passed') else 'FAIL','playback_decode':'PASS' if play.get('completed') else 'FAIL','source_sha256_unchanged':sha256_file(source)==source_sha,'all_retained_page_crc_valid':bool((pr.get('ogg') or {}).get('all_page_crc_valid')),'retained_page_bytes_exact':page_bytes_exact,'packet_and_timeline_semantics_equal':semantic_equal,'audio_packet_count_equal':pr.get('audio_packet_count')==sr.get('audio_packet_count'),'final_granule_equal':pr.get('final_granule_position')==sr.get('final_granule_position'),'pre_skip_equal':poh.get('pre_skip')==soh.get('pre_skip'),'output_gain_q7_8_equal':poh.get('output_gain_q7_8')==soh.get('output_gain_q7_8'),'output_gain_applied_to_pcm':False,'output_gain_policy':'PRESERVE_UNAPPLIED_Q7_8_IN_MANIFEST','page_bytes_modified':False,'audio_packet_bytes_modified':False}
        if not passed:
            executions.append({'repair_spec_id':eligible['spec']['id'],'status':'REJECTED','verification':ver});return {'plans':plans,'executions':executions}
        desired=source.with_name(source.stem+' [repaired].opus');out=tmp
        outsha=sha256_file(out)
        man={'schema_version':MANIFEST_SCHEMA,'producer':'LossyDoctor','producer_version':APP_VERSION,'derivation_kind':'REPAIRED_SAFE','repair_spec_id':eligible['spec']['id'],'source_path':str(source),'source_sha256':source_sha,'output_path':str(out),'output_sha256':outsha,'changed_byte_ranges':removed,'source_modified':False,'audio_recoding':False,'ogg_page_bytes_modified':False,'opus_packet_bytes_modified':False,'output_gain_q7_8':soh.get('output_gain_q7_8'),'output_gain_policy':'PRESERVE_UNAPPLIED_Q7_8_IN_MANIFEST','validation_result':'PASS','verification':ver,'chain_steps':[{'step':1,'repair_spec_id':eligible['spec']['id'],'status':'PASS','verification':{'resolved_issue_codes':['OGG_SYNC_LOSS']}}]}
        out,side,man,publication_status=publish_or_preview_with_manifest(tmp,desired,man,publish)
        executions.append({'repair_spec_id':eligible['spec']['id'],'status':publication_status,'output_path':str(out) if out is not None else None,'manifest_path':str(side) if side else None,'manifest':man,'verification':ver})
    return {'plans':plans,'executions':executions}



def _ogg_vorbis_recapture_plan(q:dict,source_size:int):
    issues=q.get('issues') or [];codes={i.code for i in issues};facts=q.get('facts') or {};og=facts.get('ogg') or {};vi=facts.get('vorbis_identification') or {};vc=facts.get('vorbis_comment') or {};vs=facts.get('vorbis_setup') or {};pages=q.get('structural_map') or []
    plans=[]
    if 'OGG_PAGE_CRC_MISMATCH' in codes:
        plans.append({'spec':SPECS['REWRITE_OGG_PAGE_CRC'],'status':'BLOCKED','reason':'una discrepancia CRC Ogg demuestra inconsistencia de página, pero no identifica si es incorrecto el campo CRC o algún byte cubierto; reescribir el checksum borraría evidencia','actions':[]})
    if 'OGG_PAGE_SEQUENCE_DISCONTINUITY' in codes:
        plans.append({'spec':SPECS['RENUMBER_OGG_PAGE_SEQUENCE'],'status':'BLOCKED','reason':'una discontinuidad de secuencia no permite distinguir un campo de secuencia corrupto de una o más páginas genuinamente ausentes; renumerar automáticamente no es seguro según la evidencia','actions':[]})
    if 'OGG_SYNC_LOSS' not in codes:return plans
    blocking=codes-{'OGG_SYNC_LOSS'}
    ranges=_ogg_complement_ranges(source_size,pages)
    coherent=(bool(pages) and not blocking and og.get('logical_stream_count')==1 and og.get('all_page_crc_valid') and og.get('bos_present') and og.get('eos_present') and vi.get('valid') and vc.get('valid') and vs.get('valid') and bool(ranges))
    if not coherent:
        reason='la recaptura de páginas requiere un único stream lógico Vorbis completo cuyas páginas retenidas tengan CRC válidos, secuencia/continuación coherente, headers/configuración obligatorios válidos y línea de tiempo de gránulos/tamaños de bloque válida; sólo puede permanecer OGG_SYNC_LOSS'
        plans.append({'spec':SPECS['OGG_RECAPTURE_VALID_PAGES_DROP_EXTRANEOUS_BYTES'],'status':'BLOCKED','reason':reason,'actions':[]});return plans
    actions=[];data=q.get('_source_data')
    for a,b in ranges:
        actions.append({'kind':'drop_extraneous_ogg_bytes','start':a,'end':b,'byte_length':b-a,'sha256':hashlib.sha256(data[a:b]).hexdigest() if isinstance(data,(bytes,bytearray)) else None})
    plans.append({'spec':SPECS['OGG_RECAPTURE_VALID_PAGES_DROP_EXTRANEOUS_BYTES'],'status':'ELIGIBLE','reason':'todas las páginas Ogg retenidas están autenticadas independientemente por CRC y forman un único stream Vorbis completo y coherente; sólo se quitan bytes externos a esas páginas','actions':actions})
    return plans


def _vorbis_semantic_snapshot(facts:dict):
    vi=facts.get('vorbis_identification') or {};vc=facts.get('vorbis_comment') or {};vs=facts.get('vorbis_setup') or {}
    amap=facts.get('audio_packet_map') or []
    ev=facts.get('vorbis_recovery_evidence') or {}
    return {
        'audio_packet_count':facts.get('audio_packet_count'),'final_granule_position':facts.get('final_granule_position'),
        'identification':{k:vi.get(k) for k in ('version','channels','sample_rate','bitrate_maximum','bitrate_nominal','bitrate_minimum','blocksize_0','blocksize_1','framing_flag','valid')},
        'comment':{'vendor':vc.get('vendor'),'comments':vc.get('comments'),'framing_flag':vc.get('framing_flag'),'valid':vc.get('valid')},
        'setup':{'mode_count':vs.get('mode_count'),'modes':vs.get('modes'),'codebook_count':vs.get('codebook_count'),'floor_count':vs.get('floor_count'),'residue_count':vs.get('residue_count'),'mapping_count':vs.get('mapping_count'),'framing_flag':vs.get('framing_flag'),'valid':vs.get('valid')},
        'audio_packet_sha256':[x.get('packet_sha256') for x in amap],
        'candidate_regions':[(r.get('pcm_start'),r.get('pcm_end'),r.get('authenticated_eos_included')) for r in (ev.get('candidate_regions') or [])],
    }


def execute_ogg_vorbis(source:Path,source_sha:str,q:dict,ffmpeg:str,ffprobe_exe:str,publish=True,max_scan=262144,timeout=300):
    from formats.ogg_vorbis import analyze as analyze_ogg
    data=source.read_bytes();qq=dict(q);qq['_source_data']=data
    plans=_ogg_vorbis_recapture_plan(qq,len(data));executions=[]
    eligible=next((p for p in plans if p['spec']['id']=='OGG_RECAPTURE_VALID_PAGES_DROP_EXTRANEOUS_BYTES' and p['status']=='ELIGIBLE'),None)
    if not eligible:
        for p in plans:executions.append({'repair_spec_id':p['spec']['id'],'status':p['status'],'reason':p['reason']})
        return {'plans':plans,'executions':executions}
    reused=_reuse_repaired(source,source_sha)
    if reused:
        executions.append(reused);return {'plans':plans,'executions':executions}
    pages=q.get('structural_map') or []
    candidate=b''.join(data[int(p['byte_start']):int(p['byte_end'])] for p in pages)
    removed=[]
    for a in eligible['actions']:
        removed.append({'step':1,'repair_spec_id':eligible['spec']['id'],'operation':'DELETE','coordinate_space':'SOURCE','byte_start':a['start'],'byte_end':a['end'],'field':'EXTRANEOUS_BYTES_OUTSIDE_AUTHENTICATED_OGG_PAGES','removed_bytes':a['byte_length'],'removed_sha256':a['sha256']})
    with tempfile.TemporaryDirectory(prefix='lossydoctor-ogg-vorbis-recapture-') as td:
        tmp=Path(td)/'candidate.ogg';tmp.write_bytes(candidate)
        post=analyze_ogg(tmp);post_codes=[i.code for i in post.get('issues',[])]
        pr=post.get('facts') or {};sr=q.get('facts') or {};probe=ffprobe(tmp,ffprobe_exe,timeout);strict=decode(tmp,ffmpeg,'STRICT_DECODE',timeout);play=decode(tmp,ffmpeg,'PLAYBACK_DECODE',timeout)
        page_bytes_exact=(candidate==b''.join(data[int(p['byte_start']):int(p['byte_end'])] for p in pages))
        semantic_equal=(_vorbis_semantic_snapshot(pr)==_vorbis_semantic_snapshot(sr))
        packet_hashes_equal=(_vorbis_semantic_snapshot(pr)['audio_packet_sha256']==_vorbis_semantic_snapshot(sr)['audio_packet_sha256'])
        passed=(not post_codes and bool((pr.get('ogg') or {}).get('all_page_crc_valid')) and page_bytes_exact and semantic_equal and packet_hashes_equal and bool(probe.get('audio_streams')) and strict.get('passed') and play.get('completed') and sha256_file(source)==source_sha)
        ver={'passed':bool(passed),'full_rescan':'PASS' if not post_codes else 'FAIL','post_issue_codes':post_codes,'ffprobe':'PASS' if probe.get('audio_streams') else 'FAIL','strict_decode':'PASS' if strict.get('passed') else 'FAIL','playback_decode':'PASS' if play.get('completed') else 'FAIL','source_sha256_unchanged':sha256_file(source)==source_sha,'all_retained_page_crc_valid':bool((pr.get('ogg') or {}).get('all_page_crc_valid')),'retained_page_bytes_exact':page_bytes_exact,'packet_and_timeline_semantics_equal':semantic_equal,'vorbis_audio_packet_hashes_equal':packet_hashes_equal,'audio_packet_count_equal':pr.get('audio_packet_count')==sr.get('audio_packet_count'),'final_granule_equal':pr.get('final_granule_position')==sr.get('final_granule_position'),'vorbis_identification_equal':_vorbis_semantic_snapshot(pr)['identification']==_vorbis_semantic_snapshot(sr)['identification'],'vorbis_comment_equal':_vorbis_semantic_snapshot(pr)['comment']==_vorbis_semantic_snapshot(sr)['comment'],'vorbis_setup_equal':_vorbis_semantic_snapshot(pr)['setup']==_vorbis_semantic_snapshot(sr)['setup'],'candidate_pcm_regions_equal':_vorbis_semantic_snapshot(pr)['candidate_regions']==_vorbis_semantic_snapshot(sr)['candidate_regions'],'page_bytes_modified':False,'vorbis_packet_bytes_modified':False}
        if not passed:
            executions.append({'repair_spec_id':eligible['spec']['id'],'status':'REJECTED','verification':ver});return {'plans':plans,'executions':executions}
        desired=source.with_name(source.stem+' [repaired].ogg');out=tmp;outsha=sha256_file(out)
        man={'schema_version':MANIFEST_SCHEMA,'producer':'LossyDoctor','producer_version':APP_VERSION,'derivation_kind':'REPAIRED_SAFE','repair_spec_id':eligible['spec']['id'],'source_path':str(source),'source_sha256':source_sha,'output_path':str(out),'output_sha256':outsha,'changed_byte_ranges':removed,'source_modified':False,'audio_recoding':False,'ogg_page_bytes_modified':False,'vorbis_packet_bytes_modified':False,'validation_result':'PASS','verification':ver,'chain_steps':[{'step':1,'repair_spec_id':eligible['spec']['id'],'status':'PASS','verification':{'resolved_issue_codes':['OGG_SYNC_LOSS']}}]}
        out,side,man,publication_status=publish_or_preview_with_manifest(tmp,desired,man,publish)
        executions.append({'repair_spec_id':eligible['spec']['id'],'status':publication_status,'output_path':str(out) if out is not None else None,'manifest_path':str(side) if side else None,'manifest':man,'verification':ver})
    return {'plans':plans,'executions':executions}
