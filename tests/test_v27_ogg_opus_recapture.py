from __future__ import annotations
import hashlib,json,os,shutil,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
from formats.ogg_opus import analyze
from formats.identify import identify
from app.pipeline import analyze_file
from app.config import load_config
MAN=json.loads((ROOT/'samples/ogg_opus_repair_v27_manifest.json').read_text(encoding="utf-8"));BASE=ROOT/'samples/ogg_opus_repair_v27';CFG=load_config(ROOT/'config.toml')
FFMPEG=os.environ.get('LOSSYDOCTOR_FFMPEG') or shutil.which('ffmpeg');FFPROBE=os.environ.get('LOSSYDOCTOR_FFPROBE') or shutil.which('ffprobe')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
class OggOpusRecaptureV27(unittest.TestCase):
 def test_fixture_hashes_and_issue_sets(self):
  for n,c in MAN['cases'].items():
   p=BASE/n;self.assertEqual(sha(p),c['sha256']);self.assertEqual([i.code for i in analyze(p)['issues']],c['expected_issues'],n)
 def test_prefix_recapture_identification_is_bounded_and_high_confidence(self):
  q=identify(BASE/'01_prefix_junk_recapture.opus');self.assertTrue(q['supported']);self.assertEqual((q['container'],q['codec'],q['confidence']),('OGG','opus','HIGH'));self.assertGreater(q.get('first_capture_offset',0),0)
 def test_output_gain_policy_source_value(self):
  q=analyze(BASE/'00_healthy_gain_policy.opus');self.assertEqual(q['facts']['opus_head']['output_gain_q7_8'],384);self.assertEqual(q['facts']['opus_head']['output_gain_db'],1.5)
 @unittest.skipUnless(FFMPEG and FFPROBE,'ffmpeg/ffprobe required')
 def test_recapture_creates_then_reuses_three_repairs(self):
  with tempfile.TemporaryDirectory() as td:
   t=Path(td);names=sorted(MAN['cases']);
   for n in names:shutil.copy2(BASE/n,t/n)
   first=[analyze_file(t/n,CFG,ROOT,FFMPEG,FFPROBE) for n in names]
   created=[x for a in first for x in a.repair_execution if x.get('status')=='CREATED']
   self.assertEqual(len(created),3)
   for ex in created:
    self.assertEqual(ex['repair_spec_id'],'OGG_RECAPTURE_VALID_PAGES_DROP_EXTRANEOUS_BYTES');m=ex['manifest'];v=m['verification']
    self.assertTrue(v['passed']);self.assertTrue(v['all_retained_page_crc_valid']);self.assertTrue(v['retained_page_bytes_exact']);self.assertTrue(v['packet_and_timeline_semantics_equal']);self.assertFalse(v['page_bytes_modified']);self.assertFalse(v['audio_packet_bytes_modified']);self.assertTrue(v['output_gain_q7_8_equal']);self.assertFalse(v['output_gain_applied_to_pcm']);self.assertEqual(m['output_gain_policy'],'PRESERVE_UNAPPLIED_Q7_8_IN_MANIFEST')
   second=[analyze_file(t/n,CFG,ROOT,FFMPEG,FFPROBE) for n in names]
   reused=[x for a in second for x in a.repair_execution if x.get('status')=='REUSED'];self.assertEqual(len(reused),3)
 def test_crc_and_sequence_repairs_are_explicitly_blocked(self):
  if not (FFMPEG and FFPROBE):self.skipTest('ffmpeg/ffprobe required')
  a=analyze_file(BASE/'04_crc_mismatch_blocked.opus',CFG,ROOT,FFMPEG,FFPROBE);b=analyze_file(BASE/'05_sequence_gap_blocked.opus',CFG,ROOT,FFMPEG,FFPROBE)
  self.assertIn(('REWRITE_OGG_PAGE_CRC','BLOCKED'),[(x.get('repair_spec_id'),x.get('status')) for x in a.repair_execution])
  self.assertIn(('RENUMBER_OGG_PAGE_SEQUENCE','BLOCKED'),[(x.get('repair_spec_id'),x.get('status')) for x in b.repair_execution])
 def test_pipeline_gain_policy_is_preserve_unapplied(self):
  if not (FFMPEG and FFPROBE):self.skipTest('ffmpeg/ffprobe required')
  a=analyze_file(BASE/'00_healthy_gain_policy.opus',CFG,ROOT,FFMPEG,FFPROBE)
  self.assertEqual(a.canonical_presentation_window['output_gain_policy'],'PRESERVE_UNAPPLIED_Q7_8_IN_MANIFEST');self.assertEqual(a.canonical_presentation_window['output_gain_q7_8'],384)
  d=next(x for x in a.policy_decisions if x['code']=='OPUS_OUTPUT_GAIN_PRESERVATION');self.assertEqual(d['decision'],'PRESERVE_UNAPPLIED_Q7_8_IN_MANIFEST')
if __name__=='__main__':unittest.main()
