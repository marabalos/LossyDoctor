from __future__ import annotations
import json, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class BootstrapStructureTests(unittest.TestCase):
    def test_single_user_launcher_uses_native_bootstrap(self):
        bat = (ROOT / 'LossyDoctor.bat').read_text(encoding='utf-8', errors='replace').lower()
        self.assertIn('lossydoctorbootstrap.exe', bat)
        self.assertNotIn('powershell', bat)
        self.assertTrue((ROOT / 'LossyDoctorBootstrap.exe').is_file())

    def test_manifest_matches_audioaudit_style_portable_profile(self):
        data = json.loads((ROOT / 'bootstrap_manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(data['schema_version'], 2)
        self.assertEqual(data['policy'], 'validated-recommended-pinned')
        self.assertEqual(data['platform'], 'windows-x86_64')
        self.assertEqual(data['uv']['version'], '0.12.5')
        self.assertEqual(data['python']['managed_by'], 'uv')
        self.assertEqual(data['python']['version'], '3.12.14')
        self.assertEqual(data['ffmpeg']['version'], '9.0.1')
        self.assertEqual(len(data['uv']['sha256']), 64)
        self.assertEqual(len(data['ffmpeg']['sha256']), 64)

    def test_no_legacy_powershell_bootstrap(self):
        self.assertFalse((ROOT / 'bootstrap.ps1').exists())
        self.assertTrue((ROOT / 'bootstrap_src' / 'main.go').exists())

if __name__ == '__main__':
    unittest.main()
