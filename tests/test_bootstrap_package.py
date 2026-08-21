from __future__ import annotations
import json,re,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

class BootstrapPackageTests(unittest.TestCase):
 def test_native_launcher_and_versions(self):
  from app.version import APP_VERSION
  s=(ROOT/'LossyDoctor.bat').read_text(errors='ignore').lower();self.assertIn('lossydoctorbootstrap.exe',s);self.assertNotIn('powershell',s)
  m=json.loads((ROOT/'bootstrap_manifest.json').read_text(encoding="utf-8"));self.assertEqual(m['lossydoctor_version'],APP_VERSION);self.assertEqual(m['uv']['version'],'0.12.5');self.assertEqual(m['python']['version'],'3.12.14');self.assertEqual(m['ffmpeg']['version'],'9.0.1');self.assertEqual(m['mpg123']['version'],'1.33.7')
  self.assertEqual(m['policy'],'validated-recommended-pinned');self.assertEqual(m['mpg123']['integrity_mode'],'pinned-sha256');self.assertEqual(m['mpg123']['sha256'],'da58591eb08f7b893136cd8950a187a01355dfdfbb545a3ba99342225ce49a72');self.assertEqual(m['mpg123']['binary_sha256'],'6c5d77cda39c033b94c51a374ad553b4d4e6f774d418e24e7780e4bfd49305de')
  src=(ROOT/'bootstrap_src'/'main.go').read_text(encoding="utf-8");self.assertIn(f'const bootstrapVersion = "{APP_VERSION}-bootstrap.1"',src)
 def test_overlay_package_source_does_not_require_persistent_dirs(self):
  self.assertTrue((ROOT/'LossyDoctorBootstrap.exe').exists())

 def test_documentation_version_surfaces_are_coherent(self):
  from app.version import APP_VERSION
  readmes=[(ROOT/name).read_text(encoding='utf-8') for name in ('README.md','README.es.md','README.zh-CN.md','README.ru.md','README.hi.md')]
  self.assertTrue(readmes[0].startswith('# LossyDoctor\n'))
  self.assertTrue(all(APP_VERSION in text for text in readmes))
  self.assertTrue((ROOT/'TESTING.md').read_text(encoding='utf-8').startswith(f'# LossyDoctor {APP_VERSION}'))
  self.assertIn(f'## {APP_VERSION}', (ROOT/'CHANGELOG.md').read_text(encoding='utf-8')[:600])
  self.assertTrue((ROOT/'PRODUCT.md').read_text(encoding='utf-8').startswith('# LossyDoctor'))

 def test_readme_explains_the_bootstrap_connectivity_message(self):
  readmes=[(ROOT/name).read_text(encoding='utf-8') for name in ('README.md','README.es.md','README.zh-CN.md','README.ru.md','README.hi.md')]
  self.assertIn('## Installation and Internet connection',readmes[0])
  self.assertIn('first preparation of LossyDoctor requires an Internet connection',readmes[0])
  self.assertTrue(all('LossyDoctorBootstrap.exe --prepare-only' in text for text in readmes))

 def test_bootstrap_manifest_schema_matches_loader(self):
  m=json.loads((ROOT/'bootstrap_manifest.json').read_text(encoding="utf-8"))
  src=(ROOT/'bootstrap_src'/'main.go').read_text(encoding="utf-8")
  hit=re.search(r'a\.manifest\.SchemaVersion\s*!=\s*(\d+)',src)
  self.assertIsNotNone(hit,'bootstrap loader schema guard not found')
  self.assertEqual(m['schema_version'],int(hit.group(1)),'distributed bootstrap_manifest schema is not accepted by native bootstrap')

 def test_final_mpg123_release_has_no_pending_attestation_mode(self):
  src=(ROOT/'bootstrap_src'/'main.go').read_text(encoding="utf-8"); manifest=(ROOT/'bootstrap_manifest.json').read_text(encoding="utf-8"); docs=(ROOT/'README.md').read_text(encoding="utf-8")+ (ROOT/'TESTING.md').read_text(encoding="utf-8")+ (ROOT/'THIRD_PARTY_NOTICES.md').read_text(encoding="utf-8")
  self.assertNotIn('ATTESTED_TLS_PENDING_PIN',src);self.assertNotIn('PENDING_PIN',src);self.assertNotIn('attest-tls-first-download-pending-pin',manifest)
  self.assertIn('PINNED_SHA256',src);self.assertIn('pinned-sha256',manifest);self.assertIn('PINNED_SHA256',docs)

if __name__=='__main__':unittest.main()
