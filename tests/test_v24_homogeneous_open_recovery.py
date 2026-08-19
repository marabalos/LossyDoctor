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
BASE=ROOT/'samples/homogeneous_open_recovery_v24'
MAN=json.loads((ROOT/'samples/homogeneous_open_recovery_v24_manifest.json').read_text(encoding="utf-8"))
EMPTY_SHA=hashlib.sha256(b'').hexdigest()
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

@unittest.skipUnless(FFMPEG and FFPROBE,'ffmpeg/ffprobe required')
class V24HomogeneousOpenRecovery(unittest.TestCase):
 def test_fixture_hashes_and_homogeneous_shapes(self):
  for name,c in MAN['cases'].items():
   p=BASE/name;self.assertEqual(sha(p),c['sha256']);m=analyze(p)
   self.assertEqual([i.code for i in m['issues']],c['issue_codes']);self.assertEqual(len(m['facts'].get('gaps') or []),c['gap_count']);self.assertEqual(bool(m['facts'].get('truncated_final_frame')),c['truncated_final_frame']);self.assertEqual(m['facts']['parameter_segments']['hard_profile_transition_count'],0)
 def test_gate_is_homogeneous_fallback_only(self):
  m=analyze(BASE/'01_unplayable_terminal_truncation.mp3');a=lossless_export.assess(m,'UNPLAYABLE');self.assertTrue(a['eligible_homogeneous_open_partial']);g=a['homogeneous_open_recovery_gate'];self.assertEqual(g['repair_priority'],'VERIFIED_BITSTREAM_REPAIR_PRECEDES_PCM');self.assertEqual(g['coverage_claim'],'PROVEN_HOMOGENEOUS_REGIONS_ONLY_NO_FULL_TIMELINE_CLAIM')
  b=lossless_export.assess(m,'PLAYABLE');self.assertFalse(b['eligible_homogeneous_open_partial']);self.assertIn('reproducible',b['homogeneous_open_recovery_gate']['reason'])
 def test_crc_and_reservoir_overrun_still_block(self):
  m=analyze(BASE/'02_unplayable_internal_gap.mp3');x=copy.deepcopy(m);x['facts']['crc_protection']['mismatch_count']=1;self.assertFalse(lossless_export.assess(x,'UNPLAYABLE')['eligible_homogeneous_open_partial'])
  y=copy.deepcopy(m);y['facts']['bit_reservoir']['main_data_overrun_frame_indices']=[7];self.assertFalse(lossless_export.assess(y,'UNPLAYABLE')['eligible_homogeneous_open_partial'])
 def _export_case(self,name):
  expected=MAN['cases'][name]
  with tempfile.TemporaryDirectory() as td:
   src=Path(td)/name;shutil.copy2(BASE/name,src);m=analyze(src);before=sha256_file(src)
   ex=lossless_export.export(src,before,m,FFMPEG,'UNPLAYABLE',True);self.assertEqual(ex['status'],'CREATED');self.assertEqual(len(ex['outputs']),expected['expected_output_count']);self.assertEqual(before,sha256_file(src))
   for o,er in zip(ex['outputs'],expected['expected_regions']):
    man=o['manifest'];self.assertEqual(man['derivation_kind'],'RECOVERED_HOMOGENEOUS_OPEN_PARTIAL_LOSSLESS');self.assertEqual(man['materialization'],'INDEPENDENT_HOMOGENEOUS_PROVEN_REGION');self.assertEqual(man['source_byte_start'],er['source_byte_start']);self.assertEqual(man['source_byte_end'],er['source_byte_end']);self.assertEqual(man['discarded_context_samples'],er['discarded_context_samples']);self.assertEqual(man['gap_before_index'],er['gap_before_index']);self.assertEqual(man['gap_after_index'],er['gap_after_index']);self.assertEqual(man['sample_count'],er['sample_count']);self.assertEqual(man['source_region_pcm_sha256'],er['pcm_sha256']);self.assertEqual(man['flac_decoded_pcm_sha256'],er['pcm_sha256']);self.assertNotEqual(er['pcm_sha256'],EMPTY_SHA);self.assertGreater(man['sample_count'],0);self.assertEqual(man['coverage_claim'],'PROVEN_HOMOGENEOUS_REGIONS_ONLY_NO_FULL_TIMELINE_CLAIM');self.assertEqual(man['repair_priority'],'VERIFIED_BITSTREAM_REPAIR_PRECEDES_PCM');self.assertEqual(man['resampling'],'NONE');self.assertEqual(man['channel_remix'],'NONE');self.assertEqual(man['synthesized_gap_silence'],[])
   names=sorted(p.name for p in Path(td).glob('*recovered-homogeneous-open-partial-lossless*.flac'));ex2=lossless_export.export(src,before,m,FFMPEG,'UNPLAYABLE',True);self.assertEqual(ex2['status'],'REUSED');self.assertEqual(len(ex2['outputs']),expected['expected_output_count']);self.assertTrue(all(x['status']=='REUSED' for x in ex2['outputs']));self.assertEqual(sorted(p.name for p in Path(td).glob('*recovered-homogeneous-open-partial-lossless*.flac')),names)
 def test_terminal_gap_and_combined_exports_exact(self):
  for n in ('01_unplayable_terminal_truncation.mp3','02_unplayable_internal_gap.mp3','03_unplayable_gap_plus_truncation.mp3'):self._export_case(n)
 def test_verified_bitstream_repair_precedes_pcm(self):
  with tempfile.TemporaryDirectory() as td:
   src=Path(td)/'priority.mp3';shutil.copy2(BASE/'00_bitstream_repair_priority.mp3',src);a=analyze_file(src,CFG,ROOT,FFMPEG,FFPROBE)
   self.assertEqual(a.playability,'UNPLAYABLE');self.assertTrue(a.recovery_assessment['eligible_homogeneous_open_partial']);self.assertEqual(a.final_status,['REPAIRED_SAFE']);self.assertEqual(a.lossless_export,[]);self.assertTrue(any(x.get('status')=='CREATED' for x in a.repair_execution));self.assertTrue(any(d.get('code')=='BITSTREAM_REPAIR_PRECEDES_PCM_DERIVATION' for d in a.policy_decisions))
 def test_pipeline_classification_and_reuse(self):
  with tempfile.TemporaryDirectory() as td:
   src=Path(td)/'case.mp3';shutil.copy2(BASE/'02_unplayable_internal_gap.mp3',src);a=analyze_file(src,CFG,ROOT,FFMPEG,FFPROBE);self.assertEqual(a.final_status,['RECOVERED_HOMOGENEOUS_OPEN_PARTIAL_LOSSLESS']);self.assertEqual(a.run_status,'SUCCESS_WITH_RECOVERY');self.assertEqual(sum(len(x.get('outputs',[])) for x in a.lossless_export),2)
   b=analyze_file(src,CFG,ROOT,FFMPEG,FFPROBE);self.assertEqual(sum(1 for e in b.lossless_export for o in e.get('outputs',[]) if o.get('status')=='REUSED'),2)
 def test_markdown_declares_homogeneous_proven_coverage(self):
  with tempfile.TemporaryDirectory() as td:
   src=Path(td)/'case.mp3';shutil.copy2(BASE/'01_unplayable_terminal_truncation.mp3',src);a=analyze_file(src,CFG,ROOT,FFMPEG,FFPROBE).to_dict();run={'run_id':'v24','started_at':'2026-08-16T22:45:00-03:00','summary':{'discovered':1,'processed':1,'ok':0,'with_findings':1,'skipped':0,'failed':0,'repaired_outputs_created':0,'lossless_outputs_created':1,'outputs_reused':0,'candidates_rejected':0},'files':[a]};p=Path(td)/'r.md';write_md(p,run);txt=p.read_text(encoding="utf-8");self.assertIn('RECOVERED_HOMOGENEOUS_OPEN_PARTIAL_LOSSLESS',txt);self.assertIn('PROVEN_HOMOGENEOUS_REGIONS_ONLY_NO_FULL_TIMELINE_CLAIM',txt);self.assertIn('VERIFIED_BITSTREAM_REPAIR_PRECEDES_PCM',txt);self.assertIn('intervalo ausente sintetizado: `NONE`',txt)
if __name__=='__main__':unittest.main()
