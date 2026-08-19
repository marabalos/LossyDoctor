from __future__ import annotations

POLICY = 'MPEG_PRESERVATION_HIERARCHY_STRICT_V1'
ORDER = [
    'TIER_1_VERIFIED_BITSTREAM_REPAIR',
    'TIER_2_COMPLETE_LOSSLESS_RECOVERY',
    'TIER_3_EXACT_TIMELINE_PARTIAL_RECOVERY',
    'TIER_4_COHERENT_SEGMENTED_RECOVERY',
    'TIER_5_BRACKETED_SEGMENTED_PARTIAL_RECOVERY',
    'TIER_6_OPEN_PROVEN_REGION_RECOVERY',
]

_KIND_TO_TIER = {
    'RECOVERED_LOSSLESS': ORDER[1],
    'RECOVERED_SEGMENTED_LOSSLESS': ORDER[3],
    'RECOVERED_SEGMENTED_PARTIAL_LOSSLESS': ORDER[4],
    'RECOVERED_SEGMENTED_OPEN_PARTIAL_LOSSLESS': ORDER[5],
    'RECOVERED_HOMOGENEOUS_OPEN_PARTIAL_LOSSLESS': ORDER[5],
}


def _verified_repaired_safe(executions:list[dict]):
    out=[]
    for ex in executions or []:
        if ex.get('status') not in ('CREATED','REUSED'):
            continue
        man=ex.get('manifest') or {}
        if man.get('derivation_kind')!='REPAIRED_SAFE':
            continue
        v=man.get('verification') or ex.get('verification') or {}
        if v.get('passed') and v.get('strict_decode')=='PASS' and v.get('playback_decode')=='PASS' and v.get('ffprobe')=='PASS':
            out.append(ex)
    return out


def _lossless_outputs(exports:list[dict]):
    return [o for e in (exports or []) for o in (e.get('outputs') or []) if o.get('status') in ('CREATED','REUSED')]


def _tier_for_output(o:dict):
    m=o.get('manifest') or {}; kind=m.get('derivation_kind')
    if kind=='RECOVERED_PARTIAL_LOSSLESS':
        if m.get('materialization')=='TIMELINE_PRESERVED_ZERO_GAPS':
            return ORDER[2]
        # La recuperación en partes independientes es una alternativa de regiones demostradas abiertas.
        return ORDER[5]
    return _KIND_TO_TIER.get(kind)


def resolve(repair_execution:list[dict], lossless_export:list[dict], recovery_assessment:dict|None=None, playability:str|None=None):
    """Resuelve el resultado de preservación realmente publicado o reutilizado.

    Esta función es observacional: nunca concede autoridad de reparación o recuperación.
    Explicita la precedencia ya aplicada y detecta la coexistencia accidental de
    familias de preservación incompatibles.
    """
    repairs=_verified_repaired_safe(repair_execution)
    loss=_lossless_outputs(lossless_export)
    families=[]
    if repairs:
        families.append(ORDER[0])
    for o in loss:
        t=_tier_for_output(o)
        if t and t not in families:
            families.append(t)
    ranked=sorted(families,key=ORDER.index) if families else []
    selected=ranked[0] if ranked else 'NO_DERIVATION_SELECTED'
    exclusive=len(ranked)<=1
    selected_rank=(ORDER.index(selected)+1) if selected in ORDER else None
    suppressed=ORDER[selected_rank:] if selected_rank is not None else []
    kinds=sorted({(o.get('manifest') or {}).get('derivation_kind') for o in loss if (o.get('manifest') or {}).get('derivation_kind')})
    status_counts={'CREATED':0,'REUSED':0}
    for x in repairs+loss:
        if x.get('status') in status_counts:status_counts[x['status']]+=1
    if selected==ORDER[0]:
        reason='la reparación verificada sin pérdida del bitstream superó la validación estructural y de decodificación posterior; se suprimen todas las alternativas PCM'
    elif selected==ORDER[1]:
        reason='ninguna reparación verificada de bitstream prevaleció; el PCM canónico completo y limpio puede preservarse sin pérdida'
    elif selected==ORDER[2]:
        reason='la recuperación completa no está disponible; se conoce la línea de tiempo canónica exacta y los intervalos dañados se representan sólo mediante marcadores explícitos de silencio cero'
    elif selected==ORDER[3]:
        reason='la geometría PCM del stream completo es heterogénea, pero cada segmento coherente de perfil nativo es recuperable de manera independiente'
    elif selected==ORDER[4]:
        reason='no todos los segmentos coherentes completos son recuperables; permanecen regiones limpias demostrables de perfil nativo delimitadas alrededor del daño'
    elif selected==ORDER[5]:
        reason='los niveles de recuperación de mayor cobertura no están disponibles; sólo se publican regiones abiertas o nativas demostradas independientemente, sin afirmar una línea de tiempo completa'
    else:
        reason='no se publicó ni reutilizó ningún derivado de preservación verificado'
    return {
        'schema':1,'policy':POLICY,'order':ORDER,'selected_tier':selected,'selected_rank':selected_rank,
        'exclusive_outcome':exclusive,'observed_tier_families':ranked,'observed_lossless_derivation_kinds':kinds,
        'selected_output_count':len(repairs) if selected==ORDER[0] else len(loss),
        'status_counts':status_counts,'suppressed_lower_tiers':suppressed,'selection_reason':reason,
        'playability':playability,'pcm_recovery_class':(recovery_assessment or {}).get('pcm_class'),
        'policy_violation':None if exclusive else 'MULTIPLE_PRESERVATION_TIERS_PUBLISHED_SIMULTANEOUSLY',
    }
