from __future__ import annotations
import hashlib, json, os, shutil, tempfile, unittest
from pathlib import Path
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
from app.config import load_config
from app.pipeline import analyze_file
from app.external import decode_to_raw_file
from app import lossless_export
from app.utils import sha256_file
from formats.mpeg import analyze

FFMPEG=os.environ.get('LOSSYDOCTOR_FFMPEG') or shutil.which('ffmpeg')
FFPROBE=os.environ.get('LOSSYDOCTOR_FFPROBE') or shutil.which('ffprobe')
CFG=load_config(ROOT/'config.toml')


def raw_bytes(path:Path,tmp:Path):
    out=tmp/(path.name+'.raw'); r=decode_to_raw_file(path,out,FFMPEG); assert r['passed'],r; return out.read_bytes()

@unittest.skipUnless(FFMPEG and FFPROBE,'ffmpeg/ffprobe required')
class VerifiedPartialLosslessTests(unittest.TestCase):
    def setUp(self):
        self.td=tempfile.TemporaryDirectory();self.d=Path(self.td.name)
        for p in (ROOT/'samples/recovery_v05').glob('*.mp3'):shutil.copy2(p,self.d/p.name)
        self.manifest=json.loads((ROOT/'samples/recovery_v05_manifest.json').read_text(encoding='utf-8'))
    def tearDown(self):self.td.cleanup()

    def A(self,name):
        p=self.d/name; before=sha256_file(p); a=analyze_file(p,CFG,ROOT,FFMPEG,FFPROBE);self.assertEqual(before,sha256_file(p),'source modified');return a

    def test_healthy_sources_do_not_convert_to_flac(self):
        for n in ('00_healthy_master_xing.mp3','02_healthy_master_no_xing.mp3'):
            with self.subTest(n=n):
                a=self.A(n);self.assertEqual(a.final_status,['OK']);self.assertEqual(a.pcm_recovery_class,'NOT_REQUIRED');self.assertEqual(a.lossless_export,[])

    def test_complete_clean_unplayable_exports_exact_recovered_lossless(self):
        master=self.d/'00_healthy_master_xing.mp3'; b=bytearray(master.read_bytes());b[6:10]=b'\x7f\x7f\x7f\x7f';bad=self.d/'complete-clean-bad-id3.mp3';bad.write_bytes(b);before=sha256_file(bad)
        # Since v0.8 the pipeline prefers a verified bitstream repair for this exact defect.
        # Keep recovered-lossless as an independently tested preservation capability.
        m=analyze(bad);self.assertEqual(before,sha256_file(bad));self.assertEqual(lossless_export.assess(m,'UNPLAYABLE')['pcm_class'],'COMPLETE_CLEAN')
        ex=lossless_export.export(bad,before,m,FFMPEG,'UNPLAYABLE',True);self.assertEqual(ex['status'],'CREATED')
        o=ex['outputs'];self.assertEqual(len(o),1);man=o[0]['manifest'];self.assertEqual(man['validation_result'],'PASS');self.assertEqual(man['source_canonical_pcm_sha256'],man['flac_canonical_pcm_sha256'])
        self.assertEqual(hashlib.sha256(raw_bytes(master,self.d)).hexdigest(),hashlib.sha256(raw_bytes(Path(o[0]['output_path']),self.d)).hexdigest())

    def test_known_timeline_regions_match_healthy_master_and_gap_is_exact_zero(self):
        a=self.A('01_unplayable_known_gap.mp3');self.assertEqual(a.playability,'UNPLAYABLE');self.assertEqual(a.pcm_recovery_class,'PARTIAL_CLEAN');self.assertEqual(a.final_status,['RECOVERED_PARTIAL_LOSSLESS']);self.assertEqual(a.validity_domains['TIMELINE_VALIDITY'],'RECOVERABLE_EXACT')
        outs=a.lossless_export[0]['outputs'];self.assertEqual(len(outs),1);man=outs[0]['manifest'];self.assertEqual(man['materialization'],'TIMELINE_PRESERVED_ZERO_GAPS')
        expected=self.manifest['cases']['01_unplayable_known_gap.mp3'];self.assertEqual([[r['source_start_sample'],r['source_end_sample']] for r in man['regions']],expected['expected_clean_regions']);self.assertEqual(sum(g['sample_count'] for g in man['synthesized_gap_silence']),expected['expected_zero_gap_samples'])
        master_raw=raw_bytes(self.d/'00_healthy_master_xing.mp3',self.d); out_raw=raw_bytes(Path(outs[0]['output_path']),self.d);channels=man['canonical_pcm_profile']['channels'];stride=channels*4
        self.assertEqual(len(out_raw)//stride,man['canonical_presentation_window']['logical_sample_count'])
        import subprocess
        q=subprocess.run([FFPROBE,'-v','error','-select_streams','a:0','-show_entries','stream=codec_name,sample_fmt,bits_per_raw_sample','-of','json',str(Path(outs[0]['output_path']))],capture_output=True,text=True,check=True)
        st=json.loads(q.stdout)['streams'][0];self.assertEqual(st['codec_name'],'flac');self.assertEqual(st['sample_fmt'],'s32');self.assertEqual(st['bits_per_raw_sample'],'32')
        for r in man['regions']:
            a0=r['source_start_sample']*stride;a1=r['source_end_sample']*stride; h=hashlib.sha256(master_raw[a0:a1]).hexdigest();self.assertEqual(r['pcm_sha256'],h);self.assertEqual(hashlib.sha256(out_raw[a0:a1]).hexdigest(),h)
        for g in man['synthesized_gap_silence']:
            z=out_raw[g['output_start_sample']*stride:g['output_end_sample']*stride];self.assertEqual(z,b'\0'*len(z))

    def test_unknown_timeline_exports_parts_and_matches_master_fragment_ground_truth(self):
        a=self.A('03_unplayable_unknown_gap.mp3');self.assertEqual(a.playability,'UNPLAYABLE');self.assertEqual(a.pcm_recovery_class,'PARTIAL_CLEAN');self.assertEqual(a.final_status,['RECOVERED_HOMOGENEOUS_OPEN_PARTIAL_LOSSLESS'])
        outs=a.lossless_export[0]['outputs'];self.assertEqual(len(outs),2);self.assertTrue(all(o['manifest']['materialization']=='INDEPENDENT_HOMOGENEOUS_PROVEN_REGION' for o in outs));self.assertTrue(all(not o['manifest']['synthesized_gap_silence'] for o in outs))
        master=self.d/'02_healthy_master_no_xing.mp3'; hb=master.read_bytes(); delta=self.manifest['cases']['03_unplayable_unknown_gap.mp3']['deleted_bytes']; deletion_start=self.manifest['cases']['03_unplayable_unknown_gap.mp3']['deletion_master_byte_start']
        for o in outs:
            man=o['manifest'];r=man;ctx=r['decode_context_byte_start'];end=r['source_byte_end'];discard=r.get('discarded_context_samples',0)
            # Bytes after the deletion in the damaged source map +delta into the healthy master.
            shift=delta if ctx>=deletion_start-delta else 0
            frag=self.d/f'groundtruth-part{man["part_index"]}.mp3';frag.write_bytes(hb[ctx+shift:end+shift]);fr_raw=raw_bytes(frag,self.d);stride=man['canonical_pcm_profile']['channels']*4;gt=fr_raw[discard*stride:discard*stride+r['sample_count']*stride]
            out_raw=raw_bytes(Path(o['output_path']),self.d);self.assertEqual(hashlib.sha256(gt).hexdigest(),r['source_region_pcm_sha256']);self.assertEqual(hashlib.sha256(out_raw).hexdigest(),r['source_region_pcm_sha256'])

    def test_second_run_reuses_all_three_lossless_outputs_without_multiplication(self):
        self.A('01_unplayable_known_gap.mp3');self.A('03_unplayable_unknown_gap.mp3')
        before=sorted(p.name for p in self.d.glob('*recovered-partial-lossless*.flac'))
        a1=self.A('01_unplayable_known_gap.mp3');a2=self.A('03_unplayable_unknown_gap.mp3')
        reused=[o for a in (a1,a2) for ex in a.lossless_export for o in ex.get('outputs',[]) if o.get('status')=='REUSED']
        self.assertEqual(len(reused),3);self.assertEqual(sorted(p.name for p in self.d.glob('*recovered-partial-lossless*.flac')),before)

    def test_interrupted_multi_output_run_reuses_complete_part_and_creates_only_missing_part(self):
        with mock.patch.dict(os.environ,{'LOSSYDOCTOR_JOURNAL_ROOT':str(self.d/'journal')}):
            first=self.A('03_unplayable_unknown_gap.mp3');outputs=first.lossless_export[0]['outputs'];self.assertEqual(len(outputs),2)
            missing=outputs[1];Path(missing['manifest_path']).unlink();Path(missing['output_path']).unlink()
            resumed=self.A('03_unplayable_unknown_gap.mp3');result=resumed.lossless_export[0];statuses=[o['status'] for o in result['outputs']]
            self.assertEqual(result['status'],'MIXED_CREATED_REUSED');self.assertEqual(statuses,['REUSED','CREATED'])
            self.assertEqual(len(list(self.d.glob('*recovered-homogeneous-open-partial-lossless*.flac'))),2)
            self.assertFalse(any(' 2].flac' in p.name for p in self.d.glob('*.flac')))

if __name__=='__main__':unittest.main()
