import hashlib,json,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
class SampleCorpusTests(unittest.TestCase):
 def test_recovery_v03_manifest_hashes(self):
  m=json.loads((ROOT/'samples/recovery_v03_manifest.json').read_text(encoding="utf-8"));d=ROOT/'samples/recovery_v03'
  def H(p):return hashlib.sha256(p.read_bytes()).hexdigest()
  self.assertEqual(H(d/m['master']['file']),m['master']['sha256'])
  for name,info in m['mutations'].items():self.assertEqual(H(d/name),info['sha256'],name)
  self.assertEqual(m['expected_summary'],{'processed':8,'ok':2,'with_findings':6,'failed':0})
 def test_repair_v04_manifest_hashes(self):
  import hashlib,json
  root=ROOT/'samples';m=json.loads((root/'repair_v04_manifest.json').read_text(encoding="utf-8"))
  for name,meta in m['files'].items():
   p=root/'repair_v04'/name;self.assertTrue(p.exists(),name);self.assertEqual(p.stat().st_size,meta['size']);self.assertEqual(hashlib.sha256(p.read_bytes()).hexdigest(),meta['sha256'])
  self.assertEqual(m['expected_first_run']['repaired_outputs_created'],2)

