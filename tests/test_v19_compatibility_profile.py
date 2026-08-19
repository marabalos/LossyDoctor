from __future__ import annotations
import hashlib,json,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
from formats.mpeg import analyze
from reporting.markdown_report import write_md
MAN=json.loads((ROOT/'samples/compatibility_v19_manifest.json').read_text(encoding="utf-8"))
BASE=ROOT/'samples/compatibility_v19'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def codes(a):return [i.code for i in a['issues']]
class CompatibilityProfileV19(unittest.TestCase):
 def test_fixture_hashes_and_no_structural_findings(self):
  for name,c in MAN['cases'].items():
   p=BASE/name;self.assertEqual(sha(p),c['sha256']);self.assertEqual(codes(analyze(p)),[],name)
 def test_lame_info_and_xing_are_declared_not_guessed(self):
  a=analyze(BASE/'00_lame_cbr_info.mp3')['facts']['compatibility_profile'];b=analyze(BASE/'01_lame_vbr_xing.mp3')['facts']['compatibility_profile']
  self.assertEqual((a['dedicated_seek_header'],a['declared_encoder'],a['encoder_attribution']),('Info','LAME3.100','DECLARED_IN_XING_TAG'))
  self.assertEqual(b['dedicated_seek_header'],'Xing');self.assertEqual(b['declared_encoder'],'LAME3.100');self.assertIn('MULTIPLE_BITRATES',b['variant_flags'])
 def test_ffmpeg_muxed_alternative_encoder_surfaces_only_declared_tag(self):
  c=analyze(BASE/'02_ffmpeg_libshine.mp3')['facts']['compatibility_profile']
  self.assertTrue(c['declared_encoder'].startswith('Lavc'));self.assertEqual(c['encoder_attribution'],'DECLARED_IN_XING_TAG');self.assertEqual(c['id3v2_major'],4)
 def test_mpeg25_mono_low_rate_is_valid_variant(self):
  c=analyze(BASE/'03_mpeg25_mono_8k.mp3')['facts']['compatibility_profile']
  self.assertEqual(c['mpeg_versions'],[25]);self.assertEqual(c['sample_rates_hz'],[8000]);self.assertEqual(c['channels'],[1]);self.assertIn('MPEG_2_5',c['variant_flags']);self.assertIn('MONO_PRESENT',c['variant_flags'])
 def test_crc_and_layer2_profiles(self):
  c=analyze(BASE/'04_lame_crc_protected.mp3')['facts']['compatibility_profile'];d=analyze(BASE/'05_twolame_mp2.mp2')['facts']['compatibility_profile']
  self.assertEqual(c['crc_protection'],'ALL');self.assertIn('CRC_PROTECTED',c['variant_flags']);self.assertEqual(d['layers'],[2]);self.assertEqual(d['declared_encoder'],None)
 def test_legacy_metadata_does_not_trigger_encoder_guess(self):
  c=analyze(BASE/'06_id3v1_no_seek_header.mp3')['facts']['compatibility_profile'];d=analyze(BASE/'07_id3v23_ffmpeg.mp3')['facts']['compatibility_profile']
  self.assertTrue(c['id3v1_present']);self.assertEqual(c['dedicated_seek_header'],'NONE');self.assertEqual(c['encoder_attribution'],'UNATTRIBUTED');self.assertIsNone(c['declared_encoder'])
  self.assertEqual(d['id3v2_major'],3);self.assertIn('ID3V2_3',d['variant_flags'])
 def test_markdown_surfaces_provenance_policy(self):
  a=analyze(BASE/'06_id3v1_no_seek_header.mp3')
  d={'display_name':'x.mp3','run_status':'SUCCESS','detected_container':'MPEG_AUDIO','detected_codec':'mp3','playability':'PLAYABLE','pcm_recovery_class':'NOT_REQUIRED','decode_results':{},'validity_domains':{},'format_facts':a['facts'],'issues':[],'repair_plan':[],'repair_execution':[],'lossless_export':[],'policy_decisions':[],'final_status':['OK']}
  run={'run_id':'x','started_at':'2026-08-16T20:20:00-03:00','summary':{'discovered':1,'processed':1,'ok':1,'with_findings':0,'skipped':0,'failed':0,'repaired_outputs_created':0,'lossless_outputs_created':0,'outputs_reused':0,'candidates_rejected':0},'files':[d]}
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)/'r.md';write_md(p,run);txt=p.read_text(encoding="utf-8")
  self.assertIn('Evidencia de compatibilidad y procedencia MPEG',txt);self.assertIn('OBSERVED_FIELDS_ONLY_NO_ENCODER_GUESSING',txt);self.assertIn('UNATTRIBUTED',txt)
if __name__=='__main__':unittest.main()
