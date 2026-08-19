from __future__ import annotations
import os,shutil,subprocess,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
from app.config import load_config
from app.pipeline import analyze_file
CFG=load_config(ROOT/'config.toml')
FFMPEG=os.environ.get('LOSSYDOCTOR_FFMPEG') or shutil.which('ffmpeg')
FFPROBE=os.environ.get('LOSSYDOCTOR_FFPROBE') or shutil.which('ffprobe')

@unittest.skipUnless(FFMPEG and FFPROBE,'ffmpeg/ffprobe required')
class MultiFormatSmoke(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.t=tempfile.TemporaryDirectory();cls.d=Path(cls.t.name);base=[FFMPEG,'-y','-hide_banner','-loglevel','error','-f','lavfi','-i','sine=frequency=997:sample_rate=44100:duration=0.45','-ac','2']
  specs=[('healthy.mp3',['-c:a','libmp3lame','-b:a','128k']),('healthy.mp2',['-c:a','mp2','-b:a','192k']),('healthy.m4a',['-c:a','aac','-b:a','128k']),('healthy.ogg',['-c:a','libvorbis','-q:a','4']),('healthy.opus',['-c:a','libopus','-b:a','96k']),('healthy.wma',['-c:a','wmav2','-b:a','128k'])]
  for n,e in specs:subprocess.run(base+e+[str(cls.d/n)],check=True)
 @classmethod
 def tearDownClass(cls):cls.t.cleanup()
 def test_all_v1_families_enter_supported_audit_path(self):
  expected={'healthy.mp3':('MPEG_AUDIO','mp3'),'healthy.mp2':('MPEG_AUDIO','mp2'),'healthy.m4a':('MP4','aac'),'healthy.ogg':('OGG','vorbis'),'healthy.opus':('OGG','opus'),'healthy.wma':('ASF','wma')}
  for n,(cont,codec) in expected.items():
   with self.subTest(n=n):
    a=analyze_file(self.d/n,CFG,ROOT,FFMPEG,FFPROBE);self.assertEqual((a.detected_container,a.detected_codec),(cont,codec));self.assertEqual(a.playability,'PLAYABLE');self.assertNotEqual(a.run_status,'FAILED')
 def test_wrong_extension_is_detected_by_content(self):
  p=self.d/'wrong.wma';shutil.copy2(self.d/'healthy.mp3',p);a=analyze_file(p,CFG,ROOT,FFMPEG,FFPROBE);self.assertEqual(a.detected_codec,'mp3');self.assertTrue(any(i.code=='EXTENSION_CONTENT_MISMATCH' for i in a.issues))
 def test_strong_non_audio_magic_is_skipped_before_hash(self):
  p=self.d/'poster.jpg';p.write_bytes(b'\xff\xd8\xff\xe0'+b'X'*50000);a=analyze_file(p,CFG,ROOT,FFMPEG,FFPROBE);self.assertEqual(a.run_status,'SKIPPED_UNSUPPORTED');self.assertEqual(a.identity,{})
if __name__=='__main__':unittest.main()
