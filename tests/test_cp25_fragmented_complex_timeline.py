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
from app.external import decode_to_raw_file
from app.mp4_aac_timeline import audit
from app.pipeline import analyze_file
from formats.mp4_aac import analyze


ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/"samples"/"mp4_aac_cp25"
MANIFEST=json.loads((ROOT/"samples"/"mp4_aac_cp25_manifest.json").read_text(encoding="utf-8"))
CFG=load_config(ROOT/"config.toml");CFG["repair"]["enabled"]=False
CFG_AUDIT=copy.deepcopy(CFG);CFG_AUDIT["app"]["mode"]="audit_only"
FFMPEG=os.environ.get("LOSSYDOCTOR_FFMPEG") or shutil.which("ffmpeg")
FFPROBE=os.environ.get("LOSSYDOCTOR_FFPROBE") or shutil.which("ffprobe")


def sha256(path:Path):return hashlib.sha256(path.read_bytes()).hexdigest()
def outputs(row):return [output for export in row.lossless_export for output in export.get("outputs",[])]


class FragmentedComplexTimelineCP25(unittest.TestCase):
    def test_fixed_corpus_matches_structural_expectations(self):
        for name,expected in MANIFEST["cases"].items():
            path=BASE/name;self.assertEqual(sha256(path),expected["sha256"],name);parsed=analyze(path);track=parsed["facts"]["tracks"][0];window=track["presentation_window"]
            self.assertTrue(parsed["facts"]["mp4"]["fragmented"],name);self.assertEqual(parsed["facts"]["fragmented_mp4"]["mapping_complete"],expected["expected_mapping_complete"],name)
            self.assertEqual([issue.code for issue in parsed["issues"]],expected["expected_issues"],name);self.assertEqual(window["determined"],expected["expected_window_determined"],name)
            self.assertEqual(window.get("presentation_model"),expected["expected_window_model"],name);self.assertEqual(window.get("reason"),expected["expected_window_reason"],name)
            self.assertEqual(window.get("presentation_sample_count"),expected["expected_presentation_sample_count"],name)

    @unittest.skipUnless(FFMPEG,"ffmpeg required")
    def test_valid_combinations_have_exact_segment_pcm_provenance(self):
        for name in list(MANIFEST["cases"])[:3]:
            path=BASE/name;track=analyze(path)["facts"]["tracks"][0];result=audit(path,track,FFMPEG)
            self.assertTrue(result["validated"],name);self.assertEqual(result["policy"],"MP4_AAC_FRAGMENTED_EDIT_LIST_PCM_PROVENANCE",name)
            self.assertEqual(result["presentation_sample_count"],52920,name);self.assertFalse(result["intervention_authority"],name)
            expected=MANIFEST["cases"][name];self.assertEqual(result["canonical_presentation_pcm_s32le_sha256"],expected["expected_canonical_pcm_sha256"],name)
            self.assertEqual(result["aac_access_unit_essence_sha256"],expected["expected_aac_essence_sha256"],name)
            self.assertEqual([segment["pcm_sha256"] for segment in result["segments"]],expected["expected_segment_pcm_sha256"],name)
        silence=audit(BASE/"02_fragmented_empty_then_media_edit.m4a",analyze(BASE/"02_fragmented_empty_then_media_edit.m4a")["facts"]["tracks"][0],FFMPEG)["segments"][0]
        self.assertEqual((silence["kind"],silence["presentation_sample_count"],silence["provenance"]),("EMPTY",4410,"EXPLICIT_TIMELINE_SILENCE_NOT_SOURCE_PCM"))

    @unittest.skipUnless(FFMPEG,"ffmpeg required")
    def test_invalid_rate_range_and_fragment_mapping_fail_closed(self):
        for name in list(MANIFEST["cases"])[3:]:
            path=BASE/name;result=audit(path,analyze(path)["facts"]["tracks"][0],FFMPEG)
            self.assertFalse(result["validated"],name);self.assertFalse(result["intervention_authority"],name)

    @unittest.skipUnless(FFMPEG and FFPROBE,"ffmpeg/ffprobe required")
    def test_pipeline_exposes_combined_provenance_and_only_policy_driven_outputs(self):
        for name in list(MANIFEST["cases"])[:3]:
            source=BASE/name;row=analyze_file(source,CFG_AUDIT,ROOT,FFMPEG,FFPROBE)
            evidence=row.format_facts["mp4_aac_multi_edit_audit"];self.assertTrue(evidence["segment_level_provenance_validated"],name)
            self.assertEqual(row.validity_domains["TIMELINE_VALIDITY"],"VALIDATED_FRAGMENTED_PRESENTATION_PCM_PROVENANCE_AUDIT_ONLY",name)
            self.assertEqual(outputs(row),[],name);self.assertTrue(any(x.get("code")=="MP4_AAC_FRAGMENTED_EDIT_PROVENANCE_AUTHORITY" for x in row.policy_decisions),name)
        for name in list(MANIFEST["cases"])[3:]:
            row=analyze_file(BASE/name,CFG,ROOT,FFMPEG,FFPROBE);self.assertEqual(outputs(row),[],name);self.assertFalse(row.recovery_assessment.get("eligible"),name)

    @unittest.skipUnless(FFMPEG and FFPROBE,"ffmpeg/ffprobe required")
    def test_enabled_pipeline_creates_verified_reusable_flac_without_touching_original(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/"combined.m4a";shutil.copy2(BASE/"01_fragmented_three_reordered_media_edits.m4a",source);before=sha256(source)
            row=analyze_file(source,CFG,ROOT,FFMPEG,FFPROBE);created=outputs(row);self.assertEqual(sha256(source),before);self.assertEqual(len(created),1)
            self.assertEqual(created[0]["status"],"CREATED");manifest=created[0]["manifest"];expected=MANIFEST["cases"]["01_fragmented_three_reordered_media_edits.m4a"]
            self.assertEqual((manifest["presentation_model"],manifest["sample_count"]),("FRAGMENTED_MULTI_EDIT_PRESENTATION",52920))
            self.assertEqual(manifest["source_canonical_pcm_sha256"],expected["expected_canonical_pcm_sha256"]);self.assertNotEqual(Path(created[0]["output_path"]),source)
            raw=Path(directory)/"verified.s32le";self.assertTrue(decode_to_raw_file(Path(created[0]["output_path"]),raw,FFMPEG,300)["passed"]);self.assertEqual(sha256(raw),expected["expected_canonical_pcm_sha256"])
            files={path.name for path in Path(directory).iterdir()};again=outputs(analyze_file(source,CFG,ROOT,FFMPEG,FFPROBE));self.assertEqual(len(again),1);self.assertEqual(again[0]["status"],"REUSED")
            self.assertEqual(files,{path.name for path in Path(directory).iterdir()});self.assertEqual(sha256(source),before)


if __name__=="__main__":unittest.main()
