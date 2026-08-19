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
from formats.mp4_aac import analyze
from reporting.markdown_report import write_md


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "samples" / "mp4_aac_cp7"
MANIFEST = json.loads((ROOT / "samples" / "mp4_aac_cp7_manifest.json").read_text(encoding="utf-8"))
CFG = load_config(ROOT / "config.toml")
CFG_AUDIT = copy.deepcopy(CFG)
CFG_AUDIT["repair"]["enabled"] = False
CFG_AUDIT["lossless_recovery"]["enabled"] = False
FFMPEG = os.environ.get("LOSSYDOCTOR_FFMPEG") or shutil.which("ffmpeg")
FFPROBE = os.environ.get("LOSSYDOCTOR_FFPROBE") or shutil.which("ffprobe")


def sha256(data:bytes):return hashlib.sha256(data).hexdigest()


class Mp4AacProvenanceCP7(unittest.TestCase):
    def test_manifest_hashes_issue_sets_and_provenance_counts(self):
        self.assertEqual(MANIFEST["checkpoint"],"CP7")
        self.assertEqual(MANIFEST["authority"],"AUDIT_ONLY_NO_REPAIR_OR_RECOVERY")
        seen=set()
        for name,expected in MANIFEST["cases"].items():
            path=BASE/name;data=path.read_bytes();digest=sha256(data)
            self.assertEqual(digest,expected["sha256"],name);self.assertNotIn(digest,seen,name);seen.add(digest)
            parsed=analyze(path);provenance=parsed["facts"]["tracks"][0]["access_unit_provenance"]
            self.assertEqual([issue.code for issue in parsed["issues"]],expected["expected_issues"],name)
            self.assertEqual(provenance["sample_count_declared"],expected["expected_declared_samples"],name)
            self.assertEqual(provenance["mapped_sample_count"],expected["expected_mapped_samples"],name)
            self.assertEqual(provenance["hashed_sample_count"],expected["expected_hashed_samples"],name)
            self.assertEqual(provenance["mapping_complete"],expected["expected_mapping_complete"],name)
            self.assertEqual(provenance["decode_timeline_complete"],expected["expected_decode_timeline_complete"],name)
        self.assertEqual(len(seen),6)

    def test_healthy_access_units_are_byte_exact_and_decode_time_contiguous(self):
        for name,count,end_units in (("00_healthy_aac_lc_44100_stereo.m4a",53,53944),("01_healthy_aac_lc_48000_mono.m4a",39,39424)):
            data=(BASE/name).read_bytes();parsed=analyze(BASE/name);track=parsed["facts"]["tracks"][0];provenance=track["access_unit_provenance"];rows=provenance["access_units"]
            self.assertTrue(provenance["mapping_complete"],name);self.assertTrue(provenance["all_access_units_hashed"],name)
            self.assertEqual((len(rows),provenance["decode_end_units"]),(count,end_units),name)
            self.assertEqual(rows[0]["decode_time_units"],0,name)
            for previous,current in zip(rows,rows[1:]):
                self.assertEqual(current["decode_time_units"],previous["decode_time_units"]+previous["duration_units"],name)
                if current["chunk_index"]==previous["chunk_index"]:self.assertEqual(current["byte_start"],previous["byte_end"],name)
            for row in rows:
                self.assertEqual(row["sha256"],sha256(data[row["byte_start"]:row["byte_end"]]),name)

    def test_negative_cases_separate_mapping_extent_timeline_and_description(self):
        rows={name:analyze(BASE/name)["facts"]["tracks"][0]["access_unit_provenance"] for name in MANIFEST["cases"]}
        self.assertEqual(rows["02_stsc_sample_coverage_mismatch.m4a"]["mapped_sample_count"],52)
        self.assertEqual(rows["03_access_unit_extent_outside_mdat.m4a"]["hashed_sample_count"],52)
        self.assertFalse(rows["04_access_unit_timeline_incomplete.m4a"]["decode_timeline_complete"])
        self.assertTrue(all(x["sample_description_valid"] for x in rows["00_healthy_aac_lc_44100_stereo.m4a"]["access_units"]))
        self.assertTrue(all(not x["sample_description_valid"] for x in rows["05_sample_description_reference_invalid.m4a"]["access_units"]))

    @unittest.skipUnless(FFMPEG and FFPROBE,"ffmpeg/ffprobe required")
    def test_healthy_direct_mapping_matches_ffprobe_packets_byte_for_byte(self):
        for name in ("00_healthy_aac_lc_44100_stereo.m4a","01_healthy_aac_lc_48000_mono.m4a"):
            row=analyze_file(BASE/name,CFG_AUDIT,ROOT,FFMPEG,FFPROBE);demux=row.format_facts["mp4_aac_demux_evidence"]
            self.assertTrue(demux["all_boundaries_and_hashes_equal"],name);self.assertTrue(demux["durations_equal"],name)
            self.assertTrue(demux["dts_shift_is_constant"],name);self.assertEqual(demux["constant_dts_shift_media_units"],-1024,name)
            self.assertEqual(row.validity_domains["ACCESS_UNIT_PROVENANCE_VALIDITY"],"VALIDATED_BYTE_EXACT_SHA256",name)
            self.assertEqual(row.validity_domains["DEMUX_BOUNDARY_VALIDITY"],"VALIDATED_DIRECT_SAMPLE_TO_FFPROBE_PACKET_IDENTITY",name)

    @unittest.skipUnless(FFMPEG and FFPROBE,"ffmpeg/ffprobe required")
    def test_pipeline_is_audit_only_and_fails_closed_when_demux_exposes_no_audio(self):
        rows={name:analyze_file(BASE/name,CFG_AUDIT,ROOT,FFMPEG,FFPROBE) for name in MANIFEST["cases"]}
        for name,expected in MANIFEST["cases"].items():
            row=rows[name]
            self.assertEqual(row.playability,expected["expected_playability"],name);self.assertEqual(row.run_status,expected["expected_run_status"],name)
            self.assertEqual(row.repair_execution,[],name);self.assertEqual(row.lossless_export,[],name)
            self.assertFalse(row.recovery_assessment["eligible"],name)
            self.assertTrue(any(x.get("code")=="MP4_AAC_ACCESS_UNIT_PROVENANCE_AUTHORITY" for x in row.policy_decisions),name)
            self.assertIn("presentation_model",row.canonical_presentation_window,name)
        blocked=rows["05_sample_description_reference_invalid.m4a"]
        self.assertEqual(blocked.validity_domains["DECODE_VALIDITY"],"INVALID_NO_DEMUXED_AUDIO_PACKETS")
        self.assertEqual(blocked.format_facts["mp4_aac_demux_evidence"]["ffprobe_packet_count"],0)
        self.assertEqual(sum(x.run_status=="SUCCESS" for x in rows.values()),2)
        self.assertEqual(sum(x.run_status=="SUCCESS_WITH_FINDINGS" for x in rows.values()),4)

    @unittest.skipUnless(FFMPEG and FFPROBE,"ffmpeg/ffprobe required")
    def test_markdown_exposes_provenance_demux_identity_and_deferred_edit_lists(self):
        row=analyze_file(BASE/"00_healthy_aac_lc_44100_stereo.m4a",CFG_AUDIT,ROOT,FFMPEG,FFPROBE).to_dict()
        run={"run_id":"cp7","started_at":"2026-08-17T22:00:00-03:00","summary":{"discovered":1,"processed":1,"ok":1,"with_findings":0,"skipped":0,"failed":0,"repaired_outputs_created":0,"lossless_outputs_created":0,"outputs_reused":0,"candidates_rejected":0},"files":[row]}
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"report.md";write_md(path,run);text=path.read_text(encoding="utf-8")
            self.assertIn("Unidades de acceso AAC asignadas / autenticadas por SHA-256: `53` / `53`",text)
            self.assertIn("posiciones `True` · tamaños `True` · SHA-256 `True` · duraciones `True`",text)
            self.assertIn("Desplazamiento DTS constante de FFprobe",text);self.assertIn("MP4_AAC_ACCESS_UNIT_PROVENANCE_AUTHORITY",text)
            self.assertIn("presentaci",text);self.assertIn("queda diferida",text)


if __name__=="__main__":unittest.main()
