from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import unittest
from pathlib import Path

from app.config import load_config
from app.pipeline import analyze_file


ROOT=Path(__file__).resolve().parents[1]
MANIFEST=json.loads((ROOT/"samples"/"aac_adts_cp26_manifest.json").read_text(encoding="utf-8"))
CFG=load_config(ROOT/"config.toml");CFG_AUDIT=copy.deepcopy(CFG);CFG_AUDIT["repair"]["enabled"]=False;CFG_AUDIT["lossless_recovery"]["enabled"]=False
FFMPEG=os.environ.get("LOSSYDOCTOR_FFMPEG") or shutil.which("ffmpeg")
FFPROBE=os.environ.get("LOSSYDOCTOR_FFPROBE") or shutil.which("ffprobe")


@unittest.skipUnless(FFMPEG and FFPROBE,"ffmpeg/ffprobe required")
class AacAdtsPcmProvenanceCP26(unittest.TestCase):
    def test_fixed_sources_have_exact_full_stream_pcm_presentations(self):
        for name,expected in MANIFEST["cases"].items():
            source=ROOT/expected["source"];self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),expected["source_sha256"],name)
            row=analyze_file(source,CFG_AUDIT,ROOT,FFMPEG,FFPROBE);evidence=row.format_facts["adts_timeline_evidence"]
            self.assertTrue(evidence["validated"],name);self.assertTrue(evidence["presentation_exact"],name)
            self.assertEqual((evidence["presentation_sample_count"],evidence["canonical_pcm_s32le_sha256"]),(expected["presentation_samples"],expected["canonical_pcm_sha256"]),name)
            self.assertEqual(row.validity_domains["TIMELINE_VALIDITY"],"VALIDATED_EXACT_CONTIGUOUS_ADTS_PCM_PRESENTATION",name)
            self.assertEqual(row.repair_execution,[],name);self.assertEqual(row.lossless_export,[],name)
            self.assertTrue(any(x.get("code")=="AAC_ADTS_PCM_PRESENTATION_AUTHORITY" for x in row.policy_decisions),name)

    def test_crc_scope_limit_remains_explicit_and_blocks_recovery_authority(self):
        expected=MANIFEST["cases"]["single_rdb_crc_scope_deferred"]
        row=analyze_file(ROOT/expected["source"],CFG_AUDIT,ROOT,FFMPEG,FFPROBE);evidence=row.format_facts["adts_timeline_evidence"]
        self.assertTrue(evidence["presentation_exact"]);self.assertTrue(evidence["crc_protected_frames_present"])
        self.assertFalse(evidence["crc_payload_authentication_complete"]);self.assertEqual(evidence["pcm_recovery_authority"],"NONE")
        self.assertEqual(evidence["payload_integrity_scope"],"CRC_SCOPE_INCOMPLETE_NO_RECOVERY_AUTHORITY")

    def test_damaged_discontinuous_and_heterogeneous_sources_remain_inexact(self):
        sources=(ROOT/"samples/aac_adts_v43/04_truncated_final_frame.aac",ROOT/"samples/aac_adts_v43/05_midstream_parameter_change.aac",
            ROOT/"samples/aac_adts_v43/06_interframe_sync_gap.aac",ROOT/"samples/aac_adts_crc_v44/03_multi_rdb_header_crc_mismatch.aac",
            ROOT/"samples/aac_adts_crc_v44/04_multi_rdb_position_invalid.aac")
        for source in sources:
            row=analyze_file(source,CFG_AUDIT,ROOT,FFMPEG,FFPROBE);evidence=row.format_facts["adts_timeline_evidence"]
            self.assertFalse(evidence["validated"],source.name);self.assertFalse(evidence["presentation_exact"],source.name)
            self.assertEqual(evidence["pcm_recovery_authority"],"NONE",source.name);self.assertEqual(row.lossless_export,[],source.name)


if __name__=="__main__":unittest.main()
