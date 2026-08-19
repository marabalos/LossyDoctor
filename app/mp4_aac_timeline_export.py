from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

from app.external import canonical_pcm_profile,decode,decode_to_raw_file,raw_file_to_flac
from app.mp4_aac_timeline import _adts_header
from app.publication import publish_or_preview_with_manifest
from app.utils import sha256_file
from app.version import APP_VERSION


POLICY="MP4_AAC_CANONICAL_TIMELINE_LOSSLESS_EXPORT"
DERIVATION_KIND="RECOVERED_LOSSLESS"
MATERIALIZATION="MP4_AAC_CANONICAL_COMPLEX_PRESENTATION_FROM_AUTHENTICATED_ACCESS_UNITS"


def assess(timeline:dict,decoder:dict):
    base={"policy":POLICY,"authority":"NO_PUBLICATION_AUTHORITY","eligible":False,"publication_enabled":False,
        "repair_authority":"NONE","pcm_recovery_authority":"NONE","pcm_class":"NOT_REQUIRED","reason":"CANONICAL_TIMELINE_EXPORT_NOT_REQUIRED"}
    if not timeline.get("segment_level_provenance_validated") or not timeline.get("canonical_presentation_pcm_s32le_sha256"):
        return {**base,"pcm_class":"POLICY_BLOCKED","reason":"EXACT_CANONICAL_TIMELINE_PCM_PROVENANCE_REQUIRED"}
    if not decoder.get("completed") or not decoder.get("pcm_sha256") or decoder.get("sample_frames") is None:
        return {**base,"pcm_class":"POLICY_BLOCKED","reason":"DIRECT_PRESENTATION_DECODER_EVIDENCE_REQUIRED"}
    canonical_hash=timeline["canonical_presentation_pcm_s32le_sha256"];canonical_samples=timeline.get("presentation_sample_count")
    differs=bool(decoder.get("pcm_sha256")!=canonical_hash or decoder.get("sample_frames")!=canonical_samples)
    if not differs:return {**base,"reason":"DIRECT_PRESENTATION_ALREADY_EQUALS_PROVEN_CANONICAL_PCM"}
    return {**base,"authority":"CANONICAL_TIMELINE_LOSSLESS_EXPORT","eligible":True,"publication_enabled":True,
        "pcm_recovery_authority":"COMPLETE_CLEAN_CANONICAL_TIMELINE","pcm_class":"COMPLETE_CLEAN_CANONICAL_TIMELINE",
        "reason":"DIRECT_PRESENTATION_PCM_DIFFERS_FROM_PROVEN_CANONICAL_TIMELINE","presentation_sample_count":canonical_samples,
        "canonical_presentation_pcm_s32le_sha256":canonical_hash,"direct_decoder_sample_count":decoder.get("sample_frames"),
        "direct_decoder_pcm_s32le_sha256":decoder.get("pcm_sha256")}


def _manifest_matches(manifest:dict,source_sha:str,assessment:dict):
    return (manifest.get("producer")=="LossyDoctor" and manifest.get("producer_version")==APP_VERSION and
        manifest.get("derivation_kind")==DERIVATION_KIND and manifest.get("materialization")==MATERIALIZATION and
        manifest.get("recovery_policy")==POLICY and manifest.get("validation_result")=="PASS" and
        manifest.get("source_sha256")==source_sha and manifest.get("sample_count")==assessment.get("presentation_sample_count") and
        manifest.get("source_canonical_pcm_sha256")==assessment.get("canonical_presentation_pcm_s32le_sha256"))


def _reuse(source:Path,source_sha:str,assessment:dict):
    for sidecar in source.parent.glob("*.lossydoctor-manifest.json"):
        try:manifest=json.loads(sidecar.read_text(encoding="utf-8"))
        except Exception:continue
        if not _manifest_matches(manifest,source_sha,assessment):continue
        output=Path(manifest.get("output_path",""))
        try:local=output.resolve().parent==source.resolve().parent and sidecar.resolve()==Path(str(output)+".lossydoctor-manifest.json").resolve()
        except Exception:local=False
        if local and output.exists() and sha256_file(output)==manifest.get("output_sha256"):
            return {"status":"REUSED","output_path":str(output),"manifest_path":str(sidecar),"manifest":manifest}
    return None


def _copy_bytes(source,target,start,length,chunk_size=1024*1024):
    source.seek(start);remaining=length
    while remaining:
        block=source.read(min(chunk_size,remaining))
        if not block:raise RuntimeError("PCM_RANGE_SHORT_READ")
        target.write(block);remaining-=len(block)


def _materialize(source:Path,track:dict,ffmpeg:str,temporary:Path,timeout:int):
    descriptions=track.get("sample_descriptions") or [];window=track.get("presentation_window") or {};provenance=track.get("access_unit_provenance") or {};units=provenance.get("access_units") or []
    if len(descriptions)!=1 or not units:return {"passed":False,"reason":"SOURCE_PROVENANCE_CHANGED"}
    description=descriptions[0];config=description.get("aac_config") or {};channels=description.get("channels");sample_rate=description.get("sample_rate") or config.get("sample_rate")
    frequency_index=config.get("sampling_frequency_index");channel_configuration=config.get("channel_configuration");adts=temporary/"media.aac";decoded_raw=temporary/"media.s32le"
    essence=hashlib.sha256()
    with source.open("rb") as inp,adts.open("xb") as out:
        for unit in units:
            inp.seek(unit["byte_start"]);payload=inp.read(unit["size"])
            if len(payload)!=unit["size"] or hashlib.sha256(payload).hexdigest()!=unit.get("sha256"):return {"passed":False,"reason":"AAC_ACCESS_UNIT_IDENTITY_CHANGED"}
            essence.update(payload);out.write(_adts_header(len(payload),2,frequency_index,channel_configuration));out.write(payload)
    strict=decode(adts,ffmpeg,"STRICT_DECODE",timeout)
    if not strict.get("passed"):return {"passed":False,"reason":"TEMPORARY_AAC_STRICT_DECODE_FAILED"}
    decoded=decode_to_raw_file(adts,decoded_raw,ffmpeg,timeout)
    if not decoded.get("passed") or not decoded_raw.exists():return {"passed":False,"reason":"TEMPORARY_AAC_PCM_DECODE_FAILED"}
    frame_bytes=channels*4;decoded_bytes=decoded_raw.stat().st_size
    if decoded_bytes%frame_bytes:return {"passed":False,"reason":"TEMPORARY_PCM_NOT_SAMPLE_ALIGNED"}
    canonical=temporary/"canonical.s32le";segment_rows=[]
    with decoded_raw.open("rb") as inp,canonical.open("xb") as out:
        for segment in window.get("presentation_segments") or []:
            count=segment.get("presentation_sample_count");kind=segment.get("kind");digest=hashlib.sha256()
            if not count or kind not in ("MEDIA","EMPTY"):return {"passed":False,"reason":"PRESENTATION_SEGMENT_GEOMETRY_CHANGED"}
            if kind=="MEDIA":
                start=segment.get("source_sample_start");end=segment.get("source_sample_end")
                if start is None or end-start!=count or end*frame_bytes>decoded_bytes:return {"passed":False,"reason":"MEDIA_SEGMENT_OUTSIDE_DECODED_PCM"}
                remaining=count*frame_bytes;inp.seek(start*frame_bytes)
                while remaining:
                    block=inp.read(min(1024*1024,remaining))
                    if not block:return {"passed":False,"reason":"PCM_RANGE_SHORT_READ"}
                    digest.update(block);out.write(block);remaining-=len(block)
            else:
                remaining=count*frame_bytes;zero=b"\0"*min(1024*1024,remaining)
                while remaining:
                    block=zero[:min(len(zero),remaining)];digest.update(block);out.write(block);remaining-=len(block)
            segment_rows.append({"index":segment.get("index"),"kind":kind,"presentation_sample_start":segment.get("presentation_sample_start"),
                "presentation_sample_end":segment.get("presentation_sample_end"),"sample_count":count,"pcm_sha256":digest.hexdigest(),
                "source_sample_start":segment.get("source_sample_start"),"source_sample_end":segment.get("source_sample_end"),
                "provenance":"AUTHENTICATED_AAC_SOURCE_PCM" if kind=="MEDIA" else "SYNTHESIZED_EDIT_LIST_ZERO_SILENCE"})
    return {"passed":True,"raw_path":canonical,"sample_rate":sample_rate,"channels":channels,"sample_count":canonical.stat().st_size//frame_bytes,
        "pcm_sha256":sha256_file(canonical),"aac_access_unit_count":len(units),"aac_access_unit_essence_sha256":essence.hexdigest(),"segments":segment_rows}


def export(source:Path,source_sha:str,track:dict,ffmpeg:str,timeline:dict,assessment:dict,publish=True,timeout=300):
    if not assessment.get("eligible"):return {"status":"NOT_ELIGIBLE","assessment":assessment,"outputs":[]}
    reused=_reuse(source,source_sha,assessment)
    if reused:return {"status":"REUSED","assessment":assessment,"outputs":[reused]}
    if sha256_file(source)!=source_sha:return {"status":"REJECTED","reason":"la fuente cambió antes de la exportación","assessment":assessment,"outputs":[]}
    with tempfile.TemporaryDirectory(prefix="lossydoctor-mp4-canonical-export-") as directory:
        temporary=Path(directory);candidate=_materialize(source,track,ffmpeg,temporary,timeout)
        if not candidate.get("passed"):return {"status":"REJECTED","reason":candidate.get("reason"),"assessment":assessment,"outputs":[]}
        if candidate["sample_count"]!=assessment.get("presentation_sample_count") or candidate["pcm_sha256"]!=assessment.get("canonical_presentation_pcm_s32le_sha256") or candidate["aac_access_unit_essence_sha256"]!=timeline.get("aac_access_unit_essence_sha256"):
            return {"status":"REJECTED","reason":"la evidencia canónica cambió desde la evaluación","assessment":assessment,"outputs":[]}
        flac=temporary/"candidate.flac";encoded=raw_file_to_flac(candidate["raw_path"],flac,ffmpeg,candidate["sample_rate"],candidate["channels"],timeout)
        if not encoded.get("passed"):return {"status":"REJECTED","reason":"falló la codificación FLAC","assessment":assessment,"outputs":[]}
        back=temporary/"back.s32le";decoded=decode_to_raw_file(flac,back,ffmpeg,timeout)
        if not decoded.get("passed") or back.stat().st_size!=candidate["raw_path"].stat().st_size or sha256_file(back)!=candidate["pcm_sha256"]:
            return {"status":"REJECTED","reason":"la verificación round-trip del PCM FLAC no coincide","assessment":assessment,"outputs":[]}
        if sha256_file(source)!=source_sha:return {"status":"REJECTED","reason":"la fuente cambió durante la exportación","assessment":assessment,"outputs":[]}
        desired=source.with_name(source.stem+" [canonical-lossless].flac");output=flac
        silence=[{"segment_index":x["index"],"presentation_sample_start":x["presentation_sample_start"],"presentation_sample_end":x["presentation_sample_end"],"sample_count":x["sample_count"]} for x in candidate["segments"] if x["kind"]=="EMPTY"]
        manifest={"schema_version":3,"producer":"LossyDoctor","producer_version":APP_VERSION,"derivation_schema":1,
            "derivation_kind":DERIVATION_KIND,"materialization":MATERIALIZATION,"recovery_policy":POLICY,
            "source_pcm_recovery_class":"COMPLETE_CLEAN_CANONICAL_TIMELINE","trigger":"DIRECT_PRESENTATION_PCM_DIFFERS_FROM_PROVEN_CANONICAL_TIMELINE",
            "source_path":str(source),"source_sha256":source_sha,"output_path":str(output),"output_sha256":sha256_file(output),
            "canonical_pcm_profile":canonical_pcm_profile(ffmpeg,candidate["sample_rate"],candidate["channels"]),"presentation_model":(track.get("presentation_window") or {}).get("presentation_model"),
            "sample_count":candidate["sample_count"],"source_canonical_pcm_sha256":candidate["pcm_sha256"],"flac_canonical_pcm_sha256":sha256_file(back),"flac_decoded_pcm_sha256":sha256_file(back),
            "direct_decoder_sample_count":assessment.get("direct_decoder_sample_count"),"direct_decoder_pcm_s32le_sha256":assessment.get("direct_decoder_pcm_s32le_sha256"),
            "aac_access_unit_count":candidate["aac_access_unit_count"],"aac_access_unit_essence_sha256":candidate["aac_access_unit_essence_sha256"],"aac_access_unit_bytes_modified":False,
            "segments":candidate["segments"],"synthesized_gap_silence":silence,"resampling":"NONE","channel_remix":"NONE",
            "validation_result":"PASS","source_modified":False,"audio_recoding":"LOSSLESS_FLAC_ONLY"}
        output,sidecar,manifest,publication_status=publish_or_preview_with_manifest(flac,desired,manifest,publish)
        return {"status":publication_status,"assessment":assessment,"outputs":[{"status":publication_status,"output_path":str(output) if output is not None else None,"manifest_path":str(sidecar) if sidecar else None,"manifest":manifest}]}
