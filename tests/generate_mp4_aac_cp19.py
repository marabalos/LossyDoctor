from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
SOURCE = ROOT / "samples" / "mp4_aac_cp8" / "00_healthy_single_edit_44100_stereo.m4a"
OUT = ROOT / "samples" / "mp4_aac_cp19"
MANIFEST = ROOT / "samples" / "mp4_aac_cp19_manifest.json"


def boxes(data:bytes,start=0,end=None):
    end=len(data) if end is None else end;p=start
    while p+8<=end:
        size=int.from_bytes(data[p:p+4],"big")
        if size<8 or p+size>end:raise RuntimeError(f"invalid box at {p}")
        yield data[p+4:p+8],p,p+8,p+size;p+=size


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


def edit(segment_duration:int,media_time:int,rate=(1,0)):
    return (segment_duration.to_bytes(4,"big")+media_time.to_bytes(4,"big",signed=True)+
        rate[0].to_bytes(2,"big",signed=True)+rate[1].to_bytes(2,"big",signed=True))


def replace_edit_list(source:bytes,entries:list[bytes]):
    moov=find_path(source,b"moov")[0];trak=find_path(source,b"moov",b"trak")[0];edts=find_path(source,b"moov",b"trak",b"edts")[0]
    elst=find_path(source,b"moov",b"trak",b"edts",b"elst")[0];stco=find_path(source,b"moov",b"trak",b"mdia",b"minf",b"stbl",b"stco")[0]
    payload=b"\0\0\0\0"+len(entries).to_bytes(4,"big")+b"".join(entries);replacement=(8+len(payload)).to_bytes(4,"big")+b"elst"+payload
    changed=bytearray(source[:elst[0]]+replacement+source[elst[2]:]);delta=len(replacement)-(elst[2]-elst[0])
    for start in (moov[0],trak[0],edts[0]):changed[start:start+4]=(int.from_bytes(source[start:start+4],"big")+delta).to_bytes(4,"big")
    old_offset=int.from_bytes(source[stco[1]+8:stco[1]+12],"big");new_field=stco[1]+delta+8
    changed[new_field:new_field+4]=(old_offset+delta).to_bytes(4,"big")
    return bytes(changed)


def main():
    argparse.ArgumentParser().parse_args()
    if MANIFEST.exists() or OUT.exists():raise SystemExit("CP19 corpus already exists; refusing to overwrite it")
    OUT.mkdir(parents=True);source=SOURCE.read_bytes()
    cases={
        "00_two_contiguous_media_edits.m4a":[edit(26460,1024),edit(26460,27484)],
        "01_three_reordered_media_edits.m4a":[edit(17640,18664),edit(17640,1024),edit(17640,36304)],
        "02_empty_then_media_edit.m4a":[edit(4410,-1),edit(48510,1024)],
        "03_second_edit_rate_unsupported.m4a":[edit(26460,1024),edit(26460,27484,(0,0))],
        "04_second_edit_outside_media.m4a":[edit(26460,1024),edit(26460,40000)],
    }
    for name,entries in cases.items():(OUT/name).write_bytes(replace_edit_list(source,entries))

    from formats.mp4_aac import analyze
    recorded={}
    for path in sorted(OUT.glob("*.m4a")):
        parsed=analyze(path);window=parsed["facts"]["tracks"][0]["presentation_window"]
        recorded[path.name]={"sha256":hashlib.sha256(path.read_bytes()).hexdigest(),
            "expected_issues":[issue.code for issue in parsed["issues"]],"expected_window_determined":window["determined"],
            "expected_window_reason":window.get("reason"),"expected_presentation_sample_count":window.get("presentation_sample_count")}
    manifest={"checkpoint":"CP19","policy":"MP4_AAC_MULTI_EDIT_PRESENTATION_AUDIT_ONLY",
        "authority":"AUDIT_ONLY_NO_REPAIR_OR_RECOVERY","cases":recorded}
    MANIFEST.write_text(json.dumps(manifest,indent=2,sort_keys=True)+"\n",encoding="utf-8",newline="\n")


if __name__=="__main__":main()
