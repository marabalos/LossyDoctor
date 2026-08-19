from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
SOURCE = ROOT / "samples" / "mp4_aac_cp6"
OUT = ROOT / "samples" / "mp4_aac_cp7"
MANIFEST = ROOT / "samples" / "mp4_aac_cp7_manifest.json"


def boxes(data:bytes,start=0,end=None):
    end=len(data) if end is None else end;p=start
    while p+8<=end:
        size32=int.from_bytes(data[p:p+4],"big");header=8
        if size32==1:
            if p+16>end:raise RuntimeError(f"truncated extended-size box at {p}")
            size=int.from_bytes(data[p+8:p+16],"big");header=16
        elif size32==0:size=end-p
        else:size=size32
        if size<header or p+size>end:raise RuntimeError(f"invalid box at {p}")
        yield data[p+4:p+8],p,p+header,p+size
        p+=size


def find_path(data:bytes,*path:bytes):
    ranges=[(0,len(data))]
    for kind in path:
        found=[]
        for start,end in ranges:
            for box_kind,box_start,payload_start,box_end in boxes(data,start,end):
                if box_kind==kind:found.append((box_start,payload_start,box_end))
        if not found:raise RuntimeError(f"missing box path component {kind!r}")
        ranges=[(payload,end) for _,payload,end in found]
    return found


def patch_u32(data:bytes,offset:int,value:int):
    changed=bytearray(data);changed[offset:offset+4]=value.to_bytes(4,"big");return bytes(changed)


def main():
    argparse.ArgumentParser().parse_args()
    if MANIFEST.exists() or OUT.exists():raise SystemExit("CP7 corpus already exists; refusing to overwrite it")
    OUT.mkdir(parents=True)
    stereo=(SOURCE/"00_healthy_aac_lc_44100_stereo.m4a").read_bytes()
    mono=(SOURCE/"01_healthy_aac_lc_48000_mono.m4a").read_bytes()
    (OUT/"00_healthy_aac_lc_44100_stereo.m4a").write_bytes(stereo)
    (OUT/"01_healthy_aac_lc_48000_mono.m4a").write_bytes(mono)

    stsc=find_path(stereo,b"moov",b"trak",b"mdia",b"minf",b"stbl",b"stsc")[0]
    samples_per_chunk_offset=stsc[1]+12
    samples_per_chunk=int.from_bytes(stereo[samples_per_chunk_offset:samples_per_chunk_offset+4],"big")
    (OUT/"02_stsc_sample_coverage_mismatch.m4a").write_bytes(patch_u32(stereo,samples_per_chunk_offset,samples_per_chunk-1))

    stsz=find_path(stereo,b"moov",b"trak",b"mdia",b"minf",b"stbl",b"stsz")[0]
    sample_count=int.from_bytes(stereo[stsz[1]+8:stsz[1]+12],"big")
    final_size_offset=stsz[1]+12+(sample_count-1)*4
    final_size=int.from_bytes(stereo[final_size_offset:final_size_offset+4],"big")
    (OUT/"03_access_unit_extent_outside_mdat.m4a").write_bytes(patch_u32(stereo,final_size_offset,final_size+4096))

    stts=find_path(stereo,b"moov",b"trak",b"mdia",b"minf",b"stbl",b"stts")[0]
    first_timing_count_offset=stts[1]+8
    first_timing_count=int.from_bytes(stereo[first_timing_count_offset:first_timing_count_offset+4],"big")
    (OUT/"04_access_unit_timeline_incomplete.m4a").write_bytes(patch_u32(stereo,first_timing_count_offset,first_timing_count-1))

    description_index_offset=stsc[1]+16
    (OUT/"05_sample_description_reference_invalid.m4a").write_bytes(patch_u32(stereo,description_index_offset,2))

    from formats.mp4_aac import analyze
    cases={}
    for path in sorted(OUT.glob("*.m4a")):
        parsed=analyze(path);track=parsed["facts"]["tracks"][0];provenance=track["access_unit_provenance"]
        cases[path.name]={"sha256":hashlib.sha256(path.read_bytes()).hexdigest(),
            "expected_issues":[issue.code for issue in parsed["issues"]],
            "expected_playability":"UNPLAYABLE" if path.name.startswith("05_") else "PLAYABLE",
            "expected_run_status":"SUCCESS" if not parsed["issues"] else "SUCCESS_WITH_FINDINGS",
            "expected_declared_samples":provenance["sample_count_declared"],
            "expected_mapped_samples":provenance["mapped_sample_count"],
            "expected_hashed_samples":provenance["hashed_sample_count"],
            "expected_mapping_complete":provenance["mapping_complete"],
            "expected_decode_timeline_complete":provenance["decode_timeline_complete"]}
    manifest={"checkpoint":"CP7","policy":"MP4_AAC_ACCESS_UNIT_PROVENANCE_AUDIT_ONLY_NO_REPAIR_OR_RECOVERY",
        "authority":"AUDIT_ONLY_NO_REPAIR_OR_RECOVERY","cases":cases}
    MANIFEST.write_text(json.dumps(manifest,indent=2,sort_keys=True)+"\n",encoding="utf-8",newline="\n")


if __name__=="__main__":main()
