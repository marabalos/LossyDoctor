from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.models import Analysis


class StreamingCollectionCoreCP37(unittest.TestCase):
    def test_main_writes_each_result_incrementally_and_continues_after_one_failure(self):
        from app.main import main
        with tempfile.TemporaryDirectory() as directory:
            outdir=Path(directory);first=Path("C:/input/first.aac");second=Path("C:/input/second.aac")
            good=Analysis(str(first),first.name,{});good.run_status="SUCCESS";good.final_status=["OK"]
            text=io.StringIO()
            with patch("app.main.recover_interrupted_publications",return_value=[]), \
                 patch("app.main.iter_discover",return_value=iter((first,second))), \
                 patch("app.main._create_report_directory",return_value=(outdir,"run")), \
                 patch("app.main.analyze_file",side_effect=(good,RuntimeError("broken file"))), \
                 patch("sys.stdout",new=text):
                self.assertEqual(main(["C:/input"]),1)
            rows=[json.loads(line) for line in (outdir/"LossyDoctor_run.files.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual([row["run_status"] for row in rows],["SUCCESS","FAILED"])
            final=json.loads((outdir/"LossyDoctor_run.json").read_text(encoding="utf-8"))
            self.assertEqual(final["files"],[]);self.assertEqual(final["file_details_ndjson"],"LossyDoctor_run.files.jsonl")
            self.assertEqual(final["event_log_ndjson"],"LossyDoctor_run.events.jsonl")
            self.assertEqual((final["summary"]["discovered"],final["summary"]["processed"],final["summary"]["failed"]),(2,2,1))
            events=[json.loads(line) for line in (outdir/"LossyDoctor_run.events.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual([event["type"] for event in events],["run_started","file_finished","file_finished","run_finished"])
            index=(outdir/"README.md").read_text(encoding="utf-8");self.assertIn("Detalle por archivo",index);self.assertIn("LossyDoctor_run.files.jsonl",index)
            self.assertIn('procesados=2 hallazgos=0 fallidos=1',text.getvalue())

    def test_main_closes_incremental_streams_after_an_unexpected_collection_error(self):
        from app.main import main
        class TrackedWriter:
            instances=[]
            def __init__(self,path):self.path=path;self.closed=False;type(self).instances.append(self)
            def write(self,row):pass
            def close(self):self.closed=True
        with tempfile.TemporaryDirectory() as directory:
            with patch("app.main.recover_interrupted_publications",return_value=[]), \
                 patch("app.main.iter_discover",side_effect=RuntimeError("unexpected collection error")), \
                 patch("app.main._create_report_directory",return_value=(Path(directory),"run")), \
                 patch("app.main.JsonLinesWriter",TrackedWriter):
                with self.assertRaisesRegex(RuntimeError,"unexpected collection error"):main(["C:/input"])
        self.assertEqual(len(TrackedWriter.instances),2)
        self.assertTrue(all(writer.closed for writer in TrackedWriter.instances))

    def test_main_records_incremental_discovery_progress(self):
        from app.main import main
        with tempfile.TemporaryDirectory() as directory:
            outdir=Path(directory);candidate=Path("C:/input/first.aac")
            def discover(*args):
                args[5]['entries_scanned']=10000;args[6](args[5]);yield candidate
            with patch("app.main.recover_interrupted_publications",return_value=[]), \
                 patch("app.main.iter_discover",side_effect=discover), \
                 patch("app.main._create_report_directory",return_value=(outdir,"run")), \
                 patch("app.main.analyze_file",return_value=Analysis(str(candidate),candidate.name,{})), \
                 patch("sys.stdout",new=io.StringIO()):
                self.assertEqual(main([str(candidate)]),0)
            events=[json.loads(line) for line in (outdir/"LossyDoctor_run.events.jsonl").read_text(encoding="utf-8").splitlines()]
        progress=next(event for event in events if event["type"]=="discovery_progress")
        self.assertEqual((progress["entries_scanned"],progress["candidates_discovered"],progress["processed"],progress["failed"]),(10000,0,0,0))


if __name__=="__main__":unittest.main()
