import tempfile,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from formats.identify import identify


def box(kind,payload=b''):
 return (len(payload)+8).to_bytes(4,'big')+kind+payload


def descriptor(tag,payload):
 assert len(payload)<128
 return bytes([tag,len(payload)])+payload


def mp4_audio(sample_kind=b'mp4a',object_type=0x40,handler=b'soun',extra_audio_object_type=None):
 def track(kind,oti,track_handler):
  asc=descriptor(0x05,b'\x12\x10')
  decoder=descriptor(0x04,bytes([oti,0x15])+b'\0'*11+asc)
  es=descriptor(0x03,b'\0\x01\0'+decoder)
  esds=box(b'esds',b'\0'*4+es)
  sample=box(kind,b'\0'*28+esds)
  stsd=box(b'stsd',b'\0'*4+(1).to_bytes(4,'big')+sample)
  stbl=box(b'stbl',stsd);minf=box(b'minf',stbl)
  hdlr=box(b'hdlr',b'\0'*8+track_handler+b'\0'*12)
  return box(b'trak',box(b'mdia',hdlr+minf))
 tracks=track(sample_kind,object_type,handler)
 if extra_audio_object_type is not None:tracks+=track(b'mp4a',extra_audio_object_type,b'soun')
 moov=box(b'moov',tracks)
 ftyp=box(b'ftyp',b'M4A '+b'\0'*4+b'M4A isom')
 return ftyp+moov


class IdentificationHardening(unittest.TestCase):
 def test_strong_jpeg_magic_blocks_embedded_mpeg(self):
  hdr=bytes.fromhex('ffff0064');data=bytearray(b'\xff\xd8\xff\xe0'+b'JUNK'*(70000));data=data[:270999]
  for off in (12148,12176,12204,12232):data[off:off+4]=hdr
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)/'Afiche.jpg';p.write_bytes(data);r=identify(p);self.assertFalse(r['supported']);self.assertIn('JPEG',r['reason'])
 def test_common_non_audio_magics(self):
  for b,n in [(b'\x89PNG\r\n\x1a\n','PNG'),(b'%PDF-1.7','PDF'),(b'PK\x03\x04','ZIP'),(b'MZxx','PE')]:
   with self.subTest(magic=n),tempfile.TemporaryDirectory() as td:
    p=Path(td)/'x.bin';p.write_bytes(b);r=identify(p);self.assertFalse(r['supported']);self.assertIn(n,r['reason'])
 def test_unknown_ogg_codec_is_not_supported(self):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)/'unknown.ogg';p.write_bytes(b'OggS'+b'\0'*32);r=identify(p);self.assertFalse(r['supported']);self.assertEqual(r['container'],'OGG')
 def test_ogg_signature_and_unframed_codec_marker_are_not_enough(self):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)/'fake.opus';p.write_bytes(b'OggS'+b'\0'*32+b'OpusHead');r=identify(p);self.assertFalse(r['supported'])
 def test_ftyp_alone_does_not_claim_aac(self):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)/'empty.m4a';p.write_bytes(box(b'ftyp',b'isom'+b'\0'*8));r=identify(p);self.assertFalse(r['supported']);self.assertEqual(r['container'],'MP4')
 def test_mp4a_label_with_non_aac_object_type_is_not_aac(self):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)/'mpeg_audio.m4a';p.write_bytes(mp4_audio(object_type=0x6b));r=identify(p);self.assertFalse(r['supported'])
 def test_mixed_aac_and_unconfirmed_audio_tracks_fail_closed(self):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)/'mixed.m4a';p.write_bytes(mp4_audio(extra_audio_object_type=0x6b));r=identify(p);self.assertFalse(r['supported'])
 def test_multiple_aac_audio_tracks_fail_closed_until_every_track_is_audited(self):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)/'multi_aac.m4a';p.write_bytes(mp4_audio(extra_audio_object_type=0x40));r=identify(p);self.assertFalse(r['supported']);self.assertEqual(r['container'],'MP4')
 def test_structurally_confirmed_mp4_aac_audio_track_is_supported(self):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)/'aac.m4a';p.write_bytes(mp4_audio());r=identify(p);self.assertTrue(r['supported']);self.assertEqual((r['container'],r['codec'],r['confidence']),('MP4','aac','MEDIUM'));self.assertEqual(r['mp4_aac_identification']['audio_track_count'],1)
