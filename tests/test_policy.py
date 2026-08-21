import sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.models import Analysis,Issue
from policy.engine import classify
class Policy(unittest.TestCase):
 def repaired(self,codes,kind='REPAIRED_SAFE',verification=None):
  a=Analysis('x','x',{});a.playability='PLAYABLE';a.issues=[Issue(c,'structure','x') for c in codes];a.repair_execution=[{'status':'CREATED','manifest':{'derivation_kind':kind,'verification':verification or {}}}];return classify(a)
 def test_extension_fix_only_resolves_extension_issue(self):
  self.assertEqual(self.repaired(['EXTENSION_CONTENT_MISMATCH'],'EXTENSION_FIXED').final_status,['REPAIRED_SAFE'])
  self.assertEqual(self.repaired(['EXTENSION_CONTENT_MISMATCH','MPEG_SYNC_LOSS'],'EXTENSION_FIXED').final_status,['REPAIRED_SAFE','ANOMALY_UNCHANGED'])
 def test_fully_resolved_normal_repair_stays_repaired_safe(self):
  self.assertEqual(self.repaired(['X'],verification={'post_issue_codes':[]}).final_status,['REPAIRED_SAFE'])
 def test_playable_without_issues_is_ok(self):
  a=Analysis('x','x',{});a.playability='PLAYABLE';classify(a);self.assertEqual(a.final_status,['OK']);self.assertEqual(a.run_status,'SUCCESS')
 def test_observed_issue_remains_visible(self):
  i=Issue('TRAILING_UNKNOWN_REGION','structure','x',confidence='LOW',integrity='VALID',compatibility='NONE');a=Analysis('x','x',{});a.issues=[i];a.playability='PLAYABLE';classify(a);self.assertEqual(a.final_status,['ANOMALY_UNCHANGED'])
 def test_unplayable_not_unrecoverable(self):
  a=Analysis('x','x',{});a.playability='UNPLAYABLE';classify(a);self.assertEqual(a.final_status,['ANOMALY_UNCHANGED']);self.assertEqual(a.run_status,'SUCCESS_WITH_FINDINGS')
 def test_unknown_playability_fails_closed(self):
  a=Analysis('x','x',{});classify(a);self.assertEqual(a.final_status,['ANOMALY_UNCHANGED']);self.assertEqual(a.run_status,'SUCCESS_WITH_FINDINGS')
