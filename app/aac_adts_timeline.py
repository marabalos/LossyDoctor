from __future__ import annotations


POLICY="AAC_ADTS_EXACT_CONTIGUOUS_PCM_PRESENTATION"
SUPPORTED_CHANNEL_CONFIGURATIONS={1:1,2:2,3:3,4:4,5:5,6:6,7:8}


def _blocked(reason:str,**evidence):
    return {"policy":POLICY,"applicable":True,"validated":False,"presentation_exact":False,
        "intervention_authority":False,"repair_authority":"NONE","pcm_recovery_authority":"NONE","reason":reason,**evidence}


def assess(parsed:dict,demux:dict,decoder:dict,strict:dict):
    facts=parsed.get("facts") or {};adts=facts.get("adts") or {};frames=facts.get("frames") or [];issues=parsed.get("issues") or []
    if not frames:return _blocked("NO_COMPLETE_ADTS_FRAMES")
    rates=adts.get("sample_rates_hz") or [];configs=adts.get("channel_configurations") or [];objects=adts.get("object_types") or []
    if len(rates)!=1 or len(configs)!=1 or len(objects)!=1:return _blocked("HOMOGENEOUS_STREAM_PARAMETERS_REQUIRED")
    channel_count=SUPPORTED_CHANNEL_CONFIGURATIONS.get(configs[0])
    if objects[0]!=2 or not channel_count:return _blocked("AAC_LC_WITH_EXPLICIT_CHANNEL_CONFIGURATION_REQUIRED")
    if adts.get("raw_data_blocks_values")!=[1]:return _blocked("ONE_RAW_DATA_BLOCK_PER_FRAME_REQUIRED")
    if issues:return _blocked("STRUCTURAL_OR_PROTECTION_FINDINGS_PRESENT",blocking_issue_codes=[issue.code for issue in issues])
    if not adts.get("all_complete_frames_physically_contiguous"):return _blocked("COMPLETE_CONTIGUOUS_FRAME_SEQUENCE_REQUIRED")
    if not demux.get("all_equal"):return _blocked("DIRECT_FRAME_TO_DEMUX_PACKET_IDENTITY_REQUIRED")
    if not strict.get("passed"):return _blocked("STRICT_FULL_STREAM_DECODE_REQUIRED")
    expected=adts.get("header_sample_count_total")
    if not decoder.get("completed") or not decoder.get("pcm_sha256") or decoder.get("sample_frames")!=expected:
        return _blocked("CANONICAL_PCM_SAMPLE_COUNT_DISAGREES_WITH_ADTS_FRAMES",header_sample_count=expected,decoder_sample_count=decoder.get("sample_frames"))
    protected=adts.get("crc_present_frame_count",0)>0
    crc_complete=adts.get("single_rdb_crc_authentication_deferred_count",0)==0
    return {"policy":POLICY,"applicable":True,"validated":True,"presentation_exact":True,
        "presentation_model":"CONTIGUOUS_HOMOGENEOUS_AAC_LC_ADTS_FRAMES","presentation_sample_count":expected,
        "sample_rate":rates[0],"channel_configuration":configs[0],"channels":channel_count,
        "complete_frame_count":len(frames),"canonical_pcm_s32le_sha256":decoder["pcm_sha256"],
        "frame_to_demux_packet_identity":True,"strict_full_stream_decode":"PASS",
        "crc_protected_frames_present":protected,"crc_payload_authentication_complete":crc_complete,
        "payload_integrity_scope":"CRC_SCOPE_INCOMPLETE_NO_RECOVERY_AUTHORITY" if protected and not crc_complete else "STRICT_DECODED_NO_DETECTED_PAYLOAD_ERROR",
        "intervention_authority":False,"repair_authority":"NONE","pcm_recovery_authority":"NONE",
        "reason":"EXACT_CONTIGUOUS_ADTS_PCM_PRESENTATION_AUDIT_ONLY"}
