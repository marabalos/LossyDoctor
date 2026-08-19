from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import unittest
from pathlib import Path

from app.config import load_config
from app.external import ffmpeg_evidence_decode
from app.mp4_aac_timeline import audit
from app.pipeline import analyze_file
from formats.mp4_aac import analyze


ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/"samples"/"mp4_aac_cp19"
MANIFEST=json.loads((ROOT/"samples"/"mp4_aac_cp20_manifest.json").read_text(encoding="utf-8"))
CFG_AUDIT=copy.deepcopy(load_config(ROOT/"config.toml"));CFG_AUDIT["repair"]["enabled"]=False;CFG_AUDIT["lossless_recovery"]["enabled"]=False
FFMPEG=os.environ.get("LOSSYDOCTOR_FFMPEG") or shutil.which("ffmpeg")
FFPROBE=os.environ.get("LOSSYDOCTOR_FFPROBE") or shutil.which("ffprobe")


class Mp4SegmentProvenanceCP20(unittest.TestCase):
    @unittest.skipUnless(FFMPEG,"ffmpeg required")
    def test_fixed_corpus_proves_ordered_pcm_and_access_unit_provenance(self):
        self.assertEqual(MANIFEST["checkpoint"],"CP20");self.assertEqual(MANIFEST["authority"],"AUDIT_ONLY_NO_REPAIR_OR_RECOVERY")
        for name,expected in MANIFEST["cases"].items():
            path=BASE/name;before=hashlib.sha256(path.read_bytes()).hexdigest();self.assertEqual(before,expected["source_sha256"],name)
            result=audit(path,analyze(path)["facts"]["tracks"][0],FFMPEG)
            self.assertTrue(result["validated"],name);self.assertTrue(result["segment_level_provenance_validated"],name);self.assertFalse(result["intervention_authority"],name)
            self.assertEqual(result["canonical_presentation_pcm_s32le_sha256"],expected["canonical_pcm_sha256"],name)
            self.assertEqual(result["aac_access_unit_essence_sha256"],expected["aac_essence_sha256"],name)
            observed=[{"kind":x["kind"],"sample_count":x["presentation_sample_count"],"pcm_sha256":x["pcm_sha256"],"au_count":x["source_access_unit_count"],"au_first":x.get("source_access_unit_first_index"),"au_last":x.get("source_access_unit_last_index")} for x in result["segments"]]
            self.assertEqual(observed,expected["segments"],name);self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),before,name)

    @unittest.skipUnless(FFMPEG,"ffmpeg required")
    def test_empty_edit_is_zero_silence_not_source_pcm(self):
        result=audit(BASE/"02_empty_then_media_edit.m4a",analyze(BASE/"02_empty_then_media_edit.m4a")["facts"]["tracks"][0],FFMPEG);empty=result["segments"][0]
        self.assertEqual(empty["kind"],"EMPTY");self.assertEqual(empty["source_access_unit_indices"],[])
        self.assertEqual(empty["provenance"],"EXPLICIT_TIMELINE_SILENCE_NOT_SOURCE_PCM");self.assertEqual(empty["presentation_sample_count"],4410)

    @unittest.skipUnless(FFMPEG,"ffmpeg required")
    def test_contiguous_segments_equal_independent_healthy_presentation(self):
        expected=MANIFEST["cases"]["00_two_contiguous_media_edits.m4a"]["canonical_pcm_sha256"]
        healthy=ffmpeg_evidence_decode(ROOT/"samples"/"mp4_aac_cp8"/"00_healthy_single_edit_44100_stereo.m4a",FFMPEG,2,300)
        self.assertTrue(healthy["passed"]);self.assertEqual(healthy["sample_frames"],52920);self.assertEqual(healthy["pcm_sha256"],expected)

    @unittest.skipUnless(FFMPEG,"ffmpeg required")
    def test_changed_access_unit_hash_fails_closed_before_claim(self):
        parsed=analyze(BASE/"00_two_contiguous_media_edits.m4a");track=copy.deepcopy(parsed["facts"]["tracks"][0]);track["access_unit_provenance"]["access_units"][10]["sha256"]="0"*64
        result=audit(BASE/"00_two_contiguous_media_edits.m4a",track,FFMPEG)
        self.assertFalse(result["validated"]);self.assertFalse(result["segment_level_provenance_validated"]);self.assertEqual(result["reason"],"AAC_ACCESS_UNIT_HASH_CHANGED")

    @unittest.skipUnless(FFMPEG and FFPROBE,"ffmpeg/ffprobe required")
    def test_pipeline_separates_canonical_provenance_from_decoder_behavior(self):
        for name in MANIFEST["cases"]:
            row=analyze_file(BASE/name,CFG_AUDIT,ROOT,FFMPEG,FFPROBE);audit_result=row.format_facts["mp4_aac_multi_edit_audit"]
            self.assertTrue(audit_result["segment_level_provenance_validated"],name);self.assertFalse(audit_result["decoder_sample_count_matches"],name)
            self.assertIn("MP4_PRESENTATION_SAMPLE_COUNT_MISMATCH",[x.code for x in row.issues],name)
            self.assertEqual(row.validity_domains["TIMELINE_VALIDITY"],"VALIDATED_MULTI_EDIT_SEGMENT_PROVENANCE_AUDIT_ONLY",name)
            self.assertEqual(row.repair_plan,[],name);self.assertEqual(row.repair_execution,[],name);self.assertEqual(row.lossless_export,[],name)
            self.assertTrue(any(x.get("code")=="MP4_AAC_MULTI_EDIT_PROVENANCE_AUTHORITY" for x in row.policy_decisions),name)

    @unittest.skipUnless(FFMPEG and FFPROBE,"ffmpeg/ffprobe required")
    def test_invalid_complex_edits_gain_no_provenance_or_authority(self):
        for name in ("03_second_edit_rate_unsupported.m4a","04_second_edit_outside_media.m4a"):
            row=analyze_file(BASE/name,CFG_AUDIT,ROOT,FFMPEG,FFPROBE);result=row.format_facts["mp4_aac_multi_edit_audit"]
            self.assertFalse(result["validated"],name);self.assertEqual(result["reason"],"MULTI_EDIT_PRESENTATION_NOT_STRUCTURALLY_DETERMINED",name)
            self.assertFalse(result["intervention_authority"],name);self.assertEqual(row.validity_domains["TIMELINE_VALIDITY"],"NONCONFORMANT_OR_INCOMPLETE_MEDIA_PRESENTATION",name)


if __name__=="__main__":unittest.main()
