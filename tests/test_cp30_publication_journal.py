from __future__ import annotations

import json
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.publication import (
    PublicationRecoveryError,
    _prepare,
    combined_publication_status,
    publish_or_preview_with_manifest,
    publish_with_manifest,
    recover_interrupted_publications,
)
from app.utils import sha256_file


def _manifest() -> dict:
    return {
        "schema_version": 3,
        "producer": "LossyDoctor",
        "source_path": "original.aac",
        "source_sha256": "1" * 64,
        "source_modified": False,
    }


def _record(journal: Path) -> dict:
    return json.loads(next(journal.glob("publication_*.json")).read_text(encoding="utf-8"))


class PublicationJournalCP30(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def source(self) -> Path:
        source = self.root / "candidate.flac"
        source.write_bytes(b"verified audio")
        return source

    def test_normal_publication_is_complete_and_never_overwrites(self):
        source = self.source()
        desired = self.root / "song [recovered].flac"
        journal = self.root / "state"

        output, sidecar, manifest = publish_with_manifest(source, desired, _manifest(), journal)
        self.assertEqual(output, desired)
        self.assertEqual(output.read_bytes(), source.read_bytes())
        self.assertEqual(json.loads(sidecar.read_text(encoding="utf-8")), manifest)
        self.assertEqual(manifest["output_sha256"], sha256_file(source))
        self.assertEqual(_record(journal)["state"], "COMPLETE")

        output2, _, _ = publish_with_manifest(source, desired, _manifest(), journal)
        self.assertEqual(output2.name, "song [recovered 2].flac")
        self.assertEqual(output.read_bytes(), b"verified audio")

    def test_completed_equivalent_publication_is_reused_after_restart(self):
        source = self.source()
        desired = self.root / "song [recovered-lossless].flac"
        journal = self.root / "state"

        output1, sidecar1, manifest1, status1 = publish_or_preview_with_manifest(source, desired, _manifest(), True, journal)
        output2, sidecar2, manifest2, status2 = publish_or_preview_with_manifest(source, desired, _manifest(), True, journal)

        self.assertEqual((status1, status2), ("CREATED", "REUSED"))
        self.assertEqual((output2, sidecar2, manifest2), (output1, sidecar1, manifest1))
        self.assertEqual(len(list(self.root.glob("song*.flac"))), 1)
        self.assertEqual(len(list(journal.glob("publication_*.json"))), 1)

    def test_verified_preview_has_no_persistent_path_or_created_count(self):
        source = self.source()
        desired = self.root / "song [recovered-lossless].flac"

        output, sidecar, manifest, status = publish_or_preview_with_manifest(
            source, desired, _manifest(), False, self.root / "state"
        )

        self.assertEqual(status, "VERIFIED_NOT_PUBLISHED")
        self.assertIsNone(output)
        self.assertIsNone(sidecar)
        self.assertIsNone(manifest["output_path"])
        self.assertEqual(manifest["publication_status"], "VERIFIED_NOT_PUBLISHED")
        self.assertEqual(manifest["output_sha256"], sha256_file(source))
        self.assertFalse(desired.exists())
        self.assertNotIn(str(self.root), json.dumps(manifest))
        outputs = [{"status": status, "output_path": output, "manifest": manifest}]
        self.assertEqual(combined_publication_status(outputs), "VERIFIED_NOT_PUBLISHED")
        self.assertEqual(sum(item["status"] == "CREATED" for item in outputs), 0)
        self.assertEqual(sum(item["status"] == "REUSED" for item in outputs), 0)

    def test_written_output_is_hash_verified_before_sidecar_publication(self):
        source = self.source()
        desired = self.root / "song [recovered-lossless].flac"
        sidecar = Path(str(desired) + ".lossydoctor-manifest.json")
        journal = self.root / "state"

        def corrupt_copy(_source: Path, output: Path) -> None:
            output.write_bytes(b"corrupt copy")

        with patch("app.publication._copy_exclusive", side_effect=corrupt_copy):
            with self.assertRaises(PublicationRecoveryError):
                publish_with_manifest(source, desired, _manifest(), journal)

        self.assertEqual(desired.read_bytes(), b"corrupt copy")
        self.assertFalse(sidecar.exists())
        record = _record(journal)
        self.assertEqual(record["state"], "RECOVERY_BLOCKED")
        self.assertIn("no coincide", record["recovery_error"])

    def test_changed_publication_contract_is_not_silently_reused(self):
        source = self.source()
        desired = self.root / "song [recovered-lossless].flac"
        journal = self.root / "state"
        publish_or_preview_with_manifest(source, desired, _manifest(), True, journal)
        changed = {**_manifest(), "materialization": "DIFFERENT_PROVEN_RESULT"}

        output, _, _, status = publish_or_preview_with_manifest(source, desired, changed, True, journal)

        self.assertEqual(status, "CREATED")
        self.assertEqual(output.name, "song [recovered-lossless 2].flac")

    def test_combined_status_exposes_partial_batch_restart(self):
        self.assertEqual(combined_publication_status([{"status": "REUSED"}, {"status": "CREATED"}]), "MIXED_CREATED_REUSED")

    def test_recovery_with_no_output_records_interruption_and_continues(self):
        source = self.source()
        journal = self.root / "state"
        _prepare(source, self.root / "future.flac", _manifest(), journal)

        events = recover_interrupted_publications(journal)
        self.assertEqual(events, [{"result": "INTERRUPTED_BEFORE_OUTPUT", "output_path": str(self.root / "future.flac")}])
        self.assertEqual(_record(journal)["state"], "INTERRUPTED_BEFORE_OUTPUT")

    def test_recovery_completes_missing_sidecar_only_for_matching_output(self):
        source = self.source()
        output = self.root / "published.flac"
        journal = self.root / "state"
        _, prepared = _prepare(source, output, _manifest(), journal)
        output.write_bytes(source.read_bytes())

        events = recover_interrupted_publications(journal)
        sidecar = Path(str(output) + ".lossydoctor-manifest.json")
        self.assertEqual(events[0]["result"], "SIDECAR_COMPLETED")
        self.assertEqual(json.loads(sidecar.read_text(encoding="utf-8")), prepared["manifest"])
        self.assertEqual(_record(journal)["state"], "RECOVERED_COMPLETE")

    def test_recovery_recognizes_matching_complete_pair(self):
        source = self.source()
        output = self.root / "published.flac"
        journal = self.root / "state"
        _, prepared = _prepare(source, output, _manifest(), journal)
        output.write_bytes(source.read_bytes())
        Path(prepared["sidecar_path"]).write_text(json.dumps(prepared["manifest"]), encoding="utf-8")

        events = recover_interrupted_publications(journal)
        self.assertEqual(events[0]["result"], "PUBLICATION_ALREADY_COMPLETE")
        self.assertEqual(_record(journal)["state"], "RECOVERED_COMPLETE")

    def test_recovery_fails_closed_without_changing_product_files(self):
        for case in ("wrong_output", "sidecar_only", "wrong_sidecar"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                source = root / "candidate.flac"
                source.write_bytes(b"verified audio")
                output = root / "published.flac"
                sidecar = Path(str(output) + ".lossydoctor-manifest.json")
                journal = root / "state"
                _prepare(source, output, _manifest(), journal)
                if case != "sidecar_only":
                    output.write_bytes(b"different" if case == "wrong_output" else source.read_bytes())
                if case in {"sidecar_only", "wrong_sidecar"}:
                    sidecar.write_text('{"unexpected": true}', encoding="utf-8")
                before_output = output.read_bytes() if output.exists() else None
                before_sidecar = sidecar.read_bytes() if sidecar.exists() else None

                with self.assertRaises(PublicationRecoveryError):
                    recover_interrupted_publications(journal)

                self.assertEqual(output.read_bytes() if output.exists() else None, before_output)
                self.assertEqual(sidecar.read_bytes() if sidecar.exists() else None, before_sidecar)
                self.assertEqual(_record(journal)["state"], "RECOVERY_BLOCKED")

    def test_normal_startup_runs_recovery_without_a_special_user_mode(self):
        from app.main import main

        with patch("app.main.recover_interrupted_publications", return_value=[]) as recovery:
            with patch("sys.stdout", new=io.StringIO()):
                self.assertEqual(main([]), 2)
        recovery.assert_called_once()

    def test_ambiguous_startup_recovery_stops_before_processing(self):
        from app.main import main

        output = io.StringIO()
        with patch("app.main.recover_interrupted_publications", side_effect=PublicationRecoveryError("ambiguous")):
            with patch("sys.stdout", new=output):
                self.assertEqual(main(["does-not-matter.aac"]), 1)
        self.assertIn("recuperación de publicación bloqueada", output.getvalue())


if __name__ == "__main__":
    unittest.main()
