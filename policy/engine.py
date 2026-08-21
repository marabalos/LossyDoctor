from __future__ import annotations

def classify(a):
    has_issue=bool(a.issues)
    created_repair=any(x.get('status')=='CREATED' for x in a.repair_execution)
    reused_repair=any(x.get('status')=='REUSED' for x in a.repair_execution)
    created_lossless=any(x.get('status')=='CREATED' for e in a.lossless_export for x in e.get('outputs',[]))
    reused_lossless=any(x.get('status')=='REUSED' for e in a.lossless_export for x in e.get('outputs',[]))
    deriv=[(e.get('outputs') or [{}])[0].get('manifest',{}).get('derivation_kind') for e in a.lossless_export if e.get('outputs')]
    if 'RECOVERED_WMA_PROVEN_REGION_LOSSLESS' in deriv:a.final_status=['RECOVERED_WMA_PROVEN_REGION_LOSSLESS'];a.run_status='SUCCESS_WITH_RECOVERY'
    elif 'RECOVERED_WMA_CONVERGED_SUFFIX_LOSSLESS' in deriv:a.final_status=['RECOVERED_WMA_CONVERGED_SUFFIX_LOSSLESS'];a.run_status='SUCCESS_WITH_RECOVERY'
    elif 'RECOVERED_VORBIS_PROVEN_REGION_LOSSLESS' in deriv:a.final_status=['RECOVERED_VORBIS_PROVEN_REGION_LOSSLESS'];a.run_status='SUCCESS_WITH_RECOVERY'
    elif 'RECOVERED_OPUS_PROVEN_REGION_LOSSLESS' in deriv:a.final_status=['RECOVERED_OPUS_PROVEN_REGION_LOSSLESS'];a.run_status='SUCCESS_WITH_RECOVERY'
    elif 'RECOVERED_HOMOGENEOUS_OPEN_PARTIAL_LOSSLESS' in deriv:a.final_status=['RECOVERED_HOMOGENEOUS_OPEN_PARTIAL_LOSSLESS'];a.run_status='SUCCESS_WITH_RECOVERY'
    elif 'RECOVERED_SEGMENTED_OPEN_PARTIAL_LOSSLESS' in deriv:a.final_status=['RECOVERED_SEGMENTED_OPEN_PARTIAL_LOSSLESS'];a.run_status='SUCCESS_WITH_RECOVERY'
    elif 'RECOVERED_SEGMENTED_PARTIAL_LOSSLESS' in deriv:a.final_status=['RECOVERED_SEGMENTED_PARTIAL_LOSSLESS'];a.run_status='SUCCESS_WITH_RECOVERY'
    elif 'RECOVERED_SEGMENTED_LOSSLESS' in deriv:a.final_status=['RECOVERED_SEGMENTED_LOSSLESS'];a.run_status='SUCCESS_WITH_RECOVERY'
    elif 'RECOVERED_PARTIAL_LOSSLESS' in deriv:a.final_status=['RECOVERED_PARTIAL_LOSSLESS'];a.run_status='SUCCESS_WITH_RECOVERY'
    elif 'RECOVERED_LOSSLESS' in deriv:a.final_status=['RECOVERED_LOSSLESS'];a.run_status='SUCCESS_WITH_RECOVERY'
    elif created_repair or reused_repair:
        residual=any(any((x.get('verification') or (x.get('manifest') or {}).get('verification') or {}).get(k) for k in ('post_repair_issue_codes','final_issue_codes','post_issue_codes','remaining_issue_codes')) for x in a.repair_execution if x.get('status') in ('CREATED','REUSED'))
        extension_only=any((x.get('manifest') or {}).get('derivation_kind')=='EXTENSION_FIXED' for x in a.repair_execution if x.get('status') in ('CREATED','REUSED'))
        if extension_only:residual=any(i.code!='EXTENSION_CONTENT_MISMATCH' for i in a.issues)
        a.final_status=['REPAIRED_SAFE']+(['ANOMALY_UNCHANGED'] if residual or any(x.get('status')=='BLOCKED' for x in a.repair_execution) else []);a.run_status='SUCCESS_WITH_REPAIR'
    # OK exige evidencia afirmativa de reproducción. Si falta o es desconocida,
    # la ambigüedad sigue la ruta de anomalía con cierre seguro.
    elif has_issue or a.playability!='PLAYABLE':a.final_status=['ANOMALY_UNCHANGED'];a.run_status='SUCCESS_WITH_FINDINGS'
    else:a.final_status=['OK'];a.run_status='SUCCESS'
    return a
