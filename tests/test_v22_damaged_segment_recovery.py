from __future__ import annotations
import hashlib,json,os,shutil,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
from formats.mpeg import analyze
from app import lossless_export
from app.pipeline import analyze_file
from app.config import load_config
from app.utils import sha256_file
FFMPEG=os.environ.get('LOSSYDOCTOR_FFMPEG') or shutil.which('ffmpeg')
FFPROBE=os.environ.get('LOSSYDOCTOR_FFPROBE') or shutil.which('ffprobe')
CFG=load_config(ROOT/'config.toml')
BASE=ROOT/'samples/damaged_segment_recovery_v22'
MAN=json.loads((ROOT/'samples/damaged_segment_recovery_v22_manifest.json').read_text(encoding="utf-8"))
EMPTY_SHA=hashlib.sha256(b'').hexdigest()

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

@unittest.skipUnless(FFMPEG and FFPROBE,'ffmpeg/ffprobe required')
class V22DamagedSegmentRecovery(unittest.TestCase):
 def test_fixture_hashes_and_structural_shapes(self):
  for name,c in MAN['cases'].items():
   p=BASE/name;self.assertEqual(sha(p),c['sha256']);a=analyze(p)
   self.assertEqual([i.code for i in a['issues']],c['issue_codes']);self.assertEqual(len(a['facts']['gaps']),c['gap_count']);self.assertEqual(a['facts']['parameter_segments']['hard_profile_transition_count'],c['hard_transition_count']);self.assertEqual(bool(a['facts']['truncated_final_frame']),c['truncated_final_frame'])
 def test_fallback_gate_blocks_reproducible_and_truncated_sources(self):
  a=lossless_export.assess(analyze(BASE/'00_playable_reservoir_gap_hetero_control.mp3'),'PLAYABLE')
  self.assertFalse(a.get('eligible_segmented_partial'));self.assertIn('reproducible',a['segment_partial_recovery_gate']['reason'])
  a=lossless_export.assess(analyze(BASE/'04_unplayable_truncated_negative.mp3'),'UNPLAYABLE')
  self.assertFalse(a.get('eligible_segmented_partial'));self.assertIn('frames finales truncados',a['segment_partial_recovery_gate']['reason'])
 def test_reservoir_gap_discards_tainted_frames_plus_one_clean_warmup(self):
  a=lossless_export.assess(analyze(BASE/'01_unplayable_reservoir_gap_hetero.mp3'),'UNPLAYABLE')
  self.assertTrue(a['eligible_segmented_partial']);r=a['segment_partial_recovery_gate']['regions'];self.assertEqual(len(r),3)
  post=r[1];self.assertEqual(post['preclean_tainted_frame_count'],2);self.assertEqual(post['warmup_clean_frame_count'],1);self.assertEqual(post['discarded_context_samples'],3*1152);self.assertEqual(post['gap_before_index'],0)
  self.assertGreater(post['source_byte_start'],post['decode_context_byte_start'])
 def _export_case(self,name):
  expected=MAN['cases'][name]
  with tempfile.TemporaryDirectory() as td:
   src=Path(td)/name;shutil.copy2(BASE/name,src);m=analyze(src);before=sha256_file(src)
   ex=lossless_export.export(src,before,m,FFMPEG,'UNPLAYABLE',True)
   self.assertEqual(ex['status'],'CREATED');self.assertEqual(len(ex['outputs']),expected['expected_output_count']);self.assertEqual(before,sha256_file(src))
   for o,er in zip(ex['outputs'],expected['expected_regions']):
    man=o['manifest'];pr=man['native_profile'];self.assertEqual(man['derivation_kind'],'RECOVERED_SEGMENTED_PARTIAL_LOSSLESS');self.assertEqual(man['materialization'],'INDEPENDENT_NATIVE_PROFILE_CLEAN_REGION');self.assertEqual([pr['mpeg_version'],pr['layer'],pr['sample_rate'],pr['channels']],er['profile'])
    self.assertEqual(man['source_byte_start'],er['source_byte_start']);self.assertEqual(man['source_byte_end'],er['source_byte_end']);self.assertEqual(man['discarded_context_samples'],er['discarded_context_samples']);self.assertEqual(man['preclean_tainted_frame_count'],er['preclean_tainted_frame_count']);self.assertEqual(man['warmup_clean_frame_count'],er['warmup_clean_frame_count']);self.assertEqual(man['gap_before_index'],er['gap_before_index']);self.assertEqual(man['sample_count'],er['sample_count'])
    self.assertEqual(man['source_region_pcm_sha256'],er['pcm_sha256']);self.assertEqual(man['flac_decoded_pcm_sha256'],er['pcm_sha256']);self.assertNotEqual(er['pcm_sha256'],EMPTY_SHA);self.assertGreater(man['sample_count'],0);self.assertEqual(man['resampling'],'NONE');self.assertEqual(man['channel_remix'],'NONE');self.assertEqual(man['synthesized_gap_silence'],[]);self.assertEqual(man['validation_result'],'PASS')
   before_outputs=sorted(p.name for p in Path(td).glob('*recovered-segmented-partial-lossless*.flac'))
   ex2=lossless_export.export(src,before,m,FFMPEG,'UNPLAYABLE',True);self.assertEqual(ex2['status'],'REUSED');self.assertEqual(len(ex2['outputs']),expected['expected_output_count']);self.assertTrue(all(x['status']=='REUSED' for x in ex2['outputs']));self.assertEqual(sorted(p.name for p in Path(td).glob('*recovered-segmented-partial-lossless*.flac')),before_outputs)
 def test_reservoir_gap_plus_heterogeneous_tail_exports_three_native_parts(self):self._export_case('01_unplayable_reservoir_gap_hetero.mp3')
 def test_parameter_change_after_gap_exports_two_native_parts(self):self._export_case('02_unplayable_rate_change_after_gap.mp3')
 def test_layer_change_after_gap_exports_two_native_parts(self):self._export_case('03_unplayable_layer_change_after_gap.mpa')
 def test_pipeline_uses_segmented_partial_recovery_and_classifies_it(self):
  with tempfile.TemporaryDirectory() as td:
   src=Path(td)/'case.mp3';shutil.copy2(BASE/'01_unplayable_reservoir_gap_hetero.mp3',src)
   a=analyze_file(src,CFG,ROOT,FFMPEG,FFPROBE)
   self.assertEqual(a.playability,'UNPLAYABLE');self.assertEqual(a.pcm_recovery_class,'HETEROGENEOUS_STREAM');self.assertEqual(a.final_status,['RECOVERED_SEGMENTED_PARTIAL_LOSSLESS']);self.assertEqual(a.run_status,'SUCCESS_WITH_RECOVERY');self.assertEqual(sum(len(x.get('outputs',[])) for x in a.lossless_export),3)
 def test_reproducible_control_pipeline_never_materializes(self):
  with tempfile.TemporaryDirectory() as td:
   src=Path(td)/'control.mp3';shutil.copy2(BASE/'00_playable_reservoir_gap_hetero_control.mp3',src)
   a=analyze_file(src,CFG,ROOT,FFMPEG,FFPROBE);self.assertEqual(a.playability,'PLAYABLE');self.assertEqual(a.lossless_export,[])
if __name__=='__main__':unittest.main()
