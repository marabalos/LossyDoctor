from __future__ import annotations

import hashlib,json,os,shutil,tempfile,unittest
from pathlib import Path

from app.config import load_config
from app.mp4_aac_preservation_hierarchy import ORDER
from app.mp4_aac_repair import DESCRIPTION_REFERENCE_SPEC_ID
from app.pipeline import analyze_file
from formats.mp4_aac import analyze
from reporting.markdown_report import write_md

ROOT=Path(__file__).resolve().parents[1];BASE=ROOT/"samples"/"mp4_aac_cp9"
MAN=json.loads((ROOT/"samples"/"mp4_aac_cp15_manifest.json").read_text(encoding="utf-8"));CFG=load_config(ROOT/"config.toml")
FFMPEG=os.environ.get("LOSSYDOCTOR_FFMPEG") or shutil.which("ffmpeg");FFPROBE=os.environ.get("LOSSYDOCTOR_FFPROBE") or shutil.which("ffprobe")
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

@unittest.skipUnless(FFMPEG and FFPROBE,"ffmpeg/ffprobe required")
class Mp4DescriptionReferenceRepairCP15(unittest.TestCase):
    def test_created_then_reused_output_equals_the_healthy_control(self):
        name=next(iter(MAN["cases"]));expected=MAN["cases"][name]
        with tempfile.TemporaryDirectory() as td:
            source=Path(td)/name;shutil.copy2(BASE/name,source);source_hash=sha(source);first=analyze_file(source,CFG,ROOT,FFMPEG,FFPROBE);execution=first.repair_execution[0];manifest=execution["manifest"]
            self.assertEqual((first.run_status,first.final_status),("SUCCESS_WITH_REPAIR",["REPAIRED_SAFE"]));self.assertEqual((execution["repair_spec_id"],execution["status"]),(DESCRIPTION_REFERENCE_SPEC_ID,"CREATED"));self.assertEqual(first.lossless_export,[])
            self.assertEqual(sha(execution["output_path"]),expected["expected_output_sha256"]);self.assertEqual(expected["expected_output_sha256"],sha(ROOT/MAN["control"]));self.assertEqual(manifest["aac_access_unit_essence_sha256"],expected["expected_aac_essence_sha256"]);self.assertEqual(manifest["verification"]["presentation_pcm_s32le_sha256"],expected["expected_pcm_sha256"])
            self.assertFalse(manifest["aac_access_unit_bytes_modified"]);self.assertFalse(manifest["audio_recoding"]);self.assertEqual(first.format_facts["mp4_aac_preservation_hierarchy"]["selected_tier"],ORDER[0]);self.assertTrue(any(x.get("code")=="MP4_AAC_SAMPLE_DESCRIPTION_REPAIR_AUTHORITY" for x in first.policy_decisions))
            files={p.name for p in source.parent.iterdir()};second=analyze_file(source,CFG,ROOT,FFMPEG,FFPROBE);self.assertEqual(second.repair_execution[0]["status"],"REUSED");self.assertEqual(files,{p.name for p in source.parent.iterdir()});self.assertEqual(sha(source),source_hash)

    def test_only_description_index_changes_and_full_mapping_rescans_clean(self):
        name=next(iter(MAN["cases"]));expected=MAN["cases"][name]
        with tempfile.TemporaryDirectory() as td:
            source=Path(td)/name;shutil.copy2(BASE/name,source);row=analyze_file(source,CFG,ROOT,FFMPEG,FFPROBE);execution=row.repair_execution[0];output=Path(execution["output_path"]);change=execution["manifest"]["changed_byte_ranges"][0];original=source.read_bytes();repaired=output.read_bytes();diff=[i for i,(a,b) in enumerate(zip(original,repaired)) if a!=b]
            self.assertEqual(change["field"],"stsc sample_description_index");self.assertTrue(diff and all(change["byte_start"]<=i<change["byte_end"] for i in diff));self.assertEqual(len(original),len(repaired));parsed=analyze(output);self.assertEqual(parsed["issues"],[]);self.assertTrue(parsed["facts"]["tracks"][0]["access_unit_provenance"]["mapping_complete"])
            self.assertEqual((execution["manifest"]["original_sample_description_index"],execution["manifest"]["replacement_sample_description_index"]),(expected["original_index"],expected["expected_replacement_index"]))

    def test_other_domains_and_wrong_extension_never_gain_reference_authority(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            for name in ("00_healthy_no_recovery_required.m4a","01_unique_mdat_wrong_offset_complete_clean.m4a","02_extra_byte_inside_mdat_ambiguous.m4a","03_sample_sizes_overrun_mdat.m4a"):
                source=root/name;shutil.copy2(BASE/name,source);row=analyze_file(source,CFG,ROOT,FFMPEG,FFPROBE);self.assertFalse(any(x.get("repair_spec_id")==DESCRIPTION_REFERENCE_SPEC_ID for x in row.repair_execution),name)
            wrong=root/"description.mp3";shutil.copy2(BASE/"04_invalid_sample_description_reference.m4a",wrong);row=analyze_file(wrong,CFG,ROOT,FFMPEG,FFPROBE);self.assertFalse(any(x.get("repair_spec_id")==DESCRIPTION_REFERENCE_SPEC_ID for x in row.repair_execution))

    def test_occupied_target_is_preserved_and_report_exposes_authority(self):
        name=next(iter(MAN["cases"]));
        with tempfile.TemporaryDirectory() as td:
            source=Path(td)/name;shutil.copy2(BASE/name,source);occupied=source.with_name(source.stem+" [repaired]"+source.suffix);occupied.write_bytes(b"unrelated");row=analyze_file(source,CFG,ROOT,FFMPEG,FFPROBE);self.assertEqual(occupied.read_bytes(),b"unrelated");self.assertNotEqual(Path(row.repair_execution[0]["output_path"]),occupied)
            run={"run_id":"cp15","started_at":"2026-08-17T20:00:00-03:00","summary":{"discovered":1,"processed":1,"ok":0,"with_findings":1,"skipped":0,"failed":0,"repaired_outputs_created":1,"lossless_outputs_created":0,"outputs_reused":0,"candidates_rejected":0},"files":[row.to_dict()]};report=Path(td)/"report.md";write_md(report,run);text=report.read_text(encoding="utf-8");self.assertIn("MP4_AAC_SAMPLE_DESCRIPTION_REPAIR_AUTHORITY",text);self.assertIn("referencia stsc",text);self.assertIn(ORDER[0],text)

if __name__=="__main__":unittest.main()
