from __future__ import annotations

from pathlib import Path

from formats import aac_adts, asf_wma, mp4_aac, mpeg, ogg_opus, ogg_vorbis


NONAUDIO = [
    (b"\xff\xd8\xff", "JPEG"),
    (b"\x89PNG\r\n\x1a\n", "PNG"),
    (b"GIF8", "GIF"),
    (b"%PDF", "PDF"),
    (b"PK\x03\x04", "ZIP"),
    (b"MZ", "PE"),
]

def _degraded_mp4_structure(head:bytes):
    off=0; kinds=[]
    while off+8<=len(head):
        size=int.from_bytes(head[off:off+4],'big');kind=head[off+4:off+8]
        if size<8 or off+size>len(head):break
        kinds.append(kind);off+=size
    return b'moov' in kinds and b'mdat' in kinds

def _degraded_asf_structure(head:bytes):
    return len(head)>=54 and 30<=int.from_bytes(head[16:24],'little')<=len(head) and head[28:30]==b'\x01\x02' and head[30:46] in (bytes.fromhex('a1dcab8c47a9cf118ee400c00c205365'),bytes.fromhex('9107dcb7b7a9cf118ee600c00c205365'))


def identify(path:Path,max_scan=262144):
    try:head=path.read_bytes()[:65536]
    except Exception:return {"supported":False,"reason":"error de lectura"}
    for signature,name in NONAUDIO:
        if head.startswith(signature):return {"supported":False,"reason":f"strong non-audio magic: {name}"}
    ogg_at=head.find(b"OggS")
    if ogg_at>=0:
        # La recaptura vigente admite un prefijo acotado, pero un marcador de codec
        # nunca basta sin confirmación del parser profundo de Ogg.
        ogg_head=head[ogg_at:]
        if b"OpusHead" in ogg_head or b"OpusTags" in ogg_head:
            q=ogg_opus.analyze(path)
            if q.get("codec")=="opus":return {"supported":True,"container":"OGG","codec":"opus","confidence":"HIGH","ogg_opus":q,"first_capture_offset":ogg_at}
        if b"\x01vorbis" in ogg_head or (b"\x03vorbis" in ogg_head and b"\x05vorbis" in ogg_head):
            q=ogg_vorbis.analyze(path)
            if q.get("codec")=="vorbis":return {"supported":True,"container":"OGG","codec":"vorbis","confidence":"HIGH","ogg_vorbis":q,"first_capture_offset":ogg_at}
        return {"supported":False,"container":"OGG","codec":None,"confidence":"HIGH","reason":"el codec Ogg no es compatible o no pudo confirmarse estructuralmente"}
    if aac_adts.looks_like_adts(head):
        q=aac_adts.analyze(path,max_scan);return {"supported":True,"container":"AAC_ADTS","codec":"aac","confidence":"HIGH","aac_adts":q}
    if len(head)>=12 and (head[4:8]==b"ftyp" or _degraded_mp4_structure(head)):
        q=mp4_aac.analyze(path);ident=q["facts"]["identification"]
        # El modelo vigente autentica la pista AAC y audita la estructura MP4 básica, pero
        # conserva confianza MEDIUM hasta contar con la auditoría completa de muestras y línea temporal.
        if ident["supported"]:return {"supported":True,"container":"MP4","codec":"aac","confidence":"MEDIUM","mp4_aac":q,"mp4_aac_identification":ident}
        if ident.get("audio_track_count",0)>1:
            return {"supported":False,"container":"MP4","codec":None,"confidence":"HIGH","reason":"MP4 con varias pistas de audio es incompatible con LossyDoctor V1"}
        return {"supported":False,"container":"MP4","codec":None,"confidence":"HIGH","reason":"la pista de audio AAC en MP4 no pudo confirmarse estructuralmente"}
    if head.startswith(bytes.fromhex("3026b2758e66cf11a6d900aa0062ce6c")) or _degraded_asf_structure(head):
        q=asf_wma.analyze(path);return {"supported":True,"container":"ASF","codec":"wma","confidence":"HIGH","asf_wma":q}
    result=mpeg.analyze(path,max_scan)
    if result.get("codec") in ("mp3","mp2"):
        facts=result.get("facts",{})
        if facts.get("free_format") and (facts.get("audio_frame_count_observed",0)<12 or facts.get("audio_coverage_ratio",0)<0.80):
            return {"supported":False,"reason":"el candidato MPEG free-format carece de cobertura estructural dominante"}
        return {"supported":True,"container":"MPEG_AUDIO","codec":result["codec"],"confidence":"HIGH","mpeg":result}
    return {"supported":False,"reason":"contenido no soportado"}
