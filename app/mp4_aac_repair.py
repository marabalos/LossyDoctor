from __future__ import annotations

from pathlib import Path
import hashlib
import json
import shutil
import tempfile

from app.external import decode,ffmpeg_evidence_decode,ffprobe,mp4_aac_demux_evidence
from app.publication import publish_or_preview_with_manifest
from app.utils import sha256_file
from app.version import APP_VERSION
from formats.mp4_aac import analyze


OFFSET_SPEC_ID="MP4_REWRITE_SINGLE_CHUNK_OFFSET_TO_UNIQUE_MDAT"
DURATION_SPEC_ID="MP4_REWRITE_MDHD_DURATION_TO_COMPLETE_STTS"
SAMPLE_COUNT_SPEC_ID="MP4_REWRITE_STSZ_SAMPLE_COUNT_TO_PROVEN_PHYSICAL_TABLE"
DESCRIPTION_REFERENCE_SPEC_ID="MP4_REWRITE_STSC_DESCRIPTION_TO_UNIQUE_VALID_AAC_ENTRY"
SPEC_ID=OFFSET_SPEC_ID


def _simple_presentation(track:dict):
    window=track.get("presentation_window") or {}
    return bool(window.get("determined") and window.get("presentation_model")=="SINGLE_NORMAL_RATE_MEDIA_EDIT")


def _offset_plan(parsed:dict,assessment:dict):
    if not assessment.get("eligible"):return None
    tracks=(parsed.get("facts") or {}).get("tracks") or [];audio=[track for track in tracks if track.get("handler_type")=="soun"]
    if len(audio)!=1:return None
    tables=audio[0].get("sample_tables") or {};offsets=tables.get("stco") or tables.get("co64") or {}
    mdat=((parsed.get("facts") or {}).get("mp4") or {}).get("mdat_payload_ranges") or []
    if len(offsets.get("offsets") or [])!=1 or len(offsets.get("entry_byte_offsets") or [])!=1 or len(mdat)!=1:return None
    width=offsets.get("entry_width");target=mdat[0].get("byte_start")
    if width not in (4,8) or target is None or target>=1<<(8*width):return None
    return {"spec":{"id":OFFSET_SPEC_ID},"status":"ELIGIBLE","reason":"un offset de chunk incorrecto tiene un único reemplazo exacto en bytes dentro de mdat",
        "byte_start":offsets["entry_byte_offsets"][0],"byte_end":offsets["entry_byte_offsets"][0]+width,"width":width,
        "field":"stco/co64 chunk_offset","original_value":offsets["offsets"][0],"replacement_value":target}


def _duration_plan(parsed:dict):
    if {issue.code for issue in parsed.get("issues",[])}!={"MP4_MEDIA_DURATION_MISMATCH"}:return None
    tracks=(parsed.get("facts") or {}).get("tracks") or []
    if len(tracks)!=1 or tracks[0].get("handler_type")!="soun":return None
    track=tracks[0];header=track.get("media_header") or {};tables=track.get("sample_tables") or {};stts=tables.get("stts") or {};stsz=tables.get("stsz") or {};provenance=track.get("access_unit_provenance") or {}
    target=stts.get("duration_units");width=header.get("duration_width");start=header.get("duration_byte_start")
    complete=bool(stts.get("complete") and stsz.get("complete") and stts.get("sample_count")==stsz.get("sample_count") and
        provenance.get("mapping_complete") and provenance.get("decode_timeline_complete") and provenance.get("all_access_units_hashed") and
        provenance.get("decode_end_units")==target and _simple_presentation(track))
    if not complete or header.get("box_count")!=1 or width not in (4,8) or start is None or target is None or target==track.get("media_duration") or target>=1<<(8*width):return None
    return {"spec":{"id":DURATION_SPEC_ID},"status":"ELIGIBLE","reason":"una duración mdhd tiene un único reemplazo derivado de la tabla completa de tiempos de decodificación",
        "byte_start":start,"byte_end":start+width,"width":width,"field":"mdhd duration",
        "original_value":track.get("media_duration"),"replacement_value":target}


def _sample_count_plan(parsed:dict):
    expected={"MP4_ACCESS_UNIT_MAPPING_INCOMPLETE","MP4_ACCESS_UNIT_TIMELINE_INCOMPLETE","MP4_SAMPLE_COUNT_MISMATCH"}
    if {issue.code for issue in parsed.get("issues",[])}!=expected:return None
    facts=parsed.get("facts") or {};tracks=facts.get("tracks") or []
    if len(tracks)!=1 or tracks[0].get("handler_type")!="soun":return None
    track=tracks[0];tables=track.get("sample_tables") or {};stsz=tables.get("stsz") or {};stts=tables.get("stts") or {};stsc=tables.get("stsc") or {};offsets=tables.get("stco") or tables.get("co64") or {};mdat=(facts.get("mp4") or {}).get("mdat_payload_ranges") or []
    target=stts.get("sample_count");declared=stsz.get("sample_count");sizes=stsz.get("available_sizes") or [];entries=stsc.get("entries") or [];chunk_offsets=offsets.get("offsets") or []
    if not (stts.get("complete") and stsz.get("complete") and stsz.get("default_sample_size")==0 and target==declared+1==stsz.get("available_entry_count") and len(sizes)==target):return None
    if len(entries)!=1 or entries[0]!={"first_chunk":1,"samples_per_chunk":target,"sample_description_index":1} or len(chunk_offsets)!=1 or len(mdat)!=1:return None
    payload_start=mdat[0].get("byte_start");payload_end=mdat[0].get("byte_end")
    if payload_start is None or payload_end is None or chunk_offsets[0]!=payload_start or any(size<=0 or size>8184 for size in sizes) or sum(sizes)!=payload_end-payload_start:return None
    if track.get("media_duration")!=stts.get("duration_units") or not _simple_presentation(track):return None
    start=stsz.get("sample_count_byte_start")
    if start is None:return None
    return {"spec":{"id":SAMPLE_COUNT_SPEC_ID},"status":"ELIGIBLE","reason":"una entrada stsz omitida está físicamente presente y corroborada independientemente por stts, stsc y la cobertura exacta de mdat",
        "byte_start":start,"byte_end":start+4,"width":4,"field":"stsz sample_count","original_value":declared,"replacement_value":target,
        "payload_start":payload_start,"physical_sizes":sizes,"presentation_sample_count":track["presentation_window"].get("presentation_sample_count")}


def _description_reference_plan(parsed:dict):
    if {issue.code for issue in parsed.get("issues",[])}!={"MP4_ACCESS_UNIT_DESCRIPTION_INVALID"}:return None
    tracks=(parsed.get("facts") or {}).get("tracks") or []
    if len(tracks)!=1 or tracks[0].get("handler_type")!="soun":return None
    track=tracks[0];descriptions=track.get("sample_descriptions") or [];tables=track.get("sample_tables") or {};stsc=tables.get("stsc") or {};stsz=tables.get("stsz") or {};stts=tables.get("stts") or {};offsets=tables.get("stco") or tables.get("co64") or {};provenance=track.get("access_unit_provenance") or {};entries=stsc.get("entries") or [];field_offsets=stsc.get("sample_description_index_byte_offsets") or []
    if len(descriptions)!=1 or not descriptions[0].get("valid") or len(entries)!=1 or len(field_offsets)!=1 or entries[0].get("sample_description_index")==1:return None
    complete=bool(stsc.get("complete") and stsc.get("valid") and stsz.get("complete") and stts.get("complete") and stsz.get("sample_count")==stts.get("sample_count")==entries[0].get("samples_per_chunk") and
        len(offsets.get("offsets") or [])==1 and offsets.get("all_offsets_inside_mdat") and provenance.get("all_access_units_within_mdat") and provenance.get("all_access_units_hashed") and provenance.get("decode_timeline_complete") and _simple_presentation(track))
    if not complete:return None
    start=field_offsets[0]
    return {"spec":{"id":DESCRIPTION_REFERENCE_SPEC_ID},"status":"ELIGIBLE","reason":"una referencia stsc no válida a descripción de muestra tiene un único reemplazo AAC autenticado",
        "byte_start":start,"byte_end":start+4,"width":4,"field":"stsc sample_description_index","original_value":entries[0]["sample_description_index"],"replacement_value":1}


def _plan(parsed:dict,assessment:dict):
    return _offset_plan(parsed,assessment) or _duration_plan(parsed) or _sample_count_plan(parsed) or _description_reference_plan(parsed)


def _manifest_matches(manifest:dict,source_sha:str,plan:dict,expected_essence:str,expected_pcm:str):
    replacement=manifest.get("replacement_field_value")
    if replacement is None:
        replacement=manifest.get("replacement_chunk_offset") if plan["spec"]["id"]==OFFSET_SPEC_ID else manifest.get("replacement_media_duration")
    verification=manifest.get("verification") or {}
    pcm_matches=(verification.get("presentation_pcm_s32le_sha256")==expected_pcm if expected_pcm else bool(verification.get("presentation_pcm_s32le_sha256")))
    return (manifest.get("producer")=="LossyDoctor" and manifest.get("producer_version")==APP_VERSION and
        manifest.get("derivation_kind")=="REPAIRED_SAFE" and manifest.get("repair_spec_id")==plan["spec"]["id"] and
        manifest.get("validation_result")=="PASS" and manifest.get("source_sha256")==source_sha and replacement==plan["replacement_value"] and
        manifest.get("aac_access_unit_essence_sha256")==expected_essence and pcm_matches)


def _reuse(source:Path,source_sha:str,plan:dict,expected_essence:str,expected_pcm:str):
    for sidecar in source.parent.glob("*.lossydoctor-manifest.json"):
        try:manifest=json.loads(sidecar.read_text(encoding="utf-8"))
        except Exception:continue
        if not _manifest_matches(manifest,source_sha,plan,expected_essence,expected_pcm):continue
        output=Path(manifest.get("output_path",""))
        try:local=output.resolve().parent==source.resolve().parent and sidecar.resolve()==Path(str(output)+".lossydoctor-manifest.json").resolve()
        except Exception:local=False
        if local and output.exists() and sha256_file(output)==manifest.get("output_sha256"):
            return {"repair_spec_id":plan["spec"]["id"],"status":"REUSED","output_path":str(output),"manifest_path":str(sidecar),"manifest":manifest,"verification":manifest.get("verification") or {}}
    return None


def _essence_hash(path:Path,units:list[dict]):
    digest=hashlib.sha256()
    with path.open("rb") as source:
        for unit in units:
            source.seek(unit["byte_start"]);payload=source.read(unit["size"])
            if len(payload)!=unit["size"]:return None
            digest.update(payload)
    return digest.hexdigest()


def _expected_evidence(source:Path,parsed:dict,assessment:dict,plan:dict,ffmpeg:str,timeout:int):
    if plan["spec"]["id"]==OFFSET_SPEC_ID:
        return assessment.get("aac_access_unit_essence_sha256"),assessment.get("presentation_sample_count"),assessment.get("presentation_pcm_s32le_sha256")
    if plan["spec"]["id"]==SAMPLE_COUNT_SPEC_ID:
        cursor=plan["payload_start"];units=[]
        for size in plan["physical_sizes"]:units.append({"byte_start":cursor,"size":size});cursor+=size
        return _essence_hash(source,units),plan.get("presentation_sample_count"),None
    facts=parsed.get("facts") or {};track=(facts.get("tracks") or [{}])[0];units=(track.get("access_unit_provenance") or {}).get("access_units") or []
    if plan["spec"]["id"]==DESCRIPTION_REFERENCE_SPEC_ID:
        return _essence_hash(source,units),(track.get("presentation_window") or {}).get("presentation_sample_count"),None
    source_pcm=facts.get("mp4_aac_presentation_decoder_evidence") or ffmpeg_evidence_decode(source,ffmpeg,(track.get("sample_descriptions") or [{}])[0].get("channels"),timeout)
    return _essence_hash(source,units),source_pcm.get("sample_frames"),source_pcm.get("pcm_sha256")


def execute(source:Path,source_sha:str,parsed:dict,assessment:dict,ffmpeg:str,ffprobe_exe:str,publish=True,timeout=300):
    if source.suffix.lower() not in (".m4a",".mp4",".m4b"):return {"plans":[],"executions":[]}
    plan=_plan(parsed,assessment)
    if not plan:return {"plans":[],"executions":[]}
    expected_essence,expected_samples,expected_pcm=_expected_evidence(source,parsed,assessment,plan,ffmpeg,timeout)
    no_source_pcm_specs={SAMPLE_COUNT_SPEC_ID,DESCRIPTION_REFERENCE_SPEC_ID}
    if not expected_essence or not expected_samples or (plan["spec"]["id"] not in no_source_pcm_specs and not expected_pcm):
        return {"plans":[plan],"executions":[{"repair_spec_id":plan["spec"]["id"],"status":"REJECTED","reason":"la evidencia de la esencia fuente o del PCM canónico está incompleta"}]}
    reused=_reuse(source,source_sha,plan,expected_essence,expected_pcm)
    if reused:return {"plans":[plan],"executions":[reused]}
    if sha256_file(source)!=source_sha:return {"plans":[plan],"executions":[{"repair_spec_id":plan["spec"]["id"],"status":"REJECTED","reason":"la fuente cambió antes de la reparación"}]}
    with tempfile.TemporaryDirectory(prefix="lossydoctor-mp4-aac-repair-") as directory:
        temporary=Path(directory);candidate=temporary/(source.stem+source.suffix);shutil.copy2(source,candidate)
        with candidate.open("r+b") as output:
            output.seek(plan["byte_start"]);old=output.read(plan["width"]);replacement=plan["replacement_value"].to_bytes(plan["width"],"big")
            output.seek(plan["byte_start"]);output.write(replacement);output.flush()
        source_bytes=source.read_bytes();candidate_bytes=candidate.read_bytes();changed=[index for index,(a,b) in enumerate(zip(source_bytes,candidate_bytes)) if a!=b]
        exact_diff=bool(len(source_bytes)==len(candidate_bytes) and changed and min(changed)>=plan["byte_start"] and max(changed)<plan["byte_end"])
        reparsed=analyze(candidate);issues=[issue.code for issue in reparsed.get("issues",[])];track=(reparsed.get("facts",{}).get("tracks") or [{}])[0]
        provenance=track.get("access_unit_provenance") or {};units=provenance.get("access_units") or [];essence=_essence_hash(candidate,units)
        strict=decode(candidate,ffmpeg,"STRICT_DECODE",timeout);play=decode(candidate,ffmpeg,"PLAYBACK_DECODE",timeout);probe=ffprobe(candidate,ffprobe_exe,timeout)
        demux=mp4_aac_demux_evidence(candidate,ffprobe_exe,units,track.get("media_timescale"),timeout);channels=(track.get("sample_descriptions") or [{}])[0].get("channels")
        pcm=ffmpeg_evidence_decode(candidate,ffmpeg,channels,timeout);window=track.get("presentation_window") or {}
        pcm_matches=(pcm.get("pcm_sha256")==expected_pcm if expected_pcm else bool(pcm.get("pcm_sha256")))
        passed=bool(exact_diff and old==plan["original_value"].to_bytes(plan["width"],"big") and not issues and
            provenance.get("mapping_complete") and provenance.get("all_access_units_hashed") and essence==expected_essence and
            strict.get("passed") and play.get("completed") and probe.get("audio_streams") and demux.get("all_boundaries_and_hashes_equal") and
            window.get("determined") and pcm.get("sample_frames")==expected_samples and pcm_matches)
        verification={"passed":passed,"strict_decode":"PASS" if strict.get("passed") else "FAIL","playback_decode":"PASS" if play.get("completed") else "FAIL","ffprobe":"PASS" if probe.get("audio_streams") else "FAIL",
            "post_repair_issue_codes":issues,"changed_bytes_within_declared_field":exact_diff,"access_unit_mapping_complete":provenance.get("mapping_complete"),
            "demux_access_units_byte_identical":demux.get("all_boundaries_and_hashes_equal"),"aac_access_unit_essence_sha256":essence,
            "presentation_sample_count":pcm.get("sample_frames"),"presentation_pcm_s32le_sha256":pcm.get("pcm_sha256")}
        spec=plan["spec"]["id"]
        if not passed:return {"plans":[plan],"executions":[{"repair_spec_id":spec,"status":"REJECTED","verification":verification}]}
        if sha256_file(source)!=source_sha:return {"plans":[plan],"executions":[{"repair_spec_id":spec,"status":"REJECTED","reason":"la fuente cambió durante la reparación","verification":verification}]}
        desired=source.with_name(source.stem+" [repaired]"+source.suffix);published=candidate
        manifest={"schema_version":3,"producer":"LossyDoctor","producer_version":APP_VERSION,"derivation_kind":"REPAIRED_SAFE","repair_spec_id":spec,
            "source_path":str(source),"source_sha256":source_sha,"output_path":str(published),"output_sha256":sha256_file(published),
            "original_field_value":plan["original_value"],"replacement_field_value":plan["replacement_value"],
            "changed_byte_ranges":[{"operation":"REPLACE","byte_start":plan["byte_start"],"byte_end":plan["byte_end"],"field":plan["field"],"original_hex":old.hex(),"replacement_hex":replacement.hex()}],
            "aac_access_unit_essence_sha256":essence,"aac_access_unit_bytes_modified":False,"source_modified":False,"audio_recoding":False,
            "validation_result":"PASS","verification":verification}
        if spec==OFFSET_SPEC_ID:
            manifest.update({"original_chunk_offset":plan["original_value"],"replacement_chunk_offset":plan["replacement_value"]})
        elif spec==DURATION_SPEC_ID:
            manifest.update({"original_media_duration":plan["original_value"],"replacement_media_duration":plan["replacement_value"]})
        elif spec==SAMPLE_COUNT_SPEC_ID:
            manifest.update({"original_sample_count":plan["original_value"],"replacement_sample_count":plan["replacement_value"]})
        else:
            manifest.update({"original_sample_description_index":plan["original_value"],"replacement_sample_description_index":plan["replacement_value"]})
        published,sidecar,manifest,publication_status=publish_or_preview_with_manifest(candidate,desired,manifest,publish)
        execution={"repair_spec_id":spec,"status":publication_status,"output_path":str(published) if published is not None else None,"manifest_path":str(sidecar) if sidecar else None,"manifest":manifest,"verification":verification}
        return {"plans":[plan],"executions":[execution]}
