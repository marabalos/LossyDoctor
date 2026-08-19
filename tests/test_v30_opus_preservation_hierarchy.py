from __future__ import annotations
import hashlib,json,os,shutil,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
from app.config import load_config
from app.pipeline import analyze_file
from app.opus_preservation_hierarchy import resolve,ORDER,POLICY
from formats.ogg_opus import analyze
from reporting.markdown_report import write_md

BASE=ROOT/'samples/opus_preservation_hierarchy_v30'
MAN=json.loads((ROOT/'samples/opus_preservation_hierarchy_v30_manifest.json').read_text(encoding="utf-8"))
CFG=load_config(ROOT/'config.toml')
FFMPEG=os.environ.get('LOSSYDOCTOR_FFMPEG') or shutil.which('ffmpeg')
FFPROBE=os.environ.get('LOSSYDOCTOR_FFPROBE') or shutil.which('ffprobe')

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def outputs(a):
    rep=[x for x in a.repair_execution if x.get('status') in ('CREATED','REUSED') and (x.get('manifest') or {}).get('derivation_kind')=='REPAIRED_SAFE']
    los=[o for e in a.lossless_export for o in (e.get('outputs') or []) if o.get('status') in ('CREATED','REUSED')]
    return rep,los

class OpusPreservationHierarchyV30(unittest.TestCase):
    def test_version_policy_and_static_fixture_hashes(self):
        self.assertEqual(MAN['policy'],POLICY);self.assertEqual(POLICY,'OPUS_PRESERVATION_HIERARCHY_STRICT_V1')
        seen=set()
        for n,c in MAN['cases'].items():
            p=BASE/n;self.assertEqual(sha(p),c['sha256']);self.assertNotIn(c['sha256'],seen);seen.add(c['sha256'])
            self.assertEqual([i.code for i in analyze(p)['issues']],c['expected_issues'],n)
        self.assertEqual(len(seen),7)

    def test_resolution_function_detects_forbidden_cross_tier_coexistence(self):
        repair={'repair_spec_id':'OGG_RECAPTURE_VALID_PAGES_DROP_EXTRANEOUS_BYTES','status':'CREATED','manifest':{'derivation_kind':'REPAIRED_SAFE','verification':{'passed':True,'strict_decode':'PASS','playback_decode':'PASS','ffprobe':'PASS'}}}
        loss={'status':'CREATED','outputs':[{'status':'CREATED','manifest':{'derivation_kind':'RECOVERED_OPUS_PROVEN_REGION_LOSSLESS','includes_authenticated_eos':True,'eos_end_trim_samples_48k':480}}]}
        r=resolve([repair],[loss],{'pcm_class':'OPUS_PROVEN_REGIONS'},'UNPLAYABLE',['OGG_SYNC_LOSS'])
        self.assertEqual(r['selected_tier'],ORDER[0]);self.assertFalse(r['exclusive_outcome']);self.assertEqual(r['policy_violation'],'MULTIPLE_OPUS_PRESERVATION_TIERS_PUBLISHED_SIMULTANEOUSLY')

    @unittest.skipUnless(FFMPEG and FFPROBE,'ffmpeg/ffprobe required')
    def test_all_tiers_first_run_and_second_run_reuse(self):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td);names=sorted(MAN['cases'])
            for n in names:shutil.copy2(BASE/n,d/n)
            first=[]
            for n in names:
                c=MAN['cases'][n];a=analyze_file(d/n,CFG,ROOT,FFMPEG,FFPROBE);first.append(a)
                h=(a.format_facts or {}).get('opus_preservation_hierarchy') or {}
                self.assertEqual(h.get('policy'),POLICY,n);self.assertEqual(h.get('selected_tier'),c['expected_tier'],n);self.assertTrue(h.get('exclusive_outcome'),n);self.assertIsNone(h.get('policy_violation'),n);self.assertEqual(h.get('selected_output_count'),c['expected_output_count'],n)
                rep,los=outputs(a);self.assertEqual(len(rep)+len(los),c['expected_output_count'],n)
                if c.get('expected_derivation_kind'):
                    kinds=[(x.get('manifest') or {}).get('derivation_kind') for x in rep+los];self.assertTrue(all(k==c['expected_derivation_kind'] for k in kinds),n)
            self.assertEqual(sum(1 for a in first for x in outputs(a)[0] if x.get('status')=='CREATED'),1)
            self.assertEqual(sum(1 for a in first for x in outputs(a)[1] if x.get('status')=='CREATED'),5)
            second=[]
            for n in names:
                a=analyze_file(d/n,CFG,ROOT,FFMPEG,FFPROBE);second.append(a);self.assertEqual(a.format_facts['opus_preservation_hierarchy']['selected_tier'],MAN['cases'][n]['expected_tier'])
            self.assertEqual(sum(1 for a in second for x in outputs(a)[0]+outputs(a)[1] if x.get('status')=='REUSED'),6)
            self.assertEqual(sum(1 for a in second for x in outputs(a)[0]+outputs(a)[1] if x.get('status')=='CREATED'),0)
            deriv=[p for p in d.iterdir() if ('[repaired]' in p.name or '[recovered-' in p.name) and not p.name.endswith('.lossydoctor-manifest.json')]
            self.assertEqual(len(deriv),6)

    @unittest.skipUnless(FFMPEG and FFPROBE,'ffmpeg/ffprobe required')
    def test_tier2_is_source_family_even_with_prefix_and_eos_tail_outputs(self):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td);src=d/'case.opus';shutil.copy2(BASE/'02_tier2_missing_page_exact_eos.opus',src)
            a=analyze_file(src,CFG,ROOT,FFMPEG,FFPROBE);h=a.format_facts['opus_preservation_hierarchy'];rep,los=outputs(a)
            self.assertFalse(rep);self.assertEqual(len(los),2);self.assertEqual(h['selected_tier'],ORDER[1]);self.assertTrue(h['exclusive_outcome'])
            self.assertEqual(sum(1 for o in los if (o.get('manifest') or {}).get('includes_authenticated_eos')),1)
            eos=next(o['manifest'] for o in los if o['manifest'].get('includes_authenticated_eos'));self.assertEqual(eos['eos_end_trim_samples_48k'],480)

    @unittest.skipUnless(FFMPEG and FFPROBE,'ffmpeg/ffprobe required')
    def test_tier3_and_report_only_have_no_false_eos_claim_or_output(self):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td)
            for n in ('04_tier3_truncated_open_region.opus','05_tier4_playable_crc_report_only.opus','06_tier4_playable_sequence_report_only.opus'):shutil.copy2(BASE/n,d/n)
            a=analyze_file(d/'04_tier3_truncated_open_region.opus',CFG,ROOT,FFMPEG,FFPROBE);h=a.format_facts['opus_preservation_hierarchy'];rep,los=outputs(a)
            self.assertEqual(h['selected_tier'],ORDER[2]);self.assertEqual(len(los),1);self.assertFalse(los[0]['manifest'].get('includes_authenticated_eos'));self.assertEqual(los[0]['manifest'].get('eos_end_trim_samples_48k'),0)
            for n in ('05_tier4_playable_crc_report_only.opus','06_tier4_playable_sequence_report_only.opus'):
                b=analyze_file(d/n,CFG,ROOT,FFMPEG,FFPROBE);hh=b.format_facts['opus_preservation_hierarchy'];rr,ll=outputs(b)
                self.assertEqual(hh['selected_tier'],ORDER[3]);self.assertEqual(hh['selected_output_count'],0);self.assertFalse(rr);self.assertFalse(ll)

    @unittest.skipUnless(FFMPEG and FFPROBE,'ffmpeg/ffprobe required')
    def test_markdown_exposes_opus_hierarchy(self):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td);src=d/'case.opus';shutil.copy2(BASE/'02_tier2_missing_page_exact_eos.opus',src)
            a=analyze_file(src,CFG,ROOT,FFMPEG,FFPROBE).to_dict()
            run={'run_id':'v30','started_at':'2026-08-17T00:00:00-03:00','summary':{'discovered':1,'processed':1,'ok':0,'with_findings':1,'skipped':0,'failed':0,'repaired_outputs_created':0,'lossless_outputs_created':2,'outputs_reused':0,'candidates_rejected':0},'files':[a]}
            p=d/'r.md';write_md(p,run);txt=p.read_text(encoding="utf-8")
            self.assertIn('Jerarquía de resolución de preservación Ogg/Opus',txt);self.assertIn(POLICY,txt);self.assertIn(ORDER[1],txt);self.assertIn('resultado exclusivo: `True`',txt);self.assertIn('Recuperación con EOS autenticado presente: `True`',txt)

if __name__=='__main__':unittest.main()
