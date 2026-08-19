from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

from app.config import load_config
from app.derived_evidence import build_causal_graph,build_pattern_analysis
from app.models import Issue

ROOT=Path(__file__).resolve().parents[1]


class DerivedEvidenceFoundationCP45(unittest.TestCase):
    def test_pattern_groups_observed_issue_codes_without_changing_policy(self):
        patterns=build_pattern_analysis([Issue('SYNC','framing','x',byte_start=10,byte_end=12),Issue('SYNC','framing','x',byte_start=30,byte_end=34),Issue('TRUNCATED','framing','x')])
        self.assertEqual(patterns['conclusion'],'OBSERVATIONAL_SUMMARY_ONLY_NO_POLICY_CHANGE')
        self.assertEqual(patterns['groups'],[{'issue_code':'SYNC','occurrence_count':2,'observation':'REPEATED','known_byte_range_count':2,'first_byte_start':10,'last_byte_end':34,'policy_effect':'NONE_OBSERVATIONAL_ONLY'},{'issue_code':'TRUNCATED','occurrence_count':1,'observation':'ISOLATED','known_byte_range_count':0,'first_byte_start':None,'last_byte_end':None,'policy_effect':'NONE_OBSERVATIONAL_ONLY'}])

    def test_causal_graph_never_infers_an_unproven_relationship(self):
        graph=build_causal_graph([Issue('SYNC','framing','x'),Issue('ID3','metadata','x')])
        self.assertEqual(graph['edges'],[])
        self.assertEqual(graph['root_cause_candidates'],[])
        self.assertEqual(graph['unresolved_observed_issue_codes'],['ID3','SYNC'])
        self.assertEqual(graph['conclusion'],'NO_CAUSAL_RELATIONSHIP_PROVEN')

    def test_causal_graph_records_only_verified_repair_dependencies(self):
        execution={'status':'CREATED','manifest':{'verification':{'incremental_rescan_after_each_step':True},'chain_steps':[{'step':1,'repair_spec_id':'FIX_TAG','status':'PASS','verification':{'resolved_issue_codes':['ID3']}},{'step':2,'repair_spec_id':'FIX_HEADER','status':'PASS','verification':{'resolved_issue_codes':['SYNC']}}]}}
        graph=build_causal_graph([Issue('ID3','metadata','x'),Issue('SYNC','framing','x')],[execution])
        self.assertEqual(graph['root_cause_candidates'],[])
        self.assertEqual(graph['conclusion'],'VERIFIED_REPAIR_DEPENDENCIES_ONLY_NO_UNPROVEN_CAUSAL_RELATIONSHIP')
        self.assertIn({'from':'repair:0:1','to':'issue:ID3','relation':'VERIFIED_RESOLVES_AFTER_RESCAN'},graph['edges'])
        self.assertIn({'from':'repair:0:1','to':'repair:0:2','relation':'VERIFIED_RESCAN_BEFORE_NEXT_STEP'},graph['edges'])

    def test_pipeline_serializes_derived_evidence_for_an_unsupported_input(self):
        from app.pipeline import analyze_file
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'unknown.bin';path.write_bytes(b'not audio')
            row=analyze_file(path,load_config(ROOT/'config.toml'),ROOT,'ffmpeg','ffprobe').to_dict()
        self.assertEqual(row['run_status'],'SKIPPED_UNSUPPORTED')
        self.assertEqual(row['pattern_analysis']['issue_count'],0)
        self.assertEqual(row['causal_graph']['conclusion'],'NO_CAUSAL_RELATIONSHIP_PROVEN')


if __name__=='__main__':unittest.main()
