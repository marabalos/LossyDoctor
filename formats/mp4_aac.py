from __future__ import annotations

import hashlib
from pathlib import Path

from app.models import Issue


CONTAINER_BOXES = {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"edts", b"mvex", b"moof", b"traf", b"mfra"}
AAC_OBJECT_TYPES = {1, 2, 3, 4, 5, 6, 17, 19, 20, 21, 22, 23, 29, 39, 42}
AAC_PROFILE_NAMES = {
    1: "AAC Main",
    2: "AAC LC",
    3: "AAC SSR",
    4: "AAC LTP",
    5: "HE-AAC / SBR",
    17: "ER AAC LC",
    23: "ER AAC LD",
    29: "HE-AAC v2 / PS",
    39: "ER AAC ELD",
    42: "USAC / xHE-AAC",
}
SAMPLE_RATES = (96000, 88200, 64000, 48000, 44100, 32000, 24000, 22050, 16000, 12000, 11025, 8000, 7350)


def _issue(code,description,layer="container",integrity="NONCONFORMANT",playability="POSSIBLY_AFFECTED",start=None,end=None,evidence=None):
    return Issue(code,layer,description,integrity=integrity,playability=playability,repairability="NONE",byte_start=start,byte_end=end,evidence=evidence or [])


def _parse_range(data:bytes,start:int,end:int,parent:str,depth:int,issues:list[Issue],structural:list[dict]):
    nodes=[];p=start
    while p<end:
        if end-p<8:
            code="MP4_TRAILING_BYTES" if parent=="file" else "MP4_CONTAINER_TRAILING_BYTES"
            issues.append(_issue(code,f"{end-p} bytes permanecen fuera de un box MP4 completo en {parent}.",integrity="SUSPICIOUS",playability="UNAFFECTED",start=p,end=end))
            structural.append({"byte_start":p,"byte_end":end,"type":"UNKNOWN_REGION","status":"SUSPICIOUS","label":f"Bytes no interpretados en {parent}","depth":depth})
            break
        size32=int.from_bytes(data[p:p+4],"big");kind=data[p+4:p+8];header=8
        if size32==1:
            if p+16>end:
                issues.append(_issue("MP4_BOX_SIZE_INVALID",f"Extended-size {kind.decode('latin-1','replace')} tiene el header de box truncado.",integrity="DAMAGED",start=p,end=end))
                structural.append({"byte_start":p,"byte_end":end,"type":"TRUNCATED_STRUCTURE","status":"DAMAGED","label":"Truncated MP4 extended-size box","depth":depth})
                break
            size=int.from_bytes(data[p+8:p+16],"big");header=16
        elif size32==0:size=end-p
        else:size=size32
        if size<header or p+size>end:
            issues.append(_issue("MP4_BOX_SIZE_INVALID",f"MP4 {kind.decode('latin-1','replace')} tiene un tamaño de box menor que su header o excede el límite de su box padre.",integrity="DAMAGED",start=p,end=min(end,p+max(size,8)),evidence=[{"box_type":kind.decode("latin-1","replace"),"declared_size":size,"parent_end":end}]))
            structural.append({"byte_start":p,"byte_end":min(end,p+max(size,8)),"type":"BROKEN_STRUCTURE","status":"DAMAGED","label":f"MP4 no válido: {kind.decode('latin-1','replace')} box","depth":depth})
            break
        box_end=p+size;payload_start=p+header
        node={"kind":kind,"start":p,"payload_start":payload_start,"end":box_end,"size":size,"header_size":header,"children":[]}
        structural.append({"byte_start":p,"byte_end":box_end,"type":"VALID_STRUCTURE","status":"VALID","label":f"MP4 {kind.decode('latin-1','replace')} box","box_type":kind.decode("latin-1","replace"),"depth":depth})
        if kind in CONTAINER_BOXES:
            node["children"]=_parse_range(data,payload_start,box_end,kind.decode("latin-1"),depth+1,issues,structural)
        nodes.append(node);p=box_end
    return nodes


def _children(node:dict,kind:bytes):
    return [x for x in node.get("children",[]) if x["kind"]==kind]


def _descriptor(data:bytes,p:int,end:int):
    if p>=end:return None
    tag=data[p];p+=1;length=0
    for _ in range(4):
        if p>=end:return None
        byte=data[p];p+=1;length=(length<<7)|(byte&0x7f)
        if not byte&0x80:
            if p+length>end:return None
            return tag,p,p+length
    return None


def _audio_specific_config(data:bytes,start:int,end:int):
    if start>=end:return None
    first=data[start];audio_object_type=first>>3
    bit_offset=5
    if audio_object_type==31:
        if start+1>=end:return None
        audio_object_type=32+(((first&0x07)<<3)|(data[start+1]>>5));bit_offset=11
    value=int.from_bytes(data[start:end],"big");total_bits=(end-start)*8
    def bits(offset,count):
        if offset+count>total_bits:return None
        return (value>>(total_bits-offset-count))&((1<<count)-1)
    frequency_index=bits(bit_offset,4);bit_offset+=4
    explicit_frequency=None
    if frequency_index==15:
        explicit_frequency=bits(bit_offset,24);bit_offset+=24
    channel_configuration=bits(bit_offset,4)
    sample_rate=explicit_frequency if frequency_index==15 else (SAMPLE_RATES[frequency_index] if frequency_index is not None and frequency_index<len(SAMPLE_RATES) else None)
    return {"audio_object_type":audio_object_type,"profile_name":AAC_PROFILE_NAMES.get(audio_object_type,f"AAC object type {audio_object_type}"),"sampling_frequency_index":frequency_index,"sample_rate":sample_rate,"channel_configuration":channel_configuration}


def _esds_config(data:bytes,start:int,end:int):
    root=_descriptor(data,start+4,end) if end-start>=4 else None
    if not root or root[0]!=0x03:return None
    p,root_end=root[1],root[2]
    if p+3>root_end:return None
    flags=data[p+2];p+=3
    if flags&0x80:p+=2
    if flags&0x40:
        if p>=root_end:return None
        p+=1+data[p]
    if flags&0x20:p+=2
    decoder=_descriptor(data,p,root_end)
    if not decoder or decoder[0]!=0x04:return None
    p,decoder_end=decoder[1],decoder[2]
    if p+13>decoder_end:return None
    object_type_indication=data[p];stream_type=(data[p+1]>>2)&0x3f;p+=13
    if stream_type!=0x05:return None
    if object_type_indication in (0x66,0x67,0x68):
        audio_object_type={0x66:1,0x67:2,0x68:3}[object_type_indication]
        return {"object_type_indication":object_type_indication,"audio_object_type":audio_object_type,"profile_name":AAC_PROFILE_NAMES[audio_object_type],"sample_rate":None,"channel_configuration":None}
    if object_type_indication!=0x40:return None
    while p<decoder_end:
        child=_descriptor(data,p,decoder_end)
        if not child:return None
        tag,a,b=child;p=b
        if tag!=0x05:continue
        config=_audio_specific_config(data,a,b)
        if config and config["audio_object_type"] in AAC_OBJECT_TYPES:
            return {"object_type_indication":object_type_indication,**config}
        return None
    return None


def _sample_description(data:bytes,node:dict):
    start=node["payload_start"];end=node["end"]
    if end-start<28:return {"sample_entry":node["kind"].decode("latin-1"),"valid":False}
    version=int.from_bytes(data[start+8:start+10],"big");fixed={0:28,1:44,2:64}.get(version)
    result={"sample_entry":node["kind"].decode("latin-1"),"version":version,"channels":int.from_bytes(data[start+16:start+18],"big"),"sample_size_bits":int.from_bytes(data[start+18:start+20],"big"),"sample_rate":int.from_bytes(data[start+24:start+28],"big")>>16,"valid":False,"aac_config":None}
    if fixed is None or start+fixed>end:return result
    children,_=_raw_boxes(data,start+fixed,end)
    for child in children:
        if child["kind"]==b"esds":result["aac_config"]=_esds_config(data,child["payload_start"],child["end"])
        elif child["kind"]==b"wave":
            nested,_=_raw_boxes(data,child["payload_start"],child["end"])
            for sub in nested:
                if sub["kind"]==b"esds":result["aac_config"]=_esds_config(data,sub["payload_start"],sub["end"])
    result["valid"]=bool(node["kind"]==b"mp4a" and result["aac_config"])
    return result


def _raw_boxes(data:bytes,start:int,end:int):
    nodes=[];p=start
    while p+8<=end:
        size32=int.from_bytes(data[p:p+4],"big");header=8
        if size32==1:
            if p+16>end:return nodes,False
            size=int.from_bytes(data[p+8:p+16],"big");header=16
        elif size32==0:size=end-p
        else:size=size32
        if size<header or p+size>end:return nodes,False
        nodes.append({"kind":data[p+4:p+8],"start":p,"payload_start":p+header,"end":p+size,"size":size,"header_size":header});p+=size
    return nodes,p==end


def _fullbox_table(data:bytes,node:dict,entry_size:int):
    start=node["payload_start"];end=node["end"]
    if end-start<8:return None
    count=int.from_bytes(data[start+4:start+8],"big")
    return {"entry_count":count,"entries_start":start+8,"complete":start+8+count*entry_size<=end}


def _expand_stts(entries:list[dict],expected_count:int):
    durations=[]
    for entry in entries:
        count=entry.get("sample_count",0);delta=entry.get("sample_delta",0)
        if count<0 or delta<=0:return []
        durations.extend([delta]*min(count,max(0,expected_count-len(durations)+1)))
        if len(durations)>expected_count:return durations
    return durations


def _build_access_units(data:bytes,result:dict,mdat_ranges:list[tuple[int,int]],issues:list[Issue]):
    tables=result["sample_tables"];stsc=tables.get("stsc") or {};stsz=tables.get("stsz") or {};stts=tables.get("stts") or {}
    offsets=tables.get("stco") or tables.get("co64") or {}
    sizes=stsz.get("sizes") or [];chunk_offsets=offsets.get("offsets") or [];entries=stsc.get("entries") or []
    declared=stsz.get("sample_count")
    provenance={"policy":"MP4_AAC_ACCESS_UNIT_PROVENANCE_AUDIT_ONLY","sample_count_declared":declared,
        "mapped_sample_count":0,"hashed_sample_count":0,"mapping_complete":False,"decode_timeline_complete":False,
        "all_access_units_within_mdat":False,"all_access_units_hashed":False,"decode_time_origin_units":0,
        "decode_end_units":None,"access_units":[]}
    result["access_unit_provenance"]=provenance
    if declared is None or not stsz.get("complete") or not offsets.get("complete") or not stsc.get("valid"):
        return
    if len(sizes)!=declared:return
    description_count=len(result.get("sample_descriptions") or [])
    chunk_plan=[]
    for chunk_index,chunk_offset in enumerate(chunk_offsets,1):
        active=None
        for entry in entries:
            if entry["first_chunk"]<=chunk_index:active=entry
            else:break
        if active is None:continue
        chunk_plan.append({"chunk_index":chunk_index,"chunk_offset":chunk_offset,
            "samples_per_chunk":active["samples_per_chunk"],"sample_description_index":active["sample_description_index"]})
    capacity=sum(x["samples_per_chunk"] for x in chunk_plan)
    if capacity!=declared:
        issues.append(_issue("MP4_ACCESS_UNIT_MAPPING_INCOMPLETE","Las entradas sample-to-chunk de MP4 no asignan exactamente la cantidad declarada de unidades de acceso AAC.",integrity="DAMAGED",evidence=[{"declared_samples":declared,"mapped_capacity":capacity,"chunk_count":len(chunk_offsets)}]))
    durations=_expand_stts(stts.get("entries") or [],declared) if stts.get("complete") else []
    if len(durations)!=declared:
        issues.append(_issue("MP4_ACCESS_UNIT_TIMELINE_INCOMPLETE","Las entradas de tiempo de decodificación MP4 no asignan exactamente una duración a cada unidad de acceso AAC declarada.",layer="timeline",integrity="DAMAGED",evidence=[{"declared_samples":declared,"timed_samples":len(durations)}]))
    sample_index=0;decode_time=0;rows=[]
    for chunk in chunk_plan:
        chunk_start=chunk["chunk_offset"];cursor=chunk_start
        for _ in range(chunk["samples_per_chunk"]):
            if sample_index>=declared:break
            size=sizes[sample_index];byte_end=cursor+size
            within=bool(size>0 and any(start<=cursor and byte_end<=end for start,end in mdat_ranges))
            duration=durations[sample_index] if sample_index<len(durations) else None
            description_index=chunk["sample_description_index"]
            description_valid=bool(1<=description_index<=description_count and result["sample_descriptions"][description_index-1].get("valid"))
            row={"index":sample_index,"chunk_index":chunk["chunk_index"],"sample_description_index":description_index,
                "sample_description_valid":description_valid,"byte_start":cursor,"byte_end":byte_end,"size":size,
                "within_mdat":within,"sha256":hashlib.sha256(data[cursor:byte_end]).hexdigest() if within else None,
                "decode_time_units":decode_time if duration is not None else None,"duration_units":duration}
            rows.append(row)
            if duration is not None:decode_time+=duration
            cursor=byte_end;sample_index+=1
    invalid_descriptions=[x["index"] for x in rows if not x["sample_description_valid"]]
    outside=[x["index"] for x in rows if not x["within_mdat"]]
    overlaps=[]
    ordered=sorted((x for x in rows if x["within_mdat"]),key=lambda x:(x["byte_start"],x["byte_end"],x["index"]))
    for previous,current in zip(ordered,ordered[1:]):
        if current["byte_start"]<previous["byte_end"]:overlaps.append((previous["index"],current["index"]))
    if invalid_descriptions:
        issues.append(_issue("MP4_ACCESS_UNIT_DESCRIPTION_INVALID","Una o más muestras MP4 asignadas referencian una descripción de muestra AAC ausente o no autenticada.",layer="codec_header",integrity="DAMAGED",evidence=[{"sample_indices":invalid_descriptions[:64]}]))
    if outside:
        issues.append(_issue("MP4_ACCESS_UNIT_OUTSIDE_MDAT","Una o más unidades de acceso AAC asignadas se extienden fuera de todos los payloads de datos multimedia MP4.",integrity="DAMAGED",evidence=[{"sample_indices":outside[:64]}]))
    if overlaps:
        issues.append(_issue("MP4_ACCESS_UNIT_OVERLAP","Los rangos de bytes asignados a unidades de acceso AAC se superponen.",integrity="DAMAGED",evidence=[{"sample_pairs":overlaps[:64]}]))
    provenance.update({"mapped_sample_count":len(rows),"hashed_sample_count":sum(bool(x["sha256"]) for x in rows),
        "mapping_complete":bool(len(rows)==declared==capacity and not invalid_descriptions and not outside and not overlaps),
        "decode_timeline_complete":bool(len(durations)==declared),"all_access_units_within_mdat":bool(rows and not outside),
        "all_access_units_hashed":bool(rows and all(x["sha256"] for x in rows)),
        "decode_end_units":decode_time if len(durations)==declared else None,"access_units":rows})


def _parse_movie_header(data:bytes,moov:dict,issues:list[Issue]):
    headers=_children(moov,b"mvhd")
    if len(headers)!=1:
        issues.append(_issue("MP4_MOVIE_HEADER_INVALID","El moov de MP4 debe contener exactamente un movie header utilizable.",integrity="DAMAGED"));return {}
    node=headers[0];p=node["payload_start"];end=node["end"]
    if p>=end:
        issues.append(_issue("MP4_MOVIE_HEADER_INVALID","El movie header de MP4 está vacío.",integrity="DAMAGED",start=node["start"],end=end));return {}
    version=data[p];timescale_offset=20 if version==1 else 12;duration_offset=24 if version==1 else 16;duration_size=8 if version==1 else 4
    if version not in (0,1) or p+duration_offset+duration_size>end:
        issues.append(_issue("MP4_MOVIE_HEADER_INVALID","La versión o los campos fijos de mvhd MP4 no son válidos.",integrity="DAMAGED",start=node["start"],end=end));return {}
    timescale=int.from_bytes(data[p+timescale_offset:p+timescale_offset+4],"big");duration=int.from_bytes(data[p+duration_offset:p+duration_offset+duration_size],"big")
    if timescale==0:issues.append(_issue("MP4_MOVIE_HEADER_INVALID","La escala temporal de la película MP4 debe ser distinta de cero.",integrity="DAMAGED",start=node["start"],end=end))
    return {"version":version,"timescale":timescale,"duration":duration,"byte_start":node["start"],"byte_end":end}


def _parse_edit_list(data:bytes,trak:dict,issues:list[Issue]):
    lists=[]
    for edts in _children(trak,b"edts"):lists.extend(_children(edts,b"elst"))
    if not lists:return {"present":False,"complete":True,"entries":[]}
    if len(lists)!=1:
        issues.append(_issue("MP4_EDIT_LIST_INVALID","La pista MP4 contiene más de una lista de edición.",layer="timeline",integrity="DAMAGED"));return {"present":True,"complete":False,"entries":[]}
    node=lists[0];p=node["payload_start"];end=node["end"]
    if end-p<8:
        issues.append(_issue("MP4_EDIT_LIST_INVALID","La lista de edición MP4 no contiene el header full-box requerido.",layer="timeline",integrity="DAMAGED",start=node["start"],end=end));return {"present":True,"complete":False,"entries":[]}
    version=data[p];count=int.from_bytes(data[p+4:p+8],"big");width=20 if version==1 else 12
    complete=bool(version in (0,1) and p+8+count*width<=end);entries=[];cursor=p+8
    if complete:
        for _ in range(count):
            if version==1:
                segment_duration=int.from_bytes(data[cursor:cursor+8],"big");media_time=int.from_bytes(data[cursor+8:cursor+16],"big",signed=True);cursor+=16
            else:
                segment_duration=int.from_bytes(data[cursor:cursor+4],"big");media_time=int.from_bytes(data[cursor+4:cursor+8],"big",signed=True);cursor+=8
            rate_integer=int.from_bytes(data[cursor:cursor+2],"big",signed=True);rate_fraction=int.from_bytes(data[cursor+2:cursor+4],"big",signed=True);cursor+=4
            entries.append({"segment_duration_movie_units":segment_duration,"media_time":media_time,"media_rate_integer":rate_integer,"media_rate_fraction":rate_fraction})
    if not complete or not entries:
        issues.append(_issue("MP4_EDIT_LIST_INVALID","La lista de edición MP4 está vacía, truncada o usa una versión no compatible.",layer="timeline",integrity="DAMAGED",start=node["start"],end=end))
    return {"present":True,"version":version,"entry_count":count,"complete":complete,"entries":entries,"byte_start":node["start"],"byte_end":end}


def _parse_track(data:bytes,trak:dict,mdat_ranges:list[tuple[int,int]],issues:list[Issue],fragmented=False):
    edit_list=_parse_edit_list(data,trak,issues)
    result={"track_id":None,"handler_type":None,"media_timescale":None,"media_duration":None,"media_header":{},"edit_list_present":edit_list["present"],"edit_list":edit_list,"sample_descriptions":[],"sample_tables":{}}
    tkhd=_children(trak,b"tkhd")
    if tkhd:
        p=tkhd[0]["payload_start"];end=tkhd[0]["end"]
        if p<end:
            version=data[p];offset=20 if version==1 else 12
            if p+offset+4<=end:result["track_id"]=int.from_bytes(data[p+offset:p+offset+4],"big")
    mdias=_children(trak,b"mdia")
    if not mdias:return result
    mdia=mdias[0]
    hdlr=_children(mdia,b"hdlr")
    if hdlr:
        p=hdlr[0]["payload_start"]
        if p+12<=hdlr[0]["end"]:result["handler_type"]=data[p+8:p+12].decode("latin-1")
    mdhd=_children(mdia,b"mdhd")
    result["media_header"]["box_count"]=len(mdhd)
    if mdhd:
        p=mdhd[0]["payload_start"];end=mdhd[0]["end"]
        if p<end:
            version=data[p];ts_offset=20 if version==1 else 12;duration_offset=24 if version==1 else 16;duration_size=8 if version==1 else 4
            if p+duration_offset+duration_size<=end:
                result["media_timescale"]=int.from_bytes(data[p+ts_offset:p+ts_offset+4],"big")
                result["media_duration"]=int.from_bytes(data[p+duration_offset:p+duration_offset+duration_size],"big")
                result["media_header"].update({"version":version,"box_start":mdhd[0]["start"],"box_end":end,
                    "duration_byte_start":p+duration_offset,"duration_byte_end":p+duration_offset+duration_size,"duration_width":duration_size})
    stbls=[]
    for minf in _children(mdia,b"minf"):stbls.extend(_children(minf,b"stbl"))
    for stbl in stbls:
        for stsd in _children(stbl,b"stsd"):
            p=stsd["payload_start"];end=stsd["end"]
            if end-p<8:continue
            declared=int.from_bytes(data[p+4:p+8],"big");entries,complete=_raw_boxes(data,p+8,end)
            if not complete or len(entries)!=declared:
                issues.append(_issue("MP4_SAMPLE_DESCRIPTION_TABLE_INVALID","Las entradas stsd de MP4 no coinciden con la cantidad declarada de descripciones de muestra.",integrity="DAMAGED",start=stsd["start"],end=end,evidence=[{"declared":declared,"parsed":len(entries)}]))
            result["sample_descriptions"].extend(_sample_description(data,x) for x in entries)
        for stts in _children(stbl,b"stts"):
            table=_fullbox_table(data,stts,8)
            if table:
                entries=[];p=table["entries_start"]
                if table["complete"]:
                    for _ in range(table["entry_count"]):
                        entries.append({"sample_count":int.from_bytes(data[p:p+4],"big"),"sample_delta":int.from_bytes(data[p+4:p+8],"big")});p+=8
                result["sample_tables"]["stts"]={"entry_count":table["entry_count"],"complete":table["complete"],"sample_count":sum(x["sample_count"] for x in entries),"duration_units":sum(x["sample_count"]*x["sample_delta"] for x in entries),"entries":entries}
                if not table["complete"]:issues.append(_issue("MP4_DECODING_TIME_TABLE_INVALID","El stts de MP4 no contiene todas las entradas de tiempo de decodificación declaradas.",layer="timeline",integrity="DAMAGED",start=stts["start"],end=stts["end"]))
        for stsz in _children(stbl,b"stsz"):
            p=stsz["payload_start"];end=stsz["end"]
            if end-p>=12:
                default=int.from_bytes(data[p+4:p+8],"big");count=int.from_bytes(data[p+8:p+12],"big");complete=bool(default or p+12+count*4<=end)
                available_count=(end-(p+12))//4 if not default else count
                available_sizes=([int.from_bytes(data[p+12+i*4:p+16+i*4],"big") for i in range(available_count)] if not default else [default]*count)
                total_mdat_bytes=sum(b-a for a,b in mdat_ranges)
                plausible=bool(not default or (default>0 and count<=total_mdat_bytes//default))
                sizes=[default]*count if default and plausible else ([int.from_bytes(data[p+12+i*4:p+16+i*4],"big") for i in range(count)] if complete and not default else [])
                complete=complete and plausible
                result["sample_tables"]["stsz"]={"default_sample_size":default,"sample_count":count,"complete":complete,"sizes":sizes,
                    "available_entry_count":available_count,"available_sizes":available_sizes,"sample_count_byte_start":p+8,"sample_count_byte_end":p+12,
                    "box_start":stsz["start"],"box_end":end}
                if not complete:issues.append(_issue("MP4_SAMPLE_SIZE_TABLE_TRUNCATED","El stsz de MP4 no contiene todos los tamaños por muestra declarados.",integrity="DAMAGED",start=stsz["start"],end=end,evidence=[{"sample_count":count,"available_entries":max(0,(end-(p+12))//4)}]))
        for stsc in _children(stbl,b"stsc"):
            table=_fullbox_table(data,stsc,12);entries=[];description_index_byte_offsets=[]
            if table and table["complete"]:
                p=table["entries_start"]
                for _ in range(table["entry_count"]):
                    description_index_byte_offsets.append(p+8);entries.append({"first_chunk":int.from_bytes(data[p:p+4],"big"),"samples_per_chunk":int.from_bytes(data[p+4:p+8],"big"),"sample_description_index":int.from_bytes(data[p+8:p+12],"big")});p+=12
            valid=bool(table and table["complete"] and entries and entries[0]["first_chunk"]==1 and all(x["samples_per_chunk"]>0 and x["sample_description_index"]>0 for x in entries) and all(a["first_chunk"]<b["first_chunk"] for a,b in zip(entries,entries[1:])))
            result["sample_tables"]["stsc"]={"entry_count":table["entry_count"] if table else None,"complete":bool(table and table["complete"]),"valid":valid,"entries":entries,"sample_description_index_byte_offsets":description_index_byte_offsets}
            if not valid and not (fragmented and table and table["complete"] and table["entry_count"]==0):issues.append(_issue("MP4_SAMPLE_TO_CHUNK_TABLE_INVALID","El stsc de MP4 está truncado, vacío o contiene transiciones sample-to-chunk no válidas.",integrity="DAMAGED",start=stsc["start"],end=stsc["end"]))
        for name,width in ((b"stco",4),(b"co64",8)):
            for box in _children(stbl,name):
                table=_fullbox_table(data,box,width);offsets=[];entry_byte_offsets=[]
                if table and table["complete"]:
                    p=table["entries_start"]
                    for _ in range(table["entry_count"]):entry_byte_offsets.append(p);offsets.append(int.from_bytes(data[p:p+width],"big"));p+=width
                inside=[any(a<=offset<b for a,b in mdat_ranges) for offset in offsets]
                result["sample_tables"][name.decode("ascii")]={"entry_count":table["entry_count"] if table else None,"complete":bool(table and table["complete"]),"offsets":offsets,"entry_byte_offsets":entry_byte_offsets,"entry_width":width,"box_start":box["start"],"box_end":box["end"],"valid_offset_count":sum(inside),"all_offsets_inside_mdat":bool(offsets and all(inside))}
                if offsets and not all(inside):issues.append(_issue("MP4_CHUNK_OFFSET_OUTSIDE_MDAT","Uno o más offsets de chunk MP4 apuntan fuera de todos los payloads de datos multimedia.",integrity="DAMAGED",start=box["start"],end=box["end"],evidence=[{"invalid_offsets":[x for x,ok in zip(offsets,inside) if not ok][:32]}]))
    if "stts" not in result["sample_tables"]:issues.append(_issue("MP4_DECODING_TIME_TABLE_INVALID","La pista de audio MP4 no tiene una tabla de tiempos de decodificación utilizable.",layer="timeline",integrity="DAMAGED"))
    if "stsz" not in result["sample_tables"]:issues.append(_issue("MP4_SAMPLE_SIZE_TABLE_TRUNCATED","La pista de audio MP4 no tiene una tabla de tamaños de muestra utilizable.",integrity="DAMAGED"))
    if "stsc" not in result["sample_tables"]:issues.append(_issue("MP4_SAMPLE_TO_CHUNK_TABLE_INVALID","La pista de audio MP4 no tiene una tabla sample-to-chunk utilizable.",integrity="DAMAGED"))
    stco=result["sample_tables"].get("stco");co64=result["sample_tables"].get("co64");offset_table=stco or co64 or {}
    if bool(stco)==bool(co64) or not offset_table.get("complete"):
        issues.append(_issue("MP4_CHUNK_OFFSET_TABLE_INVALID","La pista de audio MP4 debe tener exactamente una tabla utilizable de offsets de chunk de 32 o 64 bits.",integrity="DAMAGED"))
    _build_access_units(data,result,mdat_ranges,issues)
    stts=result["sample_tables"].get("stts") or {};stsz=result["sample_tables"].get("stsz") or {}
    if stts.get("sample_count") is not None and stsz.get("sample_count") is not None and stts["sample_count"]!=stsz["sample_count"]:
        issues.append(_issue("MP4_SAMPLE_COUNT_MISMATCH","Las tablas de tiempos de decodificación y tamaños de muestra MP4 declaran cantidades de muestras diferentes.",evidence=[{"stts_sample_count":stts["sample_count"],"stsz_sample_count":stsz["sample_count"]}]))
    if not fragmented and result["media_duration"] is not None and stts.get("duration_units") is not None and result["media_duration"]!=stts["duration_units"]:
        issues.append(_issue("MP4_MEDIA_DURATION_MISMATCH","La duración mdhd de MP4 difiere de la suma de duraciones de la tabla de tiempos de decodificación.",layer="timeline",evidence=[{"mdhd_duration":result["media_duration"],"stts_duration":stts["duration_units"],"timescale":result["media_timescale"]}]))
    return result


def _parse_fragment_defaults(data:bytes,moovs:list[dict],issues:list[Issue]):
    defaults={}
    for moov in moovs:
        for mvex in _children(moov,b"mvex"):
            for trex in _children(mvex,b"trex"):
                p=trex["payload_start"]
                if trex["end"]-p<24:
                    issues.append(_issue("MP4_FRAGMENT_DEFAULTS_INVALID","Los valores predeterminados de fragmento trex de MP4 están truncados.",integrity="DAMAGED",start=trex["start"],end=trex["end"]));continue
                track_id=int.from_bytes(data[p+4:p+8],"big")
                if not track_id or track_id in defaults:
                    issues.append(_issue("MP4_FRAGMENT_DEFAULTS_INVALID","Los identificadores de pista trex de MP4 deben ser distintos de cero y únicos.",integrity="DAMAGED",start=trex["start"],end=trex["end"]));continue
                defaults[track_id]={"sample_description_index":int.from_bytes(data[p+8:p+12],"big"),
                    "sample_duration":int.from_bytes(data[p+12:p+16],"big"),"sample_size":int.from_bytes(data[p+16:p+20],"big"),
                    "sample_flags":int.from_bytes(data[p+20:p+24],"big")}
    return defaults


def _parse_fragments(data:bytes,top:list[dict],moovs:list[dict],tracks:list[dict],mdat_ranges:list[tuple[int,int]],issues:list[Issue]):
    moofs=[x for x in top if x["kind"]==b"moof"];defaults=_parse_fragment_defaults(data,moovs,issues);by_id={x.get("track_id"):x for x in tracks if x.get("track_id")}
    facts={"policy":"MP4_AAC_FRAGMENTED_MP4_AUDIT_ONLY","fragmented":bool(moofs),"fragment_count":len(moofs),"defaults_by_track":defaults,
        "mapping_complete":False,"sequence_numbers":[],"runs":[]}
    rows_by_track={track_id:[] for track_id in by_id};complete=bool(moofs and defaults);previous_sequence=None
    for fragment_index,moof in enumerate(moofs):
        mfhd=_children(moof,b"mfhd")
        if len(mfhd)!=1 or mfhd[0]["end"]-mfhd[0]["payload_start"]<8:
            issues.append(_issue("MP4_FRAGMENT_HEADER_INVALID","Cada moof debe contener un mfhd completo.",integrity="DAMAGED",start=moof["start"],end=moof["end"]));complete=False;continue
        sequence=int.from_bytes(data[mfhd[0]["payload_start"]+4:mfhd[0]["payload_start"]+8],"big");facts["sequence_numbers"].append(sequence)
        if previous_sequence is not None and sequence!=previous_sequence+1:
            issues.append(_issue("MP4_FRAGMENT_SEQUENCE_INVALID","Los números de secuencia de fragmentos MP4 no son consecutivos.",layer="timeline",integrity="NONCONFORMANT",evidence=[{"previous":previous_sequence,"current":sequence}]));complete=False
        previous_sequence=sequence
        for traf in _children(moof,b"traf"):
            tfhds=_children(traf,b"tfhd");tfdts=_children(traf,b"tfdt");truns=_children(traf,b"trun")
            if len(tfhds)!=1 or len(tfdts)!=1 or not truns:
                issues.append(_issue("MP4_FRAGMENT_TRACK_RUN_INVALID","Cada traf compatible requiere un tfhd, un tfdt y al menos un trun.",integrity="DAMAGED",start=traf["start"],end=traf["end"]));complete=False;continue
            tfhd=tfhds[0];p=tfhd["payload_start"]
            if tfhd["end"]-p<8:complete=False;issues.append(_issue("MP4_FRAGMENT_TRACK_RUN_INVALID","El tfhd de MP4 está truncado.",integrity="DAMAGED",start=tfhd["start"],end=tfhd["end"]));continue
            flags=int.from_bytes(data[p+1:p+4],"big");track_id=int.from_bytes(data[p+4:p+8],"big");cursor=p+8;base_data_offset=None
            def take(width):
                nonlocal cursor
                if cursor+width>tfhd["end"]:return None
                value=int.from_bytes(data[cursor:cursor+width],"big");cursor+=width;return value
            if flags&0x000001:base_data_offset=take(8)
            description_index=take(4) if flags&0x000002 else (defaults.get(track_id) or {}).get("sample_description_index")
            default_duration=take(4) if flags&0x000008 else (defaults.get(track_id) or {}).get("sample_duration")
            default_size=take(4) if flags&0x000010 else (defaults.get(track_id) or {}).get("sample_size")
            if flags&0x000020:take(4)
            if cursor>tfhd["end"] or track_id not in by_id or (base_data_offset is None and not flags&0x020000):
                issues.append(_issue("MP4_FRAGMENT_BASE_OR_TRACK_UNSUPPORTED","La identidad de pista o la base del offset de datos del fragmento es ambigua.",integrity="SUSPICIOUS",start=tfhd["start"],end=tfhd["end"]));complete=False;continue
            tfdt=tfdts[0];p=tfdt["payload_start"]
            if tfdt["end"]-p<8 or data[p] not in (0,1):
                issues.append(_issue("MP4_FRAGMENT_DECODE_TIME_INVALID","El tfdt de MP4 está truncado o usa una versión no compatible.",layer="timeline",integrity="DAMAGED",start=tfdt["start"],end=tfdt["end"]));complete=False;continue
            width=8 if data[p]==1 else 4
            if p+4+width>tfdt["end"]:complete=False;continue
            decode_time=int.from_bytes(data[p+4:p+4+width],"big");run_data_end=None
            for run_index,trun in enumerate(truns):
                p=trun["payload_start"]
                if trun["end"]-p<8:complete=False;continue
                version=data[p];run_flags=int.from_bytes(data[p+1:p+4],"big");count=int.from_bytes(data[p+4:p+8],"big");cursor=p+8
                data_offset=int.from_bytes(data[cursor:cursor+4],"big",signed=True) if run_flags&0x000001 and cursor+4<=trun["end"] else None
                if run_flags&0x000001:cursor+=4
                if run_flags&0x000004:cursor+=4
                data_cursor=(base_data_offset if base_data_offset is not None else moof["start"])+data_offset if data_offset is not None else run_data_end
                if data_cursor is None:issues.append(_issue("MP4_FRAGMENT_DATA_OFFSET_INVALID","La secuencia de fragmento no tiene un inicio determinista de datos multimedia.",integrity="DAMAGED",start=trun["start"],end=trun["end"]));complete=False;continue
                run_start_decode=decode_time;run_rows=[]
                for _ in range(count):
                    duration=default_duration;size=default_size
                    if run_flags&0x000100:
                        if cursor+4>trun["end"]:duration=None
                        else:duration=int.from_bytes(data[cursor:cursor+4],"big");cursor+=4
                    if run_flags&0x000200:
                        if cursor+4>trun["end"]:size=None
                        else:size=int.from_bytes(data[cursor:cursor+4],"big");cursor+=4
                    if run_flags&0x000400:cursor+=4
                    if run_flags&0x000800:cursor+=4
                    if not duration or not size or cursor>trun["end"]:complete=False;break
                    byte_end=data_cursor+size;inside=any(start<=data_cursor and byte_end<=end for start,end in mdat_ranges)
                    row={"index":len(rows_by_track[track_id]),"fragment_index":fragment_index,"run_index":run_index,
                        "sample_description_index":description_index,"sample_description_valid":bool(description_index and 1<=description_index<=len(by_id[track_id].get("sample_descriptions") or [])),
                        "byte_start":data_cursor,"byte_end":byte_end,"size":size,"within_mdat":inside,
                        "sha256":hashlib.sha256(data[data_cursor:byte_end]).hexdigest() if inside else None,
                        "decode_time_units":decode_time,"duration_units":duration}
                    rows_by_track[track_id].append(row);run_rows.append(row);data_cursor=byte_end;decode_time+=duration
                run_data_end=data_cursor;facts["runs"].append({"fragment_index":fragment_index,"sequence_number":sequence,"track_id":track_id,"run_index":run_index,
                    "sample_count":len(run_rows),"decode_start_units":run_start_decode,"decode_end_units":decode_time,
                    "byte_start":run_rows[0]["byte_start"] if run_rows else None,"byte_end":run_rows[-1]["byte_end"] if run_rows else None})
    for track_id,rows in rows_by_track.items():
        track=by_id[track_id];continuous=bool(rows and rows[0]["decode_time_units"]==0 and all(a["decode_time_units"]+a["duration_units"]==b["decode_time_units"] for a,b in zip(rows,rows[1:])))
        valid=bool(complete and continuous and all(x["within_mdat"] and x["sha256"] and x["sample_description_valid"] for x in rows))
        track["fragmented_access_units"]={"sample_count":len(rows),"decode_duration_units":rows[-1]["decode_time_units"]+rows[-1]["duration_units"] if rows else None,
            "mapping_complete":valid,"decode_timeline_complete":continuous,"access_units":rows}
        if valid:
            track["access_unit_provenance"]={"policy":"MP4_AAC_FRAGMENTED_ACCESS_UNIT_PROVENANCE_AUDIT_ONLY","sample_count_declared":len(rows),
                "mapped_sample_count":len(rows),"hashed_sample_count":len(rows),"mapping_complete":True,"decode_timeline_complete":True,
                "all_access_units_within_mdat":True,"all_access_units_hashed":True,"decode_time_origin_units":0,
                "decode_end_units":track["fragmented_access_units"]["decode_duration_units"],"access_units":rows}
        elif rows:issues.append(_issue("MP4_FRAGMENT_ACCESS_UNIT_MAPPING_INCOMPLETE","Las secuencias de fragmentos no proporcionan una asignación completa, continua y autenticada de unidades de acceso AAC.",integrity="DAMAGED"))
    facts["mapping_complete"]=bool(rows_by_track and all((by_id[track_id].get("fragmented_access_units") or {}).get("mapping_complete") for track_id in rows_by_track))
    facts["sample_count"]=sum(len(rows) for rows in rows_by_track.values());return facts


def _build_fragmented_presentation(track:dict,movie:dict,issues:list[Issue]):
    fragment=track.get("fragmented_access_units") or {};duration=fragment.get("decode_duration_units");timescale=track.get("media_timescale")
    description=(track.get("sample_descriptions") or [{}])[0];sample_rate=description.get("sample_rate") or (description.get("aac_config") or {}).get("sample_rate")
    result={"policy":"MP4_AAC_FRAGMENTED_PRESENTATION_AUDIT_ONLY","determined":False,"presentation_model":"UNDETERMINED",
        "edit_list_present":track.get("edit_list_present",False),"media_timescale":timescale,"fragmented_media_duration":duration,
        "presentation_segments":[],"intervention_authority":False}
    track["presentation_window"]=result
    if track.get("edit_list_present"):
        if not fragment.get("mapping_complete") or not duration or not timescale or not sample_rate:
            result["reason"]="FRAGMENT_MAPPING_OR_TIMESCALE_INCOMPLETE";return
        _build_presentation_window(track,movie,issues,media_duration_override=duration,fragmented=True)
        track["presentation_window"]["fragmented_media_duration"]=duration
        return
    if not fragment.get("mapping_complete") or not duration or not timescale or not sample_rate:
        result["reason"]="FRAGMENT_MAPPING_OR_TIMESCALE_INCOMPLETE";return
    if duration*sample_rate%timescale:
        issues.append(_issue("MP4_FRAGMENT_PRESENTATION_SAMPLE_COUNT_INEXACT","La duración decodificada del fragmento no puede representarse como una cantidad exacta de muestras PCM.",layer="timeline",integrity="NONCONFORMANT"));result["reason"]="INEXACT_FRAGMENT_PRESENTATION_SAMPLE_COUNT";return
    sample_count=duration*sample_rate//timescale
    result.update({"determined":True,"presentation_model":"FRAGMENTED_NORMAL_RATE_MEDIA_TIMELINE","presentation_sample_count":sample_count,
        "sample_rate":sample_rate,"presentation_segment_count":1,"media_segment_count":1,"empty_segment_count":0,"contains_empty_edits":False,
        "reason":"VALIDATED_FRAGMENTED_MEDIA_TIMELINE_AUDIT_ONLY","presentation_segments":[{"index":0,"kind":"MEDIA",
            "media_start_units":0,"media_end_units":duration,"source_sample_start":0,"source_sample_end":sample_count,
            "presentation_sample_start":0,"presentation_sample_end":sample_count,"presentation_sample_count":sample_count,
            "sample_provenance":"SOURCE_MEDIA_PCM"}]})


def _build_presentation_window(track:dict,movie:dict,issues:list[Issue],media_duration_override=None,fragmented=False):
    edit=track.get("edit_list") or {};media_timescale=track.get("media_timescale")
    media_duration=media_duration_override if media_duration_override is not None else track.get("media_duration")
    result={"policy":"MP4_AAC_FRAGMENTED_EDIT_LIST_PRESENTATION" if fragmented else "MP4_AAC_MULTI_EDIT_PRESENTATION_AUDIT_ONLY","determined":False,
        "movie_timescale":movie.get("timescale"),"movie_duration":movie.get("duration"),"media_timescale":media_timescale,
        "media_duration":media_duration,"edit_list_present":edit.get("present",False),"presentation_model":"UNDETERMINED",
        "presentation_segments":[],"intervention_authority":False}
    track["presentation_window"]=result
    if not movie.get("timescale") or not media_timescale or media_duration is None:
        result["reason"]="MOVIE_OR_MEDIA_TIMESCALE_UNAVAILABLE";return
    entries=edit.get("entries") or []
    if not edit.get("present"):
        entries=[{"segment_duration_movie_units":movie.get("duration"),"media_time":0,"media_rate_integer":1,"media_rate_fraction":0}]
    result["edit_list_entry_count"]=len(entries)
    description=(track.get("sample_descriptions") or [{}])[0];sample_rate=description.get("sample_rate") or (description.get("aac_config") or {}).get("sample_rate")
    if not entries:
        issues.append(_issue("MP4_EDIT_LIST_INVALID","La lista de edición MP4 no contiene entradas de presentación.",layer="timeline",integrity="DAMAGED"));result["reason"]="EMPTY_EDIT_LIST";return
    if not sample_rate:
        result["reason"]="SAMPLE_RATE_UNAVAILABLE";return
    presentation_movie_cursor=0;presentation_sample_cursor=0;segments=[]
    for index,entry in enumerate(entries):
        rate=(entry.get("media_rate_integer"),entry.get("media_rate_fraction"))
        if rate!=(1,0):
            issues.append(_issue("MP4_EDIT_LIST_RATE_UNSUPPORTED","La velocidad multimedia de la lista de edición MP4 no es la velocidad normal compatible de reproducción 1.0.",layer="timeline",integrity="SUSPICIOUS",evidence=[{"edit_index":index,"media_rate_integer":rate[0],"media_rate_fraction":rate[1]}]));result["reason"]="NON_UNIT_MEDIA_RATE";return
        media_start=entry.get("media_time");segment_duration=entry.get("segment_duration_movie_units")
        if media_start is None or media_start<-1 or segment_duration is None or segment_duration<=0:
            issues.append(_issue("MP4_EDIT_LIST_MEDIA_RANGE_INVALID","La edición MP4 debe seleccionar contenido multimedia o un intervalo vacío con duración positiva.",layer="timeline",integrity="DAMAGED",evidence=[{"edit_index":index,"media_time":media_start,"segment_duration_movie_units":segment_duration}]));result["reason"]="INVALID_MEDIA_RANGE";return
        scaled=segment_duration*media_timescale
        if scaled%movie["timescale"]:
            issues.append(_issue("MP4_EDIT_LIST_TIMEBASE_INEXACT","La duración de la edición MP4 no puede representarse exactamente en la escala temporal multimedia.",layer="timeline",integrity="NONCONFORMANT",evidence=[{"edit_index":index}]));result["reason"]="INEXACT_TIMEBASE_CONVERSION";return
        duration_media_units=scaled//movie["timescale"]
        sample_scaled=segment_duration*sample_rate
        if sample_scaled%movie["timescale"]:
            issues.append(_issue("MP4_EDIT_LIST_SAMPLE_COUNT_INEXACT","El segmento de presentación MP4 no puede representarse como una cantidad entera exacta de muestras decodificadas.",layer="timeline",integrity="NONCONFORMANT",evidence=[{"edit_index":index}]));result["reason"]="INEXACT_PRESENTATION_SAMPLE_COUNT";return
        segment_samples=sample_scaled//movie["timescale"]
        segment={"index":index,"kind":"EMPTY" if media_start==-1 else "MEDIA",
            "presentation_start_movie_units":presentation_movie_cursor,
            "presentation_end_movie_units":presentation_movie_cursor+segment_duration,
            "segment_duration_movie_units":segment_duration,"segment_duration_media_units":duration_media_units,
            "presentation_sample_start":presentation_sample_cursor,
            "presentation_sample_end":presentation_sample_cursor+segment_samples,
            "presentation_sample_count":segment_samples}
        if media_start==-1:
            segment["sample_provenance"]="EMPTY_EDIT_SILENCE_NOT_SOURCE_PCM"
        else:
            media_end=media_start+duration_media_units
            if media_end>media_duration:
                issues.append(_issue("MP4_EDIT_LIST_MEDIA_RANGE_INVALID","La edición MP4 selecciona contenido más allá de la duración multimedia declarada.",layer="timeline",integrity="DAMAGED",evidence=[{"edit_index":index,"media_start":media_start,"media_end":media_end,"media_duration":media_duration}]));result["reason"]="MEDIA_RANGE_OUTSIDE_MDHD";return
            start_scaled=media_start*sample_rate;end_scaled=media_end*sample_rate
            if start_scaled%media_timescale or end_scaled%media_timescale:
                issues.append(_issue("MP4_EDIT_LIST_SAMPLE_COUNT_INEXACT","Los límites de la edición multimedia MP4 no pueden representarse como posiciones exactas de muestras decodificadas.",layer="timeline",integrity="NONCONFORMANT",evidence=[{"edit_index":index}]));result["reason"]="INEXACT_MEDIA_SAMPLE_BOUNDARY";return
            segment.update({"media_start_units":media_start,"media_end_units":media_end,
                "source_sample_start":start_scaled//media_timescale,"source_sample_end":end_scaled//media_timescale,
                "sample_provenance":"SOURCE_MEDIA_PCM"})
        segments.append(segment);presentation_movie_cursor+=segment_duration;presentation_sample_cursor+=segment_samples
    if movie.get("duration")!=presentation_movie_cursor:
        issues.append(_issue("MP4_MOVIE_DURATION_MISMATCH","La duración de la película MP4 difiere de la suma de duraciones de la lista de edición de audio.",layer="timeline",integrity="NONCONFORMANT",evidence=[{"movie_duration":movie.get("duration"),"edit_duration_sum":presentation_movie_cursor,"movie_timescale":movie.get("timescale")}]))
        result["reason"]="MOVIE_DURATION_DISAGREES_WITH_EDIT" if len(entries)==1 else "MOVIE_DURATION_DISAGREES_WITH_EDITS";return
    simple=bool(len(segments)==1 and segments[0]["kind"]=="MEDIA")
    presentation_model=("FRAGMENTED_SINGLE_NORMAL_RATE_MEDIA_EDIT" if simple else "FRAGMENTED_MULTI_EDIT_PRESENTATION") if fragmented else ("SINGLE_NORMAL_RATE_MEDIA_EDIT" if simple else "MULTI_EDIT_PRESENTATION")
    reason=("VALIDATED_FRAGMENTED_SINGLE_MEDIA_EDIT" if simple else "VALIDATED_FRAGMENTED_MULTI_EDIT_PRESENTATION") if fragmented else ("VALIDATED_SINGLE_MEDIA_EDIT" if simple else "VALIDATED_MULTI_EDIT_PRESENTATION_AUDIT_ONLY")
    result.update({"determined":True,"presentation_model":presentation_model,
        "presentation_segments":segments,"presentation_segment_count":len(segments),
        "media_segment_count":sum(x["kind"]=="MEDIA" for x in segments),"empty_segment_count":sum(x["kind"]=="EMPTY" for x in segments),
        "contains_empty_edits":any(x["kind"]=="EMPTY" for x in segments),"presentation_duration_movie_units":presentation_movie_cursor,
        "presentation_sample_count":presentation_sample_cursor,"sample_rate":sample_rate,
        "reason":reason})
    if simple:
        segment=segments[0];media_start=segment["media_start_units"];media_end=segment["media_end_units"]
        result.update({"media_start_units":media_start,"media_end_units":media_end,
            "presentation_duration_media_units":segment["segment_duration_media_units"],"initial_media_trim_units":media_start,
            "trailing_media_trim_units":media_duration-media_end})


def analyze(path:Path):
    data=path.read_bytes();issues=[];structural=[]
    top=_parse_range(data,0,len(data),"file",0,issues,structural)
    ftyp=next((x for x in top if x["kind"]==b"ftyp"),None);moovs=[x for x in top if x["kind"]==b"moov"];mdats=[x for x in top if x["kind"]==b"mdat"];fragmented=any(x["kind"]==b"moof" for x in top)
    if not ftyp:issues.append(_issue("MP4_FTYP_MISSING","Falta el box de tipo de archivo ISO BMFF.",integrity="DAMAGED",playability="BLOCKING"))
    if not moovs:issues.append(_issue("MP4_MOOV_MISSING","Falta el box de película MP4.",integrity="DAMAGED",playability="BLOCKING"))
    if not mdats:issues.append(_issue("MP4_MDAT_MISSING","Falta el box de datos multimedia MP4.",integrity="DAMAGED",playability="BLOCKING"))
    ftyp_facts={}
    if ftyp and ftyp["end"]-ftyp["payload_start"]>=8:
        p=ftyp["payload_start"];payload=data[p:ftyp["end"]]
        ftyp_facts={"major_brand":payload[:4].decode("latin-1"),"minor_version":int.from_bytes(payload[4:8],"big"),"compatible_brands":[payload[i:i+4].decode("latin-1") for i in range(8,len(payload)-3,4)]}
    mdat_ranges=[(x["payload_start"],x["end"]) for x in mdats]
    movie_headers=[_parse_movie_header(data,moov,issues) for moov in moovs];movie=movie_headers[0] if len(movie_headers)==1 else {}
    tracks=[]
    for moov in moovs:
        for trak in _children(moov,b"trak"):tracks.append(_parse_track(data,trak,mdat_ranges,issues,fragmented))
    fragment_facts=_parse_fragments(data,top,moovs,tracks,mdat_ranges,issues) if fragmented else {"fragmented":False,"fragment_count":0}
    for track in tracks:
        if fragmented:_build_fragmented_presentation(track,movie,issues)
        else:_build_presentation_window(track,movie,issues)
    audio_tracks=[x for x in tracks if x.get("handler_type")=="soun"]
    aac_tracks=[x for x in audio_tracks if x["sample_descriptions"] and all(d.get("valid") for d in x["sample_descriptions"])]
    if audio_tracks and len(aac_tracks)!=len(audio_tracks):issues.append(_issue("MP4_AAC_DECODER_CONFIG_INVALID","Una pista de audio MP4 carece de una descripción de muestra AAC y una configuración de decodificador autenticadas.",layer="codec_header",integrity="DAMAGED",playability="BLOCKING"))
    configs=[d["aac_config"] for t in aac_tracks for d in t["sample_descriptions"] if d.get("aac_config")]
    identification={"audio_track_count":len(audio_tracks),"aac_track_count":len(aac_tracks),"aac_sample_description_count":len(configs),"aac_configs":configs,
        "single_audio_track_required":True,"supported":bool(len(audio_tracks)==1 and len(aac_tracks)==1)}
    facts={"mp4":{"file_size":len(data),"top_level_boxes":[{"type":x["kind"].decode("latin-1"),"byte_start":x["start"],"byte_end":x["end"],"size":x["size"]} for x in top],"ftyp":ftyp_facts,"moov_count":len(moovs),"mdat_count":len(mdats),"mdat_payload_ranges":[{"byte_start":a,"byte_end":b} for a,b in mdat_ranges],"movie_header":movie,"fragmented":fragmented},"fragmented_mp4":fragment_facts,"tracks":tracks,"aac":{"audio_track_count":len(audio_tracks),"aac_track_count":len(aac_tracks),"profiles":sorted({x["profile_name"] for x in configs}),"audio_object_types":sorted({x["audio_object_type"] for x in configs}),"sample_rates_hz":sorted({x["sample_rate"] for x in configs if x.get("sample_rate")}),"channel_configurations":sorted({x["channel_configuration"] for x in configs if x.get("channel_configuration") is not None})},"identification":identification,"authority":"MP4_AAC_FRAGMENTED_MP4_AUDIT_ONLY_NO_REPAIR_OR_RECOVERY" if fragmented else "MP4_AAC_SIMPLE_EDIT_LIST_PRESENTATION_AUDIT_ONLY_NO_REPAIR_OR_RECOVERY"}
    first=aac_tracks[0]["sample_descriptions"][0] if aac_tracks else {}
    metadata={"sample_rate":first.get("sample_rate") or (first.get("aac_config") or {}).get("sample_rate"),"channels":first.get("channels"),"sample_size_bits":first.get("sample_size_bits")}
    return {"codec":"aac" if identification["supported"] else "mp4_unknown","metadata":metadata,"facts":facts,"structural_map":structural,"issues":issues}
