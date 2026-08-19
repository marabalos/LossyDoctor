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
    def test_nonframe_gap_is_removed_to_exact_healthy_stream_and_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/"damaged.aac";shutil.copy2(BASE/"06_interframe_sync_gap.aac",source);before=sha(source)
            first=analyze_file(source,CFG,ROOT,FFMPEG,FFPROBE);made=[x for x in first.repair_execution if x.get("repair_spec_id")==INTERFRAME_RECAPTURE_SPEC_ID]
            self.assertEqual(len(made),1);self.assertEqual(made[0]["status"],"CREATED");output=Path(made[0]["output_path"]);manifest=made[0]["manifest"]
            self.assertEqual(sha(output),sha(BASE/"00_healthy_aac_lc_44100_stereo.aac"));self.assertEqual(sha(source),before)
            self.assertEqual(manifest["changed_byte_ranges"],[{"operation":"REMOVE_NONFRAME_BYTES","byte_start":5643,"byte_end":5668,"removed_byte_count":25,"removed_sha256":"ea6e3f9e45d926acc7bc19325606d629a855c0cb2bf1bac63f3e426a549e2345"}])
            self.assertFalse(manifest["aac_frame_bytes_modified"]);self.assertFalse(manifest["aac_payload_bytes_modified"]);self.assertFalse(manifest["audio_recoding"])
            verification=manifest["verification"];self.assertTrue(verification["frame_sequence_sha256_equal"]);self.assertTrue(verification["aac_payload_sequence_sha256_equal"]);self.assertTrue(verification["source_candidate_pcm_equal"])
            self.assertEqual(first.format_facts["aac_adts_preservation_hierarchy"]["selected_tier"],ORDER[0])
            files={path.name for path in source.parent.iterdir()};second=analyze_file(source,CFG,ROOT,FFMPEG,FFPROBE);again=[x for x in second.repair_execution if x.get("repair_spec_id")==INTERFRAME_RECAPTURE_SPEC_ID]
            self.assertEqual(len(again),1);self.assertEqual(again[0]["status"],"REUSED");self.assertEqual(files,{path.name for path in source.parent.iterdir()})

    def test_existing_output_is_preserved_and_numbered(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/"damaged.aac";shutil.copy2(BASE/"06_interframe_sync_gap.aac",source);target=Path(directory)/"damaged [repaired].aac";target.write_bytes(b"KEEP")
            row=analyze_file(source,CFG,ROOT,FFMPEG,FFPROBE);made=next(x for x in row.repair_execution if x.get("repair_spec_id")==INTERFRAME_RECAPTURE_SPEC_ID)
            self.assertEqual(target.read_bytes(),b"KEEP");self.assertEqual(Path(made["output_path"]).name,"damaged [repaired 2].aac")

    def test_crc_protected_or_non_gap_damage_remains_blocked(self):
        cases=(CRC_BASE/"06_protected_interframe_sync_gap.aac",BASE/"03_invalid_frame_length.aac",BASE/"04_truncated_final_frame.aac",BASE/"05_midstream_parameter_change.aac")
        for source in cases:
            row=analyze_file(source,CFG,ROOT,FFMPEG,FFPROBE)
            self.assertFalse(any(x.get("repair_spec_id")==INTERFRAME_RECAPTURE_SPEC_ID for x in row.repair_execution),source.name)


if __name__=="__main__":unittest.main()
