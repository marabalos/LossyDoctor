from __future__ import annotations

import json
from pathlib import Path
import re
import tempfile
import unittest
import zipfile

from app.publication import combined_publication_status, publish_or_preview_with_manifest
from app.version import ANALYSIS_SCHEMA, APP_VERSION, CONFIG_SCHEMA, MANIFEST_SCHEMA, POLICY_VERSION, REPORT_SCHEMA
from formats.identify import identify
from release_packaging import CHECKSUM_FILE, ROOT, VERSION, build_zip, git_bytes, release_files


FORBIDDEN_PREFIXES = (
    ".git", "cache/", "docs/", "logs/", "reports/", "runtime/", "samples/",
    "state/", "temp/", "tests/", "toolchain/",
)
FORBIDDEN_NAMES = {".gitattributes", ".gitignore"}
HISTORY = re.compile(r"\bv0\.\d+|\bCP\d+\b|checkpoint", re.IGNORECASE)
AUDIT_ARTIFACTS = re.compile(
    r"@RELEASE_|la autoridad vigente\.0|política vigente evidence|"
    r"modelo vigente authenticates|500[ .]?000|best[- ]effort",
    re.IGNORECASE,
)
HUMAN_ENGLISH = (
    "unsupported content", "read error", "expected boolean",
    "expected positive integer", "canonical pcm materialization mismatch",
    "flac pcm round-trip mismatch", "unparsed bytes in", "structural timeline",
    "decoder sample count matches", "header-derived samples", "seek header",
    "observed lossless kinds", "native clean-region profile", "baked into pcm",
    "crc-authenticated pages", "untimestamped flush frames",
)


class ReleasePackagingTests(unittest.TestCase):
    def test_positive_list_contains_only_distribution_files(self):
        names = release_files()
        self.assertEqual(len(names), len(set(names)))
        self.assertIn("LossyDoctorBootstrap.exe", names)
        self.assertIn("bootstrap_src/main.go", names)
        self.assertIn("V1_BASELINE.md", names)
        self.assertIn(CHECKSUM_FILE, names)
        for name in names:
            self.assertNotIn(name, FORBIDDEN_NAMES)
            self.assertFalse(any(name == p.rstrip("/") or name.startswith(p) for p in FORBIDDEN_PREFIXES), name)
            self.assertNotIn("__pycache__", name)
            self.assertFalse(name.endswith((".pyc", ".pyo")))

    def test_python_allowlist_matches_production_packages(self):
        expected = {
            path.relative_to(ROOT).as_posix()
            for package in ("app", "formats", "policy", "reporting")
            for path in (ROOT / package).glob("*.py")
        }
        actual = {name for name in release_files() if name.endswith(".py") and name != "release_packaging.py"}
        self.assertEqual(actual, expected)

    def test_version_and_policy_surfaces_are_coherent(self):
        manifest = json.loads((ROOT / "bootstrap_manifest.json").read_text(encoding="utf-8"))
        source = (ROOT / "bootstrap_src/main.go").read_text(encoding="utf-8")
        self.assertEqual(APP_VERSION, VERSION)
        self.assertEqual(manifest["lossydoctor_version"], VERSION)
        self.assertIn(f'const bootstrapVersion = "{VERSION}-bootstrap.1"', source)
        self.assertEqual(POLICY_VERSION, "1.1.0-v1-stable-1")
        self.assertEqual((CONFIG_SCHEMA, ANALYSIS_SCHEMA, REPORT_SCHEMA, MANIFEST_SCHEMA), (3, 3, 3, 3))
        for name in ("README.md", "V1_BASELINE.md", "CHANGELOG.md", "ROADMAP.md"):
            self.assertIn(VERSION, (ROOT / name).read_text(encoding="utf-8"), name)

    def test_release_text_has_no_development_history(self):
        for name in release_files():
            if not (ROOT / name).is_file():
                self.assertEqual(name, "V1_BASELINE.md")
                continue
            if Path(name).suffix.lower() not in {".py", ".go", ".json", ".toml", ".txt", ".md", ".bat"}:
                continue
            text = (ROOT / name).read_text(encoding="utf-8", errors="strict")
            self.assertIsNone(HISTORY.search(text), name)
            self.assertIsNone(AUDIT_ARTIFACTS.search(text), name)
            lowered = text.lower()
            for phrase in HUMAN_ENGLISH:
                self.assertNotIn(phrase, lowered, f"{name}: {phrase}")

    def test_release_markdown_links_resolve(self):
        release = set(release_files())
        link = re.compile(r"(?<!!)\[[^]]+\]\(([^)]+)\)")
        for name in sorted(item for item in release if item.endswith(".md")):
            text = (ROOT / name).read_text(encoding="utf-8")
            for target in link.findall(text):
                clean = target.split("#", 1)[0]
                if not clean or "://" in clean or clean.startswith("mailto:"):
                    continue
                resolved = (Path(name).parent / clean).as_posix()
                self.assertIn(resolved, release, f"{name} -> {target}")
                self.assertTrue((ROOT / resolved).is_file(), f"{name} -> {target}")

    def test_mp4_authority_and_adts_hierarchy(self):
        identification = (ROOT / "formats/identify.py").read_text(encoding="utf-8")
        self.assertIn('"container":"MP4","codec":"aac","confidence":"MEDIUM"', identification)
        self.assertNotIn('"container":"MP4","codec":"aac","confidence":"HIGH"', identification)
        source = (ROOT / "app/aac_adts_preservation_hierarchy.py").read_text(encoding="utf-8")
        self.assertNotIn("PARTIAL_LOSSLESS", source)

    def test_preview_is_not_a_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate = root / "candidate.flac"
            candidate.write_bytes(b"pcm")
            output, sidecar, manifest, status = publish_or_preview_with_manifest(
                candidate,
                root / "published.flac",
                {"output_path": str(candidate)},
                False,
                root / "state",
            )
            self.assertEqual(status, "VERIFIED_NOT_PUBLISHED")
            self.assertIsNone(output)
            self.assertIsNone(sidecar)
            self.assertIsNone(manifest["output_path"])
            self.assertNotIn(str(root), json.dumps(manifest))
            self.assertEqual(combined_publication_status([{"status": status}]), status)
            self.assertFalse(status in {"CREATED", "REUSED"})

    def test_zip_exactly_matches_allowlist_and_checksums(self):
        with tempfile.TemporaryDirectory() as directory:
            archive, digest, names = build_zip(Path(directory))
            sidecar = Path(str(archive) + ".sha256")
            self.assertEqual(sidecar.read_text(encoding="ascii"), f"{digest}  {archive.name}\n")
            with zipfile.ZipFile(archive) as package:
                self.assertEqual(tuple(sorted(package.namelist())), names)
                for name in names:
                    self.assertEqual(package.read(name), git_bytes("HEAD", name), name)
                checksums = package.read(CHECKSUM_FILE).decode("utf-8").splitlines()
                self.assertEqual(len(checksums), len(names) - 1)
                for row in checksums:
                    expected, name = row.split("  ", 1)
                    self.assertEqual(hashlib_sha256(package.read(name)), expected, name)


def hashlib_sha256(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()

if __name__ == "__main__":
    unittest.main()
