from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from app.config import load_config
from app.pipeline import analyze_file


ROOT=Path(__file__).resolve().parents[1];BASE=ROOT/"samples"/"aac_adts_v43";CFG=load_config(ROOT/"config.toml")
FFMPEG=os.environ.get("LOSSYDOCTOR_FFMPEG") or shutil.which("ffmpeg");FFPROBE=os.environ.get("LOSSYDOCTOR_FFPROBE") or shutil.which("ffprobe")
SPEC="AAC_ADTS_REWRITE_UNIQUE_INVALID_SAMPLING_INDEX"


def sha(path:Path):return hashlib.sha256(path.read_bytes()).hexdigest()


@unittest.skipUnless(FFMPEG and FFPROBE,"ffmpeg/ffprobe required")
class AacAdtsSafeHeaderRepairCP27(unittest.TestCase):
    def test_one_header_field_is_repaired_to_exact_healthy_control_and_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/"damaged.aac";shutil.copy2(BASE/"02_invalid_sampling_index.aac",source);before=sha(source)
            row=analyze_file(source,CFG,ROOT,FFMPEG,FFPROBE);made=[x for x in row.repair_execution if x.get("repair_spec_id")==SPEC]
            self.assertEqual(len(made),1);self.assertEqual(made[0]["status"],"CREATED");output=Path(made[0]["output_path"]);manifest=made[0]["manifest"]
            self.assertEqual(sha(output),sha(BASE/"00_healthy_aac_lc_44100_stereo.aac"));self.assertEqual(sha(source),before)
            diffs=[i for i,(a,b) in enumerate(zip(source.read_bytes(),output.read_bytes())) if a!=b];self.assertEqual(diffs,[3755])
            self.assertFalse(manifest["aac_payload_bytes_modified"]);self.assertFalse(manifest["audio_recoding"]);self.assertEqual(manifest["validation_result"],"PASS")
            files={x.name for x in Path(directory).iterdir()};again=analyze_file(source,CFG,ROOT,FFMPEG,FFPROBE);reused=[x for x in again.repair_execution if x.get("repair_spec_id")==SPEC]
            self.assertEqual(len(reused),1);self.assertEqual(reused[0]["status"],"REUSED");self.assertEqual(files,{x.name for x in Path(directory).iterdir()})

    def test_existing_target_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/"damaged.aac";shutil.copy2(BASE/"02_invalid_sampling_index.aac",source);target=Path(directory)/"damaged [repaired].aac";sentinel=b"KEEP";target.write_bytes(sentinel)
            row=analyze_file(source,CFG,ROOT,FFMPEG,FFPROBE);made=next(x for x in row.repair_execution if x.get("repair_spec_id")==SPEC)
            self.assertEqual(target.read_bytes(),sentinel);self.assertEqual(Path(made["output_path"]).name,"damaged [repaired 2].aac")

    def test_ambiguous_or_different_damage_gains_no_repair(self):
        for name in ("03_invalid_frame_length.aac","04_truncated_final_frame.aac","05_midstream_parameter_change.aac","06_interframe_sync_gap.aac"):
            row=analyze_file(BASE/name,CFG,ROOT,FFMPEG,FFPROBE);self.assertFalse(any(x.get("repair_spec_id")==SPEC for x in row.repair_execution),name)


if __name__=="__main__":unittest.main()
