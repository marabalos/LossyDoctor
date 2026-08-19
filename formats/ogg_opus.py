from __future__ import annotations
from pathlib import Path
import hashlib
from app.models import Issue

_POLY=0x04C11DB7

def _crc_table():
    t=[]
    for i in range(256):
        r=i<<24
        for _ in range(8): r=((r<<1)^_POLY)&0xffffffff if r&0x80000000 else (r<<1)&0xffffffff
        t.append(r)
    return t
_CRC=_crc_table()

def ogg_crc(data:bytes)->int:
    r=0
    for b in data:r=((r<<8)&0xffffffff)^_CRC[((r>>24)&0xff)^b]
    return r

def _i64le(b):return int.from_bytes(b,'little',signed=True)
def _u32le(b):return int.from_bytes(b,'little')
def _u16le(b):return int.from_bytes(b,'little')
def _i16le(b):return int.from_bytes(b,'little',signed=True)

def opus_packet_samples(pkt:bytes):
    if not pkt:return None
    toc=pkt[0];config=toc>>3;code=toc&3
    if config<12:dur_ms=(10,20,40,60)[config&3]
    elif config<16:dur_ms=(10,20)[config&1]
    else:dur_ms=(2.5,5,10,20)[config&3]
    if code==0:n=1
    elif code in (1,2):n=2
    else:
        if len(pkt)<2:return None
        n=pkt[1]&0x3f
        if n==0:return None
    samples=int(dur_ms*48*n)
    return samples if samples<=5760 else None

def _parse_head(p:bytes):
    q={'present':p.startswith(b'OpusHead'),'valid':False}
    if not q['present'] or len(p)<19:return q
    q.update(version=p[8],channels=p[9],pre_skip=_u16le(p[10:12]),input_sample_rate=_u32le(p[12:16]),output_gain_q7_8=_i16le(p[16:18]),output_gain_db=_i16le(p[16:18])/256.0,mapping_family=p[18])
    fam=q['mapping_family'];c=q['channels'];need=19
    if fam!=0:
        if len(p)<21+c:return q
        q.update(stream_count=p[19],coupled_count=p[20],channel_mapping=list(p[21:21+c]));need=21+c
    else:q.update(stream_count=1,coupled_count=1 if c==2 else 0,channel_mapping=list(range(c)))
    q['packet_bytes']=len(p);q['minimum_bytes']=need
    q['version_compatible']=q['version']<=15
    mapping_ok=True
    if c==0:mapping_ok=False
    if fam==0 and c not in (1,2):mapping_ok=False
    if fam==1 and not 1<=c<=8:mapping_ok=False
    if fam!=0:
        m=q['stream_count'];n=q['coupled_count']
        if m<1 or n>m or m+n>255:mapping_ok=False
        if any(x!=255 and x>=m+n for x in q['channel_mapping']):mapping_ok=False
    q['mapping_valid']=mapping_ok
    q['valid']=q['version_compatible'] and mapping_ok and c>0 and len(p)>=need
    return q

def _parse_tags(p:bytes):
    q={'present':p.startswith(b'OpusTags'),'valid':False,'vendor':None,'comments':[]}
    if not q['present'] or len(p)<16:return q
    pos=8
    try:
        n=_u32le(p[pos:pos+4]);pos+=4
        if pos+n>len(p):return q
        q['vendor']=p[pos:pos+n].decode('utf-8','replace');pos+=n
        if pos+4>len(p):return q
        cnt=_u32le(p[pos:pos+4]);pos+=4
        if cnt>100000:return q
        for _ in range(cnt):
            if pos+4>len(p):return q
            ln=_u32le(p[pos:pos+4]);pos+=4
            if pos+ln>len(p):return q
            q['comments'].append(p[pos:pos+ln].decode('utf-8','replace'));pos+=ln
        q['valid']=True;q['comment_count']=cnt;q['trailing_bytes']=len(p)-pos
    except Exception:return q
    return q

def analyze(path:Path):
    data=path.read_bytes();issues=[];pages=[];off=0
    while off<len(data):
        if off+27>len(data) or data[off:off+4]!=b'OggS':
            nxt=data.find(b'OggS',off+1)
            end=len(data) if nxt<0 else nxt
            issues.append(Issue('OGG_SYNC_LOSS','container','Se encontraron bytes fuera de una página Ogg analizable estructuralmente.',integrity='DAMAGED',playability='POSSIBLY_AFFECTED',repairability='CONDITIONAL_SAFE_IF_VERIFIED',byte_start=off,byte_end=end))
            if nxt<0:break
            off=nxt;continue
        version=data[off+4];flags=data[off+5];gran=_i64le(data[off+6:off+14]);serial=_u32le(data[off+14:off+18]);seq=_u32le(data[off+18:off+22]);stored=_u32le(data[off+22:off+26]);nseg=data[off+26]
        hlen=27+nseg
        if off+hlen>len(data):
            issues.append(Issue('OGG_TRUNCATED_PAGE','container','El header o la tabla de segmentos de la página Ogg está truncado.',integrity='DAMAGED',playability='POSSIBLY_AFFECTED',repairability='NONE',byte_start=off,byte_end=len(data)));break
        laces=list(data[off+27:off+hlen]);blen=sum(laces);end=off+hlen+blen
        if end>len(data):
            issues.append(Issue('OGG_TRUNCATED_PAGE','container','El cuerpo de la página Ogg está truncado.',integrity='DAMAGED',playability='POSSIBLY_AFFECTED',repairability='NONE',byte_start=off,byte_end=len(data)));break
        raw=bytearray(data[off:end]);raw[22:26]=b'\0\0\0\0';calc=ogg_crc(raw);crc_ok=calc==stored
        if not crc_ok:issues.append(Issue('OGG_PAGE_CRC_MISMATCH','container','El CRC-32 de la página Ogg no coincide con sus bytes.',integrity='DAMAGED',playability='POSSIBLY_AFFECTED',repairability='NONE',byte_start=off,byte_end=end,evidence=[{'stored':f'{stored:08x}','calculated':f'{calc:08x}','serial':serial,'sequence':seq}]))
        pages.append({'index':len(pages),'byte_start':off,'byte_end':end,'version':version,'flags':flags,'continued':bool(flags&1),'bos':bool(flags&2),'eos':bool(flags&4),'granule_position':gran,'serial':serial,'sequence':seq,'stored_crc32':f'{stored:08x}','calculated_crc32':f'{calc:08x}','crc_ok':crc_ok,'lacing_values':laces,'body_start':off+hlen,'body_end':end})
        if version!=0:issues.append(Issue('OGG_VERSION_UNSUPPORTED','container',f'La versión {version} de la estructura de página Ogg no es compatible.',integrity='NONCONFORMANT',compatibility='LIKELY',repairability='NONE',byte_start=off+4,byte_end=off+5))
        off=end
    # Analiza la secuencia de seriales y páginas, y reconstruye paquetes.
    byserial={}
    for p in pages:byserial.setdefault(p['serial'],[]).append(p)
    packets=[]
    for serial,ps in byserial.items():
        prev=None;pending=bytearray();pending_start=None;pending_pages=[];pending_spans=[];pending_missing_prefix=False;packet_index=0
        for p in ps:
            seq_break=prev is not None and p['sequence']!=(prev['sequence']+1)&0xffffffff
            if seq_break:
                issues.append(Issue('OGG_PAGE_SEQUENCE_DISCONTINUITY','container','Los números de secuencia de páginas Ogg no son consecutivos dentro de un stream lógico.',integrity='DAMAGED',playability='POSSIBLY_AFFECTED',repairability='NONE',byte_start=p['byte_start'],byte_end=p['byte_end'],evidence=[{'serial':serial,'previous_sequence':prev['sequence'],'sequence':p['sequence']}]))
            expected_cont=bool(prev and prev['lacing_values'] and prev['lacing_values'][-1]==255)
            if p['continued']!=expected_cont and prev is not None:
                issues.append(Issue('OGG_CONTINUATION_FLAG_INCONSISTENT','container','El indicador de paquete continuado Ogg no coincide con el estado de lacing de la página anterior.',integrity='DAMAGED',playability='POSSIBLY_AFFECTED',repairability='NONE',byte_start=p['byte_start'],byte_end=p['byte_start']+27))
                if not p['continued']:
                    pending=bytearray();pending_start=None;pending_pages=[];pending_spans=[];pending_missing_prefix=False
            body=data[p['body_start']:p['body_end']];bp=0
            if pending_start is None:
                pending_start=p['body_start'];pending_missing_prefix=bool(p.get('continued'))
            completed=[]
            for lace in p['lacing_values']:
                seg_start=p['body_start']+bp;seg_end=seg_start+lace
                pending.extend(body[bp:bp+lace]);bp+=lace
                if p['index'] not in pending_pages:pending_pages.append(p['index'])
                pending_spans.append({'page_index':p['index'],'byte_start':seg_start,'byte_end':seg_end,'length':lace})
                if lace<255:
                    pkt=bytes(pending);packets.append({'serial':serial,'index':packet_index,'byte_start':pending_start,'byte_end':seg_end,'page_start_index':pending_pages[0] if pending_pages else p['index'],'page_end_index':p['index'],'page_indices':list(pending_pages),'segment_byte_spans':list(pending_spans),'starts_with_missing_prefix':pending_missing_prefix,'sha256':hashlib.sha256(pkt).hexdigest(),'data':pkt});completed.append(packet_index);packet_index+=1
                    pending=bytearray();pending_start=p['body_start']+bp;pending_pages=[];pending_spans=[];pending_missing_prefix=False
            p['completed_packet_indices']=completed
            prev=p
        if pending:
            issues.append(Issue('OGG_INCOMPLETE_PACKET_AT_EOF','container','El stream físico termina mientras un paquete Ogg aún está continuado.',integrity='DAMAGED',playability='POSSIBLY_AFFECTED',repairability='NONE',byte_start=pending_start,byte_end=len(data)))
    # Stream lógico Opus: primer serial cuyo primer paquete es OpusHead.
    opus_serial=None;sp=[]
    for serial in byserial:
        sp=[x for x in packets if x['serial']==serial]
        if sp and sp[0]['data'].startswith(b'OpusHead'):opus_serial=serial;break
    if opus_serial is None:return {'codec':'ogg_unknown','facts':{'pages':pages,'logical_stream_count':len(byserial)},'metadata':{},'structural_map':pages,'issues':issues}
    sp=[x for x in packets if x['serial']==opus_serial];ps=byserial[opus_serial]
    head=_parse_head(sp[0]['data']) if sp else {};tags=_parse_tags(sp[1]['data']) if len(sp)>1 else {}
    if not head.get('valid'):
        issues.append(Issue('OPUS_HEAD_INVALID','codec_header','El header de identificación OpusHead está mal formado o es incompatible.',integrity='DAMAGED',playability='BLOCKING',repairability='NONE',byte_start=sp[0]['byte_start'] if sp else None,byte_end=(sp[0]['byte_start']+len(sp[0]['data'])) if sp else None,evidence=[head]))
    if len(sp)<2 or not tags.get('valid'):
        issues.append(Issue('OPUS_TAGS_INVALID','metadata','El header obligatorio de comentarios OpusTags falta o está mal formado.',integrity='NONCONFORMANT',playability='POSSIBLY_AFFECTED',repairability='NONE',evidence=[tags]))
    if ps and (not ps[0]['bos'] or ps[0]['sequence']!=0 or ps[0]['granule_position']!=0):
        issues.append(Issue('OPUS_BOS_HEADER_PAGE_INVALID','container','La página del header de identificación Opus no cumple los requisitos de BOS, secuencia y gránulo.',integrity='NONCONFORMANT',playability='POSSIBLY_AFFECTED',repairability='NONE',byte_start=ps[0]['byte_start'],byte_end=ps[0]['byte_end']))
    if sp and (sp[0]['page_end_index']!=ps[0]['index'] or (ps[0].get('completed_packet_indices') or [])!=[0]):
        issues.append(Issue('OPUS_HEAD_PAGE_LAYOUT_INVALID','container','OpusHead debe ser el único paquete de la primera página BOS y debe completarse en esa página.',integrity='NONCONFORMANT',playability='POSSIBLY_AFFECTED',repairability='NONE'))
    if len(sp)>1:
        tp=next((p for p in ps if p['index']==sp[1]['page_end_index']),None)
        if tp and (tp.get('completed_packet_indices') or [])[-1:]!=[1]:
            issues.append(Issue('OPUS_TAGS_PAGE_LAYOUT_INVALID','container','OpusTags debe finalizar la página en la que se completa el paquete de comentarios.',integrity='NONCONFORMANT',playability='POSSIBLY_AFFECTED',repairability='NONE',byte_start=tp['byte_start'],byte_end=tp['byte_end']))
    # Temporización de paquetes y páginas de audio.
    audio=sp[2:] if len(sp)>=2 else []
    audio_samples={x['index']:opus_packet_samples(x['data']) for x in audio}
    malformed=[i for i,v in audio_samples.items() if v is None]
    if malformed:issues.append(Issue('OPUS_AUDIO_PACKET_MALFORMED','codec','Uno o más paquetes de audio Opus tienen una secuencia TOC o duración no válida.',integrity='DAMAGED',playability='POSSIBLY_AFFECTED',repairability='NONE',evidence=[{'packet_indices':malformed[:64]}]))
    # Las páginas de cabecera deben tener gránulo 0 hasta completar OpusTags.
    tag_page=sp[1]['page_end_index'] if len(sp)>1 else None
    if tag_page is not None:
        for p in ps:
            if p['index']<=tag_page and p['granule_position']!=0:
                issues.append(Issue('OPUS_HEADER_GRANULE_NONZERO','timeline','Las páginas de header Opus deben usar posición de gránulo cero.',integrity='NONCONFORMANT',playability='UNAFFECTED',repairability='NONE',byte_start=p['byte_start'],byte_end=p['byte_end']))
    prev_gp=None;prev_audio_page=None
    timing_pages=[];packet_positions={};eos_end_trim_samples_48k=None
    for p in ps:
        if tag_page is None or p['index']<=tag_page:continue
        comp=[i for i in p.get('completed_packet_indices',[]) if i>=2]
        samples=sum(audio_samples.get(i) or 0 for i in comp)
        gp=p['granule_position'];prev_before=prev_gp;delta=(gp-prev_before) if gp>=0 and prev_before is not None else None;end_trim=None
        if comp and gp<0:
            issues.append(Issue('OPUS_GRANULE_POSITION_MISSING','timeline','La página de audio completa paquetes Opus pero no tiene posición de gránulo.',integrity='NONCONFORMANT',playability='POSSIBLY_AFFECTED',repairability='NONE',byte_start=p['byte_start'],byte_end=p['byte_end']))
        if gp>=0 and prev_before is not None and gp<prev_before:
            issues.append(Issue('OPUS_GRANULE_POSITION_NONMONOTONIC','timeline','La posición de gránulo Opus disminuye dentro del stream lógico.',integrity='DAMAGED',playability='POSSIBLY_AFFECTED',repairability='NONE',byte_start=p['byte_start'],byte_end=p['byte_end'],evidence=[{'previous':prev_before,'current':gp}]))
        if gp>=0 and prev_before is not None and comp:
            if not p['eos'] and delta!=samples:
                issues.append(Issue('OPUS_GRANULE_DELTA_MISMATCH','timeline','El incremento de posición de gránulo no equivale a las muestras de los paquetes completados en esta página de audio no EOS.',integrity='NONCONFORMANT',playability='POSSIBLY_AFFECTED',repairability='NONE',byte_start=p['byte_start'],byte_end=p['byte_end'],evidence=[{'delta':delta,'completed_packet_samples':samples}]))
            if p['eos']:
                end_trim=samples-delta
                if delta<0 or delta>samples or (comp and end_trim>(audio_samples.get(comp[-1]) or 0)):
                    issues.append(Issue('OPUS_END_TRIM_INVALID','timeline','La posición de gránulo EOS implica un recorte final no válido.',integrity='NONCONFORMANT',playability='POSSIBLY_AFFECTED',repairability='NONE',byte_start=p['byte_start'],byte_end=p['byte_end'],evidence=[{'delta':delta,'completed_packet_samples':samples}]))
                else:eos_end_trim_samples_48k=end_trim
        # Asigna límites de paquetes decodificados. Las páginas no EOS se recorren
        # hacia atrás desde su gránulo; las EOS avanzan desde el gránulo anterior
        # y registran por separado el recorte de presentación.
        if gp>=0 and comp:
            if p['eos'] and prev_before is not None:
                cur=prev_before
                for pi in comp:
                    dur=audio_samples.get(pi)
                    if dur is None:continue
                    packet_positions[pi]={'decoded_granule_start':cur,'decoded_granule_end':cur+dur,'presentation_granule_end':cur+dur,'tail_trim_samples_48k':0}
                    cur+=dur
                if end_trim is not None and 0<=end_trim<=int(audio_samples.get(comp[-1]) or 0):
                    packet_positions[comp[-1]]['presentation_granule_end']=gp;packet_positions[comp[-1]]['tail_trim_samples_48k']=end_trim
            else:
                end=gp
                for pi in reversed(comp):
                    dur=audio_samples.get(pi)
                    if dur is None:continue
                    packet_positions[pi]={'decoded_granule_start':end-dur,'decoded_granule_end':end,'presentation_granule_end':end,'tail_trim_samples_48k':0};end-=dur
        if gp>=0:prev_gp=gp;prev_audio_page=p
        timing_pages.append({'page_index':p['index'],'granule_position':gp,'granule_delta':delta,'completed_audio_packets':comp,'completed_packet_samples':samples,'eos':p['eos'],'end_trim_samples_48k':end_trim})
    if ps and not ps[-1]['eos']:
        issues.append(Issue('OGG_OPUS_EOS_MISSING','container','El stream lógico Ogg Opus termina sin una página EOS; esto puede indicar una captura truncada o en vivo.',integrity='SUSPICIOUS',playability='UNAFFECTED',repairability='NONE',byte_start=ps[-1]['byte_start'],byte_end=ps[-1]['byte_end']))
    page_by_index={int(p['index']):p for p in ps};audio_packet_map=[]
    for pkt in audio:
        page_indices=[int(x) for x in (pkt.get('page_indices') or [])];pp=[page_by_index.get(x) for x in page_indices];auth=bool(pp) and not pkt.get('starts_with_missing_prefix') and all(x and x.get('crc_ok') and int(x.get('version',-1))==0 for x in pp)
        if auth and len(pp)>1:
            for a,b in zip(pp,pp[1:]):
                if int(b['sequence'])!=((int(a['sequence'])+1)&0xffffffff) or not bool(b.get('continued')) or not (a.get('lacing_values') and a['lacing_values'][-1]==255) or int(a['byte_end'])!=int(b['byte_start']):auth=False;break
        pos=packet_positions.get(pkt['index']) or {}
        audio_packet_map.append({'packet_index':pkt['index'],'duration_samples_48k':audio_samples.get(pkt['index']),'page_start_index':pkt.get('page_start_index'),'page_end_index':pkt.get('page_end_index'),'page_indices':page_indices,'page_sequences':[int(x['sequence']) for x in pp if x],'spans_pages':len(page_indices)>1,'segment_byte_spans':pkt.get('segment_byte_spans') or [],'packet_sha256':pkt.get('sha256'),'crc_authenticated_complete_packet':bool(auth),'starts_with_missing_prefix':bool(pkt.get('starts_with_missing_prefix')),'ends_on_eos_page':bool(pp and pp[-1].get('eos'))}|pos)
    facts={'ogg':{'page_count':len(ps),'logical_stream_count':len(byserial),'serial':opus_serial,'all_page_crc_valid':all(p['crc_ok'] for p in ps),'sequence_start':ps[0]['sequence'] if ps else None,'sequence_end':ps[-1]['sequence'] if ps else None,'bos_present':bool(ps and ps[0]['bos']),'eos_present':bool(ps and ps[-1]['eos'])},'opus_head':head,'opus_tags':tags,'packet_count':len(sp),'audio_packet_count':len(audio),'audio_packet_duration_samples_48k':audio_samples,'audio_packet_map':audio_packet_map,'timing_pages':timing_pages,'eos_end_trim_samples_48k':eos_end_trim_samples_48k,'final_granule_position':prev_gp,'pcm_sample_position':(prev_gp-head.get('pre_skip',0)) if prev_gp is not None and head.get('valid') else None,'playback_seconds':((prev_gp-head.get('pre_skip',0))/48000.0) if prev_gp is not None and head.get('valid') else None}
    metadata={'vendor':tags.get('vendor'),'comments':tags.get('comments',[]),'input_sample_rate':head.get('input_sample_rate'),'output_gain_q7_8':head.get('output_gain_q7_8'),'output_gain_db':head.get('output_gain_db'),'pre_skip':head.get('pre_skip'),'channels':head.get('channels'),'mapping_family':head.get('mapping_family')}
    return {'codec':'opus','facts':facts,'metadata':metadata,'structural_map':pages,'issues':issues}
