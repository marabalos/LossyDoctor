from __future__ import annotations

import unittest
from pathlib import Path

from app import pipeline
from formats import identify


ROOT = Path(__file__).resolve().parents[1]
OBSOLETE_IDENTIFICATION_MODULES = (
    "app/detector.py",
    "app/sniff.py",
    "formats/asf.py",
    "formats/mp4.py",
    "formats/ogg.py",
)
OBSOLETE_OPERATIONAL_SCAFFOLDING = (
    "app/artifacts.py",
    "app/cache.py",
    "app/events.py",
    "app/hashing.py",
)


class ArchitectureBoundaries(unittest.TestCase):
    def test_pipeline_uses_the_authoritative_identifier(self):
        self.assertIs(pipeline.identify, identify.identify)

    def test_portable_launcher_enters_through_app_main(self):
        source = (ROOT / "bootstrap_src/main.go").read_text(encoding="utf-8")
        self.assertIn('[]string{"-m", "app.main"', source)

    def test_obsolete_parallel_identification_modules_are_absent(self):
        for relative in OBSOLETE_IDENTIFICATION_MODULES:
            with self.subTest(module=relative):
                self.assertFalse((ROOT / relative).exists())

    def test_dormant_operational_scaffolding_is_not_presented_as_active(self):
        for relative in OBSOLETE_OPERATIONAL_SCAFFOLDING:
            with self.subTest(module=relative):
                self.assertFalse((ROOT / relative).exists())

    def test_all_functional_publications_use_the_transaction_journal(self):
        for path in sorted((ROOT / "app").glob("*.py")):
            if path.name in {"publication.py", "utils.py"}:
                continue
            source = path.read_text(encoding="utf-8")
            with self.subTest(module=path.name):
                self.assertNotIn("publish_exclusive_copy", source)
                self.assertNotIn("json_write_exclusive", source)


if __name__ == "__main__":
    unittest.main()
