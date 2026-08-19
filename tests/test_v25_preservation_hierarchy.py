from __future__ import annotations
import hashlib,json,os,shutil,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
from app.config import load_config
from app.pipeline import analyze_file
from app.preservation_hierarchy import resolve,ORDER,POLICY
from reporting.markdown_report import write_md

BASE=ROOT/'samples/preservation_hierarchy_v25'
MAN=json.loads((ROOT/'samples/preservation_hierarchy_v25_manifest.json').read_text(encoding="utf-8"))
CFG=load_config(ROOT/'config.toml')
FFMPEG=os.environ.get('LOSSYDOCTOR_FFMPEG') or shutil.which('ffmpeg')
FFPROBE=os.environ.get('LOSSYDOCTOR_FFPROBE') or shutil.which('ffprobe')

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def outputs(a):
    rep=[x for x in a.repair_execution if x.get('status') in ('CREATED','REUSED') and (x.get('manifest') or {}).get('derivation_kind')=='REPAIRED_SAFE']
    los=[o for e in a.lossless_export for o in (e.get('outputs') or []) if o.get('status') in ('CREATED','REUSED')]
    return rep,los

class PreservationHierarchyV25(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not FFMPEG or not FFPROBE:raise unittest.SkipTest('ffmpeg/ffprobe unavailable')

    def test_static_fixture_hashes_are_binary_distinct(self):
        seen=set()
        for n,c in MAN['cases'].items():
            p=BASE/n;self.assertEqual(sha(p),c['sha256']);self.assertNotIn(c['sha256'],seen);seen.add(c['sha256'])
        self.assertEqual(len(seen),7)

    def test_resolution_function_detects_forbidden_cross_tier_coexistence(self):
        repair={'status':'CREATED','manifest':{'derivation_kind':'REPAIRED_SAFE','verification':{'passed':True,'strict_decode':'PASS','playback_decode':'PASS','ffprobe':'PASS'}}}
        loss={'status':'CREATED','outputs':[{'status':'CREATED','manifest':{'derivation_kind':'RECOVERED_LOSSLESS'}}]}
        r=resolve([repair],[loss],{'pcm_class':'COMPLETE_CLEAN'},'UNPLAYABLE')
        self.assertEqual(r['selected_tier'],ORDER[0]);self.assertFalse(r['exclusive_outcome']);self.assertEqual(r['policy_violation'],'MULTIPLE_PRESERVATION_TIERS_PUBLISHED_SIMULTANEOUSLY')

    def test_all_hierarchy_tiers_and_second_run_reuse(self):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td)
            for name in MAN['cases']:shutil.copy2(BASE/name,d/name)
            first=[]
            for n,c in MAN['cases'].items():
                a=analyze_file(d/n,CFG,ROOT,FFMPEG,FFPROBE);first.append(a)
                h=(a.format_facts or {}).get('preservation_hierarchy') or {}
                self.assertEqual(h['policy'],POLICY,n);self.assertEqual(h['selected_tier'],c['expected_tier'],n);self.assertTrue(h['exclusive_outcome'],n);self.assertIsNone(h['policy_violation'],n);self.assertEqual(h['selected_output_count'],c['expected_output_count'],n)
                rep,los=outputs(a);kinds=[(x.get('manifest') or {}).get('derivation_kind') for x in rep+los]
                self.assertEqual(len(kinds),c['expected_output_count'],n);self.assertTrue(all(k==c['expected_derivation_kind'] for k in kinds),n)
            self.assertEqual(sum(1 for a in first for x in outputs(a)[0] if x.get('status')=='CREATED'),1)
            self.assertEqual(sum(1 for a in first for x in outputs(a)[1] if x.get('status')=='CREATED'),10)
            second=[]
            for n,c in MAN['cases'].items():
                a=analyze_file(d/n,CFG,ROOT,FFMPEG,FFPROBE);second.append(a)
                h=a.format_facts['preservation_hierarchy'];self.assertEqual(h['selected_tier'],c['expected_tier'],n);self.assertTrue(h['exclusive_outcome'])
            self.assertEqual(sum(1 for a in second for x in outputs(a)[0]+outputs(a)[1] if x.get('status')=='REUSED'),11)
            self.assertEqual(sum(1 for a in second for x in outputs(a)[0]+outputs(a)[1] if x.get('status')=='CREATED'),0)
            # No third family may appear by multiplication or fallback competition.
            deriv_files=[p for p in d.iterdir() if '[repaired]' in p.name or '[recovered-' in p.name]
            self.assertEqual(len([p for p in deriv_files if not p.name.endswith('.lossydoctor-manifest.json')]),11)

    def test_markdown_exposes_order_and_selected_tier(self):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td);src=d/'case.mp3';shutil.copy2(BASE/'01_tier2_complete_lossless.mp3',src)
            a=analyze_file(src,CFG,ROOT,FFMPEG,FFPROBE).to_dict()
            run={'run_id':'v25','started_at':'2026-08-16T23:00:00-03:00','summary':{'discovered':1,'processed':1,'ok':0,'with_findings':1,'skipped':0,'failed':0,'repaired_outputs_created':0,'lossless_outputs_created':1,'outputs_reused':0,'candidates_rejected':0},'files':[a]}
            p=d/'r.md';write_md(p,run);txt=p.read_text(encoding="utf-8")
            self.assertIn('Jerarquía de resolución de preservación MPEG',txt);self.assertIn(POLICY,txt);self.assertIn('TIER_2_COMPLETE_LOSSLESS_RECOVERY',txt);self.assertIn('resultado exclusivo: `True`',txt)

if __name__=='__main__':unittest.main()
