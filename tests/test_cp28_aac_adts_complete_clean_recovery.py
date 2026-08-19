from __future__ import annotations

import copy
import hashlib
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from app.config import load_config
from app.external import decode_to_raw_file
from app.pipeline import analyze_file


ROOT=Path(__file__).resolve().parents[1];SOURCE=ROOT/"samples/aac_adts_v43/02_invalid_sampling_index.aac"
CFG=load_config(ROOT/"config.toml");CFG_NO_REPAIR=copy.deepcopy(CFG);CFG_NO_REPAIR["repair"]["enabled"]=False
FFMPEG=os.environ.get("LOSSYDOCTOR_FFMPEG") or shutil.which("ffmpeg");FFPROBE=os.environ.get("LOSSYDOCTOR_FFPROBE") or shutil.which("ffprobe")


def sha(path:Path):return hashlib.sha256(path.read_bytes()).hexdigest()
def outputs(row):return [output for export in row.lossless_export for output in export.get("outputs",[])]


@unittest.skipUnless(FFMPEG and FFPROBE,"ffmpeg/ffprobe required")
class AacAdtsCompleteCleanRecoveryCP28(unittest.TestCase):
    def test_disabled_repair_creates_verified_flac_then_reuses_it(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/"damaged.aac";shutil.copy2(SOURCE,source);before=sha(source)
            row=analyze_file(source,CFG_NO_REPAIR,ROOT,FFMPEG,FFPROBE);made=outputs(row);self.assertEqual(len(made),1);self.assertEqual(made[0]["status"],"CREATED")
            manifest=made[0]["manifest"];self.assertEqual((manifest["derivation_kind"],manifest["materialization"]),("RECOVERED_LOSSLESS","AAC_ADTS_COMPLETE_CLEAN_FROM_PROVEN_HEADER_REPAIR"))
            self.assertEqual((manifest["sample_count"],manifest["source_canonical_pcm_sha256"]),(54272,"43e71c3fe5190eb12054833988828e63259ba935728b08d08df39008e41691d0"))
            self.assertFalse(manifest["aac_payload_bytes_modified"]);self.assertEqual((manifest["resampling"],manifest["channel_remix"]),("NONE","NONE"));self.assertEqual(sha(source),before)
            raw=Path(directory)/"verified.s32le";self.assertTrue(decode_to_raw_file(Path(made[0]["output_path"]),raw,FFMPEG,300)["passed"]);self.assertEqual(sha(raw),manifest["source_canonical_pcm_sha256"])
            files={x.name for x in Path(directory).iterdir()};again=outputs(analyze_file(source,CFG_NO_REPAIR,ROOT,FFMPEG,FFPROBE));self.assertEqual(len(again),1);self.assertEqual(again[0]["status"],"REUSED")
            self.assertEqual(files,{x.name for x in Path(directory).iterdir()});self.assertEqual(sha(source),before)

    def test_repaired_adts_copy_precedes_and_suppresses_flac(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/"damaged.aac";shutil.copy2(SOURCE,source);row=analyze_file(source,CFG,ROOT,FFMPEG,FFPROBE)
            self.assertTrue(any(x.get("status")=="CREATED" for x in row.repair_execution));self.assertEqual(outputs(row),[])
            self.assertTrue(any(x.get("code")=="AAC_ADTS_REPAIR_PRECEDES_PCM" for x in row.policy_decisions))

    def test_existing_flac_is_preserved_and_numbered(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/"damaged.aac";shutil.copy2(SOURCE,source);target=Path(directory)/"damaged [recovered-lossless].flac";sentinel=b"KEEP";target.write_bytes(sentinel)
            made=outputs(analyze_file(source,CFG_NO_REPAIR,ROOT,FFMPEG,FFPROBE));self.assertEqual(target.read_bytes(),sentinel);self.assertEqual(Path(made[0]["output_path"]).name,"damaged [recovered-lossless 2].flac")

    def test_unproven_damage_never_creates_recovery(self):
        for name in ("03_invalid_frame_length.aac","04_truncated_final_frame.aac","05_midstream_parameter_change.aac","06_interframe_sync_gap.aac"):
            row=analyze_file(ROOT/"samples/aac_adts_v43"/name,CFG_NO_REPAIR,ROOT,FFMPEG,FFPROBE);self.assertEqual(outputs(row),[],name);self.assertFalse(row.format_facts["adts_recovery_assessment"]["eligible"],name)


if __name__=="__main__":unittest.main()
