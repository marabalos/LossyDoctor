from __future__ import annotations

import copy
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from app.aac_adts_preservation_hierarchy import ORDER,POLICY,resolve
from app.config import load_config
from app.pipeline import analyze_file


ROOT=Path(__file__).resolve().parents[1];BASE=ROOT/"samples/aac_adts_v43";CFG=load_config(ROOT/"config.toml");CFG_NO_REPAIR=copy.deepcopy(CFG);CFG_NO_REPAIR["repair"]["enabled"]=False
FFMPEG=os.environ.get("LOSSYDOCTOR_FFMPEG") or shutil.which("ffmpeg");FFPROBE=os.environ.get("LOSSYDOCTOR_FFPROBE") or shutil.which("ffprobe")


@unittest.skipUnless(FFMPEG and FFPROBE,"ffmpeg/ffprobe required")
class AacAdtsPreservationHierarchyCP29(unittest.TestCase):
    def test_pipeline_selects_repair_flac_report_and_no_action_exclusively(self):
        with tempfile.TemporaryDirectory() as directory:
            temp=Path(directory);repair=temp/"repair.aac";flac=temp/"flac.aac";shutil.copy2(BASE/"02_invalid_sampling_index.aac",repair);shutil.copy2(BASE/"02_invalid_sampling_index.aac",flac)
            rows=(analyze_file(repair,CFG,ROOT,FFMPEG,FFPROBE),analyze_file(flac,CFG_NO_REPAIR,ROOT,FFMPEG,FFPROBE),
                analyze_file(BASE/"04_truncated_final_frame.aac",CFG,ROOT,FFMPEG,FFPROBE),analyze_file(BASE/"00_healthy_aac_lc_44100_stereo.aac",CFG,ROOT,FFMPEG,FFPROBE))
            self.assertEqual([row.format_facts["aac_adts_preservation_hierarchy"]["selected_tier"] for row in rows],[ORDER[0],ORDER[1],ORDER[2],"NO_ACTION_REQUIRED"])
            self.assertTrue(all(row.format_facts["aac_adts_preservation_hierarchy"]["exclusive_outcome"] for row in rows))
            self.assertTrue(all(any(x.get("code")=="AAC_ADTS_PRESERVATION_HIERARCHY" for x in row.policy_decisions) for row in rows))

    def test_unknown_unverified_and_competing_families_fail_closed(self):
        repair=[{"status":"CREATED","repair_spec_id":"AAC_ADTS_REWRITE_UNIQUE_INVALID_SAMPLING_INDEX","manifest":{"derivation_kind":"REPAIRED_SAFE","validation_result":"PASS","verification":{"passed":True,"strict_decode":"PASS","frame_to_demux_packet_identity":True}}}]
        recovery=[{"outputs":[{"status":"CREATED","manifest":{"derivation_kind":"RECOVERED_LOSSLESS","materialization":"AAC_ADTS_COMPLETE_CLEAN_FROM_PROVEN_HEADER_REPAIR"}}]}]
        self.assertEqual(resolve(repair,recovery,{},"PLAYABLE",{"X"})["policy_violation"],"MULTIPLE_AAC_ADTS_PRESERVATION_TIERS_PUBLISHED_SIMULTANEOUSLY")
        bad=copy.deepcopy(repair);bad[0]["repair_spec_id"]="UNKNOWN";self.assertEqual(resolve(bad,[],{},"PLAYABLE",{"X"})["policy_violation"],"UNKNOWN_OR_UNVERIFIED_AAC_ADTS_REPAIR_FAMILY")
        bad=copy.deepcopy(recovery);bad[0]["outputs"][0]["manifest"]["materialization"]="UNKNOWN";self.assertEqual(resolve([],bad,{},"PLAYABLE",{"X"})["policy_violation"],"UNKNOWN_AAC_ADTS_PRESERVATION_DERIVATION_FAMILY")

    def test_second_run_reuses_one_selected_tier_without_new_files(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/"source.aac";shutil.copy2(BASE/"02_invalid_sampling_index.aac",source);first=analyze_file(source,CFG,ROOT,FFMPEG,FFPROBE);files={x.name for x in source.parent.iterdir()};second=analyze_file(source,CFG,ROOT,FFMPEG,FFPROBE)
            self.assertEqual(first.format_facts["aac_adts_preservation_hierarchy"]["status_counts"],{"CREATED":1,"REUSED":0})
            self.assertEqual(second.format_facts["aac_adts_preservation_hierarchy"]["status_counts"],{"CREATED":0,"REUSED":1});self.assertEqual(files,{x.name for x in source.parent.iterdir()})


if __name__=="__main__":unittest.main()
