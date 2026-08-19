from __future__ import annotations
import json, os, shutil, tempfile, unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
from app.config import load_config
from app.pipeline import analyze_file
from app.utils import sha256_file
from formats.mpeg import analyze as analyze_mpeg

FFMPEG=os.environ.get('LOSSYDOCTOR_FFMPEG') or shutil.which('ffmpeg')
FFPROBE=os.environ.get('LOSSYDOCTOR_FFPROBE') or shutil.which('ffprobe')
CFG=load_config(ROOT/'config.toml')

@unittest.skipUnless(FFMPEG and FFPROBE,'ffmpeg/ffprobe required')
class V13VBRI(unittest.TestCase):
    def setUp(self):
        self.td=tempfile.TemporaryDirectory();self.d=Path(self.td.name)
        for p in (ROOT/'samples/vbri_v13').glob('*.mp3'):shutil.copy2(p,self.d/p.name)
        self.manifest=json.loads((ROOT/'samples/vbri_v13_manifest.json').read_text(encoding='utf-8'))
    def tearDown(self):self.td.cleanup()
    def A(self,name):
        p=self.d/name;before=sha256_file(p);a=analyze_file(p,CFG,ROOT,FFMPEG,FFPROBE);self.assertEqual(before,sha256_file(p),'source modified');return a
    def test_static_fixture_hashes(self):
        for name,c in self.manifest['cases'].items():self.assertEqual(sha256_file(self.d/name),c['sha256'])
    def test_healthy_vbri_v1_table_matches_exact_frame_map(self):
        m=analyze_mpeg(self.d/'00_healthy_vbri_v1.mp3');self.assertEqual([i.code for i in m['issues']],[])
        v=m['facts']['vbr_header']['vbri'];self.assertEqual(v['version'],1);self.assertTrue(v['layout_valid']);self.assertTrue(v['expected']['layout_representable'])
        self.assertEqual(v['frames'],155);self.assertEqual(v['bytes'],97801);self.assertEqual(v['toc'],v['expected']['toc']);self.assertEqual(v['toc_total_bytes'],97801)
    def test_positive_fixture_exposes_all_coupled_vbri_mismatches(self):
        m=analyze_mpeg(self.d/'01_vbri_coupled_corrupt.mp3');self.assertEqual(set(i.code for i in m['issues']),set(self.manifest['cases']['01_vbri_coupled_corrupt.mp3']['expected_issues']))
    def test_coherent_vbri_refresh_restores_master_and_pcm(self):
        a=self.A('01_vbri_coupled_corrupt.mp3');self.assertEqual(a.final_status,['REPAIRED_SAFE'])
        e=next(x for x in a.repair_execution if x.get('status')=='CREATED');self.assertEqual(e['repair_spec_id'],'REFRESH_VBRI_METADATA');man=e['manifest'];v=man['verification'];out=Path(e['output_path'])
        self.assertTrue(v['passed']);self.assertTrue(v['pcm_identical']);self.assertTrue(v['audio_payload_identical']);self.assertTrue(v['audio_frame_count_preserved']);self.assertTrue(v['vbri_table_coverage_validated']);self.assertEqual(v['vbri_issue_codes_remaining'],[])
        self.assertEqual(sha256_file(out),sha256_file(self.d/'00_healthy_vbri_v1.mp3'));self.assertEqual(v['source_canonical_pcm_sha256'],v['candidate_canonical_pcm_sha256'])
        self.assertEqual([r['field'] for r in man['changed_byte_ranges']],self.manifest['cases']['01_vbri_coupled_corrupt.mp3']['expected_changed_fields']);self.assertFalse(man['audio_recoding'])
        self.assertEqual([i.code for i in analyze_mpeg(out)['issues']],[])
    def test_layout_unprovable_remains_blocked(self):
        a=self.A('02_vbri_layout_unprovable.mp3');self.assertEqual(a.final_status,['ANOMALY_UNCHANGED']);self.assertTrue(any(p['spec']['id']=='REFRESH_VBRI_METADATA' and p['status']=='BLOCKED' for p in a.repair_plan));self.assertFalse(any(e.get('status') in ('CREATED','REUSED') for e in a.repair_execution))
    def test_structural_damage_remains_blocked(self):
        a=self.A('03_vbri_structural_damage.mp3');self.assertEqual(a.final_status,['ANOMALY_UNCHANGED']);self.assertTrue(any(p['spec']['id']=='REFRESH_VBRI_METADATA' and p['status']=='BLOCKED' for p in a.repair_plan));self.assertFalse(any(e.get('status') in ('CREATED','REUSED') for e in a.repair_execution))
    def test_second_run_reuses_without_duplicate(self):
        first=self.A('01_vbri_coupled_corrupt.mp3');e1=next(e for e in first.repair_execution if e.get('status')=='CREATED');before=sorted(p.name for p in self.d.iterdir());second=self.A('01_vbri_coupled_corrupt.mp3');e2=next(e for e in second.repair_execution if e.get('status')=='REUSED')
        self.assertEqual(e2['repair_spec_id'],'REFRESH_VBRI_METADATA');self.assertEqual(e1['output_path'],e2['output_path']);self.assertEqual(before,sorted(p.name for p in self.d.iterdir()))

if __name__=='__main__':unittest.main()
