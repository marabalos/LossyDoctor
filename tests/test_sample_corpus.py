import hashlib,importlib.util,json,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))


def load_runner():
 spec=importlib.util.spec_from_file_location("lossydoctor_run_tests",ROOT/'tests'/'run_tests.py')
 module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
 return module


class SampleCorpusTests(unittest.TestCase):
 def test_global_collector_uses_only_top_level_fixture_declarations(self):
  runner=load_runner()
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);samples=root/'samples';corpus=samples/'fixture_shape';corpus.mkdir(parents=True)
   (samples/'fixture_shape_manifest.json').write_text(json.dumps({
    'corpus':'fixture_shape',
    'master':{'file':'master.bin','sha256':'master-hash'},
    'named':{'source.mp3':{'sha256':'source-hash','evidence':{'pcm_sha256':'not-a-source'}},
             'nested':{'inside.ogg':{'sha256':'not-a-source'}}},
    'evidence':{'pcm_sha256':'not-a-source'},
   }),encoding='utf-8')
   original=runner.ROOT;runner.ROOT=root
   try: declared=runner._declared_fixture_hashes()
   finally: runner.ROOT=original
  self.assertEqual(declared,{corpus/'master.bin':'master-hash',corpus/'source.mp3':'source-hash'})
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
