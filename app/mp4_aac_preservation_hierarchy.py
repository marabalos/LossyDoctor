from __future__ import annotations


POLICY = "MP4_AAC_PRESERVATION_HIERARCHY_STRICT_V1"
ORDER = [
    "TIER_1_VERIFIED_AAC_BITSTREAM_REPAIR",
    "TIER_2_COMPLETE_CLEAN_LOSSLESS_RECOVERY",
    "TIER_3_REPORT_ONLY_INSUFFICIENT_EVIDENCE",
]

_REPAIR_SPECS = {
    "MP4_REWRITE_SINGLE_CHUNK_OFFSET_TO_UNIQUE_MDAT",
    "MP4_REWRITE_MDHD_DURATION_TO_COMPLETE_STTS",
    "MP4_REWRITE_STSZ_SAMPLE_COUNT_TO_PROVEN_PHYSICAL_TABLE",
    "MP4_REWRITE_STSC_DESCRIPTION_TO_UNIQUE_VALID_AAC_ENTRY",
}
_RECOVERY_KIND = "RECOVERED_LOSSLESS"
_RECOVERY_MATERIALIZATIONS = {
    "MP4_AAC_CANONICAL_PRESENTATION_FROM_BYTE_PRESERVED_ACCESS_UNITS",
    "MP4_AAC_CANONICAL_COMPLEX_PRESENTATION_FROM_AUTHENTICATED_ACCESS_UNITS",
}


def _published_repairs(executions: list[dict]) -> list[dict]:
    return [x for x in executions or [] if x.get("status") in ("CREATED", "REUSED") and (x.get("manifest") or {}).get("derivation_kind") != "EXTENSION_FIXED"]


def _verified_repairs(executions: list[dict]) -> list[dict]:
    verified = []
    for execution in _published_repairs(executions):
        manifest = execution.get("manifest") or {}
        verification = manifest.get("verification") or execution.get("verification") or {}
        if (
            execution.get("repair_spec_id") in _REPAIR_SPECS
            and manifest.get("derivation_kind") == "REPAIRED_SAFE"
            and verification.get("passed")
            and verification.get("strict_decode") == "PASS"
            and verification.get("playback_decode") == "PASS"
            and verification.get("ffprobe") == "PASS"
        ):
            verified.append(execution)
    return verified


def _lossless_outputs(exports: list[dict]) -> list[dict]:
    return [
        output
        for export in exports or []
        for output in export.get("outputs") or []
        if output.get("status") in ("CREATED", "REUSED")
    ]


def resolve(
    repair_execution: list[dict],
    lossless_export: list[dict],
    recovery_assessment: dict | None = None,
    playability: str | None = None,
    issue_codes: list[str] | set[str] | tuple[str, ...] | None = None,
):
    """Resuelve el resultado observado de preservación MP4/AAC sin otorgar autoridad."""
    published_repairs = _published_repairs(repair_execution)
    repairs = _verified_repairs(repair_execution)
    lossless = _lossless_outputs(lossless_export)
    codes = sorted(set(issue_codes or []))

    recovery = [
        output
        for output in lossless
        if (output.get("manifest") or {}).get("derivation_kind") == _RECOVERY_KIND
        and (output.get("manifest") or {}).get("materialization") in _RECOVERY_MATERIALIZATIONS
    ]
    unknown_repairs = [x for x in published_repairs if x not in repairs]
    unknown_lossless = [x for x in lossless if x not in recovery]

    families = []
    if repairs:
        families.append(ORDER[0])
    if recovery:
        families.append(ORDER[1])
    ranked = sorted(set(families), key=ORDER.index) if families else []

    if ranked:
        selected = ranked[0]
    elif codes:
        selected = ORDER[2]
    else:
        selected = "NO_ACTION_REQUIRED"
    rank = ORDER.index(selected) + 1 if selected in ORDER else None

    if unknown_repairs:
        violation = "UNKNOWN_OR_UNVERIFIED_MP4_AAC_REPAIR_FAMILY"
    elif unknown_lossless:
        violation = "UNKNOWN_MP4_AAC_PRESERVATION_DERIVATION_FAMILY"
    elif len(ranked) > 1:
        violation = "MULTIPLE_MP4_AAC_PRESERVATION_TIERS_PUBLISHED_SIMULTANEOUSLY"
    else:
        violation = None

    if selected == ORDER[0]:
        reason = "se reparó un único campo MP4 de direccionamiento o línea de tiempo demostrado, con esencia AAC idéntica en bytes y PCM canónico exacto; se suprime la alternativa PCM sin pérdida"
        selected_count = len(repairs)
    elif selected == ORDER[1]:
        reason = "ninguna reparación verificada de bitstream prevaleció; la presentación canónica completa se preservó en FLAC sin pérdida verificado a partir de unidades AAC autenticadas y cualquier silencio explícito de la línea de tiempo"
        selected_count = len(recovery)
    elif selected == ORDER[2]:
        reason = "persiste una anomalía MP4/AAC, pero la evidencia actual no autoriza una reparación segura del contenedor ni una recuperación completa y limpia sin pérdida"
        selected_count = 0
    else:
        reason = "la fuente MP4/AAC es conforme y no requiere un derivado de preservación"
        selected_count = 0

    status_counts = {"CREATED": 0, "REUSED": 0}
    for item in published_repairs + lossless:
        if item.get("status") in status_counts:
            status_counts[item["status"]] += 1

    return {
        "schema": 1,
        "policy": POLICY,
        "order": ORDER,
        "selected_tier": selected,
        "selected_rank": rank,
        "exclusive_outcome": violation is None,
        "observed_tier_families": ranked,
        "selected_output_count": selected_count,
        "status_counts": status_counts,
        "suppressed_lower_tiers": ORDER[rank:] if rank is not None else [],
        "selection_reason": reason,
        "playability": playability,
        "pcm_recovery_class": (recovery_assessment or {}).get("pcm_class"),
        "issue_codes": codes,
        "observed_lossless_derivation_kinds": sorted({
            (output.get("manifest") or {}).get("derivation_kind")
            for output in lossless
            if (output.get("manifest") or {}).get("derivation_kind")
        }),
        "policy_violation": violation,
    }
