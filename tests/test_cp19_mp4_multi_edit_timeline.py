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
from app.mp4_aac_repair import _simple_presentation
from app.pipeline import analyze_file
from formats.mp4_aac import _build_presentation_window,analyze
from reporting.markdown_report import write_md


ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/"samples"/"mp4_aac_cp19"
MANIFEST=json.loads((ROOT/"samples"/"mp4_aac_cp19_manifest.json").read_text(encoding="utf-8"))
CFG_AUDIT=copy.deepcopy(load_config(ROOT/"config.toml"));CFG_AUDIT["repair"]["enabled"]=False;CFG_AUDIT["lossless_recovery"]["enabled"]=False
FFMPEG=os.environ.get("LOSSYDOCTOR_FFMPEG") or shutil.which("ffmpeg")
FFPROBE=os.environ.get("LOSSYDOCTOR_FFPROBE") or shutil.which("ffprobe")


def _track(entries,media_duration=48000):
    return {"media_timescale":48000,"media_duration":media_duration,"sample_descriptions":[{"sample_rate":48000}],
        "edit_list":{"present":True,"entries":entries}}


def _media(duration,start,rate=(1,0)):
    return {"segment_duration_movie_units":duration,"media_time":start,"media_rate_integer":rate[0],"media_rate_fraction":rate[1]}


class Mp4MultiEditTimelineCP19(unittest.TestCase):
    def test_real_corpus_hashes_and_structural_results_are_fixed(self):
        self.assertEqual(MANIFEST["checkpoint"],"CP19");self.assertEqual(MANIFEST["authority"],"AUDIT_ONLY_NO_REPAIR_OR_RECOVERY")
        for name,expected in MANIFEST["cases"].items():
            path=BASE/name;self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),expected["sha256"],name)
            parsed=analyze(path);window=parsed["facts"]["tracks"][0]["presentation_window"]
            self.assertEqual([x.code for x in parsed["issues"]],expected["expected_issues"],name)
            self.assertEqual(window["determined"],expected["expected_window_determined"],name);self.assertEqual(window.get("reason"),expected["expected_window_reason"],name)
            self.assertEqual(window.get("presentation_sample_count"),expected["expected_presentation_sample_count"],name)

    def test_two_media_edits_preserve_order_and_exact_source_provenance(self):
        issues=[];track=_track([_media(12000,24000),_media(6000,0),_media(6000,12000)])
        _build_presentation_window(track,{"timescale":48000,"duration":24000},issues);window=track["presentation_window"]
        self.assertEqual(issues,[]);self.assertTrue(window["determined"]);self.assertFalse(window["intervention_authority"])
        self.assertEqual(window["presentation_model"],"MULTI_EDIT_PRESENTATION");self.assertEqual(window["presentation_sample_count"],24000)
        self.assertEqual([(x["media_start_units"],x["media_end_units"]) for x in window["presentation_segments"]],[(24000,36000),(0,6000),(12000,18000)])
        self.assertEqual([(x["presentation_sample_start"],x["presentation_sample_end"]) for x in window["presentation_segments"]],[(0,12000),(12000,18000),(18000,24000)])
        self.assertTrue(all(x["sample_provenance"]=="SOURCE_MEDIA_PCM" for x in window["presentation_segments"]))

    def test_empty_edit_is_explicit_silence_and_never_claimed_as_source_pcm(self):
        issues=[];track=_track([_media(4800,-1),_media(19200,9600)])
        _build_presentation_window(track,{"timescale":48000,"duration":24000},issues);window=track["presentation_window"];segments=window["presentation_segments"]
        self.assertEqual(issues,[]);self.assertTrue(window["determined"]);self.assertTrue(window["contains_empty_edits"])
        self.assertEqual((window["media_segment_count"],window["empty_segment_count"]),(1,1))
        self.assertEqual(segments[0]["kind"],"EMPTY");self.assertEqual(segments[0]["sample_provenance"],"EMPTY_EDIT_SILENCE_NOT_SOURCE_PCM")
        self.assertNotIn("media_start_units",segments[0]);self.assertEqual(segments[1]["source_sample_start"],9600)

    def test_multi_edit_timeline_cannot_reuse_simple_repair_authority(self):
        issues=[];track=_track([_media(24000,0),_media(24000,24000)])
        _build_presentation_window(track,{"timescale":48000,"duration":48000},issues)
        self.assertFalse(_simple_presentation(track));self.assertFalse(track["presentation_window"]["intervention_authority"])

    def test_invalid_negative_media_time_fails_closed(self):
        issues=[];track=_track([_media(48000,-2)])
        _build_presentation_window(track,{"timescale":48000,"duration":48000},issues)
        self.assertFalse(track["presentation_window"]["determined"]);self.assertEqual(track["presentation_window"]["reason"],"INVALID_MEDIA_RANGE")
        self.assertEqual([x.code for x in issues],["MP4_EDIT_LIST_MEDIA_RANGE_INVALID"])

    def test_zero_duration_edit_fails_closed(self):
        issues=[];track=_track([_media(0,0)])
        _build_presentation_window(track,{"timescale":48000,"duration":0},issues)
        self.assertFalse(track["presentation_window"]["determined"]);self.assertEqual([x.code for x in issues],["MP4_EDIT_LIST_MEDIA_RANGE_INVALID"])

    def test_non_unit_rate_still_fails_closed(self):
        issues=[];track=_track([_media(48000,0,(0,0))])
        _build_presentation_window(track,{"timescale":48000,"duration":48000},issues)
        self.assertFalse(track["presentation_window"]["determined"]);self.assertEqual([x.code for x in issues],["MP4_EDIT_LIST_RATE_UNSUPPORTED"])

    def test_inexact_media_sample_boundary_fails_closed(self):
        issues=[];track={"media_timescale":3,"media_duration":6,"sample_descriptions":[{"sample_rate":2}],"edit_list":{"present":True,"entries":[_media(1,1)]}}
        _build_presentation_window(track,{"timescale":1,"duration":1},issues)
        self.assertFalse(track["presentation_window"]["determined"]);self.assertEqual(track["presentation_window"]["reason"],"INEXACT_MEDIA_SAMPLE_BOUNDARY")
        self.assertEqual([x.code for x in issues],["MP4_EDIT_LIST_SAMPLE_COUNT_INEXACT"])

    def test_movie_duration_must_equal_all_edit_durations(self):
        issues=[];track=_track([_media(12000,0),_media(12000,12000)])
        _build_presentation_window(track,{"timescale":48000,"duration":48000},issues)
        self.assertFalse(track["presentation_window"]["determined"]);self.assertEqual(track["presentation_window"]["reason"],"MOVIE_DURATION_DISAGREES_WITH_EDITS")
        self.assertEqual([x.code for x in issues],["MP4_MOVIE_DURATION_MISMATCH"])

    @unittest.skipUnless(FFMPEG and FFPROBE,"ffmpeg/ffprobe required")
    def test_real_multi_edit_files_remain_report_only_after_cp20_provenance(self):
        valid=("00_two_contiguous_media_edits.m4a","01_three_reordered_media_edits.m4a","02_empty_then_media_edit.m4a")
        for name in valid:
            row=analyze_file(BASE/name,CFG_AUDIT,ROOT,FFMPEG,FFPROBE);audit=row.format_facts["mp4_aac_multi_edit_audit"]
            self.assertTrue(row.canonical_presentation_window["determined"],name);self.assertTrue(audit["structural_timeline_determined"],name)
            self.assertFalse(audit["decoder_sample_count_matches"],name);self.assertTrue(audit["segment_level_provenance_validated"],name);self.assertFalse(audit["intervention_authority"],name)
            self.assertIn("MP4_PRESENTATION_SAMPLE_COUNT_MISMATCH",[x.code for x in row.issues],name)
            self.assertEqual(row.validity_domains["DEMUX_BOUNDARY_VALIDITY"],"VALIDATED_DIRECT_MULTI_EDIT_SEGMENT_ACCESS_UNIT_PROVENANCE",name)
            self.assertEqual(row.validity_domains["TIMELINE_VALIDITY"],"VALIDATED_MULTI_EDIT_SEGMENT_PROVENANCE_AUDIT_ONLY",name)
            self.assertEqual(row.repair_plan,[],name);self.assertEqual(row.repair_execution,[],name);self.assertEqual(row.lossless_export,[],name)
            self.assertTrue(any(x.get("code")=="MP4_AAC_MULTI_EDIT_PRESENTATION_AUTHORITY" for x in row.policy_decisions),name)

    @unittest.skipUnless(FFMPEG and FFPROBE,"ffmpeg/ffprobe required")
    def test_report_exposes_ordered_segments_and_cp19_limit(self):
        row=analyze_file(BASE/"02_empty_then_media_edit.m4a",CFG_AUDIT,ROOT,FFMPEG,FFPROBE).to_dict()
        run={"run_id":"cp19","started_at":"2026-08-17T23:00:00-03:00","summary":{"discovered":1,"processed":1,"ok":0,"with_findings":1,"skipped":0,"failed":0,"repaired_outputs_created":0,"lossless_outputs_created":0,"outputs_reused":0,"candidates_rejected":0},"files":[row]}
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"report.md";write_md(path,run);text=path.read_text(encoding="utf-8")
            self.assertIn("Segmentos de presentación: `2`",text);self.assertIn("`EMPTY`",text);self.assertIn("silencio explícito de línea de tiempo (no es PCM fuente)",text)
            self.assertIn("Auditoría de múltiples ediciones",text);self.assertIn("procedencia de segmentos `True`",text);self.assertIn("MP4_AAC_MULTI_EDIT_PRESENTATION_AUTHORITY",text)


if __name__=="__main__":unittest.main()
