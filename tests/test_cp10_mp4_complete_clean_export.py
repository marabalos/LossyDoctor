from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from app.config import load_config
from app.external import decode_to_raw_file
from app.pipeline import analyze_file
from reporting.markdown_report import write_md


ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/"samples"/"mp4_aac_cp9"
MANIFEST=json.loads((ROOT/"samples"/"mp4_aac_cp10_manifest.json").read_text(encoding="utf-8"))
CFG=load_config(ROOT/"config.toml");CFG["repair"]["enabled"]=False
FFMPEG=os.environ.get("LOSSYDOCTOR_FFMPEG") or shutil.which("ffmpeg")
FFPROBE=os.environ.get("LOSSYDOCTOR_FFPROBE") or shutil.which("ffprobe")


def sha256(path:Path):return hashlib.sha256(path.read_bytes()).hexdigest()
def outputs(row):return [output for export in row.lossless_export for output in export.get("outputs",[])]


@unittest.skipUnless(FFMPEG and FFPROBE,"ffmpeg/ffprobe required")
class Mp4CompleteCleanExportCP10(unittest.TestCase):
    def test_manifest_reuses_the_cp9_binary_distinct_corpus(self):
        self.assertEqual((MANIFEST["checkpoint"],MANIFEST["authority"]),("CP10","COMPLETE_CLEAN_LOSSLESS_EXPORT"))
        seen=set()
        for name,expected in MANIFEST["cases"].items():
            digest=sha256(BASE/name);self.assertEqual(digest,expected["source_sha256"],name);self.assertNotIn(digest,seen,name);seen.add(digest)
        self.assertEqual(len(seen),5)

    def test_first_run_creates_one_verified_flac_and_second_run_reuses_it(self):
        with tempfile.TemporaryDirectory() as directory:
            temp=Path(directory)
            for name in MANIFEST["cases"]:shutil.copy2(BASE/name,temp/name)
            originals={name:sha256(temp/name) for name in MANIFEST["cases"]}
            first={name:analyze_file(temp/name,CFG,ROOT,FFMPEG,FFPROBE) for name in MANIFEST["cases"]}
            positive=first["01_unique_mdat_wrong_offset_complete_clean.m4a"];created=outputs(positive)
            self.assertEqual((positive.run_status,positive.final_status),("SUCCESS_WITH_RECOVERY",["RECOVERED_LOSSLESS"]));self.assertEqual(len(created),1)
            self.assertEqual(created[0]["status"],"CREATED");manifest=created[0]["manifest"];expected=MANIFEST["cases"]["01_unique_mdat_wrong_offset_complete_clean.m4a"]
            self.assertEqual((manifest["derivation_kind"],manifest["materialization"]),(expected["expected_derivation_kind"],expected["expected_materialization"]))
            self.assertEqual((manifest["sample_count"],manifest["source_canonical_pcm_sha256"]),(expected["expected_sample_count"],expected["expected_pcm_sha256"]))
            self.assertEqual(manifest["source_canonical_pcm_sha256"],manifest["flac_decoded_pcm_sha256"]);self.assertFalse(manifest["aac_access_unit_bytes_modified"])
            self.assertEqual((manifest["resampling"],manifest["channel_remix"]),("NONE","NONE"));self.assertEqual(manifest["synthesized_gap_silence"],[])
            output=Path(created[0]["output_path"]);sidecar=Path(created[0]["manifest_path"]);self.assertTrue(output.exists() and sidecar.exists())
            with tempfile.TemporaryDirectory() as decode_directory:
                raw=Path(decode_directory)/"output.s32le";decoded=decode_to_raw_file(output,raw,FFMPEG,CFG["app"]["external_timeout_seconds"])
                self.assertTrue(decoded["passed"]);self.assertEqual(sha256(raw),expected["expected_pcm_sha256"]);self.assertEqual(raw.stat().st_size,52920*2*4)
            self.assertTrue(all(not outputs(row) for name,row in first.items() if name!="01_unique_mdat_wrong_offset_complete_clean.m4a"))
            self.assertEqual(originals,{name:sha256(temp/name) for name in MANIFEST["cases"]})
            files_after_first={path.name for path in temp.iterdir()}
            second={name:analyze_file(temp/name,CFG,ROOT,FFMPEG,FFPROBE) for name in MANIFEST["cases"]};reused=outputs(second["01_unique_mdat_wrong_offset_complete_clean.m4a"])
            self.assertEqual(len(reused),1);self.assertEqual(reused[0]["status"],"REUSED");self.assertEqual(reused[0]["output_path"],str(output))
            self.assertTrue(all(not outputs(row) for name,row in second.items() if name!="01_unique_mdat_wrong_offset_complete_clean.m4a"))
            self.assertEqual(files_after_first,{path.name for path in temp.iterdir()});self.assertEqual(originals,{name:sha256(temp/name) for name in MANIFEST["cases"]})

    def test_existing_unrelated_target_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            temp=Path(directory);source=temp/"01_unique_mdat_wrong_offset_complete_clean.m4a";shutil.copy2(BASE/source.name,source)
            desired=temp/"01_unique_mdat_wrong_offset_complete_clean [recovered-lossless].flac";sentinel=b"UNRELATED EXISTING FILE";desired.write_bytes(sentinel)
            row=analyze_file(source,CFG,ROOT,FFMPEG,FFPROBE);created=outputs(row)
            self.assertEqual(len(created),1);self.assertEqual(desired.read_bytes(),sentinel);self.assertNotEqual(Path(created[0]["output_path"]),desired)
            self.assertEqual(Path(created[0]["output_path"]).name,"01_unique_mdat_wrong_offset_complete_clean [recovered-lossless 2].flac")

    def test_markdown_exposes_lossless_result_and_byte_preservation(self):
        with tempfile.TemporaryDirectory() as directory:
            temp=Path(directory);source=temp/"source.m4a";shutil.copy2(BASE/"01_unique_mdat_wrong_offset_complete_clean.m4a",source)
            row=analyze_file(source,CFG,ROOT,FFMPEG,FFPROBE).to_dict();run={"run_id":"cp10","started_at":"2026-08-18T00:00:00-03:00","summary":{"discovered":1,"processed":1,"ok":0,"with_findings":0,"skipped":0,"failed":0,"repaired_outputs_created":0,"lossless_outputs_created":1,"outputs_reused":0,"candidates_rejected":0},"files":[row]}
            report=temp/"report.md";write_md(report,run);text=report.read_text(encoding="utf-8")
            self.assertIn("Exportación de preservación sin pérdida",text);self.assertIn("Estado de exportación: `CREATED`",text);self.assertIn("`RECOVERED_LOSSLESS`",text)
            self.assertIn("MP4_AAC_CANONICAL_PRESENTATION_FROM_BYTE_PRESERVED_ACCESS_UNITS",text)
            self.assertIn("SHA-256 PCM canónico fuente/recuperado: `09b85e",text);self.assertIn("MP4_AAC_COMPLETE_CLEAN_EXPORT_AUTHORITY",text)


if __name__=="__main__":unittest.main()
