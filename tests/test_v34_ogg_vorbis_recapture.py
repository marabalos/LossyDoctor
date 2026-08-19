from __future__ import annotations
import hashlib,json,os,shutil,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
from app.config import load_config
from app.pipeline import analyze_file
from formats.ogg_vorbis import analyze
from reporting.markdown_report import write_md
BASE=ROOT/'samples/ogg_vorbis_repair_v34'
MAN=json.loads((ROOT/'samples/ogg_vorbis_repair_v34_manifest.json').read_text(encoding="utf-8"))
CFG=load_config(ROOT/'config.toml')
FFMPEG=os.environ.get('LOSSYDOCTOR_FFMPEG') or shutil.which('ffmpeg')
FFPROBE=os.environ.get('LOSSYDOCTOR_FFPROBE') or shutil.which('ffprobe')
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()

class OggVorbisRecaptureV34(unittest.TestCase):
    def test_fixture_hashes_and_issue_sets(self):
        self.assertEqual(MAN['app_version'],'0.34.0');self.assertEqual(MAN['policy'],'0.34-ogg-vorbis-safe-page-recapture-1')
        seen=set()
        for n,c in MAN['cases'].items():
            p=BASE/n;self.assertEqual(sha(p),c['sha256'],n);self.assertNotIn(c['sha256'],seen);seen.add(c['sha256'])
            self.assertEqual([i.code for i in analyze(p)['issues']],c['expected_issues'],n)
        self.assertEqual(len(seen),7)

    @unittest.skipUnless(FFMPEG and FFPROBE,'ffmpeg/ffprobe required')
    def test_three_safe_recaptures_are_exact_page_preserving_repairs(self):
        healthy_sha=sha(BASE/'00_healthy_control.ogg')
        with tempfile.TemporaryDirectory() as td:
            d=Path(td)
            for p in BASE.glob('*.ogg'):shutil.copy2(p,d/p.name)
            rows={n:analyze_file(d/n,CFG,ROOT,FFMPEG,FFPROBE) for n in sorted(MAN['cases'])}
            created=[]
            for n in ('01_prefix_junk_recapture.ogg','02_interpage_junk_recapture.ogg','03_suffix_junk_recapture.ogg'):
                ex=next(x for x in rows[n].repair_execution if x.get('status')=='CREATED');created.append(ex)
                self.assertEqual(ex['repair_spec_id'],'OGG_RECAPTURE_VALID_PAGES_DROP_EXTRANEOUS_BYTES')
                m=ex['manifest'];v=m['verification']
                self.assertEqual(m['output_sha256'],healthy_sha)
                self.assertTrue(v['passed']);self.assertTrue(v['all_retained_page_crc_valid']);self.assertTrue(v['retained_page_bytes_exact'])
                self.assertTrue(v['packet_and_timeline_semantics_equal']);self.assertTrue(v['vorbis_audio_packet_hashes_equal'])
                self.assertTrue(v['vorbis_identification_equal']);self.assertTrue(v['vorbis_comment_equal']);self.assertTrue(v['vorbis_setup_equal']);self.assertTrue(v['candidate_pcm_regions_equal'])
                self.assertFalse(v['page_bytes_modified']);self.assertFalse(v['vorbis_packet_bytes_modified']);self.assertFalse(m['ogg_page_bytes_modified']);self.assertFalse(m['vorbis_packet_bytes_modified'])
            self.assertEqual(len(created),3)
            self.assertEqual(rows['01_prefix_junk_recapture.ogg'].lossless_export,[])
            self.assertTrue(any(x['code']=='BITSTREAM_REPAIR_PRECEDES_PCM_DERIVATION' for x in rows['01_prefix_junk_recapture.ogg'].policy_decisions))

    @unittest.skipUnless(FFMPEG and FFPROBE,'ffmpeg/ffprobe required')
    def test_crc_and_sequence_ambiguity_remain_blocked(self):
        a=analyze_file(BASE/'04_crc_mismatch_blocked.ogg',CFG,ROOT,FFMPEG,FFPROBE)
        b=analyze_file(BASE/'05_sequence_gap_blocked.ogg',CFG,ROOT,FFMPEG,FFPROBE)
        self.assertIn(('REWRITE_OGG_PAGE_CRC','BLOCKED'),[(x.get('repair_spec_id'),x.get('status')) for x in a.repair_execution])
        self.assertIn(('RENUMBER_OGG_PAGE_SEQUENCE','BLOCKED'),[(x.get('repair_spec_id'),x.get('status')) for x in b.repair_execution])
        self.assertFalse(a.lossless_export);self.assertFalse(b.lossless_export)

    @unittest.skipUnless(FFMPEG and FFPROBE,'ffmpeg/ffprobe required')
    def test_recapture_is_blocked_if_any_retained_page_crc_is_not_authenticated(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'06_junk_plus_crc_blocked.ogg';shutil.copy2(BASE/p.name,p)
            a=analyze_file(p,CFG,ROOT,FFMPEG,FFPROBE)
            pairs=[(x.get('repair_spec_id'),x.get('status')) for x in a.repair_execution]
            self.assertIn(('REWRITE_OGG_PAGE_CRC','BLOCKED'),pairs)
            self.assertIn(('OGG_RECAPTURE_VALID_PAGES_DROP_EXTRANEOUS_BYTES','BLOCKED'),pairs)
            # v0.33 proven-region recovery remains a fallback when the source is unplayable.
            self.assertEqual(sum(len(ex.get('outputs',[])) for ex in a.lossless_export),1)
            self.assertEqual(a.lossless_export[0]['outputs'][0]['manifest']['derivation_kind'],'RECOVERED_VORBIS_PROVEN_REGION_LOSSLESS')

    @unittest.skipUnless(FFMPEG and FFPROBE,'ffmpeg/ffprobe required')
    def test_two_run_contract_three_repairs_plus_one_fallback_then_four_reuses(self):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td)
            for p in BASE.glob('*.ogg'):shutil.copy2(p,d/p.name)
            names=sorted(MAN['cases'])
            first=[analyze_file(d/n,CFG,ROOT,FFMPEG,FFPROBE) for n in names]
            repaired=sum(1 for a in first for x in a.repair_execution if x.get('status')=='CREATED')
            lossless=sum(1 for a in first for e in a.lossless_export for x in e.get('outputs',[]) if x.get('status')=='CREATED')
            reused=sum(1 for a in first for x in a.repair_execution if x.get('status')=='REUSED')+sum(1 for a in first for e in a.lossless_export for x in e.get('outputs',[]) if x.get('status')=='REUSED')
            self.assertEqual((repaired,lossless,reused),(3,1,0))
            second=[analyze_file(d/n,CFG,ROOT,FFMPEG,FFPROBE) for n in names]
            repaired2=sum(1 for a in second for x in a.repair_execution if x.get('status')=='CREATED')
            lossless2=sum(1 for a in second for e in a.lossless_export for x in e.get('outputs',[]) if x.get('status')=='CREATED')
            reused2=sum(1 for a in second for x in a.repair_execution if x.get('status')=='REUSED')+sum(1 for a in second for e in a.lossless_export for x in e.get('outputs',[]) if x.get('status')=='REUSED')
            self.assertEqual((repaired2,lossless2,reused2),(0,0,4))

    @unittest.skipUnless(FFMPEG and FFPROBE,'ffmpeg/ffprobe required')
    def test_markdown_exposes_vorbis_recapture_and_blocked_repairs(self):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td);src=d/'x.ogg';shutil.copy2(BASE/'01_prefix_junk_recapture.ogg',src)
            a=analyze_file(src,CFG,ROOT,FFMPEG,FFPROBE).to_dict();run={'run_id':'v34','started_at':'2026-08-17T02:00:00-03:00','summary':{'discovered':1,'processed':1,'ok':0,'with_findings':1,'skipped':0,'failed':0,'repaired_outputs_created':1,'lossless_outputs_created':0,'outputs_reused':0,'candidates_rejected':0},'files':[a]}
            p=d/'r.md';write_md(p,run);txt=p.read_text(encoding="utf-8")
            self.assertIn('OGG_RECAPTURE_VALID_PAGES_DROP_EXTRANEOUS_BYTES',txt);self.assertIn('Nuevo análisis posterior a la reparación: `PASS`',txt);self.assertIn('Jerarquía de resolución de preservación Ogg/Vorbis',txt);self.assertIn('recodificación de audio: `False`',txt)

    @unittest.skipUnless(FFMPEG and FFPROBE,'ffmpeg/ffprobe required')
    def test_pipeline_first_run_summary_shape(self):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td)
            for p in BASE.glob('*.ogg'):shutil.copy2(p,d/p.name)
            rows=[analyze_file(d/n,CFG,ROOT,FFMPEG,FFPROBE) for n in sorted(MAN['cases'])]
            self.assertEqual(sum(a.run_status=='SUCCESS' for a in rows),1)
            self.assertEqual(sum(a.run_status=='SUCCESS_WITH_REPAIR' for a in rows),3)
            self.assertEqual(sum(a.run_status=='SUCCESS_WITH_RECOVERY' for a in rows),1)
            self.assertEqual(sum(a.run_status=='SUCCESS_WITH_FINDINGS' for a in rows),2)
            self.assertEqual(sum(a.run_status=='FAILED' for a in rows),0)

if __name__=='__main__':unittest.main()
