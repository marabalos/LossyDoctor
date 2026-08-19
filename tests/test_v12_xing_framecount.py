from __future__ import annotations
import hashlib,os,shutil,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
from app.config import load_config
from app.pipeline import analyze_file
from app.utils import sha256_file
from formats.mpeg import analyze
CFG=load_config(ROOT/'config.toml');FFMPEG=os.environ.get('LOSSYDOCTOR_FFMPEG') or shutil.which('ffmpeg');FFPROBE=os.environ.get('LOSSYDOCTOR_FFPROBE') or shutil.which('ffprobe')

@unittest.skipUnless(FFMPEG and FFPROBE,'ffmpeg/ffprobe required')
class V12XingFrameCount(unittest.TestCase):
 def setUp(self):
  self.td=tempfile.TemporaryDirectory();self.d=Path(self.td.name)
  for p in (ROOT/'samples/xing_framecount_v12').glob('*.mp3'):shutil.copy2(p,self.d/p.name)
 def tearDown(self):self.td.cleanup()
 def A(self,n):
  p=self.d/n;h=sha256_file(p);a=analyze_file(p,CFG,ROOT,FFMPEG,FFPROBE);self.assertEqual(sha256_file(p),h,'source modified');return a
 def test_static_fixture_hashes(self):
  exp={'00_healthy_ffmpeg_xing.mp3':'12f614e3fc507471071c20426f511aa4698b8349e8356be2831e943adf7574a7','01_frame_count_provable.mp3':'a44289a0e1894c18d5ded2234fb64f71b1649af71ffd77769a33e787f65b83f2','02_frame_count_low_padding_unprovable.mp3':'951f8efb916eea95a0d23294a28ae2294b7ea8fc38c1fa8ecb81fc9411c6e272','03_structural_damage_negative.mp3':'97572ab1984848f98a6f7c7244d13fbe663f1e315faaa09308b36ec5f631621d'}
  for n,h in exp.items():self.assertEqual(sha256_file(self.d/n),h)
 def test_frame_count_repair_uses_independent_structural_presentation_proof(self):
  a=self.A('01_frame_count_provable.mp3');e=next(e for e in a.repair_execution if e.get('status')=='CREATED');v=e['manifest']['verification']
  self.assertEqual(e['repair_spec_id'],'REFRESH_XING_METADATA');self.assertEqual(a.final_status,['REPAIRED_SAFE']);self.assertEqual(v['pcm_identity_gate'],'STRUCTURAL_GAPLESS_PROOF')
  self.assertFalse(v['pcm_identical']);self.assertTrue(v['source_normal_decode_differs_due_to_bad_frame_count']);self.assertTrue(v['physical_pcm_identical']);self.assertTrue(v['structural_window_pcm_identical']);self.assertTrue(v['candidate_matches_structural_window']);self.assertTrue(v['presentation_equivalent_independent_of_declared_frame_count'])
  sp=v['source_structural_presentation_proof'];self.assertEqual((sp['physical_sample_count'],sp['expected_physical_sample_count']),(178560,178560));self.assertEqual((sp['window_start_sample'],sp['window_end_sample'],sp['logical_sample_count']),(1105,177505,176400))
  self.assertEqual(sp['physical_pcm_sha256'],'322345836135dd2ea80b466a2b1a2041d08ff6f7ce52c635b8044c0e798fd1f9');self.assertEqual(sp['structural_window_pcm_sha256'],'bc91dcc5d2081e680610eff97e672da3e10b3635dc348eefab174ff349bae65c')
  self.assertEqual(v['candidate_canonical_pcm_sha256'],sp['structural_window_pcm_sha256'])
 def test_frame_count_repair_restores_healthy_master_and_only_changes_count(self):
  a=self.A('01_frame_count_provable.mp3');e=next(e for e in a.repair_execution if e.get('status')=='CREATED');m=e['manifest'];out=Path(e['output_path'])
  self.assertEqual(sha256_file(out),sha256_file(self.d/'00_healthy_ffmpeg_xing.mp3'));self.assertEqual([r['field'] for r in m['changed_byte_ranges']],['XING_FRAME_COUNT']);self.assertFalse(m['audio_recoding']);self.assertEqual(analyze(out)['issues'],[])
 def test_low_padding_frame_count_case_remains_blocked(self):
  a=self.A('02_frame_count_low_padding_unprovable.mp3');e=next(e for e in a.repair_execution if e.get('repair_spec_id')=='REFRESH_XING_METADATA');self.assertEqual(e['status'],'BLOCKED');self.assertIn('padding final debe ser al menos 529',e['reason']);self.assertEqual(a.final_status,['ANOMALY_UNCHANGED'])
 def test_structural_damage_still_blocks_frame_count_refresh(self):
  a=self.A('03_structural_damage_negative.mp3');e=next(e for e in a.repair_execution if e.get('repair_spec_id')=='REFRESH_XING_METADATA');self.assertEqual(e['status'],'BLOCKED');self.assertIn('dañado, truncado o estructuralmente ambiguo',e['reason']);self.assertEqual(a.final_status,['ANOMALY_UNCHANGED'])
 def test_second_run_reuses_frame_count_repair_without_duplicate(self):
  self.A('01_frame_count_provable.mp3');before=sorted(p.name for p in self.d.glob('*repaired*.mp3'));a=self.A('01_frame_count_provable.mp3');e=next(e for e in a.repair_execution if e.get('status')=='REUSED');self.assertEqual(e['repair_spec_id'],'REFRESH_XING_METADATA');self.assertEqual(before,sorted(p.name for p in self.d.glob('*repaired*.mp3')))

if __name__=='__main__':unittest.main()
