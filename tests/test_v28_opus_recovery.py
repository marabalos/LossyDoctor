from __future__ import annotations
import hashlib,json,os,shutil,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
from formats.ogg_opus import analyze
from app.opus_recovery import assess,PRE_ROLL_SAMPLES_48K
from app.pipeline import analyze_file
from app.config import load_config
from app.version import APP_VERSION,POLICY_VERSION
MAN=json.loads((ROOT/'samples/opus_recovery_v28_manifest.json').read_text(encoding="utf-8"));BASE=ROOT/'samples/opus_recovery_v28';CFG=load_config(ROOT/'config.toml')
FFMPEG=os.environ.get('LOSSYDOCTOR_FFMPEG') or shutil.which('ffmpeg');FFPROBE=os.environ.get('LOSSYDOCTOR_FFPROBE') or shutil.which('ffprobe')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
class OpusRecoveryV28(unittest.TestCase):
 def test_version_policy_and_fixture_hashes(self):
  for n,c in MAN['cases'].items():
   p=BASE/n;self.assertEqual(sha(p),c['sha256']);self.assertEqual([i.code for i in analyze(p)['issues']],c['expected_issues'],n)
 def test_missing_page_has_two_exact_regions_and_rfc_preroll(self):
  p=BASE/'01_unplayable_missing_page.opus';q=analyze(p);a=assess(p,q,'UNPLAYABLE');self.assertTrue(a['eligible']);self.assertEqual(len(a['regions']),2)
  r1,r2=a['regions'];self.assertEqual((r1['source_pcm_start_48k'],r1['source_pcm_end_48k']),(0,66888));self.assertEqual(r1['decoder_context_discard_samples_48k'],312)
  self.assertEqual((r2['source_pcm_start_48k'],r2['source_pcm_end_48k']),(80328,153600));self.assertEqual(r2['eos_end_trim_samples_48k'],648);self.assertTrue(r2['includes_authenticated_eos']);self.assertGreaterEqual(r2['decoder_context_discard_samples_48k'],PRE_ROLL_SAMPLES_48K);self.assertNotIn(9,r2['source_page_sequences'])
 def test_crc_damaged_page_is_excluded_not_repaired_for_pcm(self):
  p=BASE/'02_unplayable_crc_page.opus';q=analyze(p);a=assess(p,q,'UNPLAYABLE');self.assertTrue(a['eligible']);self.assertEqual(len(a['regions']),2)
  for r in a['regions']:self.assertNotIn(9,r['source_page_sequences'])
 def test_terminal_truncation_recovers_only_complete_non_eos_prefix(self):
  p=BASE/'03_unplayable_truncated_tail.opus';q=analyze(p);a=assess(p,q,'UNPLAYABLE');self.assertTrue(a['eligible']);self.assertEqual(len(a['regions']),1)
  r=a['regions'][0];self.assertEqual((r['source_pcm_start_48k'],r['source_pcm_end_48k']),(0,153288));self.assertEqual(r['source_page_sequences'][-1],17);self.assertEqual(a['coverage_claim'],'PROVEN_PACKET_REGIONS_WITH_EXACT_EOS_TRIM_NO_FULL_TIMELINE_CLAIM')
 def test_playable_sequence_gap_remains_policy_blocked(self):
  p=BASE/'04_playable_sequence_gap.opus';q=analyze(p);a=assess(p,q,'PLAYABLE');self.assertFalse(a['eligible']);self.assertEqual(a['pcm_class'],'POLICY_BLOCKED_PLAYABLE')
 @unittest.skipUnless(FFMPEG and FFPROBE,'ffmpeg/ffprobe required')
 def test_pipeline_creates_five_unity_gain_regions_then_reuses(self):
  with tempfile.TemporaryDirectory() as td:
   t=Path(td);names=sorted(MAN['cases'])
   for n in names:shutil.copy2(BASE/n,t/n)
   first=[analyze_file(t/n,CFG,ROOT,FFMPEG,FFPROBE) for n in names]
   outs=[o for a in first for e in a.lossless_export for o in e.get('outputs',[]) if o.get('status')=='CREATED'];self.assertEqual(len(outs),5)
   for o in outs:
    m=o['manifest'];self.assertEqual(m['derivation_kind'],'RECOVERED_OPUS_PROVEN_REGION_LOSSLESS');self.assertEqual(m['source_output_gain_q7_8'],384);self.assertFalse(m['output_gain_baked_into_pcm']);self.assertEqual(m['temporary_decode_view_output_gain_q7_8'],0);self.assertEqual(m['region_pcm_sha256'],m['flac_decoded_pcm_sha256']);self.assertFalse(m['opus_audio_packet_bytes_modified']);self.assertTrue(m['source_page_crc_authenticated'])
    if m['context_policy']=='RFC7845_SEEK_PREROLL_AT_LEAST_80MS':self.assertGreaterEqual(m['decoder_context_discard_samples_48k'],3840)
   second=[analyze_file(t/n,CFG,ROOT,FFMPEG,FFPROBE) for n in names]
   reused=[o for a in second for e in a.lossless_export for o in e.get('outputs',[]) if o.get('status')=='REUSED'];self.assertEqual(len(reused),5)
 def test_v27_safe_recapture_still_precedes_pcm(self):
  if not (FFMPEG and FFPROBE):self.skipTest('ffmpeg/ffprobe required')
  p=ROOT/'samples/ogg_opus_repair_v27/01_prefix_junk_recapture.opus'
  with tempfile.TemporaryDirectory() as td:
   q=Path(td)/p.name;shutil.copy2(p,q);a=analyze_file(q,CFG,ROOT,FFMPEG,FFPROBE)
   self.assertTrue(any(x.get('status')=='CREATED' for x in a.repair_execution));self.assertFalse(a.lossless_export);self.assertTrue(any(x.get('code')=='BITSTREAM_REPAIR_PRECEDES_PCM_DERIVATION' for x in a.policy_decisions))
if __name__=='__main__':unittest.main()
