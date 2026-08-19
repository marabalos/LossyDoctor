from __future__ import annotations
import hashlib,json,os,shutil,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
from formats.mpeg import analyze
from app import lossless_export
from app.utils import sha256_file
FFMPEG=os.environ.get('LOSSYDOCTOR_FFMPEG') or shutil.which('ffmpeg')
MAN=json.loads((ROOT/'samples/segment_recovery_v21_manifest.json').read_text(encoding="utf-8"))
BASE=ROOT/'samples/segment_recovery_v21'

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

@unittest.skipUnless(FFMPEG,'ffmpeg required')
class V21SegmentAwareRecovery(unittest.TestCase):
 def test_fixture_hashes(self):
  for n,c in MAN['cases'].items():self.assertEqual(sha(BASE/n),c['sha256'])
 def test_gate_is_fallback_only_and_blocks_real_gap(self):
  a=lossless_export.assess(analyze(BASE/'00_playable_coherent_control.mp3'),'PLAYABLE')
  self.assertEqual(a['pcm_class'],'HETEROGENEOUS_STREAM');self.assertFalse(a['eligible_segmented'])
  a=lossless_export.assess(analyze(BASE/'01_unplayable_sample_rate_concat.mp3'),'UNPLAYABLE')
  self.assertTrue(a['eligible_segmented']);self.assertEqual(len(a['segment_recovery_gate']['segments']),2)
  a=lossless_export.assess(analyze(BASE/'04_unplayable_after_gap_negative.mp3'),'UNPLAYABLE')
  self.assertFalse(a['eligible_segmented']);self.assertIn('brechas ni truncamiento',a['segment_recovery_gate']['reason'])
  # v0.22 may independently admit the same source into the newer damaged-region
  # recovery gate; that does not relax the v0.21 coherent-segment gate.
  self.assertIn('eligible_segmented_partial',a)
 def _export_case(self,name,expected_profiles):
  with tempfile.TemporaryDirectory() as td:
   src=Path(td)/name;shutil.copy2(BASE/name,src);m=analyze(src)
   ex=lossless_export.export(src,sha256_file(src),m,FFMPEG,'UNPLAYABLE',True)
   self.assertEqual(ex['status'],'CREATED');self.assertEqual(len(ex['outputs']),len(expected_profiles))
   for o,ep in zip(ex['outputs'],expected_profiles):
    man=o['manifest'];self.assertEqual(man['derivation_kind'],'RECOVERED_SEGMENTED_LOSSLESS');self.assertEqual(man['materialization'],'INDEPENDENT_NATIVE_PROFILE_SEGMENT')
    self.assertEqual((man['native_profile']['layer'],man['native_profile']['sample_rate'],man['native_profile']['channels']),ep)
    self.assertEqual(man['source_segment_pcm_sha256'],man['flac_decoded_pcm_sha256']);self.assertEqual(man['resampling'],'NONE');self.assertEqual(man['channel_remix'],'NONE');self.assertEqual(man['validation_result'],'PASS')
   ex2=lossless_export.export(src,sha256_file(src),m,FFMPEG,'UNPLAYABLE',True)
   self.assertEqual(ex2['status'],'REUSED');self.assertEqual(len(ex2['outputs']),len(expected_profiles));self.assertTrue(all(o['status']=='REUSED' for o in ex2['outputs']))
 def test_sample_rate_segments_preserve_native_geometry_and_reuse(self):
  self._export_case('01_unplayable_sample_rate_concat.mp3',[(3,44100,2),(3,22050,2)])
 def test_layer_segments_preserve_native_geometry(self):
  self._export_case('02_unplayable_layer_concat.mpa',[(3,44100,2),(2,44100,2)])
 def test_channel_segments_preserve_native_geometry(self):
  self._export_case('03_unplayable_channel_concat.mp3',[(3,44100,2),(3,44100,1)])
if __name__=='__main__':unittest.main()
