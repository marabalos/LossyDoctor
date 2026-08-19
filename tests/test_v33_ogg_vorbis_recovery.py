from __future__ import annotations
import hashlib,json,os,shutil,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
from app.config import load_config
from app.external import decode_to_raw_file
from app.pipeline import analyze_file
from app.version import APP_VERSION,POLICY_VERSION
from app.vorbis_recovery import assess
from formats.ogg_vorbis import analyze
from reporting.markdown_report import write_md

BASE=ROOT/'samples/ogg_vorbis_recovery_v33'
MAN=json.loads((ROOT/'samples/ogg_vorbis_recovery_v33_manifest.json').read_text(encoding="utf-8"))
CFG=load_config(ROOT/'config.toml')
FFMPEG=os.environ.get('LOSSYDOCTOR_FFMPEG') or shutil.which('ffmpeg')
FFPROBE=os.environ.get('LOSSYDOCTOR_FFPROBE') or shutil.which('ffprobe')
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()

class OggVorbisRecoveryV33(unittest.TestCase):
    def test_version_policy_hashes_and_structural_expectations(self):
        self.assertEqual(MAN['policy'],'0.33-ogg-vorbis-proven-region-recovery-1');self.assertGreaterEqual(tuple(map(int,APP_VERSION.split('.'))),(0,33,0));self.assertTrue(POLICY_VERSION)
        seen=set()
        for n,c in MAN['cases'].items():
            p=BASE/n;self.assertEqual(sha(p),c['sha256'],n);self.assertNotIn(c['sha256'],seen);seen.add(c['sha256'])
            q=analyze(p);self.assertEqual([i.code for i in q['issues']],c['expected_issues'],n)
        self.assertEqual(len(seen),6)

    @unittest.skipUnless(FFMPEG and FFPROBE,'ffmpeg/ffprobe required')
    def test_assessment_regions_and_playability_policy(self):
        for n,c in MAN['cases'].items():
            p=BASE/n;a=analyze_file(p,CFG,ROOT,FFMPEG,FFPROBE)
            self.assertEqual(a.playability,c['expected_playability'],n)
            self.assertEqual(a.pcm_recovery_class,c['expected_pcm_class'],n)
            self.assertEqual(bool(a.recovery_assessment.get('eligible')),c['expected_eligible'],n)
            self.assertEqual([[r['pcm_start'],r['pcm_end']] for r in a.recovery_assessment.get('regions',[])],c['expected_regions'],n)
        self.assertEqual(analyze_file(BASE/'04_playable_sequence_gap_control.ogg',CFG,ROOT,FFMPEG,FFPROBE).lossless_export,[])
        self.assertEqual(analyze_file(BASE/'05_unplayable_invalid_header_negative.ogg',CFG,ROOT,FFMPEG,FFPROBE).lossless_export,[])

    @unittest.skipUnless(FFMPEG and FFPROBE,'ffmpeg/ffprobe required')
    def test_recovered_pcm_matches_pristine_reference_slices_exactly(self):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td)
            for p in BASE.glob('*.ogg'):shutil.copy2(p,d/p.name)
            ref=d/MAN['reference_file'];refraw=d/'reference.raw';self.assertTrue(decode_to_raw_file(ref,refraw,FFMPEG)['passed']);rb=refraw.read_bytes();frame_bytes=8
            outs=[]
            for n in sorted(MAN['cases']):
                a=analyze_file(d/n,CFG,ROOT,FFMPEG,FFPROBE)
                for ex in a.lossless_export:
                    outs.extend(ex.get('outputs',[]))
            self.assertEqual(len(outs),5)
            for o in outs:
                m=o['manifest'];self.assertEqual(m['derivation_kind'],'RECOVERED_VORBIS_PROVEN_REGION_LOSSLESS')
                self.assertEqual(m['materialization'],'VORBIS_AUTHENTICATED_PACKET_CHAIN_PRIMED_OVERLAP_ADD')
                self.assertEqual(m['priming_packet_count'],1);self.assertFalse(m['vorbis_packet_bytes_modified']);self.assertEqual(m['synthesized_missing_span'],'NONE')
                raw=d/(Path(o['output_path']).stem+'.raw');self.assertTrue(decode_to_raw_file(Path(o['output_path']),raw,FFMPEG)['passed'])
                expected=rb[m['source_pcm_start']*frame_bytes:m['source_pcm_end']*frame_bytes]
                self.assertEqual(raw.read_bytes(),expected,Path(o['output_path']).name)
                self.assertEqual(hashlib.sha256(expected).hexdigest(),m['region_pcm_sha256'])
                self.assertEqual(m['region_pcm_sha256'],m['flac_decoded_pcm_sha256'])

    @unittest.skipUnless(FFMPEG and FFPROBE,'ffmpeg/ffprobe required')
    def test_two_run_creation_then_exact_reuse(self):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td)
            for p in BASE.glob('*.ogg'):shutil.copy2(p,d/p.name)
            first=[analyze_file(d/n,CFG,ROOT,FFMPEG,FFPROBE) for n in sorted(MAN['cases'])]
            created=sum(1 for a in first for ex in a.lossless_export for o in ex.get('outputs',[]) if o.get('status')=='CREATED')
            self.assertEqual(created,5)
            second=[analyze_file(d/n,CFG,ROOT,FFMPEG,FFPROBE) for n in sorted(MAN['cases'])]
            reused=sum(1 for a in second for ex in a.lossless_export for o in ex.get('outputs',[]) if o.get('status')=='REUSED')
            created2=sum(1 for a in second for ex in a.lossless_export for o in ex.get('outputs',[]) if o.get('status')=='CREATED')
            self.assertEqual((created2,reused),(0,5))
            self.assertEqual(len(list(d.glob('*recovered-vorbis-proven-region*.flac'))),5)
            self.assertEqual(len(list(d.glob('*recovered-vorbis-proven-region*.flac.lossydoctor-manifest.json'))),5)

    @unittest.skipUnless(FFMPEG and FFPROBE,'ffmpeg/ffprobe required')
    def test_pipeline_first_run_contract_and_classification(self):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td)
            for p in BASE.glob('*.ogg'):shutil.copy2(p,d/p.name)
            rows=[analyze_file(d/n,CFG,ROOT,FFMPEG,FFPROBE) for n in sorted(MAN['cases'])]
            self.assertEqual(sum(a.run_status=='SUCCESS' for a in rows),1)
            self.assertEqual(sum(a.run_status=='SUCCESS_WITH_RECOVERY' for a in rows),3)
            self.assertEqual(sum(a.run_status=='SUCCESS_WITH_FINDINGS' for a in rows),2)
            self.assertEqual(sum(a.run_status=='FAILED' for a in rows),0)
            self.assertEqual(sum(1 for a in rows for ex in a.lossless_export for o in ex.get('outputs',[]) if o.get('status')=='CREATED'),5)
            for a in rows[1:4]:self.assertTrue(any(x['code']=='VORBIS_PRESERVATION_HIERARCHY_AUTHORITY' for x in a.policy_decisions))

    def test_provenance_math_uses_priming_and_exact_overlap_intervals(self):
        for n in ('01_unplayable_missing_middle_page.ogg','02_unplayable_crc_middle_page.ogg'):
            q=analyze(BASE/n);v=q['facts']['vorbis_recovery_evidence'];self.assertEqual([(r['pcm_start'],r['pcm_end']) for r in v['candidate_regions']],[(0,39488),(60992,88200)])
            self.assertNotEqual(v['candidate_regions'][1]['priming_packet_index'],v['candidate_regions'][1]['first_published_overlap_packet_index'])
        q=analyze(BASE/'03_unplayable_truncated_tail.ogg');self.assertEqual([(r['pcm_start'],r['pcm_end']) for r in q['facts']['vorbis_recovery_evidence']['candidate_regions']],[(0,87616)])

    @unittest.skipUnless(FFMPEG and FFPROBE,'ffmpeg/ffprobe required')
    def test_markdown_exposes_vorbis_recovery_details(self):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td);src=d/'x.ogg';shutil.copy2(BASE/'01_unplayable_missing_middle_page.ogg',src)
            a=analyze_file(src,CFG,ROOT,FFMPEG,FFPROBE).to_dict();run={'run_id':'v33','started_at':'2026-08-17T02:00:00-03:00','summary':{'discovered':1,'processed':1,'ok':0,'with_findings':1,'skipped':0,'failed':0,'repaired_outputs_created':0,'lossless_outputs_created':2,'outputs_reused':0,'candidates_rejected':0},'files':[a]}
            p=d/'r.md';write_md(p,run);txt=p.read_text(encoding="utf-8")
            self.assertIn('RECOVERED_VORBIS_PROVEN_REGION_LOSSLESS',txt);self.assertIn('Región Vorbis demostrada',txt);self.assertIn('paquete de preparación',txt);self.assertIn('intervalo ausente sintetizado `NONE`',txt)

if __name__=='__main__':unittest.main()
