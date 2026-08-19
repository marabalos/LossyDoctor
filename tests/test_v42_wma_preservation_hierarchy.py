from __future__ import annotations
import hashlib,json,os,shutil,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
from app.config import load_config
from app.pipeline import analyze_file
from app.wma_preservation_hierarchy import POLICY,ORDER,resolve
from reporting.markdown_report import write_md

BASE=ROOT/'samples/asf_wma_preservation_hierarchy_v42'
MAN=json.loads((ROOT/'samples/asf_wma_preservation_hierarchy_v42_manifest.json').read_text(encoding='utf-8'))
CFG=load_config(ROOT/'config.toml')
FFMPEG=os.environ.get('LOSSYDOCTOR_FFMPEG') or shutil.which('ffmpeg')
FFPROBE=os.environ.get('LOSSYDOCTOR_FFPROBE') or shutil.which('ffprobe')
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def outputs(a):return [o for ex in a.lossless_export for o in ex.get('outputs',[])]

@unittest.skipUnless(FFMPEG and FFPROBE,'ffmpeg/ffprobe required')
class WmaPreservationHierarchyV42(unittest.TestCase):
    def test_version_manifest_hashes_and_binary_distinctness(self):
        self.assertEqual(MAN['hierarchy_policy'],POLICY);self.assertEqual(POLICY,'WMA_PRESERVATION_HIERARCHY_STRICT_V1')
        historical=set()
        for d in (ROOT/'samples').iterdir():
            if not d.is_dir() or d==BASE:continue
            for p in d.glob('*.wma'):historical.add(sha(p))
        seen=set()
        for n,c in MAN['cases'].items():
            h=sha(BASE/n);self.assertEqual(h,c['sha256'],n);self.assertNotIn(h,seen,n);self.assertNotIn(h,historical,n);seen.add(h)
        self.assertEqual(len(seen),7)

    def test_all_source_level_outcomes_are_exclusive_and_expected(self):
        with tempfile.TemporaryDirectory() as td:
            td=Path(td)
            for p in BASE.glob('*.wma'):shutil.copy2(p,td/p.name)
            for n,c in MAN['cases'].items():
                a=analyze_file(td/n,CFG,ROOT,FFMPEG,FFPROBE);h=a.format_facts['wma_preservation_hierarchy']
                self.assertEqual(a.playability,c['expected_playability'],n);self.assertEqual([i.code for i in a.issues],c['expected_issues'],n)
                self.assertEqual(h['policy'],POLICY,n);self.assertEqual(h['order'],ORDER,n);self.assertEqual(h['selected_tier'],c['expected_tier'],n)
                self.assertEqual(h['selected_output_count'],c['expected_selected_output_count'],n);self.assertTrue(h['exclusive_outcome'],n);self.assertIsNone(h['policy_violation'],n)

    def test_fail_closed_on_competing_published_wma_families(self):
        loss=[
          {'status':'CREATED','outputs':[{'status':'CREATED','manifest':{'derivation_kind':'RECOVERED_WMA_PROVEN_REGION_LOSSLESS'}}]},
          {'status':'CREATED','outputs':[{'status':'CREATED','manifest':{'derivation_kind':'RECOVERED_WMA_CONVERGED_SUFFIX_LOSSLESS'}}]},
        ]
        h=resolve(loss,{'pcm_class':'WMA_PROVEN_MULTI_REGION'},'PLAYABLE',{'ASF_WMA_DEMUX_TIMELINE_DISCONTINUITY'})
        self.assertFalse(h['exclusive_outcome']);self.assertEqual(h['selected_tier'],ORDER[0])
        self.assertEqual(h['policy_violation'],'MULTIPLE_WMA_PRESERVATION_TIERS_PUBLISHED_SIMULTANEOUSLY')

    def test_unknown_lossless_family_fails_closed(self):
        h=resolve([{'status':'CREATED','outputs':[{'status':'CREATED','manifest':{'derivation_kind':'SOMETHING_NEW'}}]}],{},'PLAYABLE',{'X'})
        self.assertFalse(h['exclusive_outcome']);self.assertEqual(h['policy_violation'],'UNKNOWN_WMA_PRESERVATION_DERIVATION_FAMILY')

    def test_tier_families_match_derivation_kinds(self):
        with tempfile.TemporaryDirectory() as td:
            td=Path(td)
            for n in ('01_tier1_wmav2_multi_region.wma','03_tier2_wmav2_single_suffix.wma'):
                shutil.copy2(BASE/n,td/n);a=analyze_file(td/n,CFG,ROOT,FFMPEG,FFPROBE);h=a.format_facts['wma_preservation_hierarchy'];k={o['manifest']['derivation_kind'] for o in outputs(a)}
                if n.startswith('01_'):
                    self.assertEqual(h['selected_tier'],ORDER[0]);self.assertEqual(k,{'RECOVERED_WMA_PROVEN_REGION_LOSSLESS'});self.assertEqual(len(outputs(a)),3)
                else:
                    self.assertEqual(h['selected_tier'],ORDER[1]);self.assertEqual(k,{'RECOVERED_WMA_CONVERGED_SUFFIX_LOSSLESS'});self.assertEqual(len(outputs(a)),1)

    def test_two_run_contract_eight_created_then_eight_reused(self):
        with tempfile.TemporaryDirectory() as td:
            td=Path(td)
            for p in BASE.glob('*.wma'):shutil.copy2(p,td/p.name)
            names=sorted(MAN['cases'])
            r1=[analyze_file(td/n,CFG,ROOT,FFMPEG,FFPROBE) for n in names]
            r2=[analyze_file(td/n,CFG,ROOT,FFMPEG,FFPROBE) for n in names]
            self.assertEqual((sum(a.run_status=='SUCCESS' for a in r1),sum(a.run_status=='SUCCESS_WITH_RECOVERY' for a in r1),sum(a.run_status=='SUCCESS_WITH_FINDINGS' for a in r1)),(1,4,2))
            self.assertEqual(sum(o.get('status')=='CREATED' for a in r1 for o in outputs(a)),8)
            self.assertEqual(sum(o.get('status')=='REUSED' for a in r2 for o in outputs(a)),8)
            self.assertEqual(len(list(td.glob('*.flac'))),8);self.assertEqual(len(list(td.glob('*.lossydoctor-manifest.json'))),8)

    def test_markdown_exposes_strict_wma_hierarchy(self):
        with tempfile.TemporaryDirectory() as td:
            td=Path(td);src=td/'x.wma';shutil.copy2(BASE/'01_tier1_wmav2_multi_region.wma',src)
            a=analyze_file(src,CFG,ROOT,FFMPEG,FFPROBE).to_dict()
            run={'run_id':'v42','started_at':'2026-08-17T15:20:00-03:00','summary':{'discovered':1,'processed':1,'ok':0,'with_findings':1,'skipped':0,'failed':0,'repaired_outputs_created':0,'lossless_outputs_created':3,'outputs_reused':0,'candidates_rejected':0},'files':[a]}
            p=td/'r.md';write_md(p,run);txt=p.read_text(encoding='utf-8')
            self.assertIn('Jerarquía de resolución de preservación ASF/WMA',txt);self.assertIn('WMA_PRESERVATION_HIERARCHY_STRICT_V1',txt)
            self.assertIn('resultado exclusivo: `True`',txt);self.assertIn('TIER_1_PROVEN_MULTI_REGION_RECOVERY',txt);self.assertIn('WMA_ASF_PRESERVATION_HIERARCHY',txt)

if __name__=='__main__':unittest.main()
