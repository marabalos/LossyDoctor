from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from app.config import load_config
from app.pipeline import analyze_file
from formats.identify import identify
from formats.mp4_aac import analyze


ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/"samples"/"mp4_aac_cp24"
MANIFEST=json.loads((ROOT/"samples"/"mp4_aac_cp24_manifest.json").read_text(encoding="utf-8"))
CFG=load_config(ROOT/"config.toml")
FFMPEG=os.environ.get("LOSSYDOCTOR_FFMPEG") or shutil.which("ffmpeg")
FFPROBE=os.environ.get("LOSSYDOCTOR_FFPROBE") or shutil.which("ffprobe")


class Mp4MultitrackRejectionCP24(unittest.TestCase):
    def test_fixed_fixture_is_two_authenticated_aac_tracks(self):
        self.assertEqual((MANIFEST["checkpoint"],MANIFEST["authority"]),("CP24","EXPLICIT_UNSUPPORTED_NO_OUTPUT"))
        name,expected=next(iter(MANIFEST["cases"].items()));path=BASE/name
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),expected["sha256"])
        identification=analyze(path)["facts"]["identification"]
        self.assertEqual((identification["audio_track_count"],identification["aac_track_count"]),(2,2));self.assertFalse(identification["supported"])

    def test_fast_identification_states_product_incompatibility_explicitly(self):
        name,expected=next(iter(MANIFEST["cases"].items()));result=identify(BASE/name)
        self.assertFalse(result["supported"]);self.assertEqual(result["container"],"MP4");self.assertEqual(result["confidence"],"HIGH")
        self.assertEqual(result["reason"],expected["skip_reason"]);self.assertIn("varias pistas de audio",result["reason"])

    @unittest.skipUnless(FFMPEG and FFPROBE,"ffmpeg/ffprobe required")
    def test_pipeline_skips_without_hashing_decoding_or_outputs(self):
        name,expected=next(iter(MANIFEST["cases"].items()))
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/name;shutil.copy2(BASE/name,source);before=hashlib.sha256(source.read_bytes()).hexdigest()
            row=analyze_file(source,CFG,ROOT,FFMPEG,FFPROBE)
            self.assertEqual(row.run_status,"SKIPPED_UNSUPPORTED");self.assertEqual(row.skipped_reason,expected["skip_reason"])
            self.assertEqual(row.identity,{});self.assertEqual(row.decode_results,{});self.assertEqual(row.repair_plan,[]);self.assertEqual(row.repair_execution,[]);self.assertEqual(row.lossless_export,[])
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),before);self.assertEqual([x.name for x in Path(directory).iterdir()],[name])


if __name__=="__main__":unittest.main()
