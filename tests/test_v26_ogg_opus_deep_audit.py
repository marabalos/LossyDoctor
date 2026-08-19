from __future__ import annotations
import hashlib,json,os,shutil,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
from formats.ogg_opus import analyze,opus_packet_samples
from formats.identify import identify
from app.pipeline import analyze_file
from app.config import load_config
MAN=json.loads((ROOT/'samples/ogg_opus_v26_manifest.json').read_text(encoding="utf-8"));BASE=ROOT/'samples/ogg_opus_v26';CFG=load_config(ROOT/'config.toml')
FFMPEG=os.environ.get('LOSSYDOCTOR_FFMPEG') or shutil.which('ffmpeg');FFPROBE=os.environ.get('LOSSYDOCTOR_FFPROBE') or shutil.which('ffprobe')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
class OggOpusDeepAuditV26(unittest.TestCase):
 def test_fixture_hashes_and_expected_issue_sets(self):
  for n,c in MAN['cases'].items():
   p=BASE/n;self.assertEqual(sha(p),c['sha256']);self.assertEqual([i.code for i in analyze(p)['issues']],c['expected_issues'],n)
 def test_healthy_timing_is_exact(self):
  q=analyze(BASE/'00_healthy_stereo.opus');f=q['facts'];h=f['opus_head']
  self.assertTrue(f['ogg']['all_page_crc_valid']);self.assertEqual(f['ogg']['page_count'],4);self.assertEqual(f['audio_packet_count'],63)
  self.assertEqual(h['pre_skip'],312);self.assertEqual(h['channels'],2);self.assertEqual(h['mapping_family'],0);self.assertEqual(f['final_granule_position'],60312);self.assertEqual(f['pcm_sample_position'],60000);self.assertEqual(f['playback_seconds'],1.25)
 def test_nonzero_output_gain_is_observed_not_an_issue(self):
  q=analyze(BASE/'01_healthy_output_gain.opus');self.assertEqual(q['issues'],[]);self.assertEqual(q['facts']['opus_head']['output_gain_q7_8'],384);self.assertEqual(q['facts']['opus_head']['output_gain_db'],1.5)
 def test_identify_routes_opus_to_deep_parser(self):
  q=identify(BASE/'00_healthy_stereo.opus');self.assertEqual((q['container'],q['codec']),('OGG','opus'));self.assertIn('ogg_opus',q)
 def test_toc_duration_parser_sane(self):
  # Config 28 (CELT fullband 2.5 ms), code 0 => 120 samples at 48 kHz.
  self.assertEqual(opus_packet_samples(bytes([(28<<3)|0])),120)
  # Same config, code 1 => two frames.
  self.assertEqual(opus_packet_samples(bytes([(28<<3)|1,0])),240)
 @unittest.skipUnless(FFMPEG and FFPROBE,'ffmpeg/ffprobe required')
 def test_pipeline_acceptance_summary(self):
  rows=[analyze_file(BASE/n,CFG,ROOT,FFMPEG,FFPROBE) for n in sorted(MAN['cases'])]
  self.assertEqual(sum(a.run_status=='SUCCESS' for a in rows),2);self.assertEqual(sum(a.run_status=='SUCCESS_WITH_FINDINGS' for a in rows),4);self.assertTrue(all(not any(x.get('status') in ('CREATED','REUSED') for x in a.repair_execution) and not a.lossless_export for a in rows))
  good=rows[0];self.assertEqual(good.validity_domains['CONTAINER_VALIDITY'],'VALID');self.assertEqual(good.validity_domains['TIMELINE_VALIDITY'],'VALIDATED_GRANULES')
if __name__=='__main__':unittest.main()
