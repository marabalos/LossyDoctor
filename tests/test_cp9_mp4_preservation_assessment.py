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
from app.external import ffmpeg_evidence_decode
from app.pipeline import analyze_file
from formats.mp4_aac import analyze
from reporting.markdown_report import write_md


ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/"samples"/"mp4_aac_cp9"
MANIFEST=json.loads((ROOT/"samples"/"mp4_aac_cp9_manifest.json").read_text(encoding="utf-8"))
CFG=load_config(ROOT/"config.toml");CFG_AUDIT=copy.deepcopy(CFG);CFG_AUDIT["repair"]["enabled"]=False;CFG_AUDIT["lossless_recovery"]["enabled"]=False
FFMPEG=os.environ.get("LOSSYDOCTOR_FFMPEG") or shutil.which("ffmpeg")
FFPROBE=os.environ.get("LOSSYDOCTOR_FFPROBE") or shutil.which("ffprobe")


def sha256(path:Path):return hashlib.sha256(path.read_bytes()).hexdigest()


@unittest.skipUnless(FFMPEG and FFPROBE,"ffmpeg/ffprobe required")
class Mp4PreservationAssessmentCP9(unittest.TestCase):
    def rows(self):return {name:analyze_file(BASE/name,CFG_AUDIT,ROOT,FFMPEG,FFPROBE) for name in MANIFEST["cases"]}

    def test_manifest_hashes_expected_outcomes_and_binary_distinctness(self):
        self.assertEqual((MANIFEST["checkpoint"],MANIFEST["authority"]),("CP9","ASSESSMENT_ONLY_NO_PUBLICATION"))
        seen=set();rows=self.rows()
        for name,expected in MANIFEST["cases"].items():
            digest=sha256(BASE/name);self.assertEqual(digest,expected["sha256"],name);self.assertNotIn(digest,seen,name);seen.add(digest)
            row=rows[name];self.assertEqual(row.playability,expected["expected_playability"],name)
            self.assertEqual(row.pcm_recovery_class,expected["expected_pcm_class"],name);self.assertEqual(row.recovery_assessment["eligible"],expected["expected_eligible"],name)
            self.assertEqual(row.recovery_assessment["reason"],expected["expected_reason"],name);self.assertEqual([issue.code for issue in row.issues],expected["expected_issue_codes"],name)
        self.assertEqual(len(seen),5)
        self.assertEqual(sum(row.run_status=="SUCCESS" for row in rows.values()),1);self.assertEqual(sum(row.run_status=="SUCCESS_WITH_FINDINGS" for row in rows.values()),4)
        self.assertTrue(all(row.run_status!="FAILED" for row in rows.values()))

    def test_unique_mdat_candidate_preserves_every_aac_byte_and_exact_control_pcm(self):
        positive=analyze_file(BASE/"01_unique_mdat_wrong_offset_complete_clean.m4a",CFG_AUDIT,ROOT,FFMPEG,FFPROBE);assessment=positive.recovery_assessment
        healthy_path=BASE/"00_healthy_no_recovery_required.m4a";parsed=analyze(healthy_path);track=parsed["facts"]["tracks"][0];units=track["access_unit_provenance"]["access_units"];data=healthy_path.read_bytes()
        essence=hashlib.sha256(b"".join(data[unit["byte_start"]:unit["byte_end"]] for unit in units)).hexdigest()
        canonical=ffmpeg_evidence_decode(healthy_path,FFMPEG,2,CFG_AUDIT["app"]["external_timeout_seconds"])
        self.assertTrue(assessment["eligible"]);self.assertEqual(assessment["aac_access_unit_essence_sha256"],essence)
        self.assertEqual(assessment["presentation_sample_count"],52920);self.assertEqual(assessment["presentation_pcm_s32le_sha256"],canonical["pcm_sha256"])
        self.assertEqual(assessment["temporary_strict_decode"],"PASS")
        self.assertEqual(assessment["temporary_transport"],"ADTS_HEADERS_SYNTHESIZED_AAC_PAYLOAD_BYTES_UNCHANGED")

    def test_ambiguous_or_inconsistent_candidates_fail_closed(self):
        rows=self.rows()
        reasons={"02_extra_byte_inside_mdat_ambiguous.m4a":"PARTIAL_RECOVERY_BLOCKED_AMBIGUOUS_EXTRA_MDAT_BYTES","03_sample_sizes_overrun_mdat.m4a":"PARTIAL_RECOVERY_BLOCKED_UNPROVEN_MDAT_CHUNK_ORIGIN"}
        for name,reason in reasons.items():
            self.assertFalse(rows[name].recovery_assessment["eligible"],name);self.assertEqual(rows[name].recovery_assessment["reason"],reason,name)
        invalid=rows["04_invalid_sample_description_reference.m4a"]
        self.assertFalse(invalid.recovery_assessment["eligible"]);self.assertEqual(invalid.recovery_assessment["reason"],"ISSUE_SET_OUTSIDE_UNIQUE_CHUNK_OFFSET_SCOPE")

    def test_assessment_is_read_only_and_publishes_nothing(self):
        before={path.name:sha256(path) for path in BASE.glob("*.m4a")};names={path.name for path in BASE.iterdir()}
        rows=self.rows()
        self.assertTrue(all(not row.repair_execution and not row.lossless_export for row in rows.values()))
        self.assertTrue(all(not row.format_facts["mp4_aac_recovery_assessment"]["publication_enabled"] for row in rows.values()))
        self.assertEqual(before,{path.name:sha256(path) for path in BASE.glob("*.m4a")});self.assertEqual(names,{path.name for path in BASE.iterdir()})
        self.assertTrue(all(any(decision.get("code")=="MP4_AAC_COMPLETE_CLEAN_ASSESSMENT_AUTHORITY" for decision in row.policy_decisions) for row in rows.values()))

    def test_markdown_exposes_proof_and_no_publication_authority(self):
        row=analyze_file(BASE/"01_unique_mdat_wrong_offset_complete_clean.m4a",CFG_AUDIT,ROOT,FFMPEG,FFPROBE).to_dict()
        run={"run_id":"cp9","started_at":"2026-08-17T23:30:00-03:00","summary":{"discovered":1,"processed":1,"ok":0,"with_findings":1,"skipped":0,"failed":0,"repaired_outputs_created":0,"lossless_outputs_created":0,"outputs_reused":0,"candidates_rejected":0},"files":[row]}
        with tempfile.TemporaryDirectory() as directory:
            report=Path(directory)/"report.md";write_md(report,run);text=report.read_text(encoding="utf-8")
            self.assertIn("Evaluación de preservación: clase `COMPLETE_CLEAN` · elegible `True` · publicación `False`",text)
            self.assertIn("Unidades de acceso AAC preservadas byte a byte: `53` / `19655` bytes",text);self.assertIn("PCM de presentación demostrado: muestras `52920`",text)
            self.assertIn("MP4_AAC_COMPLETE_CLEAN_ASSESSMENT_AUTHORITY",text);self.assertIn("la reparación y la publicación de salidas permanecen bloqueadas",text)


if __name__=="__main__":unittest.main()
