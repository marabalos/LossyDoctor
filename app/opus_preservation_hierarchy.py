from __future__ import annotations

POLICY = 'OPUS_PRESERVATION_HIERARCHY_STRICT_V1'
ORDER = [
    'TIER_1_VERIFIED_OGG_BITSTREAM_REPAIR',
    'TIER_2_PROVEN_REGION_RECOVERY_AUTHENTICATED_EOS',
    'TIER_3_OPEN_OR_TRUNCATED_PROVEN_REGION_RECOVERY',
    'TIER_4_REPORT_ONLY_INSUFFICIENT_EVIDENCE',
]

_ALLOWED_REPAIR_SPECS = {'OGG_RECAPTURE_VALID_PAGES_DROP_EXTRANEOUS_BYTES'}
_ALLOWED_RECOVERY_KIND = 'RECOVERED_OPUS_PROVEN_REGION_LOSSLESS'


def _verified_ogg_repairs(executions: list[dict]) -> list[dict]:
    out = []
    for ex in executions or []:
        if ex.get('status') not in ('CREATED', 'REUSED'):
            continue
        if ex.get('repair_spec_id') not in _ALLOWED_REPAIR_SPECS:
            continue
        man = ex.get('manifest') or {}
        if man.get('derivation_kind') != 'REPAIRED_SAFE':
            continue
        v = man.get('verification') or ex.get('verification') or {}
        if v.get('passed') and v.get('strict_decode') == 'PASS' and v.get('playback_decode') == 'PASS' and v.get('ffprobe') == 'PASS':
            out.append(ex)
    return out


def _lossless_outputs(exports: list[dict]) -> list[dict]:
    return [
        o
        for e in (exports or [])
        for o in (e.get('outputs') or [])
        if o.get('status') in ('CREATED', 'REUSED')
    ]


def resolve(
    repair_execution: list[dict],
    lossless_export: list[dict],
    recovery_assessment: dict | None = None,
    playability: str | None = None,
    issue_codes: list[str] | set[str] | tuple[str, ...] | None = None,
):
    """Resuelve el resultado publicado o reutilizado de preservación Opus.

    Es sólo observacional: este módulo no concede autoridad de reparación o recuperación.
    El proceso ya aplicó las puertas de elegibilidad y verificación. El resolutor
    explicita la precedencia y falla de forma cerrada si coexisten familias de salida
    incompatibles.
    """
    repairs = _verified_ogg_repairs(repair_execution)
    loss = _lossless_outputs(lossless_export)
    codes = sorted(set(issue_codes or []))

    recovery_outputs = [
        o for o in loss
        if (o.get('manifest') or {}).get('derivation_kind') == _ALLOWED_RECOVERY_KIND
    ]
    unknown_lossless = [
        (o.get('manifest') or {}).get('derivation_kind')
        for o in loss
        if (o.get('manifest') or {}).get('derivation_kind') != _ALLOWED_RECOVERY_KIND
    ]

    families: list[str] = []
    if repairs:
        families.append(ORDER[0])
    recovery_tier = None
    if recovery_outputs:
        # Familia a nivel de origen: una recuperación en varias partes puede contener un prefijo sin
        # EOS y una cola que alcanza un EOS autenticado. Si alguna región publicada
        # Si una región alcanza ese EOS autenticado con recorte exacto, la familia del origen
        # es Tier 2; de lo contrario, todas las regiones son abiertas o truncadas Tier 3.
        has_exact_authenticated_eos = any(
            bool((o.get('manifest') or {}).get('includes_authenticated_eos'))
            and (o.get('manifest') or {}).get('eos_end_trim_samples_48k') is not None
            for o in recovery_outputs
        )
        recovery_tier = ORDER[1] if has_exact_authenticated_eos else ORDER[2]
        families.append(recovery_tier)

    ranked = sorted(set(families), key=ORDER.index) if families else []
    exclusive = len(ranked) <= 1 and not unknown_lossless
    if ranked:
        selected = ranked[0]
    elif codes:
        selected = ORDER[3]
    else:
        selected = 'NO_ACTION_REQUIRED'
    selected_rank = ORDER.index(selected) + 1 if selected in ORDER else None

    status_counts = {'CREATED': 0, 'REUSED': 0}
    for x in repairs + loss:
        if x.get('status') in status_counts:
            status_counts[x['status']] += 1

    if selected == ORDER[0]:
        reason = 'la reparación verificada por recaptura de páginas Ogg superó la validación estructural y de decodificación completa posterior; se suprimen todas las alternativas PCM'
    elif selected == ORDER[1]:
        reason = 'ninguna reparación verificada de bitstream prevaleció; las regiones de paquetes Opus completos autenticados por CRC son recuperables y al menos una alcanza un EOS autenticado con recorte final exacto'
    elif selected == ORDER[2]:
        reason = 'los niveles superiores no están disponibles; sólo se publican regiones de paquetes Opus completos autenticados por CRC con cobertura abierta o truncada, sin afirmar una línea de tiempo completa'
    elif selected == ORDER[3]:
        reason = 'persiste una anomalía, pero la evidencia no autoriza una reparación segura del bitstream ni una salida de preservación PCM según la política Opus V1'
    else:
        reason = 'el stream es conforme y no requiere un derivado de preservación'

    if repairs:
        selected_output_count = len(repairs) if selected == ORDER[0] else 0
    elif recovery_outputs and selected in (ORDER[1], ORDER[2]):
        selected_output_count = len(recovery_outputs)
    else:
        selected_output_count = 0

    if unknown_lossless:
        violation = 'UNKNOWN_OPUS_PRESERVATION_DERIVATION_FAMILY'
    elif len(ranked) > 1:
        violation = 'MULTIPLE_OPUS_PRESERVATION_TIERS_PUBLISHED_SIMULTANEOUSLY'
    else:
        violation = None

    return {
        'schema': 1,
        'policy': POLICY,
        'order': ORDER,
        'selected_tier': selected,
        'selected_rank': selected_rank,
        'exclusive_outcome': exclusive,
        'observed_tier_families': ranked,
        'selected_output_count': selected_output_count,
        'status_counts': status_counts,
        'suppressed_lower_tiers': ORDER[selected_rank:] if selected_rank is not None else [],
        'selection_reason': reason,
        'playability': playability,
        'pcm_recovery_class': (recovery_assessment or {}).get('pcm_class'),
        'issue_codes': codes,
        'authenticated_eos_recovery_present': bool(recovery_tier == ORDER[1]),
        'observed_lossless_derivation_kinds': sorted({
            (o.get('manifest') or {}).get('derivation_kind') for o in loss
            if (o.get('manifest') or {}).get('derivation_kind')
        }),
        'policy_violation': violation,
    }
