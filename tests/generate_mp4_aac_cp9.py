from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path


ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
CP6=ROOT/"samples"/"mp4_aac_cp6"
CP7=ROOT/"samples"/"mp4_aac_cp7"
OUT=ROOT/"samples"/"mp4_aac_cp9"
MANIFEST=ROOT/"samples"/"mp4_aac_cp9_manifest.json"


def boxes(data:bytes,start=0,end=None):
    end=len(data) if end is None else end;position=start
    while position+8<=end:
        size=int.from_bytes(data[position:position+4],"big");header=8
        if size==1:size=int.from_bytes(data[position+8:position+16],"big");header=16
        elif size==0:size=end-position
        if size<header or position+size>end:raise RuntimeError(f"invalid box at {position}")
        yield data[position+4:position+8],position,position+header,position+size
        position+=size


def find_path(data:bytes,*path:bytes):
    ranges=[(0,len(data))]
    for kind in path:
        found=[]
        for start,end in ranges:
            for box_kind,box_start,payload_start,box_end in boxes(data,start,end):
                if box_kind==kind:found.append((box_start,payload_start,box_end))
        if not found:raise RuntimeError(f"missing {kind!r}")
        ranges=[(payload,end) for _,payload,end in found]
    return found


def patch_u32(data:bytes,offset:int,value:int):
    changed=bytearray(data);changed[offset:offset+4]=value.to_bytes(4,"big");return bytes(changed)


def main():
    parser=argparse.ArgumentParser();parser.add_argument("--ffmpeg",required=True);parser.add_argument("--ffprobe",required=True);args=parser.parse_args()
    if OUT.exists() or MANIFEST.exists():raise SystemExit("CP9 corpus already exists; refusing to overwrite it")
    OUT.mkdir(parents=True)
    healthy=(CP6/"00_healthy_aac_lc_44100_stereo.m4a").read_bytes()
    broken=(CP6/"03_chunk_offset_outside_mdat.m4a").read_bytes()
    (OUT/"00_healthy_no_recovery_required.m4a").write_bytes(healthy)
    (OUT/"01_unique_mdat_wrong_offset_complete_clean.m4a").write_bytes(broken)

    mdat=find_path(broken,b"mdat")[0];box_start,payload_start,_=mdat
    size=int.from_bytes(broken[box_start:box_start+4],"big")
    extra=patch_u32(broken,box_start,size+1)
    extra=extra[:payload_start]+b"\x00"+extra[payload_start:]
    (OUT/"02_extra_byte_inside_mdat_ambiguous.m4a").write_bytes(extra)

    stsz=find_path(broken,b"moov",b"trak",b"mdia",b"minf",b"stbl",b"stsz")[0];count=int.from_bytes(broken[stsz[1]+8:stsz[1]+12],"big")
    final_size_offset=stsz[1]+12+(count-1)*4;final_size=int.from_bytes(broken[final_size_offset:final_size_offset+4],"big")
    (OUT/"03_sample_sizes_overrun_mdat.m4a").write_bytes(patch_u32(broken,final_size_offset,final_size+1))
    (OUT/"04_invalid_sample_description_reference.m4a").write_bytes((CP7/"05_sample_description_reference_invalid.m4a").read_bytes())

    from app.config import load_config
    from app.pipeline import analyze_file
    cfg=copy.deepcopy(load_config(ROOT/"config.toml"));cfg["repair"]["enabled"]=False;cfg["lossless_recovery"]["enabled"]=False
    cases={}
    for path in sorted(OUT.glob("*.m4a")):
        row=analyze_file(path,cfg,ROOT,args.ffmpeg,args.ffprobe);assessment=row.recovery_assessment
        cases[path.name]={"sha256":hashlib.sha256(path.read_bytes()).hexdigest(),"expected_playability":row.playability,
            "expected_pcm_class":row.pcm_recovery_class,"expected_eligible":assessment.get("eligible"),
            "expected_reason":assessment.get("reason"),"expected_issue_codes":[issue.code for issue in row.issues]}
    manifest={"checkpoint":"CP9","policy":"MP4_AAC_UNIQUE_MDAT_COMPLETE_CLEAN_ASSESSMENT",
        "authority":"ASSESSMENT_ONLY_NO_PUBLICATION","cases":cases}
    MANIFEST.write_text(json.dumps(manifest,indent=2,sort_keys=True)+"\n",encoding="utf-8",newline="\n")


if __name__=="__main__":main()
