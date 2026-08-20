from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from app.aac_adts_repair import INTERFRAME_RECAPTURE_SPEC_ID
from app.aac_adts_preservation_hierarchy import ORDER
from app.config import load_config
from app.pipeline import analyze_file


ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/"samples"/"aac_adts_v43"
CRC_BASE=ROOT/"samples"/"aac_adts_crc_v44"
CFG=load_config(ROOT/"config.toml")
FFMPEG=os.environ.get("LOSSYDOCTOR_FFMPEG") or shutil.which("ffmpeg")
FFPROBE=os.environ.get("LOSSYDOCTOR_FFPROBE") or shutil.which("ffprobe")


def sha(path:Path):return hashlib.sha256(path.read_bytes()).hexdigest()


@unittest.skipUnless(FFMPEG and FFPROBE,"ffmpeg/ffprobe required")
class AacAdtsInterframeRecaptureCP33(unittest.TestCase):
    def test_parser_gap_does_not_authorize_destructive_recapture(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/"damaged.aac";shutil.copy2(BASE/"06_interframe_sync_gap.aac",source);before=sha(source)
            first=analyze_file(source,CFG,ROOT,FFMPEG,FFPROBE)
            self.assertFalse(any(x.get("repair_spec_id")==INTERFRAME_RECAPTURE_SPEC_ID for x in first.repair_execution))
            self.assertEqual(sha(source),before);self.assertIn("AAC_ADTS_SYNC_LOSS",[x.code for x in first.issues])

    def test_existing_output_is_preserved_and_numbered(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/"damaged.aac";shutil.copy2(BASE/"06_interframe_sync_gap.aac",source);target=Path(directory)/"damaged [repaired].aac";target.write_bytes(b"KEEP")
            row=analyze_file(source,CFG,ROOT,FFMPEG,FFPROBE)
            self.assertEqual(target.read_bytes(),b"KEEP");self.assertFalse(any(x.get("repair_spec_id")==INTERFRAME_RECAPTURE_SPEC_ID for x in row.repair_execution))

    def test_crc_protected_or_non_gap_damage_remains_blocked(self):
        cases=(CRC_BASE/"06_protected_interframe_sync_gap.aac",BASE/"03_invalid_frame_length.aac",BASE/"04_truncated_final_frame.aac",BASE/"05_midstream_parameter_change.aac")
        for source in cases:
            row=analyze_file(source,CFG,ROOT,FFMPEG,FFPROBE)
            self.assertFalse(any(x.get("repair_spec_id")==INTERFRAME_RECAPTURE_SPEC_ID for x in row.repair_execution),source.name)


if __name__=="__main__":unittest.main()
