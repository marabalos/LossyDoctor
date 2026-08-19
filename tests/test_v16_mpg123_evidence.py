from __future__ import annotations
import os, shutil, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
from app.external import independent_decoder_evidence
from app.config import load_config
from app.pipeline import analyze_file
from reporting.markdown_report import write_md

FFMPEG=shutil.which('ffmpeg');FFPROBE=shutil.which('ffprobe');CFG=load_config(ROOT/'config.toml')

def ev(ff_done=True,mp_done=True,ff_n=100,mp_n=100,trust='PINNED_SHA256'):
    return {
      'policy':'EVIDENCE_ONLY_NONCANONICAL','canonical_decoder':'ffmpeg','independent_decoder':'mpg123',
      'ffmpeg':{'attempted':True,'decoder':'ffmpeg','completed':ff_done,'passed':ff_done,'output_bytes':ff_n*8,'sample_frames':ff_n,'pcm_sha256':'a'*64},
      'mpg123':{'attempted':True,'available':True,'decoder':'mpg123','completed':mp_done,'passed':mp_done,'output_bytes':mp_n*8,'sample_frames':mp_n,'pcm_sha256':'b'*64,'decoder_version':'mpg123 1.33.7','decoder_binary_sha256':'c'*64,'supply_chain_trust':trust},
      'agreement':{'both_completed':ff_done and mp_done,'completion_equal':ff_done==mp_done,'sample_frame_count_equal':(ff_n==mp_n) if ff_done and mp_done else None,'raw_s32_pcm_sha256_equal':False if ff_done and mp_done else None,'pcm_hash_interpretation':'informational_only_decoder_synthesis_may_differ'}
    }

class MPG123EvidencePrimitive(unittest.TestCase):
 def test_evidence_is_noncanonical_and_pcm_hash_difference_is_informational(self):
  with patch('app.external.ffmpeg_evidence_decode',return_value=ev()['ffmpeg']), patch('app.external.mpg123_evidence_decode',return_value=ev()['mpg123']):
   q=independent_decoder_evidence(Path('x.mp3'),'ffmpeg','mpg123',2,30,'PINNED_SHA256')
  self.assertEqual(q['policy'],'EVIDENCE_ONLY_NONCANONICAL');self.assertEqual(q['canonical_decoder'],'ffmpeg');self.assertEqual(q['independent_decoder'],'mpg123')
  self.assertTrue(q['agreement']['both_completed']);self.assertTrue(q['agreement']['sample_frame_count_equal']);self.assertFalse(q['agreement']['raw_s32_pcm_sha256_equal'])

@unittest.skipUnless(FFMPEG and FFPROBE,'ffmpeg/ffprobe required')
class MPG123EvidencePipeline(unittest.TestCase):
 def test_healthy_mpeg_records_independent_evidence_without_changing_classification(self):
  p=ROOT/'samples/crc_v14/00_mpeg1_l3_crc_healthy.mp3'
  with patch('app.pipeline.independent_decoder_evidence',return_value=ev()):
   a=analyze_file(p,CFG,ROOT,FFMPEG,FFPROBE,'mpg123','PINNED_SHA256')
  self.assertEqual(a.final_status,['OK']);self.assertEqual(a.format_facts['decoder_evidence']['policy'],'EVIDENCE_ONLY_NONCANONICAL')
  self.assertFalse(any(i.code=='DECODER_COMPLETION_DISAGREEMENT' for i in a.issues))
 def test_clean_stream_completion_disagreement_becomes_report_only_finding(self):
  p=ROOT/'samples/crc_v14/00_mpeg1_l3_crc_healthy.mp3'
  with patch('app.pipeline.independent_decoder_evidence',return_value=ev(ff_done=True,mp_done=False)):
   a=analyze_file(p,CFG,ROOT,FFMPEG,FFPROBE,'mpg123','PINNED_SHA256')
  hit=[i for i in a.issues if i.code=='DECODER_COMPLETION_DISAGREEMENT'];self.assertEqual(len(hit),1);self.assertEqual(hit[0].repairability,'NONE')
  self.assertFalse(a.repair_plan);self.assertFalse(a.repair_execution)
 def test_markdown_surfaces_decoder_identity_trust_and_counts(self):
  p=ROOT/'samples/crc_v14/00_mpeg1_l3_crc_healthy.mp3'
  with patch('app.pipeline.independent_decoder_evidence',return_value=ev(ff_n=100,mp_n=101,trust='PINNED_SHA256')):
   a=analyze_file(p,CFG,ROOT,FFMPEG,FFPROBE,'mpg123','PINNED_SHA256')
  run={'run_id':'x','started_at':'2026-08-16T19:00:00-03:00','summary':{'discovered':1,'processed':1,'ok':1,'with_findings':0,'skipped':0,'failed':0,'repaired_outputs_created':0,'lossless_outputs_created':0,'outputs_reused':0,'candidates_rejected':0},'files':[a.to_dict()]}
  with tempfile.TemporaryDirectory() as td:
   out=Path(td)/'r.md';write_md(out,run);txt=out.read_text(encoding="utf-8")
  self.assertIn('Evidencia de decodificador MPEG independiente',txt);self.assertIn('mpg123 1.33.7',txt);self.assertIn('PINNED_SHA256',txt);self.assertIn('`100` / `101`',txt)

if __name__=='__main__':unittest.main()
