from __future__ import annotations
import os,shutil,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
from app.config import load_config
from app.pipeline import analyze_file
from app.utils import sha256_file
from formats.mpeg import analyze
CFG=load_config(ROOT/'config.toml');FFMPEG=os.environ.get('LOSSYDOCTOR_FFMPEG') or shutil.which('ffmpeg');FFPROBE=os.environ.get('LOSSYDOCTOR_FFPROBE') or shutil.which('ffprobe')
@unittest.skipUnless(FFMPEG and FFPROBE,'ffmpeg/ffprobe required')
class V11XingRefresh(unittest.TestCase):
 def setUp(self):
  self.td=tempfile.TemporaryDirectory();self.d=Path(self.td.name)
  for p in (ROOT/'samples/xing_refresh_v11').glob('*.mp3'):shutil.copy2(p,self.d/p.name)
 def tearDown(self):self.td.cleanup()
 def A(self,n):
  p=self.d/n;h=sha256_file(p);a=analyze_file(p,CFG,ROOT,FFMPEG,FFPROBE);self.assertEqual(sha256_file(p),h);return a
 def test_healthy_ffmpeg_profile_matches_all_recomputed_fields(self):
  m=analyze(self.d/'00_healthy_ffmpeg_xing.mp3');self.assertEqual(m['issues'],[]);x=m['facts']['vbr_header']['xing'];self.assertTrue(x['ffmpeg_extended_profile']);e=x['expected'];self.assertEqual((x['kind'],x['frames'],x['bytes'],x['toc'],x['music_length'],x['music_crc'],x['tag_crc']),(e['kind'],e['frames'],e['bytes'],e['toc'],e['music_length'],e['music_crc'],e['tag_crc']))
 def test_positive_fixture_exposes_full_coupled_mismatch_set(self):
  codes={i.code for i in analyze(self.d/'01_ffmpeg_xing_coupled_metadata_corrupt.mp3')['issues']};self.assertEqual(codes,{'XING_BYTE_COUNT_MISMATCH','XING_KIND_MISMATCH','XING_TOC_MISMATCH','XING_MUSIC_LENGTH_MISMATCH','XING_AUDIO_CRC_MISMATCH','XING_TAG_CRC_MISMATCH'})
 def test_coherent_refresh_restores_master_and_pcm_identity(self):
  a=self.A('01_ffmpeg_xing_coupled_metadata_corrupt.mp3');e=next(e for e in a.repair_execution if e.get('status')=='CREATED');m=e['manifest'];self.assertEqual(e['repair_spec_id'],'REFRESH_XING_METADATA');self.assertEqual(a.final_status,['REPAIRED_SAFE']);self.assertEqual(sha256_file(Path(e['output_path'])),sha256_file(self.d/'00_healthy_ffmpeg_xing.mp3'));self.assertTrue(m['verification']['pcm_identical']);self.assertTrue(m['verification']['audio_payload_identical']);self.assertTrue(m['verification']['seekability_metadata_validated']);self.assertEqual(m['verification']['xing_issue_codes_remaining'],[])
 def test_refresh_diff_contains_only_coupled_xing_fields(self):
  a=self.A('01_ffmpeg_xing_coupled_metadata_corrupt.mp3');e=next(e for e in a.repair_execution if e.get('status')=='CREATED');fields={r['field'] for r in e['manifest']['changed_byte_ranges']};self.assertEqual(fields,{'XING_KIND','XING_BYTE_COUNT','XING_TOC','XING_MUSIC_LENGTH','XING_MUSIC_CRC','XING_TAG_CRC'});self.assertFalse(e['manifest']['audio_recoding'])
 def test_frame_count_fixture_exposes_single_structural_mismatch(self):
  m=analyze(self.d/'02_frame_count_presentation_negative.mp3');self.assertEqual([i.code for i in m['issues']],['XING_FRAME_COUNT_MISMATCH']);self.assertEqual(m['facts']['audio_frame_count_observed'],155);self.assertEqual(m['facts']['vbr_header']['xing']['frames'],154)
 def test_structural_damage_remains_blocked(self):
  a=self.A('03_structural_damage_negative.mp3');e=next(e for e in a.repair_execution if e.get('repair_spec_id')=='REFRESH_XING_METADATA');self.assertEqual(e['status'],'BLOCKED');self.assertEqual(a.final_status,['ANOMALY_UNCHANGED'])
 def test_unknown_encoder_profile_remains_blocked(self):
  a=self.A('04_non_ffmpeg_profile_negative.mp3');e=next(e for e in a.repair_execution if e.get('repair_spec_id')=='REFRESH_XING_METADATA');self.assertEqual(e['status'],'BLOCKED');self.assertIn('FFmpeg reconocible',e['reason'])
 def test_repaired_xing_output_reanalyzes_as_ok(self):
  a=self.A('01_ffmpeg_xing_coupled_metadata_corrupt.mp3');e=next(e for e in a.repair_execution if e.get('status')=='CREATED');b=analyze_file(Path(e['output_path']),CFG,ROOT,FFMPEG,FFPROBE);self.assertEqual(b.final_status,['OK']);self.assertEqual(b.issues,[])
 def test_static_v11_fixture_hashes(self):
  exp={'00_healthy_ffmpeg_xing.mp3':'12f614e3fc507471071c20426f511aa4698b8349e8356be2831e943adf7574a7','01_ffmpeg_xing_coupled_metadata_corrupt.mp3':'dc630ff32f8649195abc28a73a687f5e84b2a5fe1fdb56f24f8ca523ffe60708','02_frame_count_presentation_negative.mp3':'a44289a0e1894c18d5ded2234fb64f71b1649af71ffd77769a33e787f65b83f2','03_structural_damage_negative.mp3':'97572ab1984848f98a6f7c7244d13fbe663f1e315faaa09308b36ec5f631621d','04_non_ffmpeg_profile_negative.mp3':'d913512611f98fced2085e27c6f1170e3d6ae4a07bb193594dcf90482b2f9f2e'}
  for n,h in exp.items():self.assertEqual(sha256_file(self.d/n),h)
 def test_second_run_reuses_verified_refresh(self):
  self.A('01_ffmpeg_xing_coupled_metadata_corrupt.mp3');before=sorted(p.name for p in self.d.glob('*repaired*.mp3'));a=self.A('01_ffmpeg_xing_coupled_metadata_corrupt.mp3');e=next(e for e in a.repair_execution if e.get('status')=='REUSED');self.assertEqual(e['repair_spec_id'],'REFRESH_XING_METADATA');self.assertEqual(before,sorted(p.name for p in self.d.glob('*repaired*.mp3')))
