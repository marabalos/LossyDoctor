from __future__ import annotations
import hashlib,json,os,shutil,tempfile,unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
from formats.mpeg import analyze,mpeg_audio_crc16
from app.config import load_config
from app.pipeline import analyze_file

FFMPEG=os.environ.get('LOSSYDOCTOR_FFMPEG') or shutil.which('ffmpeg')
FFPROBE=os.environ.get('LOSSYDOCTOR_FFPROBE') or shutil.which('ffprobe')
CFG=load_config(ROOT/'config.toml')
MAN=json.loads((ROOT/'samples/crc_v14_manifest.json').read_text(encoding='utf-8'))

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()

class MPEGCRCPrimitiveTests(unittest.TestCase):
    def test_lame_mpeg1_stereo_crc_matches_every_protected_frame(self):
        p=ROOT/'samples/crc_v14/00_mpeg1_l3_crc_healthy.mp3';m=analyze(p)
        self.assertEqual(m['facts']['mpeg_version'],1);self.assertEqual(m['facts']['layer'],3)
        c=m['facts']['crc_protection'];self.assertEqual((c['checked_frame_count'],c['mismatch_count']),(40,0))
        self.assertTrue(all(f['crc']['valid'] for f in m['frames'] if f.get('crc') and f['crc'].get('checked')))
    def test_lame_mpeg2_crc_matches_every_protected_frame(self):
        m=analyze(ROOT/'samples/crc_v14/03_mpeg2_l3_crc_healthy.mp3')
        self.assertEqual(m['facts']['mpeg_version'],2);self.assertEqual((m['facts']['crc_protection']['checked_frame_count'],m['facts']['crc_protection']['mismatch_count']),(41,0))
    def test_crc_word_corruption_is_detected_but_not_repaired(self):
        m=analyze(ROOT/'samples/crc_v14/01_crc_word_corrupt.mp3');bad=[f for f in m['frames'] if f.get('crc') and f['crc'].get('checked') and not f['crc']['valid']]
        self.assertEqual(len(bad),1);self.assertEqual(bad[0]['index'],7);self.assertNotEqual(bad[0]['crc']['stored'],bad[0]['crc']['computed'])
        self.assertEqual([i.code for i in m['issues']],['MPEG_CRC_MISMATCH']);self.assertEqual(m['issues'][0].repairability,'NONE')
    def test_sideinfo_bit_corruption_is_inside_crc_coverage(self):
        good=analyze(ROOT/'samples/crc_v14/00_mpeg1_l3_crc_healthy.mp3');bad=analyze(ROOT/'samples/crc_v14/02_sideinfo_private_bit_corrupt.mp3')
        self.assertEqual(good['frames'][8]['crc']['stored'],bad['frames'][8]['crc']['stored'])
        self.assertNotEqual(good['frames'][8]['crc']['computed'],bad['frames'][8]['crc']['computed'])
        self.assertFalse(bad['frames'][8]['crc']['valid']);self.assertEqual(bad['facts']['crc_protection']['mismatch_count'],1)
    def test_unprotected_control_has_no_crc_claims(self):
        m=analyze(ROOT/'samples/crc_v14/04_unprotected_control.mp3');c=m['facts']['crc_protection']
        self.assertEqual((c['protected_frame_count'],c['checked_frame_count'],c['mismatch_count']),(0,0,0));self.assertFalse(any(i.code=='MPEG_CRC_MISMATCH' for i in m['issues']))
    def test_fixture_hashes(self):
        for name,c in MAN['cases'].items():self.assertEqual(sha(ROOT/'samples/crc_v14'/name),c['sha256'])

@unittest.skipUnless(FFMPEG and FFPROBE,'ffmpeg/ffprobe required')
class MPEGCRCPipelineTests(unittest.TestCase):
    def test_pipeline_classification_and_no_write_for_crc_mismatch(self):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td)
            for p in (ROOT/'samples/crc_v14').glob('*.mp3'):shutil.copy2(p,d/p.name)
            for name,c in MAN['cases'].items():
                p=d/name;before=sha(p);a=analyze_file(p,CFG,ROOT,FFMPEG,FFPROBE);self.assertEqual(before,sha(p),'source changed')
                self.assertEqual(a.final_status,[c['expected']]);self.assertEqual((a.format_facts['crc_protection']['protected_frame_count'],a.format_facts['crc_protection']['mismatch_count']),(c['protected_frames'],c['crc_mismatches']))
                if c['crc_mismatches']:
                    self.assertTrue(any(i.code=='MPEG_CRC_MISMATCH' for i in a.issues));self.assertFalse(a.repair_plan);self.assertFalse(a.repair_execution);self.assertFalse(a.lossless_export)
    def test_report_contains_crc_evidence(self):
        from reporting.markdown_report import write_md
        with tempfile.TemporaryDirectory() as td:
            d=Path(td);p=d/'x.mp3';shutil.copy2(ROOT/'samples/crc_v14/01_crc_word_corrupt.mp3',p);a=analyze_file(p,CFG,ROOT,FFMPEG,FFPROBE)
            run={'run_id':'test','started_at':'2026-08-16T18:00:00-03:00','summary':{'discovered':1,'processed':1,'ok':0,'with_findings':1,'skipped':0,'failed':0,'repaired_outputs_created':0,'lossless_outputs_created':0,'outputs_reused':0,'candidates_rejected':0},'files':[a.to_dict()]}
            out=d/'r.md';write_md(out,run);txt=out.read_text(encoding='utf-8')
            self.assertIn('**Protección CRC MPEG**',txt);self.assertIn('CRC válidos/discrepantes: `39` / `1`',txt);self.assertIn('`MPEG_CRC_MISMATCH`',txt)

if __name__=='__main__':unittest.main()
