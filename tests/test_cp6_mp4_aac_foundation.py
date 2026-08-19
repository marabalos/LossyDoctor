from __future__ import annotations

import copy
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
from reporting.markdown_report import write_md


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "samples" / "mp4_aac_cp6"
MANIFEST = json.loads((ROOT / "samples" / "mp4_aac_cp6_manifest.json").read_text(encoding="utf-8"))
CFG = load_config(ROOT / "config.toml")
CFG_AUDIT = copy.deepcopy(CFG)
CFG_AUDIT["repair"]["enabled"] = False
CFG_AUDIT["lossless_recovery"]["enabled"] = False
FFMPEG = os.environ.get("LOSSYDOCTOR_FFMPEG") or shutil.which("ffmpeg")
FFPROBE = os.environ.get("LOSSYDOCTOR_FFPROBE") or shutil.which("ffprobe")


def sha256(path:Path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Mp4AacFoundationCP6(unittest.TestCase):
    def test_manifest_hashes_issue_sets_and_binary_distinctness(self):
        self.assertEqual(MANIFEST["checkpoint"],"CP6")
        self.assertEqual(MANIFEST["authority"],"AUDIT_ONLY_NO_REPAIR_OR_RECOVERY")
        seen=set()
        for name,expected in MANIFEST["cases"].items():
            path=BASE/name;digest=sha256(path)
            self.assertEqual(digest,expected["sha256"],name);self.assertNotIn(digest,seen,name);seen.add(digest)
            parsed=analyze(path)
            # Later MP4 checkpoints may add deeper findings while preserving every
            # structural issue that CP6 established as authoritative.
            self.assertTrue(set(expected["expected_issues"]).issubset({issue.code for issue in parsed["issues"]}),name)
        self.assertEqual(len(seen),6)

    def test_healthy_controls_expose_track_aac_and_sample_table_facts(self):
        expected={
            "00_healthy_aac_lc_44100_stereo.m4a":(44100,2,53,53944),
            "01_healthy_aac_lc_48000_mono.m4a":(48000,1,39,39424),
        }
        for name,(sample_rate,channels,samples,duration) in expected.items():
            parsed=analyze(BASE/name);facts=parsed["facts"];track=facts["tracks"][0];description=track["sample_descriptions"][0];tables=track["sample_tables"]
            self.assertEqual(parsed["issues"],[],name)
            self.assertEqual((facts["mp4"]["moov_count"],facts["mp4"]["mdat_count"]),(1,1),name)
            self.assertEqual((track["track_id"],track["handler_type"]),(1,"soun"),name)
            self.assertEqual((description["sample_entry"],description["aac_config"]["profile_name"]),("mp4a","AAC LC"),name)
            self.assertEqual((description["sample_rate"],description["channels"]),(sample_rate,channels),name)
            self.assertEqual((tables["stts"]["sample_count"],tables["stsz"]["sample_count"]),(samples,samples),name)
            self.assertEqual((track["media_duration"],tables["stts"]["duration_units"]),(duration,duration),name)
            self.assertTrue(tables["stco"]["all_offsets_inside_mdat"],name)
            self.assertTrue(track["edit_list_present"],name)

    def test_mutations_isolate_sample_count_chunk_duration_and_trailing_domains(self):
        rows={name:analyze(BASE/name) for name in MANIFEST["cases"]}
        self.assertEqual(rows["02_stsz_sample_count_mismatch.m4a"]["facts"]["tracks"][0]["sample_tables"]["stsz"]["sample_count"],52)
        self.assertFalse(rows["03_chunk_offset_outside_mdat.m4a"]["facts"]["tracks"][0]["sample_tables"]["stco"]["all_offsets_inside_mdat"])
        duration_track=rows["04_media_duration_mismatch.m4a"]["facts"]["tracks"][0]
        self.assertNotEqual(duration_track["media_duration"],duration_track["sample_tables"]["stts"]["duration_units"])
        self.assertEqual(rows["05_trailing_unknown_bytes.m4a"]["structural_map"][-1]["type"],"UNKNOWN_REGION")

    def test_identify_routes_authenticated_aac_track_to_deep_parser(self):
        result=identify(BASE/"00_healthy_aac_lc_44100_stereo.m4a")
        self.assertEqual((result["container"],result["codec"],result["confidence"]),("MP4","aac","MEDIUM"))
        self.assertIn("mp4_aac",result)
        self.assertEqual(result["mp4_aac"]["facts"]["identification"]["aac_track_count"],1)

    @unittest.skipUnless(FFMPEG and FFPROBE,"ffmpeg/ffprobe required")
    def test_pipeline_is_audit_only_and_preserves_expected_status_shape(self):
        rows={name:analyze_file(BASE/name,CFG_AUDIT,ROOT,FFMPEG,FFPROBE) for name in MANIFEST["cases"]}
        for name,expected in MANIFEST["cases"].items():
            row=rows[name]
            self.assertEqual(row.playability,expected["expected_playability"],name)
            self.assertEqual(row.run_status,expected["expected_run_status"],name)
            self.assertEqual(row.repair_execution,[],name);self.assertEqual(row.lossless_export,[],name)
            self.assertEqual(row.format_facts["mp4_aac_recovery_assessment"]["authority"],"ASSESSMENT_ONLY_NO_PUBLICATION",name)
            self.assertTrue(any(x.get("code")=="MP4_AAC_STRUCTURAL_AUDIT_AUTHORITY" for x in row.policy_decisions),name)
            self.assertIn("presentation_model",row.canonical_presentation_window,name)
        self.assertEqual(sum(x.run_status=="SUCCESS" for x in rows.values()),2)
        self.assertEqual(sum(x.run_status=="SUCCESS_WITH_FINDINGS" for x in rows.values()),4)

    @unittest.skipUnless(FFMPEG and FFPROBE,"ffmpeg/ffprobe required")
    def test_markdown_exposes_scope_and_no_intervention_authority(self):
        row=analyze_file(BASE/"00_healthy_aac_lc_44100_stereo.m4a",CFG_AUDIT,ROOT,FFMPEG,FFPROBE).to_dict()
        run={"run_id":"cp6","started_at":"2026-08-17T20:00:00-03:00","summary":{"discovered":1,"processed":1,"ok":1,"with_findings":0,"skipped":0,"failed":0,"repaired_outputs_created":0,"lossless_outputs_created":0,"outputs_reused":0,"candidates_rejected":0},"files":[row]}
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"report.md";write_md(path,run);text=path.read_text(encoding="utf-8")
            self.assertIn("Auditoría estructural MP4/M4A + AAC",text)
            self.assertIn("AAC LC",text);self.assertIn("desplazamientos dentro de mdat `True`",text)
            self.assertIn("reparaci",text);self.assertIn("recuperaci",text);self.assertIn("PCM NONE",text)


if __name__=="__main__":unittest.main()
