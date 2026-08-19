from __future__ import annotations

from pathlib import Path
import hashlib
import json
import tempfile

from app.external import binary_sha256,canonical_pcm_profile,decode,decode_to_raw_file,raw_file_to_flac,version
from app.publication import publish_or_preview_with_manifest
from app.utils import sha256_file
from app.version import APP_VERSION


POLICY="MP4_AAC_UNIQUE_MDAT_COMPLETE_CLEAN_ASSESSMENT"
EXPORT_POLICY="MP4_AAC_COMPLETE_CLEAN_LOSSLESS_EXPORT"
DERIVATION_KIND="RECOVERED_LOSSLESS"
MATERIALIZATION="MP4_AAC_CANONICAL_PRESENTATION_FROM_BYTE_PRESERVED_ACCESS_UNITS"


def _result(reason:str,pcm_class:str="MP4_AAC_RECOVERY_BLOCKED",evidence:dict|None=None):
    result={"policy":POLICY,"authority":"ASSESSMENT_ONLY_NO_PUBLICATION","eligible":False,
        "publication_enabled":False,"repair_authority":"NONE","pcm_recovery_authority":"NONE",
        "pcm_class":pcm_class,"reason":reason}
    if evidence:result.update(evidence)
    return result


def _hash_range(path:Path,start:int,length:int,chunk_size=1024*1024):
    digest=hashlib.sha256();remaining=length
    with path.open("rb") as source:
        source.seek(start)
        while remaining:
            block=source.read(min(chunk_size,remaining))
            if not block:raise RuntimeError("PCM_RANGE_SHORT_READ")
            digest.update(block);remaining-=len(block)
    return digest.hexdigest()


def _adts_header(payload_size:int,audio_object_type:int,frequency_index:int,channel_configuration:int):
    frame_length=payload_size+7;profile=audio_object_type-1
    return bytes((0xff,0xf1,(profile<<6)|(frequency_index<<2)|(channel_configuration>>2),
        ((channel_configuration&3)<<6)|(frame_length>>11),(frame_length>>3)&0xff,
        ((frame_length&7)<<5)|0x1f,0xfc))


def _decode_candidate(path:Path,plan:dict,ffmpeg:str,temporary:Path,timeout:int):
    adts=temporary/"candidate.aac";raw=temporary/"candidate.s32le";essence=hashlib.sha256()
    try:
        with path.open("rb") as source,adts.open("xb") as output:
            source.seek(plan["payload_start"])
            for size in plan["sizes"]:
                payload=source.read(size)
                if len(payload)!=size:return {"passed":False,"reason":"AAC_ACCESS_UNIT_SHORT_READ"}
                essence.update(payload);output.write(_adts_header(size,2,plan["frequency_index"],plan["channel_configuration"]));output.write(payload)
        strict=decode(adts,ffmpeg,"STRICT_DECODE",timeout)
        if not strict.get("passed"):return {"passed":False,"reason":"TEMPORARY_AAC_STRICT_DECODE_DID_NOT_PASS"}
        decoded=decode_to_raw_file(adts,raw,ffmpeg,timeout)
        if not decoded.get("passed") or not raw.exists():return {"passed":False,"reason":"TEMPORARY_AAC_DECODE_DID_NOT_COMPLETE"}
        frame_bytes=plan["channels"]*4;raw_bytes=raw.stat().st_size
        if raw_bytes%frame_bytes:return {"passed":False,"reason":"TEMPORARY_PCM_NOT_SAMPLE_ALIGNED"}
        decoded_samples=raw_bytes//frame_bytes;expected_decoded_samples=len(plan["sizes"])*1024
        if decoded_samples!=expected_decoded_samples or plan["pcm_end"]>decoded_samples:
            return {"passed":False,"reason":"TEMPORARY_DECODE_SAMPLE_GEOMETRY_DISAGREES_WITH_AAC_LC_FRAMES"}
        pcm_length=(plan["pcm_end"]-plan["pcm_start"])*frame_bytes
        pcm_hash=_hash_range(raw,plan["pcm_start"]*frame_bytes,pcm_length)
        return {"passed":True,"raw_path":raw,"frame_bytes":frame_bytes,"decoded_sample_count":decoded_samples,
            "presentation_byte_start":plan["pcm_start"]*frame_bytes,"presentation_byte_length":pcm_length,
            "aac_access_unit_essence_sha256":essence.hexdigest(),"presentation_pcm_s32le_sha256":pcm_hash}
    except Exception as exc:return {"passed":False,"reason":f"ASSESSMENT_ERROR_{type(exc).__name__}"}


def _copy_range(source:Path,target:Path,start:int,length:int,chunk_size=1024*1024):
    remaining=length
    with source.open("rb") as inp,target.open("xb") as out:
        inp.seek(start)
        while remaining:
            block=inp.read(min(chunk_size,remaining))
            if not block:raise RuntimeError("PCM_RANGE_SHORT_READ")
            out.write(block);remaining-=len(block)


def assess(path:Path,parsed:dict,playability:str,ffmpeg:str,timeout=300):
    """Evalúa un caso acotado y exacto de recuperación completa MP4/AAC.

    Acepta sólo un archivo AAC-LC de una pista no reproducible cuyo único defecto
    sea un offset de chunk incorrecto y cuyo payload mdat esté cubierto por la
    tabla completa de tamaños. Una vista ADTS temporal preserva cada byte AAC y
    existe únicamente para demostrar el PCM canónico.
    """
    if playability!="UNPLAYABLE":
        return _result("RECOVERY_NOT_REQUIRED_FOR_PLAYABLE_SOURCE","NOT_REQUIRED" if not parsed.get("issues") else "POLICY_BLOCKED_PLAYABLE")
    issue_codes={issue.code for issue in parsed.get("issues",[])}
    if issue_codes!={"MP4_CHUNK_OFFSET_OUTSIDE_MDAT","MP4_ACCESS_UNIT_OUTSIDE_MDAT"}:
        return _result("ISSUE_SET_OUTSIDE_UNIQUE_CHUNK_OFFSET_SCOPE")
    facts=parsed.get("facts") or {};tracks=facts.get("tracks") or []
    audio=[track for track in tracks if track.get("handler_type")=="soun"]
    if len(audio)!=1:return _result("EXACTLY_ONE_AUDIO_TRACK_REQUIRED")
    track=audio[0];descriptions=track.get("sample_descriptions") or []
    if len(descriptions)!=1 or not descriptions[0].get("valid"):
        return _result("EXACTLY_ONE_AUTHENTICATED_AAC_DESCRIPTION_REQUIRED")
    description=descriptions[0];config=description.get("aac_config") or {}
    sample_rate=description.get("sample_rate") or config.get("sample_rate");channels=description.get("channels")
    frequency_index=config.get("sampling_frequency_index");channel_configuration=config.get("channel_configuration")
    if config.get("audio_object_type")!=2 or frequency_index not in range(13) or channel_configuration not in range(1,8) or channels!=channel_configuration:
        return _result("TEMPORARY_DECODE_VIEW_SUPPORTS_ONLY_AAC_LC_STANDARD_RATE_AND_CHANNEL_CONFIG")
    tables=track.get("sample_tables") or {};stsz=tables.get("stsz") or {};stsc=tables.get("stsc") or {};stts=tables.get("stts") or {}
    sizes=stsz.get("sizes") or [];stsc_entries=stsc.get("entries") or []
    if not stsz.get("complete") or not sizes or len(sizes)!=stsz.get("sample_count") or any(size<=0 or size>8184 for size in sizes):
        return _result("COMPLETE_BOUNDED_SAMPLE_SIZE_TABLE_REQUIRED")
    if not stsc.get("complete") or not stsc.get("valid") or len(stsc_entries)!=1:
        return _result("EXACTLY_ONE_COMPLETE_SAMPLE_TO_CHUNK_ENTRY_REQUIRED")
    entry=stsc_entries[0]
    if entry!={"first_chunk":1,"samples_per_chunk":len(sizes),"sample_description_index":1}:
        return _result("SINGLE_CHUNK_MUST_COVER_EVERY_DECLARED_SAMPLE")
    if not stts.get("complete") or stts.get("sample_count")!=len(sizes) or not (track.get("access_unit_provenance") or {}).get("decode_timeline_complete"):
        return _result("COMPLETE_DECODE_TIMELINE_REQUIRED")
    mdat=(facts.get("mp4") or {}).get("mdat_payload_ranges") or []
    if len(mdat)!=1:return _result("EXACTLY_ONE_MDAT_PAYLOAD_REQUIRED")
    payload_start=mdat[0].get("byte_start");payload_end=mdat[0].get("byte_end")
    if payload_start is None or payload_end is None:return _result("UNIQUE_MDAT_PAYLOAD_BOUNDS_REQUIRED")
    payload_bytes=payload_end-payload_start;declared_bytes=sum(sizes)
    if declared_bytes<payload_bytes:
        return _result("PARTIAL_RECOVERY_BLOCKED_AMBIGUOUS_EXTRA_MDAT_BYTES",evidence={
            "partial_candidate_assessed":True,"candidate_origin_structurally_proven":False,
            "declared_sample_bytes":declared_bytes,"mdat_payload_bytes":payload_bytes,
            "unassigned_mdat_bytes":payload_bytes-declared_bytes,"candidate_region_count":0})
    if declared_bytes>payload_bytes and len(sizes)>1 and sum(sizes[:-1])<payload_bytes:
        return _result("PARTIAL_RECOVERY_BLOCKED_UNPROVEN_MDAT_CHUNK_ORIGIN",evidence={
            "partial_candidate_assessed":True,"candidate_origin_structurally_proven":False,
            "declared_sample_bytes":declared_bytes,"mdat_payload_bytes":payload_bytes,
            "terminal_declared_overrun_bytes":declared_bytes-payload_bytes,
            "testable_prefix_access_unit_count":len(sizes)-1,"testable_prefix_bytes":sum(sizes[:-1]),
            "candidate_region_count":0})
    if declared_bytes!=payload_bytes:return _result("SAMPLE_SIZES_MUST_COVER_THE_UNIQUE_MDAT_EXACTLY")
    presentation=track.get("presentation_window") or {};media_timescale=track.get("media_timescale")
    if presentation.get("presentation_model")!="SINGLE_NORMAL_RATE_MEDIA_EDIT" or not presentation.get("determined") or not sample_rate or not channels or not media_timescale:
        return _result("DETERMINED_PRESENTATION_WINDOW_REQUIRED")
    start_scaled=presentation.get("media_start_units",-1)*sample_rate;end_scaled=presentation.get("media_end_units",-1)*sample_rate
    if start_scaled<0 or end_scaled<0 or start_scaled%media_timescale or end_scaled%media_timescale:
        return _result("PRESENTATION_BOUNDARIES_MUST_MAP_TO_EXACT_PCM_SAMPLES")
    pcm_start=start_scaled//media_timescale;pcm_end=end_scaled//media_timescale
    if pcm_end<=pcm_start or pcm_end-pcm_start!=presentation.get("presentation_sample_count"):
        return _result("PRESENTATION_SAMPLE_COUNT_DISAGREES_WITH_MEDIA_WINDOW")
    plan={"payload_start":payload_start,"payload_end":payload_end,"sizes":sizes,"frequency_index":frequency_index,
        "channel_configuration":channel_configuration,"sample_rate":sample_rate,"channels":channels,"pcm_start":pcm_start,"pcm_end":pcm_end}
    with tempfile.TemporaryDirectory() as directory:candidate=_decode_candidate(path,plan,ffmpeg,Path(directory),timeout)
    if not candidate.get("passed"):return _result(candidate.get("reason","TEMPORARY_AAC_DECODE_DID_NOT_COMPLETE"))
    result={"policy":POLICY,"authority":"ASSESSMENT_ONLY_NO_PUBLICATION","eligible":True,
        "publication_enabled":False,"repair_authority":"NONE","pcm_recovery_authority":"ASSESSMENT_ONLY",
        "pcm_class":"COMPLETE_CLEAN","reason":"UNIQUE_MDAT_EXACTLY_COVERED_AND_CANONICAL_PCM_PROVEN",
        "source_audio_track_count":1,"access_unit_count":len(sizes),"aac_access_unit_bytes":sum(sizes),
        "aac_access_unit_essence_sha256":candidate["aac_access_unit_essence_sha256"],"temporary_transport":"ADTS_HEADERS_SYNTHESIZED_AAC_PAYLOAD_BYTES_UNCHANGED",
        "decoded_sample_count_before_presentation_trim":candidate["decoded_sample_count"],
        "presentation_pcm_start_sample":pcm_start,"presentation_pcm_end_sample":pcm_end,
        "presentation_sample_count":pcm_end-pcm_start,"presentation_pcm_s32le_sha256":candidate["presentation_pcm_s32le_sha256"],
        "temporary_strict_decode":"PASS",
        "canonical_decoder":"ffmpeg","decoder_version":version(ffmpeg),"decoder_binary_sha256":binary_sha256(ffmpeg),
        "original_modified":False,"output_created":False,"_private_plan":plan}
    return result


def _public(assessment:dict):return {key:value for key,value in assessment.items() if not key.startswith("_")}


def _manifest_matches(manifest:dict,source_sha:str,assessment:dict):
    return (manifest.get("producer")=="LossyDoctor" and manifest.get("producer_version")==APP_VERSION and
        manifest.get("derivation_kind")==DERIVATION_KIND and manifest.get("materialization")==MATERIALIZATION and
        manifest.get("recovery_policy")==EXPORT_POLICY and manifest.get("validation_result")=="PASS" and
        manifest.get("source_sha256")==source_sha and manifest.get("sample_count")==assessment.get("presentation_sample_count") and
        manifest.get("source_canonical_pcm_sha256")==assessment.get("presentation_pcm_s32le_sha256") and
        manifest.get("aac_access_unit_essence_sha256")==assessment.get("aac_access_unit_essence_sha256"))


def _reuse(source:Path,source_sha:str,assessment:dict):
    for sidecar in source.parent.glob("*.lossydoctor-manifest.json"):
        try:manifest=json.loads(sidecar.read_text(encoding="utf-8"))
        except Exception:continue
        if not _manifest_matches(manifest,source_sha,assessment):continue
        output=Path(manifest.get("output_path",""))
        try:local_output=output.resolve().parent==source.resolve().parent
        except Exception:local_output=False
        if local_output and sidecar.resolve()==Path(str(output)+".lossydoctor-manifest.json").resolve() and output.exists() and sha256_file(output)==manifest.get("output_sha256"):
            return {"status":"REUSED","output_path":str(output),"manifest_path":str(sidecar),"manifest":manifest}
    return None


def export(source:Path,source_sha:str,ffmpeg:str,assessment:dict,publish=True,timeout=300):
    public=_public(assessment)
    if not assessment.get("eligible"):return {"status":"NOT_ELIGIBLE","assessment":public,"outputs":[]}
    plan=assessment.get("_private_plan") or {}
    if not plan:return {"status":"REJECTED","reason":"el plan privado de recuperación no está disponible","assessment":public,"outputs":[]}
    reused=_reuse(source,source_sha,public)
    if reused:return {"status":"REUSED","assessment":public,"outputs":[reused]}
    if sha256_file(source)!=source_sha:return {"status":"REJECTED","reason":"la fuente cambió antes de la exportación","assessment":public,"outputs":[]}
    with tempfile.TemporaryDirectory(prefix="lossydoctor-mp4-aac-recovery-") as directory:
        temporary=Path(directory);candidate=_decode_candidate(source,plan,ffmpeg,temporary,timeout)
        if not candidate.get("passed"):return {"status":"REJECTED","reason":candidate.get("reason"),"assessment":public,"outputs":[]}
        if candidate.get("aac_access_unit_essence_sha256")!=assessment.get("aac_access_unit_essence_sha256") or candidate.get("presentation_pcm_s32le_sha256")!=assessment.get("presentation_pcm_s32le_sha256"):
            return {"status":"REJECTED","reason":"la evidencia del candidato cambió desde la evaluación","assessment":public,"outputs":[]}
        raw=temporary/"canonical.s32le";_copy_range(Path(candidate["raw_path"]),raw,candidate["presentation_byte_start"],candidate["presentation_byte_length"])
        if sha256_file(raw)!=assessment.get("presentation_pcm_s32le_sha256"):
            return {"status":"REJECTED","reason":"el hash del segmento PCM canónico no coincide","assessment":public,"outputs":[]}
        flac=temporary/"candidate.flac";encoded=raw_file_to_flac(raw,flac,ffmpeg,plan["sample_rate"],plan["channels"],timeout)
        if not encoded.get("passed"):return {"status":"REJECTED","reason":"falló la codificación FLAC","detail":encoded,"assessment":public,"outputs":[]}
        back=temporary/"back.s32le";decoded=decode_to_raw_file(flac,back,ffmpeg,timeout)
        if not decoded.get("passed") or back.stat().st_size!=raw.stat().st_size or sha256_file(back)!=sha256_file(raw):
            return {"status":"REJECTED","reason":"la verificación round-trip del PCM FLAC no coincide","assessment":public,"outputs":[]}
        if sha256_file(source)!=source_sha:return {"status":"REJECTED","reason":"la fuente cambió durante la exportación","assessment":public,"outputs":[]}
        desired=source.with_name(source.stem+" [recovered-lossless].flac")
        output=flac
        manifest={"schema_version":3,"producer":"LossyDoctor","producer_version":APP_VERSION,"derivation_schema":1,
            "derivation_kind":DERIVATION_KIND,"materialization":MATERIALIZATION,"recovery_policy":EXPORT_POLICY,
            "source_pcm_recovery_class":"COMPLETE_CLEAN","source_path":str(source),"source_sha256":source_sha,
            "output_path":str(output),"output_sha256":sha256_file(output),"canonical_pcm_profile":canonical_pcm_profile(ffmpeg,plan["sample_rate"],plan["channels"]),
            "canonical_presentation_window":{"determined":True,"presentation_pcm_start_sample":plan["pcm_start"],"presentation_pcm_end_sample":plan["pcm_end"],"presentation_sample_count":assessment["presentation_sample_count"]},
            "sample_count":assessment["presentation_sample_count"],"source_canonical_pcm_sha256":assessment["presentation_pcm_s32le_sha256"],
            "flac_canonical_pcm_sha256":sha256_file(back),"flac_decoded_pcm_sha256":sha256_file(back),
            "aac_access_unit_count":assessment["access_unit_count"],"aac_access_unit_bytes":assessment["aac_access_unit_bytes"],
            "aac_access_unit_essence_sha256":assessment["aac_access_unit_essence_sha256"],"aac_access_unit_bytes_modified":False,
            "temporary_transport":assessment["temporary_transport"],"temporary_strict_decode":"PASS","synthesized_gap_silence":[],
            "resampling":"NONE","channel_remix":"NONE","validation_result":"PASS","source_modified":False,"audio_recoding":"LOSSLESS_FLAC_ONLY"}
        output,sidecar,manifest,publication_status=publish_or_preview_with_manifest(flac,desired,manifest,publish)
        return {"status":publication_status,"assessment":public,"outputs":[{"status":publication_status,"output_path":str(output) if output is not None else None,"manifest_path":str(sidecar) if sidecar else None,"manifest":manifest}]}
