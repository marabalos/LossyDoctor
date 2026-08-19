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
from app.mp4_aac_timeline_export import assess
from app.pipeline import analyze_file


ROOT=Path(__file__).resolve().parents[1]
MANIFEST=json.loads((ROOT/"samples"/"mp4_aac_cp23_manifest.json").read_text(encoding="utf-8"))
CFG=load_config(ROOT/"config.toml");CFG["repair"]["enabled"]=False
CFG_AUDIT=copy.deepcopy(CFG);CFG_AUDIT["app"]["mode"]="audit_only"
FFMPEG=os.environ.get("LOSSYDOCTOR_FFMPEG") or shutil.which("ffmpeg")
FFPROBE=os.environ.get("LOSSYDOCTOR_FFPROBE") or shutil.which("ffprobe")


def sha256(path:Path):return hashlib.sha256(path.read_bytes()).hexdigest()
def outputs(row):return [output for export in row.lossless_export for output in export.get("outputs",[])]


@unittest.skipUnless(FFMPEG and FFPROBE,"ffmpeg/ffprobe required")
class CanonicalTimelineFlacCP23(unittest.TestCase):
    def _copy_cases(self,directory:Path):
        copied={}
        for name,expected in MANIFEST["cases"].items():
            source=ROOT/expected["source"];target=directory/name;shutil.copy2(source,target);copied[name]=target
        return copied

    def test_proven_pcm_difference_creates_verified_flac_and_reuses_it(self):
        with tempfile.TemporaryDirectory() as directory:
            temp=Path(directory);sources=self._copy_cases(temp);originals={name:sha256(path) for name,path in sources.items()}
            first={name:analyze_file(path,CFG,ROOT,FFMPEG,FFPROBE) for name,path in sources.items()}
            for name,row in first.items():
                expected=MANIFEST["cases"][name];created=outputs(row);self.assertEqual((row.run_status,row.final_status),("SUCCESS_WITH_RECOVERY",["RECOVERED_LOSSLESS"]),name);self.assertEqual(len(created),1,name)
                output=created[0];self.assertEqual(output["status"],"CREATED",name);manifest=output["manifest"]
                self.assertEqual((manifest["derivation_kind"],manifest["materialization"]),("RECOVERED_LOSSLESS",MANIFEST["materialization"]),name)
                self.assertEqual((manifest["sample_count"],manifest["source_canonical_pcm_sha256"]),(expected["presentation_samples"],expected["canonical_pcm_sha256"]),name)
                self.assertEqual((manifest["direct_decoder_sample_count"],manifest["direct_decoder_pcm_s32le_sha256"]),(expected["direct_decoder_samples"],expected["direct_decoder_pcm_sha256"]),name)
                self.assertEqual(manifest["source_canonical_pcm_sha256"],manifest["flac_decoded_pcm_sha256"],name);self.assertFalse(manifest["aac_access_unit_bytes_modified"],name)
                self.assertEqual((manifest["resampling"],manifest["channel_remix"],manifest["audio_recoding"]),("NONE","NONE","LOSSLESS_FLAC_ONLY"),name)
                flac=Path(output["output_path"]);sidecar=Path(output["manifest_path"]);self.assertTrue(flac.exists() and sidecar.exists(),name)
                with tempfile.TemporaryDirectory() as decoded_directory:
                    raw=Path(decoded_directory)/"output.s32le";decoded=decode_to_raw_file(flac,raw,FFMPEG,300);self.assertTrue(decoded["passed"],name);self.assertEqual(sha256(raw),expected["canonical_pcm_sha256"],name)
                self.assertEqual(row.format_facts["mp4_aac_preservation_hierarchy"]["selected_tier"],"TIER_2_COMPLETE_CLEAN_LOSSLESS_RECOVERY",name)
            silence=outputs(first["02_empty_then_media_edit.m4a"])[0]["manifest"]["synthesized_gap_silence"]
            self.assertEqual(silence,[{"segment_index":0,"presentation_sample_start":0,"presentation_sample_end":4410,"sample_count":4410}])
            self.assertTrue(all(not outputs(first[name])[0]["manifest"]["synthesized_gap_silence"] for name in first if name!="02_empty_then_media_edit.m4a"))
            self.assertEqual(originals,{name:sha256(path) for name,path in sources.items()});files_after={x.name for x in temp.iterdir()}
            second={name:analyze_file(path,CFG,ROOT,FFMPEG,FFPROBE) for name,path in sources.items()}
            self.assertTrue(all(len(outputs(row))==1 and outputs(row)[0]["status"]=="REUSED" for row in second.values()))
            self.assertEqual(files_after,{x.name for x in temp.iterdir()});self.assertEqual(originals,{name:sha256(path) for name,path in sources.items()})

    def test_existing_target_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            temp=Path(directory);source=temp/"source.m4a";shutil.copy2(ROOT/MANIFEST["cases"]["01_three_reordered_media_edits.m4a"]["source"],source)
            desired=temp/"source [canonical-lossless].flac";sentinel=b"UNRELATED EXISTING FILE";desired.write_bytes(sentinel)
            row=analyze_file(source,CFG,ROOT,FFMPEG,FFPROBE);created=outputs(row);self.assertEqual(len(created),1);self.assertEqual(desired.read_bytes(),sentinel)
            self.assertEqual(Path(created[0]["output_path"]).name,"source [canonical-lossless 2].flac")

    def test_equal_direct_pcm_creates_no_authority_or_output(self):
        timeline={"segment_level_provenance_validated":True,"presentation_sample_count":100,"canonical_presentation_pcm_s32le_sha256":"a"*64}
        decision=assess(timeline,{"completed":True,"sample_frames":100,"pcm_sha256":"a"*64})
        self.assertFalse(decision["eligible"]);self.assertFalse(decision["publication_enabled"]);self.assertEqual(decision["reason"],"DIRECT_PRESENTATION_ALREADY_EQUALS_PROVEN_CANONICAL_PCM")

    def test_audit_mode_reports_eligibility_but_creates_nothing(self):
        source=ROOT/MANIFEST["cases"]["00_two_contiguous_media_edits.m4a"]["source"];row=analyze_file(source,CFG_AUDIT,ROOT,FFMPEG,FFPROBE)
        self.assertTrue(row.recovery_assessment["eligible"]);self.assertEqual(row.lossless_export,[]);self.assertTrue(any(x.get("code")=="MP4_AAC_CANONICAL_TIMELINE_EXPORT_AUTHORITY" for x in row.policy_decisions))

    def test_invalid_complex_and_fragmented_sources_create_nothing(self):
        cases=(ROOT/"samples/mp4_aac_cp19/03_second_edit_rate_unsupported.m4a",ROOT/"samples/mp4_aac_cp21/04_ambiguous_fragment_data_base.m4a")
        for source in cases:
            row=analyze_file(source,CFG,ROOT,FFMPEG,FFPROBE);self.assertEqual(outputs(row),[],source.name);self.assertFalse(row.recovery_assessment.get("eligible"),source.name)


if __name__=="__main__":unittest.main()
