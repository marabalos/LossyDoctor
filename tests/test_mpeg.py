import tempfile,unittest,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from formats.identify import identify
from formats.mpeg import parse_header,analyze
class MPEGTests(unittest.TestCase):
 def test_header(self):
  h=parse_header(bytes.fromhex('fffb9064'));self.assertEqual((h['version'],h['layer'],h['bitrate_kbps'],h['sample_rate'],h['frame_length']),(1,3,128,44100,417))
 def test_crc_flag(self):self.assertTrue(parse_header(bytes.fromhex('fffa9064'))['protected_by_crc'])
 def test_free_geometry(self):
  hdr=bytes.fromhex('fffb0064');data=bytearray(2000)
  for off in (0,500,1000,1500):data[off:off+4]=hdr
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)/'x.mp3';p.write_bytes(data);q=analyze(p);self.assertEqual(q['facts']['free_format_frame_length'],500)
 def test_dominant_free_identifies(self):
  hdr=bytes.fromhex('fffb0064');data=b''.join(hdr+bytes(496) for _ in range(64))
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)/'x.mp3';p.write_bytes(data);q=identify(p);self.assertTrue(q['supported']);self.assertTrue(q['mpeg']['facts']['free_format']);self.assertGreater(q['mpeg']['facts']['audio_coverage_ratio'],.99)
 def test_short_embedded_free_rejected(self):
  hdr=bytes.fromhex('fffb0064');data=bytearray(b'X'*200000)
  for off in (4096,4596,5096,5596):data[off:off+4]=hdr
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)/'x.bin';p.write_bytes(data);q=identify(p);self.assertFalse(q['supported'])

class ScopeTests(unittest.TestCase):
 def test_layer1_is_out_of_scope_even_if_dominant(self):
  hdr=bytes.fromhex('ffff0064');data=b''.join(hdr+bytes(24) for _ in range(64))
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)/'x.mp1';p.write_bytes(data);q=identify(p);self.assertFalse(q['supported'])

class SeekMetadataTests(unittest.TestCase):
 def test_xing_toc_summary_is_exposed(self):
  # Synthetic frame with a monotonic Xing TOC.
  from formats.mpeg import _parse_xing
  h=parse_header(bytes.fromhex('fffb9064'));frame=bytearray(h['frame_length']);frame[:4]=bytes.fromhex('fffb9064')
  o=36;frame[o:o+4]=b'Xing';frame[o+4:o+8]=(4).to_bytes(4,'big')
  for i in range(100):frame[o+8+i]=min(255,i*2)
  x=_parse_xing(bytes(frame),h)
  self.assertTrue(x['toc_present']);self.assertEqual(len(x['toc']),100);self.assertEqual(x['toc'],sorted(x['toc']))

class VBRISeekMetadataTests(unittest.TestCase):
 def test_vbri_v1_seek_table_descriptor_is_bounded(self):
  from formats.mpeg import _parse_vbri
  h=parse_header(bytes.fromhex('fffb9064'));frame=bytearray(h['frame_length']);frame[:4]=bytes.fromhex('fffb9064')
  o=36;frame[o:o+4]=b'VBRI';frame[o+4:o+6]=(1).to_bytes(2,'big');frame[o+6:o+8]=(0).to_bytes(2,'big');frame[o+8:o+10]=(50).to_bytes(2,'big')
  frame[o+10:o+14]=(4000).to_bytes(4,'big');frame[o+14:o+18]=(100).to_bytes(4,'big')
  frame[o+18:o+20]=(4).to_bytes(2,'big');frame[o+20:o+22]=(1).to_bytes(2,'big');frame[o+22:o+24]=(2).to_bytes(2,'big');frame[o+24:o+26]=(25).to_bytes(2,'big')
  q=o+26
  for v in (100,110,120,130):frame[q:q+2]=v.to_bytes(2,'big');q+=2
  vb=_parse_vbri(bytes(frame),h)
  self.assertTrue(vb['layout_valid']);self.assertEqual(vb['toc_entries'],4);self.assertEqual(vb['toc_total_bytes'],460)
