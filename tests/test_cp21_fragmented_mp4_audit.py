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


ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/"samples"/"mp4_aac_cp21"
MANIFEST=json.loads((ROOT/"samples"/"mp4_aac_cp21_manifest.json").read_text(encoding="utf-8"))
CFG_AUDIT=copy.deepcopy(load_config(ROOT/"config.toml"));CFG_AUDIT["repair"]["enabled"]=False;CFG_AUDIT["lossless_recovery"]["enabled"]=False
FFMPEG=os.environ.get("LOSSYDOCTOR_FFMPEG") or shutil.which("ffmpeg")
FFPROBE=os.environ.get("LOSSYDOCTOR_FFPROBE") or shutil.which("ffprobe")


class FragmentedMp4AuditCP21(unittest.TestCase):
    def test_corpus_hashes_and_fail_closed_results_are_fixed(self):
        self.assertEqual(MANIFEST["checkpoint"],"CP21");self.assertEqual(MANIFEST["authority"],"AUDIT_ONLY_NO_REPAIR_OR_RECOVERY")
        for name,expected in MANIFEST["cases"].items():
            path=BASE/name;self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),expected["sha256"],name)
            parsed=analyze(path);fragment=parsed["facts"]["fragmented_mp4"];window=parsed["facts"]["tracks"][0]["presentation_window"]
            self.assertEqual([x.code for x in parsed["issues"]],expected["expected_issues"],name)
            self.assertEqual(fragment["fragment_count"],expected["expected_fragment_count"],name);self.assertEqual(fragment["mapping_complete"],expected["expected_mapping_complete"],name)
            self.assertEqual(window["determined"],expected["expected_window_determined"],name);self.assertEqual(window["reason"],expected["expected_window_reason"],name)

    def test_healthy_fragment_runs_map_every_aac_byte_and_decode_time(self):
        path=BASE/"00_healthy_five_fragments.m4a";parsed=analyze(path);facts=parsed["facts"];track=facts["tracks"][0];provenance=track["access_unit_provenance"];units=provenance["access_units"]
        self.assertEqual(parsed["issues"],[]);self.assertTrue(facts["mp4"]["fragmented"]);self.assertEqual(facts["fragmented_mp4"]["sequence_numbers"],[1,2,3,4,5])
        self.assertEqual((facts["fragmented_mp4"]["fragment_count"],len(facts["fragmented_mp4"]["runs"])),(5,5))
        self.assertTrue(provenance["mapping_complete"]);self.assertEqual((len(units),provenance["decode_end_units"]),(53,53944));self.assertEqual(units[-1]["duration_units"],696)
        data=path.read_bytes();essence=hashlib.sha256()
        for unit in units:
            payload=data[unit["byte_start"]:unit["byte_end"]];self.assertEqual(hashlib.sha256(payload).hexdigest(),unit["sha256"]);essence.update(payload)
        self.assertEqual(essence.hexdigest(),"c35e51f676aa80415f9a36872e66c4495cd0f9715ef098550ec7210522452ab3")
        window=track["presentation_window"];self.assertTrue(window["determined"]);self.assertEqual(window["presentation_model"],"FRAGMENTED_NORMAL_RATE_MEDIA_TIMELINE");self.assertEqual(window["presentation_sample_count"],53944);self.assertFalse(window["intervention_authority"])

    @unittest.skipUnless(FFMPEG and FFPROBE,"ffmpeg/ffprobe required")
    def test_pipeline_validates_fragment_mapping_but_grants_no_intervention(self):
        row=analyze_file(BASE/"00_healthy_five_fragments.m4a",CFG_AUDIT,ROOT,FFMPEG,FFPROBE);audit=row.format_facts["mp4_aac_fragmented_audit"]
        self.assertEqual(row.playability,"PLAYABLE");self.assertTrue(audit["access_unit_mapping_complete"]);self.assertEqual((audit["fragment_count"],audit["fragment_run_count"],audit["access_unit_count"]),(5,5,53))
        self.assertFalse(audit["decoder_sample_count_matches"]);self.assertFalse(audit["intervention_authority"]);self.assertIn("MP4_PRESENTATION_SAMPLE_COUNT_MISMATCH",[x.code for x in row.issues])
        self.assertEqual(row.validity_domains["SAMPLE_TABLE_VALIDITY"],"VALIDATED_FRAGMENT_RUN_ACCESS_UNIT_MAPPING")
        self.assertEqual(row.validity_domains["DEMUX_BOUNDARY_VALIDITY"],"VALIDATED_DIRECT_SAMPLE_TO_FFPROBE_PACKET_IDENTITY")
        self.assertEqual(row.validity_domains["TIMELINE_VALIDITY"],"VALIDATED_FRAGMENTED_PRESENTATION_PCM_PROVENANCE_AUDIT_ONLY")
        self.assertEqual(row.validity_domains["SEEKABILITY_VALIDITY"],"VALIDATED_FRAGMENT_RUN_ADDRESSING")
        self.assertEqual(row.repair_plan,[]);self.assertEqual(row.repair_execution,[]);self.assertEqual(row.lossless_export,[])
        self.assertTrue(any(x.get("code")=="MP4_AAC_FRAGMENTED_AUDIT_AUTHORITY" for x in row.policy_decisions))

    @unittest.skipUnless(FFMPEG and FFPROBE,"ffmpeg/ffprobe required")
    def test_invalid_fragments_never_gain_mapping_or_timeline_authority(self):
        for name in tuple(MANIFEST["cases"])[1:]:
            row=analyze_file(BASE/name,CFG_AUDIT,ROOT,FFMPEG,FFPROBE);audit=row.format_facts["mp4_aac_fragmented_audit"]
            self.assertFalse(audit["access_unit_mapping_complete"],name);self.assertFalse(audit["presentation_determined"],name);self.assertFalse(audit["intervention_authority"],name)
            self.assertEqual(row.validity_domains["TIMELINE_VALIDITY"],"NONCONFORMANT_OR_INCOMPLETE_MEDIA_PRESENTATION",name)
            self.assertEqual(row.repair_plan,[],name);self.assertEqual(row.lossless_export,[],name)

    @unittest.skipUnless(FFMPEG and FFPROBE,"ffmpeg/ffprobe required")
    def test_report_exposes_fragment_counts_and_audit_limit(self):
        row=analyze_file(BASE/"00_healthy_five_fragments.m4a",CFG_AUDIT,ROOT,FFMPEG,FFPROBE).to_dict()
        run={"run_id":"cp21","started_at":"2026-08-17T23:00:00-03:00","summary":{"discovered":1,"processed":1,"ok":0,"with_findings":1,"skipped":0,"failed":0,"repaired_outputs_created":0,"lossless_outputs_created":0,"outputs_reused":0,"candidates_rejected":0},"files":[row]}
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"report.md";write_md(path,run);text=path.read_text(encoding="utf-8")
            self.assertIn("Auditoría de MP4 fragmentado: fragmentos `5`",text);self.assertIn("unidades AAC `53`",text);self.assertIn("intervención `False`",text);self.assertIn("MP4_AAC_FRAGMENTED_AUDIT_AUTHORITY",text)


if __name__=="__main__":unittest.main()
