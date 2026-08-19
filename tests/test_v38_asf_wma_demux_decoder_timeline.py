from __future__ import annotations
import copy,hashlib,json,os,shutil,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
from app.config import load_config
from app.pipeline import analyze_file
from app.version import APP_VERSION,POLICY_VERSION
from formats.asf_wma import analyze
from reporting.markdown_report import write_md

BASE=ROOT/'samples/asf_wma_demux_decoder_v38'
MAN=json.loads((ROOT/'samples/asf_wma_demux_decoder_v38_manifest.json').read_text(encoding='utf-8'))
CFG=load_config(ROOT/'config.toml')
CFG_AUDIT=copy.deepcopy(CFG)
CFG_AUDIT['lossless_recovery']['enabled']=False
FFMPEG=os.environ.get('LOSSYDOCTOR_FFMPEG') or shutil.which('ffmpeg')
FFPROBE=os.environ.get('LOSSYDOCTOR_FFPROBE') or shutil.which('ffprobe')

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()

@unittest.skipUnless(FFMPEG and FFPROBE,'ffmpeg/ffprobe required')
class AsfWmaDemuxDecoderTimelineV38(unittest.TestCase):
    def test_manifest_hashes_issue_sets_and_binary_distinctness(self):
        self.assertEqual(MAN['app_version'],'0.38.0');self.assertEqual(MAN['policy'],'0.38-asf-wma-demux-decoder-timeline-evidence-1')
        self.assertGreaterEqual(tuple(map(int,APP_VERSION.split('.'))),(0,38,0))
        seen=set()
        for n,c in MAN['cases'].items():
            p=BASE/n;h=sha(p);self.assertEqual(h,c['sha256'],n);self.assertNotIn(h,seen,n);seen.add(h)
            q=analyze(p);self.assertEqual([i.code for i in q['issues']],c['expected_parser_issues'],n)
            self.assertEqual(q['facts']['media_objects']['complete_media_objects'],c['expected_complete_media_objects'],n)
            self.assertEqual(q['facts']['media_objects']['incomplete_media_objects'],c['expected_incomplete_media_objects'],n)
        self.assertEqual(len(seen),8)

    def test_healthy_controls_map_media_objects_byte_exactly_to_demux_packets(self):
        expected={
            '00_healthy_wmav2_demux_decoder.wma':(44,43,2048,88064),
            '01_healthy_wmav1_demux_decoder.wma':(33,32,1024,32768),
        }
        for n,(packets,frames,nb_samples,total_samples) in expected.items():
            a=analyze_file(BASE/n,CFG_AUDIT,ROOT,FFMPEG,FFPROBE);d=a.format_facts['demux_decoder_evidence']
            self.assertEqual(a.run_status,'SUCCESS',n);self.assertEqual(a.issues,[],n)
            self.assertEqual((d['complete_media_object_count'],d['demux_packet_count']),(packets,packets),n)
            self.assertTrue(d['one_to_one_complete_media_object_mapping'],n);self.assertTrue(d['all_packet_hashes_equal'],n);self.assertTrue(d['all_packet_sizes_equal'],n)
            self.assertTrue(d['all_pts_match_media_object_presentation_minus_preroll'],n);self.assertEqual(d['demux_timeline_discontinuities'],[],n)
            self.assertEqual(d['decoded_frame_count'],frames,n);self.assertEqual(d['decoded_frame_nb_samples_values'],[nb_samples],n)
            self.assertEqual(d['decoded_sample_frames_from_ffprobe'],total_samples,n);self.assertEqual(d['decoder_output_sample_frames'],total_samples,n)
            self.assertEqual(d['first_timestamped_decoder_output_demux_packet_index'],2,n);self.assertEqual(d['untimestamped_flush_frame_count'],1,n)
            self.assertEqual(d['decoded_frame_max_frames_per_packet_position'],2,n);self.assertTrue(d['ffprobe_frame_sample_count_matches_raw_decode'],n)

    def test_preroll_shift_is_observed_not_misclassified_as_corruption(self):
        a=analyze_file(BASE/'02_valid_preroll_shift_control.wma',CFG_AUDIT,ROOT,FFMPEG,FFPROBE);d=a.format_facts['demux_decoder_evidence']
        self.assertEqual(a.run_status,'SUCCESS');self.assertEqual(a.issues,[])
        self.assertEqual(d['demux_pts_start_ms'],-100.0);self.assertTrue(d['all_pts_match_media_object_presentation_minus_preroll'])
        self.assertTrue(d['one_to_one_complete_media_object_mapping']);self.assertEqual(d['demux_timeline_discontinuities'],[])
        self.assertFalse(a.canonical_presentation_window['determined'])

    def test_complete_objects_can_still_expose_demux_timeline_discontinuity(self):
        a=analyze_file(BASE/'03_missing_complete_media_object.wma',CFG_AUDIT,ROOT,FFMPEG,FFPROBE);d=a.format_facts['demux_decoder_evidence']
        self.assertEqual([i.code for i in a.issues],['ASF_WMA_DEMUX_TIMELINE_DISCONTINUITY'])
        self.assertTrue(d['one_to_one_complete_media_object_mapping']);self.assertTrue(d['all_pts_match_media_object_presentation_minus_preroll'])
        self.assertEqual(len(d['demux_timeline_discontinuities']),1);self.assertEqual(d['demux_timeline_discontinuities'][0]['excess_or_deficit_ms'],47.0)
        b=analyze_file(BASE/'04_complete_object_presentation_time_jump.wma',CFG_AUDIT,ROOT,FFMPEG,FFPROBE);e=b.format_facts['demux_decoder_evidence']
        self.assertEqual([i.code for i in b.issues],['ASF_WMA_DEMUX_TIMELINE_DISCONTINUITY'])
        self.assertTrue(e['one_to_one_complete_media_object_mapping']);self.assertTrue(e['all_pts_match_media_object_presentation_minus_preroll'])
        self.assertEqual(len(e['demux_timeline_discontinuities']),2);self.assertFalse(e['demux_pts_monotonic'])

    def test_incomplete_media_objects_and_invalid_header_never_gain_pcm_authority(self):
        for n in ('05_internal_fragment_gap.wma','06_terminal_missing_tail_fragment.wma','07_invalid_waveformatex_tag.wma'):
            a=analyze_file(BASE/n,CFG_AUDIT,ROOT,FFMPEG,FFPROBE);d=a.format_facts['demux_decoder_evidence']
            self.assertEqual(a.repair_execution,[],n);self.assertEqual(a.lossless_export,[],n)
            self.assertFalse(a.canonical_presentation_window['determined'],n);self.assertFalse(d['pcm_sample_exact_claim'],n);self.assertFalse(d['publication_enabled'],n)
            self.assertTrue(any(d.get('code')=='WMA_ASF_DECODER_CONVERGENCE_AUTHORITY' for d in a.policy_decisions),n)
        self.assertEqual(analyze_file(BASE/'07_invalid_waveformatex_tag.wma',CFG_AUDIT,ROOT,FFMPEG,FFPROBE).validity_domains['DEMUX_DECODER_TIMELINE_VALIDITY'],'DEMUX_MAPPING_VALID_DECODER_EVIDENCE_UNAVAILABLE')

    def test_markdown_exposes_demux_decoder_evidence_and_no_sample_exact_claim(self):
        a=analyze_file(BASE/'00_healthy_wmav2_demux_decoder.wma',CFG_AUDIT,ROOT,FFMPEG,FFPROBE).to_dict()
        run={'run_id':'v38','started_at':'2026-08-17T03:30:00-03:00','summary':{'discovered':1,'processed':1,'ok':1,'with_findings':0,'skipped':0,'failed':0,'repaired_outputs_created':0,'lossless_outputs_created':0,'outputs_reused':0,'candidates_rejected':0},'files':[a]}
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'r.md';write_md(p,run);txt=p.read_text(encoding='utf-8')
            self.assertIn('Evidencia de línea de tiempo de demux y decodificador ASF/WMA',txt);self.assertIn('asignación uno a uno por hash+tamaño `True`',txt)
            self.assertIn('paquetes iniciales antes de la primera salida con marca temporal `2`',txt);self.assertIn('no límites de recuperación ASF exactos por muestra',txt)
            self.assertIn('ASF_WMA_DEMUX_DECODER_TIMELINE_EVIDENCE_ONLY',txt);self.assertIn('ASF_WMA_DECODER_CONVERGENCE_EVIDENCE_ONLY',txt)

    def test_e2e_three_ok_five_findings_zero_derivatives(self):
        rows=[analyze_file(BASE/n,CFG_AUDIT,ROOT,FFMPEG,FFPROBE) for n in sorted(MAN['cases'])]
        self.assertEqual(sum(a.run_status=='SUCCESS' for a in rows),3);self.assertEqual(sum(a.run_status=='SUCCESS_WITH_FINDINGS' for a in rows),5);self.assertEqual(sum(a.run_status=='FAILED' for a in rows),0)
        self.assertEqual(sum(bool(a.repair_execution) for a in rows),0);self.assertEqual(sum(bool(a.lossless_export) for a in rows),0)

if __name__=='__main__':unittest.main()
