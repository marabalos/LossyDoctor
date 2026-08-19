from __future__ import annotations
import hashlib,json,os,shutil,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
from formats.mpeg import analyze
from app import lossless_export
from app.external import decode_to_raw_file
from app.utils import sha256_file
FFMPEG=os.environ.get('LOSSYDOCTOR_FFMPEG') or shutil.which('ffmpeg')

def raw_hash(path,tmp):
 out=tmp/(path.name+'.raw');r=decode_to_raw_file(path,out,FFMPEG);assert r['passed'];return sha256_file(out)

@unittest.skipUnless(FFMPEG,'ffmpeg required')
class V06CompleteCleanRegression(unittest.TestCase):
 def test_static_fixture_hashes(self):
  expect={'00_healthy_master_xing.mp3':'12f614e3fc507471071c20426f511aa4698b8349e8356be2831e943adf7574a7','01_unplayable_complete_clean_bad_id3.mp3':'7d4a87587a2894b08b4def4bc1d414699b996988f45b7d0788592167cc482729','02_playable_partial_negative.mp3':'97572ab1984848f98a6f7c7244d13fbe663f1e315faaa09308b36ec5f631621d'}
  for n,h in expect.items():self.assertEqual(sha256_file(ROOT/'samples/recovery_v06'/n),h)
 def test_complete_clean_engine_remains_exact(self):
  with tempfile.TemporaryDirectory() as td:
   d=Path(td);master=d/'master.mp3';bad=d/'bad.mp3';shutil.copy2(ROOT/'samples/recovery_v06/00_healthy_master_xing.mp3',master);shutil.copy2(ROOT/'samples/recovery_v06/01_unplayable_complete_clean_bad_id3.mp3',bad)
   m=analyze(bad);a=lossless_export.assess(m,'UNPLAYABLE');self.assertEqual(a['pcm_class'],'COMPLETE_CLEAN')
   ex=lossless_export.export(bad,sha256_file(bad),m,FFMPEG,'UNPLAYABLE',True);self.assertEqual(ex['status'],'CREATED');out=Path(ex['outputs'][0]['output_path']);man=ex['outputs'][0]['manifest']
   self.assertEqual(man['source_canonical_pcm_sha256'],man['flac_decoded_pcm_sha256']);self.assertEqual(raw_hash(master,d),raw_hash(out,d));self.assertEqual(man['source_pcm_recovery_class'],'COMPLETE_CLEAN')
 def test_real_payload_loss_never_promotes_complete_clean(self):
  m=analyze(ROOT/'samples/recovery_v06/02_playable_partial_negative.mp3');self.assertEqual(lossless_export.assess(m,'PLAYABLE')['pcm_class'],'PARTIAL_CLEAN')
