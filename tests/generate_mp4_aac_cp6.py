from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
OUT = ROOT / "samples" / "mp4_aac_cp6"
MANIFEST = ROOT / "samples" / "mp4_aac_cp6_manifest.json"


def boxes(data:bytes,start=0,end=None):
    end=len(data) if end is None else end;p=start
    while p+8<=end:
        size32=int.from_bytes(data[p:p+4],"big");header=8
        if size32==1:
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
    q=bytearray(data);q[offset:offset+4]=value.to_bytes(4,"big");return bytes(q)


def generate_control(ffmpeg:str,path:Path,frequency:int,sample_rate:int,channels:int,duration:str):
    subprocess.run([
        ffmpeg,"-y","-hide_banner","-loglevel","error","-f","lavfi","-i",
        f"sine=frequency={frequency}:sample_rate={sample_rate}:duration={duration}",
        "-map_metadata","-1","-ac",str(channels),"-ar",str(sample_rate),
        "-c:a","aac","-b:a","128k","-movflags","+faststart",str(path),
    ],check=True)


def main():
    parser=argparse.ArgumentParser();parser.add_argument("--ffmpeg",required=True);args=parser.parse_args()
    expected={f"{n:02d}_{suffix}" for n,suffix in enumerate(("healthy_aac_lc_44100_stereo.m4a","healthy_aac_lc_48000_mono.m4a","stsz_sample_count_mismatch.m4a","chunk_offset_outside_mdat.m4a","media_duration_mismatch.m4a","trailing_unknown_bytes.m4a"))}
    if MANIFEST.exists():raise SystemExit("CP6 manifest already exists; refusing to overwrite the corpus")
    if OUT.exists():
        present={x.name for x in OUT.glob("*.m4a")}
        if present!=expected:raise SystemExit(f"incomplete or unexpected CP6 corpus: {sorted(present)}")
    else:
        OUT.mkdir(parents=True)
        stereo=OUT/"00_healthy_aac_lc_44100_stereo.m4a";mono=OUT/"01_healthy_aac_lc_48000_mono.m4a"
        generate_control(args.ffmpeg,stereo,997,44100,2,"1.2")
        generate_control(args.ffmpeg,mono,613,48000,1,"0.8")
        source=stereo.read_bytes()

        stsz=find_path(source,b"moov",b"trak",b"mdia",b"minf",b"stbl",b"stsz")[0]
        stsz_count_offset=stsz[1]+8;stsz_count=int.from_bytes(source[stsz_count_offset:stsz_count_offset+4],"big")
        (OUT/"02_stsz_sample_count_mismatch.m4a").write_bytes(patch_u32(source,stsz_count_offset,stsz_count-1))

        stco=find_path(source,b"moov",b"trak",b"mdia",b"minf",b"stbl",b"stco")[0]
        first_chunk_offset=stco[1]+8
        (OUT/"03_chunk_offset_outside_mdat.m4a").write_bytes(patch_u32(source,first_chunk_offset,len(source)+4096))

        mdhd=find_path(source,b"moov",b"trak",b"mdia",b"mdhd")[0];version=source[mdhd[1]]
        duration_offset=mdhd[1]+(24 if version==1 else 16);duration_size=8 if version==1 else 4
        duration=int.from_bytes(source[duration_offset:duration_offset+duration_size],"big")
        q=bytearray(source);q[duration_offset:duration_offset+duration_size]=(duration+1024).to_bytes(duration_size,"big")
        (OUT/"04_media_duration_mismatch.m4a").write_bytes(q)

        (OUT/"05_trailing_unknown_bytes.m4a").write_bytes(source+b"CP6!!")

    from formats.mp4_aac import analyze
    cases={}
    for path in sorted(OUT.glob("*.m4a")):
        parsed=analyze(path);track=parsed["facts"]["tracks"][0]
        cases[path.name]={
            "sha256":hashlib.sha256(path.read_bytes()).hexdigest(),
            "expected_issues":[issue.code for issue in parsed["issues"]],
            "expected_playability":"UNPLAYABLE" if path.name.startswith("03_") else "PLAYABLE",
            "expected_run_status":"SUCCESS" if not parsed["issues"] else "SUCCESS_WITH_FINDINGS",
            "expected_track_id":track["track_id"],
            "expected_timescale":track["media_timescale"],
            "expected_sample_count":(track["sample_tables"].get("stsz") or {}).get("sample_count"),
        }
    manifest={"checkpoint":"CP6","policy":"MP4_AAC_STRUCTURAL_AUDIT_ONLY_NO_REPAIR_OR_RECOVERY","authority":"AUDIT_ONLY_NO_REPAIR_OR_RECOVERY","cases":cases}
    MANIFEST.write_text(json.dumps(manifest,indent=2,sort_keys=True)+"\n",encoding="utf-8",newline="\n")


if __name__=="__main__":main()
