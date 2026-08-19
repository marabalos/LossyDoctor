from __future__ import annotations
import json, os, shutil, tempfile, unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

from app.config import load_config
from app.discovery import discover
from app.utils import sha256_file
from formats.mpeg import parse_header, analyze

class CoreTests(unittest.TestCase):
    def test_config_is_strict(self):
        cfg=load_config(ROOT/'config.toml')
        self.assertEqual(cfg['app']['mode'],'repair_safe_verified')
        self.assertEqual(cfg['lossless_recovery']['flac_bits_per_sample'],32)
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'bad.toml';p.write_text('[app]\nbogus=true\n',encoding='utf-8')
            with self.assertRaises(ValueError):load_config(p)

    def test_basic_mpeg_header(self):
        h=parse_header(bytes.fromhex('fffb9064'))
        self.assertIsNotNone(h);self.assertEqual(h['layer'],3);self.assertEqual(h['sample_rate'],44100);self.assertEqual(h['bitrate_kbps'],128)

    def test_acceptance_fixture_hashes_match_manifest(self):
        m=json.loads((ROOT/'samples/recovery_v05_manifest.json').read_text(encoding='utf-8'))
        for name,case in m['cases'].items():
            self.assertEqual(sha256_file(ROOT/'samples/recovery_v05'/name),case['sha256'],name)

    def test_discovery_ignores_verified_generated_output_by_manifest(self):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td); src=d/'source.mp3';src.write_bytes(b'abc');out=d/'x [recovered-partial-lossless].flac';out.write_bytes(b'def')
            side=Path(str(out)+'.lossydoctor-manifest.json')
            side.write_text(json.dumps({'producer':'LossyDoctor','output_path':str(out),'output_sha256':sha256_file(out),'validation_result':'PASS'}),encoding='utf-8')
            files,_=discover([str(d)],ROOT)
            self.assertIn(src,files);self.assertNotIn(out,files);self.assertNotIn(side,files)

    def test_known_gap_parser_tracks_timeline_and_clean_suffix(self):
        r=analyze(ROOT/'samples/recovery_v05/01_unplayable_known_gap.mp3')
        self.assertTrue(r['facts']['canonical_presentation_window']['determined'])
        self.assertEqual(len(r['gaps']),1);self.assertTrue(r['gaps'][0]['timeline_known']);self.assertEqual(r['gaps'][0]['missing_frame_count'],1)
        self.assertTrue(any(f.get('logical_audio_index')==42 and f['clean'] for f in r['frames']))

if __name__=='__main__':unittest.main()

class FreeFormatRegressionTests(unittest.TestCase):
    def test_valid_dominant_layer3_free_format_is_identified(self):
        from formats.identify import identify
        hdr=bytes.fromhex('fffb0064')
        data=b''.join(hdr+bytes(496) for _ in range(64))
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'free.mp3';p.write_bytes(data);q=identify(p)
            self.assertTrue(q['supported']);self.assertEqual(q['codec'],'mp3');self.assertTrue(q['mpeg']['facts']['free_format']);self.assertEqual(q['mpeg']['facts']['free_format_frame_length'],500)
    def test_short_embedded_free_format_chain_is_rejected(self):
        from formats.identify import identify
        hdr=bytes.fromhex('fffb0064');data=bytearray(b'X'*200000)
        for off in (4096,4596,5096,5596):data[off:off+4]=hdr
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'blob.bin';p.write_bytes(data);q=identify(p)
            self.assertFalse(q['supported'])

class NamingAndNoOverwriteTests(unittest.TestCase):
    def test_collision_number_stays_inside_semantic_suffix(self):
        from app.utils import collision_path
        with tempfile.TemporaryDirectory() as td:
            d=Path(td);p=d/'Tema [recovered-partial-lossless].flac';p.write_bytes(b'keep')
            self.assertEqual(collision_path(p).name,'Tema [recovered-partial-lossless 2].flac')
            q=d/'Tema [repaired].mp3';q.write_bytes(b'keep')
            self.assertEqual(collision_path(q).name,'Tema [repaired 2].mp3')

class ExclusivePublishTests(unittest.TestCase):
    def test_exclusive_publish_never_overwrites_existing_output_or_sidecar(self):
        from app.publication import publish_with_manifest
        with tempfile.TemporaryDirectory() as td:
            d=Path(td);src=d/'candidate.bin';src.write_bytes(b'new-data')
            desired=d/'Tema [recovered-partial-lossless].flac';desired.write_bytes(b'keep-output')
            second=d/'Tema [recovered-partial-lossless 2].flac';Path(str(second)+'.lossydoctor-manifest.json').write_text('keep-sidecar',encoding='utf-8')
            actual,sidecar,_=publish_with_manifest(src,desired,{'producer':'LossyDoctor'},d/'state')
            self.assertEqual(desired.read_bytes(),b'keep-output')
            self.assertEqual(Path(str(second)+'.lossydoctor-manifest.json').read_text(encoding="utf-8"), 'keep-sidecar')
            self.assertEqual(actual.name,'Tema [recovered-partial-lossless 3].flac');self.assertEqual(actual.read_bytes(),b'new-data')
            self.assertEqual(sidecar,Path(str(actual)+'.lossydoctor-manifest.json'))
