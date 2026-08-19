from __future__ import annotations
from pathlib import Path
import hashlib
import uuid
from app.models import Issue

# GUID de objetos ASF (texto tras decodificar el GUID little-endian de Windows).
ASF_HEADER='75b22630-668e-11cf-a6d9-00aa0062ce6c'
ASF_DATA='75b22636-668e-11cf-a6d9-00aa0062ce6c'
ASF_FILE_PROPERTIES='8cabdca1-a947-11cf-8ee4-00c00c205365'
ASF_STREAM_PROPERTIES='b7dc0791-a9b7-11cf-8ee6-00c00c205365'
ASF_HEADER_EXTENSION='5fbf03b5-a92e-11cf-8ee3-00c00c205365'
ASF_AUDIO_MEDIA='f8699e40-5b4d-11cf-a8fd-00805f5c442b'

WMA_TAGS={
    0x000A:'Windows Media Audio Voice',
    0x0160:'Windows Media Audio 1',
    0x0161:'Windows Media Audio Standard',
    0x0162:'Windows Media Audio Professional',
    0x0163:'Windows Media Audio Lossless',
    0x0164:'Windows Media Audio Professional over S/PDIF',
}
OBJ_NAMES={
    ASF_HEADER:'Header Object', ASF_DATA:'Data Object', ASF_FILE_PROPERTIES:'File Properties Object',
    ASF_STREAM_PROPERTIES:'Stream Properties Object', ASF_HEADER_EXTENSION:'Header Extension Object',
    '75b22633-668e-11cf-a6d9-00aa0062ce6c':'Content Description Object',
    'd2d0a440-e307-11d2-97f0-00a0c95ea850':'Extended Content Description Object',
    '86d15240-311d-11d0-a3a4-00a0c90348f6':'Codec List Object',
    '33000890-e5b1-11cf-89f4-00a0c90349cb':'Simple Index Object',
}

def _u16(b,o=0):return int.from_bytes(b[o:o+2],'little')
def _u32(b,o=0):return int.from_bytes(b[o:o+4],'little')
def _u64(b,o=0):return int.from_bytes(b[o:o+8],'little')
def _guid(b,o=0):
    if o+16>len(b):return None
    try:return str(uuid.UUID(bytes_le=bytes(b[o:o+16])))
    except Exception:return None

def _size_code(code:int)->int:
    return {0:0,1:1,2:2,3:4}.get(code,0)

def _read_var(buf:bytes,pos:int,size:int):
    if size==0:return None,pos
    if pos+size>len(buf):raise EOFError
    return int.from_bytes(buf[pos:pos+size],'little'),pos+size

def _parse_packet(buf:bytes, packet_index:int, byte_start:int, declared_packet_size:int, known_streams:set[int]):
    q={'index':packet_index,'byte_start':byte_start,'byte_end':byte_start+len(buf),'packet_bytes':len(buf),'valid':False,
       'payloads':[],'send_time_ms':None,'duration_ms':None,'padding_length':0,'sequence':None,'multiple_payloads':False}
    try:
        pos=0
        if len(buf)<8:raise EOFError
        first=buf[pos];pos+=1
        if first&0x80:
            # Flags de corrección ASF; la forma común usa una longitud en el nibble bajo.
            if first&0x60:
                # Un tipo de longitud no nulo es legal en ASF, pero no está modelado aquí.
                q['unsupported_error_correction_length_type']=True
                return q
            ec_len=first&0x0f
            if pos+ec_len>len(buf):raise EOFError
            q['error_correction_present']=True;q['error_correction_length']=ec_len;pos+=ec_len
            if pos>=len(buf):raise EOFError
            len_flags=buf[pos];pos+=1
        else:
            q['error_correction_present']=False;q['error_correction_length']=0;len_flags=first
        if pos>=len(buf):raise EOFError
        prop_flags=buf[pos];pos+=1
        q['length_type_flags']=len_flags;q['property_flags']=prop_flags
        packet_len_size=_size_code((len_flags&0x60)>>5)
        seq_size=_size_code((len_flags&0x06)>>1)
        pad_size=_size_code((len_flags&0x18)>>3)
        packet_len,pos=_read_var(buf,pos,packet_len_size)
        seq,pos=_read_var(buf,pos,seq_size)
        padding,pos=_read_var(buf,pos,pad_size)
        packet_len=packet_len or declared_packet_size or len(buf)
        padding=padding or 0
        if pos+6>len(buf):raise EOFError
        send=_u32(buf,pos);pos+=4;dur=_u16(buf,pos);pos+=2
        q.update(packet_length=packet_len,sequence=seq,padding_length=padding,send_time_ms=send,duration_ms=dur,
                 multiple_payloads=bool(len_flags&1))
        if packet_len<=0 or packet_len>len(buf):
            q['packet_length_invalid']=True;return q
        payload_count=1;payload_len_size=0
        if len_flags&1:
            if pos>=packet_len:raise EOFError
            pf=buf[pos];pos+=1;payload_count=pf&0x3f;payload_len_size=_size_code((pf&0xc0)>>6)
            q['payload_flags']=pf
            if payload_count==0 or payload_count>63:return q
        q['payload_count']=payload_count
        stream_num_size=_size_code((prop_flags&0xc0)>>6)
        media_obj_size=_size_code((prop_flags&0x30)>>4)
        offset_size=_size_code((prop_flags&0x0c)>>2)
        repl_len_size=_size_code(prop_flags&0x03)
        if stream_num_size==0:return q
        payload_region_end=packet_len-padding
        if payload_region_end<pos or payload_region_end>len(buf):return q
        for pi in range(payload_count):
            p0=pos
            stream_raw,pos=_read_var(buf,pos,stream_num_size)
            media_obj,pos=_read_var(buf,pos,media_obj_size)
            media_off,pos=_read_var(buf,pos,offset_size)
            repl_len,pos=_read_var(buf,pos,repl_len_size)
            repl_len=repl_len or 0
            if pos+repl_len>payload_region_end:raise EOFError
            repl_start=pos;repl=buf[pos:pos+repl_len];pos+=repl_len
            plen=None
            if len_flags&1:
                plen,pos=_read_var(buf,pos,payload_len_size)
                if plen is None:return q
            else:
                plen=payload_region_end-pos
            if plen<0 or pos+plen>payload_region_end:return q
            stream_number=(stream_raw or 0)&0x7f
            key_frame=bool((stream_raw or 0)&0x80)
            presentation_time_ms=_u32(repl,4) if len(repl)>=8 else None
            media_object_size=_u32(repl,0) if len(repl)>=8 else None
            compressed_payload=bool(repl_len==1)
            payload_data_start=pos;payload_data_end=pos+plen
            q['payloads'].append({'index':pi,'byte_start':byte_start+p0,'byte_end':byte_start+payload_data_end,
                                  'payload_data_byte_start':byte_start+payload_data_start,'payload_data_byte_end':byte_start+payload_data_end,
                                  'payload_sha256':hashlib.sha256(buf[payload_data_start:payload_data_end]).hexdigest(),
                                  'stream_number':stream_number,'stream_declared':stream_number in known_streams,
                                  'key_frame':key_frame,'media_object_number':media_obj,'media_object_number_size_bytes':media_obj_size,'offset_into_media_object':media_off,
                                  'replicated_data_length':repl_len,'replicated_data_byte_start':byte_start+repl_start,'replicated_data_byte_end':byte_start+repl_start+repl_len,'media_object_size':media_object_size,
                                  'presentation_time_ms':presentation_time_ms,'compressed_payload':compressed_payload,
                                  'payload_data_length':plen})
            pos+=plen
        q['payload_bytes_end']=pos;q['payload_region_end']=payload_region_end
        q['valid']=(pos==payload_region_end and all(x['stream_declared'] for x in q['payloads']))
        return q
    except Exception:
        q['truncated_or_malformed']=True
        return q

def _parse_waveformatex(ts:bytes):
    q={'present':len(ts)>=18,'valid':False}
    if len(ts)<18:return q
    tag=_u16(ts,0);channels=_u16(ts,2);sr=_u32(ts,4);avg=_u32(ts,8);align=_u16(ts,12);bps=_u16(ts,14);cb=_u16(ts,16)
    q.update(format_tag=tag,format_tag_hex=f'0x{tag:04x}',codec_name=WMA_TAGS.get(tag,'UNKNOWN'),channels=channels,
             sample_rate=sr,avg_bytes_per_sec=avg,nominal_bit_rate=avg*8,block_align=align,bits_per_sample=bps,
             extra_size=cb,type_specific_length=len(ts),extra_data_hex=ts[18:18+min(cb,64)].hex())
    q['valid']=(tag in WMA_TAGS and channels>0 and sr>0 and avg>0 and align>0 and 18+cb<=len(ts))
    return q



def _merge_intervals(intervals):
    merged=[]
    for start,end in sorted(intervals):
        if not merged or start>merged[-1][1]:
            merged.append([start,end])
        else:
            merged[-1][1]=max(merged[-1][1],end)
    return merged

def _build_media_object_provenance(packets:list[dict], audio_stream_numbers:set[int], source_data:bytes):
    """Reensambla objetos ASF ordinarios desde fragmentos de payload de paquetes.

    No afirma semántica PCM exacta por muestra. Sólo demuestra si los rangos de
    bytes forman un objeto completo, contiguo y sin superposición. El modo de
    payload comprimido se contabiliza, pero aquí no se reensambla.
    """
    grouped={}
    compressed=[]
    for packet in packets:
        if not packet.get('valid'):
            continue
        for payload in packet.get('payloads') or []:
            if payload.get('stream_number') not in audio_stream_numbers:
                continue
            if payload.get('compressed_payload'):
                compressed.append({'packet_index':packet.get('index'),'payload_index':payload.get('index'),
                                   'stream_number':payload.get('stream_number'),'byte_start':payload.get('payload_data_byte_start'),
                                   'byte_end':payload.get('payload_data_byte_end'),'media_object_number_size_bytes':payload.get('media_object_number_size_bytes')})
                continue
            key=(payload.get('stream_number'),payload.get('media_object_number'))
            grouped.setdefault(key,[]).append({
                'packet_index':packet.get('index'),'payload_index':payload.get('index'),
                'media_object_number_size_bytes':payload.get('media_object_number_size_bytes'),
                'offset':payload.get('offset_into_media_object'),'length':payload.get('payload_data_length'),
                'byte_start':payload.get('payload_data_byte_start'),'byte_end':payload.get('payload_data_byte_end'),
                'payload_sha256':payload.get('payload_sha256'),'media_object_size':payload.get('media_object_size'),
                'presentation_time_ms':payload.get('presentation_time_ms'),'replicated_data_byte_start':payload.get('replicated_data_byte_start'),
                'replicated_data_byte_end':payload.get('replicated_data_byte_end')})
    objects=[]
    anomaly_buckets={'replicated_data_mismatch':[],'fragment_gap':[],'fragment_overlap':[],
                     'fragment_bounds_invalid':[],'incomplete':[]}
    for (stream_no,obj_no),frags in sorted(grouped.items(), key=lambda kv:(kv[0][0], kv[0][1] if kv[0][1] is not None else -1)):
        frags=sorted(frags,key=lambda x:((x.get('offset') if x.get('offset') is not None else -1),x.get('packet_index') or -1,x.get('payload_index') or -1))
        sizes={f.get('media_object_size') for f in frags if f.get('media_object_size') is not None}
        times={f.get('presentation_time_ms') for f in frags if f.get('presentation_time_ms') is not None}
        replicated_consistent=(len(sizes)==1 and len(times)==1 and all(f.get('offset') is not None and f.get('length') is not None for f in frags))
        declared_size=next(iter(sizes)) if len(sizes)==1 else None
        presentation_time=next(iter(times)) if len(times)==1 else None
        intervals=[];gaps=[];overlaps=[];bounds_invalid=False
        for f in frags:
            o=f.get('offset');ln=f.get('length')
            if o is None or ln is None or o<0 or ln<0:
                bounds_invalid=True;continue
            e=o+ln;intervals.append((o,e))
            if declared_size is not None and e>declared_size:
                bounds_invalid=True
        sorted_intervals=sorted(intervals)
        if sorted_intervals:
            cursor=sorted_intervals[0][1]
            if sorted_intervals[0][0]>0:gaps.append([0,sorted_intervals[0][0]])
            for st,en in sorted_intervals[1:]:
                if st>cursor:gaps.append([cursor,st])
                elif st<cursor:overlaps.append([st,min(cursor,en)])
                cursor=max(cursor,en)
            if declared_size is not None and cursor<declared_size:gaps.append([cursor,declared_size])
        elif declared_size:
            gaps=[[0,declared_size]]
        merged=_merge_intervals(intervals);covered=sum(e-s for s,e in merged)
        starts_at_zero=bool(sorted_intervals and sorted_intervals[0][0]==0)
        ends_at_declared=bool(declared_size is not None and merged and merged[-1][1]==declared_size)
        complete=bool(replicated_consistent and declared_size is not None and declared_size>=0 and starts_at_zero and ends_at_declared and not gaps and not overlaps and not bounds_invalid and covered==declared_size)
        if not replicated_consistent:completion='REPLICATED_DATA_MISMATCH';anomaly_buckets['replicated_data_mismatch'].append(obj_no)
        elif bounds_invalid:completion='FRAGMENT_BOUNDS_INVALID';anomaly_buckets['fragment_bounds_invalid'].append(obj_no)
        elif overlaps:completion='FRAGMENT_OVERLAP';anomaly_buckets['fragment_overlap'].append(obj_no)
        elif gaps:
            if gaps[0][0]==0:completion='MISSING_PREFIX'
            elif declared_size is not None and gaps[-1][1]==declared_size:completion='MISSING_TAIL'
            else:completion='INTERNAL_GAP'
            anomaly_buckets['fragment_gap'].append(obj_no);anomaly_buckets['incomplete'].append(obj_no)
        elif not complete:completion='INCOMPLETE';anomaly_buckets['incomplete'].append(obj_no)
        else:completion='COMPLETE'
        packet_indices=sorted({f.get('packet_index') for f in frags if f.get('packet_index') is not None})
        number_sizes={f.get('media_object_number_size_bytes') for f in frags if f.get('media_object_number_size_bytes') is not None}
        media_object_number_size_bytes=next(iter(number_sizes)) if len(number_sizes)==1 else None
        assembled_sha256=None
        if complete:
            h=hashlib.sha256()
            for f in sorted(frags,key=lambda x:x.get('offset') or 0):
                bs=f.get('byte_start');be=f.get('byte_end')
                if bs is None or be is None or bs<0 or be<bs or be>len(source_data):
                    h=None;break
                h.update(source_data[bs:be])
            assembled_sha256=h.hexdigest() if h is not None else None
        objects.append({'stream_number':stream_no,'media_object_number':obj_no,'completion':completion,'complete':complete,
                        'fragment_count':len(frags),'packet_indices':packet_indices,'spans_packets':len(packet_indices)>1,
                        'declared_size':declared_size,'covered_unique_bytes':covered,'starts_at_zero':starts_at_zero,
                        'ends_at_declared_size':ends_at_declared,'replicated_data_consistent':replicated_consistent,
                        'presentation_time_ms':presentation_time,'assembled_sha256':assembled_sha256,
                        'media_object_number_size_bytes':media_object_number_size_bytes,
                        'gaps':gaps,'overlaps':overlaps,'bounds_valid':not bounds_invalid,
                        'fragments':frags})
    complete=[x for x in objects if x['complete']]
    incomplete=[x for x in objects if not x['complete']]
    pts=[x['presentation_time_ms'] for x in complete if x.get('presentation_time_ms') is not None]
    return {'policy':'ASF_WMA_MEDIA_OBJECT_PROVENANCE_EVIDENCE_ONLY',
            'publication_enabled':False,'pcm_sample_exact_claim':False,
            'ordinary_payload_media_objects_observed':len(objects),'complete_media_objects':len(complete),
            'incomplete_media_objects':len(incomplete),'fragmented_media_objects':sum(1 for x in objects if x['fragment_count']>1),
            'multi_packet_media_objects':sum(1 for x in objects if x['spans_packets']),
            'compressed_payloads_unmodeled':len(compressed),'compressed_payload_evidence':compressed,
            'presentation_time_start_ms':min(pts) if pts else None,'presentation_time_end_ms':max(pts) if pts else None,
            'media_objects':objects,'anomalies':anomaly_buckets}

def analyze(path:Path):
    data=path.read_bytes();n=len(data);issues=[];struct=[];facts={'asf':{},'streams':[]};meta={}
    # Header Object.
    if n<30 or _guid(data,0)!=ASF_HEADER:
        issues.append(Issue('ASF_HEADER_OBJECT_INVALID','container','El Header Object de ASF falta o está mal formado en el byte 0.',integrity='DAMAGED',playability='BLOCKING',repairability='NONE',byte_start=0,byte_end=min(n,30)))
        return {'codec':'wma','facts':facts,'metadata':meta,'structural_map':struct,'issues':issues}
    hsize=_u64(data,16);hcount=_u32(data,24);res1=data[28];res2=data[29]
    facts['asf'].update(header_object_size=hsize,header_object_count_declared=hcount,header_reserved_1=res1,header_reserved_2=res2)
    struct.append({'kind':'ASF_OBJECT','name':'Header Object','guid':ASF_HEADER,'byte_start':0,'byte_end':min(hsize,n),'declared_size':hsize})
    if hsize<30 or hsize>n:
        issues.append(Issue('ASF_HEADER_SIZE_INVALID','container','El tamaño del Header Object de ASF excede los límites físicos del archivo.',integrity='DAMAGED',playability='BLOCKING',repairability='NONE',byte_start=0,byte_end=min(n,30),evidence=[{'declared_size':hsize,'file_size':n}]))
        header_end=min(max(hsize,30),n)
    else:header_end=hsize
    if (res1,res2)!=(1,2):
        issues.append(Issue('ASF_HEADER_RESERVED_BYTES_INVALID','container','Los bytes reservados del Header Object de ASF no contienen los valores requeridos 0x01/0x02.',integrity='NONCONFORMANT',playability='POSSIBLY_AFFECTED',repairability='NONE',byte_start=28,byte_end=30,evidence=[{'reserved_1':res1,'reserved_2':res2}]))
    off=30;sub=[]
    for i in range(hcount):
        if off+24>header_end:
            issues.append(Issue('ASF_HEADER_SUBOBJECT_COUNT_MISMATCH','container','El Header Object declara más subobjetos de los que caben dentro del Header Object.',integrity='DAMAGED',playability='BLOCKING',repairability='NONE',byte_start=off,byte_end=header_end,evidence=[{'declared_count':hcount,'parsed_count':len(sub)}]));break
        g=_guid(data,off);sz=_u64(data,off+16)
        if sz<24 or off+sz>header_end:
            issues.append(Issue('ASF_HEADER_SUBOBJECT_SIZE_INVALID','container','Un subobjeto del Header Object de ASF tiene un tamaño no válido o atraviesa el límite del Header Object.',integrity='DAMAGED',playability='BLOCKING',repairability='NONE',byte_start=off,byte_end=min(off+24,n),evidence=[{'guid':g,'declared_size':sz,'header_end':header_end}]))
            break
        q={'index':i,'guid':g,'name':OBJ_NAMES.get(g,'Header Object desconocido'),'byte_start':off,'byte_end':off+sz,'declared_size':sz}
        sub.append(q);struct.append({'kind':'ASF_HEADER_SUBOBJECT',**q});off+=sz
    facts['asf']['header_object_count_parsed']=len(sub);facts['asf']['header_subobjects']=[{'guid':x['guid'],'name':x['name'],'size':x['declared_size']} for x in sub]
    if off!=header_end:
        issues.append(Issue('ASF_HEADER_BOUNDARY_MISMATCH','container','Los subobjetos analizados del Header Object de ASF no ocupan exactamente el tamaño declarado del Header Object.',integrity='NONCONFORMANT',playability='POSSIBLY_AFFECTED',repairability='NONE',byte_start=min(off,header_end),byte_end=max(off,header_end),evidence=[{'parsed_end':off,'header_end':header_end}]))
    # File Properties Object.
    fps=[x for x in sub if x['guid']==ASF_FILE_PROPERTIES];fp=None
    if len(fps)!=1:
        issues.append(Issue('ASF_FILE_PROPERTIES_OBJECT_COUNT_INVALID','container','El Header Object de ASF debe contener exactamente un File Properties Object.',integrity='DAMAGED',playability='BLOCKING',repairability='NONE',evidence=[{'count':len(fps)}]))
    elif fps[0]['declared_size']>=104:
        o=fps[0]['byte_start'];fp={'file_id':_guid(data,o+24),'file_size':_u64(data,o+40),'creation_time_100ns':_u64(data,o+48),
          'data_packets_count':_u64(data,o+56),'play_duration_100ns':_u64(data,o+64),'send_duration_100ns':_u64(data,o+72),
          'preroll_ms':_u64(data,o+80),'flags':_u32(data,o+88),'min_packet_size':_u32(data,o+92),'max_packet_size':_u32(data,o+96),'max_bitrate':_u32(data,o+100)}
        fp['broadcast']=bool(fp['flags']&1);fp['seekable']=bool(fp['flags']&2);facts['asf']['file_properties']=fp
        if not fp['broadcast'] and fp['file_size']!=n:
            issues.append(Issue('ASF_FILE_SIZE_MISMATCH','container','El campo de tamaño del File Properties Object no coincide con el tamaño físico del archivo.',integrity='NONCONFORMANT',playability='POSSIBLY_AFFECTED',repairability='NONE',byte_start=o+40,byte_end=o+48,evidence=[{'declared_file_size':fp['file_size'],'physical_file_size':n}]))
        if fp['min_packet_size']==0 or fp['max_packet_size']==0 or fp['min_packet_size']>fp['max_packet_size']:
            issues.append(Issue('ASF_PACKET_SIZE_FIELDS_INVALID','container','El File Properties Object contiene tamaños mínimo o máximo de paquete de datos no válidos.',integrity='DAMAGED',playability='BLOCKING',repairability='NONE',byte_start=o+92,byte_end=o+100,evidence=[{'min_packet_size':fp['min_packet_size'],'max_packet_size':fp['max_packet_size']}]))
    else:
        issues.append(Issue('ASF_FILE_PROPERTIES_OBJECT_TRUNCATED','container','El File Properties Object es más corto que la estructura fija obligatoria.',integrity='DAMAGED',playability='BLOCKING',repairability='NONE',byte_start=fps[0]['byte_start'],byte_end=fps[0]['byte_end']))
    # Stream Properties Object y WAVEFORMATEX.
    sps=[x for x in sub if x['guid']==ASF_STREAM_PROPERTIES]
    if not sps:issues.append(Issue('ASF_STREAM_PROPERTIES_MISSING','container','El Header Object de ASF no contiene ningún Stream Properties Object.',integrity='DAMAGED',playability='BLOCKING',repairability='NONE'))
    streams=[]
    for x in sps:
        o=x['byte_start'];sz=x['declared_size'];st={'object_offset':o,'object_size':sz}
        if sz<78:
            st['valid']=False;streams.append(st);continue
        stype=_guid(data,o+24);ectype=_guid(data,o+40);timeoff=_u64(data,o+56);tslen=_u32(data,o+64);eclen=_u32(data,o+68);flags=_u16(data,o+72);stream_no=flags&0x7f;encrypted=bool(flags&0x8000)
        st.update(stream_type_guid=stype,error_correction_type_guid=ectype,time_offset_100ns=timeoff,type_specific_length=tslen,error_correction_length=eclen,
                  flags=flags,stream_number=stream_no,encrypted=encrypted,is_audio=(stype==ASF_AUDIO_MEDIA))
        body=o+78
        if body+tslen+eclen>x['byte_end']:
            st['valid']=False;issues.append(Issue('ASF_STREAM_PROPERTIES_LENGTH_INVALID','container','Los datos específicos de tipo o corrección de errores del Stream Properties Object exceden su límite.',integrity='DAMAGED',playability='BLOCKING',repairability='NONE',byte_start=o,byte_end=x['byte_end'],evidence=[{'stream_number':stream_no,'type_specific_length':tslen,'error_correction_length':eclen}]))
        elif st['is_audio']:
            wf=_parse_waveformatex(data[body:body+tslen]);st['waveformatex']=wf;st['valid']=wf['valid'] and stream_no>0 and not encrypted
            if not wf['valid']:
                issues.append(Issue('ASF_WMA_WAVEFORMATEX_INVALID','codec_header','El Stream Properties Object de audio ASF contiene una estructura WAVEFORMATEX de Windows Media Audio no válida o no compatible.',integrity='DAMAGED',playability='BLOCKING',repairability='NONE',byte_start=body,byte_end=body+tslen,evidence=[wf]))
            if encrypted:
                issues.append(Issue('ASF_ENCRYPTED_AUDIO_UNSUPPORTED','codec_header','El stream de audio ASF está marcado como cifrado; la política vigente no puede autenticar la estructura del payload del codec.',integrity='SUSPICIOUS',playability='POSSIBLY_AFFECTED',repairability='NONE',byte_start=o+72,byte_end=o+74))
        else:
            st['valid']=stream_no>0
        streams.append(st)
    facts['streams']=streams
    audio=[s for s in streams if s.get('is_audio')]
    facts['asf']['audio_stream_count']=len(audio);facts['asf']['stream_count']=len(streams)
    if not audio:
        issues.append(Issue('ASF_AUDIO_STREAM_MISSING','codec_header','No se encontró ningún Stream Properties Object de audio ASF.',integrity='DAMAGED',playability='BLOCKING',repairability='NONE'))
    elif audio:
        wf=audio[0].get('waveformatex') or {}
        meta={'format_tag':wf.get('format_tag'),'codec_name':wf.get('codec_name'),'sample_rate':wf.get('sample_rate'),'channels':wf.get('channels'),
              'nominal_bit_rate':wf.get('nominal_bit_rate'),'block_align':wf.get('block_align'),'bits_per_sample':wf.get('bits_per_sample')}
    # Data Object debe seguir al Header Object.
    data_obj=None
    if header_end+24<=n and _guid(data,header_end)==ASF_DATA:
        dsize=_u64(data,header_end+16);data_obj={'byte_start':header_end,'declared_size':dsize,'byte_end':header_end+dsize}
        struct.append({'kind':'ASF_OBJECT','name':'Data Object','guid':ASF_DATA,'byte_start':header_end,'byte_end':min(header_end+dsize,n),'declared_size':dsize})
        if dsize<50 or header_end+dsize>n:
            issues.append(Issue('ASF_DATA_OBJECT_TRUNCATED','container','El Data Object de ASF es más corto de lo declarado o se extiende más allá del archivo físico.',integrity='DAMAGED',playability='POSSIBLY_AFFECTED',repairability='NONE',byte_start=header_end,byte_end=n,evidence=[{'declared_size':dsize,'available_size':n-header_end}]))
        if header_end+50<=n:
            data_obj.update(file_id=_guid(data,header_end+24),total_data_packets=_u64(data,header_end+40),reserved=_u16(data,header_end+48),packet_data_start=header_end+50)
    else:
        issues.append(Issue('ASF_DATA_OBJECT_MISSING_OR_MISPLACED','container','El Data Object obligatorio de ASF no aparece inmediatamente después del Header Object.',integrity='DAMAGED',playability='BLOCKING',repairability='NONE',byte_start=header_end,byte_end=min(header_end+24,n)))
    facts['asf']['data_object']=data_obj
    packets=[]
    known_streams={s.get('stream_number') for s in streams if s.get('stream_number')}
    if data_obj and data_obj.get('packet_data_start') is not None and fp:
        if data_obj.get('file_id')!=fp.get('file_id'):
            issues.append(Issue('ASF_DATA_FILE_ID_MISMATCH','container','El File ID del Data Object no coincide con el File ID del File Properties Object.',integrity='DAMAGED',playability='POSSIBLY_AFFECTED',repairability='NONE',byte_start=header_end+24,byte_end=header_end+40))
        if not fp['broadcast'] and data_obj.get('total_data_packets')!=fp.get('data_packets_count'):
            issues.append(Issue('ASF_DATA_PACKET_COUNT_FIELDS_DISAGREE','container','La cantidad de paquetes del Data Object no coincide con la indicada por el File Properties Object.',integrity='NONCONFORMANT',playability='POSSIBLY_AFFECTED',repairability='NONE',byte_start=header_end+40,byte_end=header_end+48,evidence=[{'data_object_count':data_obj.get('total_data_packets'),'file_properties_count':fp.get('data_packets_count')}]))
        packet_size=fp.get('max_packet_size') if fp.get('min_packet_size')==fp.get('max_packet_size') else None
        available=max(0,min(n,data_obj['byte_end'])-data_obj['packet_data_start'])
        facts['asf']['packet_region_bytes_available']=available;facts['asf']['fixed_packet_size']=packet_size
        if packet_size:
            physical=available//packet_size;rem=available%packet_size;facts['asf']['physical_complete_packet_count']=physical;facts['asf']['packet_region_remainder_bytes']=rem
            expected=data_obj.get('total_data_packets')
            if not fp['broadcast'] and expected is not None and physical!=expected:
                issues.append(Issue('ASF_DATA_PACKET_COUNT_MISMATCH','container','La cantidad física de paquetes ASF completos no coincide con la cantidad declarada.',integrity='DAMAGED',playability='POSSIBLY_AFFECTED',repairability='NONE',byte_start=data_obj['packet_data_start'],byte_end=min(n,data_obj['byte_end']),evidence=[{'declared_packets':expected,'physical_complete_packets':physical,'packet_size':packet_size,'remainder_bytes':rem}]))
            if rem:
                issues.append(Issue('ASF_PARTIAL_DATA_PACKET_AT_END','container','El objeto Data ASF termina con un paquete de datos de tamaño fijo incompleto.',integrity='DAMAGED',playability='POSSIBLY_AFFECTED',repairability='NONE',byte_start=data_obj['packet_data_start']+physical*packet_size,byte_end=min(n,data_obj['byte_end']),evidence=[{'partial_bytes':rem,'packet_size':packet_size}]))
            for i in range(physical):
                s=data_obj['packet_data_start']+i*packet_size;b=data[s:s+packet_size];pq=_parse_packet(b,i,s,packet_size,known_streams);packets.append(pq);struct.append({'kind':'ASF_DATA_PACKET','index':i,'byte_start':s,'byte_end':s+packet_size,'send_time_ms':pq.get('send_time_ms'),'duration_ms':pq.get('duration_ms'),'payload_count':pq.get('payload_count'),'valid':pq.get('valid')})
            bad=[p['index'] for p in packets if not p.get('valid')]
            if bad:
                issues.append(Issue('ASF_DATA_PACKET_HEADER_INVALID','packet','Uno o más headers de paquetes de datos o descriptores de payload ASF están mal formados o referencian streams no declarados.',integrity='DAMAGED',playability='POSSIBLY_AFFECTED',repairability='NONE',evidence=[{'packet_indices':bad[:64]}]))
            sends=[p.get('send_time_ms') for p in packets if p.get('valid') and p.get('send_time_ms') is not None]
            nonmono=[i for i in range(1,len(sends)) if sends[i]<sends[i-1]]
            if nonmono:
                issues.append(Issue('ASF_PACKET_SEND_TIME_NONMONOTONIC','timeline','Los tiempos de envío de los paquetes de datos ASF disminuyen en el orden físico de los paquetes.',integrity='NONCONFORMANT',playability='POSSIBLY_AFFECTED',repairability='NONE',evidence=[{'positions':nonmono[:64]}]))
        else:
            facts['asf']['physical_complete_packet_count']=None
    audio_stream_numbers={x.get('stream_number') for x in audio if x.get('stream_number')}
    media_objects=_build_media_object_provenance(packets,audio_stream_numbers,data)
    facts['media_objects']=media_objects
    an=media_objects.get('anomalies') or {}
    if an.get('replicated_data_mismatch'):
        issues.append(Issue('ASF_MEDIA_OBJECT_REPLICATED_DATA_MISMATCH','media_object','Los fragmentos asignados al mismo objeto multimedia ASF discrepan en el tamaño replicado del objeto o en el tiempo de presentación.',integrity='NONCONFORMANT',playability='POSSIBLY_AFFECTED',repairability='NONE',evidence=[{'media_object_numbers':an['replicated_data_mismatch'][:64]}]))
    if an.get('fragment_gap'):
        details=[{'stream_number':x['stream_number'],'media_object_number':x['media_object_number'],'completion':x['completion'],'gaps':x['gaps'],'packet_indices':x['packet_indices']} for x in media_objects.get('media_objects',[]) if x['media_object_number'] in set(an['fragment_gap'])][:32]
        issues.append(Issue('ASF_MEDIA_OBJECT_FRAGMENT_GAP','media_object','Uno o más objetos multimedia ASF tienen un rango de bytes sin cubrir entre el offset 0 y su tamaño declarado; no se sintetizan los bytes ausentes.',integrity='DAMAGED',playability='POSSIBLY_AFFECTED',repairability='NONE',evidence=details))
    if an.get('fragment_overlap'):
        details=[{'stream_number':x['stream_number'],'media_object_number':x['media_object_number'],'overlaps':x['overlaps'],'packet_indices':x['packet_indices']} for x in media_objects.get('media_objects',[]) if x['media_object_number'] in set(an['fragment_overlap'])][:32]
        issues.append(Issue('ASF_MEDIA_OBJECT_FRAGMENT_OVERLAP','media_object','Los fragmentos asignados al mismo objeto multimedia ASF se superponen en el espacio de bytes declarado del objeto.',integrity='NONCONFORMANT',playability='POSSIBLY_AFFECTED',repairability='NONE',evidence=details))
    if an.get('fragment_bounds_invalid'):
        issues.append(Issue('ASF_MEDIA_OBJECT_FRAGMENT_BOUNDS_INVALID','media_object','Uno o más fragmentos de payload ASF se extienden fuera del tamaño replicado de su objeto multimedia.',integrity='DAMAGED',playability='POSSIBLY_AFFECTED',repairability='NONE',evidence=[{'media_object_numbers':an['fragment_bounds_invalid'][:64]}]))
    generic_incomplete=[x for x in (an.get('incomplete') or []) if x not in set(an.get('fragment_gap') or [])]
    if generic_incomplete:
        issues.append(Issue('ASF_MEDIA_OBJECT_INCOMPLETE','media_object','No pudo demostrarse que uno o más objetos multimedia ASF estén completos a partir de sus fragmentos de payload.',integrity='DAMAGED',playability='POSSIBLY_AFFECTED',repairability='NONE',evidence=[{'media_object_numbers':generic_incomplete[:64]}]))
    facts['packets']={'parsed_count':len(packets),'all_valid':all(p.get('valid') for p in packets) if packets else False,
                      'send_time_start_ms':next((p.get('send_time_ms') for p in packets if p.get('send_time_ms') is not None),None),
                      'send_time_end_ms':next((p.get('send_time_ms') for p in reversed(packets) if p.get('send_time_ms') is not None),None),
                      'duration_min_ms':min((p.get('duration_ms') for p in packets if p.get('duration_ms') is not None),default=None),
                      'duration_max_ms':max((p.get('duration_ms') for p in packets if p.get('duration_ms') is not None),default=None),
                      'payload_count_total':sum(len(p.get('payloads') or []) for p in packets),
                      'undeclared_stream_payload_count':sum(1 for p in packets for x in p.get('payloads') or [] if not x.get('stream_declared'))}
    return {'codec':'wma','facts':facts,'metadata':meta,'structural_map':struct,'issues':issues}
