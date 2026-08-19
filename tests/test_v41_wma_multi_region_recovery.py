from __future__ import annotations
import copy,hashlib,json,os,shutil,subprocess,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
from app.config import load_config
from app.pipeline import analyze_file
from formats.asf_wma import analyze
from reporting.markdown_report import write_md

BASE=ROOT/'samples/asf_wma_multi_region_v41'
OLD=ROOT/'samples/asf_wma_recovery_v40'
MAN=json.loads((ROOT/'samples/asf_wma_multi_region_v41_manifest.json').read_text(encoding='utf-8'))
CFG=load_config(ROOT/'config.toml');CFG_AUDIT=copy.deepcopy(CFG);CFG_AUDIT['lossless_recovery']['enabled']=False
FFMPEG=os.environ.get('LOSSYDOCTOR_FFMPEG') or shutil.which('ffmpeg');FFPROBE=os.environ.get('LOSSYDOCTOR_FFPROBE') or shutil.which('ffprobe')
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def decode_raw(src:Path,dst:Path):
    r=subprocess.run([FFMPEG,'-v','error','-i',str(src),'-map','0:a:0','-f','s32le','-acodec','pcm_s32le','-y',str(dst)],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    if r.returncode:raise AssertionError(r.stderr.decode(errors='replace'))
def outputs(a):return [o for ex in a.lossless_export for o in ex.get('outputs',[])]

@unittest.skipUnless(FFMPEG and FFPROBE,'ffmpeg/ffprobe required')
class WmaMultiRegionRecoveryV41(unittest.TestCase):
    def test_version_manifest_hashes_and_distinctness(self):
        self.assertEqual(MAN['app_version'],'0.41.0');self.assertEqual(MAN['policy'],'0.41-wma-multi-region-recovery-1')
        seen=set();old={sha(p) for p in OLD.glob('*.wma')}
        for n,c in MAN['cases'].items():
            p=BASE/n;h=sha(p);self.assertEqual(h,c['sha256'],n);self.assertNotIn(h,seen,n);self.assertNotIn(h,old,n);seen.add(h)
            self.assertEqual([i.code for i in analyze(p)['issues']],c['expected_parser_issues'],n)
        self.assertEqual(len(seen),9)

    def test_assessment_multi_gap_region_geometry_and_blocking(self):
        expected={
          '02_wmav2_two_independent_gaps.wma':[(0,18432),(20480,49152),(51200,83968)],
          '03_wmav1_two_independent_gaps.wma':[(0,9216),(10240,20480),(21504,30720)],
          '04_wmav2_mixed_double_single_gaps.wma':[(0,18432),(20480,51200),(53248,81920)],
          '05_wmav2_three_independent_gaps.wma':[(0,10240),(12288,36864),(38912,63488),(65536,81920)],
        }
        for n in MAN['cases']:
            a=analyze_file(BASE/n,CFG_AUDIT,ROOT,FFMPEG,FFPROBE);m=a.format_facts['wma_multi_region_recovery_assessment']
            if n in expected:
                self.assertTrue(m['eligible'],n);self.assertEqual(m['pcm_class'],'WMA_PROVEN_MULTI_REGION',n)
                self.assertEqual([(r['decoded_sample_start'],r['decoded_sample_end']) for r in m['regions']],expected[n],n)
                self.assertEqual(m['region_count'],len(expected[n]),n);self.assertEqual(len(m['excluded_context_intervals']),m['gap_count'],n)
                self.assertTrue(all(r['provenance_complete'] and r['pcm_sha256'] for r in m['regions']),n)
                self.assertTrue(all(x['sample_count']==m['decoder_frame_length_samples'] and not x['context_audio_published'] for x in m['excluded_context_intervals']),n)
                self.assertFalse(m['regions_concatenated']);self.assertFalse(m['full_source_pcm_timeline_claim']);self.assertEqual(m['synthesized_missing_span'],'NONE')
            else:self.assertFalse(m['eligible'],n)
        self.assertEqual(analyze_file(BASE/'06_timestamp_jump_no_missing_objects.wma',CFG_AUDIT,ROOT,FFMPEG,FFPROBE).pcm_recovery_class,'WMA_RECOVERY_BLOCKED')
        self.assertEqual(analyze_file(BASE/'07_incomplete_media_object_blocked.wma',CFG_AUDIT,ROOT,FFMPEG,FFPROBE).pcm_recovery_class,'WMA_RECOVERY_BLOCKED')
        self.assertEqual(analyze_file(BASE/'08_insufficient_postgap_context.wma',CFG_AUDIT,ROOT,FFMPEG,FFPROBE).pcm_recovery_class,'WMA_RECOVERY_BLOCKED')

    def test_positive_exports_and_manifest_invariants(self):
        for n in list(MAN['cases'])[2:6]:
            with tempfile.TemporaryDirectory() as td:
                p=Path(td)/n;shutil.copy2(BASE/n,p);a=analyze_file(p,CFG,ROOT,FFMPEG,FFPROBE);oo=outputs(a);self.assertEqual(len(oo),a.format_facts['wma_multi_region_recovery_assessment']['region_count'],n)
                for i,o in enumerate(oo,1):
                    m=o['manifest'];self.assertEqual(m['derivation_kind'],'RECOVERED_WMA_PROVEN_REGION_LOSSLESS');self.assertEqual(m['region_index'],i)
                    self.assertEqual(m['validation_result'],'PASS');self.assertEqual(m['region_pcm_sha256'],m['flac_decoded_pcm_sha256'])
                    self.assertFalse(m['context_audio_published']);self.assertFalse(m['regions_concatenated']);self.assertFalse(m['full_source_pcm_timeline_claim'])
                    self.assertEqual((m['synthesized_missing_span'],m['resampling'],m['channel_remix']),('NONE','NONE','NONE'));self.assertFalse(m['source_modified']);self.assertFalse(m['wma_media_object_bytes_modified'])

    def test_all_regions_equal_healthy_reference_slices(self):
        refs={'02_wmav2_two_independent_gaps.wma':'00_healthy_wmav2_multi_region_control.wma','03_wmav1_two_independent_gaps.wma':'01_healthy_wmav1_multi_region_control.wma','04_wmav2_mixed_double_single_gaps.wma':'00_healthy_wmav2_multi_region_control.wma','05_wmav2_three_independent_gaps.wma':'00_healthy_wmav2_multi_region_control.wma'}
        with tempfile.TemporaryDirectory() as td:
            td=Path(td);cache={}
            for n,h in refs.items():
                if h not in cache:
                    r=td/(h+'.raw');decode_raw(BASE/h,r);cache[h]=r.read_bytes()
                p=td/n;shutil.copy2(BASE/n,p);a=analyze_file(p,CFG,ROOT,FFMPEG,FFPROBE);flen=a.format_facts['demux_decoder_evidence']['decoded_frame_nb_samples_values'][0];ch=a.metadata['channels'];healthy=cache[h]
                for o in outputs(a):
                    m=o['manifest'];r=td/(f"{n}.{m['region_index']}.raw");decode_raw(Path(o['output_path']),r);got=r.read_bytes();objs=m['selected_media_object_numbers']
                    hs=0 if m['boundary_start']=='CANONICAL_DECODE_START' else (objs[0]-3)*flen
                    he=(len(healthy)//(ch*4)) if m['boundary_end']=='CANONICAL_DECODE_END' else (objs[-1]-2)*flen
                    self.assertEqual(got,healthy[hs*ch*4:he*ch*4],f'{n} region {m["region_index"]}')

    def test_single_gap_v40_fallback_still_exactly_one_suffix(self):
        with tempfile.TemporaryDirectory() as td:
            src=OLD/'02_wmav2_single_mid_gap_recover.wma';p=Path(td)/src.name;shutil.copy2(src,p);a=analyze_file(p,CFG,ROOT,FFMPEG,FFPROBE);oo=outputs(a)
            self.assertEqual(len(oo),1);self.assertEqual(oo[0]['manifest']['derivation_kind'],'RECOVERED_WMA_CONVERGED_SUFFIX_LOSSLESS')

    def test_markdown_surfaces_multi_region_scope(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'x.wma';shutil.copy2(BASE/'02_wmav2_two_independent_gaps.wma',p);a=analyze_file(p,CFG,ROOT,FFMPEG,FFPROBE).to_dict()
            run={'run_id':'v41','started_at':'2026-08-17T14:30:00-03:00','summary':{'discovered':1,'processed':1,'ok':0,'with_findings':1,'skipped':0,'failed':0,'repaired_outputs_created':0,'lossless_outputs_created':3,'outputs_reused':0,'candidates_rejected':0},'files':[a]}
            md=Path(td)/'r.md';write_md(md,run);txt=md.read_text(encoding='utf-8')
            self.assertIn('Evaluación de recuperación multirregión demostrada ASF/WMA',txt);self.assertIn('WMA_PROVEN_MULTI_REGION_RECOVERY_V1',txt);self.assertIn('RECOVERED_WMA_PROVEN_REGION_LOSSLESS',txt);self.assertIn('regiones concatenadas `False`',txt);self.assertIn('WMA_ASF_MULTI_REGION_RECOVERY_AUTHORITY',txt)

    def test_two_run_creation_then_reuse_exactly_thirteen(self):
        with tempfile.TemporaryDirectory() as td:
            td=Path(td)
            for p in BASE.glob('*.wma'):shutil.copy2(p,td/p.name)
            def run_once():return [analyze_file(td/n,CFG,ROOT,FFMPEG,FFPROBE) for n in sorted(MAN['cases'])]
            r1=run_once();r2=run_once()
            self.assertEqual((sum(a.run_status=='SUCCESS' for a in r1),sum(bool(a.issues) for a in r1)),(2,7))
            self.assertEqual(sum(o.get('status')=='CREATED' for a in r1 for o in outputs(a)),13)
            self.assertEqual(sum(o.get('status')=='REUSED' for a in r2 for o in outputs(a)),13)
            self.assertEqual(len(list(td.glob('*[[]recovered-wma-region-*[]].flac'))),13);self.assertEqual(len(list(td.glob('*.lossydoctor-manifest.json'))),13)

if __name__=='__main__':unittest.main()
