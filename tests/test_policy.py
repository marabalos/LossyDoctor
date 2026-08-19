import sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.models import Analysis,Issue
from policy.engine import classify
class Policy(unittest.TestCase):
 def test_playable_without_issues_is_ok(self):
  a=Analysis('x','x',{});a.playability='PLAYABLE';classify(a);self.assertEqual(a.final_status,['OK']);self.assertEqual(a.run_status,'SUCCESS')
 def test_observed_issue_remains_visible(self):
  i=Issue('TRAILING_UNKNOWN_REGION','structure','x',confidence='LOW',integrity='VALID',compatibility='NONE');a=Analysis('x','x',{});a.issues=[i];a.playability='PLAYABLE';classify(a);self.assertEqual(a.final_status,['ANOMALY_UNCHANGED'])
 def test_unplayable_not_unrecoverable(self):
  a=Analysis('x','x',{});a.playability='UNPLAYABLE';classify(a);self.assertEqual(a.final_status,['ANOMALY_UNCHANGED']);self.assertEqual(a.run_status,'SUCCESS_WITH_FINDINGS')
 def test_unknown_playability_fails_closed(self):
  a=Analysis('x','x',{});classify(a);self.assertEqual(a.final_status,['ANOMALY_UNCHANGED']);self.assertEqual(a.run_status,'SUCCESS_WITH_FINDINGS')
