from __future__ import annotations

import tempfile,unittest
from pathlib import Path

from app.config import DEFAULT,load_config

ROOT=Path(__file__).resolve().parents[1]

class StrictConfigValidationCP17(unittest.TestCase):
    def load_text(self,text):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'config.toml';path.write_text(text,encoding='utf-8');return load_config(path)

    def assert_invalid(self,text,field):
        with self.assertRaisesRegex(ValueError,field.replace('.','\\.')):self.load_text(text)

    def test_shipped_and_absent_config_are_validated_defaults(self):
        self.assertEqual(load_config(ROOT/'config.toml'),DEFAULT)
        with tempfile.TemporaryDirectory() as td:self.assertEqual(load_config(Path(td)/'absent.toml'),DEFAULT)

    def test_boolean_keys_require_actual_toml_booleans(self):
        for section,key in (('app','recursive'),('app','follow_symlinks'),('repair','enabled'),('repair','publish_verified'),('lossless_recovery','enabled'),('lossless_recovery','publish_verified'),('reports','json'),('reports','markdown')):
            for value in ('1','"true"'):
                with self.subTest(section=section,key=key,value=value):self.assert_invalid(f'[{section}]\n{key}={value}\n',f'{section}.{key}')

    def test_positive_integer_keys_reject_bool_zero_negative_and_float(self):
        for section,key in (('app','max_resync_scan_bytes'),('app','external_timeout_seconds'),('analysis','sha256_chunk_size')):
            for value in ('true','0','-1','1.5'):
                with self.subTest(section=section,key=key,value=value):self.assert_invalid(f'[{section}]\n{key}={value}\n',f'{section}.{key}')

    def test_only_implemented_canonical_profiles_are_accepted(self):
        invalid=(('[analysis]\ncanonical_decoder="other"\n','analysis.canonical_decoder'),('[analysis]\ncanonical_pcm_sample_format="f32le"\n','analysis.canonical_pcm_sample_format'),('[lossless_recovery]\nflac_bits_per_sample=24\n','lossless_recovery.flac_bits_per_sample'))
        for text,field in invalid:
            with self.subTest(field=field):self.assert_invalid(text,field)

    def test_report_path_is_portable_and_one_format_remains_enabled(self):
        self.assertEqual(self.load_text('[reports]\nroot="reports/archive"\n')['reports']['root'],'reports/archive')
        for value in ('""','"../outside"','"C:/outside"','"D:outside"'):
            with self.subTest(value=value):self.assert_invalid(f'[reports]\nroot={value}\n','reports.root')
        self.assert_invalid('[reports]\njson=false\nmarkdown=false\n','reports')

    def test_schema_versions_cannot_claim_an_unsupported_contract(self):
        for key in DEFAULT['schemas']:
            with self.subTest(key=key):self.assert_invalid(f'[schemas]\n{key}=999\n',f'schemas.{key}')

    def test_unknown_section_and_key_remain_rejected(self):
        self.assert_invalid('[unknown]\nvalue=true\n','sección desconocida')
        self.assert_invalid('[app]\nunknown=true\n','clave desconocida')

if __name__=='__main__':unittest.main()
