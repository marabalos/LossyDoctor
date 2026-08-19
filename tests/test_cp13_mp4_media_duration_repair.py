from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from app.config import load_config
from app.mp4_aac_preservation_hierarchy import ORDER
from app.mp4_aac_repair import DURATION_SPEC_ID
from app.pipeline import analyze_file
from formats.mp4_aac import analyze
from reporting.markdown_report import write_md


ROOT=Path(__file__).resolve().parents[1];BASE=ROOT/"samples"/"mp4_aac_cp6"
MANIFEST=json.loads((ROOT/"samples"/"mp4_aac_cp13_manifest.json").read_text(encoding="utf-8"));CFG=load_config(ROOT/"config.toml")
FFMPEG=os.environ.get("LOSSYDOCTOR_FFMPEG") or shutil.which("ffmpeg");FFPROBE=os.environ.get("LOSSYDOCTOR_FFPROBE") or shutil.which("ffprobe")


def sha256(path:Path):return hashlib.sha256(path.read_bytes()).hexdigest()


@unittest.skipUnless(FFMPEG and FFPROBE,"ffmpeg/ffprobe required")
class Mp4MediaDurationRepairCP13(unittest.TestCase):
    def test_first_run_repairs_and_second_run_reuses_the_healthy_control(self):
        name=next(iter(MANIFEST["cases"]));expected=MANIFEST["cases"][name]
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/name;shutil.copy2(BASE/name,source);source_hash=sha256(source)
            first=analyze_file(source,CFG,ROOT,FFMPEG,FFPROBE);execution=first.repair_execution[0];manifest=execution["manifest"]
            self.assertEqual((first.run_status,first.final_status),("SUCCESS_WITH_REPAIR",["REPAIRED_SAFE"]));self.assertEqual(first.lossless_export,[])
            self.assertEqual((execution["repair_spec_id"],execution["status"]),(DURATION_SPEC_ID,"CREATED"));self.assertEqual(sha256(Path(execution["output_path"])),expected["expected_output_sha256"])
            self.assertEqual(expected["expected_output_sha256"],sha256(BASE/MANIFEST["control"]));self.assertEqual(manifest["aac_access_unit_essence_sha256"],expected["expected_aac_essence_sha256"])
            self.assertEqual(manifest["verification"]["presentation_pcm_s32le_sha256"],expected["expected_pcm_sha256"]);self.assertFalse(manifest["aac_access_unit_bytes_modified"]);self.assertFalse(manifest["audio_recoding"])
            self.assertEqual(first.format_facts["mp4_aac_preservation_hierarchy"]["selected_tier"],ORDER[0]);self.assertTrue(any(x.get("code")=="MP4_AAC_MEDIA_DURATION_REPAIR_AUTHORITY" for x in first.policy_decisions))
            files={path.name for path in source.parent.iterdir()};second=analyze_file(source,CFG,ROOT,FFMPEG,FFPROBE)
            self.assertEqual(second.repair_execution[0]["status"],"REUSED");self.assertEqual(files,{path.name for path in source.parent.iterdir()});self.assertEqual(sha256(source),source_hash)

    def test_repair_changes_only_mdhd_duration_and_rescans_clean(self):
        name=next(iter(MANIFEST["cases"]));expected=MANIFEST["cases"][name]
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/name;shutil.copy2(BASE/name,source);row=analyze_file(source,CFG,ROOT,FFMPEG,FFPROBE);execution=row.repair_execution[0];output=Path(execution["output_path"]);changed=execution["manifest"]["changed_byte_ranges"]
            self.assertEqual(len(changed),1);self.assertEqual(changed[0]["field"],"mdhd duration")
            original=source.read_bytes();repaired=output.read_bytes();diff=[index for index,(a,b) in enumerate(zip(original,repaired)) if a!=b]
            self.assertTrue(diff);self.assertTrue(all(changed[0]["byte_start"]<=index<changed[0]["byte_end"] for index in diff));self.assertEqual(len(original),len(repaired))
            parsed=analyze(output);self.assertEqual(parsed["issues"],[]);track=parsed["facts"]["tracks"][0]
            self.assertEqual(track["media_duration"],track["sample_tables"]["stts"]["duration_units"]);self.assertEqual((execution["manifest"]["original_media_duration"],execution["manifest"]["replacement_media_duration"]),(expected["original_duration"],expected["expected_replacement_duration"]))

    def test_other_cp6_domains_never_gain_duration_repair_authority(self):
        with tempfile.TemporaryDirectory() as directory:
            temp=Path(directory)
            for name in ("00_healthy_aac_lc_44100_stereo.m4a","02_stsz_sample_count_mismatch.m4a","03_chunk_offset_outside_mdat.m4a","05_trailing_unknown_bytes.m4a"):
                source=temp/name;shutil.copy2(BASE/name,source);row=analyze_file(source,CFG,ROOT,FFMPEG,FFPROBE)
                self.assertFalse(any(x.get("repair_spec_id")==DURATION_SPEC_ID for x in row.repair_execution),name)
            wrong_extension=temp/"duration.mp3";shutil.copy2(BASE/"04_media_duration_mismatch.m4a",wrong_extension);row=analyze_file(wrong_extension,CFG,ROOT,FFMPEG,FFPROBE)
            self.assertFalse(any(x.get("repair_spec_id")==DURATION_SPEC_ID for x in row.repair_execution))

    def test_existing_unrelated_target_is_not_overwritten(self):
        name=next(iter(MANIFEST["cases"]));
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/name;shutil.copy2(BASE/name,source);occupied=source.with_name(source.stem+" [repaired]"+source.suffix);occupied.write_bytes(b"unrelated")
            row=analyze_file(source,CFG,ROOT,FFMPEG,FFPROBE);output=Path(row.repair_execution[0]["output_path"])
            self.assertEqual(occupied.read_bytes(),b"unrelated");self.assertNotEqual(output,occupied);self.assertTrue(output.exists())

    def test_markdown_exposes_duration_repair_authority(self):
        name=next(iter(MANIFEST["cases"]));
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/name;shutil.copy2(BASE/name,source);row=analyze_file(source,CFG,ROOT,FFMPEG,FFPROBE).to_dict();run={"run_id":"cp13","started_at":"2026-08-17T20:00:00-03:00","summary":{"discovered":1,"processed":1,"ok":0,"with_findings":1,"skipped":0,"failed":0,"repaired_outputs_created":1,"lossless_outputs_created":0,"outputs_reused":0,"candidates_rejected":0},"files":[row]}
            report=Path(directory)/"report.md";write_md(report,run);text=report.read_text(encoding="utf-8")
            self.assertIn("MP4_AAC_MEDIA_DURATION_REPAIR_AUTHORITY",text);self.assertIn("duración mdhd",text);self.assertIn("TIER_1_VERIFIED_AAC_BITSTREAM_REPAIR",text)


if __name__=="__main__":unittest.main()
