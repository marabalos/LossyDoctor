from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
SOURCE = ROOT / "samples" / "mp4_aac_cp7"
OUT = ROOT / "samples" / "mp4_aac_cp8"
MANIFEST = ROOT / "samples" / "mp4_aac_cp8_manifest.json"


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


def patch_integer(data:bytes,offset:int,width:int,value:int,signed=False):
    changed=bytearray(data);changed[offset:offset+width]=value.to_bytes(width,"big",signed=signed);return bytes(changed)


def main():
    argparse.ArgumentParser().parse_args()
    if MANIFEST.exists() or OUT.exists():raise SystemExit("CP8 corpus already exists; refusing to overwrite it")
    OUT.mkdir(parents=True)
    stereo=(SOURCE/"00_healthy_aac_lc_44100_stereo.m4a").read_bytes();mono=(SOURCE/"01_healthy_aac_lc_48000_mono.m4a").read_bytes()
    (OUT/"00_healthy_single_edit_44100_stereo.m4a").write_bytes(stereo)
    (OUT/"01_healthy_single_edit_48000_mono.m4a").write_bytes(mono)

    elst=find_path(stereo,b"moov",b"trak",b"edts",b"elst")[0];elst_payload=elst[1]
    (OUT/"02_edit_media_rate_unsupported.m4a").write_bytes(patch_integer(stereo,elst_payload+16,2,2,signed=True))
    (OUT/"03_edit_media_range_outside_duration.m4a").write_bytes(patch_integer(stereo,elst_payload+12,4,2048,signed=True))

    mvhd=find_path(stereo,b"moov",b"mvhd")[0];mvhd_payload=mvhd[1]
    movie_duration=int.from_bytes(stereo[mvhd_payload+16:mvhd_payload+20],"big")
    (OUT/"04_movie_duration_mismatch.m4a").write_bytes(patch_integer(stereo,mvhd_payload+16,4,movie_duration-1))
    movie_timescale=int.from_bytes(stereo[mvhd_payload+12:mvhd_payload+16],"big")
    (OUT/"05_edit_timebase_inexact.m4a").write_bytes(patch_integer(stereo,mvhd_payload+12,4,movie_timescale+1))

    from formats.mp4_aac import analyze
    cases={}
    for path in sorted(OUT.glob("*.m4a")):
        parsed=analyze(path);track=parsed["facts"]["tracks"][0];window=track["presentation_window"]
        cases[path.name]={"sha256":hashlib.sha256(path.read_bytes()).hexdigest(),
            "expected_issues":[issue.code for issue in parsed["issues"]],
            "expected_window_determined":window["determined"],"expected_window_reason":window.get("reason")}
    manifest={"checkpoint":"CP8","policy":"MP4_AAC_SIMPLE_EDIT_LIST_PRESENTATION_AUDIT_ONLY_NO_REPAIR_OR_RECOVERY",
        "authority":"AUDIT_ONLY_NO_REPAIR_OR_RECOVERY","cases":cases}
    MANIFEST.write_text(json.dumps(manifest,indent=2,sort_keys=True)+"\n",encoding="utf-8",newline="\n")


if __name__=="__main__":main()
