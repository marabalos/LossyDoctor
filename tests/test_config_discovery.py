import tempfile,unittest,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.config import load_config
from app.discovery import discover
class ConfigDiscovery(unittest.TestCase):
 def test_config(self):self.assertEqual(load_config(ROOT/'config.toml')['app']['mode'],'repair_safe_verified')
 def test_unknown_key_rejected(self):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)/'x.toml';p.write_text('[app]\nmode="audit_only"\nbogus=true\n')
   with self.assertRaises(ValueError):load_config(p)
 def test_dedup(self):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)/'x';p.write_bytes(b'x');f,_=discover([str(p),str(p)],ROOT);self.assertEqual(len(f),1)
 def test_application_state_directories_are_not_descended_when_scanning_the_app_root(self):
  with tempfile.TemporaryDirectory() as td:
   app=Path(td);(app/'runtime'/'deep').mkdir(parents=True);(app/'cache'/'deep').mkdir(parents=True);(app/'.git'/'objects').mkdir(parents=True);(app/'runtime'/'deep'/'ignored.mp3').write_bytes(b'x');(app/'cache'/'deep'/'ignored.aac').write_bytes(b'x');(app/'.git'/'objects'/'ignored.ogg').write_bytes(b'x');(app/'kept.mp3').write_bytes(b'x')
   found,skipped=discover([str(app)],app)
   self.assertEqual([p.name for p in found],['kept.mp3']);self.assertEqual(skipped,[])
