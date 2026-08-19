from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

from app.external import decode,decode_to_raw_file


POLICY="MP4_AAC_MULTI_EDIT_SEGMENT_PROVENANCE_AUDIT_ONLY"
FRAGMENTED_POLICY="MP4_AAC_FRAGMENTED_PRESENTATION_PCM_PROVENANCE_AUDIT_ONLY"
FRAGMENTED_EDIT_POLICY="MP4_AAC_FRAGMENTED_EDIT_LIST_PCM_PROVENANCE"


def _result(reason:str,policy=POLICY,**evidence):
    return {"policy":policy,"applicable":False,"validated":False,"segment_level_provenance_validated":False,
        "intervention_authority":False,"repair_authority":"NONE","pcm_recovery_authority":"NONE","reason":reason,**evidence}


def _adts_header(payload_size:int,audio_object_type:int,frequency_index:int,channel_configuration:int):
    frame_length=payload_size+7;profile=audio_object_type-1
    return bytes((0xff,0xf1,(profile<<6)|(frequency_index<<2)|(channel_configuration>>2),
        ((channel_configuration&3)<<6)|(frame_length>>11),(frame_length>>3)&0xff,
        ((frame_length&7)<<5)|0x1f,0xfc))


def _hash_raw_range(path:Path,start:int,length:int,aggregate=None,chunk_size=1024*1024):
    digest=hashlib.sha256();remaining=length
    with path.open("rb") as source:
        source.seek(start)
        while remaining:
            block=source.read(min(chunk_size,remaining))
            if not block:raise RuntimeError("PCM_RANGE_SHORT_READ")
            digest.update(block)
            if aggregate is not None:aggregate.update(block)
            remaining-=len(block)
    return digest.hexdigest()


def _hash_silence(length:int,aggregate=None,chunk_size=1024*1024):
    digest=hashlib.sha256();remaining=length;zero=b"\0"*min(chunk_size,max(1,length))
    while remaining:
        block=zero[:min(len(zero),remaining)];digest.update(block)
        if aggregate is not None:aggregate.update(block)
        remaining-=len(block)
    return digest.hexdigest()


def audit(path:Path,track:dict,ffmpeg:str,timeout=300):
    window=track.get("presentation_window") or {}
    model=window.get("presentation_model");fragmented=model in ("FRAGMENTED_NORMAL_RATE_MEDIA_TIMELINE","FRAGMENTED_SINGLE_NORMAL_RATE_MEDIA_EDIT","FRAGMENTED_MULTI_EDIT_PRESENTATION")
    policy=FRAGMENTED_EDIT_POLICY if model in ("FRAGMENTED_SINGLE_NORMAL_RATE_MEDIA_EDIT","FRAGMENTED_MULTI_EDIT_PRESENTATION") else (FRAGMENTED_POLICY if fragmented else POLICY)
    def blocked(reason,**evidence):return _result(reason,policy=policy,**evidence)
    accepted_models=("MULTI_EDIT_PRESENTATION","FRAGMENTED_NORMAL_RATE_MEDIA_TIMELINE","FRAGMENTED_SINGLE_NORMAL_RATE_MEDIA_EDIT","FRAGMENTED_MULTI_EDIT_PRESENTATION")
    if window.get("edit_list_entry_count",0)<=1 and model not in accepted_models:return blocked("NOT_A_COMPLEX_OR_FRAGMENTED_PRESENTATION")
    if model not in accepted_models:return blocked("MULTI_EDIT_PRESENTATION_NOT_STRUCTURALLY_DETERMINED",applicable=True)
    if not window.get("determined"):return blocked("PRESENTATION_NOT_STRUCTURALLY_DETERMINED",applicable=True)
    descriptions=track.get("sample_descriptions") or [];provenance=track.get("access_unit_provenance") or {};units=provenance.get("access_units") or []
    if len(descriptions)!=1 or not descriptions[0].get("valid"):return blocked("EXACTLY_ONE_AUTHENTICATED_AAC_DESCRIPTION_REQUIRED",applicable=True)
    description=descriptions[0];config=description.get("aac_config") or {};channels=description.get("channels")
    frequency_index=config.get("sampling_frequency_index");channel_configuration=config.get("channel_configuration")
    if config.get("audio_object_type")!=2 or frequency_index not in range(13) or channel_configuration not in range(1,8) or channels!=channel_configuration:
        return blocked("TEMPORARY_DECODE_VIEW_REQUIRES_AAC_LC_STANDARD_RATE_AND_CHANNEL_CONFIG",applicable=True)
    if not provenance.get("mapping_complete") or not provenance.get("decode_timeline_complete") or not provenance.get("all_access_units_hashed") or not units:
        return blocked("COMPLETE_ACCESS_UNIT_BYTE_AND_TIMELINE_PROVENANCE_REQUIRED",applicable=True)
    if any(unit.get("sample_description_index")!=1 or unit.get("duration_units") is None for unit in units):
        return blocked("UNIFORM_AUTHENTICATED_ACCESS_UNIT_DESCRIPTION_AND_DURATION_REQUIRED",applicable=True)
    frame_bytes=channels*4;essence=hashlib.sha256()
    try:
        with tempfile.TemporaryDirectory(prefix="lossydoctor-mp4-segment-provenance-") as directory:
            temporary=Path(directory);adts=temporary/"media.aac";raw=temporary/"media.s32le"
            with path.open("rb") as source,adts.open("xb") as output:
                for unit in units:
                    source.seek(unit["byte_start"]);payload=source.read(unit["size"])
                    if len(payload)!=unit["size"]:return blocked("AAC_ACCESS_UNIT_SHORT_READ",applicable=True)
                    if hashlib.sha256(payload).hexdigest()!=unit.get("sha256"):return blocked("AAC_ACCESS_UNIT_HASH_CHANGED",applicable=True)
                    essence.update(payload);output.write(_adts_header(len(payload),2,frequency_index,channel_configuration));output.write(payload)
            strict=decode(adts,ffmpeg,"STRICT_DECODE",timeout)
            if not strict.get("passed"):return blocked("TEMPORARY_AAC_STRICT_DECODE_DID_NOT_PASS",applicable=True)
            decoded=decode_to_raw_file(adts,raw,ffmpeg,timeout)
            if not decoded.get("passed") or not raw.exists():return blocked("TEMPORARY_AAC_DECODE_DID_NOT_COMPLETE",applicable=True)
            raw_bytes=raw.stat().st_size
            if raw_bytes%frame_bytes:return blocked("TEMPORARY_PCM_NOT_SAMPLE_ALIGNED",applicable=True)
            decoded_samples=raw_bytes//frame_bytes
            if decoded_samples!=len(units)*1024:return blocked("TEMPORARY_DECODE_SAMPLE_GEOMETRY_DISAGREES_WITH_AAC_LC_FRAMES",applicable=True,decoded_sample_count=decoded_samples)
            canonical=hashlib.sha256();segments=[]
            for segment in window.get("presentation_segments") or []:
                row={key:segment.get(key) for key in ("index","kind","presentation_sample_start","presentation_sample_end","presentation_sample_count","sample_provenance")}
                pcm_bytes=segment["presentation_sample_count"]*frame_bytes
                if segment["kind"]=="EMPTY":
                    row.update({"pcm_sha256":_hash_silence(pcm_bytes,canonical),"source_access_unit_indices":[],"source_access_unit_count":0,
                        "provenance":"EXPLICIT_TIMELINE_SILENCE_NOT_SOURCE_PCM"})
                else:
                    sample_start=segment["source_sample_start"];sample_end=segment["source_sample_end"]
                    if sample_start<0 or sample_end<=sample_start or sample_end>decoded_samples:return blocked("MEDIA_SEGMENT_OUTSIDE_INDEPENDENT_DECODE",applicable=True)
                    selected=[unit for unit in units if unit["decode_time_units"]<segment["media_end_units"] and unit["decode_time_units"]+unit["duration_units"]>segment["media_start_units"]]
                    if not selected:return blocked("MEDIA_SEGMENT_HAS_NO_ACCESS_UNIT_COVERAGE",applicable=True)
                    coverage_start=selected[0]["decode_time_units"];coverage_end=selected[-1]["decode_time_units"]+selected[-1]["duration_units"]
                    if coverage_start>segment["media_start_units"] or coverage_end<segment["media_end_units"]:
                        return blocked("MEDIA_SEGMENT_ACCESS_UNIT_COVERAGE_INCOMPLETE",applicable=True)
                    selected_essence=hashlib.sha256()
                    with path.open("rb") as source:
                        for unit in selected:
                            source.seek(unit["byte_start"]);payload=source.read(unit["size"])
                            if len(payload)!=unit["size"]:return blocked("SELECTED_ACCESS_UNIT_SHORT_READ",applicable=True)
                            selected_essence.update(payload)
                    row.update({"pcm_sha256":_hash_raw_range(raw,sample_start*frame_bytes,pcm_bytes,canonical),
                        "source_sample_start":sample_start,"source_sample_end":sample_end,
                        "source_access_unit_indices":[unit["index"] for unit in selected],"source_access_unit_count":len(selected),
                        "source_access_unit_first_index":selected[0]["index"],"source_access_unit_last_index":selected[-1]["index"],
                        "source_access_unit_essence_sha256":selected_essence.hexdigest(),
                        "leading_access_unit_trim_media_units":segment["media_start_units"]-coverage_start,
                        "trailing_access_unit_trim_media_units":coverage_end-segment["media_end_units"],
                        "provenance":"AUTHENTICATED_AAC_ACCESS_UNITS_AND_INDEPENDENT_PCM_RANGE"})
                segments.append(row)
            presented_media_end=max((x.get("source_sample_end",0) for x in window.get("presentation_segments") or [] if x.get("kind")=="MEDIA"),default=0)
            return {"policy":policy,"applicable":True,"validated":True,"segment_level_provenance_validated":True,
                "intervention_authority":False,"repair_authority":"NONE","pcm_recovery_authority":"NONE",
                "reason":"FRAGMENTED_PRESENTATION_HAS_EXACT_PCM_PROVENANCE_AUDIT_ONLY" if fragmented else "ALL_PRESENTATION_SEGMENTS_HAVE_EXACT_PROVENANCE_AUDIT_ONLY","temporary_transport":"ADTS_HEADERS_SYNTHESIZED_AAC_PAYLOAD_BYTES_UNCHANGED",
                "temporary_strict_decode":"PASS","decoded_media_sample_count":decoded_samples,"presentation_sample_count":window.get("presentation_sample_count"),
                "decoded_tail_padding_samples_excluded":decoded_samples-presented_media_end if fragmented else None,
                "aac_access_unit_count":len(units),"aac_access_unit_essence_sha256":essence.hexdigest(),
                "canonical_presentation_pcm_s32le_sha256":canonical.hexdigest(),"segments":segments}
    except Exception as exc:return blocked(f"SEGMENT_PROVENANCE_ERROR_{type(exc).__name__}",applicable=True)
