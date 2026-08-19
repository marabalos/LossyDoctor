from __future__ import annotations
import copy,hashlib,json,os,shutil,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
from app.config import load_config
from app.pipeline import analyze_file
from formats.aac_adts import analyze,crc16_fdk8005,_header
from reporting.markdown_report import write_md

BASE=ROOT/'samples/aac_adts_crc_v44'
MAN=json.loads((ROOT/'samples/aac_adts_crc_v44_manifest.json').read_text(encoding='utf-8'))
CFG=load_config(ROOT/'config.toml'); CFG_AUDIT=copy.deepcopy(CFG)
CFG_AUDIT['repair']['enabled']=False;CFG_AUDIT['lossless_recovery']['enabled']=False
FFMPEG=os.environ.get('LOSSYDOCTOR_FFMPEG') or shutil.which('ffmpeg')
FFPROBE=os.environ.get('LOSSYDOCTOR_FFPROBE') or shutil.which('ffprobe')
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()

class AacAdtsProvenanceCrcV44(unittest.TestCase):
    def test_manifest_hashes_issue_sets_and_expected_crc_facts(self):
        self.assertEqual(MAN['app_version'],'0.44.0');self.assertEqual(MAN['policy'],'0.44-aac-adts-frame-provenance-crc-audit-1')
        seen=set()
        for n,c in MAN['cases'].items():
            p=BASE/n;h=sha(p);self.assertEqual(h,c['sha256'],n);self.assertNotIn(h,seen,n);seen.add(h)
            q=analyze(p);ad=q['facts']['adts'];self.assertEqual([i.code for i in q['issues']],c['expected_issues'],n)
            self.assertEqual(ad['complete_frame_count'],c['expected_complete_frames'],n)
            self.assertEqual(ad['crc_validation'],c['expected_crc_validation'],n)
            self.assertEqual(ad['crc_present_frame_count'],c['expected_crc_present_frames'],n)
            self.assertEqual(ad['multi_rdb_header_crc_checked_count'],c['expected_multi_header_crc_checked'],n)
            self.assertEqual(ad['multi_rdb_header_crc_authenticated_count'],c['expected_multi_header_crc_authenticated'],n)
            self.assertEqual(ad['multi_rdb_header_crc_mismatch_count'],c['expected_multi_header_crc_mismatch'],n)
            self.assertEqual(ad['multi_rdb_position_invalid_count'],c['expected_multi_position_invalid'],n)
        self.assertEqual(len(seen),7)

    def test_crc16_algorithm_and_multi_rdb_header_scope_authenticate_exactly(self):
        # Build a minimal synthetic two-RDB transport frame. Payload syntax is intentionally
        # opaque; v0.44 only authenticates the header scope that FDK checks before AAC payload parsing.
        src=(BASE/'00_healthy_crc_absent_control.aac').read_bytes();h=_header(src,0);orig=bytearray(src[:7]);p=b'\x11'*12+b'\x00\x00'+b'\x22'*12+b'\x00\x00'
        orig[1]&=0xFE;orig[6]=(orig[6]&0xFC)|1
        pos=14;L=7+2+2+len(p);orig[3]=(orig[3]&0xFC)|((L>>11)&3);orig[4]=(L>>3)&0xFF;orig[5]=(orig[5]&0x1F)|((L&7)<<5)
        posb=pos.to_bytes(2,'big');crc=crc16_fdk8005(bytes(orig)+posb)
        with tempfile.TemporaryDirectory() as td:
            f=Path(td)/'x.aac';f.write_bytes(bytes(orig)+posb+crc.to_bytes(2,'big')+p)
            q=analyze(f);ad=q['facts']['adts'];row=q['facts']['frames'][0]
            self.assertEqual(ad['multi_rdb_header_crc_checked_count'],1);self.assertEqual(ad['multi_rdb_header_crc_authenticated_count'],1)
            self.assertEqual(ad['multi_rdb_header_crc_mismatch_count'],0);self.assertTrue(row['header_crc_authenticated'])
            self.assertEqual(row['header_crc_computed_hex'],f'{crc:04x}')

    def test_single_rdb_crc_scope_is_explicitly_deferred_not_falsely_authenticated(self):
        q=analyze(BASE/'01_healthy_single_rdb_crc_present_deferred.aac');ad=q['facts']['adts'];fr=q['facts']['frames']
        self.assertEqual(q['issues'],[]);self.assertEqual(ad['crc_present_frame_count'],53)
        self.assertEqual(ad['single_rdb_crc_authentication_deferred_count'],53)
        self.assertEqual(ad['crc_validation'],'SINGLE_RDB_CRC_PRESENT_AUTHENTICATION_DEFERRED')
        self.assertTrue(all(r['header_crc_authenticated'] is None for r in fr))
        self.assertTrue(all(r['raw_data_block_crc_authentication']=='DEFERRED_AAC_RAW_DATA_BLOCK_SYNTAX_REQUIRED' for r in fr))

    def test_multi_rdb_crc_mismatch_and_position_invalid_are_independent(self):
        a=analyze(BASE/'03_multi_rdb_header_crc_mismatch.aac');b=analyze(BASE/'04_multi_rdb_position_invalid.aac')
        self.assertEqual([i.code for i in a['issues']],['AAC_ADTS_HEADER_CRC_MISMATCH'])
        self.assertEqual([i.code for i in b['issues']],['AAC_ADTS_RAW_DATA_BLOCK_POSITION_INVALID'])
        self.assertEqual(a['facts']['adts']['multi_rdb_header_crc_authenticated_count'],0)
        self.assertEqual(b['facts']['adts']['multi_rdb_header_crc_authenticated_count'],1)

    @unittest.skipUnless(FFMPEG and FFPROBE,'ffmpeg/ffprobe required')
    def test_pipeline_remains_audit_only_and_controls_map_to_ffmpeg(self):
        got={n:analyze_file(BASE/n,CFG_AUDIT,ROOT,FFMPEG,FFPROBE) for n in MAN['cases']}
        for n,a in got.items():
            self.assertEqual(a.repair_execution,[],n);self.assertEqual(a.lossless_export,[],n);self.assertEqual(a.pcm_recovery_class,'AAC_ADTS_AUDIT_ONLY',n)
            self.assertEqual(a.recovery_assessment['policy'],'AAC_ADTS_FRAME_CRC_AUTHORITY',n)
            self.assertTrue(any(d.get('code')=='AAC_ADTS_FRAME_CRC_AUTHORITY' and d.get('decision')=='FRAME_PROVENANCE_AND_CRC_AUDIT_ONLY_NO_REPAIR_OR_RECOVERY' for d in a.policy_decisions),n)
        for n in ('00_healthy_crc_absent_control.aac','01_healthy_single_rdb_crc_present_deferred.aac'):
            d=got[n].format_facts['adts_demux_evidence'];self.assertTrue(d['all_equal'],n)

    @unittest.skipUnless(FFMPEG and FFPROBE,'ffmpeg/ffprobe required')
    def test_markdown_exposes_scope_limits_and_crc_algorithm(self):
        a=analyze_file(BASE/'01_healthy_single_rdb_crc_present_deferred.aac',CFG_AUDIT,ROOT,FFMPEG,FFPROBE).to_dict()
        run={'run_id':'v44','started_at':'2026-08-17T16:20:00-03:00','summary':{'discovered':1,'processed':1,'ok':1,'with_findings':0,'skipped':0,'failed':0,'repaired_outputs_created':0,'lossless_outputs_created':0,'outputs_reused':0,'candidates_rejected':0},'files':[a]}
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'r.md';write_md(p,run);txt=p.read_text(encoding='utf-8')
            self.assertIn('Auditoría de procedencia de cuadros AAC/ADTS y protección CRC',txt)
            self.assertIn('0x8005',txt);self.assertIn('0xFFFF',txt)
            self.assertIn('SINGLE_RDB_CRC_PRESENT_AUTHENTICATION_DEFERRED',txt)
            self.assertIn('Autoridad de procedencia y CRC ADTS: reparación `NONE` · recuperación PCM `NONE`',txt)

    @unittest.skipUnless(FFMPEG and FFPROBE,'ffmpeg/ffprobe required')
    def test_e2e_shape_two_ok_five_findings_zero_derivatives(self):
        rows=[analyze_file(BASE/n,CFG_AUDIT,ROOT,FFMPEG,FFPROBE) for n in sorted(MAN['cases'])]
        self.assertEqual(sum(a.run_status=='SUCCESS' for a in rows),2)
        self.assertEqual(sum(a.run_status=='SUCCESS_WITH_FINDINGS' for a in rows),5)
        self.assertEqual(sum(a.run_status=='FAILED' for a in rows),0)
        self.assertEqual(sum(bool(a.repair_execution) for a in rows),0);self.assertEqual(sum(bool(a.lossless_export) for a in rows),0)

if __name__=='__main__':unittest.main()
