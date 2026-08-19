from __future__ import annotations

import importlib
import json
import tempfile
import unittest
from pathlib import Path

from app.utils import json_write


ROOT = Path(__file__).resolve().parents[1]
PRODUCT_MODULE_ROOTS = ("app", "formats", "policy", "reporting")


class ModuleHealth(unittest.TestCase):
    def test_all_product_modules_import(self):
        for root in PRODUCT_MODULE_ROOTS:
            for path in sorted((ROOT / root).glob("*.py")):
                if path.name == "__init__.py":
                    continue
                module = ".".join(path.relative_to(ROOT).with_suffix("").parts)
                with self.subTest(module=module):
                    importlib.import_module(module)

    def test_active_json_writer_roundtrips_without_partial_file(self):
        payload = {"app": "LossyDoctor", "text": "preservación", "values": [1, 2]}
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "report.json"
            json_write(path, payload)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), payload)
            self.assertFalse(path.with_suffix(".json.part").exists())


if __name__ == "__main__":
    unittest.main()
