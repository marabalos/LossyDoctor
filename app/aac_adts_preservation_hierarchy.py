from __future__ import annotations


POLICY="AAC_ADTS_PRESERVATION_HIERARCHY_STRICT_V1"
ORDER=["TIER_1_VERIFIED_ADTS_BITSTREAM_REPAIR","TIER_2_COMPLETE_CLEAN_LOSSLESS_RECOVERY","TIER_3_REPORT_ONLY_INSUFFICIENT_EVIDENCE"]
REPAIR_SPECS={"AAC_ADTS_REWRITE_UNIQUE_INVALID_SAMPLING_INDEX","AAC_ADTS_REMOVE_UNAMBIGUOUS_INTERFRAME_NONFRAME_BYTES"}
RECOVERY_MATERIALIZATION="AAC_ADTS_COMPLETE_CLEAN_FROM_PROVEN_HEADER_REPAIR"


def resolve(repair_execution:list[dict],lossless_export:list[dict],recovery_assessment:dict|None=None,playability=None,issue_codes=None):
    published=[x for x in repair_execution or [] if x.get("status") in ("CREATED","REUSED") and (x.get("manifest") or {}).get("derivation_kind")!="EXTENSION_FIXED"]
    repairs=[]
    for execution in published:
        manifest=execution.get("manifest") or {};verification=manifest.get("verification") or execution.get("verification") or {}
        spec=execution.get("repair_spec_id")
        specific=(spec=="AAC_ADTS_REWRITE_UNIQUE_INVALID_SAMPLING_INDEX" or
            (spec=="AAC_ADTS_REMOVE_UNAMBIGUOUS_INTERFRAME_NONFRAME_BYTES" and verification.get("frame_sequence_sha256_equal") and
             verification.get("aac_payload_sequence_sha256_equal") and verification.get("source_candidate_pcm_equal")))
        if (spec in REPAIR_SPECS and specific and manifest.get("derivation_kind")=="REPAIRED_SAFE" and
            manifest.get("validation_result")=="PASS" and verification.get("passed") and verification.get("strict_decode")=="PASS" and verification.get("frame_to_demux_packet_identity")):
            repairs.append(execution)
    lossless=[output for export in lossless_export or [] for output in export.get("outputs") or [] if output.get("status") in ("CREATED","REUSED")]
    recovery=[output for output in lossless if (output.get("manifest") or {}).get("derivation_kind")=="RECOVERED_LOSSLESS" and
        (output.get("manifest") or {}).get("materialization")==RECOVERY_MATERIALIZATION]
    unknown_repairs=[x for x in published if x not in repairs];unknown_lossless=[x for x in lossless if x not in recovery]
    families=[]
    if repairs:families.append(ORDER[0])
    if recovery:families.append(ORDER[1])
    codes=sorted(set(issue_codes or []));selected=families[0] if families else (ORDER[2] if codes else "NO_ACTION_REQUIRED")
    if unknown_repairs:violation="UNKNOWN_OR_UNVERIFIED_AAC_ADTS_REPAIR_FAMILY"
    elif unknown_lossless:violation="UNKNOWN_AAC_ADTS_PRESERVATION_DERIVATION_FAMILY"
    elif len(families)>1:violation="MULTIPLE_AAC_ADTS_PRESERVATION_TIERS_PUBLISHED_SIMULTANEOUSLY"
    else:violation=None
    rank=ORDER.index(selected)+1 if selected in ORDER else None
    status={"CREATED":0,"REUSED":0}
    for item in published+lossless:
        if item.get("status") in status:status[item["status"]]+=1
    return {"schema":1,"policy":POLICY,"order":ORDER,"selected_tier":selected,"selected_rank":rank,
        "exclusive_outcome":violation is None,"observed_tier_families":families,"selected_output_count":len(repairs) if selected==ORDER[0] else (len(recovery) if selected==ORDER[1] else 0),
        "status_counts":status,"suppressed_lower_tiers":ORDER[rank:] if rank else [],"playability":playability,
        "pcm_recovery_class":(recovery_assessment or {}).get("pcm_class"),"issue_codes":codes,"policy_violation":violation}
