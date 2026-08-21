from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from pathlib import Path

from app import aac_adts_timeline
from app.external import aac_adts_demux_evidence,decode,ffmpeg_evidence_decode
from app.publication import publish_or_preview_with_manifest
from app.utils import sha256_file
from app.version import APP_VERSION
from formats.aac_adts import _header,analyze


SPEC_ID="AAC_ADTS_REWRITE_UNIQUE_INVALID_SAMPLING_INDEX"
INTERFRAME_RECAPTURE_SPEC_ID="AAC_ADTS_REMOVE_UNAMBIGUOUS_INTERFRAME_NONFRAME_BYTES"
INTERFRAME_RECAPTURE_POLICY="AAC_ADTS_SAFE_INTERFRAME_RECAPTURE"


def plan(source:Path,parsed:dict):
    if source.suffix.lower() not in (".aac",".adts"):return None
    if [issue.code for issue in parsed.get("issues",[])]!=["AAC_ADTS_SAMPLING_INDEX_INVALID"]:return None
    frames=(parsed.get("facts") or {}).get("frames") or [];invalid=[row for row in parsed.get("structural_map",[]) if row.get("type")=="INVALID_ADTS_FRAME" and row.get("reason")=="SAMPLING_INDEX_INVALID"]
    if len(invalid)!=1 or len(frames)<2:return None
    signatures={(row.get("mpeg_id"),row.get("object_type"),row.get("sampling_frequency_index"),row.get("channel_configuration"),row.get("raw_data_blocks")) for row in frames}
    if len(signatures)!=1:return None
    _,object_type,target,channel_configuration,raw_blocks=next(iter(signatures))
    if object_type!=2 or target not in range(13) or channel_configuration not in range(1,8) or raw_blocks!=1:return None
    data=source.read_bytes();start=invalid[0]["byte_start"];header=_header(data,start)
    if header.get("reason")!="SAMPLING_INDEX_INVALID" or start+header.get("frame_length",0)!=invalid[0]["byte_end"]:return None
    field=start+2;original=data[field];replacement=(original&~0x3c)|(target<<2)
    if replacement==original:return None
    return {"spec":{"id":SPEC_ID},"status":"ELIGIBLE","reason":"un índice de muestreo reservado tiene un único reemplazo unánime para el stream AAC-LC",
        "byte_start":field,"byte_end":field+1,"original_value":original,"replacement_value":replacement,
        "sampling_frequency_index":target,"sample_rate":frames[0]["sample_rate"],"channels":{1:1,2:2,3:3,4:4,5:5,6:6,7:8}[channel_configuration]}


def plan_interframe_recapture(source:Path,parsed:dict):
    # Un gap de sync también puede ser un frame auténtico con el header dañado.
    return None


def _matching_reuse(source:Path,source_sha:str,repair:dict):
    for sidecar in source.parent.glob("*.lossydoctor-manifest.json"):
        try:manifest=json.loads(sidecar.read_text(encoding="utf-8"))
        except Exception:continue
        if not (manifest.get("producer")=="LossyDoctor" and manifest.get("producer_version")==APP_VERSION and manifest.get("repair_spec_id")==SPEC_ID and
            manifest.get("source_sha256")==source_sha and manifest.get("replacement_sampling_frequency_index")==repair["sampling_frequency_index"] and manifest.get("validation_result")=="PASS"):continue
        output=Path(manifest.get("output_path",""))
        try:local=output.resolve().parent==source.resolve().parent and sidecar.resolve()==Path(str(output)+".lossydoctor-manifest.json").resolve()
        except Exception:local=False
        if local and output.exists() and sha256_file(output)==manifest.get("output_sha256"):
            return {"repair_spec_id":SPEC_ID,"status":"REUSED","output_path":str(output),"manifest_path":str(sidecar),"manifest":manifest}
    return None


def execute(source:Path,source_sha:str,parsed:dict,ffmpeg:str,ffprobe:str,publish=True,timeout=300):
    repair=plan(source,parsed)
    if not repair:
        recapture=plan_interframe_recapture(source,parsed)
        if not recapture:return {"plans":[],"executions":[]}
        return _execute_interframe_recapture(source,source_sha,parsed,recapture,ffmpeg,ffprobe,publish,timeout)
    reused=_matching_reuse(source,source_sha,repair)
    if reused:return {"plans":[repair],"executions":[reused]}
    if sha256_file(source)!=source_sha:return {"plans":[repair],"executions":[{"repair_spec_id":SPEC_ID,"status":"REJECTED","reason":"la fuente cambió antes de la reparación"}]}
    with tempfile.TemporaryDirectory(prefix="lossydoctor-adts-repair-") as directory:
        candidate=Path(directory)/(source.stem+source.suffix);shutil.copy2(source,candidate)
        with candidate.open("r+b") as output:
            output.seek(repair["byte_start"]);old=output.read(1);output.seek(repair["byte_start"]);output.write(bytes([repair["replacement_value"]]));output.flush()
        before=source.read_bytes();after=candidate.read_bytes();changed=[index for index,(a,b) in enumerate(zip(before,after)) if a!=b]
        reparsed=analyze(candidate);frames=(reparsed.get("facts") or {}).get("frames") or [];strict=decode(candidate,ffmpeg,"STRICT_DECODE",timeout)
        demux=aac_adts_demux_evidence(candidate,ffprobe,frames,timeout);decoder=ffmpeg_evidence_decode(candidate,ffmpeg,repair["channels"],timeout)
        timeline=aac_adts_timeline.assess(reparsed,demux,decoder,strict)
        passed=bool(len(before)==len(after) and changed==[repair["byte_start"]] and old==bytes([repair["original_value"]]) and
            not reparsed.get("issues") and timeline.get("presentation_exact") and timeline.get("presentation_sample_count")==len(frames)*1024)
        verification={"passed":passed,"changed_byte_offsets":changed,"post_repair_issue_codes":[issue.code for issue in reparsed.get("issues",[])],
            "strict_decode":"PASS" if strict.get("passed") else "FAIL","frame_to_demux_packet_identity":demux.get("all_equal"),
            "presentation_sample_count":timeline.get("presentation_sample_count"),"presentation_pcm_s32le_sha256":timeline.get("canonical_pcm_s32le_sha256")}
        if not passed:return {"plans":[repair],"executions":[{"repair_spec_id":SPEC_ID,"status":"REJECTED","verification":verification}]}
        if sha256_file(source)!=source_sha:return {"plans":[repair],"executions":[{"repair_spec_id":SPEC_ID,"status":"REJECTED","reason":"la fuente cambió durante la reparación"}]}
        desired=source.with_name(source.stem+" [repaired]"+source.suffix);published=candidate
        manifest={"schema_version":3,"producer":"LossyDoctor","producer_version":APP_VERSION,"derivation_kind":"REPAIRED_SAFE","repair_spec_id":SPEC_ID,
            "source_path":str(source),"source_sha256":source_sha,"output_path":str(published),"output_sha256":sha256_file(published),
            "replacement_sampling_frequency_index":repair["sampling_frequency_index"],"replacement_sample_rate":repair["sample_rate"],
            "changed_byte_ranges":[{"operation":"REPLACE_BITS","byte_start":repair["byte_start"],"byte_end":repair["byte_end"],"field":"sampling_frequency_index","original_hex":old.hex(),"replacement_hex":bytes([repair["replacement_value"]]).hex()}],
            "aac_payload_bytes_modified":False,"source_modified":False,"audio_recoding":False,"validation_result":"PASS","verification":verification}
        published,sidecar,manifest,publication_status=publish_or_preview_with_manifest(candidate,desired,manifest,publish)
        return {"plans":[repair],"executions":[{"repair_spec_id":SPEC_ID,"status":publication_status,"output_path":str(published) if published is not None else None,"manifest_path":str(sidecar) if sidecar else None,"manifest":manifest,"verification":verification}]}


def _execute_interframe_recapture(source:Path,source_sha:str,parsed:dict,repair:dict,ffmpeg:str,ffprobe:str,publish=True,timeout=300):
    if sha256_file(source)!=source_sha:return {"plans":[repair],"executions":[{"repair_spec_id":INTERFRAME_RECAPTURE_SPEC_ID,"status":"REJECTED","reason":"la fuente cambió antes de la reparación"}]}
    with tempfile.TemporaryDirectory(prefix="lossydoctor-adts-recapture-") as directory:
        candidate=Path(directory)/(source.stem+source.suffix);before=source.read_bytes();start=repair["byte_start"];end=repair["byte_end"]
        candidate.write_bytes(before[:start]+before[end:]);reparsed=analyze(candidate)
        source_frames=(parsed.get("facts") or {}).get("frames") or [];candidate_frames=(reparsed.get("facts") or {}).get("frames") or []
        frame_hashes_equal=[row.get("frame_sha256") for row in source_frames]==[row.get("frame_sha256") for row in candidate_frames]
        payload_hashes_equal=[row.get("payload_sha256") for row in source_frames]==[row.get("payload_sha256") for row in candidate_frames]
        strict=decode(candidate,ffmpeg,"STRICT_DECODE",timeout);demux=aac_adts_demux_evidence(candidate,ffprobe,candidate_frames,timeout)
        candidate_decode=ffmpeg_evidence_decode(candidate,ffmpeg,repair["channels"],timeout);source_decode=ffmpeg_evidence_decode(source,ffmpeg,repair["channels"],timeout)
        timeline=aac_adts_timeline.assess(reparsed,demux,candidate_decode,strict)
        pcm_equal=bool(source_decode.get("passed") and candidate_decode.get("passed") and source_decode.get("sample_frames")==candidate_decode.get("sample_frames") and source_decode.get("pcm_sha256")==candidate_decode.get("pcm_sha256"))
        passed=bool(len(before)-len(candidate.read_bytes())==repair["removed_byte_count"] and not reparsed.get("issues") and
            frame_hashes_equal and payload_hashes_equal and demux.get("all_equal") and timeline.get("presentation_exact") and pcm_equal)
        verification={"passed":passed,"removed_byte_range":{"byte_start":start,"byte_end":end,"length":repair["removed_byte_count"]},
            "post_repair_issue_codes":[issue.code for issue in reparsed.get("issues",[])],"strict_decode":"PASS" if strict.get("passed") else "FAIL",
            "frame_to_demux_packet_identity":demux.get("all_equal"),"frame_sequence_sha256_equal":frame_hashes_equal,
            "aac_payload_sequence_sha256_equal":payload_hashes_equal,"source_candidate_pcm_equal":pcm_equal,
            "presentation_sample_count":timeline.get("presentation_sample_count"),"presentation_pcm_s32le_sha256":timeline.get("canonical_pcm_s32le_sha256")}
        if not passed:return {"plans":[repair],"executions":[{"repair_spec_id":INTERFRAME_RECAPTURE_SPEC_ID,"status":"REJECTED","verification":verification}]}
        if sha256_file(source)!=source_sha:return {"plans":[repair],"executions":[{"repair_spec_id":INTERFRAME_RECAPTURE_SPEC_ID,"status":"REJECTED","reason":"la fuente cambió durante la reparación"}]}
        desired=source.with_name(source.stem+" [repaired]"+source.suffix)
        manifest={"schema_version":3,"producer":"LossyDoctor","producer_version":APP_VERSION,"derivation_kind":"REPAIRED_SAFE","repair_spec_id":INTERFRAME_RECAPTURE_SPEC_ID,"repair_policy":INTERFRAME_RECAPTURE_POLICY,
            "source_path":str(source),"source_sha256":source_sha,"output_path":str(candidate),"output_sha256":sha256_file(candidate),
            "changed_byte_ranges":[{"operation":"REMOVE_NONFRAME_BYTES","byte_start":start,"byte_end":end,"removed_byte_count":repair["removed_byte_count"],"removed_sha256":hashlib.sha256(before[start:end]).hexdigest()}],
            "aac_frame_bytes_modified":False,"aac_payload_bytes_modified":False,"source_modified":False,"audio_recoding":False,"validation_result":"PASS","verification":verification}
        published,sidecar,manifest,publication_status=publish_or_preview_with_manifest(candidate,desired,manifest,publish)
        return {"plans":[repair],"executions":[{"repair_spec_id":INTERFRAME_RECAPTURE_SPEC_ID,"status":publication_status,"output_path":str(published) if published is not None else None,"manifest_path":str(sidecar) if sidecar else None,"manifest":manifest,"verification":verification}]}
