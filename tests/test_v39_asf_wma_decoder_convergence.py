from __future__ import annotations
import copy,hashlib,json,os,shutil,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
from app.config import load_config
from app.pipeline import analyze_file
from app.version import APP_VERSION,POLICY_VERSION
from app.external import _decode_s32_file
from formats.asf_wma import analyze
from reporting.markdown_report import write_md

BASE=ROOT/'samples/asf_wma_decoder_convergence_v39'
MAN=json.loads((ROOT/'samples/asf_wma_decoder_convergence_v39_manifest.json').read_text(encoding='utf-8'))
CFG=load_config(ROOT/'config.toml')
CFG_AUDIT=copy.deepcopy(CFG)
CFG_AUDIT['lossless_recovery']['enabled']=False
FFMPEG=os.environ.get('LOSSYDOCTOR_FFMPEG') or shutil.which('ffmpeg')
FFPROBE=os.environ.get('LOSSYDOCTOR_FFPROBE') or shutil.which('ffprobe')

def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def analysis(name): return analyze_file(BASE/name,CFG_AUDIT,ROOT,FFMPEG,FFPROBE)

@unittest.skipUnless(FFMPEG and FFPROBE,'ffmpeg/ffprobe required')
class AsfWmaDecoderConvergenceV39(unittest.TestCase):
    def test_manifest_hashes_issue_sets_and_binary_distinctness(self):
        self.assertEqual(MAN['app_version'],'0.39.0');self.assertEqual(MAN['policy'],'0.39-asf-wma-decoder-convergence-evidence-1')
        self.assertGreaterEqual(tuple(map(int,APP_VERSION.split('.'))),(0,39,0));self.assertTrue(POLICY_VERSION)
        seen=set()
        for n,c in MAN['cases'].items():
            p=BASE/n;h=sha(p);self.assertEqual(h,c['sha256'],n);self.assertNotIn(h,seen,n);seen.add(h)
            q=analyze(p);self.assertEqual([i.code for i in q['issues']],c['expected_parser_issues'],n)
            self.assertEqual(q['facts']['media_objects']['complete_media_objects'],c['expected_complete_media_objects'],n)
            self.assertEqual(q['facts']['media_objects']['incomplete_media_objects'],c['expected_incomplete_media_objects'],n)
        # Corpus must not byte-repeat v0.38 fixtures.
        old={sha(p) for p in (ROOT/'samples/asf_wma_demux_decoder_v38').glob('*.wma')}
        self.assertTrue(seen.isdisjoint(old))

    def test_controls_need_no_convergence_candidate(self):
        for n,frame_len in [('00_healthy_wmav2_convergence_control.wma',2048),('01_healthy_wmav1_convergence_control.wma',1024)]:
            a=analysis(n);c=a.format_facts['decoder_convergence_evidence']
            self.assertEqual(a.run_status,'SUCCESS',n);self.assertEqual(a.issues,[],n)
            self.assertEqual(c['candidate_count'],0,n);self.assertEqual(c['eligibility'],'NOT_REQUIRED_OR_NO_PROVEN_MISSING_MEDIA_OBJECT_RUN',n)
            self.assertEqual(c.get('observed_decoder_frame_len_samples'),None,n)  # no gap => full-decode convergence probe is unnecessary
            self.assertFalse(c['publication_enabled']);self.assertEqual(c['pcm_recovery_authority'],'NONE')

    def test_single_and_double_gaps_validate_one_survivor_context_rule(self):
        cases={
            '02_wmav2_single_mid_gap.wma':(1,13,14),
            '03_wmav1_single_mid_gap.wma':(1,13,14),
            '04_wmav2_double_mid_gap.wma':(2,14,15),
            '05_wmav1_double_mid_gap.wma':(2,14,15),
        }
        for n,(missing,context_obj,candidate_obj) in cases.items():
            a=analysis(n);c=a.format_facts['decoder_convergence_evidence'];x=c['candidates'][0]
            self.assertEqual([i.code for i in a.issues],['ASF_WMA_DEMUX_TIMELINE_DISCONTINUITY'],n)
            self.assertEqual(c['candidate_count'],1,n);self.assertEqual(c['validated_candidate_count'],1,n);self.assertTrue(c['all_candidates_validated'],n)
            self.assertEqual(x['missing_media_object_count'],missing,n);self.assertEqual(x['context_media_object_number'],context_obj,n);self.assertEqual(x['expected_first_candidate_media_object_number'],candidate_obj,n)
            self.assertTrue(x['seek_decode_matches_full_decode_suffix'],n);self.assertTrue(x['one_surviving_packet_context_observed'],n)
            self.assertEqual(x['status'],'VALIDATED_DETERMINISTIC_CONVERGENCE_EVIDENCE_ONLY',n)
            self.assertEqual(a.validity_domains['DECODER_CONVERGENCE_VALIDITY'],'VALIDATED_DETERMINISTIC_POST_GAP_CONVERGENCE_EVIDENCE_ONLY',n)
            self.assertEqual(a.repair_execution,[]);self.assertEqual(a.lossless_export,[])

    def test_late_gap_converges_when_two_survivors_remain(self):
        a=analysis('06_wmav2_late_gap.wma');c=a.format_facts['decoder_convergence_evidence'];x=c['candidates'][0]
        self.assertEqual(x['missing_media_object_count'],1);self.assertEqual(x['context_media_object_number'],41);self.assertEqual(x['expected_first_candidate_media_object_number'],42)
        self.assertTrue(x['validated']);self.assertTrue(c['all_candidates_validated'])

    def test_timestamp_only_jump_and_incomplete_object_do_not_become_convergence_candidates(self):
        a=analysis('07_timestamp_jump_without_missing_object.wma');c=a.format_facts['decoder_convergence_evidence']
        self.assertEqual([i.code for i in a.issues],['ASF_WMA_DEMUX_TIMELINE_DISCONTINUITY'])
        self.assertEqual(c['candidate_count'],0);self.assertEqual(c['eligibility'],'NOT_REQUIRED_OR_NO_PROVEN_MISSING_MEDIA_OBJECT_RUN')
        self.assertEqual(a.validity_domains['DECODER_CONVERGENCE_VALIDITY'],'NOT_REQUIRED_NO_PROVEN_MISSING_MEDIA_OBJECT_RUN')
        b=analysis('08_incomplete_media_object_negative.wma');d=b.format_facts['decoder_convergence_evidence']
        self.assertEqual([i.code for i in b.issues],['ASF_MEDIA_OBJECT_FRAGMENT_GAP'])
        self.assertEqual(d['candidate_count'],0);self.assertEqual(d['eligibility'],'BLOCKED_DEMUX_OR_MEDIA_OBJECT_MAPPING_NOT_PROVEN')
        self.assertEqual(b.validity_domains['DECODER_CONVERGENCE_VALIDITY'],'BLOCKED_INCOMPLETE_OR_UNPROVEN_MEDIA_OBJECT_EVIDENCE')

    def test_test_only_healthy_reference_equivalence_is_exact(self):
        refs={'wmav2':BASE/'00_healthy_wmav2_convergence_control.wma','wmav1':BASE/'01_healthy_wmav1_convergence_control.wma'}
        expected={
            '02_wmav2_single_mid_gap.wma':('wmav2',2,2048,11),
            '03_wmav1_single_mid_gap.wma':('wmav1',1,1024,11),
            '04_wmav2_double_mid_gap.wma':('wmav2',2,2048,12),
            '05_wmav1_double_mid_gap.wma':('wmav1',1,1024,12),
            '06_wmav2_late_gap.wma':('wmav2',2,2048,39),
        }
        for n,(kind,ch,frame_len,healthy_frame_index) in expected.items():
            a=analysis(n);x=a.format_facts['decoder_convergence_evidence']['candidates'][0]
            with tempfile.TemporaryDirectory() as td:
                td=Path(td);dam=td/'dam.raw';ref=td/'ref.raw'
                self.assertTrue(_decode_s32_file(BASE/n,dam,FFMPEG,300,x['seek_from_missing_interval_ms']/1000.0)['passed'],n)
                self.assertTrue(_decode_s32_file(refs[kind],ref,FFMPEG,300)['passed'],n)
                rb=ref.read_bytes();db=dam.read_bytes();off=healthy_frame_index*frame_len*ch*4
                self.assertEqual(db,rb[off:],n)
        # This equality belongs to the controlled acceptance corpus; arbitrary files do not inherit it as authority.
        self.assertTrue(analysis('02_wmav2_single_mid_gap.wma').format_facts['decoder_convergence_evidence']['reference_equivalence_is_test_only'])

    def test_markdown_and_e2e_remain_audit_only(self):
        rows=[analysis(n) for n in sorted(MAN['cases'])]
        self.assertEqual(sum(a.run_status=='SUCCESS' for a in rows),2);self.assertEqual(sum(a.run_status=='SUCCESS_WITH_FINDINGS' for a in rows),7);self.assertEqual(sum(a.run_status=='FAILED' for a in rows),0)
        self.assertEqual(sum(bool(a.repair_execution) for a in rows),0);self.assertEqual(sum(bool(a.lossless_export) for a in rows),0)
        a=analysis('02_wmav2_single_mid_gap.wma').to_dict()
        run={'run_id':'v39','started_at':'2026-08-17T14:00:00-03:00','summary':{'discovered':1,'processed':1,'ok':0,'with_findings':1,'skipped':0,'failed':0,'repaired_outputs_created':0,'lossless_outputs_created':0,'outputs_reused':0,'candidates_rejected':0},'files':[a]}
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'r.md';write_md(p,run);txt=p.read_text(encoding='utf-8')
            self.assertIn('Convergencia del decodificador ASF/WMA y evidencia de recuperación',txt)
            self.assertIn('primer objeto candidato `14`',txt);self.assertIn('igualdad del hash búsqueda/sufijo `True`',txt)
            self.assertIn('ASF_WMA_DECODER_CONVERGENCE_EVIDENCE_ONLY',txt)

if __name__=='__main__': unittest.main()
