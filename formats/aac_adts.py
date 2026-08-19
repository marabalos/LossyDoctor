from __future__ import annotations
from pathlib import Path
import hashlib
from app.models import Issue

# Tabla samplingFrequencyIndex de ISO/IEC 14496-3 usada por las utilidades MPEG-4 de FFmpeg.
SAMPLE_RATES=(96000,88200,64000,48000,44100,32000,24000,22050,16000,12000,11025,8000,7350)
PROFILE_NAMES={1:'AAC Main',2:'AAC LC',3:'AAC SSR',4:'AAC LTP'}
CRC16_POLY=0x8005
CRC16_INIT=0xFFFF


def _u13(b3,b4,b5):
    return ((b3 & 0x03)<<11) | (b4<<3) | ((b5>>5)&0x07)


def crc16_fdk8005(data:bytes, init:int=CRC16_INIT) -> int:
    """FDK-compatible MSB-first CRC-16, polynomial 0x8005, initial value 0xFFFF."""
    crc=init & 0xFFFF
    for byte in data:
        for shift in range(7,-1,-1):
            bit=(byte>>shift)&1
            tmp=bit ^ ((crc>>15)&1)
            crc=((crc<<1)&0xFFFF)
            if tmp:
                crc ^= CRC16_POLY
    return crc & 0xFFFF


def _header(data:bytes,off:int):
    if off+7>len(data):
        return {'valid':False,'reason':'HEADER_TRUNCATED','offset':off}
    b=data[off:off+7]
    if b[0]!=0xFF or (b[1]&0xF0)!=0xF0:
        return {'valid':False,'reason':'SYNC','offset':off}
    mpeg_id=(b[1]>>3)&1
    layer=(b[1]>>1)&0x03
    protection_absent=b[1]&1
    profile=((b[2]>>6)&0x03)+1
    sfi=(b[2]>>2)&0x0F
    private_bit=(b[2]>>1)&1
    chan=((b[2]&1)<<2)|((b[3]>>6)&0x03)
    originality=(b[3]>>5)&1
    home=(b[3]>>4)&1
    copyright_id_bit=(b[3]>>3)&1
    copyright_id_start=(b[3]>>2)&1
    frame_length=_u13(b[3],b[4],b[5])
    fullness=((b[5]&0x1F)<<6)|((b[6]>>2)&0x3F)
    num_raw_blocks_field=b[6]&0x03
    rdb=num_raw_blocks_field+1
    # Cuando hay protección CRC y múltiples raw_data_blocks,
    # La cabecera ADTS contiene N palabras raw_data_block_position seguidas por
    # el crc_check de 16 bits antes del primer raw_data_block.
    protection_header_size=7 if protection_absent else 7+(2*num_raw_blocks_field)+2
    q={'valid':True,'offset':off,'mpeg_id':mpeg_id,'mpeg_version':'MPEG-4' if mpeg_id==0 else 'MPEG-2',
       'layer':layer,'protection_absent':bool(protection_absent),'crc_present':not bool(protection_absent),
       'object_type':profile,'profile_name':PROFILE_NAMES.get(profile,f'Audio Object Type {profile}'),
       'sampling_frequency_index':sfi,'sample_rate':SAMPLE_RATES[sfi] if sfi<len(SAMPLE_RATES) else None,
       'private_bit':private_bit,'channel_configuration':chan,'originality':originality,'home':home,
       'copyright_id_bit':copyright_id_bit,'copyright_id_start':copyright_id_start,
       'frame_length':frame_length,'adts_buffer_fullness':fullness,'num_raw_data_blocks_field':num_raw_blocks_field,
       'raw_data_blocks':rdb,'samples_from_header':1024*rdb,'header_size':protection_header_size}
    if layer!=0:q.update(valid=False,reason='LAYER_NONZERO')
    elif sfi>=13:q.update(valid=False,reason='SAMPLING_INDEX_INVALID')
    elif frame_length<protection_header_size:q.update(valid=False,reason='FRAME_LENGTH_INVALID')
    return q


def looks_like_adts(head:bytes):
    if len(head)<7:return False
    if head[0]!=0xFF or (head[1]&0xF6)!=0xF0:return False  # sync + layer==0; ID/protection may vary
    h=_header(head,0)
    return h.get('reason') not in ('SYNC','LAYER_NONZERO')


def _find_next_valid(data:bytes,start:int,limit:int):
    end=min(len(data)-7,start+max(0,limit))
    i=max(0,start)
    while i<=end:
        if data[i]==0xFF and (data[i+1]&0xF6)==0xF0:
            h=_header(data,i)
            if h.get('valid') and i+h['frame_length']<=len(data):return i,h
        i+=1
    return None,None


def _protection_evidence(data:bytes,off:int,end:int,h:dict):
    """Devuelve evidencia conservadora de CRC/protección para un frame ADTS completo.

    FDK autentica el CRC del header con varios bloques sobre sus 56 bits y
    cada palabra raw_data_block_position. Los CRC de un solo RDB y por bloque
    requieren analizar sintaxis raw_data_block de AAC, fuera de esta autoridad.
    """
    if not h['crc_present']:
        return {
            'protection_mode':'CRC_ABSENT','crc_scope_class':'NO_CRC_FIELD',
            'crc_check_word_hex':None,'header_crc_authenticated':None,
            'raw_data_block_crc_authentication':'NOT_APPLICABLE',
            'payload_byte_start':off+7,
        }
    n=h['num_raw_data_blocks_field']
    if n==0:
        return {
            'protection_mode':'CRC_PRESENT_SINGLE_RAW_DATA_BLOCK',
            'crc_scope_class':'SINGLE_RDB_FRAME_CRC_REQUIRES_AAC_SYNTAX',
            'crc_check_word_hex':data[off+7:off+9].hex() if off+9<=end else None,
            'header_crc_authenticated':None,
            'raw_data_block_crc_authentication':'DEFERRED_AAC_RAW_DATA_BLOCK_SYNTAX_REQUIRED',
            'payload_byte_start':off+9,
        }
    pos_start=off+7
    pos_end=pos_start+2*n
    crc_off=pos_end
    if crc_off+2>end:
        return {
            'protection_mode':'CRC_PRESENT_MULTIPLE_RAW_DATA_BLOCKS',
            'crc_scope_class':'MULTI_RDB_HEADER_CRC_SYNTAX_TRUNCATED',
            'crc_check_word_hex':None,'header_crc_authenticated':False,
            'raw_data_block_positions':[], 'raw_data_block_positions_valid':False,
            'raw_data_block_crc_authentication':'DEFERRED_AAC_RAW_DATA_BLOCK_SYNTAX_REQUIRED',
            'payload_byte_start':min(end,crc_off+2),
        }
    positions=[int.from_bytes(data[pos_start+2*i:pos_start+2*i+2],'big') for i in range(n)]
    read=int.from_bytes(data[crc_off:crc_off+2],'big')
    computed=crc16_fdk8005(data[off:pos_end])
    protected_region_bytes=h['frame_length']-7-(2*n)-2
    # Las palabras de posición son offsets acumulados dentro de los datos protegidos.
    # FDK las convierte en deltas e incluye el CRC de 16 bits de cada bloque; se exige
    # aumento estricto y espacio para un CRC de dos bytes por segmento.
    increasing=all(positions[i] > (positions[i-1] if i else 0) for i in range(len(positions)))
    within=all(2 <= p <= max(0,protected_region_bytes-2) for p in positions)
    tail_room=(not positions) or (protected_region_bytes-positions[-1] >= 2)
    pos_valid=bool(increasing and within and tail_room)
    return {
        'protection_mode':'CRC_PRESENT_MULTIPLE_RAW_DATA_BLOCKS',
        'crc_scope_class':'MULTI_RDB_HEADER_CRC_AUTHENTICATABLE',
        'crc_check_word_hex':f'{read:04x}','header_crc_computed_hex':f'{computed:04x}',
        'header_crc_authenticated':computed==read,
        'raw_data_block_positions':positions,'raw_data_block_positions_valid':pos_valid,
        'protected_raw_data_region_bytes':protected_region_bytes,
        'raw_data_block_crc_authentication':'DEFERRED_AAC_RAW_DATA_BLOCK_SYNTAX_REQUIRED',
        'payload_byte_start':crc_off+2,
    }


def analyze(path:Path,max_resync_scan_bytes:int=262144):
    data=Path(path).read_bytes(); issues=[]; frames=[]; structural=[]; gaps=[]
    off=0;idx=0;invalid_headers=0;truncated=False
    while off<len(data):
        if off+7>len(data):
            issues.append(Issue('AAC_ADTS_TRAILING_BYTES','container','Quedan bytes después del último frame ADTS completo y son insuficientes para contener otro header ADTS.',integrity='NONCONFORMANT',compatibility='POSSIBLE',byte_start=off,byte_end=len(data),evidence=[{'trailing_bytes':len(data)-off}]))
            structural.append({'type':'TRAILING_BYTES','byte_start':off,'byte_end':len(data),'length':len(data)-off});break
        h=_header(data,off)
        if not h.get('valid'):
            reason=h.get('reason')
            code={'LAYER_NONZERO':'AAC_ADTS_LAYER_NONZERO','SAMPLING_INDEX_INVALID':'AAC_ADTS_SAMPLING_INDEX_INVALID','FRAME_LENGTH_INVALID':'AAC_ADTS_FRAME_LENGTH_INVALID','HEADER_TRUNCATED':'AAC_ADTS_HEADER_TRUNCATED'}.get(reason)
            if code:
                desc={'AAC_ADTS_LAYER_NONZERO':'El campo layer de ADTS no es cero; una secuencia válida de frames ADTS requiere layer 00.',
                      'AAC_ADTS_SAMPLING_INDEX_INVALID':'El sampling_frequency_index de ADTS está reservado o no es válido y no identifica una frecuencia de muestreo compatible.',
                      'AAC_ADTS_FRAME_LENGTH_INVALID':'La longitud del frame ADTS es menor que su propio header requerido de transporte/protección y no puede describir un frame válido.',
                      'AAC_ADTS_HEADER_TRUNCATED':'El archivo termina antes de que haya un header ADTS fijo+variable completo disponible.'}[code]
                issues.append(Issue(code,'codec_header',desc,integrity='DAMAGED',playability='POSSIBLY_AFFECTED',repairability='NONE',byte_start=off,byte_end=min(len(data),off+7),evidence=[{'reason':reason}]))
                invalid_headers+=1
                declared_end=off+(h.get('frame_length') or 0)
                if reason in ('LAYER_NONZERO','SAMPLING_INDEX_INVALID') and h.get('frame_length',0)>=h.get('header_size',7) and declared_end<=len(data):
                    at_eof=declared_end==len(data)
                    next_sync=(declared_end+2<=len(data) and data[declared_end]==0xFF and (data[declared_end+1]&0xF0)==0xF0)
                    if at_eof or next_sync:
                        structural.append({'type':'INVALID_ADTS_FRAME','byte_start':off,'byte_end':declared_end,'length':declared_end-off,'reason':reason});off=declared_end;continue
                nxt,nh=_find_next_valid(data,off+1,max_resync_scan_bytes)
                if nxt is None:break
                structural.append({'type':'INVALID_HEADER_REGION','byte_start':off,'byte_end':nxt,'length':nxt-off,'reason':reason});off=nxt;continue
            nxt,nh=_find_next_valid(data,off+1,max_resync_scan_bytes)
            if nxt is None:
                issues.append(Issue('AAC_ADTS_SYNC_LOSS','container','No se encontró la syncword ADTS esperada ni pudo autenticarse un frame ADTS completo posterior dentro del recorrido acotado de resincronización.',integrity='DAMAGED',playability='POSSIBLY_AFFECTED',repairability='NONE',byte_start=off,byte_end=len(data)))
                structural.append({'type':'UNRESOLVED_SYNC_LOSS','byte_start':off,'byte_end':len(data),'length':len(data)-off});break
            gaps.append({'byte_start':off,'byte_end':nxt,'length':nxt-off})
            issues.append(Issue('AAC_ADTS_SYNC_LOSS','container','Bytes extraños o dañados interrumpen una continuidad de frames ADTS reconocible; el análisis se reanuda sólo en un header ADTS posterior e independientemente válido.',integrity='NONCONFORMANT',compatibility='POSSIBLE',playability='POSSIBLY_AFFECTED',repairability='NONE',byte_start=off,byte_end=nxt,evidence=[{'resynchronized_at':nxt,'gap_bytes':nxt-off}]))
            structural.append({'type':'SYNC_GAP','byte_start':off,'byte_end':nxt,'length':nxt-off});off=nxt;continue
        end=off+h['frame_length']
        if end>len(data):
            truncated=True
            issues.append(Issue('AAC_ADTS_TRUNCATED_FRAME','container','El header ADTS final declara un frame que se extiende más allá del fin del archivo; los bytes de payload ausentes no se sintetizan.',integrity='DAMAGED',playability='POSSIBLY_AFFECTED',repairability='NONE',byte_start=off,byte_end=len(data),evidence=[{'declared_frame_length':h['frame_length'],'available_bytes':len(data)-off,'missing_bytes':end-len(data)}]))
            structural.append({'type':'TRUNCATED_ADTS_FRAME','frame_index':idx,'byte_start':off,'byte_end':len(data),'declared_byte_end':end});break
        pe=_protection_evidence(data,off,end,h)
        payload_start=pe['payload_byte_start']
        frame_bytes=data[off:end]
        row={**h,**pe,'frame_index':idx,'byte_start':off,'byte_end':end,'payload_byte_start':payload_start,'payload_byte_end':end,
             'header_sha256':hashlib.sha256(data[off:payload_start]).hexdigest(),
             'frame_sha256':hashlib.sha256(frame_bytes).hexdigest(),'payload_sha256':hashlib.sha256(data[payload_start:end]).hexdigest()}
        if pe.get('crc_scope_class')=='MULTI_RDB_HEADER_CRC_SYNTAX_TRUNCATED':
            issues.append(Issue('AAC_ADTS_CRC_SYNTAX_TRUNCATED','codec_header','El frame ADTS protegido por CRC declara varios bloques de datos crudos, pero no contiene las palabras de posición y el crc_check del header requeridos dentro del frame declarado.',integrity='DAMAGED',playability='POSSIBLY_AFFECTED',repairability='NONE',byte_start=off,byte_end=end))
        elif pe.get('crc_scope_class')=='MULTI_RDB_HEADER_CRC_AUTHENTICATABLE':
            if pe.get('header_crc_authenticated') is False:
                issues.append(Issue('AAC_ADTS_HEADER_CRC_MISMATCH','codec_header','El CRC de header ADTS reproducible matemáticamente para un frame protegido con varios bloques de datos crudos no coincide con su crc_check almacenado.',integrity='DAMAGED',playability='POSSIBLY_AFFECTED',repairability='NONE',byte_start=off,byte_end=payload_start,evidence=[{'stored_crc':pe.get('crc_check_word_hex'),'computed_crc':pe.get('header_crc_computed_hex'),'polynomial':'0x8005','initial_value':'0xFFFF'}]))
            if pe.get('raw_data_block_positions_valid') is False:
                issues.append(Issue('AAC_ADTS_RAW_DATA_BLOCK_POSITION_INVALID','codec_header','Los valores raw_data_block_position de ADTS para un frame protegido con varios bloques no son estrictamente crecientes o no dejan espacio para las palabras CRC requeridas por bloque dentro de la región protegida declarada.',integrity='DAMAGED',playability='POSSIBLY_AFFECTED',repairability='NONE',byte_start=off+7,byte_end=payload_start,evidence=[{'positions':pe.get('raw_data_block_positions'),'protected_raw_data_region_bytes':pe.get('protected_raw_data_region_bytes')}]))
        frames.append(row);structural.append({'type':'ADTS_FRAME','frame_index':idx,'byte_start':off,'byte_end':end,'frame_length':h['frame_length'],'header_size':h['header_size'],'protection_mode':pe.get('protection_mode')})
        idx+=1;off=end
    # Consistencia de parámetros entre frames completos. Los cambios no se presumen corruptos,
    # pero son hallazgos estructurales relevantes dentro de un stream elemental ADTS.
    changes=[];protection_changes=[]
    if frames:
        base=frames[0]
        for r in frames[1:]:
            fields=[]
            for k in ('mpeg_id','object_type','sampling_frequency_index','channel_configuration'):
                if r.get(k)!=base.get(k):fields.append({'field':k,'initial':base.get(k),'observed':r.get(k)})
            if fields:changes.append({'frame_index':r['frame_index'],'byte_start':r['byte_start'],'changes':fields})
            if r.get('crc_present')!=base.get('crc_present'):
                protection_changes.append({'frame_index':r['frame_index'],'byte_start':r['byte_start'],'initial_crc_present':base.get('crc_present'),'observed_crc_present':r.get('crc_present')})
    if changes:
        issues.append(Issue('AAC_ADTS_PARAMETER_CHANGE','codec_header','Un stream elemental ADTS cambia el MPEG ID, el tipo de objeto AAC, el índice de frecuencia de muestreo o la configuración de canales entre frames completos; el cambio se informa y no se normaliza.',integrity='NONCONFORMANT',compatibility='POSSIBLE',playability='POSSIBLY_AFFECTED',repairability='NONE',evidence=changes[:64]))
    if protection_changes:
        issues.append(Issue('AAC_ADTS_PROTECTION_MODE_CHANGE','codec_header','Un stream elemental ADTS cambia el modo de protección CRC entre frames completos; el cambio se preserva y se informa en lugar de normalizarse.',integrity='NONCONFORMANT',compatibility='POSSIBLE',playability='POSSIBLY_AFFECTED',repairability='NONE',evidence=protection_changes[:64]))
    rates=sorted({r['sample_rate'] for r in frames if r.get('sample_rate')})
    chans=sorted({r['channel_configuration'] for r in frames})
    ots=sorted({r['object_type'] for r in frames})
    crc_count=sum(1 for r in frames if r['crc_present'])
    single_crc=sum(1 for r in frames if r.get('crc_scope_class')=='SINGLE_RDB_FRAME_CRC_REQUIRES_AAC_SYNTAX')
    multi_checked=sum(1 for r in frames if r.get('crc_scope_class')=='MULTI_RDB_HEADER_CRC_AUTHENTICATABLE')
    multi_ok=sum(1 for r in frames if r.get('header_crc_authenticated') is True)
    multi_bad=sum(1 for r in frames if r.get('header_crc_authenticated') is False and r.get('crc_scope_class')=='MULTI_RDB_HEADER_CRC_AUTHENTICATABLE')
    pos_bad=sum(1 for r in frames if r.get('crc_scope_class')=='MULTI_RDB_HEADER_CRC_AUTHENTICATABLE' and r.get('raw_data_block_positions_valid') is False)
    if multi_bad:
        crc_validation='SUPPORTED_MULTI_RDB_HEADER_CRC_MISMATCH_DETECTED'
    elif multi_checked:
        crc_validation='SUPPORTED_MULTI_RDB_HEADER_CRC_SCOPES_AUTHENTICATED' if multi_ok==multi_checked else 'PARTIAL_MULTI_RDB_HEADER_CRC_EVIDENCE'
    elif single_crc:
        crc_validation='SINGLE_RDB_CRC_PRESENT_AUTHENTICATION_DEFERRED'
    else:
        crc_validation='CRC_NOT_PRESENT'
    total_samples=sum(r['samples_from_header'] for r in frames)
    facts={'adts':{
        'present':bool(frames or (len(data)>=2 and data[:1]==b'\xff')),
        'file_size':len(data),'complete_frame_count':len(frames),'invalid_header_count':invalid_headers,
        'sync_gap_count':len(gaps),'sync_gaps':gaps,'truncated_final_frame':truncated,
        'first_frame_offset':frames[0]['byte_start'] if frames else None,'last_complete_frame_end':frames[-1]['byte_end'] if frames else None,
        'sample_rates_hz':rates,'channel_configurations':chans,'object_types':ots,
        'profile_names':sorted({r['profile_name'] for r in frames}),
        'crc_present_frame_count':crc_count,'crc_absent_frame_count':len(frames)-crc_count,
        'crc_validation':crc_validation,'crc_policy':'ADTS_CRC_SCOPE_AUTHENTICATION_V1',
        'crc_polynomial_hex':'0x8005','crc_initial_value_hex':'0xFFFF','crc_bit_order':'MSB_FIRST',
        'single_rdb_crc_present_count':single_crc,'single_rdb_crc_authentication_deferred_count':single_crc,
        'multi_rdb_header_crc_checked_count':multi_checked,'multi_rdb_header_crc_authenticated_count':multi_ok,
        'multi_rdb_header_crc_mismatch_count':multi_bad,'multi_rdb_position_invalid_count':pos_bad,
        'raw_data_block_crc_authentication':'DEFERRED_AAC_RAW_DATA_BLOCK_SYNTAX_REQUIRED' if crc_count else 'NOT_APPLICABLE',
        'frame_provenance_policy':'AAC_ADTS_FRAME_PROVENANCE',
        'frame_sha256_count':len(frames),'payload_sha256_count':len(frames),'header_sha256_count':len(frames),
        'raw_data_blocks_values':sorted({r['raw_data_blocks'] for r in frames}),
        'header_sample_count_total':total_samples,
        'header_duration_seconds':(total_samples/frames[0]['sample_rate']) if frames and len(rates)==1 and rates[0] else None,
        'parameter_change_count':len(changes),'protection_mode_change_count':len(protection_changes),
        'protection_modes':sorted({r.get('protection_mode') for r in frames}),
        'all_complete_frames_physically_contiguous':not gaps and not invalid_headers and not truncated and (frames[-1]['byte_end']==len(data) if frames else False),
    },'frames':frames}
    metadata={'aac_profile':frames[0]['profile_name'] if frames else None,'sample_rate':frames[0]['sample_rate'] if frames else None,'channel_configuration':frames[0]['channel_configuration'] if frames else None}
    return {'metadata':metadata,'facts':facts,'structural_map':structural,'issues':issues}
