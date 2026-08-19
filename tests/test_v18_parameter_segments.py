from __future__ import annotations
import hashlib,json,tempfile,unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
from formats.mpeg import analyze
from reporting.markdown_report import write_md
from app.lossless_export import assess

MAN=json.loads((ROOT/'samples/parameter_segments_v18_manifest.json').read_text(encoding="utf-8"))
BASE=ROOT/'samples/parameter_segments_v18'

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def codes(a):return [i.code for i in a['issues']]

class ParameterSegmentsV18(unittest.TestCase):
    def test_fixture_hashes(self):
        for name,c in MAN['cases'].items():self.assertEqual(sha(BASE/name),c['sha256'])

    def test_bitrate_variation_is_frame_level_not_parameter_discontinuity(self):
        a=analyze(BASE/'01_bitrate_variation_control.mp3');pm=a['facts']['parameter_segments']
        self.assertEqual(codes(a),[]);self.assertEqual(pm['segment_count'],1);self.assertEqual(pm['hard_profile_transition_count'],0)
        self.assertEqual(pm['segments'][0]['bitrate_kbps_values'],[96,192]);self.assertEqual(pm['segments'][0]['bitrate_mode'],'VBR_OR_MIXED')

    def test_channel_mode_change_is_soft_encoding_variation(self):
        a=analyze(BASE/'02_channel_mode_variation.mp3');pm=a['facts']['parameter_segments']
        self.assertEqual(codes(a),[]);self.assertEqual(pm['segment_count'],2);self.assertEqual(pm['soft_transition_count'],1)
        t=pm['transitions'][0];self.assertEqual(t['changed_fields'],['channel_mode']);self.assertEqual(t['interpretation'],'ENCODING_MODE_VARIATION')
        self.assertTrue(t['contiguous'])

    def test_clean_hard_change_is_coherent_concatenation_not_sync_loss(self):
        a=analyze(BASE/'03_coherent_hard_concat.mp3');pm=a['facts']['parameter_segments']
        self.assertEqual(codes(a),['MPEG_COHERENT_PARAMETER_CONCATENATION']);self.assertEqual(a['gaps'],[])
        t=pm['transitions'][0];self.assertEqual(t['interpretation'],'COHERENT_CONCATENATION');self.assertTrue(t['contiguous'])
        self.assertEqual(t['hard_changed_fields'],['mpeg_version','sample_rate','samples_per_frame'])
        self.assertEqual(pm['segments'][0]['profile']['sample_rate'],44100);self.assertEqual(pm['segments'][1]['profile']['sample_rate'],22050)

    def test_parameter_change_after_real_gap_remains_damage(self):
        a=analyze(BASE/'04_parameter_change_after_gap.mp3');pm=a['facts']['parameter_segments']
        self.assertEqual(codes(a),['MPEG_SYNC_LOSS','MPEG_PARAMETER_CHANGE_AFTER_RESYNC']);self.assertEqual(len(a['gaps']),1)
        t=pm['transitions'][0];self.assertEqual(t['interpretation'],'PARAMETER_CHANGE_AFTER_RESYNC');self.assertFalse(t['contiguous']);self.assertEqual(t['gap_index'],0)
        self.assertTrue(all(i.repairability in ('RECOVERY_ONLY','NONE') for i in a['issues']))

    def test_hard_parameter_transitions_block_homogeneous_pcm_recovery_materializer(self):
        a=analyze(BASE/'03_coherent_hard_concat.mp3');ra=assess(a,'UNPLAYABLE')
        self.assertEqual(ra['pcm_class'],'HETEROGENEOUS_STREAM');self.assertFalse(ra['eligible_partial']);self.assertFalse(ra['eligible_complete'])

    def test_layer_change_is_explicit_hard_transition(self):
        a=analyze(BASE/'05_layer_change_concat.mpa');pm=a['facts']['parameter_segments']
        self.assertEqual(codes(a),['MPEG_COHERENT_PARAMETER_CONCATENATION']);t=pm['transitions'][0]
        self.assertIn('layer',t['hard_changed_fields']);self.assertEqual([s['profile']['layer'] for s in pm['segments']],[3,2])
        self.assertEqual(a['facts']['crc_protection']['supported_scope'],'MIXED_MPEG_LAYERS')

    def test_channel_count_change_is_hard_but_contiguous(self):
        a=analyze(BASE/'06_channel_count_concat.mp3');pm=a['facts']['parameter_segments'];t=pm['transitions'][0]
        self.assertEqual(codes(a),['MPEG_COHERENT_PARAMETER_CONCATENATION']);self.assertIn('channels',t['hard_changed_fields']);self.assertTrue(t['contiguous'])
        self.assertEqual([s['profile']['channels'] for s in pm['segments']],[2,1])

    def test_markdown_surfaces_segments_and_transition_interpretation(self):
        a=analyze(BASE/'04_parameter_change_after_gap.mp3')
        d={'display_name':'04_parameter_change_after_gap.mp3','run_status':'SUCCESS_WITH_FINDINGS','detected_container':'MPEG_AUDIO','detected_codec':'mp3','playability':'PLAYABLE','pcm_recovery_class':'PARTIAL_CLEAN','decode_results':{},'validity_domains':{},'format_facts':a['facts'],'issues':[i.to_dict() for i in a['issues']],'repair_plan':[],'repair_execution':[],'lossless_export':[],'policy_decisions':[],'final_status':['ANOMALY_UNCHANGED']}
        run={'run_id':'x','started_at':'2026-08-16T20:00:00-03:00','summary':{'discovered':1,'processed':1,'ok':0,'with_findings':1,'skipped':0,'failed':0,'repaired_outputs_created':0,'lossless_outputs_created':0,'outputs_reused':0,'candidates_rejected':0},'files':[d]}
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'r.md';write_md(p,run);txt=p.read_text(encoding="utf-8")
        self.assertIn('Segmentación multiparámetro MPEG',txt);self.assertIn('PARAMETER_CHANGE_AFTER_RESYNC',txt);self.assertIn('sample_rate',txt)

if __name__=='__main__':unittest.main()
