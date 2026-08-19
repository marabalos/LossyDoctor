from __future__ import annotations
import json, os, shutil, tempfile, unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
from app.config import load_config
from app.pipeline import analyze_file
from app.utils import sha256_file, run_id, local_iso
from formats.mpeg import analyze as analyze_mpeg

FFMPEG=os.environ.get('LOSSYDOCTOR_FFMPEG') or shutil.which('ffmpeg')
FFPROBE=os.environ.get('LOSSYDOCTOR_FFPROBE') or shutil.which('ffprobe')
CFG=load_config(ROOT/'config.toml')

class LocalTimeV09Tests(unittest.TestCase):
    def test_run_id_and_visible_timestamp_use_explicit_local_offset(self):
        dt=datetime(2026,8,16,16,25,0,123456,tzinfo=timezone(timedelta(hours=-3)))
        self.assertEqual(run_id(dt),'20260816_162500_123456-0300')
        self.assertEqual(local_iso(dt),'2026-08-16T16:25:00.123456-03:00')
        self.assertNotIn('Z',run_id(dt))

@unittest.skipUnless(FFMPEG and FFPROBE,'ffmpeg/ffprobe required')
class TerminalPaddingV09Tests(unittest.TestCase):
    def setUp(self):
        self.td=tempfile.TemporaryDirectory();self.d=Path(self.td.name)
        for p in (ROOT/'samples/terminal_padding_v09').glob('*.mp3'):shutil.copy2(p,self.d/p.name)
        self.manifest=json.loads((ROOT/'samples/terminal_padding_v09_manifest.json').read_text(encoding='utf-8'))
    def tearDown(self):self.td.cleanup()
    def A(self,name):
        p=self.d/name; before=sha256_file(p); a=analyze_file(p,CFG,ROOT,FFMPEG,FFPROBE); self.assertEqual(before,sha256_file(p),'source modified'); return a
    def test_fixture_hashes(self):
        for name,c in self.manifest['cases'].items(): self.assertEqual(sha256_file(self.d/name),c['sha256'])
    def test_zero_padding_is_objectively_classified_and_repaired(self):
        p=self.d/'01_terminal_zero_padding.mp3';m=analyze_mpeg(p)
        self.assertEqual([i.code for i in m['issues']],['MPEG_TRAILING_ZERO_PADDING'])
        tr=m['facts']['trailing_region'];self.assertEqual(tr['type'],'PADDING');self.assertTrue(tr['all_zero']);self.assertEqual(tr['byte_length'],64)
        source_mpeg=m['data'][m['facts']['first_audio_offset']:m['facts']['scan_end_offset']]
        a=self.A(p.name);self.assertEqual(a.final_status,['REPAIRED_SAFE'])
        e=[x for x in a.repair_execution if x.get('repair_spec_id')=='DROP_CONFIRMED_TERMINAL_ZERO_PADDING'][0]
        self.assertEqual(e['status'],'CREATED');man=e['manifest'];out=Path(e['output_path'])
        self.assertEqual(out.stat().st_size,p.stat().st_size-64)
        self.assertEqual(man['changed_byte_ranges'][0]['removed_bytes'],64);self.assertEqual(man['changed_byte_ranges'][0]['field'],'TERMINAL_ZERO_PADDING')
        self.assertTrue(man['verification']['passed']);self.assertTrue(man['verification']['source_sha256_unchanged'])
        om=analyze_mpeg(out);out_mpeg=om['data'][om['facts']['first_audio_offset']:om['facts']['scan_end_offset']]
        self.assertEqual(source_mpeg,out_mpeg);self.assertEqual(om['issues'],[])
    def test_unknown_trailer_is_never_auto_deleted(self):
        m=analyze_mpeg(self.d/'02_terminal_unknown_bytes.mp3')
        self.assertIn('MPEG_TRAILING_UNKNOWN_BYTES',[i.code for i in m['issues']]);self.assertEqual(m['facts']['trailing_region']['type'],'UNKNOWN_REGION');self.assertFalse(m['facts']['trailing_region']['all_zero'])
        a=self.A('02_terminal_unknown_bytes.mp3');self.assertEqual(a.final_status,['ANOMALY_UNCHANGED'])
        self.assertFalse(any(x.get('repair_spec_id')=='DROP_CONFIRMED_TERMINAL_ZERO_PADDING' and x.get('status') in ('CREATED','REUSED') for x in a.repair_execution))
    def test_second_run_reuses_without_duplicate(self):
        a=self.A('01_terminal_zero_padding.mp3');self.assertTrue(any(e.get('status')=='CREATED' for e in a.repair_execution))
        before=sorted(p.name for p in self.d.iterdir())
        b=self.A('01_terminal_zero_padding.mp3');self.assertTrue(any(e.get('repair_spec_id')=='DROP_CONFIRMED_TERMINAL_ZERO_PADDING' and e.get('status')=='REUSED' for e in b.repair_execution))
        self.assertEqual(before,sorted(p.name for p in self.d.iterdir()))

if __name__=='__main__':unittest.main()
