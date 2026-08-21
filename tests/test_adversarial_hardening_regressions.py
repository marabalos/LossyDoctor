from __future__ import annotations
import tempfile,unittest
from pathlib import Path

from app.repairs import plan
from formats.identify import identify
from formats.mpeg import analyze
from formats.ogg_opus import ogg_crc

ROOT=Path(__file__).resolve().parents[1]

def write_temp(directory,name,data):
    p=Path(directory)/name;p.write_bytes(data);return p

def damage_first_ogg_packet(source:Path):
    data=bytearray(source.read_bytes());nseg=data[26];end=27+nseg+sum(data[27:27+nseg]);body=27+nseg;data[body]^=1;data[22:26]=b'\0'*4;data[22:26]=ogg_crc(data[:end]).to_bytes(4,'little');return bytes(data)

class AdversarialHardeningRegressions(unittest.TestCase):
    def test_early_mpeg_frames_remain_accounted(self):
        cases=(ROOT/'samples/causal_chain_v10/00_healthy_master_no_xing.mp3',ROOT/'samples/xing_refresh_v11/00_healthy_ffmpeg_xing.mp3',ROOT/'samples/crc_l2_v15/00_mpeg1_l2_table1_crc_healthy.mp2')
        with tempfile.TemporaryDirectory() as td:
            for source in cases:
                base=analyze(source);raw=source.read_bytes();frames=base['frames'];early=raw[frames[0]['byte_start']:frames[1]['byte_end']];stream=raw[base['facts']['first_audio_offset']:base['facts']['scan_end_offset']];p=write_temp(td,source.name,early+b'BROKEN'+stream);row=analyze(p)
                self.assertIn(row['codec'],('mp3','mp2'));self.assertEqual(row['facts']['first_audio_offset'],0);self.assertGreaterEqual(row['facts']['frame_count'],len(frames)+2);self.assertEqual(row['frames'][0]['byte_start'],0);self.assertIn('MPEG_SYNC_LOSS',[i.code for i in row['issues']])

    def test_later_xing_is_structural_and_payload_marker_is_not(self):
        source=ROOT/'samples/xing_refresh_v11/00_healthy_ffmpeg_xing.mp3';base=analyze(source);raw=source.read_bytes();stream=raw[base['facts']['first_audio_offset']:base['facts']['scan_end_offset']]
        with tempfile.TemporaryDirectory() as td:
            concat=write_temp(td,'concat.mp3',stream+stream);row=analyze(concat);self.assertIn('MPEG_UNEXPECTED_STREAM_HEADER',[i.code for i in row['issues']]);self.assertTrue(any(p['spec']['id']=='REFRESH_XING_METADATA' and p['status']=='BLOCKED' for p in plan(concat,row)))
            data=bytearray(stream);f=base['frames'][2];relative=f['byte_end']-base['facts']['first_audio_offset']-8;data[relative:relative+4]=b'Xing';fake=analyze(write_temp(td,'fake.mp3',data));self.assertNotIn('MPEG_UNEXPECTED_STREAM_HEADER',[i.code for i in fake['issues']])

    def test_degraded_ogg_recognition_uses_later_headers(self):
        cases=((ROOT/'samples/ogg_opus_v26/00_healthy_stereo.opus','OPUS_HEAD_INVALID'),(ROOT/'samples/ogg_vorbis_v31/00_healthy_stereo_44100.ogg','VORBIS_IDENTIFICATION_HEADER_INVALID'))
        with tempfile.TemporaryDirectory() as td:
            for source,code in cases:
                row=identify(write_temp(td,source.name,damage_first_ogg_packet(source)));self.assertTrue(row['supported']);q=row.get('ogg_opus') or row.get('ogg_vorbis');self.assertIn(code,[i.code for i in q['issues']])

    def test_degraded_mp4_and_asf_require_coherent_layout(self):
        with tempfile.TemporaryDirectory() as td:
            mp4=ROOT/'samples/mp4_aac_cp6/00_healthy_aac_lc_44100_stereo.m4a';data=bytearray(mp4.read_bytes());data[4:8]=b'junk';row=identify(write_temp(td,'damaged.m4a',data));self.assertTrue(row['supported']);self.assertIn('MP4_FTYP_MISSING',[i.code for i in row['mp4_aac']['issues']])
            self.assertFalse(identify(write_temp(td,'fake.m4a',b'x'*20+b'moov'+b'y'*20+b'mdat'))['supported'])
            asf=ROOT/'samples/asf_wma_v36/00_healthy_wmav2.wma';data=bytearray(asf.read_bytes());data[0]^=1;row=identify(write_temp(td,'damaged.wma',data));self.assertTrue(row['supported']);self.assertIn('ASF_HEADER_OBJECT_INVALID',[i.code for i in row['asf_wma']['issues']])
            self.assertFalse(identify(write_temp(td,'fake.wma',b'x'*30+bytes.fromhex('a1dcab8c47a9cf118ee400c00c205365')))['supported'])

if __name__=='__main__':unittest.main()
