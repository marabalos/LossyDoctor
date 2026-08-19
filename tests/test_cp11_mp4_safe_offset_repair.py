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
from formats.mp4_aac import analyze


ROOT=Path(__file__).resolve().parents[1];BASE=ROOT/"samples"/"mp4_aac_cp9"
MANIFEST=json.loads((ROOT/"samples"/"mp4_aac_cp11_manifest.json").read_text(encoding="utf-8"));CFG=load_config(ROOT/"config.toml")
FFMPEG=os.environ.get("LOSSYDOCTOR_FFMPEG") or shutil.which("ffmpeg");FFPROBE=os.environ.get("LOSSYDOCTOR_FFPROBE") or shutil.which("ffprobe")


def sha256(path:Path):return hashlib.sha256(path.read_bytes()).hexdigest()


@unittest.skipUnless(FFMPEG and FFPROBE,"ffmpeg/ffprobe required")
class Mp4SafeOffsetRepairCP11(unittest.TestCase):
    def test_first_run_repairs_and_second_run_reuses_without_flac_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            temp=Path(directory);source=temp/MANIFEST["positive_source"];shutil.copy2(BASE/source.name,source);source_hash=sha256(source)
            first=analyze_file(source,CFG,ROOT,FFMPEG,FFPROBE);self.assertEqual((first.run_status,first.final_status),("SUCCESS_WITH_REPAIR",["REPAIRED_SAFE"]))
            self.assertEqual(len(first.repair_execution),1);execution=first.repair_execution[0];self.assertEqual((execution["repair_spec_id"],execution["status"]),(MANIFEST["expected_repair_spec_id"],MANIFEST["expected_first_status"]))
            self.assertEqual(first.lossless_export,[]);self.assertTrue(any(decision.get("code")=="BITSTREAM_REPAIR_PRECEDES_PCM_DERIVATION" for decision in first.policy_decisions))
            output=Path(execution["output_path"]);sidecar=Path(execution["manifest_path"]);self.assertTrue(output.exists() and sidecar.exists());manifest=execution["manifest"]
            self.assertEqual(manifest["aac_access_unit_essence_sha256"],MANIFEST["expected_aac_essence_sha256"]);self.assertFalse(manifest["aac_access_unit_bytes_modified"]);self.assertFalse(manifest["audio_recoding"])
            self.assertEqual(len(manifest["changed_byte_ranges"]),1);self.assertTrue(manifest["verification"]["passed"]);self.assertEqual(manifest["verification"]["presentation_pcm_s32le_sha256"],MANIFEST["expected_pcm_sha256"])
            repaired=analyze(output);self.assertEqual(repaired["issues"],[]);provenance=repaired["facts"]["tracks"][0]["access_unit_provenance"];self.assertTrue(provenance["mapping_complete"] and provenance["all_access_units_hashed"])
            files_after_first={path.name for path in temp.iterdir()};second=analyze_file(source,CFG,ROOT,FFMPEG,FFPROBE)
            self.assertEqual(second.repair_execution[0]["status"],MANIFEST["expected_second_status"]);self.assertEqual(second.lossless_export,[]);self.assertEqual(files_after_first,{path.name for path in temp.iterdir()})
            self.assertEqual(sha256(source),source_hash);self.assertEqual(source_hash,MANIFEST["source_sha256"])

    def test_ambiguous_cases_never_gain_repair_or_recovery_output(self):
        with tempfile.TemporaryDirectory() as directory:
            temp=Path(directory)
            for name in ("02_extra_byte_inside_mdat_ambiguous.m4a","03_sample_sizes_overrun_mdat.m4a"):
                source=temp/name;shutil.copy2(BASE/name,source);before=sha256(source);row=analyze_file(source,CFG,ROOT,FFMPEG,FFPROBE)
                self.assertEqual(row.repair_execution,[],name);self.assertEqual(row.lossless_export,[],name);self.assertEqual(sha256(source),before,name)

    def test_repair_changes_only_the_declared_offset_field(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/MANIFEST["positive_source"];shutil.copy2(BASE/source.name,source);row=analyze_file(source,CFG,ROOT,FFMPEG,FFPROBE);output=Path(row.repair_execution[0]["output_path"])
            original=source.read_bytes();repaired=output.read_bytes();changed=[i for i,(a,b) in enumerate(zip(original,repaired)) if a!=b];entry=row.repair_execution[0]["manifest"]["changed_byte_ranges"][0]
            self.assertTrue(changed);self.assertTrue(all(entry["byte_start"]<=index<entry["byte_end"] for index in changed));self.assertEqual(len(original),len(repaired))


if __name__=="__main__":unittest.main()
