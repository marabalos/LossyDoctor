from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class MillionEntryScanCP44(unittest.TestCase):
    def test_main_records_progress_while_scanning_one_million_non_candidates(self):
        from app.main import main

        total=1_000_000
        class CountingWriter:
            instances=[]
            def __init__(self,path):self.path=path;self.rows=0;self.closed=False;type(self).instances.append(self)
            def write(self,row):self.rows+=1
            def close(self):self.closed=True
        def discover(*args):
            metrics,on_entry=args[5],args[6]
            for number in range(1,total+1):
                metrics['entries_scanned']=number;on_entry(metrics)
            if False:yield None

        with tempfile.TemporaryDirectory() as directory:
            with patch('app.main.recover_interrupted_publications',return_value=[]), \
                 patch('app.main.iter_discover',side_effect=discover), \
                 patch('app.main._create_report_directory',return_value=(Path(directory),'run')), \
                 patch('app.main.JsonLinesWriter',CountingWriter), \
                 patch('sys.stdout',new=io.StringIO()):
                self.assertEqual(main(['C:/virtual']),0)

        self.assertEqual([writer.rows for writer in CountingWriter.instances],[0,102])
        self.assertTrue(all(writer.closed for writer in CountingWriter.instances))


if __name__=='__main__':unittest.main()
