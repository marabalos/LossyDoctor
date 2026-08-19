from __future__ import annotations
import json, os, shutil, tempfile, unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
from app.config import load_config
from app.pipeline import analyze_file
from app.repairs import plan as plan_repairs
from app.utils import sha256_file
from formats.mpeg import analyze as analyze_mpeg

FFMPEG=os.environ.get('LOSSYDOCTOR_FFMPEG') or shutil.which('ffmpeg')
FFPROBE=os.environ.get('LOSSYDOCTOR_FFPROBE') or shutil.which('ffprobe')
CFG=load_config(ROOT/'config.toml')

@unittest.skipUnless(FFMPEG and FFPROBE,'ffmpeg/ffprobe required')
class Id3SafeRepairV08Tests(unittest.TestCase):
    def setUp(self):
        self.td=tempfile.TemporaryDirectory();self.d=Path(self.td.name)
        for p in (ROOT/'samples/id3_repair_v08').glob('*.mp3'):shutil.copy2(p,self.d/p.name)
        self.manifest=json.loads((ROOT/'samples/id3_repair_v08_manifest.json').read_text(encoding='utf-8'))
    def tearDown(self):self.td.cleanup()

    def A(self,name):
        p=self.d/name;before=sha256_file(p);a=analyze_file(p,CFG,ROOT,FFMPEG,FFPROBE);self.assertEqual(before,sha256_file(p),'source modified');return a

    def test_fixture_hashes_match_manifest(self):
        for name,c in self.manifest['cases'].items():
            self.assertEqual(sha256_file(self.d/name),c['sha256'])

    def test_parser_proves_repairable_id3v24_boundary(self):
        m=analyze_mpeg(self.d/'01_bad_id3_repairable.mp3')
        q=plan_repairs(self.d/'01_bad_id3_repairable.mp3',m)[0];a=q['actions'][0]
        self.assertEqual(q['status'],'ELIGIBLE');self.assertEqual(a['inferred_payload_size'],34);self.assertEqual(a['inferred_total_size'],44);self.assertEqual(a['replacement_hex'],'00000022');self.assertEqual(a['frame_count'],1);self.assertEqual(a['padding_bytes'],10)

    def test_ambiguous_id3_layout_is_never_auto_repaired(self):
        m=analyze_mpeg(self.d/'02_bad_id3_ambiguous.mp3')
        q=plan_repairs(self.d/'02_bad_id3_ambiguous.mp3',m)[0];self.assertEqual(q['status'],'BLOCKED')
        a=self.A('02_bad_id3_ambiguous.mp3')
        p=[x for x in a.repair_plan if x['spec']['id']=='REPAIR_ID3V24_SIZE_TO_VERIFIED_BOUNDARY'];self.assertEqual(len(p),1);self.assertEqual(p[0]['status'],'BLOCKED')
        self.assertEqual(a.final_status,['RECOVERED_LOSSLESS']);self.assertEqual(a.lossless_export[0]['outputs'][0]['manifest']['derivation_kind'],'RECOVERED_LOSSLESS')

    def test_repairable_id3_changes_only_size_field_and_matches_healthy_master(self):
        a=self.A('01_bad_id3_repairable.mp3')
        self.assertEqual(a.playability,'UNPLAYABLE');self.assertEqual(a.pcm_recovery_class,'COMPLETE_CLEAN');self.assertEqual(a.final_status,['REPAIRED_SAFE']);self.assertEqual(a.lossless_export,[])
        e=[x for x in a.repair_execution if x.get('repair_spec_id')=='REPAIR_ID3V24_SIZE_TO_VERIFIED_BOUNDARY'][0]
        self.assertEqual(e['status'],'CREATED');man=e['manifest'];out=Path(e['output_path'])
        self.assertEqual(sha256_file(out),sha256_file(self.d/'00_healthy_master.mp3'))
        self.assertEqual([(r['byte_start'],r['byte_end']) for r in man['changed_byte_ranges']],[(6,10)])
        self.assertEqual(man['changed_byte_ranges'][0]['field'],'ID3V2_SIZE')
        self.assertEqual(man['changed_byte_ranges'][0]['replacement_hex'],'00000022')
        self.assertTrue(man['verification']['passed']);self.assertEqual(man['verification']['new_damaged_issue_codes'],[])
        self.assertTrue(man['chain_steps'][0]['verification']['target_issues_resolved']);self.assertEqual(man['chain_steps'][0]['verification']['post_issue_codes'],[])
        self.assertTrue(any(d.get('code')=='BITSTREAM_REPAIR_PRECEDES_PCM_DERIVATION' for d in a.policy_decisions))

    def test_second_run_reuses_both_preservational_outputs(self):
        a1=self.A('01_bad_id3_repairable.mp3');a2=self.A('02_bad_id3_ambiguous.mp3')
        self.assertEqual([e for e in a1.repair_execution if e.get('status')=='CREATED'].__len__(),1)
        self.assertEqual(a2.lossless_export[0]['outputs'][0]['status'],'CREATED')
        before=sorted(p.name for p in self.d.iterdir())
        b1=self.A('01_bad_id3_repairable.mp3');b2=self.A('02_bad_id3_ambiguous.mp3')
        self.assertTrue(any(e.get('repair_spec_id')=='REPAIR_ID3V24_SIZE_TO_VERIFIED_BOUNDARY' and e.get('status')=='REUSED' for e in b1.repair_execution))
        self.assertEqual(b2.lossless_export[0]['outputs'][0]['status'],'REUSED')
        self.assertEqual(sorted(p.name for p in self.d.iterdir()),before)

if __name__=='__main__':unittest.main()
