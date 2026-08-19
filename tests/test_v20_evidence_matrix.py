from __future__ import annotations
import hashlib,json,shutil,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
from formats.mpeg import analyze
from app.evidence_matrix import build_mpeg_evidence_matrix
from app.config import load_config
from app.pipeline import analyze_file
from reporting.markdown_report import write_md
MAN=json.loads((ROOT/'samples/evidence_matrix_v20_manifest.json').read_text(encoding="utf-8"))
BASE=ROOT/'samples/evidence_matrix_v20'
FFMPEG=shutil.which('ffmpeg');FFPROBE=shutil.which('ffprobe');CFG=load_config(ROOT/'config.toml')

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def issue_codes(m):return [i.code for i in m['issues']]
def ev(ff_n=100,mp_n=100,ff_done=True,mp_done=True):
 return {'policy':'EVIDENCE_ONLY_NONCANONICAL','canonical_decoder':'ffmpeg','independent_decoder':'mpg123',
  'ffmpeg':{'attempted':True,'decoder':'ffmpeg','completed':ff_done,'passed':ff_done,'sample_frames':ff_n,'pcm_sha256':'a'*64},
  'mpg123':{'attempted':True,'available':True,'decoder':'mpg123','completed':mp_done,'passed':mp_done,'sample_frames':mp_n,'pcm_sha256':'b'*64,'decoder_version':'mpg123 1.33.7','decoder_binary_sha256':'c'*64,'supply_chain_trust':'PINNED_SHA256'},
  'agreement':{'both_completed':ff_done and mp_done,'completion_equal':ff_done==mp_done,'sample_frame_count_equal':(ff_n==mp_n) if ff_done and mp_done else None,'raw_s32_pcm_sha256_equal':False if ff_done and mp_done else None,'pcm_hash_interpretation':'informational_only_decoder_synthesis_may_differ'}}

class EvidenceMatrixV20(unittest.TestCase):
 def test_fixture_hashes_issues_and_interpretations(self):
  for name,c in MAN['cases'].items():
   p=BASE/name;self.assertEqual(sha(p),c['sha256'],name);m=analyze(p);self.assertEqual(issue_codes(m),c['issues'],name)
   strict={'completed':True,'passed':not any(i.code in ('MPEG_SYNC_LOSS','TRUNCATED_MPEG_FRAME') for i in m['issues'])}
   q,added=build_mpeg_evidence_matrix(m,ev(),strict,{'attempted':True,'completed':True})
   self.assertEqual(q['interpretation'],c['interpretation'],name);self.assertEqual(q['repair_authority'],'NONE');self.assertFalse(q['raw_pcm_hash_equality_used_for_integrity']);self.assertFalse(added,name)
 def test_correlated_damage_and_heterogeneity_are_distinct(self):
  a=analyze(BASE/'04_coherent_heterogeneity.mp3');qa,_=build_mpeg_evidence_matrix(a,ev(ff_n=100,mp_n=80),{'completed':True,'passed':True},{'attempted':True,'completed':True})
  b=analyze(BASE/'05_multisignal_structural_damage.mp3');qb,_=build_mpeg_evidence_matrix(b,ev(),{'completed':True,'passed':False},{'attempted':True,'completed':True})
  self.assertEqual(qa['interpretation'],'COHERENT_HETEROGENEITY');self.assertEqual(qa['signals']['decoder_sample_count_divergence_explained_by'],'HETEROGENEOUS_STREAM_PARAMETERS')
  self.assertEqual(qb['interpretation'],'CORROBORATED_STRUCTURAL_DAMAGE');self.assertIn('FRAMING',qb['active_evidence_domains']);self.assertIn('PARAMETER_SEGMENTATION',qb['active_evidence_domains'])
 def test_unexplained_decoder_sample_count_disagreement_is_report_only(self):
  m=analyze(BASE/'00_consistent_healthy.mp3');q,added=build_mpeg_evidence_matrix(m,ev(ff_n=100,mp_n=101),{'completed':True,'passed':True},{'attempted':True,'completed':True})
  self.assertEqual(q['interpretation'],'DECODER_EVIDENCE_DISAGREEMENT');self.assertEqual([i.code for i in added],['DECODER_SAMPLE_COUNT_DISAGREEMENT']);self.assertEqual(added[0].repairability,'NONE')

@unittest.skipUnless(FFMPEG and FFPROBE,'ffmpeg/ffprobe required')
class EvidenceMatrixPipelineV20(unittest.TestCase):
 def test_pipeline_surfaces_matrix_and_never_creates_repair_authority(self):
  p=BASE/'01_isolated_crc_inconsistency.mp3'
  with patch('app.pipeline.independent_decoder_evidence',return_value=ev()):
   a=analyze_file(p,CFG,ROOT,FFMPEG,FFPROBE,'mpg123','PINNED_SHA256')
  q=a.format_facts['evidence_consistency'];self.assertEqual(q['interpretation'],'ISOLATED_CRC_INCONSISTENCY');self.assertEqual(q['repair_authority'],'NONE');self.assertFalse(a.repair_plan);self.assertFalse(a.repair_execution)
 def test_pipeline_adds_unexplained_sample_count_finding_only(self):
  p=BASE/'00_consistent_healthy.mp3'
  with patch('app.pipeline.independent_decoder_evidence',return_value=ev(ff_n=100,mp_n=101)):
   a=analyze_file(p,CFG,ROOT,FFMPEG,FFPROBE,'mpg123','PINNED_SHA256')
  self.assertIn('DECODER_SAMPLE_COUNT_DISAGREEMENT',[i.code for i in a.issues]);self.assertEqual(a.final_status,['ANOMALY_UNCHANGED']);self.assertFalse(a.repair_execution)
 def test_markdown_surfaces_matrix_policy(self):
  p=BASE/'03_isolated_seek_metadata_nonconformance.mp3'
  with patch('app.pipeline.independent_decoder_evidence',return_value=ev()):
   a=analyze_file(p,CFG,ROOT,FFMPEG,FFPROBE,'mpg123','PINNED_SHA256')
  run={'run_id':'x','started_at':'2026-08-16T20:30:00-03:00','summary':{'discovered':1,'processed':1,'ok':0,'with_findings':1,'skipped':0,'failed':0,'repaired_outputs_created':0,'lossless_outputs_created':0,'outputs_reused':0,'candidates_rejected':0},'files':[a.to_dict()]}
  with tempfile.TemporaryDirectory() as td:
   out=Path(td)/'r.md';write_md(out,run);txt=out.read_text(encoding="utf-8")
  self.assertIn('Matriz de consistencia cruzada de evidencia MPEG',txt);self.assertIn('CORROBORATE_DO_NOT_REPAIR_FROM_DISAGREEMENT',txt);self.assertIn('ISOLATED_METADATA_NONCONFORMANCE',txt)

if __name__=='__main__':unittest.main()
