from __future__ import annotations
import copy,hashlib,json,os,shutil,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
from app.config import load_config
from app.pipeline import analyze_file
from formats.identify import identify
from formats.asf_wma import analyze
from reporting.markdown_report import write_md

BASE=ROOT/'samples/asf_wma_v36'
MAN=json.loads((ROOT/'samples/asf_wma_v36_manifest.json').read_text(encoding='utf-8'))
CFG=load_config(ROOT/'config.toml')
CFG_AUDIT=copy.deepcopy(CFG)
CFG_AUDIT['lossless_recovery']['enabled']=False
FFMPEG=os.environ.get('LOSSYDOCTOR_FFMPEG') or shutil.which('ffmpeg')
FFPROBE=os.environ.get('LOSSYDOCTOR_FFPROBE') or shutil.which('ffprobe')

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()

class AsfWmaDeepAuditV36(unittest.TestCase):
    def test_manifest_hashes_issue_sets_and_binary_distinctness(self):
        self.assertEqual(MAN['app_version'],'0.36.0');self.assertEqual(MAN['policy'],'0.36-asf-wma-deep-audit-1')
        seen=set()
        for n,c in MAN['cases'].items():
            p=BASE/n; h=sha(p); self.assertEqual(h,c['sha256'],n); self.assertNotIn(h,seen,n); seen.add(h)
            q=analyze(p); self.assertEqual([i.code for i in q['issues']],c['expected_issues'],n)
        self.assertEqual(len(seen),7)

    def test_healthy_wmav1_wmav2_object_stream_and_packet_facts(self):
        for n,tag,sr,ch,packets in (
            ('00_healthy_wmav2.wma','0x0161',44100,2,11),
            ('01_healthy_wmav1.wma','0x0160',22050,1,5),
        ):
            q=analyze(BASE/n); f=q['facts']; a=f['asf']; w=f['streams'][0]['waveformatex']; p=f['packets']
            self.assertEqual(q['issues'],[],n); self.assertEqual((a['header_reserved_1'],a['header_reserved_2']),(1,2),n)
            self.assertEqual(a['header_object_count_declared'],a['header_object_count_parsed'],n)
            self.assertEqual((w['format_tag_hex'],w['sample_rate'],w['channels']),(tag,sr,ch),n); self.assertTrue(w['valid'],n)
            self.assertEqual(a['file_properties']['data_packets_count'],packets,n); self.assertEqual(a['data_object']['total_data_packets'],packets,n)
            self.assertEqual(a['physical_complete_packet_count'],packets,n); self.assertEqual(a['packet_region_remainder_bytes'],0,n)
            self.assertEqual(p['parsed_count'],packets,n); self.assertTrue(p['all_valid'],n); self.assertLessEqual(p['send_time_start_ms'],p['send_time_end_ms'],n)

    def test_mutations_are_isolated_to_expected_structural_domains(self):
        for n,c in MAN['cases'].items():
            q=analyze(BASE/n); f=q['facts']; a=f['asf']; w=f['streams'][0]['waveformatex']
            self.assertEqual(w['format_tag_hex'],c['expected_format_tag'],n); self.assertEqual(w['sample_rate'],c['expected_sample_rate'],n); self.assertEqual(w['channels'],c['expected_channels'],n)
            self.assertEqual(a['file_properties']['data_packets_count'],c['expected_declared_packets'],n)
            self.assertEqual(a['physical_complete_packet_count'],c['expected_physical_complete_packets'],n)
            self.assertEqual(a['packet_region_remainder_bytes'],c['expected_packet_remainder_bytes'],n)
        self.assertEqual(analyze(BASE/'02_file_size_mismatch.wma')['facts']['packets']['all_valid'],True)
        self.assertEqual(analyze(BASE/'03_packet_count_mismatch.wma')['facts']['packets']['all_valid'],True)
        self.assertFalse(analyze(BASE/'05_invalid_waveformatex_tag.wma')['facts']['streams'][0]['waveformatex']['valid'])
        self.assertTrue(analyze(BASE/'06_nonmonotonic_send_time.wma')['facts']['packets']['all_valid'])

    def test_identify_routes_asf_wma_to_deep_parser(self):
        q=identify(BASE/'00_healthy_wmav2.wma')
        self.assertEqual((q['container'],q['codec'],q['confidence']),('ASF','wma','HIGH')); self.assertIn('asf_wma',q)
        self.assertEqual(q['asf_wma']['facts']['streams'][0]['waveformatex']['format_tag_hex'],'0x0161')

    @unittest.skipUnless(FFMPEG and FFPROBE,'ffmpeg/ffprobe required')
    def test_pipeline_validity_playability_and_audit_only_authority(self):
        got={n:analyze_file(BASE/n,CFG_AUDIT,ROOT,FFMPEG,FFPROBE) for n in MAN['cases']}
        for n,c in MAN['cases'].items():
            a=got[n]; self.assertEqual(a.playability,c['expected_playability'],n); self.assertEqual(a.repair_execution,[],n); self.assertEqual(a.lossless_export,[],n)
            self.assertTrue(any(d.get('code')=='WMA_ASF_DECODER_CONVERGENCE_AUTHORITY' for d in a.policy_decisions),n); self.assertEqual(a.lossless_export,[],n)
        self.assertEqual(got['00_healthy_wmav2.wma'].validity_domains['PACKET_VALIDITY'],'VALIDATED_FIXED_PACKET_STRUCTURE')
        self.assertEqual(got['00_healthy_wmav2.wma'].validity_domains['TIMELINE_VALIDITY'],'VALIDATED_PACKET_SEND_TIMES')
        self.assertEqual(got['04_truncated_data_packet.wma'].validity_domains['DECODE_VALIDITY'],'USABLE_WITH_ERRORS')
        self.assertEqual(got['04_truncated_data_packet.wma'].validity_domains['PACKET_VALIDITY'],'NONCONFORMANT_OR_INCOMPLETE')
        self.assertEqual(got['05_invalid_waveformatex_tag.wma'].validity_domains['CODEC_HEADER_VALIDITY'],'NONCONFORMANT_OR_DAMAGED')
        self.assertEqual(got['06_nonmonotonic_send_time.wma'].validity_domains['TIMELINE_VALIDITY'],'NONCONFORMANT_OR_INCOMPLETE_PACKET_TIMELINE')
        self.assertFalse(got['00_healthy_wmav2.wma'].canonical_presentation_window['determined'])
        self.assertEqual(got['00_healthy_wmav2.wma'].canonical_presentation_window['presentation_model'],'ASF_FILE_PROPERTIES_DURATION_NOT_SAMPLE_EXACT')

    @unittest.skipUnless(FFMPEG and FFPROBE,'ffmpeg/ffprobe required')
    def test_markdown_exposes_asf_wma_structural_audit(self):
        a=analyze_file(BASE/'00_healthy_wmav2.wma',CFG_AUDIT,ROOT,FFMPEG,FFPROBE).to_dict()
        run={'run_id':'v36','started_at':'2026-08-17T03:00:00-03:00','summary':{'discovered':1,'processed':1,'ok':1,'with_findings':0,'skipped':0,'failed':0,'repaired_outputs_created':0,'lossless_outputs_created':0,'outputs_reused':0,'candidates_rejected':0},'files':[a]}
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'r.md';write_md(p,run);txt=p.read_text(encoding='utf-8')
            self.assertIn('Auditoría estructural ASF/WMA',txt); self.assertIn('0x0161',txt); self.assertIn('Windows Media Audio Standard',txt)
            self.assertIn('físicamente completos `11`',txt); self.assertIn('Procedencia de objetos multimedia ASF/WMA y evidencia de recuperación',txt); self.assertIn('ASF_WMA_DECODER_CONVERGENCE_EVIDENCE_ONLY',txt)

    @unittest.skipUnless(FFMPEG and FFPROBE,'ffmpeg/ffprobe required')
    def test_e2e_status_shape_two_ok_five_findings_zero_derivatives(self):
        rows=[analyze_file(BASE/n,CFG_AUDIT,ROOT,FFMPEG,FFPROBE) for n in sorted(MAN['cases'])]
        self.assertEqual(sum(a.run_status=='SUCCESS' for a in rows),2); self.assertEqual(sum(a.run_status=='SUCCESS_WITH_FINDINGS' for a in rows),5); self.assertEqual(sum(a.run_status=='FAILED' for a in rows),0)
        self.assertEqual(sum(bool(a.repair_execution) for a in rows),0); self.assertEqual(sum(bool(a.lossless_export) for a in rows),0)
        self.assertEqual(sum(a.playability=='UNPLAYABLE' for a in rows),1)

if __name__=='__main__':unittest.main()
