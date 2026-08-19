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
SOURCE=ROOT/"samples"/"mp4_aac_cp21"/"00_healthy_five_fragments.m4a"
MANIFEST=json.loads((ROOT/"samples"/"mp4_aac_cp22_manifest.json").read_text(encoding="utf-8"))
EXPECTED=MANIFEST["cases"][SOURCE.name]
CFG_AUDIT=copy.deepcopy(load_config(ROOT/"config.toml"));CFG_AUDIT["repair"]["enabled"]=False;CFG_AUDIT["lossless_recovery"]["enabled"]=False
FFMPEG=os.environ.get("LOSSYDOCTOR_FFMPEG") or shutil.which("ffmpeg")
FFPROBE=os.environ.get("LOSSYDOCTOR_FFPROBE") or shutil.which("ffprobe")


class FragmentedPcmProvenanceCP22(unittest.TestCase):
    @unittest.skipUnless(FFMPEG,"ffmpeg required")
    def test_independent_aac_decode_proves_exact_fragment_presentation(self):
        before=hashlib.sha256(SOURCE.read_bytes()).hexdigest();self.assertEqual(before,EXPECTED["source_sha256"])
        result=audit(SOURCE,analyze(SOURCE)["facts"]["tracks"][0],FFMPEG)
        self.assertTrue(result["validated"]);self.assertEqual(result["policy"],MANIFEST["policy"]);self.assertFalse(result["intervention_authority"])
        for key in ("aac_access_unit_count","aac_access_unit_essence_sha256","decoded_media_sample_count","decoded_tail_padding_samples_excluded","presentation_sample_count","canonical_presentation_pcm_s32le_sha256"):
            expected_key="aac_essence_sha256" if key=="aac_access_unit_essence_sha256" else key
            self.assertEqual(result[key],EXPECTED[expected_key],key)
        self.assertEqual(result["segments"][0]["source_access_unit_count"],53);self.assertEqual(result["segments"][0]["presentation_sample_count"],53944)
        self.assertEqual(hashlib.sha256(SOURCE.read_bytes()).hexdigest(),before)

    @unittest.skipUnless(FFMPEG,"ffmpeg required")
    def test_direct_container_decode_prefix_independently_matches_canonical_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            raw=Path(directory)/"fragmented.s32le";decoded=decode_to_raw_file(SOURCE,raw,FFMPEG,300);self.assertTrue(decoded["passed"])
            data=raw.read_bytes();frame_bytes=2*4;self.assertEqual(len(data)//frame_bytes,54272)
            canonical=data[:53944*frame_bytes];self.assertEqual(hashlib.sha256(canonical).hexdigest(),EXPECTED["canonical_presentation_pcm_s32le_sha256"])
            self.assertEqual((len(data)-len(canonical))//frame_bytes,328)

    @unittest.skipUnless(FFMPEG and FFPROBE,"ffmpeg/ffprobe required")
    def test_pipeline_exposes_provenance_but_keeps_all_outputs_blocked(self):
        row=analyze_file(SOURCE,CFG_AUDIT,ROOT,FFMPEG,FFPROBE);evidence=row.format_facts["mp4_aac_fragmented_audit"]
        self.assertTrue(evidence["presentation_pcm_provenance_validated"]);self.assertEqual(evidence["canonical_presentation_pcm_s32le_sha256"],EXPECTED["canonical_presentation_pcm_s32le_sha256"])
        self.assertEqual(evidence["decoded_tail_padding_samples_excluded"],328);self.assertFalse(evidence["intervention_authority"])
        self.assertEqual(row.validity_domains["TIMELINE_VALIDITY"],"VALIDATED_FRAGMENTED_PRESENTATION_PCM_PROVENANCE_AUDIT_ONLY")
        self.assertEqual(row.repair_plan,[]);self.assertEqual(row.repair_execution,[]);self.assertEqual(row.lossless_export,[])
        self.assertTrue(any(x.get("code")=="MP4_AAC_FRAGMENTED_PCM_PROVENANCE_AUTHORITY" for x in row.policy_decisions))


if __name__=="__main__":unittest.main()
