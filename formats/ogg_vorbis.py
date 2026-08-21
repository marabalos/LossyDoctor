from __future__ import annotations
from pathlib import Path
import hashlib
from app.models import Issue
from formats.ogg_opus import ogg_crc


def _i64le(b): return int.from_bytes(b,'little',signed=True)
def _u32le(b): return int.from_bytes(b,'little')
def _i32le(b): return int.from_bytes(b,'little',signed=True)
def _u16le(b): return int.from_bytes(b,'little')

def _ilog(x:int)->int:
    n=0
    while x>0:n+=1;x>>=1
    return n

class _Bits:
    def __init__(self,data:bytes): self.data=data; self.pos=0; self.n=len(data)*8
    def read(self,n:int)->int:
        if n<0 or self.pos+n>self.n: raise EOFError
        v=0
        for i in range(n):
            p=self.pos+i; v|=((self.data[p>>3]>>(p&7))&1)<<i
        self.pos+=n; return v
    def skip(self,n:int):
        if self.pos+n>self.n: raise EOFError
        self.pos+=n
    def remaining_bits(self): return self.n-self.pos
    def remaining_zero(self):
        p=self.pos
        while p<self.n:
            if (self.data[p>>3]>>(p&7))&1:return False
            p+=1
        return True

def _lookup1_values(entries:int, dimensions:int)->int:
    if entries<=0 or dimensions<=0:return 0
    lo,hi=0,1
    while pow(hi,dimensions)<=entries: hi*=2
    while lo+1<hi:
        m=(lo+hi)//2
        if pow(m,dimensions)<=entries:lo=m
        else:hi=m
    return lo

def _parse_ident(p:bytes):
    q={'present':p.startswith(b'\x01vorbis'),'valid':False}
    if not q['present'] or len(p)<30:return q
    q.update(version=_u32le(p[7:11]),channels=p[11],sample_rate=_u32le(p[12:16]),
             bitrate_maximum=_i32le(p[16:20]),bitrate_nominal=_i32le(p[20:24]),bitrate_minimum=_i32le(p[24:28]))
    bs=p[28];e0=bs&15;e1=(bs>>4)&15
    q.update(blocksize_0_exponent=e0,blocksize_1_exponent=e1,blocksize_0=1<<e0,blocksize_1=1<<e1,framing_flag=bool(p[29]&1),packet_bytes=len(p))
    q['valid']=(q['version']==0 and q['channels']>0 and q['sample_rate']>0 and 6<=e0<=13 and 6<=e1<=13 and e0<=e1 and q['framing_flag'])
    return q

def _parse_comment(p:bytes):
    q={'present':p.startswith(b'\x03vorbis'),'valid':False,'vendor':None,'comments':[]}
    if not q['present'] or len(p)<12:return q
    pos=7
    try:
        if pos+4>len(p):return q
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
        if pos>=len(p):return q
        framing=bool(p[pos]&1); trailing_bits_zero=(p[pos]&0xfe)==0 and all(x==0 for x in p[pos+1:])
        q.update(comment_count=cnt,framing_flag=framing,trailing_bytes=len(p)-pos-1,valid=framing and trailing_bits_zero)
    except Exception:return q
    return q

def _parse_setup(p:bytes,channels:int):
    q={'present':p.startswith(b'\x05vorbis'),'valid':False,'modes':[]}
    if not q['present'] or len(p)<8:return q
    b=_Bits(p[7:]); codebooks=[]
    try:
        ncb=b.read(8)+1
        if ncb>256:raise ValueError('codebook_count')
        for _ in range(ncb):
            if b.read(24)!=0x564342:raise ValueError('codebook_sync')
            dims=b.read(16); entries=b.read(24)
            if dims==0 or entries==0:raise ValueError('codebook_shape')
            ordered=b.read(1)
            if not ordered:
                sparse=b.read(1)
                for _e in range(entries):
                    if sparse:
                        if b.read(1):b.read(5)
                    else:b.read(5)
            else:
                cur=0;b.read(5)
                while cur<entries:
                    width=_ilog(entries-cur);num=b.read(width)
                    if num<=0 or cur+num>entries:raise ValueError('ordered_lengths')
                    cur+=num
            lookup=b.read(4)
            if lookup>2:raise ValueError('lookup_type')
            if lookup:
                b.read(32);b.read(32);value_bits=b.read(4)+1;b.read(1)
                vals=_lookup1_values(entries,dims) if lookup==1 else entries*dims
                if vals<0 or vals>100000000:raise ValueError('lookup_values')
                b.skip(vals*value_bits)
            codebooks.append({'dimensions':dims,'entries':entries,'lookup_type':lookup})
        time_count=b.read(6)+1
        for _ in range(time_count):
            if b.read(16)!=0:raise ValueError('time_type')
        floor_count=b.read(6)+1
        for _ in range(floor_count):
            ft=b.read(16)
            if ft==0:
                b.read(8);b.read(16);b.read(16);b.read(6);b.read(8);nb=b.read(4)+1
                for _j in range(nb):
                    if b.read(8)>=ncb:raise ValueError('floor0_book')
            elif ft==1:
                np=b.read(5);classes=[b.read(4) for _j in range(np)];mx=max(classes) if classes else -1;dims=[]
                for _j in range(mx+1):
                    d=b.read(3)+1;sc=b.read(2);dims.append(d)
                    if sc and b.read(8)>=ncb:raise ValueError('floor1_masterbook')
                    for _k in range(1<<sc):
                        book=b.read(8)-1
                        if book>=ncb:raise ValueError('floor1_subbook')
                b.read(2);rb=b.read(4);vals=2
                xs={0,1<<rb}
                for cl in classes:
                    for _k in range(dims[cl]):
                        x=b.read(rb);vals+=1
                        if x in xs:raise ValueError('floor1_x_duplicate')
                        xs.add(x)
                if vals>65:raise ValueError('floor1_too_many_values')
            else:raise ValueError('floor_type')
        residue_count=b.read(6)+1
        for _ in range(residue_count):
            rt=b.read(16)
            if rt>2:raise ValueError('residue_type')
            b.read(24);b.read(24);b.read(24);cls=b.read(6)+1;classbook=b.read(8)
            if classbook>=ncb:raise ValueError('residue_classbook')
            casc=[]
            for _j in range(cls):
                low=b.read(3);high=b.read(5) if b.read(1) else 0;casc.append((high<<3)|low)
            for c in casc:
                for bit in range(8):
                    if c&(1<<bit):
                        book=b.read(8)
                        if book>=ncb or codebooks[book]['lookup_type']==0:raise ValueError('residue_book')
        mapping_count=b.read(6)+1
        mappings=[]
        for _ in range(mapping_count):
            if b.read(16)!=0:raise ValueError('mapping_type')
            submaps=b.read(4)+1 if b.read(1) else 1
            coupling=[]
            if b.read(1):
                steps=b.read(8)+1;bits=_ilog(channels-1)
                for _j in range(steps):
                    mag=b.read(bits);ang=b.read(bits)
                    if mag==ang or mag>=channels or ang>=channels:raise ValueError('coupling')
                    coupling.append((mag,ang))
            if b.read(2)!=0:raise ValueError('mapping_reserved')
            mux=[b.read(4) for _j in range(channels)] if submaps>1 else [0]*channels
            if any(x>=submaps for x in mux):raise ValueError('mapping_mux')
            sm=[]
            for _j in range(submaps):
                b.read(8);fl=b.read(8);rs=b.read(8)
                if fl>=floor_count or rs>=residue_count:raise ValueError('mapping_ref')
                sm.append({'floor':fl,'residue':rs})
            mappings.append({'submaps':submaps,'coupling_steps':len(coupling),'mux':mux,'submap_config':sm})
        mode_count=b.read(6)+1;modes=[]
        for i in range(mode_count):
            bf=bool(b.read(1));wt=b.read(16);tt=b.read(16);mp=b.read(8)
            if wt!=0 or tt!=0 or mp>=mapping_count:raise ValueError('mode')
            modes.append({'index':i,'blockflag':bf,'windowtype':wt,'transformtype':tt,'mapping':mp})
        framing=bool(b.read(1))
        if not framing:raise ValueError('setup_framing')
        if not b.remaining_zero():raise ValueError('setup_padding')
        q.update(valid=True,codebook_count=ncb,time_count=time_count,floor_count=floor_count,residue_count=residue_count,mapping_count=mapping_count,mode_count=mode_count,modes=modes,framing_flag=True,bits_consumed=b.pos,padding_bits=b.remaining_bits())
    except (EOFError,ValueError) as e:
        q.update(valid=False,error=str(e),bits_consumed=b.pos)
    return q

def _audio_packet_header(pkt:bytes,setup:dict,ident:dict):
    q={'valid':False}
    if not setup.get('valid') or not ident.get('valid') or not pkt:return q
    b=_Bits(pkt)
    try:
        if b.read(1)!=0:return {'valid':False,'reason':'non_audio_packet_type'}
        mode_bits=_ilog(setup['mode_count']-1);mode=b.read(mode_bits)
        if mode>=setup['mode_count']:return {'valid':False,'reason':'mode_out_of_range','mode':mode}
        m=setup['modes'][mode];bs=ident['blocksize_1'] if m['blockflag'] else ident['blocksize_0'];prevf=nextf=None
        if m['blockflag']:
            prevf=bool(b.read(1));nextf=bool(b.read(1))
        q.update(valid=True,mode=mode,mode_bits=mode_bits,blockflag=m['blockflag'],blocksize=bs,previous_window_flag=prevf,next_window_flag=nextf)
    except EOFError:q={'valid':False,'reason':'truncated_audio_packet_header'}
    return q

def _parse_pages_packets(data:bytes):
    issues=[];pages=[];off=0
    while off<len(data):
        if off+27>len(data) or data[off:off+4]!=b'OggS':
            nxt=data.find(b'OggS',off+1);end=len(data) if nxt<0 else nxt
            issues.append(Issue('OGG_SYNC_LOSS','container','Se encontraron bytes fuera de una página Ogg analizable estructuralmente.',integrity='DAMAGED',playability='POSSIBLY_AFFECTED',repairability='NONE',byte_start=off,byte_end=end))
            if nxt<0:break
            off=nxt;continue
        version=data[off+4];flags=data[off+5];gran=_i64le(data[off+6:off+14]);serial=_u32le(data[off+14:off+18]);seq=_u32le(data[off+18:off+22]);stored=_u32le(data[off+22:off+26]);nseg=data[off+26];hlen=27+nseg
        if off+hlen>len(data):issues.append(Issue('OGG_TRUNCATED_PAGE','container','El header o la tabla de segmentos de la página Ogg está truncado.',integrity='DAMAGED',playability='POSSIBLY_AFFECTED',repairability='NONE',byte_start=off,byte_end=len(data)));break
        laces=list(data[off+27:off+hlen]);end=off+hlen+sum(laces)
        if end>len(data):issues.append(Issue('OGG_TRUNCATED_PAGE','container','El cuerpo de la página Ogg está truncado.',integrity='DAMAGED',playability='POSSIBLY_AFFECTED',repairability='NONE',byte_start=off,byte_end=len(data)));break
        raw=bytearray(data[off:end]);raw[22:26]=b'\0\0\0\0';calc=ogg_crc(raw);ok=calc==stored
        if not ok:issues.append(Issue('OGG_PAGE_CRC_MISMATCH','container','El CRC-32 de la página Ogg no coincide con sus bytes.',integrity='DAMAGED',playability='POSSIBLY_AFFECTED',repairability='NONE',byte_start=off,byte_end=end,evidence=[{'stored':f'{stored:08x}','calculated':f'{calc:08x}','serial':serial,'sequence':seq}]))
        p={'index':len(pages),'byte_start':off,'byte_end':end,'version':version,'flags':flags,'continued':bool(flags&1),'bos':bool(flags&2),'eos':bool(flags&4),'granule_position':gran,'serial':serial,'sequence':seq,'stored_crc32':f'{stored:08x}','calculated_crc32':f'{calc:08x}','crc_ok':ok,'lacing_values':laces,'body_start':off+hlen,'body_end':end,'sequence_contiguous_from_previous':None,'continuation_consistent_from_previous':None};pages.append(p)
        if version!=0:issues.append(Issue('OGG_VERSION_UNSUPPORTED','container',f'La versión {version} de la estructura de página Ogg no es compatible.',integrity='NONCONFORMANT',repairability='NONE',byte_start=off+4,byte_end=off+5))
        off=end
    byserial={}
    for p in pages:byserial.setdefault(p['serial'],[]).append(p)
    packets=[]
    for serial,ps in byserial.items():
        prev=None;pending=bytearray();start=None;pp=[];spans=[];idx=0;pending_missing_prefix=False
        for p in ps:
            seq_ok=True;cont_ok=True
            if prev is not None:
                seq_ok=p['sequence']==((prev['sequence']+1)&0xffffffff)
                if not seq_ok:issues.append(Issue('OGG_PAGE_SEQUENCE_DISCONTINUITY','container','Los números de secuencia de páginas Ogg no son consecutivos dentro de un stream lógico.',integrity='DAMAGED',playability='POSSIBLY_AFFECTED',repairability='NONE',byte_start=p['byte_start'],byte_end=p['byte_end']))
                exp=bool(prev['lacing_values'] and prev['lacing_values'][-1]==255)
                cont_ok=p['continued']==exp
                if not cont_ok:issues.append(Issue('OGG_CONTINUATION_FLAG_INCONSISTENT','container','El indicador de paquete continuado Ogg no coincide con el lacing de la página anterior.',integrity='DAMAGED',playability='POSSIBLY_AFFECTED',repairability='NONE',byte_start=p['byte_start'],byte_end=p['byte_start']+27))
            p['sequence_contiguous_from_previous']=seq_ok if prev is not None else True
            p['continuation_consistent_from_previous']=cont_ok if prev is not None else True
            if p['continued'] and (prev is None or not seq_ok or not pending):pending_missing_prefix=True
            body=data[p['body_start']:p['body_end']];bp=0;done=[]
            if start is None:start=p['body_start']
            for lace in p['lacing_values']:
                ss=p['body_start']+bp;se=ss+lace
                pending.extend(body[bp:bp+lace]);bp+=lace
                spans.append({'page_index':p['index'],'byte_start':ss,'byte_end':se,'length':lace})
                if p['index'] not in pp:pp.append(p['index'])
                if lace<255:
                    raw=bytes(pending)
                    page_objs=[pages[x] for x in pp]
                    seq_internal=all(page_objs[j]['sequence']==((page_objs[j-1]['sequence']+1)&0xffffffff) for j in range(1,len(page_objs)))
                    cont_internal=all(page_objs[j]['continued'] and page_objs[j]['continuation_consistent_from_previous'] for j in range(1,len(page_objs)))
                    auth=(not pending_missing_prefix and all(x['crc_ok'] and x['version']==0 for x in page_objs) and seq_internal and cont_internal)
                    packets.append({'serial':serial,'index':idx,'data':raw,'sha256':hashlib.sha256(raw).hexdigest(),'byte_start':start,'byte_end':p['body_start']+bp,'page_start_index':pp[0],'page_end_index':p['index'],'page_indices':list(pp),'page_sequences':[x['sequence'] for x in page_objs],'segment_byte_spans':list(spans),'spans_pages':len(pp)>1,'starts_with_missing_prefix':pending_missing_prefix,'crc_authenticated_complete_packet':auth})
                    done.append(idx);idx+=1;pending=bytearray();start=p['body_start']+bp;pp=[];spans=[];pending_missing_prefix=False
            p['completed_packet_indices']=done;prev=p
        if pending:issues.append(Issue('OGG_INCOMPLETE_PACKET_AT_EOF','container','El stream físico termina mientras un paquete Ogg aún está continuado.',integrity='DAMAGED',playability='POSSIBLY_AFFECTED',repairability='NONE',byte_start=start,byte_end=len(data)))
    return pages,byserial,packets,issues


def _packet_link_trusted(prev_pkt:dict,curr_pkt:dict,page_by_index:dict)->bool:
    if not prev_pkt.get('crc_authenticated_complete_packet') or not curr_pkt.get('crc_authenticated_complete_packet'):return False
    a=prev_pkt['page_end_index'];b=curr_pkt['page_start_index']
    if b<a:return False
    if b==a:return True
    for pi in range(a+1,b+1):
        p=page_by_index.get(pi)
        if not p or not p.get('crc_ok') or p.get('version')!=0 or not p.get('sequence_contiguous_from_previous') or not p.get('continuation_consistent_from_previous'):return False
    return True


def _build_packet_provenance(ps:list,audio:list,headers:dict,contributions:dict,setup_page:int|None):
    page_by_index={p['index']:p for p in ps}
    # Ancla las posiciones PCM finales de paquetes a gránulos de páginas autenticadas por CRC.
    absolute_end={}
    for p in ps:
        if setup_page is None or p['index']<=setup_page or not p.get('crc_ok') or p.get('granule_position',-1)<0:continue
        comp=[i for i in p.get('completed_packet_indices',[]) if i in headers and headers.get(i,{}).get('valid')]
        if not comp:continue
        cur=p['granule_position']
        for i in reversed(comp):
            absolute_end.setdefault(i,cur)
            cur-=contributions.get(i,0)
    amap=[]
    for pos,pkt in enumerate(audio):
        h=headers.get(pkt['index']) or {};prev=audio[pos-1] if pos else None
        prev_idx=prev['index'] if prev else None
        link=(prev is not None and _packet_link_trusted(prev,pkt,page_by_index))
        amap.append({'packet_index':pkt['index'],'packet_sha256':pkt['sha256'],'mode':h.get('mode'),'blocksize':h.get('blocksize'),'blockflag':h.get('blockflag'),'previous_window_flag':h.get('previous_window_flag'),'next_window_flag':h.get('next_window_flag'),'page_start_index':pkt['page_start_index'],'page_end_index':pkt['page_end_index'],'page_indices':pkt.get('page_indices',[]),'page_sequences':pkt.get('page_sequences',[]),'segment_byte_spans':pkt.get('segment_byte_spans',[]),'spans_pages':pkt.get('spans_pages',False),'starts_with_missing_prefix':pkt.get('starts_with_missing_prefix',False),'crc_authenticated_complete_packet':bool(pkt.get('crc_authenticated_complete_packet') and h.get('valid')),'overlap_previous_packet_index':prev_idx,'overlap_output_samples':contributions.get(pkt['index']),'overlap_dependency_authenticated':bool(link and h.get('valid') and (headers.get(prev_idx) or {}).get('valid')) if prev else False,'absolute_pcm_end':absolute_end.get(pkt['index'])})
    # Cadenas consecutivas de paquetes autenticados. El primer paquete sólo prepara el decodificador.
    regions=[];run=[]
    def flush(reason_end):
        nonlocal run
        if len(run)>=2:
            first,last=run[0],run[-1];st=first.get('absolute_pcm_end');en=last.get('absolute_pcm_end')
            if st is not None and en is not None and en>st:
                last_page=page_by_index.get(last['page_end_index']) or {}
                eos=bool(last_page.get('eos') and last_page.get('crc_ok'))
                regions.append({'region_index':len(regions)+1,'priming_packet_index':first['packet_index'],'first_published_overlap_packet_index':run[1]['packet_index'],'last_packet_index':last['packet_index'],'packet_indices':[x['packet_index'] for x in run],'pcm_start':st,'pcm_end':en,'sample_count':en-st,'sample_rate':None,'boundary_start':'STREAM_START_OR_GRANULE_ANCHOR' if first['packet_index']==audio[0]['index'] else 'AFTER_UNTRUSTED_OR_MISSING_PACKET','boundary_end':'AUTHENTICATED_EOS' if eos else reason_end,'authenticated_eos_included':eos,'publication_authority':'NONE_PROVEN_PACKET_REGION_EVIDENCE_ONLY'})
        run=[]
    for i,m in enumerate(amap):
        if not m['crc_authenticated_complete_packet']:
            flush('BEFORE_UNTRUSTED_OR_MISSING_PACKET');continue
        if not run:run=[m];continue
        if m['overlap_dependency_authenticated']:run.append(m)
        else:
            flush('BEFORE_DISCONTINUITY');run=[m]
    flush('OPEN_OR_STREAM_END')
    return amap,regions

def analyze(path:Path):
    data=path.read_bytes();pages,byserial,packets,issues=_parse_pages_packets(data)
    serial=None;sp=[]
    for s in byserial:
        q=[x for x in packets if x['serial']==s]
        if q and (q[0]['data'].startswith(b'\x01vorbis') or (len(q)>2 and q[1]['data'].startswith(b'\x03vorbis') and q[2]['data'].startswith(b'\x05vorbis'))):serial=s;sp=q;break
    if serial is None:return {'codec':'ogg_unknown','facts':{'pages':pages,'logical_stream_count':len(byserial)},'metadata':{},'structural_map':pages,'issues':issues}
    ps=byserial[serial];ident=_parse_ident(sp[0]['data']) if sp else {};comment=_parse_comment(sp[1]['data']) if len(sp)>1 else {};setup=_parse_setup(sp[2]['data'],ident.get('channels',0)) if len(sp)>2 else {}
    if not ident.get('valid'):issues.append(Issue('VORBIS_IDENTIFICATION_HEADER_INVALID','codec_header','El header de identificación Vorbis está mal formado o es incompatible con Vorbis I.',integrity='DAMAGED',playability='BLOCKING',repairability='NONE',evidence=[ident]))
    if not comment.get('valid'):issues.append(Issue('VORBIS_COMMENT_HEADER_INVALID','metadata','El header obligatorio de comentarios Vorbis falta o está mal formado.',integrity='NONCONFORMANT',playability='POSSIBLY_AFFECTED',repairability='NONE',evidence=[comment]))
    if not setup.get('valid'):issues.append(Issue('VORBIS_SETUP_HEADER_INVALID','codec_header','El header de configuración Vorbis falta, está mal formado o contiene una configuración de codec no válida.',integrity='DAMAGED',playability='BLOCKING',repairability='NONE',evidence=[setup]))
    if len(sp)>=3 and not (sp[0]['data'].startswith(b'\x01vorbis') and sp[1]['data'].startswith(b'\x03vorbis') and sp[2]['data'].startswith(b'\x05vorbis')):issues.append(Issue('VORBIS_HEADER_ORDER_INVALID','codec_header','Los headers Vorbis no siguen el orden requerido de identificación, comentarios y configuración.',integrity='DAMAGED',playability='BLOCKING',repairability='NONE'))
    if ps and (not ps[0]['bos'] or ps[0]['sequence']!=0 or ps[0]['granule_position']!=0):issues.append(Issue('VORBIS_BOS_HEADER_PAGE_INVALID','container','La página de identificación Vorbis debe ser BOS, secuencia 0 y posición de gránulo 0.',integrity='NONCONFORMANT',playability='POSSIBLY_AFFECTED',repairability='NONE',byte_start=ps[0]['byte_start'],byte_end=ps[0]['byte_end']))
    if sp and ((ps[0].get('completed_packet_indices') or [])!=[0] or sp[0]['page_end_index']!=ps[0]['index']):issues.append(Issue('VORBIS_IDENTIFICATION_PAGE_LAYOUT_INVALID','container','El paquete de identificación Vorbis debe estar solo en la primera página Ogg.',integrity='NONCONFORMANT',playability='POSSIBLY_AFFECTED',repairability='NONE'))
    setup_page=sp[2]['page_end_index'] if len(sp)>2 else None
    if setup_page is not None:
        ep=next((p for p in ps if p['index']==setup_page),None)
        if ep and (ep.get('completed_packet_indices') or [])[-1:]!=[2]:issues.append(Issue('VORBIS_SETUP_PAGE_LAYOUT_INVALID','container','El paquete de configuración Vorbis debe finalizar la página en la que termina; el primer paquete de audio debe comenzar en una página nueva.',integrity='NONCONFORMANT',playability='POSSIBLY_AFFECTED',repairability='NONE'))
        for p in ps:
            if p['index']<=setup_page and p['granule_position']!=0:issues.append(Issue('VORBIS_HEADER_GRANULE_NONZERO','timeline','Las páginas que sólo contienen headers Vorbis deben usar posición de gránulo cero.',integrity='NONCONFORMANT',playability='UNAFFECTED',repairability='NONE',byte_start=p['byte_start'],byte_end=p['byte_end']))
    audio=sp[3:] if len(sp)>=3 else [];headers={x['index']:_audio_packet_header(x['data'],setup,ident) for x in audio};bad=[i for i,h in headers.items() if not h.get('valid')]
    if bad:issues.append(Issue('VORBIS_AUDIO_PACKET_HEADER_INVALID','codec','Uno o más paquetes de audio Vorbis tienen campos no válidos de tipo de paquete, modo o ventana.',integrity='DAMAGED',playability='POSSIBLY_AFFECTED',repairability='NONE',evidence=[{'packet_indices':bad[:64]}]))
    # Progreso de decodificación: el primer paquete prepara; cada siguiente aporta prev/4 + current/4 muestras.
    contributions={};prev_bs=None;cum=0;packet_end={}
    for pkt in audio:
        h=headers.get(pkt['index']) or {};bs=h.get('blocksize')
        if bs is None:continue
        add=0 if prev_bs is None else prev_bs//4+bs//4;cum+=add;contributions[pkt['index']]=add;packet_end[pkt['index']]=cum;prev_bs=bs
    timing=[];last_gp=None;first_audio_page=True
    for p in ps:
        if setup_page is None or p['index']<=setup_page:continue
        comp=[i for i in p.get('completed_packet_indices',[]) if i>=3];gp=p['granule_position'];calc_end=packet_end.get(comp[-1]) if comp else None
        if comp and gp<0:issues.append(Issue('VORBIS_GRANULE_POSITION_MISSING','timeline','La página de audio completa paquetes Vorbis pero no tiene posición de gránulo.',integrity='NONCONFORMANT',playability='POSSIBLY_AFFECTED',repairability='NONE',byte_start=p['byte_start'],byte_end=p['byte_end']))
        if gp>=0 and last_gp is not None and gp<last_gp:issues.append(Issue('VORBIS_GRANULE_POSITION_NONMONOTONIC','timeline','La posición de gránulo Vorbis disminuye dentro del stream lógico.',integrity='DAMAGED',playability='POSSIBLY_AFFECTED',repairability='NONE',byte_start=p['byte_start'],byte_end=p['byte_end']))
        # En streams ordinarios con origen cero, los deltas no EOS deben coincidir con los aportes solapados de paquetes.
        page_add=sum(contributions.get(i,0) for i in comp)
        delta=(gp-last_gp) if gp>=0 and last_gp is not None else None
        if delta is not None and comp and not p['eos'] and delta!=page_add:issues.append(Issue('VORBIS_GRANULE_DELTA_MISMATCH','timeline','El incremento de posición de gránulo Vorbis no coincide con la contribución overlap/add indicada por los tamaños de bloque de paquetes adyacentes.',integrity='NONCONFORMANT',playability='POSSIBLY_AFFECTED',repairability='NONE',byte_start=p['byte_start'],byte_end=p['byte_end'],evidence=[{'delta':delta,'calculated_packet_contribution':page_add}]))
        eos_trim=None
        if p['eos'] and gp>=0 and calc_end is not None:
            eos_trim=calc_end-gp
            max_trim=(headers.get(comp[-1]) or {}).get('blocksize',0)//2 if comp else 0
            if eos_trim<0 or eos_trim>max_trim:issues.append(Issue('VORBIS_EOS_TRIM_INVALID','timeline','La posición final de gránulo Vorbis implica un recorte terminal imposible.',integrity='NONCONFORMANT',playability='POSSIBLY_AFFECTED',repairability='NONE',byte_start=p['byte_start'],byte_end=p['byte_end'],evidence=[{'calculated_pcm_end':calc_end,'granule_position':gp,'trim':eos_trim,'max_trim_guard':max_trim}]))
        if gp>=0:last_gp=gp
        timing.append({'page_index':p['index'],'granule_position':gp,'granule_delta':delta,'completed_audio_packets':comp,'calculated_packet_contribution':page_add,'calculated_pcm_end_from_zero_origin':calc_end,'eos':p['eos'],'eos_trim_samples':eos_trim})
    if ps and not ps[-1]['eos']:issues.append(Issue('OGG_VORBIS_EOS_MISSING','container','El stream lógico Ogg Vorbis termina sin una página EOS; esto puede indicar una captura truncada o en vivo.',integrity='SUSPICIOUS',playability='UNAFFECTED',repairability='NONE',byte_start=ps[-1]['byte_start'],byte_end=ps[-1]['byte_end']))
    mode_counts={}
    for h in headers.values():
        if h.get('valid'):mode_counts[str(h['mode'])]=mode_counts.get(str(h['mode']),0)+1
    block_counts={}
    for h in headers.values():
        if h.get('valid'):block_counts[str(h['blocksize'])]=block_counts.get(str(h['blocksize']),0)+1
    audio_packet_map,candidate_regions=_build_packet_provenance(ps,audio,headers,contributions,setup_page)
    for r in candidate_regions:r['sample_rate']=ident.get('sample_rate')
    recovery_evidence={'schema':1,'policy':'VORBIS_PROVEN_PACKET_REGION_EVIDENCE_ONLY','repair_authority':'NONE','pcm_recovery_authority':'NONE','publication_enabled':False,'first_packet_of_each_chain_is_priming_only':True,'overlap_dependency_rule':'PCM_BETWEEN_PACKET_CENTERS_DEPENDS_ON_PREVIOUS_AND_CURRENT_BLOCK','authenticated_audio_packet_count':sum(1 for x in audio_packet_map if x.get('crc_authenticated_complete_packet')),'cross_page_audio_packet_count':sum(1 for x in audio_packet_map if x.get('spans_pages')),'authenticated_cross_page_audio_packet_count':sum(1 for x in audio_packet_map if x.get('spans_pages') and x.get('crc_authenticated_complete_packet')),'candidate_region_count':len(candidate_regions),'candidate_regions':candidate_regions}
    facts={'ogg':{'page_count':len(ps),'logical_stream_count':len(byserial),'serial':serial,'all_page_crc_valid':all(p['crc_ok'] for p in ps),'sequence_start':ps[0]['sequence'] if ps else None,'sequence_end':ps[-1]['sequence'] if ps else None,'bos_present':bool(ps and ps[0]['bos']),'eos_present':bool(ps and ps[-1]['eos'])},'vorbis_identification':ident,'vorbis_comment':comment,'vorbis_setup':setup,'packet_count':len(sp),'audio_packet_count':len(audio),'audio_packet_headers':headers,'audio_packet_map':audio_packet_map,'audio_mode_counts':mode_counts,'audio_blocksize_counts':block_counts,'timing_pages':timing,'vorbis_recovery_evidence':recovery_evidence,'final_granule_position':last_gp,'playback_seconds':(last_gp/ident['sample_rate']) if last_gp is not None and ident.get('valid') else None}
    metadata={'vendor':comment.get('vendor'),'comments':comment.get('comments',[]),'sample_rate':ident.get('sample_rate'),'channels':ident.get('channels'),'bitrate_nominal':ident.get('bitrate_nominal'),'bitrate_minimum':ident.get('bitrate_minimum'),'bitrate_maximum':ident.get('bitrate_maximum')}
    return {'codec':'vorbis','facts':facts,'metadata':metadata,'structural_map':ps,'issues':issues}
