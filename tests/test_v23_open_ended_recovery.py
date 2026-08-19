from __future__ import annotations
import copy,hashlib,json,os,shutil,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
from formats.mpeg import analyze
from app import lossless_export
from app.pipeline import analyze_file
from app.config import load_config
from app.utils import sha256_file
from reporting.markdown_report import write_md
FFMPEG=os.environ.get('LOSSYDOCTOR_FFMPEG') or shutil.which('ffmpeg')
FFPROBE=os.environ.get('LOSSYDOCTOR_FFPROBE') or shutil.which('ffprobe')
CFG=load_config(ROOT/'config.toml')
BASE=ROOT/'samples/open_ended_recovery_v23'
MAN=json.loads((ROOT/'samples/open_ended_recovery_v23_manifest.json').read_text(encoding="utf-8"))
EMPTY_SHA=hashlib.sha256(b'').hexdigest()
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

@unittest.skipUnless(FFMPEG and FFPROBE,'ffmpeg/ffprobe required')
class V23OpenEndedRecovery(unittest.TestCase):
 def test_fixture_hashes_and_structural_shapes(self):
  for name,c in MAN['cases'].items():
   p=BASE/name;self.assertEqual(sha(p),c['sha256']);m=analyze(p)
   self.assertEqual([i.code for i in m['issues']],c['issue_codes']);self.assertEqual(len(m['facts'].get('gaps') or []),c['gap_count']);self.assertEqual(m['facts']['parameter_segments']['hard_profile_transition_count'],c['hard_transition_count']);self.assertEqual(bool(m['facts']['truncated_final_frame']),c['truncated_final_frame'])
 def test_playable_terminal_truncation_remains_report_only(self):
  m=analyze(BASE/'00_playable_terminal_truncation_control.mp3');a=lossless_export.assess(m,'PLAYABLE')
  self.assertFalse(a.get('eligible_segmented_open_partial'));self.assertIn('reproducible',a['segment_open_partial_recovery_gate']['reason'])
  with tempfile.TemporaryDirectory() as td:
   src=Path(td)/'control.mp3';shutil.copy2(BASE/'00_playable_terminal_truncation_control.mp3',src)
   r=analyze_file(src,CFG,ROOT,FFMPEG,FFPROBE);self.assertEqual(r.playability,'PLAYABLE');self.assertEqual(r.lossless_export,[])
 def test_terminal_truncation_regions_end_before_incomplete_frame(self):
  for name in ('01_unplayable_terminal_truncation.mp3','02_unplayable_gap_plus_terminal_truncation.mp3'):
   m=analyze(BASE/name);a=lossless_export.assess(m,'UNPLAYABLE');g=a['segment_open_partial_recovery_gate'];self.assertTrue(g['eligible']);self.assertTrue(g['truncated_final_frame']);self.assertIsNotNone(g['terminal_damage'])
   cut=g['terminal_damage']['byte_start'];self.assertTrue(all(r['source_byte_end']<=cut for r in g['regions']))
 def test_unbracketed_gap_keeps_only_proven_side(self):
  m=analyze(BASE/'03_unplayable_unbracketed_late_gap.mp3');a=lossless_export.assess(m,'UNPLAYABLE')
  self.assertFalse(a['eligible_segmented_partial']);self.assertTrue(a['eligible_segmented_open_partial']);g=a['segment_open_partial_recovery_gate'];self.assertEqual(g['unbracketed_gap_indices'],[0]);self.assertFalse(g['truncated_final_frame'])
  gap=m['facts']['gaps'][0];self.assertTrue(all(r['source_byte_end']<=gap['byte_start'] for r in g['regions']))
 def test_crc_and_reservoir_overrun_still_block_open_recovery(self):
  m=analyze(BASE/'01_unplayable_terminal_truncation.mp3');x=copy.deepcopy(m);x['facts']['crc_protection']['mismatch_count']=1
  self.assertFalse(lossless_export.assess(x,'UNPLAYABLE')['eligible_segmented_open_partial'])
  y=copy.deepcopy(m);y['facts']['bit_reservoir']['main_data_overrun_frame_indices']=[1]
  self.assertFalse(lossless_export.assess(y,'UNPLAYABLE')['eligible_segmented_open_partial'])
 def _export_case(self,name):
  expected=MAN['cases'][name]
  with tempfile.TemporaryDirectory() as td:
   src=Path(td)/name;shutil.copy2(BASE/name,src);m=analyze(src);before=sha256_file(src)
   ex=lossless_export.export(src,before,m,FFMPEG,'UNPLAYABLE',True);self.assertEqual(ex['status'],'CREATED');self.assertEqual(len(ex['outputs']),expected['expected_output_count']);self.assertEqual(before,sha256_file(src))
   for o,er in zip(ex['outputs'],expected['expected_regions']):
    man=o['manifest'];pr=man['native_profile'];self.assertEqual(man['derivation_kind'],'RECOVERED_SEGMENTED_OPEN_PARTIAL_LOSSLESS');self.assertEqual(man['materialization'],'INDEPENDENT_NATIVE_PROFILE_PROVEN_REGION');self.assertEqual([pr['mpeg_version'],pr['layer'],pr['sample_rate'],pr['channels']],er['profile'])
    self.assertEqual(man['source_byte_start'],er['source_byte_start']);self.assertEqual(man['source_byte_end'],er['source_byte_end']);self.assertEqual(man['discarded_context_samples'],er['discarded_context_samples']);self.assertEqual(man['gap_before_index'],er['gap_before_index']);self.assertEqual(man['gap_after_index'],er['gap_after_index']);self.assertEqual(man['sample_count'],er['sample_count'])
    self.assertEqual(man['source_region_pcm_sha256'],er['pcm_sha256']);self.assertEqual(man['flac_decoded_pcm_sha256'],er['pcm_sha256']);self.assertNotEqual(er['pcm_sha256'],EMPTY_SHA);self.assertGreater(man['sample_count'],0);self.assertEqual(man['coverage_claim'],'PROVEN_REGIONS_ONLY_NO_FULL_TIMELINE_CLAIM');self.assertEqual(man['resampling'],'NONE');self.assertEqual(man['channel_remix'],'NONE');self.assertEqual(man['synthesized_gap_silence'],[]);self.assertEqual(man['validation_result'],'PASS')
   first=sorted(p.name for p in Path(td).glob('*recovered-segmented-open-partial-lossless*.flac'))
   ex2=lossless_export.export(src,before,m,FFMPEG,'UNPLAYABLE',True);self.assertEqual(ex2['status'],'REUSED');self.assertEqual(len(ex2['outputs']),expected['expected_output_count']);self.assertTrue(all(x['status']=='REUSED' for x in ex2['outputs']));self.assertEqual(sorted(p.name for p in Path(td).glob('*recovered-segmented-open-partial-lossless*.flac')),first)
 def test_terminal_and_unbracketed_exports_are_exact_and_reusable(self):
  for name in ('01_unplayable_terminal_truncation.mp3','02_unplayable_gap_plus_terminal_truncation.mp3','03_unplayable_unbracketed_late_gap.mp3'):self._export_case(name)
 def test_pipeline_classification_and_second_run_reuse(self):
  with tempfile.TemporaryDirectory() as td:
   src=Path(td)/'case.mp3';shutil.copy2(BASE/'01_unplayable_terminal_truncation.mp3',src)
   a=analyze_file(src,CFG,ROOT,FFMPEG,FFPROBE);self.assertEqual(a.playability,'UNPLAYABLE');self.assertEqual(a.final_status,['RECOVERED_SEGMENTED_OPEN_PARTIAL_LOSSLESS']);self.assertEqual(a.run_status,'SUCCESS_WITH_RECOVERY');self.assertEqual(sum(len(x.get('outputs',[])) for x in a.lossless_export),2)
   b=analyze_file(src,CFG,ROOT,FFMPEG,FFPROBE);self.assertEqual(sum(1 for e in b.lossless_export for o in e.get('outputs',[]) if o.get('status')=='REUSED'),2)
 def test_markdown_declares_open_ended_coverage(self):
  with tempfile.TemporaryDirectory() as td:
   src=Path(td)/'case.mp3';shutil.copy2(BASE/'03_unplayable_unbracketed_late_gap.mp3',src);a=analyze_file(src,CFG,ROOT,FFMPEG,FFPROBE).to_dict()
   run={'run_id':'v23','started_at':'2026-08-16T22:30:00-03:00','summary':{'discovered':1,'processed':1,'ok':0,'with_findings':1,'skipped':0,'failed':0,'repaired_outputs_created':0,'lossless_outputs_created':2,'outputs_reused':0,'candidates_rejected':0},'files':[a]};p=Path(td)/'r.md';write_md(p,run);txt=p.read_text(encoding="utf-8")
   self.assertIn('RECOVERED_SEGMENTED_OPEN_PARTIAL_LOSSLESS',txt);self.assertIn('PROVEN_REGIONS_ONLY_NO_FULL_TIMELINE_CLAIM',txt);self.assertIn('intervalo ausente sintetizado: `NONE`',txt)
if __name__=='__main__':unittest.main()
