from __future__ import annotations
from pathlib import Path
from app.models import Issue

BITRATES={(1,2):[0,32,48,56,64,80,96,112,128,160,192,224,256,320,384],(1,3):[0,32,40,48,56,64,80,96,112,128,160,192,224,256,320],(2,2):[0,8,16,24,32,40,48,56,64,80,96,112,128,144,160],(2,3):[0,8,16,24,32,40,48,56,64,80,96,112,128,144,160]}
RATES={1:[44100,48000,32000],2:[22050,24000,16000],25:[11025,12000,8000]}
XING_TOC_SIZE=100
XING_NUM_BAGS=400
XING_SIZE=156


def parse_header(b:bytes):
    if len(b)<4:return None
    x=int.from_bytes(b[:4],'big')
    if (x&0xffe00000)!=0xffe00000:return None
    vb=(x>>19)&3
    if vb==1:return None
    ver={3:1,2:2,0:25}[vb]; lb=(x>>17)&3
    if lb==0:return None
    layer={3:1,2:2,1:3}[lb]
    if layer not in (2,3):return None
    bi=(x>>12)&15; si=(x>>10)&3
    if bi==15 or si==3:return None
    sr=RATES[ver][si]; pad=(x>>9)&1; prot=((x>>16)&1)==0; mode=(x>>6)&3; mode_ext=(x>>4)&3; ch=1 if mode==3 else 2
    spf=1152 if layer==2 or ver==1 else 576
    if bi==0:return {'version':ver,'layer':layer,'bitrate_index':0,'bitrate_kbps':None,'sample_rate':sr,'padding':pad,'protected_by_crc':prot,'channel_mode':mode,'mode_extension':mode_ext,'channels':ch,'samples_per_frame':spf,'frame_length':None,'free_format':True}
    key=(1 if ver==1 else 2,layer); arr=BITRATES.get(key)
    if not arr:return None
    br=arr[bi]
    if not br:return None
    fl=(144*br*1000)//sr+pad if layer==2 else ((144 if ver==1 else 72)*br*1000)//sr+pad
    return {'version':ver,'layer':layer,'bitrate_index':bi,'bitrate_kbps':br,'sample_rate':sr,'padding':pad,'protected_by_crc':prot,'channel_mode':mode,'mode_extension':mode_ext,'channels':ch,'samples_per_frame':spf,'frame_length':fl,'free_format':False}


def _sig(h):return (h['version'],h['layer'],h['sample_rate'])


def _channel_mode_name(mode:int|None):
    return {0:'STEREO',1:'JOINT_STEREO',2:'DUAL_CHANNEL',3:'MONO'}.get(mode,'UNKNOWN')

def _hard_profile(h:dict):
    return {
        'mpeg_version':h.get('version'),'layer':h.get('layer'),'sample_rate':h.get('sample_rate'),
        'channels':h.get('channels'),'samples_per_frame':h.get('samples_per_frame'),
        'free_format':bool(h.get('free_format')),
    }

def _segment_profile(h:dict):
    q=_hard_profile(h)
    q.update({'channel_mode':h.get('channel_mode'),'channel_mode_name':_channel_mode_name(h.get('channel_mode')),
              'protected_by_crc':bool(h.get('protected_by_crc'))})
    return q

def _build_parameter_segments(frames:list[dict],gaps:list[dict]):
    """Construye tramos contiguos de parámetros MPEG sin tratar el bitrate como discontinuidad.

    Bitrate, padding y mode_extension son decisiones del codificador por frame. Los cambios
    duros son versión MPEG, Layer, frecuencia, canales, muestras por frame y modo free-format.
    Los cambios de modo de canal y protección CRC se conservan como segmentos blandos para
    mostrar decisiones normales de joint stereo sin convertirlas en hallazgos de daño.
    """
    if not frames:
        return {'schema':1,'segment_count':0,'segments':[],'transitions':[],'hard_profile_transition_count':0,'soft_transition_count':0}
    fields=('mpeg_version','layer','sample_rate','channels','samples_per_frame','free_format','channel_mode','protected_by_crc')
    hard={'mpeg_version','layer','sample_rate','channels','samples_per_frame','free_format'}
    segments=[];cur=None
    for f in frames:
        prof=_segment_profile(f['header']); key=tuple(prof.get(k) for k in fields)
        # Una resincronización inicia un segmento estructural aunque no cambien los parámetros.
        structural_id=int(f.get('reservoir_segment_id',0))
        if cur and cur['_key']==key and cur['structural_segment_id']==structural_id and cur['byte_end']==f['byte_start']:
            cur['byte_end']=f['byte_end'];cur['frame_end_index']=f['index'];cur['frame_count']+=1
            if not f.get('is_vbr_header'):cur['audio_frame_count']+=1
            br=f['header'].get('bitrate_kbps')
            if br is not None:cur['_bitrates'].add(br)
        else:
            cur={'index':len(segments),'structural_segment_id':structural_id,'byte_start':f['byte_start'],'byte_end':f['byte_end'],
                 'frame_start_index':f['index'],'frame_end_index':f['index'],'frame_count':1,'audio_frame_count':0 if f.get('is_vbr_header') else 1,
                 'profile':prof,'_key':key,'_bitrates':set()}
            br=f['header'].get('bitrate_kbps')
            if br is not None:cur['_bitrates'].add(br)
            segments.append(cur)
    for seg in segments:
        br=sorted(seg.pop('_bitrates'))
        seg.pop('_key',None);seg['bitrate_kbps_values']=br;seg['bitrate_mode']='CBR' if len(br)==1 else ('VBR_OR_MIXED' if len(br)>1 else 'FREE_FORMAT')
    transitions=[]
    for a,b in zip(segments,segments[1:]):
        changed=[k for k in fields if a['profile'].get(k)!=b['profile'].get(k)]
        hard_changed=[k for k in changed if k in hard]
        contiguous=a['byte_end']==b['byte_start']
        gap=next((g for g in gaps if g['byte_start']>=a['byte_end'] and g['byte_end']<=b['byte_start']),None)
        if hard_changed:
            interpretation='COHERENT_CONCATENATION' if contiguous and gap is None else 'PARAMETER_CHANGE_AFTER_RESYNC'
        elif gap is not None:
            interpretation='RESYNC_CONTINUATION_SAME_PARAMETERS'
        else:
            interpretation='ENCODING_MODE_VARIATION'
        transitions.append({'from_segment':a['index'],'to_segment':b['index'],'byte_offset':b['byte_start'],
                            'changed_fields':changed,'hard_changed_fields':hard_changed,'contiguous':contiguous,'gap_index':gap.get('index') if gap else None,
                            'interpretation':interpretation})
    hard_trans=[t for t in transitions if t['hard_changed_fields']]
    soft=[t for t in transitions if t['changed_fields'] and not t['hard_changed_fields']]
    return {'schema':1,'segmentation_basis':'MPEG_HEADER_STREAM_AND_ENCODING_PARAMETERS_EXCLUDING_BITRATE_PADDING_MODE_EXTENSION',
            'segment_count':len(segments),'segments':segments,'transitions':transitions,
            'hard_profile_transition_count':len(hard_trans),'soft_transition_count':len(soft),
            'coherent_concatenation_transition_count':sum(t['interpretation']=='COHERENT_CONCATENATION' for t in transitions),
            'parameter_change_after_resync_count':sum(t['interpretation']=='PARAMETER_CHANGE_AFTER_RESYNC' for t in transitions)}


def _compatibility_profile(frames:list[dict],xing:dict|None,vbri:dict|None,metadata:dict):
    """Resume rasgos MPEG y del contenedor observados sin conjeturar el codificador."""
    hs=[f.get('header') or {} for f in frames if not f.get('is_vbr_header')]
    if not hs: hs=[f.get('header') or {} for f in frames]
    versions=sorted({h.get('version') for h in hs if h.get('version') is not None})
    layers=sorted({h.get('layer') for h in hs if h.get('layer') is not None})
    rates=sorted({h.get('sample_rate') for h in hs if h.get('sample_rate') is not None})
    channels=sorted({h.get('channels') for h in hs if h.get('channels') is not None})
    modes=sorted({_channel_mode_name(h.get('channel_mode')) for h in hs})
    bitrates=sorted({h.get('bitrate_kbps') for h in hs if h.get('bitrate_kbps') is not None})
    protected=sum(bool(h.get('protected_by_crc')) for h in hs)
    if not hs: crc_mode='NONE'
    elif protected==0: crc_mode='NONE'
    elif protected==len(hs): crc_mode='ALL'
    else: crc_mode='MIXED'
    seek='NONE';declared_encoder=None;attribution='UNATTRIBUTED'
    if xing:
        seek=xing.get('kind') or 'XING_INFO';declared_encoder=(xing.get('encoder') or None)
        if declared_encoder: attribution='DECLARED_IN_XING_TAG'
    elif vbri: seek='VBRI'
    flags=[]
    if 25 in versions: flags.append('MPEG_2_5')
    if 2 in versions: flags.append('MPEG_2_LSF')
    if any(bool(h.get('free_format')) for h in hs): flags.append('FREE_FORMAT')
    if 1 in channels: flags.append('MONO_PRESENT')
    if crc_mode!='NONE': flags.append('CRC_PROTECTED')
    if seek=='NONE': flags.append('NO_DEDICATED_SEEK_HEADER')
    elif seek=='Info': flags.append('INFO_HEADER')
    elif seek=='Xing': flags.append('XING_HEADER')
    elif seek=='VBRI': flags.append('VBRI_HEADER')
    if metadata.get('id3v1'): flags.append('ID3V1_PRESENT')
    id3v2=metadata.get('id3v2') or {}
    if id3v2.get('present'): flags.append(f"ID3V2_{id3v2.get('version_major')}")
    if len(bitrates)>1: flags.append('MULTIPLE_BITRATES')
    return {'schema':1,'evidence_policy':'OBSERVED_FIELDS_ONLY_NO_ENCODER_GUESSING',
            'mpeg_versions':versions,'layers':layers,'sample_rates_hz':rates,'channels':channels,
            'channel_modes':modes,'bitrate_kbps_values':bitrates,'crc_protection':crc_mode,
            'dedicated_seek_header':seek,'declared_encoder':declared_encoder,'encoder_attribution':attribution,
            'id3v2_major':id3v2.get('version_major') if id3v2.get('present') else None,
            'id3v1_present':bool(metadata.get('id3v1')),'variant_flags':flags}

def _synchsafe(b):
    if len(b)!=4 or any(x&0x80 for x in b):return None
    return (b[0]<<21)|(b[1]<<14)|(b[2]<<7)|b[3]

def _id3(data:bytes):
    if len(data)<10 or data[:3]!=b'ID3':return None
    sz=_synchsafe(data[6:10]); footer=10 if data[5]&0x10 else 0
    total=(10+sz+footer) if sz is not None else 10
    malformed=sz is None or total>len(data)
    return {'present':True,'version_major':data[3],'revision':data[4],'flags':data[5],
            'payload_size_declared':sz,'size_declared':total,'malformed':malformed,
            'search_start':10 if malformed else total}

def _sideinfo_len(h:dict):
    if h['layer']!=3:return 0
    if h['version']==1:return 17 if h['channels']==1 else 32
    return 9 if h['channels']==1 else 17

def _main_data_capacity(h:dict):
    if h['layer']!=3:return 0
    return max(0,h['frame_length']-4-(2 if h['protected_by_crc'] else 0)-_sideinfo_len(h))

def _mdb(data:bytes,off:int,h:dict):
    if h['layer']!=3:return 0
    si=off+4+(2 if h['protected_by_crc'] else 0)
    if h['version']==1:
        if si+2>len(data):return None
        return ((data[si]<<1)|(data[si+1]>>7))&0x1ff
    if si>=len(data):return None
    return data[si]

def _layer3_sideinfo_usage(frame:bytes,h:dict):
    """Analiza el puntero del reservorio Layer III y agrega la demanda part2_3_length.

    La información lateral tiene tamaño fijo para cada versión MPEG y cantidad de canales.
    El mapa sólo usa campos con autoridad para ubicar main-data: main_data_begin y cada
    part2_3_length por gránulo y canal. Los demás campos se omiten con su ancho fijo.
    """
    if h.get('layer')!=3:return None
    side_len=_sideinfo_len(h);base=4+(2 if h.get('protected_by_crc') else 0)
    if side_len<=0 or len(frame)<base+side_len:return {'parsed':False,'reason':'información lateral Layer III incompleta'}
    side=frame[base:base+side_len];bitpos=0
    def take(n):
        nonlocal bitpos
        v,bitpos2=_read_bits(side,bitpos,n)
        if v is None:return None
        bitpos=bitpos2;return v
    if h.get('version')==1:
        mdb=take(9); private=take(5 if h.get('channels')==1 else 3)
        if mdb is None or private is None:return {'parsed':False,'reason':'truncated MPEG-1 side-info prefix'}
        # scfsi: cuatro bits por canal, seguidos por dos gránulos por canal; cada
        # estructura granule-info MPEG-1 ocupa 59 bits y comienza con el campo
        # part2_3_length de 12 bits.
        if take(4*h.get('channels',2)) is None:return {'parsed':False,'reason':'truncated MPEG-1 scfsi'}
        lengths=[]
        for _gr in range(2):
            for _ch in range(h.get('channels',2)):
                q=take(12)
                if q is None:return {'parsed':False,'reason':'truncated MPEG-1 part2_3_length'}
                lengths.append(q)
                if take(47) is None:return {'parsed':False,'reason':'truncated MPEG-1 granule info'}
    else:
        mdb=take(8); private=take(1 if h.get('channels')==1 else 2)
        if mdb is None or private is None:return {'parsed':False,'reason':'truncated MPEG-2/2.5 side-info prefix'}
        # MPEG-2/2.5 usa un gránulo; granule-info ocupa 63 bits por canal y también
        # comienza con part2_3_length de 12 bits.
        lengths=[]
        for _ch in range(h.get('channels',2)):
            q=take(12)
            if q is None:return {'parsed':False,'reason':'truncated MPEG-2/2.5 part2_3_length'}
            lengths.append(q)
            if take(51) is None:return {'parsed':False,'reason':'truncated MPEG-2/2.5 granule info'}
    return {'parsed':True,'main_data_begin':mdb,'part2_3_length_bits':lengths,
            'main_data_bits_required':sum(lengths),'side_info_bytes':side_len}

def _build_layer3_reservoir_map(frames:list[dict]):
    """Construye evidencia física de dependencias de bytes y bits para frames Layer III.

    main_data_begin apunta hacia atrás dentro de los slots main-data concatenados, no dentro
    del archivo completo. Cada brecha de resincronización inicia un segmento demostrable:
    las referencias anteriores se informan como no disponibles, sin conjeturas. El mapa
    nunca intenta reparar el payload.
    """
    seg_cursor={}
    seg_regions={}
    mapped=[]; underflow=[]; overrun=[]
    for f in frames:
        if f.get('header',{}).get('layer')!=3:continue
        seg=int(f.get('reservoir_segment_id',0));cur=seg_cursor.get(seg,0)
        cap=int(f.get('main_data_capacity_bytes') or 0)
        reg={'frame_index':f['index'],'start_bit':cur*8,'end_bit':(cur+cap)*8}
        seg_regions.setdefault(seg,[]).append(reg)
        use=f.get('layer3_sideinfo_usage') or {}
        mdb=use.get('main_data_begin') if use.get('parsed') else f.get('main_data_begin')
        bits=use.get('main_data_bits_required') if use.get('parsed') else None
        dep={'segment_id':seg,'main_data_slot_offset_bytes':cur,'main_data_capacity_bytes':cap,
             'main_data_begin_bytes':mdb,'main_data_bits_required':bits,'dependency_source_frame_indices':[],
             'dependency_backspan_frames':0,'unavailable_pre_segment_bytes':0,'overruns_current_frame':False,'provable':False}
        if use.get('parsed') and mdb is not None and bits is not None:
            start_bit=(cur-int(mdb))*8; end_bit=start_bit+int(bits); dep['main_data_start_bit']=start_bit;dep['main_data_end_bit']=end_bit
            if start_bit<0:
                dep['unavailable_pre_segment_bytes']=(-start_bit+7)//8;underflow.append(f['index'])
            if end_bit>(cur+cap)*8:
                dep['overruns_current_frame']=True;overrun.append(f['index'])
            # Recorta sólo para enumerar evidencia; provable sigue falso si cualquier
            # bit requerido queda fuera del segmento o frame disponible.
            lo=max(0,start_bit);hi=min((cur+cap)*8,end_bit)
            src=[]
            if hi>lo:
                for r in seg_regions[seg]:
                    if r['end_bit']>lo and r['start_bit']<hi:src.append(r['frame_index'])
            dep['dependency_source_frame_indices']=src
            prior=[x for x in src if x!=f['index']]
            if prior:dep['dependency_backspan_frames']=f['index']-min(prior)
            dep['provable']=start_bit>=0 and end_bit<=(cur+cap)*8
        f['reservoir_dependency']=dep
        # Conserva la jerarquía de recuperación al derivar limpieza o contaminación
        # desde el mapa enriquecido. Los frames de cabecera son auxiliares y no generan PCM.
        if not f.get('is_vbr_header') and use.get('parsed'):
            if dep['unavailable_pre_segment_bytes']>0:
                f['clean']=False;f['taint_reason']='bit_reservoir_dependency_before_resync_segment'
            elif dep['overruns_current_frame']:
                f['clean']=False;f['taint_reason']='bit_reservoir_main_data_overrun'
            elif f.get('taint_reason')=='bit_reservoir_dependency_after_gap':
                f['clean']=True;f['taint_reason']=None
        mapped.append(f)
        seg_cursor[seg]=cur+cap
    # Las aristas inversas permiten medir el impacto de pérdida o corrupción de un frame
    # físico: enumeran frames posteriores cuyos datos principales lo alcanzan.
    byidx={f['index']:f for f in mapped}
    for f in mapped:f['reservoir_dependents']=[]
    for f in mapped:
        for src in (f.get('reservoir_dependency') or {}).get('dependency_source_frame_indices',[]):
            if src!=f['index'] and src in byidx:byidx[src]['reservoir_dependents'].append(f['index'])
    audio=[f for f in mapped if not f.get('is_vbr_header')]
    refs=[f for f in audio if (f.get('reservoir_dependency') or {}).get('main_data_begin_bytes',0)>0]
    return {'supported_scope':'MPEG_LAYER_III','mapping':'MAIN_DATA_BEGIN_PLUS_PART2_3_LENGTH',
            'layer3_frame_count':len(audio),'mapped_frame_count':sum(1 for f in audio if (f.get('layer3_sideinfo_usage') or {}).get('parsed')),
            'frames_with_backreferences':len(refs),'max_main_data_begin_bytes':max([(f.get('reservoir_dependency') or {}).get('main_data_begin_bytes') or 0 for f in audio],default=0),
            'max_dependency_backspan_frames':max([(f.get('reservoir_dependency') or {}).get('dependency_backspan_frames') or 0 for f in audio],default=0),
            'max_dependent_frame_count':max([len(f.get('reservoir_dependents') or []) for f in audio],default=0),
            'unresolved_pre_segment_frame_indices':underflow,'main_data_overrun_frame_indices':overrun,
            'fully_provable_frame_count':sum(1 for f in audio if (f.get('reservoir_dependency') or {}).get('provable'))}

def _free_coeff(h:dict): return 144 if h['layer']==2 or h['version']==1 else 72

def _materialize_free(h:dict,base:int):
    q=dict(h);q['frame_length']=base+h['padding'];q['_free_base_length']=base
    q['bitrate_kbps']=round(base*h['sample_rate']/_free_coeff(h)/1000.0,3);return q

def _infer_free_base(data:bytes,off:int,end:int,h:dict,need=3):
    lim=min(end-4,off+8192)
    for nxt in range(off+24,lim+1):
        h2=parse_header(data[nxt:nxt+4])
        if not h2 or not h2['free_format'] or _sig(h2)!=_sig(h):continue
        base=(nxt-off)-h['padding']
        if base<=4:continue
        br=base*h['sample_rate']/_free_coeff(h)/1000.0
        if br<8 or br>640:continue
        pos=off;ok=True
        for _ in range(need):
            hh=parse_header(data[pos:pos+4]) if pos+4<=end else None
            if not hh or not hh['free_format'] or _sig(hh)!=_sig(h):ok=False;break
            fl=base+hh['padding']
            if pos+fl>end:ok=False;break
            pos+=fl
        if ok:return base
    return None

def _coherent_at(data:bytes,off:int,end:int,need=3,expected_sig=None):
    pos=off; first=None;free_base=None
    for _ in range(need):
        h=parse_header(data[pos:pos+4]) if pos+4<=end else None
        if not h:return None
        if expected_sig and _sig(h)!=expected_sig:return None
        if first is None:
            if h['free_format']:
                free_base=_infer_free_base(data,off,end,h,need)
                if free_base is None:return None
                h=_materialize_free(h,free_base)
            first=h
        else:
            if _sig(h)!=_sig(first) or h['free_format']!=first['free_format']:return None
            if h['free_format']:h=_materialize_free(h,free_base)
        if h['frame_length'] is None or pos+h['frame_length']>end:return None
        pos+=h['frame_length']
    return first

def _find_chain(data:bytes,start:int,end:int,max_scan:int,expected_sig=None):
    lim=min(end-4,start+max_scan)
    for off in range(max(0,start),max(0,lim+1)):
        h=_coherent_at(data,off,end,3,expected_sig)
        if h:return off,h
    return None

def mpeg_audio_crc16(data:bytes,init=0xffff):
    """Registro de desplazamiento CRC-16 de audio MPEG (polinomio 0x8005, MSB primero)."""
    crc=init&0xffff
    for byte in data:
        crc^=byte<<8
        for _ in range(8):
            crc=((crc<<1)^0x8005)&0xffff if crc&0x8000 else (crc<<1)&0xffff
    return crc

# Geometría de asignación Layer II para delimitar la región protegida por CRC de
# asignación de bits y SCFSI. Son selectores ISO Layer II expresados como cantidad
# de bits de asignación por subbanda; las tablas de cuantización no se duplican aquí.
_L2_NBAL_BY_OFFSET={0:2,1:2,2:3,3:3,4:4,5:4,6:4,7:4}
_L2_SBQUANT=[
    (27,[7,7,7,6,6,6,6,6,6,6,6,3,3,3,3,3,3,3,3,3,3,3,3,0,0,0,0]),
    (30,[7,7,7,6,6,6,6,6,6,6,6,3,3,3,3,3,3,3,3,3,3,3,3,0,0,0,0,0,0,0]),
    (8,[5,5,2,2,2,2,2,2]),
    (12,[5,5,2,2,2,2,2,2,2,2,2,2]),
    (30,[4,4,4,4,2,2,2,2,2,2,2,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1]),
]

def _crc16_update_value(crc:int,value:int,nbits:int):
    """Incorpora al CRC-16 MPEG los *nbits* de value desde el más significativo."""
    for shift in range(nbits-1,-1,-1):
        bit=(value>>shift)&1
        top=(crc>>15)&1
        crc=(crc<<1)&0xffff
        if top^bit:crc^=0x8005
    return crc&0xffff

def _read_bits(frame:bytes,bitpos:int,nbits:int):
    if nbits<0 or bitpos<0 or bitpos+nbits>len(frame)*8:return None,bitpos
    v=0
    for i in range(nbits):
        q=bitpos+i;v=(v<<1)|((frame[q>>3]>>(7-(q&7)))&1)
    return v,bitpos+nbits

def _layer2_table_geometry(h:dict):
    if h.get('layer')!=2:return None
    nch=h.get('channels',2)
    if h.get('version')!=1:
        idx=4
    elif h.get('free_format'):
        idx=0 if h.get('sample_rate')==48000 else 1
    else:
        br=(h.get('bitrate_kbps') or 0)*1000
        per=br//nch if nch else br
        if per<=48000:idx=3 if h.get('sample_rate')==32000 else 2
        elif per<=80000:idx=0
        else:idx=0 if h.get('sample_rate')==48000 else 1
    sblimit,offsets=_L2_SBQUANT[idx]
    bound=sblimit
    if h.get('channel_mode')==1:bound=min(sblimit,4+int(h.get('mode_extension',0))*4)
    return {'table_index':idx,'sblimit':sblimit,'offsets':offsets,'bound':bound,'channels':nch}

def _layer2_crc_status(frame:bytes,h:dict):
    """Devuelve el CRC almacenado y calculado para frames MPEG Layer II protegidos.

    MPEG Layer II protege los 16 bits bajos de cabecera, los campos completos de asignación
    de bits y los SCFSI de subbandas asignadas. La palabra CRC y los factores de escala o
    datos de muestras posteriores quedan fuera de esta puerta.
    """
    if h.get('layer')!=2 or not h.get('protected_by_crc'):return None
    if len(frame)<6:return {'checked':False,'reason':'frame demasiado corto para la palabra CRC de Layer II'}
    geom=_layer2_table_geometry(h)
    if not geom:return {'checked':False,'reason':'no pudo seleccionarse la tabla de asignación de Layer II'}
    crc=mpeg_audio_crc16(frame[2:4]); bitpos=6*8; start=bitpos
    alloc=[[0]*geom['sblimit'] for _ in range(max(2,geom['channels']))]
    # Asignación de bits.
    for sb in range(geom['bound']):
        nbal=_L2_NBAL_BY_OFFSET[geom['offsets'][sb]]
        for ch in range(geom['channels']):
            v,bitpos=_read_bits(frame,bitpos,nbal)
            if v is None:return {'checked':False,'reason':'el frame termina dentro de la región de asignación de bits de Layer II'}
            alloc[ch][sb]=v;crc=_crc16_update_value(crc,v,nbal)
    for sb in range(geom['bound'],geom['sblimit']):
        nbal=_L2_NBAL_BY_OFFSET[geom['offsets'][sb]]
        v,bitpos=_read_bits(frame,bitpos,nbal)
        if v is None:return {'checked':False,'reason':'el frame termina dentro de la región de asignación de bits joint-stereo de Layer II'}
        alloc[0][sb]=v
        if geom['channels']>1:alloc[1][sb]=v
        crc=_crc16_update_value(crc,v,nbal)
    # La selección de factores de escala está protegida por CRC; los factores no.
    for sb in range(geom['sblimit']):
        for ch in range(geom['channels']):
            if alloc[ch][sb]:
                v,bitpos=_read_bits(frame,bitpos,2)
                if v is None:return {'checked':False,'reason':'el frame termina dentro de la región SCFSI de Layer II'}
                crc=_crc16_update_value(crc,v,2)
    stored=int.from_bytes(frame[4:6],'big')
    return {'checked':True,'stored':stored,'computed':crc,'valid':stored==crc,
            'protected_bits':16+(bitpos-start),'protected_structure_bits':bitpos-start,
            'sblimit':geom['sblimit'],'joint_stereo_bound':geom['bound'],'allocation_table_index':geom['table_index'],
            'scope':'HEADER_LOW16_PLUS_LAYER2_BITALLOC_SCFI','polynomial':'0x8005','initial_state':'0xffff'}

def _mpeg_crc_status(frame:bytes,h:dict):
    if h.get('layer')==2:return _layer2_crc_status(frame,h)
    if h.get('layer')==3:return _layer3_crc_status(frame,h)
    return None

def _layer3_crc_status(frame:bytes,h:dict):
    """Devuelve el CRC almacenado y calculado para frames MPEG Layer III protegidos.

    Se protegen los 16 bits bajos de cabecera y toda la información lateral de Layer III.
    La propia palabra CRC de dos bytes se omite.
    """
    if h.get('layer')!=3 or not h.get('protected_by_crc'):
        return None
    side_len=_sideinfo_len(h)
    if side_len<=0 or len(frame)<6+side_len:
        return {'checked':False,'reason':'frame demasiado corto para la palabra CRC y la información lateral completa de Layer III'}
    stored=int.from_bytes(frame[4:6],'big')
    computed=mpeg_audio_crc16(frame[2:4]+frame[6:6+side_len])
    return {'checked':True,'stored':stored,'computed':computed,'valid':stored==computed,
            'protected_bits':16+side_len*8,'side_info_bytes':side_len,
            'scope':'HEADER_LOW16_PLUS_LAYER3_SIDEINFO','polynomial':'0x8005','initial_state':'0xffff'}

def crc16_ansi_le(data:bytes,init=0):
    crc=init&0xffff
    for byte in data:
        crc^=byte
        for _ in range(8): crc=(crc>>1)^(0xA001 if crc&1 else 0)
    return crc&0xffff

def _parse_xing(frame:bytes,h:dict):
    if h['layer']!=3:return None
    o=4+(2 if h['protected_by_crc'] else 0)+_sideinfo_len(h)
    if len(frame)<o+8 or frame[o:o+4] not in (b'Xing',b'Info'):return None
    kind=frame[o:o+4].decode(); flags=int.from_bytes(frame[o+4:o+8],'big'); p=o+8
    x={'kind':kind,'flags':flags,'frames':None,'bytes':None,'toc_present':False,'encoder':None,
       'encoder_delay_samples':None,'end_padding_samples':None,'gapless_fields_trusted':False,
       'xing_offset_in_frame':o,'xing_size':XING_SIZE}
    if flags&1 and p+4<=len(frame):x['frames']=int.from_bytes(frame[p:p+4],'big');p+=4
    if flags&2 and p+4<=len(frame):x['bytes']=int.from_bytes(frame[p:p+4],'big');p+=4
    if flags&4 and p+100<=len(frame):x['toc_present']=True;x['toc']=list(frame[p:p+100]);p+=100
    if flags&8 and p+4<=len(frame):x['quality']=int.from_bytes(frame[p:p+4],'big');p+=4
    if p+9<=len(frame):
        rawenc=frame[p:p+9];enc=rawenc.split(b'\0',1)[0].decode('latin1','replace').strip();x['encoder']=enc;q=p+9
        dp=q+12
        if dp+3<=len(frame) and enc[:4] in ('LAME','Lavf','Lavc'):
            v=int.from_bytes(frame[dp:dp+3],'big');x['encoder_delay_samples']=v>>12;x['end_padding_samples']=v&4095;x['gapless_fields_trusted']=True
    # El muxer de FFmpeg escribe un bloque Xing/LAME de 156 bytes. La actualización
    # acoplada automática se limita a ese perfil; otros codificadores sólo se informan.
    if flags==0x0f and o+XING_SIZE<=len(frame) and (x.get('encoder') or '').startswith(('Lavc','Lavf')):
        x['ffmpeg_extended_profile']=True
        x['music_length']=int.from_bytes(frame[o+148:o+152],'big')
        x['music_crc']=int.from_bytes(frame[o+152:o+154],'big')
        x['tag_crc']=int.from_bytes(frame[o+154:o+156],'big')
    else:x['ffmpeg_extended_profile']=False
    return x

def _parse_vbri(frame:bytes,h:dict):
    # VBRI v1 se ubica 32 bytes después de la cabecera MPEG de cuatro bytes. A diferencia
    # de Xing, la posición es fija y no depende del modo de canal ni de la información
    # lateral. Se conservan valores crudos y tamaños escalados para demostrar cobertura exacta.
    if h['layer']!=3:return None
    o=4+32
    if len(frame)<o+26 or frame[o:o+4]!=b'VBRI':return None
    x={'version':int.from_bytes(frame[o+4:o+6],'big'),
       'delay':int.from_bytes(frame[o+6:o+8],'big'),
       'quality':int.from_bytes(frame[o+8:o+10],'big'),
       'bytes':int.from_bytes(frame[o+10:o+14],'big'),
       'frames':int.from_bytes(frame[o+14:o+18],'big'),
       'toc_entries':int.from_bytes(frame[o+18:o+20],'big'),
       'toc_scale':int.from_bytes(frame[o+20:o+22],'big'),
       'toc_entry_size':int.from_bytes(frame[o+22:o+24],'big'),
       'toc_frames_per_entry':int.from_bytes(frame[o+24:o+26],'big'),
       'vbri_offset_in_frame':o,'header_min_size':26}
    entries=x['toc_entries'];esz=x['toc_entry_size'];scale=x['toc_scale'];fpe=x['toc_frames_per_entry']
    x['layout_valid']=bool(x['version']==1 and entries>0 and scale>0 and esz in (1,2,3,4) and fpe>0 and o+26+entries*esz<=len(frame))
    x['toc']=[];x['toc_segment_bytes']=[];x['table_byte_length']=entries*esz if entries>=0 and esz>=0 else None
    if x['layout_valid']:
        p=o+26
        for _ in range(entries):
            v=int.from_bytes(frame[p:p+esz],'big');p+=esz
            x['toc'].append(v);x['toc_segment_bytes'].append(v*scale)
        x['table_end_offset_in_frame']=p
        x['toc_total_bytes']=sum(x['toc_segment_bytes'])
    return x

def _terminal_start(data:bytes,end:int):
    cur=end; md={}
    if cur>=128 and data[cur-128:cur-125]==b'TAG':md['id3v1']={'present':True};cur-=128
    if cur>=32 and data[cur-32:cur-24]==b'APETAGEX':
        footer=cur-32;ts=int.from_bytes(data[footer+12:footer+16],'little');start=cur-ts
        if start>=32 and data[start-32:start-24]==b'APETAGEX':start-=32
        if 0<=start<cur:md['apev2']={'present':True,'complete_size':cur-start};cur=start
    return cur,md

def _ffmpeg_xing_toc(frames:list[dict]):
    if not frames or not frames[0].get('is_vbr_header'):return None
    audio=[f for f in frames if not f.get('is_vbr_header')]
    if not audio:return None
    bag=[0]*XING_NUM_BAGS; want=1;seen=0;pos=0;size=frames[0]['frame_length']
    for f in audio:
        seen+=1;size+=f['frame_length']
        if want==seen:
            bag[pos]=size;pos+=1
            if pos==XING_NUM_BAGS:
                for i in range(1,XING_NUM_BAGS,2):bag[i>>1]=bag[i]
                want*=2;pos=XING_NUM_BAGS//2
            seen=0
    if pos<=0:return None
    toc=[0]*XING_TOC_SIZE
    for i in range(1,XING_TOC_SIZE):
        j=i*pos//XING_TOC_SIZE; seek=256*bag[j]//size;toc[i]=min(seek,255)
    return toc

def _expected_xing(data:bytes,frames:list[dict],first_off:int,scan_end:int,xing:dict):
    if not xing or not frames:return None
    audio=[f for f in frames if not f['is_vbr_header']]
    bitrates={f['header'].get('bitrate_kbps') for f in audio}
    expected_kind='Info' if len(bitrates)==1 and None not in bitrates else 'Xing'
    exp={'kind':expected_kind,'frames':len(audio),'bytes':scan_end-first_off,'toc':_ffmpeg_xing_toc(frames)}
    if xing.get('ffmpeg_extended_profile'):
        audio_bytes=b''.join(data[f['byte_start']:f['byte_end']] for f in audio)
        exp['music_length']=scan_end-first_off; exp['music_crc']=crc16_ansi_le(audio_bytes)
        # Construye el primer frame actualizando todos los campos acoplados y calcula
        # el CRC de la etiqueta como FFmpeg. Pone a cero su ubicación si cae dentro
        # de los primeros 190 bytes, como en disposiciones mono o MPEG-2.
        f0=frames[0]; frame=bytearray(data[f0['byte_start']:f0['byte_end']]); o=xing['xing_offset_in_frame']
        frame[o:o+4]=expected_kind.encode('ascii');frame[o+8:o+12]=len(audio).to_bytes(4,'big');frame[o+12:o+16]=(scan_end-first_off).to_bytes(4,'big')
        if exp['toc'] is not None:frame[o+16:o+116]=bytes(exp['toc'])
        frame[o+148:o+152]=(scan_end-first_off).to_bytes(4,'big');frame[o+152:o+154]=exp['music_crc'].to_bytes(2,'big')
        if o+154<190:frame[o+154:o+156]=b'\0\0'
        exp['tag_crc']=crc16_ansi_le(bytes(frame[:190]))
    return exp

def _expected_vbri(frames:list[dict],first_off:int,scan_end:int,vbri:dict):
    if not vbri or not vbri.get('layout_valid') or not frames or not frames[0].get('is_vbr_header'):return None
    audio=[f for f in frames if not f.get('is_vbr_header')]
    entries=vbri.get('toc_entries');scale=vbri.get('toc_scale');esz=vbri.get('toc_entry_size');fpe=vbri.get('toc_frames_per_entry')
    if not audio or not all(isinstance(v,int) and v>0 for v in (entries,scale,esz,fpe)):return None
    # La tabla debe cubrir exactamente los grupos observados. El primer segmento comienza
    # en el frame VBRI e incluye ese auxiliar y el primer grupo de audio; los siguientes
    # comienzan en el límite previo. Es el modelo de posición usado por Android/ExoPlayer.
    expected_entries=(len(audio)+fpe-1)//fpe
    if expected_entries!=entries:return {'layout_representable':False,'reason':'la cantidad de entradas de la TOC y los frames por entrada no cubren exactamente la cantidad observada de frames de audio','frames':len(audio),'bytes':scan_end-first_off}
    vals=[];segments=[];seg_start=first_off;maxv=(1<<(8*esz))-1
    for i in range(entries):
        group=audio[i*fpe:min((i+1)*fpe,len(audio))]
        if not group:return {'layout_representable':False,'reason':'empty VBRI TOC group','frames':len(audio),'bytes':scan_end-first_off}
        seg_end=group[-1]['byte_end'];seg=seg_end-seg_start
        if seg<=0 or seg%scale:return {'layout_representable':False,'reason':'la longitud observada del segmento en bytes no puede representarse exactamente con la escala VBRI declarada','frames':len(audio),'bytes':scan_end-first_off}
        v=seg//scale
        if v>maxv:return {'layout_representable':False,'reason':'el segmento observado no cabe en el ancho de entrada VBRI declarado','frames':len(audio),'bytes':scan_end-first_off}
        vals.append(v);segments.append(seg);seg_start=seg_end
    if seg_start!=scan_end:return {'layout_representable':False,'reason':'la disposición de la tabla VBRI no alcanza el final exacto del stream MPEG coherente','frames':len(audio),'bytes':scan_end-first_off}
    return {'layout_representable':True,'frames':len(audio),'bytes':scan_end-first_off,'toc':vals,'toc_segment_bytes':segments,'toc_total_bytes':sum(segments)}


def analyze(path:Path,max_scan=262144):
    data=path.read_bytes(); n=len(data); issues=[]; md={}; id3=_id3(data); start=0
    if id3:
        md['id3v2']=id3; start=id3['search_start']
        if id3['malformed']:issues.append(Issue('ID3V2_MALFORMED','metadata','ID3v2 declara un tamaño mayor que el archivo disponible; se usó el recorrido de recuperación MPEG crudo.',integrity='DAMAGED',compatibility='LIKELY',playability='BLOCKING',byte_start=0,byte_end=min(10,n),repairability='SAFE_IF_VERIFIED'))
    terminal,tailmd=_terminal_start(data,n);md.update(tailmd)
    first=_find_chain(data,start,terminal,max_scan)
    if not first:
        return {'codec':None,'facts':{'identified':False},'metadata':md,'issues':issues+[Issue('MPEG_SYNC_NOT_FOUND','framing','No se encontró una cadena MPEG Layer II/III coherente',integrity='DAMAGED',playability='BLOCKING')],'structural_map':[],'frames':[],'gaps':[],'data':data}
    first_off,h0=first; sig=_sig(h0); current_free_format=bool(h0.get('free_format')); free_base=h0.get('_free_base_length'); frames=[];gaps=[];pos=first_off;after_gap=False;reservoir_bytes_since_gap=0;logical_audio_idx=0;gap_seq=0;last_frame_idx=None;reservoir_segment_id=0;unexpected_stream_headers=[]
    if first_off>start and not (id3 and id3.get('malformed')):
        issues.append(Issue('MPEG_SYNC_LOSS','framing','Hay una región inicial no explicada antes del ancla MPEG coherente.',integrity='DAMAGED',compatibility='LIKELY',playability='DEGRADED',byte_start=start,byte_end=first_off,repairability='RECOVERY_ONLY'))
    xing=None; vbri=None; first_is_vbr=False; audio_frames_observed=0
    while pos+4<=terminal:
        h=parse_header(data[pos:pos+4])
        if h and h['free_format'] and free_base is not None and _sig(h)==sig:h=_materialize_free(h,free_base)
        valid_current=bool(h and h['frame_length'] is not None and _sig(h)==sig and bool(h['free_format'])==current_free_format)
        if not valid_current:
            # Una secuencia de cabeceras distinta pero coherente que comienza en este byte
            # es un límite limpio de parámetros, no una pérdida de sincronización.
            at=_coherent_at(data,pos,terminal,3)
            if at and (_sig(at)!=sig or bool(at.get('free_format'))!=current_free_format):
                h=at;sig=_sig(h);current_free_format=bool(h.get('free_format'));free_base=h.get('_free_base_length')
                reservoir_segment_id+=1;after_gap=False;reservoir_bytes_since_gap=0;logical_audio_idx=None
            else:
                old_sig=sig;old_free=current_free_format
                # Tras bytes dañados se acepta cualquier cadena MPEG II/III coherente.
                # Limitar la resincronización a la firma anterior ocultaría cambios reales
                # y clasificaría incorrectamente el resto como basura final.
                nxt=_find_chain(data,pos+1,terminal,max_scan)
                if not nxt:break
                no,nh=nxt
                new_sig=_sig(nh);new_free=bool(nh.get('free_format'))
                if nh.get('_free_base_length') is not None:free_base=nh['_free_base_length']
                gaplen=no-pos; expected=(frames[-1]['frame_length'] if frames else nh['frame_length']); missing=None
                if new_sig==old_sig and new_free==old_free and expected and gaplen>0 and gaplen%expected==0 and gaplen//expected<=128:missing=gaplen//expected
                if last_frame_idx is not None and missing is None:
                    frames[last_frame_idx]['clean']=False; frames[last_frame_idx]['taint_reason']='boundary_before_unquantified_sync_loss'
                g={'index':gap_seq,'byte_start':pos,'byte_end':no,'byte_length':gaplen,'missing_frame_count':missing,'timeline_known':missing is not None,'logical_audio_frame_start':logical_audio_idx if logical_audio_idx is not None else None,
                   'parameter_signature_before':old_sig,'parameter_signature_after':new_sig}
                gaps.append(g);gap_seq+=1;reservoir_segment_id+=1
                sig=new_sig;current_free_format=new_free
                if logical_audio_idx is not None and missing is not None:logical_audio_idx+=missing
                else:logical_audio_idx=None
                after_gap=True;reservoir_bytes_since_gap=0;pos=no;continue
        fl=h['frame_length']
        if pos+fl>terminal:
            issues.append(Issue('TRUNCATED_MPEG_FRAME','framing','El frame MPEG final está incompleto',integrity='DAMAGED',compatibility='LIKELY',byte_start=pos,byte_end=terminal,repairability='SAFE_IF_VERIFIED'));break
        frame=data[pos:pos+fl]
        if not frames:
            xing=_parse_xing(frame,h);vbri=None if xing else _parse_vbri(frame,h);first_is_vbr=bool(xing or vbri)
        else:
            later_xing=_parse_xing(frame,h);later_vbri=None if later_xing else _parse_vbri(frame,h)
            xing_layout_valid=bool(later_xing and not (later_xing.get('flags',0)&~0x0f) and (not later_xing.get('flags')&1 or later_xing.get('frames') is not None) and (not later_xing.get('flags')&2 or later_xing.get('bytes') is not None) and (not later_xing.get('flags')&4 or later_xing.get('toc_present')))
            if xing_layout_valid or (later_vbri and later_vbri.get('layout_valid')):
                unexpected_stream_headers.append({'frame_index':len(frames),'byte_start':pos,'kind':later_xing.get('kind') if xing_layout_valid else 'VBRI'})
        is_header_frame=(len(frames)==0 and first_is_vbr);mdb=_mdb(data,pos,h);clean=True
        if after_gap and h['layer']==3:
            clean=(mdb is not None and mdb<=reservoir_bytes_since_gap);reservoir_bytes_since_gap += _main_data_capacity(h)
            if clean:after_gap=False
        elif after_gap:clean=True;after_gap=False
        li=None if is_header_frame else logical_audio_idx
        crc_status=_mpeg_crc_status(frame,h)
        usage=_layer3_sideinfo_usage(frame,h) if h['layer']==3 else None
        rec={'index':len(frames),'byte_start':pos,'byte_end':pos+fl,'frame_length':fl,'header':h,'main_data_begin':mdb,'is_vbr_header':is_header_frame,'logical_audio_index':li,'clean':clean,'taint_reason':None if clean else 'bit_reservoir_dependency_after_gap','crc':crc_status,'reservoir_segment_id':reservoir_segment_id,'main_data_capacity_bytes':_main_data_capacity(h) if h['layer']==3 else 0,'layer3_sideinfo_usage':usage}
        frames.append(rec);last_frame_idx=len(frames)-1
        if not is_header_frame:
            audio_frames_observed+=1
            if logical_audio_idx is not None:logical_audio_idx+=1
        pos+=fl
    if xing and xing.get('frames') is not None:
        diff=xing['frames']-audio_frames_observed;unknown=[g for g in gaps if not g['timeline_known']]
        if diff>0 and len(unknown)==1:
            g=unknown[0];g['missing_frame_count']=diff;g['timeline_known']=True
            gap_frame_pos=sum(1 for f in frames if not f['is_vbr_header'] and f['byte_end']<=g['byte_start'])
            for f in frames:
                if f['is_vbr_header']:continue
                observed_before=sum(1 for q in frames if not q['is_vbr_header'] and q['byte_start']<f['byte_start'])
                f['logical_audio_index']=observed_before+(diff if f['byte_start']>=g['byte_end'] else 0)
            g['logical_audio_frame_start']=gap_frame_pos
    for g in gaps:
        issues.append(Issue('MPEG_SYNC_LOSS','framing','La secuencia coherente de frames MPEG se interrumpe y luego se resincroniza.',integrity='DAMAGED',compatibility='LIKELY',playability='DEGRADED',byte_start=g['byte_start'],byte_end=g['byte_end'],repairability='RECOVERY_ONLY',evidence=[{'missing_frame_count':g['missing_frame_count'],'timeline_known':g['timeline_known']}]))
    if unexpected_stream_headers:
        u=unexpected_stream_headers[0]
        issues.append(Issue('MPEG_UNEXPECTED_STREAM_HEADER','stream_structure','Se encontró un header global Xing, Info o VBRI estructuralmente válido en un frame MPEG posterior al primer stream header.',integrity='SUSPICIOUS',compatibility='POSSIBLE',playability='UNAFFECTED',repairability='NONE',byte_start=u['byte_start'],byte_end=u['byte_start']+4,evidence=unexpected_stream_headers[:16]))
    parameter_map=_build_parameter_segments(frames,gaps)
    coherent=[t for t in parameter_map.get('transitions',[]) if t.get('interpretation')=='COHERENT_CONCATENATION']
    damaged_change=[t for t in parameter_map.get('transitions',[]) if t.get('interpretation')=='PARAMETER_CHANGE_AFTER_RESYNC']
    if coherent:
        issues.append(Issue('MPEG_COHERENT_PARAMETER_CONCATENATION','stream_parameters',
            f"{len(coherent)} transición(es) contigua(s) de parámetros MPEG forman segmentos coherentes independientemente. Esto es compatible con una concatenación intencional de streams elementales y no con corrupción de bytes, pero no constituye un único stream MPEG homogéneo.",
            integrity='SUSPICIOUS',compatibility='POSSIBLE',playability='UNAFFECTED',repairability='NONE',
            byte_start=coherent[0].get('byte_offset'),byte_end=coherent[0].get('byte_offset'),evidence=coherent[:16]))
    if damaged_change:
        issues.append(Issue('MPEG_PARAMETER_CHANGE_AFTER_RESYNC','stream_parameters',
            f"{len(damaged_change)} transición(es) de parámetros MPEG ocurren sólo después de una región de bytes dañada o resincronizada. El stream posterior a la brecha es coherente, pero el límite no es una concatenación limpia.",
            integrity='DAMAGED',compatibility='LIKELY',playability='DEGRADED',repairability='RECOVERY_ONLY',
            byte_start=damaged_change[0].get('byte_offset'),byte_end=damaged_change[0].get('byte_offset'),evidence=damaged_change[:16]))
    trailing=None
    if pos<terminal and not any(i.code=='TRUNCATED_MPEG_FRAME' for i in issues):
        blob=data[pos:terminal]
        if len(blob)>=16 and not any(blob):
            trailing={'byte_start':pos,'byte_end':terminal,'byte_length':len(blob),'type':'PADDING','all_zero':True}
            issues.append(Issue('MPEG_TRAILING_ZERO_PADDING','trailing','Todos los bytes posteriores al último frame MPEG coherente son padding cero fuera del audio o metadata MPEG reconocidos.',integrity='NONCONFORMANT',compatibility='NONE',playability='UNAFFECTED',byte_start=pos,byte_end=terminal,repairability='SAFE_IF_VERIFIED'))
        else:
            trailing={'byte_start':pos,'byte_end':terminal,'byte_length':len(blob),'type':'UNKNOWN_REGION','all_zero':False}
            issues.append(Issue('MPEG_TRAILING_UNKNOWN_BYTES','trailing','Quedan bytes después del último frame MPEG coherente que no se reconocen como metadata terminal ni como padding cero demostrado.',integrity='SUSPICIOUS',compatibility='POSSIBLE',playability='UNAFFECTED',byte_start=pos,byte_end=terminal,repairability='NONE'))
    if xing and parameter_map.get('hard_profile_transition_count',0)==0:
        if xing.get('frames') is not None and xing['frames']!=audio_frames_observed:
            issues.append(Issue('XING_FRAME_COUNT_MISMATCH','timeline',f"Xing/Info declara {xing['frames']} frames de audio; el análisis estructural observa {audio_frames_observed}.",integrity='NONCONFORMANT',compatibility='POSSIBLE',repairability='SAFE_IF_VERIFIED',evidence=[{'declared':xing['frames'],'observed':audio_frames_observed,'delta':xing['frames']-audio_frames_observed}]))
        if xing.get('bytes') is not None:
            obs=pos-first_off
            if xing['bytes']!=obs:issues.append(Issue('XING_BYTE_COUNT_MISMATCH','seek_metadata',f"Xing/Info declara {xing['bytes']} bytes MPEG; el análisis estructural observa {obs}.",integrity='NONCONFORMANT',compatibility='POSSIBLE',repairability='SAFE_IF_VERIFIED',evidence=[{'declared':xing['bytes'],'observed':obs,'delta':xing['bytes']-obs}]))
        # Los campos Xing acoplados de FFmpeg sólo se diagnostican contra un stream contiguo completo.
        if xing.get('ffmpeg_extended_profile') and not gaps and not any(i.code=='TRUNCATED_MPEG_FRAME' for i in issues) and trailing is None:
            exp=_expected_xing(data,frames,first_off,pos,xing); xing['expected']=exp
            checks=[('kind','XING_KIND_MISMATCH','seek_metadata'),('toc','XING_TOC_MISMATCH','seek_metadata'),('music_length','XING_MUSIC_LENGTH_MISMATCH','seek_metadata'),('music_crc','XING_AUDIO_CRC_MISMATCH','integrity'),('tag_crc','XING_TAG_CRC_MISMATCH','integrity')]
            for key,code,layer in checks:
                if exp and key in exp and xing.get(key)!=exp.get(key):
                    issues.append(Issue(code,layer,f'FFmpeg-profile Xing/Info field {key} es inconsistente con el stream MPEG verificado.',integrity='NONCONFORMANT',compatibility='POSSIBLE',playability='UNAFFECTED',repairability='SAFE_IF_VERIFIED',evidence=[{'declared':xing.get(key),'expected':exp.get(key)}]))
    if vbri and parameter_map.get('hard_profile_transition_count',0)==0:
        if vbri.get('version')!=1:
            issues.append(Issue('VBRI_VERSION_UNSUPPORTED','seek_metadata',f"VBRI version {vbri.get('version')} no está dentro del alcance verificado del parser/reparador V1.",integrity='SUSPICIOUS',compatibility='POSSIBLE',playability='UNAFFECTED',repairability='NONE'))
        elif not vbri.get('layout_valid'):
            issues.append(Issue('VBRI_TOC_LAYOUT_INVALID','seek_metadata','La disposición de la TOC VBRI v1 tiene una cantidad de entradas, escala, ancho de entrada o frames por entrada no válidos, o excede el frame de header contenedor.',integrity='NONCONFORMANT',compatibility='LIKELY',playability='UNAFFECTED',repairability='NONE'))
        if vbri.get('frames') is not None and vbri['frames']!=audio_frames_observed:
            issues.append(Issue('VBRI_FRAME_COUNT_MISMATCH','timeline',f"VBRI declara {vbri['frames']} frames de audio; el análisis estructural observa {audio_frames_observed}.",integrity='NONCONFORMANT',compatibility='POSSIBLE',playability='UNAFFECTED',repairability='SAFE_IF_VERIFIED',evidence=[{'declared':vbri['frames'],'observed':audio_frames_observed,'delta':vbri['frames']-audio_frames_observed}]))
        obs=pos-first_off
        if vbri.get('bytes') is not None and vbri['bytes']!=obs:
            issues.append(Issue('VBRI_BYTE_COUNT_MISMATCH','seek_metadata',f"VBRI declara {vbri['bytes']} bytes MPEG; el análisis estructural observa {obs}.",integrity='NONCONFORMANT',compatibility='POSSIBLE',playability='UNAFFECTED',repairability='SAFE_IF_VERIFIED',evidence=[{'declared':vbri['bytes'],'observed':obs,'delta':vbri['bytes']-obs}]))
        if vbri.get('layout_valid') and not gaps and not any(i.code=='TRUNCATED_MPEG_FRAME' for i in issues) and trailing is None:
            expv=_expected_vbri(frames,first_off,pos,vbri);vbri['expected']=expv
            if expv and not expv.get('layout_representable'):
                issues.append(Issue('VBRI_TOC_FRAME_COVERAGE_MISMATCH','seek_metadata',expv.get('reason') or 'La disposición de la TOC VBRI no puede representar exactamente el mapa observado de frames coherentes.',integrity='NONCONFORMANT',compatibility='POSSIBLE',playability='UNAFFECTED',repairability='NONE'))
            elif expv and vbri.get('toc')!=expv.get('toc'):
                issues.append(Issue('VBRI_TOC_MISMATCH','seek_metadata','La tabla de segmentos de la TOC VBRI no coincide con el mapa verificado de límites de frames MPEG.',integrity='NONCONFORMANT',compatibility='POSSIBLE',playability='UNAFFECTED',repairability='SAFE_IF_VERIFIED',evidence=[{'declared':vbri.get('toc'),'expected':expv.get('toc')}]))
    reservoir_summary=_build_layer3_reservoir_map(frames) if any(f['header'].get('layer')==3 for f in frames) else None
    crc_frames=[f for f in frames if f.get('crc') and f['crc'].get('checked')]
    crc_bad=[f for f in crc_frames if not f['crc'].get('valid')]
    layers=sorted({f['header'].get('layer') for f in frames})
    layer_label='MPEG_LAYER_II' if layers==[2] else ('MPEG_LAYER_III' if layers==[3] else 'MIXED_MPEG_LAYERS')
    coverage='HEADER_LOW16_PLUS_LAYER2_BITALLOC_SCFI' if layers==[2] else ('HEADER_LOW16_PLUS_LAYER3_SIDEINFO' if layers==[3] else 'LAYER_SPECIFIC_PROTECTED_FIELDS')
    crc_summary={'supported_scope':layer_label,'protected_frame_count':sum(1 for f in frames if f['header'].get('protected_by_crc')),
                 'checked_frame_count':len(crc_frames),'valid_frame_count':len(crc_frames)-len(crc_bad),'mismatch_count':len(crc_bad),
                 'algorithm':'MPEG_AUDIO_CRC16','polynomial':'0x8005','initial_state':'0xffff','coverage':coverage}
    if crc_bad:
        first_bad=crc_bad[0]
        samples=[{'frame_index':f['index'],'byte_start':f['byte_start'],'stored':f['crc']['stored'],'computed':f['crc']['computed']} for f in crc_bad[:16]]
        bad_layer=first_bad['header'].get('layer');human_layer='Layer II' if bad_layer==2 else 'Layer III'
        covered='header/bit-allocation/SCFSI' if bad_layer==2 else 'header/side-information'
        issues.append(Issue('MPEG_CRC_MISMATCH','crc',f"{len(crc_bad)} cuadro(s) MPEG {human_layer} protegido(s) tienen una palabra CRC inconsistente con los bits protegidos de {covered}. La discrepancia demuestra una inconsistencia de integridad, pero no localiza si está corrupto el CRC almacenado o los bits cubiertos.",integrity='NONCONFORMANT',compatibility='POSSIBLE',playability='UNAFFECTED',byte_start=first_bad['byte_start']+4,byte_end=first_bad['byte_start']+6,repairability='NONE',evidence=[{'checked_frame_count':len(crc_frames),'mismatch_count':len(crc_bad),'sample_mismatches':samples}]))
    for f in frames:
        if f['is_vbr_header'] or f['header']['layer']!=3 or f['main_data_begin'] is None:continue
        if f['main_data_begin']>511:issues.append(Issue('BIT_RESERVOIR_BACKPOINTER_IMPOSSIBLE','codec','main_data_begin de Layer III excede el rango legal del reservorio',integrity='DAMAGED',playability='DEGRADED',byte_start=f['byte_start'],byte_end=f['byte_start']+4,repairability='RECOVERY_ONLY'))
        dep=f.get('reservoir_dependency') or {}
        if dep.get('overruns_current_frame'):
            issues.append(Issue('BIT_RESERVOIR_MAIN_DATA_OVERRUN','codec','La demanda part2_3_length de Layer III excede todos los bytes de datos principales disponibles hasta el frame actual.',integrity='DAMAGED',playability='DEGRADED',byte_start=f['byte_start'],byte_end=f['byte_end'],repairability='RECOVERY_ONLY',evidence=[{'frame_index':f['index'],'main_data_begin_bytes':dep.get('main_data_begin_bytes'),'main_data_bits_required':dep.get('main_data_bits_required'),'main_data_capacity_bytes':dep.get('main_data_capacity_bytes')}]))
        # Un faltante tras una brecha de resincronización ya está representado por
        # MPEG_SYNC_LOSS y se expone allí como evidencia del reservorio, sin duplicar
        # hallazgos. En el primer segmento ininterrumpido es imposible y sí se expone.
        if dep.get('unavailable_pre_segment_bytes') and dep.get('segment_id')==0:
            issues.append(Issue('BIT_RESERVOIR_BACKPOINTER_UNAVAILABLE','codec','main_data_begin de Layer III referencia bytes anteriores al inicio del segmento demostrable de datos principales.',integrity='DAMAGED',playability='DEGRADED',byte_start=f['byte_start'],byte_end=f['byte_start']+6,repairability='RECOVERY_ONLY',evidence=[{'frame_index':f['index'],'unavailable_pre_segment_bytes':dep.get('unavailable_pre_segment_bytes')}]))
    spf=h0['samples_per_frame']; cpw={'determined':False,'sample_rate':h0['sample_rate'],'channels':h0['channels'],'samples_per_frame':spf}
    if xing and parameter_map.get('hard_profile_transition_count',0)==0 and xing.get('frames') is not None and xing.get('gapless_fields_trusted'):
        total=xing['frames']*spf;delay=xing['encoder_delay_samples'];pad=xing['end_padding_samples']; logical=max(0,total-delay-pad)
        cpw.update({'determined':True,'audio_frame_count':xing['frames'],'encoder_delay_samples':delay,'end_padding_samples':pad,'raw_sample_count':total,'logical_sample_count':logical,'logical_duration_seconds':logical/h0['sample_rate']})
    facts={'identified':True,'first_audio_offset':first_off,'mpeg_version':h0['version'],'layer':h0['layer'],'sample_rate':h0['sample_rate'],'channels':h0['channels'],'samples_per_frame':spf,'frame_count':len(frames),'audio_frame_count_observed':audio_frames_observed,'free_format':bool(h0.get('free_format')),'free_format_frame_length':h0.get('frame_length') if h0.get('free_format') else None,'free_format_bitrate_kbps_estimate':h0.get('bitrate_kbps') if h0.get('free_format') else None,'vbr_header':({'xing':xing} if xing else ({'vbri':vbri} if vbri else {})),'gaps':gaps,'truncated_final_frame':any(i.code=='TRUNCATED_MPEG_FRAME' for i in issues),'canonical_presentation_window':cpw,'terminal_offset':terminal,'scan_end_offset':pos,'trailing_region':trailing,'audio_coverage_ratio':(max(0,pos-first_off)/max(1,terminal-first_off)),'crc_protection':crc_summary,'bit_reservoir':reservoir_summary,'parameter_segments':parameter_map,'compatibility_profile':_compatibility_profile(frames,xing,vbri,md)}
    structural=[]
    if first_off>start:structural.append({'byte_start':start,'byte_end':first_off,'type':'UNKNOWN_REGION','label':'bytes iniciales antes del MPEG coherente'})
    run=None
    for f in frames:
        typ='VALID_AUDIO' if f['clean'] else 'AUDIO_UNVERIFIED_AFTER_DAMAGE'
        if f['is_vbr_header']:typ='AUXILIARY_STRUCTURE'
        if run and run['type']==typ and run['byte_end']==f['byte_start']:run['byte_end']=f['byte_end'];run['frame_count']+=1
        else:
            run={'byte_start':f['byte_start'],'byte_end':f['byte_end'],'type':typ,'label':typ,'frame_count':1};structural.append(run)
    for g in gaps:structural.append({'byte_start':g['byte_start'],'byte_end':g['byte_end'],'type':'BROKEN_STRUCTURE','label':'MPEG resync gap'})
    if trailing:structural.append({'byte_start':trailing['byte_start'],'byte_end':trailing['byte_end'],'type':trailing['type'],'label':'terminal zero padding' if trailing['all_zero'] else 'bytes terminales desconocidos'})
    structural.sort(key=lambda x:x['byte_start'])
    return {'codec':'mp3' if h0['layer']==3 else 'mp2','facts':facts,'metadata':md,'issues':issues,'structural_map':structural,'frames':frames,'gaps':gaps,'data':data}
