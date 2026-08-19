from __future__ import annotations
import hashlib,json,os,shutil,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
from app.config import load_config
from app.pipeline import analyze_file
from app.vorbis_preservation_hierarchy import POLICY,ORDER,resolve
from formats.ogg_vorbis import analyze
from reporting.markdown_report import write_md
BASE=ROOT/'samples/ogg_vorbis_preservation_hierarchy_v35'
MAN=json.loads((ROOT/'samples/ogg_vorbis_preservation_hierarchy_v35_manifest.json').read_text(encoding="utf-8"))
CFG=load_config(ROOT/'config.toml')
FFMPEG=os.environ.get('LOSSYDOCTOR_FFMPEG') or shutil.which('ffmpeg')
FFPROBE=os.environ.get('LOSSYDOCTOR_FFPROBE') or shutil.which('ffprobe')
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()

class VorbisPreservationHierarchyV35(unittest.TestCase):
    def test_fixture_hashes_issue_sets_and_binary_distinctness(self):
        self.assertEqual(MAN['app_version'],'0.35.0')
        self.assertEqual(MAN['policy'],'0.35-vorbis-preservation-hierarchy-1')
        self.assertEqual(MAN['hierarchy_policy'],POLICY)
        historical=set()
        for d in (ROOT/'samples').iterdir():
            if not d.is_dir() or d==BASE:continue
            for p in d.glob('*.ogg'):
                historical.add(sha(p))
        seen=set()
        for n,c in MAN['cases'].items():
            p=BASE/n; h=sha(p)
            self.assertEqual(h,c['sha256'],n);self.assertNotIn(h,seen,n);seen.add(h)
            self.assertNotIn(h,historical,n)
            self.assertEqual([i.code for i in analyze(p)['issues']],c['expected_issues'],n)
        self.assertEqual(len(seen),7)

    @unittest.skipUnless(FFMPEG and FFPROBE,'ffmpeg/ffprobe required')
    def test_all_source_level_hierarchy_outcomes_are_exclusive(self):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td)
            for p in BASE.glob('*.ogg'):shutil.copy2(p,d/p.name)
            for n,c in MAN['cases'].items():
                a=analyze_file(d/n,CFG,ROOT,FFMPEG,FFPROBE)
                h=a.format_facts['vorbis_preservation_hierarchy']
                self.assertEqual(a.playability,c['expected_playability'],n)
                self.assertEqual(h['policy'],POLICY,n);self.assertEqual(h['order'],ORDER,n)
                self.assertEqual(h['selected_tier'],c['expected_tier'],n)
                self.assertEqual(h['selected_output_count'],c['expected_selected_output_count'],n)
                self.assertTrue(h['exclusive_outcome'],n);self.assertIsNone(h['policy_violation'],n)

    @unittest.skipUnless(FFMPEG and FFPROBE,'ffmpeg/ffprobe required')
    def test_tier1_suppresses_vorbis_pcm_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'x.ogg';shutil.copy2(BASE/'01_tier1_verified_recapture.ogg',p)
            a=analyze_file(p,CFG,ROOT,FFMPEG,FFPROBE)
            self.assertEqual(a.format_facts['vorbis_preservation_hierarchy']['selected_tier'],ORDER[0])
            self.assertEqual(a.lossless_export,[])
            self.assertTrue(any(x['code']=='BITSTREAM_REPAIR_PRECEDES_PCM_DERIVATION' for x in a.policy_decisions))

    def test_fail_closed_on_competing_published_families(self):
        repair=[{'status':'CREATED','repair_spec_id':'OGG_RECAPTURE_VALID_PAGES_DROP_EXTRANEOUS_BYTES','manifest':{'derivation_kind':'REPAIRED_SAFE','verification':{'passed':True,'strict_decode':'PASS','playback_decode':'PASS','ffprobe':'PASS'}}}]
        loss=[{'status':'CREATED','outputs':[{'status':'CREATED','manifest':{'derivation_kind':'RECOVERED_VORBIS_PROVEN_REGION_LOSSLESS','includes_authenticated_eos':True,'temporary_eos_trim_samples':0}}]}]
        h=resolve(repair,loss,{'pcm_class':'VORBIS_PROVEN_REGIONS'},'UNPLAYABLE',{'OGG_SYNC_LOSS'})
        self.assertFalse(h['exclusive_outcome'])
        self.assertEqual(h['policy_violation'],'MULTIPLE_VORBIS_PRESERVATION_TIERS_PUBLISHED_SIMULTANEOUSLY')

    @unittest.skipUnless(FFMPEG and FFPROBE,'ffmpeg/ffprobe required')
    def test_two_run_contract_one_repair_five_lossless_then_six_reuses(self):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td)
            for p in BASE.glob('*.ogg'):shutil.copy2(p,d/p.name)
            names=sorted(MAN['cases'])
            first=[analyze_file(d/n,CFG,ROOT,FFMPEG,FFPROBE) for n in names]
            repaired=sum(1 for a in first for x in a.repair_execution if x.get('status')=='CREATED')
            lossless=sum(1 for a in first for e in a.lossless_export for x in e.get('outputs',[]) if x.get('status')=='CREATED')
            reused=sum(1 for a in first for x in a.repair_execution if x.get('status')=='REUSED')+sum(1 for a in first for e in a.lossless_export for x in e.get('outputs',[]) if x.get('status')=='REUSED')
            self.assertEqual((repaired,lossless,reused),(1,5,0))
            second=[analyze_file(d/n,CFG,ROOT,FFMPEG,FFPROBE) for n in names]
            repaired2=sum(1 for a in second for x in a.repair_execution if x.get('status')=='CREATED')
            lossless2=sum(1 for a in second for e in a.lossless_export for x in e.get('outputs',[]) if x.get('status')=='CREATED')
            reused2=sum(1 for a in second for x in a.repair_execution if x.get('status')=='REUSED')+sum(1 for a in second for e in a.lossless_export for x in e.get('outputs',[]) if x.get('status')=='REUSED')
            self.assertEqual((repaired2,lossless2,reused2),(0,0,6))

    @unittest.skipUnless(FFMPEG and FFPROBE,'ffmpeg/ffprobe required')
    def test_markdown_exposes_strict_vorbis_hierarchy(self):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td);src=d/'x.ogg';shutil.copy2(BASE/'02_tier2_missing_page_exact_eos.ogg',src)
            a=analyze_file(src,CFG,ROOT,FFMPEG,FFPROBE).to_dict()
            run={'run_id':'v35','started_at':'2026-08-17T02:30:00-03:00','summary':{'discovered':1,'processed':1,'ok':0,'with_findings':1,'skipped':0,'failed':0,'repaired_outputs_created':0,'lossless_outputs_created':2,'outputs_reused':0,'candidates_rejected':0},'files':[a]}
            p=d/'r.md';write_md(p,run);txt=p.read_text(encoding="utf-8")
            self.assertIn('Jerarquía de resolución de preservación Ogg/Vorbis',txt)
            self.assertIn('VORBIS_PRESERVATION_HIERARCHY_STRICT_V1',txt)
            self.assertIn('resultado exclusivo: `True`',txt)
            self.assertIn('TIER_2_PROVEN_REGION_RECOVERY_AUTHENTICATED_EOS',txt)

    @unittest.skipUnless(FFMPEG and FFPROBE,'ffmpeg/ffprobe required')
    def test_first_run_status_shape(self):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td)
            for p in BASE.glob('*.ogg'):shutil.copy2(p,d/p.name)
            rows=[analyze_file(d/n,CFG,ROOT,FFMPEG,FFPROBE) for n in sorted(MAN['cases'])]
            self.assertEqual(sum(a.run_status=='SUCCESS' for a in rows),1)
            self.assertEqual(sum(a.run_status=='SUCCESS_WITH_REPAIR' for a in rows),1)
            self.assertEqual(sum(a.run_status=='SUCCESS_WITH_RECOVERY' for a in rows),3)
            self.assertEqual(sum(a.run_status=='SUCCESS_WITH_FINDINGS' for a in rows),2)
            self.assertEqual(sum(a.run_status=='FAILED' for a in rows),0)

if __name__=='__main__':unittest.main()
