from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class LargeCollectionSupportCP43(unittest.TestCase):
    def test_main_processes_one_hundred_thousand_virtual_candidates_incrementally(self):
        from app.main import main

        total=100_000
        class CountingWriter:
            instances=[]
            def __init__(self,path):self.path=path;self.rows=0;self.closed=False;type(self).instances.append(self)
            def write(self,row):self.rows+=1
            def close(self):self.closed=True
        class Result:
            def __init__(self,path):self.path=path
            def to_dict(self):return {'input_path':str(self.path),'run_status':'SUCCESS','final_status':['OK'],'repair_execution':[],'lossless_export':[]}
        def discover(*args):
            for number in range(total):yield Path(f'C:/virtual/{number}.aac')

        with tempfile.TemporaryDirectory() as directory:
            outdir=Path(directory)
            with patch('app.main.recover_interrupted_publications',return_value=[]), \
                 patch('app.main.iter_discover',side_effect=discover), \
                 patch('app.main._create_report_directory',return_value=(outdir,'run')), \
                 patch('app.main.JsonLinesWriter',CountingWriter), \
                 patch('app.main.analyze_file',side_effect=lambda path,*args:Result(path)), \
                 patch('sys.stdout',new=io.StringIO()):
                self.assertEqual(main(['C:/virtual']),0)
            final=json.loads((outdir/'LossyDoctor_run.json').read_text(encoding='utf-8'))

        self.assertEqual((final['summary']['discovered'],final['summary']['processed'],final['summary']['entries_scanned']),(total,total,total))
        self.assertEqual([writer.rows for writer in CountingWriter.instances],[total,total+2])
        self.assertTrue(all(writer.closed for writer in CountingWriter.instances))


if __name__=='__main__':unittest.main()
