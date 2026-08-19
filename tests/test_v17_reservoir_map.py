from __future__ import annotations
import hashlib,json,tempfile,unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
from formats.mpeg import analyze
from reporting.markdown_report import write_md

MAN=json.loads((ROOT/'samples/reservoir_v17_manifest.json').read_text(encoding="utf-8"))
BASE=ROOT/'samples/reservoir_v17'

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def set_bits(buf:bytearray,bitpos:int,nbits:int,value:int):
    for i in range(nbits):
        q=bitpos+i;bit=(value>>(nbits-1-i))&1;mask=1<<(7-(q&7))
        if bit:buf[q>>3]|=mask
        else:buf[q>>3]&=~mask

class ReservoirMapV17(unittest.TestCase):
    def test_fixture_hashes(self):
        for name,c in MAN['cases'].items():self.assertEqual(sha(BASE/name),c['sha256'])

    def test_healthy_reservoir_maps_exact_dependencies(self):
        a=analyze(BASE/'00_reservoir_healthy.mp3');r=a['facts']['bit_reservoir'];c=MAN['cases']['00_reservoir_healthy.mp3']
        self.assertEqual([i.code for i in a['issues']],[]);self.assertEqual(r['mapping'],'MAIN_DATA_BEGIN_PLUS_PART2_3_LENGTH')
        self.assertEqual(r['mapped_frame_count'],c['frames']);self.assertEqual(r['fully_provable_frame_count'],c['fully_provable_frames'])
        self.assertEqual(r['frames_with_backreferences'],c['frames_with_backreferences']);self.assertEqual(r['max_main_data_begin_bytes'],c['max_main_data_begin_bytes']);self.assertEqual(r['max_dependency_backspan_frames'],2)
        refs=[f for f in a['frames'] if not f['is_vbr_header'] and (f['reservoir_dependency']['main_data_begin_bytes'] or 0)>0]
        self.assertTrue(refs);self.assertTrue(all(f['layer3_sideinfo_usage']['main_data_bits_required']>=0 for f in refs));self.assertTrue(all(f['reservoir_dependency']['provable'] for f in refs))
        self.assertTrue(any(len(f.get('reservoir_dependents') or [])>=2 for f in a['frames']))

    def test_reservoir_disabled_control_has_no_backreferences(self):
        a=analyze(BASE/'01_reservoir_disabled.mp3');r=a['facts']['bit_reservoir']
        self.assertEqual([i.code for i in a['issues']],[]);self.assertEqual(r['frames_with_backreferences'],0);self.assertEqual(r['max_main_data_begin_bytes'],0);self.assertEqual(r['fully_provable_frame_count'],53)
        self.assertTrue(all((f.get('main_data_begin') or 0)==0 for f in a['frames']))

    def test_gap_dependency_envelope_is_exact_and_recovery_safe(self):
        a=analyze(BASE/'02_reservoir_gap.mp3');r=a['facts']['bit_reservoir']
        self.assertEqual([i.code for i in a['issues']],['MPEG_SYNC_LOSS']);self.assertEqual(r['unresolved_pre_segment_frame_indices'],[14,15]);self.assertEqual(r['fully_provable_frame_count'],50)
        bad=[f for f in a['frames'] if (f.get('reservoir_dependency') or {}).get('unavailable_pre_segment_bytes')]
        self.assertEqual([f['index'] for f in bad],[14,15]);self.assertTrue(all(not f['clean'] for f in bad));self.assertTrue(all(f['taint_reason']=='bit_reservoir_dependency_before_resync_segment' for f in bad))
        post=[f for f in a['frames'] if f['index']>15];self.assertTrue(post and all(f['clean'] for f in post))

    def test_part23_overrun_is_damage_not_repair_permission(self):
        src=BASE/'00_reservoir_healthy.mp3';b=bytearray(src.read_bytes());a0=analyze(src);f=a0['frames'][0]
        # MPEG-1 stereo: side-info begins after 4-byte header; part2_3_length of
        # granule0/channel0 begins after 9 mdb + 3 private + 8 scfsi = 20 bits.
        side=(f['byte_start']+4)*8;set_bits(b,side+20,12,4095)
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'bad.mp3';p.write_bytes(b);a=analyze(p)
        hit=[i for i in a['issues'] if i.code=='BIT_RESERVOIR_MAIN_DATA_OVERRUN'];self.assertEqual(len(hit),1);self.assertEqual(hit[0].repairability,'RECOVERY_ONLY')

    def test_markdown_surfaces_dependency_summary(self):
        a=analyze(BASE/'02_reservoir_gap.mp3')
        d={'display_name':'02_reservoir_gap.mp3','run_status':'SUCCESS_WITH_FINDINGS','detected_container':'MPEG_AUDIO','detected_codec':'mp3','playability':'PLAYABLE','pcm_recovery_class':'PARTIAL_CLEAN','decode_results':{},'validity_domains':{},'format_facts':a['facts'],'issues':[i.to_dict() for i in a['issues']],'repair_plan':[],'repair_execution':[],'lossless_export':[],'policy_decisions':[],'final_status':['ANOMALY_UNCHANGED']}
        run={'run_id':'x','started_at':'2026-08-16T19:00:00-03:00','summary':{'discovered':1,'processed':1,'ok':0,'with_findings':1,'skipped':0,'failed':0,'repaired_outputs_created':0,'lossless_outputs_created':0,'outputs_reused':0,'candidates_rejected':0},'files':[d]}
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'r.md';write_md(p,run);txt=p.read_text(encoding="utf-8")
        self.assertIn('Mapa de dependencias del bit reservoir Layer III',txt);self.assertIn('Referencias sin resolver antes del segmento de resincronización: `2`',txt);self.assertIn('frames `14, 15`',txt)

if __name__=='__main__':unittest.main()
