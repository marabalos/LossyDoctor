from __future__ import annotations
import os,shutil,tempfile,unittest,re
from pathlib import Path
from datetime import datetime,timezone,timedelta
ROOT=Path(__file__).resolve().parents[1]
from app.config import load_config
from app.pipeline import analyze_file
from app.utils import sha256_file,run_id,local_iso
CFG=load_config(ROOT/'config.toml');FFMPEG=os.environ.get('LOSSYDOCTOR_FFMPEG') or shutil.which('ffmpeg');FFPROBE=os.environ.get('LOSSYDOCTOR_FFPROBE') or shutil.which('ffprobe')
@unittest.skipUnless(FFMPEG and FFPROBE,'ffmpeg/ffprobe required')
class V09TerminalRegression(unittest.TestCase):
 def setUp(self):
  self.td=tempfile.TemporaryDirectory();self.d=Path(self.td.name)
  for p in (ROOT/'samples/terminal_padding_v09').glob('*.mp3'):shutil.copy2(p,self.d/p.name)
 def tearDown(self):self.td.cleanup()
 def A(self,n):
  p=self.d/n;h=sha256_file(p);a=analyze_file(p,CFG,ROOT,FFMPEG,FFPROBE);self.assertEqual(sha256_file(p),h);return a
 def test_confirmed_zero_padding_is_removed_exactly(self):
  a=self.A('01_terminal_zero_padding.mp3');e=next(e for e in a.repair_execution if e.get('status')=='CREATED');self.assertEqual(a.final_status,['REPAIRED_SAFE']);self.assertEqual(sha256_file(Path(e['output_path'])),sha256_file(self.d/'00_healthy_master.mp3'));r=e['manifest']['changed_byte_ranges'][0];self.assertEqual((r['operation'],r['removed_bytes'],r['field']),('DELETE',64,'TERMINAL_ZERO_PADDING'))
 def test_unknown_trailer_is_never_deleted(self):
  a=self.A('02_terminal_unknown_bytes.mp3');self.assertEqual(a.final_status,['ANOMALY_UNCHANGED']);self.assertFalse(any(e.get('status') in ('CREATED','REUSED') for e in a.repair_execution))
 def test_second_run_reuses_padding_repair(self):
  self.A('01_terminal_zero_padding.mp3');a=self.A('01_terminal_zero_padding.mp3');self.assertTrue(any(e.get('status')=='REUSED' for e in a.repair_execution))
 def test_explicit_offset_run_id_format(self):
  dt=datetime(2026,8,16,17,30,1,123456,tzinfo=timezone(timedelta(hours=-3)));self.assertEqual(run_id(dt),'20260816_173001_123456-0300');self.assertTrue(local_iso(dt).endswith('-03:00'))
