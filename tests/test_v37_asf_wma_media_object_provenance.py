from __future__ import annotations
import copy,hashlib,json,os,shutil,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
from app.config import load_config
from app.pipeline import analyze_file
from app.version import APP_VERSION,POLICY_VERSION
from formats.asf_wma import analyze
from formats.identify import identify
from reporting.markdown_report import write_md

BASE=ROOT/'samples/asf_wma_provenance_v37'
MAN=json.loads((ROOT/'samples/asf_wma_provenance_v37_manifest.json').read_text(encoding='utf-8'))
CFG=load_config(ROOT/'config.toml')
CFG_AUDIT=copy.deepcopy(CFG)
CFG_AUDIT['lossless_recovery']['enabled']=False
FFMPEG=os.environ.get('LOSSYDOCTOR_FFMPEG') or shutil.which('ffmpeg')
FFPROBE=os.environ.get('LOSSYDOCTOR_FFPROBE') or shutil.which('ffprobe')

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()

class AsfWmaMediaObjectProvenanceV37(unittest.TestCase):
    def test_manifest_hashes_issue_sets_and_binary_distinctness(self):
        self.assertEqual(MAN['app_version'],'0.37.0');self.assertEqual(MAN['policy'],'0.37-asf-wma-media-object-provenance-1')
        seen=set()
        for n,c in MAN['cases'].items():
            p=BASE/n;h=sha(p);self.assertEqual(h,c['sha256'],n);self.assertNotIn(h,seen,n);seen.add(h)
            self.assertEqual([i.code for i in analyze(p)['issues']],c['expected_issues'],n)
        self.assertEqual(len(seen),7)

    def test_healthy_fragmented_media_objects_reassemble_exactly(self):
        for n,tag,sr,ch,objects,frags_first in (
            ('00_healthy_fragmented_wmav2.wma','0x0161',44100,2,44,2),
            ('01_healthy_fragmented_wmav1.wma','0x0160',22050,1,33,3),
        ):
            q=analyze(BASE/n);m=q['facts']['media_objects'];w=q['facts']['streams'][0]['waveformatex']
            self.assertEqual(q['issues'],[],n);self.assertEqual((w['format_tag_hex'],w['sample_rate'],w['channels']),(tag,sr,ch),n)
            self.assertEqual(m['ordinary_payload_media_objects_observed'],objects,n);self.assertEqual(m['complete_media_objects'],objects,n);self.assertEqual(m['incomplete_media_objects'],0,n)
            self.assertEqual(m['fragmented_media_objects'],objects,n);self.assertEqual(m['multi_packet_media_objects'],objects,n);self.assertEqual(m['compressed_payloads_unmodeled'],0,n)
            first=m['media_objects'][0];self.assertTrue(first['complete'],n);self.assertTrue(first['spans_packets'],n);self.assertEqual(first['fragment_count'],frags_first,n)
            self.assertEqual(first['covered_unique_bytes'],first['declared_size'],n);self.assertTrue(first['replicated_data_consistent'],n)
            self.assertTrue(all(x.get('payload_sha256') for x in first['fragments']),n)

    def test_adversarial_completion_classes_are_explicit(self):
        for n,c in MAN['cases'].items():
            q=analyze(BASE/n);m=q['facts']['media_objects']
            self.assertEqual(m['complete_media_objects'],c['expected_complete_media_objects'],n);self.assertEqual(m['incomplete_media_objects'],c['expected_incomplete_media_objects'],n)
            self.assertEqual(m['fragmented_media_objects'],c['expected_fragmented_media_objects'],n);self.assertEqual(m['multi_packet_media_objects'],c['expected_multi_packet_media_objects'],n)
            bad=[{'media_object_number':x['media_object_number'],'completion':x['completion'],'gaps':x['gaps'],'overlaps':x['overlaps']} for x in m['media_objects'] if not x['complete']]
            self.assertEqual(bad,c['expected_bad_objects'],n)
        self.assertEqual(analyze(BASE/'02_internal_fragment_gap.wma')['facts']['media_objects']['media_objects'][0]['completion'],'INTERNAL_GAP')
        self.assertEqual(analyze(BASE/'06_terminal_missing_tail_fragment.wma')['facts']['media_objects']['media_objects'][-1]['completion'],'MISSING_TAIL')

    def test_identify_routes_and_preserves_media_object_facts(self):
        q=identify(BASE/'00_healthy_fragmented_wmav2.wma')
        self.assertEqual((q['container'],q['codec'],q['confidence']),('ASF','wma','HIGH'));self.assertIn('asf_wma',q)
        self.assertEqual(q['asf_wma']['facts']['media_objects']['complete_media_objects'],44)

    @unittest.skipUnless(FFMPEG and FFPROBE,'ffmpeg/ffprobe required')
    def test_pipeline_adds_media_object_validity_and_remains_audit_only(self):
        got={n:analyze_file(BASE/n,CFG_AUDIT,ROOT,FFMPEG,FFPROBE) for n in MAN['cases']}
        for n,a in got.items():
            self.assertEqual(a.repair_execution,[],n);self.assertEqual(a.lossless_export,[],n)
            self.assertEqual((a.format_facts.get('media_objects') or {}).get('policy'),'ASF_WMA_MEDIA_OBJECT_PROVENANCE_EVIDENCE_ONLY',n);self.assertTrue(any(d.get('code')=='WMA_ASF_DECODER_CONVERGENCE_AUTHORITY' for d in a.policy_decisions),n)
        self.assertEqual(got['00_healthy_fragmented_wmav2.wma'].validity_domains['MEDIA_OBJECT_VALIDITY'],'VALIDATED_ORDINARY_MEDIA_OBJECT_REASSEMBLY')
        for n in list(MAN['cases'])[2:]:self.assertEqual(got[n].validity_domains['MEDIA_OBJECT_VALIDITY'],'NONCONFORMANT_OR_INCOMPLETE_MEDIA_OBJECTS',n)
        self.assertFalse(got['00_healthy_fragmented_wmav2.wma'].canonical_presentation_window['determined'])
        self.assertEqual(got['00_healthy_fragmented_wmav2.wma'].canonical_presentation_window['presentation_model'],'ASF_FILE_PROPERTIES_DURATION_NOT_SAMPLE_EXACT')

    @unittest.skipUnless(FFMPEG and FFPROBE,'ffmpeg/ffprobe required')
    def test_markdown_exposes_media_object_provenance_without_pcm_claim(self):
        a=analyze_file(BASE/'02_internal_fragment_gap.wma',CFG_AUDIT,ROOT,FFMPEG,FFPROBE).to_dict()
        run={'run_id':'v37','started_at':'2026-08-17T03:00:00-03:00','summary':{'discovered':1,'processed':1,'ok':0,'with_findings':1,'skipped':0,'failed':0,'repaired_outputs_created':0,'lossless_outputs_created':0,'outputs_reused':0,'candidates_rejected':0},'files':[a]}
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'r.md';write_md(p,run);txt=p.read_text(encoding='utf-8')
            self.assertIn('Procedencia de objetos multimedia ASF/WMA y evidencia de recuperación',txt);self.assertIn('INTERNAL_GAP',txt)
            self.assertIn('publicación habilitada `False`',txt);self.assertIn('afirmación PCM exacta por muestra `False`',txt);self.assertIn('ASF_WMA_MEDIA_OBJECT_PROVENANCE_EVIDENCE_ONLY',txt);self.assertIn('ASF_WMA_DEMUX_DECODER_TIMELINE_EVIDENCE_ONLY',txt);self.assertIn('ASF_WMA_DECODER_CONVERGENCE_EVIDENCE_ONLY',txt)

    @unittest.skipUnless(FFMPEG and FFPROBE,'ffmpeg/ffprobe required')
    def test_e2e_two_ok_five_findings_zero_derivatives(self):
        rows=[analyze_file(BASE/n,CFG_AUDIT,ROOT,FFMPEG,FFPROBE) for n in sorted(MAN['cases'])]
        self.assertEqual(sum(a.run_status=='SUCCESS' for a in rows),2);self.assertEqual(sum(a.run_status=='SUCCESS_WITH_FINDINGS' for a in rows),5);self.assertEqual(sum(a.run_status=='FAILED' for a in rows),0)
        self.assertEqual(sum(bool(a.repair_execution) for a in rows),0);self.assertEqual(sum(bool(a.lossless_export) for a in rows),0)

if __name__=='__main__':unittest.main()
