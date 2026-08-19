from __future__ import annotations
import hashlib,json,os,shutil,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
from app.config import load_config
from app.pipeline import analyze_file
from app.version import APP_VERSION,POLICY_VERSION
from formats.identify import identify
from formats.ogg_vorbis import analyze
from reporting.markdown_report import write_md

BASE=ROOT/'samples/ogg_vorbis_v31'
MAN=json.loads((ROOT/'samples/ogg_vorbis_v31_manifest.json').read_text(encoding="utf-8"))
CFG=load_config(ROOT/'config.toml')
FFMPEG=os.environ.get('LOSSYDOCTOR_FFMPEG') or shutil.which('ffmpeg')
FFPROBE=os.environ.get('LOSSYDOCTOR_FFPROBE') or shutil.which('ffprobe')

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()

class OggVorbisDeepAuditV31(unittest.TestCase):
    def test_version_policy_fixture_hashes_and_issue_sets(self):
        self.assertGreaterEqual(tuple(map(int,APP_VERSION.split('.'))),(0,31,0));self.assertEqual(MAN['policy'],'0.31-ogg-vorbis-deep-audit-1')
        seen=set()
        for n,c in MAN['cases'].items():
            p=BASE/n;self.assertEqual(sha(p),c['sha256'],n);self.assertNotIn(c['sha256'],seen);seen.add(c['sha256'])
            q=analyze(p);self.assertEqual([i.code for i in q['issues']],c['expected_issues'],n);self.assertEqual(q['facts']['audio_mode_counts'],c['expected_mode_counts'],n);self.assertEqual(q['facts']['final_granule_position'],c['expected_final_granule'],n)
        self.assertEqual(len(seen),7)

    def test_healthy_headers_setup_and_zero_origin_timeline(self):
        q=analyze(BASE/'00_healthy_stereo_44100.ogg');f=q['facts'];vi=f['vorbis_identification'];vs=f['vorbis_setup']
        self.assertEqual(q['issues'],[]);self.assertTrue(f['ogg']['all_page_crc_valid']);self.assertTrue(f['ogg']['eos_present'])
        self.assertEqual((vi['version'],vi['channels'],vi['sample_rate']),(0,2,44100));self.assertEqual((vi['blocksize_0'],vi['blocksize_1']),(256,2048));self.assertTrue(vi['valid'])
        self.assertTrue(vs['valid']);self.assertEqual((vs['codebook_count'],vs['floor_count'],vs['residue_count'],vs['mapping_count'],vs['mode_count']),(42,2,2,2,2))
        self.assertEqual(f['audio_mode_counts'],{'0':1,'1':66});self.assertEqual(f['final_granule_position'],66150);self.assertEqual(f['playback_seconds'],1.5)
        first=f['timing_pages'][0];self.assertEqual(first['granule_position'],first['calculated_packet_contribution']);self.assertEqual(first['granule_position'],first['calculated_pcm_end_from_zero_origin'])

    def test_transient_control_exercises_both_blocksizes_repeatedly(self):
        q=analyze(BASE/'01_healthy_mode_transitions.ogg');f=q['facts'];self.assertEqual(q['issues'],[])
        self.assertGreater(f['audio_mode_counts']['0'],10);self.assertGreater(f['audio_mode_counts']['1'],10);self.assertEqual(f['audio_blocksize_counts'],{'256':64,'2048':96})
        self.assertEqual(f['final_granule_position'],105840);self.assertEqual(f['playback_seconds'],2.4);self.assertEqual(f['timing_pages'][-1]['eos_trim_samples'],80)

    def test_identify_routes_vorbis_to_deep_parser(self):
        q=identify(BASE/'00_healthy_stereo_44100.ogg');self.assertEqual((q['container'],q['codec'],q['confidence']),('OGG','vorbis','HIGH'));self.assertIn('ogg_vorbis',q)

    @unittest.skipUnless(FFMPEG and FFPROBE,'ffmpeg/ffprobe required')
    def test_pipeline_is_audit_only_and_summary_shape_is_two_ok_five_findings(self):
        rows=[analyze_file(BASE/n,CFG,ROOT,FFMPEG,FFPROBE) for n in sorted(MAN['cases'])]
        self.assertEqual(sum(a.run_status=='SUCCESS' for a in rows),2);self.assertEqual(sum(a.run_status=='SUCCESS_WITH_FINDINGS' for a in rows),5);self.assertEqual(sum(a.run_status=='FAILED' for a in rows),0)
        self.assertTrue(all(not a.lossless_export for a in rows));self.assertTrue(all(all(x.get('status')=='BLOCKED' for x in a.repair_execution) for a in rows))
        good=rows[0];self.assertEqual(good.validity_domains['CONTAINER_VALIDITY'],'VALID');self.assertEqual(good.validity_domains['CODEC_HEADER_VALIDITY'],'VALID');self.assertEqual(good.validity_domains['TIMELINE_VALIDITY'],'VALIDATED_GRANULES_AND_BLOCKSIZES');self.assertEqual(good.pcm_recovery_class,'NOT_REQUIRED')
        for a in rows[2:]:self.assertIn(a.pcm_recovery_class,('POLICY_BLOCKED_PLAYABLE','VORBIS_RECOVERY_BLOCKED'));self.assertTrue(a.policy_decisions[-1]['code']=='VORBIS_PRESERVATION_HIERARCHY_AUTHORITY')

    @unittest.skipUnless(FFMPEG and FFPROBE,'ffmpeg/ffprobe required')
    def test_negative_domains_separate_crc_sequence_header_granule_and_eos(self):
        got={n:analyze_file(BASE/n,CFG,ROOT,FFMPEG,FFPROBE) for n in MAN['cases']}
        self.assertEqual(got['02_crc_mismatch.ogg'].validity_domains['CONTAINER_VALIDITY'],'NONCONFORMANT_OR_DAMAGED');self.assertEqual(got['02_crc_mismatch.ogg'].validity_domains['TIMELINE_VALIDITY'],'VALIDATED_GRANULES_AND_BLOCKSIZES')
        self.assertEqual(got['03_sequence_gap.ogg'].validity_domains['CONTAINER_VALIDITY'],'NONCONFORMANT_OR_DAMAGED');self.assertEqual(got['03_sequence_gap.ogg'].validity_domains['CODEC_HEADER_VALIDITY'],'VALID')
        self.assertEqual(got['04_invalid_ident_blocksize.ogg'].validity_domains['CODEC_HEADER_VALIDITY'],'NONCONFORMANT_OR_DAMAGED');self.assertEqual(got['04_invalid_ident_blocksize.ogg'].playability,'UNPLAYABLE')
        self.assertEqual(got['05_granule_mismatch.ogg'].validity_domains['TIMELINE_VALIDITY'],'NONCONFORMANT_GRANULES_OR_BLOCKSIZES')
        self.assertEqual(got['06_missing_eos.ogg'].validity_domains['TIMELINE_VALIDITY'],'OPEN_ENDED_NO_EOS')

    @unittest.skipUnless(FFMPEG and FFPROBE,'ffmpeg/ffprobe required')
    def test_markdown_exposes_vorbis_deep_audit(self):
        a=analyze_file(BASE/'01_healthy_mode_transitions.ogg',CFG,ROOT,FFMPEG,FFPROBE).to_dict()
        run={'run_id':'v31','started_at':'2026-08-17T01:00:00-03:00','summary':{'discovered':1,'processed':1,'ok':1,'with_findings':0,'skipped':0,'failed':0,'repaired_outputs_created':0,'lossless_outputs_created':0,'outputs_reused':0,'candidates_rejected':0},'files':[a]}
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'r.md';write_md(p,run);txt=p.read_text(encoding="utf-8")
            self.assertIn('Auditoría estructural Ogg/Vorbis',txt);self.assertIn('tamaños de bloque `256` / `2048`',txt);self.assertIn("cantidades por modo `{'0': 64, '1': 96}`",txt);self.assertIn('Auditoría estructural Ogg/Vorbis',txt);self.assertIn('Vorbis',txt)

if __name__=='__main__':unittest.main()
