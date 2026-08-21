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
from app.mp4_aac_preservation_hierarchy import ORDER, POLICY, resolve
from app.pipeline import analyze_file
from reporting.markdown_report import write_md


ROOT=Path(__file__).resolve().parents[1];BASE=ROOT/"samples"/"mp4_aac_cp9"
MANIFEST=json.loads((ROOT/"samples"/"mp4_aac_cp12_manifest.json").read_text(encoding="utf-8"));CFG=load_config(ROOT/"config.toml")
FFMPEG=os.environ.get("LOSSYDOCTOR_FFMPEG") or shutil.which("ffmpeg");FFPROBE=os.environ.get("LOSSYDOCTOR_FFPROBE") or shutil.which("ffprobe")


def sha256(path:Path):return hashlib.sha256(path.read_bytes()).hexdigest()


class Mp4PreservationHierarchyCP12(unittest.TestCase):
    def test_extension_fix_is_outside_codec_repair_domain(self):
        extension=[{"status":"CREATED","repair_spec_id":"FIX_EXTENSION_BYTE_IDENTICAL","manifest":{"derivation_kind":"EXTENSION_FIXED"}}]
        self.assertIsNone(resolve(extension,[],{},"PLAYABLE",{"EXTENSION_CONTENT_MISMATCH"})["policy_violation"])
    def test_manifest_reuses_the_byte_distinct_cp9_corpus(self):
        self.assertEqual((MANIFEST["checkpoint"],MANIFEST["policy"]),("CP12",POLICY))
        seen=set()
        for name,expected in MANIFEST["cases"].items():
            digest=sha256(BASE/name);self.assertEqual(digest,expected["sha256"],name);self.assertNotIn(digest,seen,name);seen.add(digest)
        self.assertEqual(len(seen),5)

    @unittest.skipUnless(FFMPEG and FFPROBE,"ffmpeg/ffprobe required")
    def test_default_pipeline_outcomes_are_exclusive(self):
        with tempfile.TemporaryDirectory() as directory:
            temp=Path(directory)
            for name in MANIFEST["cases"]:shutil.copy2(BASE/name,temp/name)
            for name,expected in MANIFEST["cases"].items():
                row=analyze_file(temp/name,CFG,ROOT,FFMPEG,FFPROBE);hierarchy=row.format_facts["mp4_aac_preservation_hierarchy"]
                self.assertEqual((hierarchy["policy"],hierarchy["order"]),(POLICY,ORDER),name)
                self.assertEqual(hierarchy["selected_tier"],expected["expected_default_tier"],name)
                self.assertTrue(hierarchy["exclusive_outcome"],name);self.assertIsNone(hierarchy["policy_violation"],name)
                self.assertTrue(any(x.get("code")=="MP4_AAC_PRESERVATION_HIERARCHY" for x in row.policy_decisions),name)

    @unittest.skipUnless(FFMPEG and FFPROBE,"ffmpeg/ffprobe required")
    def test_repair_precedes_flac_and_flac_remains_the_disabled_repair_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            temp=Path(directory);name="01_unique_mdat_wrong_offset_complete_clean.m4a"
            repaired_source=temp/("repair_"+name);flac_source=temp/("flac_"+name);shutil.copy2(BASE/name,repaired_source);shutil.copy2(BASE/name,flac_source)
            repaired=analyze_file(repaired_source,CFG,ROOT,FFMPEG,FFPROBE)
            self.assertEqual(repaired.format_facts["mp4_aac_preservation_hierarchy"]["selected_tier"],ORDER[0]);self.assertEqual(repaired.lossless_export,[])
            fallback_cfg=copy.deepcopy(CFG);fallback_cfg["repair"]["enabled"]=False
            fallback=analyze_file(flac_source,fallback_cfg,ROOT,FFMPEG,FFPROBE)
            self.assertEqual(fallback.format_facts["mp4_aac_preservation_hierarchy"]["selected_tier"],ORDER[1]);self.assertEqual(fallback.repair_execution,[])
            self.assertEqual(len(fallback.lossless_export[0]["outputs"]),1)

    def test_competing_or_unknown_published_families_fail_closed(self):
        repair=[{"status":"CREATED","repair_spec_id":"MP4_REWRITE_SINGLE_CHUNK_OFFSET_TO_UNIQUE_MDAT","manifest":{"derivation_kind":"REPAIRED_SAFE","verification":{"passed":True,"strict_decode":"PASS","playback_decode":"PASS","ffprobe":"PASS"}}}]
        recovery=[{"status":"CREATED","outputs":[{"status":"CREATED","manifest":{"derivation_kind":"RECOVERED_LOSSLESS","materialization":"MP4_AAC_CANONICAL_PRESENTATION_FROM_BYTE_PRESERVED_ACCESS_UNITS"}}]}]
        competing=resolve(repair,recovery,{"pcm_class":"COMPLETE_CLEAN"},"UNPLAYABLE",{"MP4_CHUNK_OFFSET_OUTSIDE_MDAT"})
        self.assertFalse(competing["exclusive_outcome"]);self.assertEqual(competing["policy_violation"],"MULTIPLE_MP4_AAC_PRESERVATION_TIERS_PUBLISHED_SIMULTANEOUSLY")
        unknown_repair=copy.deepcopy(repair);unknown_repair[0]["repair_spec_id"]="UNKNOWN"
        self.assertEqual(resolve(unknown_repair,[],{},"UNPLAYABLE",{"X"})["policy_violation"],"UNKNOWN_OR_UNVERIFIED_MP4_AAC_REPAIR_FAMILY")
        unknown_recovery=copy.deepcopy(recovery);unknown_recovery[0]["outputs"][0]["manifest"]["materialization"]="UNKNOWN"
        self.assertEqual(resolve([],unknown_recovery,{},"UNPLAYABLE",{"X"})["policy_violation"],"UNKNOWN_MP4_AAC_PRESERVATION_DERIVATION_FAMILY")

    @unittest.skipUnless(FFMPEG and FFPROBE,"ffmpeg/ffprobe required")
    def test_second_run_reuses_the_same_tier_without_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/"01_unique_mdat_wrong_offset_complete_clean.m4a";shutil.copy2(BASE/source.name,source)
            first=analyze_file(source,CFG,ROOT,FFMPEG,FFPROBE);files={path.name for path in source.parent.iterdir()}
            second=analyze_file(source,CFG,ROOT,FFMPEG,FFPROBE)
            self.assertEqual(first.format_facts["mp4_aac_preservation_hierarchy"]["status_counts"],{"CREATED":1,"REUSED":0})
            self.assertEqual(second.format_facts["mp4_aac_preservation_hierarchy"]["status_counts"],{"CREATED":0,"REUSED":1})
            self.assertEqual(files,{path.name for path in source.parent.iterdir()})

    @unittest.skipUnless(FFMPEG and FFPROBE,"ffmpeg/ffprobe required")
    def test_markdown_exposes_the_strict_mp4_aac_hierarchy(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/"x.m4a";shutil.copy2(BASE/"01_unique_mdat_wrong_offset_complete_clean.m4a",source)
            row=analyze_file(source,CFG,ROOT,FFMPEG,FFPROBE).to_dict();run={"run_id":"cp12","started_at":"2026-08-17T20:00:00-03:00","summary":{"discovered":1,"processed":1,"ok":0,"with_findings":1,"skipped":0,"failed":0,"repaired_outputs_created":1,"lossless_outputs_created":0,"outputs_reused":0,"candidates_rejected":0},"files":[row]}
            report=Path(directory)/"report.md";write_md(report,run);text=report.read_text(encoding="utf-8")
            self.assertIn("Jerarquía de resolución de preservación MP4/AAC",text);self.assertIn(POLICY,text);self.assertIn(ORDER[0],text);self.assertIn("resultado exclusivo: `True`",text)


if __name__=="__main__":unittest.main()
