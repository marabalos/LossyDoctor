from __future__ import annotations
import hashlib, os, shutil, tempfile, unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
from app.repairs import plan, execute
from app.utils import sha256_file
from formats.mpeg import analyze

FFMPEG=os.environ.get('LOSSYDOCTOR_FFMPEG') or shutil.which('ffmpeg')
FFPROBE=os.environ.get('LOSSYDOCTOR_FFPROBE') or shutil.which('ffprobe')

@unittest.skipUnless(FFMPEG and FFPROBE,'ffmpeg/ffprobe required')
class RepairRegressionTests(unittest.TestCase):
    def test_single_bit_header_repair_still_roundtrips(self):
        master=ROOT/'samples/recovery_v05/02_healthy_master_no_xing.mp3'; m=analyze(master)
        target=next(f for f in m['frames'] if not f['is_vbr_header'] and f.get('logical_audio_index')==20)
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'damaged.mp3'; b=bytearray(master.read_bytes()); b[target['byte_start']]^=1; p.write_bytes(b); before=sha256_file(p)
            dm=analyze(p); pls=[x for x in plan(p,dm) if x['spec']['id']=='LOSSLESS_SINGLE_BIT_HEADER_REPAIR']
            self.assertEqual(len(pls),1);self.assertEqual(pls[0]['status'],'ELIGIBLE')
            ex=execute(p,before,dm,pls[0],FFMPEG,FFPROBE,True)
            self.assertEqual(ex['status'],'CREATED');self.assertEqual(sha256_file(p),before)
            self.assertEqual(sha256_file(Path(ex['output_path'])),sha256_file(master))

    def test_truncated_tail_without_vbr_metadata_repair(self):
        master=ROOT/'samples/recovery_v05/02_healthy_master_no_xing.mp3'
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'truncated.mp3'; b=master.read_bytes(); p.write_bytes(b[:-97]); before=sha256_file(p)
            dm=analyze(p);pls=[x for x in plan(p,dm) if x['spec']['id']=='DROP_TRUNCATED_TAIL_NO_VBR_METADATA']
            self.assertEqual(len(pls),1);self.assertEqual(pls[0]['status'],'ELIGIBLE')
            ex=execute(p,before,dm,pls[0],FFMPEG,FFPROBE,True)
            self.assertEqual(ex['status'],'CREATED');self.assertTrue(Path(ex['output_path']).exists());self.assertEqual(sha256_file(p),before)
            rr=analyze(Path(ex['output_path']));self.assertFalse(any(i.code in ('TRUNCATED_MPEG_FRAME','MPEG_SYNC_LOSS') for i in rr['issues']))

if __name__=='__main__':unittest.main()
