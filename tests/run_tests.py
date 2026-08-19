from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _declared_fixture_hashes() -> dict[Path, str]:
    declared: dict[Path, str] = {}
    samples = ROOT / "samples"
    for manifest in sorted(samples.glob("*_manifest.json")):
        data = json.loads(manifest.read_text(encoding="utf-8"))
        corpus = data.get("corpus") or manifest.stem.removesuffix("_manifest")
        base = samples / corpus
        for section in ("cases", "files", "mutations"):
            for name, facts in (data.get(section) or {}).items():
                if not isinstance(facts, dict) or not facts.get("sha256"):
                    continue
                path = base / name
                previous = declared.get(path)
                if previous is not None and previous != facts["sha256"]:
                    raise RuntimeError(f"conflicting fixture hashes for {path}")
                declared[path] = facts["sha256"]
    return declared


def _verify_corpus(declared: dict[Path, str]) -> list[str]:
    errors = []
    for path, expected in declared.items():
        if not path.is_file():
            errors.append(f"missing fixture: {path.relative_to(ROOT)}")
            continue
        actual = _sha256(path)
        if actual != expected:
            errors.append(
                f"fixture hash mismatch: {path.relative_to(ROOT)} "
                f"expected={expected} actual={actual}"
            )
    return errors


declared = _declared_fixture_hashes()
before_errors = _verify_corpus(declared)
if before_errors:
    print("Acceptance corpus is not pristine:", file=sys.stderr)
    print("\n".join(before_errors), file=sys.stderr)
    raise SystemExit(1)

with tempfile.TemporaryDirectory(prefix="lossydoctor-test-journal-") as journal_root:
    previous_journal_root = os.environ.get("LOSSYDOCTOR_JOURNAL_ROOT")
    os.environ["LOSSYDOCTOR_JOURNAL_ROOT"] = journal_root
    try:
        suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern="test_*.py")
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    finally:
        if previous_journal_root is None:
            os.environ.pop("LOSSYDOCTOR_JOURNAL_ROOT", None)
        else:
            os.environ["LOSSYDOCTOR_JOURNAL_ROOT"] = previous_journal_root

after_errors = _verify_corpus(declared)
if after_errors:
    print("Acceptance corpus changed during the test run:", file=sys.stderr)
    print("\n".join(after_errors), file=sys.stderr)

raise SystemExit(0 if result.wasSuccessful() and not after_errors else 1)
