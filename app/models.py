from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any

@dataclass
class Issue:
    code:str; layer:str; description:str
    evidence_nature:str='OBJECTIVE'; confidence:str='HIGH'; integrity:str='SUSPICIOUS'
    compatibility:str='POSSIBLE'; playability:str='UNAFFECTED'; repairability:str='NONE'
    byte_start:int|None=None; byte_end:int|None=None; evidence:list[Any]=field(default_factory=list)
    def to_dict(self): return asdict(self)

@dataclass
class Analysis:
    input_path:str; display_name:str; filesystem:dict[str,Any]
    run_status:str='PENDING'; skipped_reason:str|None=None; error:str|None=None
    detected_container:str|None=None; detected_codec:str|None=None; expected_extension:str|None=None; format_confidence:str='LOW'
    identity:dict=field(default_factory=dict); structural_map:list[dict]=field(default_factory=list); issues:list[Issue]=field(default_factory=list)
    causal_graph:dict=field(default_factory=dict); pattern_analysis:dict=field(default_factory=dict)
    metadata:dict=field(default_factory=dict); format_facts:dict=field(default_factory=dict); decode_results:dict=field(default_factory=dict)
    canonical_pcm_profile:dict=field(default_factory=dict); canonical_presentation_window:dict=field(default_factory=dict)
    playability:str|None=None; pcm_recovery_class:str='NOT_ASSESSED'; recovery_assessment:dict=field(default_factory=dict)
    validity_domains:dict=field(default_factory=dict); repair_plan:list[dict]=field(default_factory=list); repair_execution:list[dict]=field(default_factory=list)
    lossless_export:list[dict]=field(default_factory=list); policy_decisions:list[dict]=field(default_factory=list); final_status:list[str]=field(default_factory=list); events:list[dict]=field(default_factory=list)
    def to_dict(self):
        d=asdict(self); d['issues']=[x.to_dict() if hasattr(x,'to_dict') else x for x in self.issues]; return d
