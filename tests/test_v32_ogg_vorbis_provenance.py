from __future__ import annotations
import hashlib,json,os,shutil,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
from app.config import load_config
from app.pipeline import analyze_file
from app.version import APP_VERSION,POLICY_VERSION
from formats.ogg_vorbis import analyze
from reporting.markdown_report import write_md

BASE=ROOT/'samples/ogg_vorbis_provenance_v32'
MAN=json.loads((ROOT/'samples/ogg_vorbis_provenance_v32_manifest.json').read_text(encoding="utf-8"))
CFG=load_config(ROOT/'config.toml')
FFMPEG=os.environ.get('LOSSYDOCTOR_FFMPEG') or shutil.which('ffmpeg')
FFPROBE=os.environ.get('LOSSYDOCTOR_FFPROBE') or shutil.which('ffprobe')
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()

class OggVorbisProvenanceV32(unittest.TestCase):
    def test_version_policy_hashes_issues_and_candidate_counts(self):
        self.assertGreaterEqual(tuple(map(int,APP_VERSION.split('.'))),(0,32,0));self.assertEqual(MAN['policy'],'0.32-ogg-vorbis-proven-packet-evidence-1')
        seen=set()
        for n,c in MAN['cases'].items():
            p=BASE/n;self.assertEqual(sha(p),c['sha256'],n);self.assertNotIn(c['sha256'],seen);seen.add(c['sha256'])
            q=analyze(p);v=q['facts']['vorbis_recovery_evidence']
            self.assertEqual([i.code for i in q['issues']],c['expected_issues'],n)
            self.assertEqual(v['candidate_region_count'],c['expected_candidate_region_count'],n)
            self.assertEqual(v['authenticated_audio_packet_count'],c['expected_authenticated_audio_packets'],n)
            self.assertEqual(v['cross_page_audio_packet_count'],c['expected_cross_page_audio_packets'],n)
            self.assertEqual(v['authenticated_cross_page_audio_packet_count'],c['expected_authenticated_cross_page_audio_packets'],n)
        self.assertEqual(len(seen),6)

    def test_healthy_cross_page_packet_is_fully_authenticated(self):
        f=analyze(BASE/'00_healthy_cross_page_eos.ogg')['facts'];v=f['vorbis_recovery_evidence'];m=f['audio_packet_map'][-1]
        self.assertEqual(v['candidate_region_count'],1);self.assertEqual((v['candidate_regions'][0]['pcm_start'],v['candidate_regions'][0]['pcm_end']),(0,88200))
        self.assertTrue(m['spans_pages']);self.assertTrue(m['crc_authenticated_complete_packet']);self.assertEqual(len(m['page_indices']),2)
        self.assertTrue(v['candidate_regions'][0]['authenticated_eos_included'])

    def test_missing_crc_and_sequence_damage_split_clean_chains(self):
        miss=analyze(BASE/'01_missing_middle_page.ogg')['facts']['vorbis_recovery_evidence']['candidate_regions']
        crc=analyze(BASE/'02_crc_middle_page.ogg')['facts']['vorbis_recovery_evidence']['candidate_regions']
        seq=analyze(BASE/'03_sequence_gap_no_loss.ogg')['facts']['vorbis_recovery_evidence']['candidate_regions']
        self.assertEqual([(r['pcm_start'],r['pcm_end']) for r in miss],[(0,39488),(60992,88200)])
        self.assertEqual([(r['pcm_start'],r['pcm_end']) for r in crc],[(0,39488),(60992,88200)])
        self.assertEqual([(r['pcm_start'],r['pcm_end']) for r in seq],[(0,39488),(40512,88200)])
        for rr in (miss,crc,seq):
            self.assertNotEqual(rr[1]['priming_packet_index'],rr[1]['first_published_overlap_packet_index'])
            self.assertEqual(rr[1]['publication_authority'],'NONE_PROVEN_PACKET_REGION_EVIDENCE_ONLY')

    def test_truncated_and_cross_page_crc_do_not_claim_final_packet(self):
        trunc=analyze(BASE/'04_truncated_continued_tail.ogg')['facts']['vorbis_recovery_evidence']
        bad=analyze(BASE/'05_crc_first_half_continued_packet.ogg')['facts']['vorbis_recovery_evidence']
        self.assertEqual([(r['pcm_start'],r['pcm_end']) for r in trunc['candidate_regions']],[(0,87616)])
        self.assertEqual([(r['pcm_start'],r['pcm_end']) for r in bad['candidate_regions']],[(0,87616)])
        self.assertEqual(trunc['authenticated_cross_page_audio_packet_count'],0)
        self.assertEqual(bad['cross_page_audio_packet_count'],1);self.assertEqual(bad['authenticated_cross_page_audio_packet_count'],0)
        self.assertFalse(bad['candidate_regions'][0]['authenticated_eos_included'])

    def test_overlap_dependency_map_uses_previous_and_current_packets(self):
        f=analyze(BASE/'00_healthy_cross_page_eos.ogg')['facts'];m=f['audio_packet_map']
        self.assertFalse(m[0]['overlap_dependency_authenticated'])
        self.assertEqual(m[0]['overlap_output_samples'],0)
        self.assertTrue(m[1]['overlap_dependency_authenticated'])
        self.assertGreater(m[1]['overlap_output_samples'],0)
        self.assertEqual(f['vorbis_recovery_evidence']['overlap_dependency_rule'],'PCM_BETWEEN_PACKET_CENTERS_DEPENDS_ON_PREVIOUS_AND_CURRENT_BLOCK')

    @unittest.skipUnless(FFMPEG and FFPROBE,'ffmpeg/ffprobe required')
    def test_pipeline_remains_evidence_only_one_ok_five_findings(self):
        rows=[analyze_file(BASE/n,CFG,ROOT,FFMPEG,FFPROBE) for n in sorted(MAN['cases'])]
        self.assertEqual(sum(a.run_status=='SUCCESS' for a in rows),1);self.assertEqual(sum(a.run_status=='SUCCESS_WITH_FINDINGS' for a in rows),5);self.assertEqual(sum(a.run_status=='FAILED' for a in rows),0)
        self.assertTrue(all(not a.lossless_export for a in rows));self.assertTrue(all(all(x.get('status')=='BLOCKED' for x in a.repair_execution) for a in rows))
        for a in rows[1:]:
            self.assertEqual(a.pcm_recovery_class,'POLICY_BLOCKED_PLAYABLE');self.assertTrue(any(x['code']=='VORBIS_PRESERVATION_HIERARCHY_AUTHORITY' for x in a.policy_decisions))

    @unittest.skipUnless(FFMPEG and FFPROBE,'ffmpeg/ffprobe required')
    def test_markdown_exposes_candidate_regions_and_no_authority(self):
        a=analyze_file(BASE/'01_missing_middle_page.ogg',CFG,ROOT,FFMPEG,FFPROBE).to_dict()
        run={'run_id':'v32','started_at':'2026-08-17T01:30:00-03:00','summary':{'discovered':1,'processed':1,'ok':0,'with_findings':1,'skipped':0,'failed':0,'repaired_outputs_created':0,'lossless_outputs_created':0,'outputs_reused':0,'candidates_rejected':0},'files':[a]}
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'r.md';write_md(p,run);txt=p.read_text(encoding='utf-8')
            self.assertIn('Procedencia de paquetes Vorbis y evidencia de recuperación',txt)
            self.assertIn('Regiones PCM candidatas demostradas: `2`',txt)
            self.assertIn('PCM `0–39488`',txt)
            self.assertIn('PCM `60992–88200`',txt)
            self.assertIn('publicación PCM `False`',txt)

if __name__=='__main__':unittest.main()
