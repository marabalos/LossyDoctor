from __future__ import annotations

POLICY = 'WMA_PRESERVATION_HIERARCHY_STRICT_V1'
ORDER = [
    'TIER_1_PROVEN_MULTI_REGION_RECOVERY',
    'TIER_2_PROVEN_CONVERGED_SUFFIX_RECOVERY',
    'TIER_3_REPORT_ONLY_INSUFFICIENT_EVIDENCE',
]

_MULTI_KIND = 'RECOVERED_WMA_PROVEN_REGION_LOSSLESS'
_SUFFIX_KIND = 'RECOVERED_WMA_CONVERGED_SUFFIX_LOSSLESS'
_ALLOWED = {_MULTI_KIND, _SUFFIX_KIND}


def _lossless_outputs(exports: list[dict]) -> list[dict]:
    return [
        o
        for e in (exports or [])
        for o in (e.get('outputs') or [])
        if o.get('status') in ('CREATED', 'REUSED')
    ]


def resolve(
    lossless_export: list[dict],
    recovery_assessment: dict | None = None,
    playability: str | None = None,
    issue_codes: list[str] | set[str] | tuple[str, ...] | None = None,
):
    """Resuelve la familia WMA de preservación publicada o reutilizada.

    Es sólo observacional y no concede autoridad nueva: la elegibilidad, la
    convergencia, los hashes de regiones y el round-trip FLAC ya se verificaron.
    """
    loss = _lossless_outputs(lossless_export)
    codes = sorted(set(issue_codes or []))
    multi = [o for o in loss if (o.get('manifest') or {}).get('derivation_kind') == _MULTI_KIND]
    suffix = [o for o in loss if (o.get('manifest') or {}).get('derivation_kind') == _SUFFIX_KIND]
    unknown = sorted({
        (o.get('manifest') or {}).get('derivation_kind')
        for o in loss
        if (o.get('manifest') or {}).get('derivation_kind') not in _ALLOWED
    }, key=lambda x: '' if x is None else str(x))

    families=[]
    if multi: families.append(ORDER[0])
    if suffix: families.append(ORDER[1])
    ranked=sorted(set(families), key=ORDER.index) if families else []
    exclusive=(len(ranked)<=1 and not unknown)

    if ranked:
        selected=ranked[0]
    elif codes:
        selected=ORDER[2]
    else:
        selected='NO_ACTION_REQUIRED'
    rank=ORDER.index(selected)+1 if selected in ORDER else None

    if selected==ORDER[0]:
        reason='dos o más secuencias de objetos multimedia ausentes demostradas independientemente se preservan como regiones limpias separadas de decodificación canónica con hash; se suprimen los niveles inferiores de sufijo único o sólo reporte'
        selected_count=len(multi)
    elif selected==ORDER[1]:
        reason='exactamente una secuencia demostrada de objetos multimedia ausentes tiene convergencia determinista del decodificador canónico después de un objeto de contexto superviviente; sólo se preserva el sufijo convergente verificado'
        selected_count=len(suffix)
    elif selected==ORDER[2]:
        reason='persiste una anomalía ASF/WMA, pero la evidencia WMA V1 actual no autoriza un derivado de preservación verificado'
        selected_count=0
    else:
        reason='el stream es conforme y no requiere un derivado de preservación'
        selected_count=0

    status={'CREATED':0,'REUSED':0}
    for o in loss:
        if o.get('status') in status: status[o['status']]+=1

    if unknown:
        violation='UNKNOWN_WMA_PRESERVATION_DERIVATION_FAMILY'
    elif len(ranked)>1:
        violation='MULTIPLE_WMA_PRESERVATION_TIERS_PUBLISHED_SIMULTANEOUSLY'
    else:
        violation=None

    return {
        'schema':1,
        'policy':POLICY,
        'order':ORDER,
        'selected_tier':selected,
        'selected_rank':rank,
        'exclusive_outcome':exclusive,
        'observed_tier_families':ranked,
        'selected_output_count':selected_count,
        'status_counts':status,
        'suppressed_lower_tiers':ORDER[rank:] if rank is not None else [],
        'selection_reason':reason,
        'playability':playability,
        'pcm_recovery_class':(recovery_assessment or {}).get('pcm_class'),
        'issue_codes':codes,
        'observed_lossless_derivation_kinds':sorted({
            (o.get('manifest') or {}).get('derivation_kind') for o in loss
            if (o.get('manifest') or {}).get('derivation_kind')
        }),
        'multi_region_output_count':len(multi),
        'converged_suffix_output_count':len(suffix),
        'policy_violation':violation,
    }
