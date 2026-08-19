from __future__ import annotations

import copy,hashlib,json,os,shutil,tempfile,unittest
from pathlib import Path

from app.config import load_config
from app.external import decode_to_raw_file
from app.mp4_aac_preservation_hierarchy import ORDER
from app.mp4_aac_recovery import _decode_candidate
from app.pipeline import analyze_file
from formats.mp4_aac import analyze
from reporting.markdown_report import write_md

ROOT=Path(__file__).resolve().parents[1];BASE=ROOT/"samples"/"mp4_aac_cp9"
MAN=json.loads((ROOT/"samples"/"mp4_aac_cp16_manifest.json").read_text(encoding="utf-8"));CFG=load_config(ROOT/"config.toml");CFG_AUDIT=copy.deepcopy(CFG);CFG_AUDIT["repair"]["enabled"]=False;CFG_AUDIT["lossless_recovery"]["enabled"]=False
FFMPEG=os.environ.get("LOSSYDOCTOR_FFMPEG") or shutil.which("ffmpeg");FFPROBE=os.environ.get("LOSSYDOCTOR_FFPROBE") or shutil.which("ffprobe")
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def range_sha(path,start,length):return hashlib.sha256(Path(path).read_bytes()[start:start+length]).hexdigest()

@unittest.skipUnless(FFMPEG and FFPROBE,"ffmpeg/ffprobe required")
class Mp4PartialRecoveryProvenanceGateCP16(unittest.TestCase):
    def test_ambiguous_byte_and_terminal_overrun_have_distinct_blocking_evidence(self):
        for name,expected in MAN["cases"].items():
            row=analyze_file(BASE/name,CFG_AUDIT,ROOT,FFMPEG,FFPROBE);assessment=row.recovery_assessment
            self.assertEqual(sha(BASE/name),expected["sha256"],name);self.assertFalse(assessment["eligible"],name);self.assertFalse(assessment["publication_enabled"],name)
            self.assertEqual(assessment["reason"],expected["expected_reason"],name);self.assertTrue(assessment["partial_candidate_assessed"],name);self.assertFalse(assessment["candidate_origin_structurally_proven"],name);self.assertEqual(assessment["candidate_region_count"],0,name)
            self.assertEqual(assessment["declared_sample_bytes"],expected["declared_sample_bytes"],name);self.assertEqual(assessment["mdat_payload_bytes"],expected["mdat_payload_bytes"],name)

    def test_decoder_valid_fixture_prefix_still_grants_no_production_authority(self):
        name="03_sample_sizes_overrun_mdat.m4a";expected=MAN["cases"][name];source=BASE/name;parsed=analyze(source);track=parsed["facts"]["tracks"][0];description=track["sample_descriptions"][0];config=description["aac_config"];sizes=track["sample_tables"]["stsz"]["sizes"][:-1];payload_start=parsed["facts"]["mp4"]["mdat_payload_ranges"][0]["byte_start"]
        plan={"payload_start":payload_start,"sizes":sizes,"frequency_index":config["sampling_frequency_index"],"channel_configuration":config["channel_configuration"],"sample_rate":description["sample_rate"],"channels":description["channels"],"pcm_start":1024,"pcm_end":53248}
        with tempfile.TemporaryDirectory() as td:
            candidate=_decode_candidate(source,plan,FFMPEG,Path(td),CFG["app"]["external_timeout_seconds"]);healthy_raw=Path(td)/"healthy.s32le";decoded=decode_to_raw_file(ROOT/MAN["control"],healthy_raw,FFMPEG,CFG["app"]["external_timeout_seconds"])
            self.assertTrue(candidate["passed"]);self.assertTrue(decoded["passed"]);self.assertEqual(candidate["aac_access_unit_essence_sha256"],expected["expected_test_only_prefix_aac_essence_sha256"]);self.assertEqual(candidate["presentation_pcm_s32le_sha256"],expected["expected_test_only_prefix_pcm_s32le_sha256"])
            self.assertEqual(range_sha(healthy_raw,0,expected["expected_test_only_presentation_samples"]*8),candidate["presentation_pcm_s32le_sha256"])
        row=analyze_file(source,CFG_AUDIT,ROOT,FFMPEG,FFPROBE);self.assertFalse(row.recovery_assessment["eligible"]);self.assertEqual(row.recovery_assessment["candidate_region_count"],0)

    def test_default_pipeline_remains_report_only_and_preserves_source(self):
        with tempfile.TemporaryDirectory() as td:
            for name in MAN["cases"]:
                source=Path(td)/name;shutil.copy2(BASE/name,source);before=sha(source);files={p.name for p in source.parent.iterdir()};row=analyze_file(source,CFG,ROOT,FFMPEG,FFPROBE)
                self.assertEqual(sha(source),before,name);self.assertEqual(row.repair_execution,[],name);self.assertEqual(row.lossless_export,[],name);self.assertEqual(row.format_facts["mp4_aac_preservation_hierarchy"]["selected_tier"],ORDER[2],name);self.assertTrue(any(x.get("code")=="MP4_AAC_PARTIAL_RECOVERY_GATE_AUTHORITY" for x in row.policy_decisions),name);self.assertEqual({p.name for p in source.parent.iterdir()},files,name)

    def test_markdown_exposes_the_provenance_gate_in_plain_terms(self):
        row=analyze_file(BASE/"03_sample_sizes_overrun_mdat.m4a",CFG_AUDIT,ROOT,FFMPEG,FFPROBE).to_dict()
        with tempfile.TemporaryDirectory() as td:
            report=Path(td)/"report.md";write_md(report,{"run_id":"cp16","started_at":"2026-08-17T20:00:00-03:00","summary":{"discovered":1,"processed":1,"ok":0,"with_findings":1,"skipped":0,"failed":0,"repaired_outputs_created":0,"lossless_outputs_created":0,"outputs_reused":0,"candidates_rejected":0},"files":[row]});text=report.read_text(encoding="utf-8")
            self.assertIn("Puerta de recuperación parcial",text);self.assertIn("origen del fragmento probado estructuralmente `False`",text);self.assertIn("MP4_AAC_PARTIAL_RECOVERY_GATE_AUTHORITY",text);self.assertIn("permanec",text);self.assertIn("reporte",text)

if __name__=="__main__":unittest.main()
