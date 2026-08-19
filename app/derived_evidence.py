from __future__ import annotations

from collections import defaultdict


def _value(issue,key,default=None):
    return issue.get(key,default) if isinstance(issue,dict) else getattr(issue,key,default)


def build_pattern_analysis(issues):
    """Resume hallazgos observados repetidos sin alterar su significado ni política."""
    grouped=defaultdict(list)
    for issue in issues:grouped[_value(issue,'code','UNKNOWN')].append(issue)
    groups=[]
    for code,occurrences in sorted(grouped.items()):
        starts=[_value(issue,'byte_start') for issue in occurrences if _value(issue,'byte_start') is not None]
        ends=[_value(issue,'byte_end') for issue in occurrences if _value(issue,'byte_end') is not None]
        groups.append({'issue_code':code,'occurrence_count':len(occurrences),'observation':('ISOLATED' if len(occurrences)==1 else 'REPEATED'),'known_byte_range_count':len(starts),'first_byte_start':min(starts) if starts else None,'last_byte_end':max(ends) if ends else None,'policy_effect':'NONE_OBSERVATIONAL_ONLY'})
    return {'schema_version':1,'scope':'SINGLE_FILE','issue_count':sum(len(rows) for rows in grouped.values()),'distinct_issue_code_count':len(groups),'groups':groups,'conclusion':'OBSERVATIONAL_SUMMARY_ONLY_NO_POLICY_CHANGE'}


def build_causal_graph(issues,repair_execution=()):
    """Representa hallazgos y sólo dependencias de reparación verificadas tras el reanálisis."""
    codes=sorted({_value(issue,'code','UNKNOWN') for issue in issues})
    nodes=[{'id':f'issue:{code}','kind':'OBSERVED_ISSUE','issue_code':code} for code in codes];edges=[]
    for execution_index,execution in enumerate(repair_execution):
        if execution.get('status') not in ('CREATED','REUSED'):continue
        manifest=execution.get('manifest') or {};verification=manifest.get('verification') or execution.get('verification') or {}
        steps=manifest.get('chain_steps') or execution.get('chain_steps') or []
        for step_index,step in enumerate(steps):
            if step.get('status')!='PASS':continue
            node_id=f'repair:{execution_index}:{step_index+1}';nodes.append({'id':node_id,'kind':'VERIFIED_REPAIR_STEP','repair_spec_id':step.get('repair_spec_id'),'step':step.get('step',step_index+1)})
            for code in step.get('verification',{}).get('resolved_issue_codes') or []:
                edges.append({'from':node_id,'to':f'issue:{code}','relation':'VERIFIED_RESOLVES_AFTER_RESCAN'})
            if step_index and verification.get('incremental_rescan_after_each_step'):
                edges.append({'from':f'repair:{execution_index}:{step_index}','to':node_id,'relation':'VERIFIED_RESCAN_BEFORE_NEXT_STEP'})
    conclusion='VERIFIED_REPAIR_DEPENDENCIES_ONLY_NO_UNPROVEN_CAUSAL_RELATIONSHIP' if edges else 'NO_CAUSAL_RELATIONSHIP_PROVEN'
    return {'schema_version':1,'scope':'SINGLE_FILE','nodes':nodes,'edges':edges,'root_cause_candidates':[],'unresolved_observed_issue_codes':codes,'conclusion':conclusion}
