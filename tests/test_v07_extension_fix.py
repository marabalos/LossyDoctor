from __future__ import annotations
import json,os,shutil,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
from app.config import load_config
from app.pipeline import analyze_file
from app.utils import sha256_file
CFG=load_config(ROOT/'config.toml');FFMPEG=os.environ.get('LOSSYDOCTOR_FFMPEG') or shutil.which('ffmpeg');FFPROBE=os.environ.get('LOSSYDOCTOR_FFPROBE') or shutil.which('ffprobe')
MAN=json.loads((ROOT/'samples/extension_v07_manifest.json').read_text(encoding='utf-8'))
@unittest.skipUnless(FFMPEG and FFPROBE,'ffmpeg/ffprobe required')
class V07ExtensionRegression(unittest.TestCase):
 def setUp(self):
  self.td=tempfile.TemporaryDirectory();self.d=Path(self.td.name)
  for name in MAN['cases']:shutil.copy2(ROOT/'samples/extension_v07'/name,self.d/name)
 def tearDown(self):self.td.cleanup()
 def A(self,n):
  p=self.d/n;b=sha256_file(p);a=analyze_file(p,CFG,ROOT,FFMPEG,FFPROBE);self.assertEqual(sha256_file(p),b);return a
 def test_three_high_confidence_mismatches_publish_byte_identical(self):
  cases=[('00_mp3_named_wma.wma','.mp3'),('01_wma_named_mp3.mp3','.wma'),('02_opus_named_bin.bin','.opus')]
  for n,ext in cases:
   with self.subTest(n=n):
    p=self.d/n;h=sha256_file(p);a=self.A(n);e=next(x for x in a.repair_execution if x.get('status')=='CREATED');o=Path(e['output_path']);self.assertEqual(o.suffix,ext);self.assertEqual(sha256_file(o),h);self.assertEqual(e['manifest']['changed_byte_ranges'],[])
 def test_medium_confidence_mp4_is_blocked(self):
  a=self.A('03_aac_mp4_named_mp3.mp3');e=next(x for x in a.repair_execution if x.get('repair_spec_id')=='FIX_EXTENSION_BYTE_IDENTICAL');self.assertEqual(e['status'],'BLOCKED')
 def test_compatible_ogg_extension_is_not_mismatch(self):
  p=self.d/'generic.ogg';shutil.copy2(self.d/'02_opus_named_bin.bin',p);a=self.A('generic.ogg');self.assertFalse(any(i.code=='EXTENSION_CONTENT_MISMATCH' for i in a.issues));self.assertEqual(a.final_status,['OK'])
 def test_second_run_reuses_without_duplicate(self):
  a=self.A('00_mp3_named_wma.wma');before=sorted(x.name for x in self.d.glob('*extension-fixed*.mp3'));b=self.A('00_mp3_named_wma.wma');self.assertTrue(any(e.get('status')=='REUSED' for e in b.repair_execution));self.assertEqual(before,sorted(x.name for x in self.d.glob('*extension-fixed*.mp3')))
 def test_unrelated_existing_target_is_not_overwritten(self):
  target=self.d/'00_mp3_named_wma [extension-fixed].mp3';target.write_bytes(b'FOREIGN');a=self.A('00_mp3_named_wma.wma');e=next(x for x in a.repair_execution if x.get('status')=='CREATED');self.assertEqual(target.read_bytes(),b'FOREIGN');self.assertIn('[extension-fixed 2]',Path(e['output_path']).name)
