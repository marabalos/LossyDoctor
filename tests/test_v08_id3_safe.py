from __future__ import annotations
import os,shutil,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
from app.config import load_config
from app.pipeline import analyze_file
from app.utils import sha256_file
CFG=load_config(ROOT/'config.toml');FFMPEG=os.environ.get('LOSSYDOCTOR_FFMPEG') or shutil.which('ffmpeg');FFPROBE=os.environ.get('LOSSYDOCTOR_FFPROBE') or shutil.which('ffprobe')
@unittest.skipUnless(FFMPEG and FFPROBE,'ffmpeg/ffprobe required')
class V08ID3Regression(unittest.TestCase):
 def setUp(self):
  self.td=tempfile.TemporaryDirectory();self.d=Path(self.td.name)
  for p in (ROOT/'samples/id3_repair_v08').glob('*.mp3'):shutil.copy2(p,self.d/p.name)
 def tearDown(self):self.td.cleanup()
 def A(self,n):
  p=self.d/n;h=sha256_file(p);a=analyze_file(p,CFG,ROOT,FFMPEG,FFPROBE);self.assertEqual(sha256_file(p),h);return a
 def test_repairable_id3_changes_only_size_and_matches_master(self):
  a=self.A('01_bad_id3_repairable.mp3');e=next(e for e in a.repair_execution if e.get('status')=='CREATED');m=e['manifest'];self.assertEqual(a.final_status,['REPAIRED_SAFE']);self.assertEqual(m['repair_spec_id'],'REPAIR_ID3V24_SIZE_TO_VERIFIED_BOUNDARY');self.assertEqual([(r['byte_start'],r['byte_end'],r['field']) for r in m['changed_byte_ranges']],[(6,10,'ID3V2_SIZE')]);self.assertEqual(sha256_file(Path(e['output_path'])),sha256_file(self.d/'00_healthy_master.mp3'));self.assertEqual(a.lossless_export,[])
 def test_ambiguous_boundary_is_blocked_and_falls_back_lossless(self):
  a=self.A('02_bad_id3_ambiguous.mp3');self.assertEqual(a.final_status,['RECOVERED_LOSSLESS']);self.assertTrue(any(e.get('status')=='BLOCKED' for e in a.repair_execution));self.assertEqual(a.lossless_export[0]['outputs'][0]['manifest']['derivation_kind'],'RECOVERED_LOSSLESS')
 def test_second_run_reuses_both_derivation_types(self):
  self.A('01_bad_id3_repairable.mp3');self.A('02_bad_id3_ambiguous.mp3');a=self.A('01_bad_id3_repairable.mp3');b=self.A('02_bad_id3_ambiguous.mp3');self.assertTrue(any(e.get('status')=='REUSED' for e in a.repair_execution));self.assertTrue(any(o.get('status')=='REUSED' for ex in b.lossless_export for o in ex.get('outputs',[])))
 def test_healthy_master_remains_ok(self):self.assertEqual(self.A('00_healthy_master.mp3').final_status,['OK'])
