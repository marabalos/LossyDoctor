from __future__ import annotations
import hashlib,json,os,shutil,tempfile,unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
from formats.mpeg import analyze
from app.config import load_config
from app.pipeline import analyze_file

FFMPEG=os.environ.get('LOSSYDOCTOR_FFMPEG') or shutil.which('ffmpeg')
FFPROBE=os.environ.get('LOSSYDOCTOR_FFPROBE') or shutil.which('ffprobe')
CFG=load_config(ROOT/'config.toml')
MAN=json.loads((ROOT/'samples/crc_l2_v15_manifest.json').read_text(encoding='utf-8'))
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()

class LayerIICRCPrimitiveTests(unittest.TestCase):
    def test_fixture_hashes(self):
        for name,c in MAN['cases'].items():self.assertEqual(sha(ROOT/'samples/crc_l2_v15'/name),c['sha256'])
    def test_all_five_layer2_allocation_table_geometries_match_twolame_crc(self):
        seen=set()
        for name,c in MAN['cases'].items():
            if c['allocation_table_index'] is None or c['crc_mismatches']:continue
            m=analyze(ROOT/'samples/crc_l2_v15'/name);cr=m['facts']['crc_protection'];seen.add(c['allocation_table_index'])
            self.assertEqual(m['facts']['layer'],2);self.assertEqual(cr['supported_scope'],'MPEG_LAYER_II')
            self.assertEqual((cr['checked_frame_count'],cr['mismatch_count']),(c['protected_frames'],0))
            self.assertTrue(all(f['crc']['valid'] for f in m['frames'] if f.get('crc') and f['crc'].get('checked')))
            first=next(f for f in m['frames'] if f.get('crc') and f['crc'].get('checked'))
            self.assertEqual(first['crc']['allocation_table_index'],c['allocation_table_index'])
        self.assertEqual(seen,{0,1,2,3,4})
    def test_joint_stereo_bound_is_applied_before_crc_scfsi_walk(self):
        m=analyze(ROOT/'samples/crc_l2_v15/03_mpeg1_l2_table0_joint_crc_healthy.mp2');f=m['frames'][0]
        self.assertEqual(f['header']['channel_mode'],1);self.assertEqual(f['header']['mode_extension'],0)
        self.assertEqual((f['crc']['allocation_table_index'],f['crc']['sblimit'],f['crc']['joint_stereo_bound']),(0,27,4));self.assertTrue(f['crc']['valid'])
    def test_stored_crc_word_corruption_is_detected_but_not_localized(self):
        m=analyze(ROOT/'samples/crc_l2_v15/01_l2_crc_word_corrupt.mp2');bad=[f for f in m['frames'] if f.get('crc') and f['crc'].get('checked') and not f['crc']['valid']]
        self.assertEqual(len(bad),1);self.assertEqual(bad[0]['index'],7);self.assertEqual([i.code for i in m['issues']],['MPEG_CRC_MISMATCH']);self.assertEqual(m['issues'][0].repairability,'NONE')
    def test_scfsi_corruption_is_inside_layer2_crc_coverage(self):
        good=analyze(ROOT/'samples/crc_l2_v15/00_mpeg1_l2_table1_crc_healthy.mp2');bad=analyze(ROOT/'samples/crc_l2_v15/02_l2_scfsi_corrupt.mp2')
        g=good['frames'][0];b=bad['frames'][0]
        self.assertEqual(g['crc']['stored'],b['crc']['stored']);self.assertNotEqual(g['crc']['computed'],b['crc']['computed']);self.assertFalse(b['crc']['valid'])
        self.assertEqual(b['crc']['scope'],'HEADER_LOW16_PLUS_LAYER2_BITALLOC_SCFI')
    def test_mpeg2_layer2_crc_is_supported(self):
        m=analyze(ROOT/'samples/crc_l2_v15/06_mpeg2_l2_table4_crc_healthy.mp2');c=m['facts']['crc_protection']
        self.assertEqual((m['facts']['mpeg_version'],m['facts']['layer']),(2,2));self.assertEqual((c['checked_frame_count'],c['mismatch_count']),(20,0))
    def test_unprotected_layer2_control_has_no_crc_claim(self):
        m=analyze(ROOT/'samples/crc_l2_v15/07_mpeg1_l2_unprotected_control.mp2');c=m['facts']['crc_protection']
        self.assertEqual((c['protected_frame_count'],c['checked_frame_count'],c['mismatch_count']),(0,0,0));self.assertFalse(any(i.code=='MPEG_CRC_MISMATCH' for i in m['issues']))

@unittest.skipUnless(FFMPEG and FFPROBE,'ffmpeg/ffprobe required')
class LayerIICRCPipelineTests(unittest.TestCase):
    def test_pipeline_classification_no_crc_repairs_or_exports(self):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td)
            for p in (ROOT/'samples/crc_l2_v15').glob('*.mp2'):shutil.copy2(p,d/p.name)
            for name,c in MAN['cases'].items():
                p=d/name;before=sha(p);a=analyze_file(p,CFG,ROOT,FFMPEG,FFPROBE);self.assertEqual(before,sha(p),'source changed')
                self.assertEqual(a.final_status,[c['expected']]);crc=a.format_facts['crc_protection'];self.assertEqual((crc['protected_frame_count'],crc['mismatch_count']),(c['protected_frames'],c['crc_mismatches']))
                if c['crc_mismatches']:
                    self.assertTrue(any(i.code=='MPEG_CRC_MISMATCH' for i in a.issues));self.assertFalse(a.repair_plan);self.assertFalse(a.repair_execution);self.assertFalse(a.lossless_export)
    def test_markdown_reports_layer2_crc_scope(self):
        from reporting.markdown_report import write_md
        with tempfile.TemporaryDirectory() as td:
            d=Path(td);p=d/'x.mp2';shutil.copy2(ROOT/'samples/crc_l2_v15/02_l2_scfsi_corrupt.mp2',p);a=analyze_file(p,CFG,ROOT,FFMPEG,FFPROBE)
            run={'run_id':'test','started_at':'2026-08-16T19:00:00-03:00','summary':{'discovered':1,'processed':1,'ok':0,'with_findings':1,'skipped':0,'failed':0,'repaired_outputs_created':0,'lossless_outputs_created':0,'outputs_reused':0,'candidates_rejected':0},'files':[a.to_dict()]}
            out=d/'r.md';write_md(out,run);txt=out.read_text(encoding='utf-8')
            self.assertIn('Alcance: `MPEG_LAYER_II`',txt);self.assertIn('HEADER_LOW16_PLUS_LAYER2_BITALLOC_SCFI',txt);self.assertIn('CRC válidos/discrepantes: `38` / `1`',txt)

if __name__=='__main__':unittest.main()
