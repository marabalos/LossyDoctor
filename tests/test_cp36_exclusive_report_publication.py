from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.main import _create_report_directory
from reporting.json_report import write_json_report


class ExclusiveReportPublicationCP36(unittest.TestCase):
    def test_colliding_run_directory_is_numbered_and_existing_content_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/"reports";existing=root/"20260818_010203_000000-0300";existing.mkdir(parents=True);sentinel=existing/"keep.txt";sentinel.write_text("KEEP",encoding="utf-8")
            created,actual=_create_report_directory(root,"20260818_010203_000000-0300")
            self.assertEqual((created.name,actual),("20260818_010203_000000-0300 2","20260818_010203_000000-0300 2"));self.assertEqual(sentinel.read_text(encoding="utf-8"),"KEEP")

    def test_json_report_write_is_exclusive(self):
        run={"run_id":"x","started_at":"now","summary":{"discovered":0,"processed":0,"ok":0,"with_findings":0,"skipped":0,"failed":0,"repaired_outputs_created":0,"lossless_outputs_created":0,"outputs_reused":0,"candidates_rejected":0},"files":[]}
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);json_path=root/"report.json";json_path.write_text("KEEP",encoding="utf-8")
            with self.assertRaises(FileExistsError):write_json_report(json_path,run)
            self.assertEqual(json_path.read_text(encoding="utf-8"),"KEEP")


if __name__=="__main__":unittest.main()
