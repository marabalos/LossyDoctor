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
from formats.mp4_aac import _build_presentation_window,analyze
from reporting.markdown_report import write_md


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "samples" / "mp4_aac_cp8"
MANIFEST = json.loads((ROOT / "samples" / "mp4_aac_cp8_manifest.json").read_text(encoding="utf-8"))
CFG = load_config(ROOT / "config.toml")
CFG_AUDIT = copy.deepcopy(CFG)
CFG_AUDIT["repair"]["enabled"] = False
CFG_AUDIT["lossless_recovery"]["enabled"] = False
FFMPEG = os.environ.get("LOSSYDOCTOR_FFMPEG") or shutil.which("ffmpeg")
FFPROBE = os.environ.get("LOSSYDOCTOR_FFPROBE") or shutil.which("ffprobe")


def sha256(path:Path):return hashlib.sha256(path.read_bytes()).hexdigest()


class Mp4PresentationWindowCP8(unittest.TestCase):
    def test_manifest_hashes_issue_sets_and_window_authority(self):
        self.assertEqual(MANIFEST["checkpoint"],"CP8");self.assertEqual(MANIFEST["authority"],"AUDIT_ONLY_NO_REPAIR_OR_RECOVERY")
        seen=set()
        for name,expected in MANIFEST["cases"].items():
            path=BASE/name;digest=sha256(path);self.assertEqual(digest,expected["sha256"],name);self.assertNotIn(digest,seen,name);seen.add(digest)
            parsed=analyze(path);window=parsed["facts"]["tracks"][0]["presentation_window"]
            self.assertEqual([issue.code for issue in parsed["issues"]],expected["expected_issues"],name)
            self.assertEqual(window["determined"],expected["expected_window_determined"],name)
            self.assertEqual(window.get("reason"),expected["expected_window_reason"],name)
        self.assertEqual(len(seen),6)

    def test_healthy_single_edits_define_exact_audible_windows(self):
        expected={"00_healthy_single_edit_44100_stereo.m4a":(44100,53944,52920),"01_healthy_single_edit_48000_mono.m4a":(48000,39424,38400)}
        for name,(timescale,media_duration,presentation_samples) in expected.items():
            parsed=analyze(BASE/name);movie=parsed["facts"]["mp4"]["movie_header"];track=parsed["facts"]["tracks"][0];window=track["presentation_window"]
            self.assertEqual(parsed["issues"],[],name);self.assertTrue(window["determined"],name)
            self.assertEqual((movie["timescale"],track["media_timescale"]),(timescale,timescale),name)
            self.assertEqual((window["media_start_units"],window["media_end_units"]),(1024,media_duration),name)
            self.assertEqual((window["initial_media_trim_units"],window["trailing_media_trim_units"]),(1024,0),name)
            self.assertEqual(window["presentation_sample_count"],presentation_samples,name)

    def test_multiple_edits_are_now_under_cp19_audit_authority(self):
        issues=[];track={"media_timescale":48000,"media_duration":48000,"sample_descriptions":[{"sample_rate":48000}],"edit_list":{"present":True,"entries":[{"segment_duration_movie_units":24000,"media_time":0,"media_rate_integer":1,"media_rate_fraction":0},{"segment_duration_movie_units":24000,"media_time":24000,"media_rate_integer":1,"media_rate_fraction":0}]}}
        _build_presentation_window(track,{"timescale":48000,"duration":48000},issues)
        self.assertTrue(track["presentation_window"]["determined"]);self.assertEqual(track["presentation_window"]["reason"],"VALIDATED_MULTI_EDIT_PRESENTATION_AUDIT_ONLY")
        self.assertEqual(track["presentation_window"]["presentation_model"],"MULTI_EDIT_PRESENTATION");self.assertEqual(issues,[])

    def test_nonintegral_presentation_sample_count_fails_closed(self):
        issues=[];track={"media_timescale":3,"media_duration":1,"sample_descriptions":[{"sample_rate":2}],"edit_list":{"present":True,"entries":[{"segment_duration_movie_units":1,"media_time":0,"media_rate_integer":1,"media_rate_fraction":0}]}}
        _build_presentation_window(track,{"timescale":3,"duration":1},issues)
        self.assertFalse(track["presentation_window"]["determined"]);self.assertEqual(track["presentation_window"]["reason"],"INEXACT_PRESENTATION_SAMPLE_COUNT")
        self.assertEqual([issue.code for issue in issues],["MP4_EDIT_LIST_SAMPLE_COUNT_INEXACT"])

    @unittest.skipUnless(FFMPEG and FFPROBE,"ffmpeg/ffprobe required")
    def test_decoder_samples_and_demux_shift_confirm_healthy_windows(self):
        expected={"00_healthy_single_edit_44100_stereo.m4a":52920,"01_healthy_single_edit_48000_mono.m4a":38400}
        for name,samples in expected.items():
            row=analyze_file(BASE/name,CFG_AUDIT,ROOT,FFMPEG,FFPROBE);decode=row.format_facts["mp4_aac_presentation_decoder_evidence"];demux=row.format_facts["mp4_aac_demux_evidence"]
            self.assertTrue(row.canonical_presentation_window["determined"],name);self.assertEqual(decode["sample_frames"],samples,name)
            self.assertEqual(demux["constant_dts_shift_media_units"],-row.canonical_presentation_window["media_start_units"],name)
            self.assertEqual(row.validity_domains["TIMELINE_VALIDITY"],"VALIDATED_SINGLE_EDIT_PRESENTATION_AND_DECODER_SAMPLE_COUNT",name)

    @unittest.skipUnless(FFMPEG and FFPROBE,"ffmpeg/ffprobe required")
    def test_pipeline_remains_audit_only_and_blocks_ambiguous_presentation_claims(self):
        rows={name:analyze_file(BASE/name,CFG_AUDIT,ROOT,FFMPEG,FFPROBE) for name in MANIFEST["cases"]}
        for name,row in rows.items():
            self.assertEqual(row.playability,"PLAYABLE",name);self.assertEqual(row.format_confidence,"MEDIUM",name)
            self.assertEqual(row.repair_execution,[],name);self.assertEqual(row.lossless_export,[],name)
            self.assertFalse(row.recovery_assessment["eligible"],name)
            self.assertTrue(any(x.get("code")=="MP4_AAC_SIMPLE_EDIT_PRESENTATION_AUTHORITY" for x in row.policy_decisions),name)
        self.assertEqual(sum(x.run_status=="SUCCESS" for x in rows.values()),2)
        self.assertEqual(sum(x.run_status=="SUCCESS_WITH_FINDINGS" for x in rows.values()),4)
        for name in ("02_edit_media_rate_unsupported.m4a","03_edit_media_range_outside_duration.m4a","04_movie_duration_mismatch.m4a","05_edit_timebase_inexact.m4a"):
            self.assertFalse(rows[name].canonical_presentation_window["determined"],name)
            self.assertEqual(rows[name].validity_domains["TIMELINE_VALIDITY"],"NONCONFORMANT_OR_INCOMPLETE_MEDIA_PRESENTATION",name)

    @unittest.skipUnless(FFMPEG and FFPROBE,"ffmpeg/ffprobe required")
    def test_markdown_exposes_audible_window_and_cp8_limits(self):
        row=analyze_file(BASE/"00_healthy_single_edit_44100_stereo.m4a",CFG_AUDIT,ROOT,FFMPEG,FFPROBE).to_dict()
        run={"run_id":"cp8","started_at":"2026-08-17T23:00:00-03:00","summary":{"discovered":1,"processed":1,"ok":1,"with_findings":0,"skipped":0,"failed":0,"repaired_outputs_created":0,"lossless_outputs_created":0,"outputs_reused":0,"candidates_rejected":0},"files":[row]}
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"report.md";write_md(path,run);text=path.read_text(encoding="utf-8")
            self.assertIn("Presentación canónica determinada `True`",text);self.assertIn("ventana multimedia `1024–53944`",text)
            self.assertIn("muestras de presentación `52920`",text);self.assertIn("Muestras de presentación canónica de FFmpeg: `52920`",text)
            self.assertIn("MP4_AAC_SIMPLE_EDIT_PRESENTATION_AUTHORITY",text);self.assertIn("la reparación y la recuperación PCM permanecen bloqueadas",text)


if __name__=="__main__":unittest.main()
