from __future__ import annotations
import hashlib,json,os,shutil,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
from formats.ogg_opus import analyze
from app.opus_recovery import assess,PRE_ROLL_SAMPLES_48K
from app.pipeline import analyze_file
from app.config import load_config
MAN=json.loads((ROOT/'samples/opus_eos_continuation_v29_manifest.json').read_text(encoding="utf-8"));BASE=ROOT/'samples/opus_eos_continuation_v29';CFG=load_config(ROOT/'config.toml')
FFMPEG=os.environ.get('LOSSYDOCTOR_FFMPEG') or shutil.which('ffmpeg');FFPROBE=os.environ.get('LOSSYDOCTOR_FFPROBE') or shutil.which('ffprobe')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
class OpusEosContinuationV29(unittest.TestCase):
 def test_fixture_hashes(self):
  for n,c in MAN['cases'].items():
   p=BASE/n;self.assertEqual(sha(p),c['sha256']);self.assertEqual([i.code for i in analyze(p)['issues']],c['expected_issues'],n)
 def test_healthy_final_packet_spans_pages_and_exact_end_trim(self):
  q=analyze(BASE/'00_healthy_continued_eos_trim.opus');self.assertFalse(q['issues']);self.assertEqual(q['facts']['eos_end_trim_samples_48k'],480)
  span=[x for x in q['facts']['audio_packet_map'] if x.get('spans_pages')];self.assertEqual(len(span),1);x=span[0]
  self.assertTrue(x['crc_authenticated_complete_packet']);self.assertEqual(x['page_sequences'],[10,11]);self.assertTrue(x['ends_on_eos_page'])
  self.assertEqual((x['decoded_granule_start'],x['decoded_granule_end'],x['presentation_granule_end'],x['tail_trim_samples_48k']),(75840,76800,76320,480))
 def test_missing_page_tail_keeps_authenticated_continued_eos_packet(self):
  p=BASE/'01_unplayable_missing_page_continued_tail.opus';a=assess(p,analyze(p),'UNPLAYABLE');self.assertTrue(a['eligible']);self.assertEqual(len(a['regions']),2)
  r=a['regions'][1];self.assertTrue(r['includes_authenticated_eos']);self.assertEqual(r['eos_end_trim_samples_48k'],480);self.assertEqual(r['continued_source_packet_count'],1)
  self.assertEqual((r['source_pcm_start_48k'],r['source_pcm_end_48k'],r['expected_pcm_samples_48k']),(41928,76008,34080));self.assertGreaterEqual(r['decoder_context_discard_samples_48k'],PRE_ROLL_SAMPLES_48K)
 def test_crc_page_tail_also_keeps_exact_eos(self):
  p=BASE/'02_unplayable_crc_page_continued_tail.opus';a=assess(p,analyze(p),'UNPLAYABLE');self.assertTrue(a['eligible']);self.assertEqual(len(a['regions']),2)
  self.assertTrue(a['regions'][1]['includes_authenticated_eos']);self.assertEqual(a['regions'][1]['continued_source_packet_count'],1);self.assertEqual(a['regions'][1]['source_pcm_end_48k'],76008)
 def test_bad_first_half_invalidates_whole_continued_packet(self):
  p=BASE/'03_unplayable_crc_first_half_continued_packet.opus';q=analyze(p);span=[x for x in q['facts']['audio_packet_map'] if x.get('spans_pages')];self.assertEqual(len(span),1);self.assertFalse(span[0]['crc_authenticated_complete_packet'])
  a=assess(p,q,'UNPLAYABLE');self.assertTrue(a['eligible']);self.assertEqual(len(a['regions']),1);r=a['regions'][0]
  self.assertFalse(r['includes_authenticated_eos']);self.assertEqual(r['continued_source_packet_count'],0);self.assertEqual(r['source_pcm_end_48k'],75528)
 def test_truncated_continued_eos_makes_no_end_trim_claim(self):
  p=BASE/'04_unplayable_truncated_continued_eos.opus';q=analyze(p);self.assertIsNone(q['facts']['eos_end_trim_samples_48k']);a=assess(p,q,'UNPLAYABLE');self.assertTrue(a['eligible']);self.assertEqual(len(a['regions']),1)
  r=a['regions'][0];self.assertFalse(r['includes_authenticated_eos']);self.assertEqual(r['eos_end_trim_samples_48k'],0);self.assertEqual(r['source_pcm_end_48k'],75528)
 @unittest.skipUnless(FFMPEG and FFPROBE,'ffmpeg/ffprobe required')
 def test_pipeline_creates_six_regions_then_reuses(self):
  with tempfile.TemporaryDirectory() as td:
   t=Path(td);names=sorted(MAN['cases'])
   for n in names:shutil.copy2(BASE/n,t/n)
   first=[analyze_file(t/n,CFG,ROOT,FFMPEG,FFPROBE) for n in names]
   outs=[o for a in first for e in a.lossless_export for o in e.get('outputs',[]) if o.get('status')=='CREATED'];self.assertEqual(len(outs),6)
   eos=[o for o in outs if o['manifest'].get('includes_authenticated_eos')];self.assertEqual(len(eos),2)
   for o in outs:
    m=o['manifest'];self.assertEqual(m['derivation_kind'],'RECOVERED_OPUS_PROVEN_REGION_LOSSLESS');self.assertEqual(m['derivation_schema'],2);self.assertEqual(m['source_output_gain_q7_8'],384);self.assertFalse(m['output_gain_baked_into_pcm']);self.assertTrue(m['temporary_decode_view_repages_packets']);self.assertEqual(m['region_pcm_sha256'],m['flac_decoded_pcm_sha256'])
   for o in eos:
    m=o['manifest'];self.assertEqual(m['eos_end_trim_samples_48k'],480);self.assertEqual(m['continued_source_packet_count'],1);self.assertTrue(m['source_page_crc_authenticated'])
   second=[analyze_file(t/n,CFG,ROOT,FFMPEG,FFPROBE) for n in names]
   reused=[o for a in second for e in a.lossless_export for o in e.get('outputs',[]) if o.get('status')=='REUSED'];self.assertEqual(len(reused),6)
 def test_v27_safe_recapture_precedence_is_unchanged(self):
  if not (FFMPEG and FFPROBE):self.skipTest('ffmpeg/ffprobe required')
  p=ROOT/'samples/ogg_opus_repair_v27/01_prefix_junk_recapture.opus'
  with tempfile.TemporaryDirectory() as td:
   q=Path(td)/p.name;shutil.copy2(p,q);a=analyze_file(q,CFG,ROOT,FFMPEG,FFPROBE)
   self.assertTrue(any(x.get('status')=='CREATED' for x in a.repair_execution));self.assertFalse(a.lossless_export);self.assertTrue(any(x.get('code')=='BITSTREAM_REPAIR_PRECEDES_PCM_DERIVATION' for x in a.policy_decisions))
if __name__=='__main__':unittest.main()
