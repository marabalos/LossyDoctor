from __future__ import annotations
import copy,hashlib,json,os,shutil,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
from app.config import load_config
from app.pipeline import analyze_file
from formats.identify import identify
from formats.aac_adts import analyze
from reporting.markdown_report import write_md

BASE=ROOT/'samples/aac_adts_v43'
MAN=json.loads((ROOT/'samples/aac_adts_v43_manifest.json').read_text(encoding='utf-8'))
CFG=load_config(ROOT/'config.toml'); CFG_AUDIT=copy.deepcopy(CFG)
CFG_AUDIT['repair']['enabled']=False; CFG_AUDIT['lossless_recovery']['enabled']=False
FFMPEG=os.environ.get('LOSSYDOCTOR_FFMPEG') or shutil.which('ffmpeg')
FFPROBE=os.environ.get('LOSSYDOCTOR_FFPROBE') or shutil.which('ffprobe')

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()

class AacAdtsDeepAuditV43(unittest.TestCase):
    def test_manifest_hashes_issue_sets_and_binary_distinctness(self):
        self.assertEqual(MAN['app_version'],'0.43.0');self.assertEqual(MAN['policy'],'0.43-aac-adts-deep-audit-1')
        seen=set()
        for n,c in MAN['cases'].items():
            p=BASE/n;h=sha(p);self.assertEqual(h,c['sha256'],n);self.assertNotIn(h,seen,n);seen.add(h)
            q=analyze(p);self.assertEqual([i.code for i in q['issues']],c['expected_issues'],n)
        self.assertEqual(len(seen),7)

    def test_healthy_controls_have_exact_contiguous_adts_geometry(self):
        for n,sr,cc,count in (
            ('00_healthy_aac_lc_44100_stereo.aac',44100,2,53),
            ('01_healthy_aac_lc_48000_mono.aac',48000,1,44),
        ):
            q=analyze(BASE/n);ad=q['facts']['adts'];fr=q['facts']['frames']
            self.assertEqual(q['issues'],[],n);self.assertEqual(ad['complete_frame_count'],count,n)
            self.assertEqual(ad['sample_rates_hz'],[sr],n);self.assertEqual(ad['channel_configurations'],[cc],n)
            self.assertEqual(ad['object_types'],[2],n);self.assertEqual(ad['profile_names'],['AAC LC'],n)
            self.assertEqual(ad['raw_data_blocks_values'],[1],n);self.assertEqual(ad['header_sample_count_total'],count*1024,n)
            self.assertTrue(ad['all_complete_frames_physically_contiguous'],n);self.assertEqual(fr[0]['byte_start'],0,n)
            self.assertEqual(fr[-1]['byte_end'],(BASE/n).stat().st_size,n)

    def test_negative_fixtures_isolate_structural_domains(self):
        for n,c in MAN['cases'].items():
            q=analyze(BASE/n);ad=q['facts']['adts']
            self.assertEqual(ad['complete_frame_count'],c['expected_complete_frames'],n)
            self.assertEqual(ad['sample_rates_hz'],c['expected_sample_rates_hz'],n)
            self.assertEqual(ad['channel_configurations'],c['expected_channel_configurations'],n)
            self.assertEqual(ad['sync_gap_count'],c['expected_sync_gaps'],n)
            self.assertEqual(ad['truncated_final_frame'],c['expected_truncated_final_frame'],n)
        self.assertEqual(analyze(BASE/'05_midstream_parameter_change.aac')['facts']['adts']['parameter_change_count'],1)
        self.assertEqual(analyze(BASE/'06_interframe_sync_gap.aac')['facts']['adts']['sync_gap_count'],1)

    def test_identify_routes_adts_separately_from_mp4_aac(self):
        q=identify(BASE/'00_healthy_aac_lc_44100_stereo.aac')
        self.assertEqual((q['container'],q['codec'],q['confidence']),('AAC_ADTS','aac','HIGH'));self.assertIn('aac_adts',q)
        self.assertEqual(q['aac_adts']['facts']['adts']['complete_frame_count'],53)

    @unittest.skipUnless(FFMPEG and FFPROBE,'ffmpeg/ffprobe required')
    def test_pipeline_audit_only_authority_and_demux_boundary_evidence(self):
        got={n:analyze_file(BASE/n,CFG_AUDIT,ROOT,FFMPEG,FFPROBE) for n in MAN['cases']}
        for n,c in MAN['cases'].items():
            a=got[n];self.assertEqual(a.playability,c['expected_playability'],n);self.assertEqual(a.repair_execution,[],n);self.assertEqual(a.lossless_export,[],n)
            self.assertEqual(a.expected_extension,'.aac',n)
            self.assertEqual(a.pcm_recovery_class,'COMPLETE_CLEAN' if n=='02_invalid_sampling_index.aac' else 'AAC_ADTS_AUDIT_ONLY',n)
            self.assertTrue(any(d.get('code')=='AAC_ADTS_AUDIT_AUTHORITY' and d.get('decision')=='AUDIT_ONLY_NO_REPAIR_OR_RECOVERY' for d in a.policy_decisions),n)
            if not c['expected_issues']:
                self.assertTrue(a.canonical_presentation_window['determined'],n);self.assertEqual(a.canonical_presentation_window['presentation_model'],'CONTIGUOUS_HOMOGENEOUS_AAC_LC_ADTS_FRAMES',n)
            else:
                self.assertFalse(a.canonical_presentation_window['determined'],n);self.assertEqual(a.canonical_presentation_window['presentation_model'],'ADTS_FRAME_SAMPLE_COUNT_NOT_PRESENTATION_WINDOW',n)
        for n in ('00_healthy_aac_lc_44100_stereo.aac','01_healthy_aac_lc_48000_mono.aac'):
            d=got[n].format_facts['adts_demux_evidence'];self.assertTrue(d['all_equal'],n)
            self.assertEqual(got[n].validity_domains['DEMUX_BOUNDARY_VALIDITY'],'VALIDATED_DIRECT_FRAME_TO_FFMPEG_PACKET_BOUNDARIES',n)

    @unittest.skipUnless(FFMPEG and FFPROBE,'ffmpeg/ffprobe required')
    def test_markdown_exposes_adts_audit_and_no_authority(self):
        a=analyze_file(BASE/'00_healthy_aac_lc_44100_stereo.aac',CFG_AUDIT,ROOT,FFMPEG,FFPROBE).to_dict()
        run={'run_id':'v43','started_at':'2026-08-17T15:40:00-03:00','summary':{'discovered':1,'processed':1,'ok':1,'with_findings':0,'skipped':0,'failed':0,'repaired_outputs_created':0,'lossless_outputs_created':0,'outputs_reused':0,'candidates_rejected':0},'files':[a]}
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'r.md';write_md(p,run);txt=p.read_text(encoding='utf-8')
            self.assertIn('Auditoría estructural AAC/ADTS',txt);self.assertIn('AAC LC',txt);self.assertIn('ADTS_FRAME_SAMPLE_COUNT_NOT_PRESENTATION_WINDOW',txt)
            self.assertIn('Autoridad de auditoría ADTS: reparación `NONE` · recuperación PCM `NONE`',txt)

    @unittest.skipUnless(FFMPEG and FFPROBE,'ffmpeg/ffprobe required')
    def test_e2e_shape_two_ok_five_findings_zero_derivatives(self):
        rows=[analyze_file(BASE/n,CFG_AUDIT,ROOT,FFMPEG,FFPROBE) for n in sorted(MAN['cases'])]
        self.assertEqual(sum(a.run_status=='SUCCESS' for a in rows),2);self.assertEqual(sum(a.run_status=='SUCCESS_WITH_FINDINGS' for a in rows),5)
        self.assertEqual(sum(a.run_status=='FAILED' for a in rows),0);self.assertEqual(sum(bool(a.repair_execution) for a in rows),0);self.assertEqual(sum(bool(a.lossless_export) for a in rows),0)

if __name__=='__main__':unittest.main()
