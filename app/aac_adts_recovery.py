from __future__ import annotations

import json
import tempfile
from pathlib import Path

from app import aac_adts_repair,aac_adts_timeline
from app.external import aac_adts_demux_evidence,decode,decode_to_raw_file,ffmpeg_evidence_decode,raw_file_to_flac
from app.publication import publish_or_preview_with_manifest
from app.utils import sha256_file
from app.version import APP_VERSION
from formats.aac_adts import analyze


POLICY="AAC_ADTS_COMPLETE_CLEAN_LOSSLESS_RECOVERY"
DERIVATION_KIND="RECOVERED_LOSSLESS"
MATERIALIZATION="AAC_ADTS_COMPLETE_CLEAN_FROM_PROVEN_HEADER_REPAIR"


def _blocked(reason:str):
    return {"policy":POLICY,"authority":"NO_PUBLICATION_AUTHORITY","eligible":False,"publication_enabled":False,
        "repair_authority":"NONE","pcm_recovery_authority":"NONE","pcm_class":"AAC_ADTS_RECOVERY_BLOCKED","reason":reason}


def _write_candidate(source:Path,target:Path,repair:dict):
    data=bytearray(source.read_bytes())
    if repair["byte_start"]>=len(data) or data[repair["byte_start"]]!=repair["original_value"]:return False
    data[repair["byte_start"]]=repair["replacement_value"];target.write_bytes(data);return True


def _prove_candidate(source:Path,parsed:dict,ffmpeg:str,ffprobe:str,temporary:Path,timeout:int):
    repair=aac_adts_repair.plan(source,parsed)
    if not repair:return None
    candidate=temporary/(source.stem+".repaired"+source.suffix)
    if not _write_candidate(source,candidate,repair):return None
    source_bytes=source.read_bytes();candidate_bytes=candidate.read_bytes();changed=[i for i,(a,b) in enumerate(zip(source_bytes,candidate_bytes)) if a!=b]
    reparsed=analyze(candidate);frames=(reparsed.get("facts") or {}).get("frames") or [];strict=decode(candidate,ffmpeg,"STRICT_DECODE",timeout)
    demux=aac_adts_demux_evidence(candidate,ffprobe,frames,timeout);decoder=ffmpeg_evidence_decode(candidate,ffmpeg,repair["channels"],timeout)
    timeline=aac_adts_timeline.assess(reparsed,demux,decoder,strict)
    if not (len(source_bytes)==len(candidate_bytes) and changed==[repair["byte_start"]] and not reparsed.get("issues") and timeline.get("presentation_exact")):return None
    return {"candidate":candidate,"repair":repair,"timeline":timeline,"repaired_adts_sha256":sha256_file(candidate),
        "presentation_sample_count":timeline["presentation_sample_count"],"canonical_pcm_s32le_sha256":timeline["canonical_pcm_s32le_sha256"],
        "sample_rate":timeline["sample_rate"],"channels":timeline["channels"],"complete_frame_count":timeline["complete_frame_count"]}


def assess(source:Path,parsed:dict,ffmpeg:str,ffprobe:str,timeout=300):
    with tempfile.TemporaryDirectory(prefix="lossydoctor-adts-assessment-") as directory:
        proof=_prove_candidate(source,parsed,ffmpeg,ffprobe,Path(directory),timeout)
    if not proof:return _blocked("UNIQUE_VERIFIED_COMPLETE_CLEAN_HEADER_REPAIR_REQUIRED")
    return {"policy":POLICY,"authority":"COMPLETE_CLEAN_LOSSLESS_RECOVERY","eligible":True,"publication_enabled":True,
        "repair_authority":"PREFER_VERIFIED_ADTS_COPY","pcm_recovery_authority":"COMPLETE_CLEAN","pcm_class":"COMPLETE_CLEAN",
        "reason":"PROVEN_HEADER_REPAIR_DEFINES_EXACT_COMPLETE_CLEAN_PCM","source_repair_spec_id":aac_adts_repair.SPEC_ID,
        "repaired_adts_sha256":proof["repaired_adts_sha256"],"presentation_sample_count":proof["presentation_sample_count"],
        "canonical_pcm_s32le_sha256":proof["canonical_pcm_s32le_sha256"],"sample_rate":proof["sample_rate"],"channels":proof["channels"],
        "complete_frame_count":proof["complete_frame_count"]}


def _manifest_matches(manifest:dict,source_sha:str,assessment:dict):
    return (manifest.get("producer")=="LossyDoctor" and manifest.get("producer_version")==APP_VERSION and
        manifest.get("derivation_kind")==DERIVATION_KIND and manifest.get("materialization")==MATERIALIZATION and
        manifest.get("recovery_policy")==POLICY and manifest.get("source_sha256")==source_sha and manifest.get("validation_result")=="PASS" and
        manifest.get("sample_count")==assessment.get("presentation_sample_count") and
        manifest.get("source_canonical_pcm_sha256")==assessment.get("canonical_pcm_s32le_sha256"))


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


def export(source:Path,source_sha:str,parsed:dict,ffmpeg:str,ffprobe:str,assessment:dict,publish=True,timeout=300):
    if not assessment.get("eligible"):return {"status":"NOT_ELIGIBLE","assessment":assessment,"outputs":[]}
    reused=_reuse(source,source_sha,assessment)
    if reused:return {"status":"REUSED","assessment":assessment,"outputs":[reused]}
    if sha256_file(source)!=source_sha:return {"status":"REJECTED","reason":"la fuente cambió antes de la recuperación","assessment":assessment,"outputs":[]}
    with tempfile.TemporaryDirectory(prefix="lossydoctor-adts-recovery-") as directory:
        temporary=Path(directory);proof=_prove_candidate(source,parsed,ffmpeg,ffprobe,temporary,timeout)
        if not proof or proof["repaired_adts_sha256"]!=assessment.get("repaired_adts_sha256") or proof["canonical_pcm_s32le_sha256"]!=assessment.get("canonical_pcm_s32le_sha256"):
            return {"status":"REJECTED","reason":"la evidencia completa y limpia cambió desde la evaluación","assessment":assessment,"outputs":[]}
        raw=temporary/"canonical.s32le";decoded=decode_to_raw_file(proof["candidate"],raw,ffmpeg,timeout)
        frame_bytes=proof["channels"]*4
        if not decoded.get("passed") or raw.stat().st_size!=proof["presentation_sample_count"]*frame_bytes or sha256_file(raw)!=proof["canonical_pcm_s32le_sha256"]:
            return {"status":"REJECTED","reason":"la materialización del PCM canónico no coincide","assessment":assessment,"outputs":[]}
        flac=temporary/"candidate.flac";encoded=raw_file_to_flac(raw,flac,ffmpeg,proof["sample_rate"],proof["channels"],timeout)
        back=temporary/"back.s32le";verified=decode_to_raw_file(flac,back,ffmpeg,timeout) if encoded.get("passed") else {"passed":False}
        if not verified.get("passed") or back.stat().st_size!=raw.stat().st_size or sha256_file(back)!=sha256_file(raw):
            return {"status":"REJECTED","reason":"la verificación round-trip del PCM FLAC no coincide","assessment":assessment,"outputs":[]}
        if sha256_file(source)!=source_sha:return {"status":"REJECTED","reason":"la fuente cambió durante la recuperación","assessment":assessment,"outputs":[]}
        desired=source.with_name(source.stem+" [recovered-lossless].flac");output=flac
        manifest={"schema_version":3,"producer":"LossyDoctor","producer_version":APP_VERSION,"derivation_kind":DERIVATION_KIND,
            "materialization":MATERIALIZATION,"recovery_policy":POLICY,"source_pcm_recovery_class":"COMPLETE_CLEAN",
            "source_path":str(source),"source_sha256":source_sha,"output_path":str(output),"output_sha256":sha256_file(output),
            "source_repair_spec_id":aac_adts_repair.SPEC_ID,"repaired_adts_sha256":proof["repaired_adts_sha256"],
            "sample_rate":proof["sample_rate"],"channels":proof["channels"],"sample_count":proof["presentation_sample_count"],
            "source_canonical_pcm_sha256":proof["canonical_pcm_s32le_sha256"],"flac_decoded_pcm_sha256":sha256_file(back),
            "aac_payload_bytes_modified":False,"source_modified":False,"resampling":"NONE","channel_remix":"NONE",
            "audio_recoding":"LOSSLESS_FLAC_ONLY","validation_result":"PASS"}
        output,sidecar,manifest,publication_status=publish_or_preview_with_manifest(flac,desired,manifest,publish)
        return {"status":publication_status,"assessment":assessment,"outputs":[{"status":publication_status,"output_path":str(output) if output is not None else None,"manifest_path":str(sidecar) if sidecar else None,"manifest":manifest}]}
