from __future__ import annotations
import os,shutil,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
from app.config import load_config
from app.pipeline import analyze_file
from app.utils import sha256_file
CFG=load_config(ROOT/'config.toml');FFMPEG=os.environ.get('LOSSYDOCTOR_FFMPEG') or shutil.which('ffmpeg');FFPROBE=os.environ.get('LOSSYDOCTOR_FFPROBE') or shutil.which('ffprobe')
@unittest.skipUnless(FFMPEG and FFPROBE,'ffmpeg/ffprobe required')
class V10CausalRegression(unittest.TestCase):
 def setUp(self):
  self.td=tempfile.TemporaryDirectory();self.d=Path(self.td.name)
  for p in (ROOT/'samples/causal_chain_v10').glob('*.mp3'):shutil.copy2(p,self.d/p.name)
 def tearDown(self):self.td.cleanup()
 def A(self,n):
  p=self.d/n;h=sha256_file(p);a=analyze_file(p,CFG,ROOT,FFMPEG,FFPROBE);self.assertEqual(sha256_file(p),h);return a
 def test_fixture_hashes_match_accepted_windows_corpus(self):
  self.assertEqual(sha256_file(self.d/'01_id3_then_header_chain.mp3'),'ec6cdec51c6e3bd4f46c9dd93838dc39a34638ad965be1031ae73595f8283a49');self.assertEqual(sha256_file(self.d/'02_xing_coupled_negative.mp3'),'97572ab1984848f98a6f7c7244d13fbe663f1e315faaa09308b36ec5f631621d')
 def test_two_step_chain_replans_and_matches_master(self):
  a=self.A('01_id3_then_header_chain.mp3');initial=[p for p in a.repair_plan if p.get('chain_iteration')==0];follow=[p for p in a.repair_plan if p.get('chain_iteration')==1];self.assertTrue(any(p['spec']['id']=='REPAIR_ID3V24_SIZE_TO_VERIFIED_BOUNDARY' and p['status']=='ELIGIBLE' for p in initial));self.assertTrue(any(p['spec']['id']=='LOSSLESS_SINGLE_BIT_HEADER_REPAIR' and p['status']=='BLOCKED' for p in initial));self.assertTrue(any(p['spec']['id']=='LOSSLESS_SINGLE_BIT_HEADER_REPAIR' and p['status']=='ELIGIBLE' for p in follow));e=next(e for e in a.repair_execution if e.get('status')=='CREATED');m=e['manifest'];self.assertEqual(e['repair_spec_id'],'CAUSAL_REPAIR_CHAIN');self.assertEqual(m['applied_repair_specs'],['REPAIR_ID3V24_SIZE_TO_VERIFIED_BOUNDARY','LOSSLESS_SINGLE_BIT_HEADER_REPAIR']);self.assertEqual(m['verification']['final_issue_codes'],[]);self.assertEqual(sha256_file(Path(e['output_path'])),sha256_file(self.d/'00_healthy_master_no_xing.mp3'));self.assertEqual(a.lossless_export,[])
 def test_coupled_xing_damage_stays_blocked(self):
  a=self.A('02_xing_coupled_negative.mp3');self.assertEqual(a.final_status,['ANOMALY_UNCHANGED']);self.assertTrue(any(e.get('repair_spec_id')=='REFRESH_XING_METADATA' and e.get('status')=='BLOCKED' for e in a.repair_execution))
 def test_repaired_chain_output_reanalyzes_as_ok(self):
  a=self.A('01_id3_then_header_chain.mp3');e=next(e for e in a.repair_execution if e.get('status')=='CREATED');out=Path(e['output_path']);b=analyze_file(out,CFG,ROOT,FFMPEG,FFPROBE);self.assertEqual(b.final_status,['OK'])
 def test_second_run_reuses_chain_without_duplicate(self):
  self.A('01_id3_then_header_chain.mp3');before=sorted(p.name for p in self.d.glob('*repaired*.mp3'));a=self.A('01_id3_then_header_chain.mp3');self.assertTrue(any(e.get('status')=='REUSED' and e.get('repair_spec_id')=='CAUSAL_REPAIR_CHAIN' for e in a.repair_execution));self.assertEqual(before,sorted(p.name for p in self.d.glob('*repaired*.mp3')))
