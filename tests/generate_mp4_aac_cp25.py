from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path


ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
SOURCE=ROOT/"samples"/"mp4_aac_cp21"/"00_healthy_five_fragments.m4a"
OUT=ROOT/"samples"/"mp4_aac_cp25"
MANIFEST=ROOT/"samples"/"mp4_aac_cp25_manifest.json"


def boxes(data:bytes,start=0,end=None):
    end=len(data) if end is None else end;p=start
    while p+8<=end:
        size=int.from_bytes(data[p:p+4],"big")
        if size<8 or p+size>end:raise RuntimeError(f"invalid box at {p}")
        yield {"kind":data[p+4:p+8],"start":p,"payload":p+8,"end":p+size};p+=size


def children(data:bytes,node:dict,kind:bytes):return [x for x in boxes(data,node["payload"],node["end"]) if x["kind"]==kind]


def one(data:bytes,node:dict,kind:bytes):
    found=children(data,node,kind)
    if len(found)!=1:raise RuntimeError(f"expected one {kind!r}")
    return found[0]


def box(kind:bytes,payload:bytes):return (len(payload)+8).to_bytes(4,"big")+kind+payload


def edit(segment_duration:int,media_time:int,rate=(1,0)):
    return (segment_duration.to_bytes(4,"big")+media_time.to_bytes(4,"big",signed=True)+
        rate[0].to_bytes(2,"big",signed=True)+rate[1].to_bytes(2,"big",signed=True))


def add_edit_list(source:bytes,entries:list[bytes]):
    top={x["kind"]:x for x in boxes(source)};moov=top[b"moov"];trak=one(source,moov,b"trak");mvhd=one(source,moov,b"mvhd")
    if children(source,trak,b"edts"):raise RuntimeError("source already has edts")
    elst=box(b"elst",b"\0\0\0\0"+len(entries).to_bytes(4,"big")+b"".join(entries));addition=box(b"edts",elst);delta=len(addition)
    changed=bytearray(source[:trak["end"]]+addition+source[trak["end"]:])
    for start in (moov["start"],trak["start"]):changed[start:start+4]=(int.from_bytes(source[start:start+4],"big")+delta).to_bytes(4,"big")
    duration=sum(int.from_bytes(entry[:4],"big") for entry in entries);changed[mvhd["payload"]+16:mvhd["payload"]+20]=duration.to_bytes(4,"big")
    return bytes(changed)


def patch_ambiguous_fragment_base(data:bytes):
    moof=next(x for x in boxes(data) if x["kind"]==b"moof");traf=one(data,moof,b"traf");tfhd=one(data,traf,b"tfhd")
    flags=int.from_bytes(data[tfhd["payload"]+1:tfhd["payload"]+4],"big");changed=bytearray(data)
    changed[tfhd["payload"]+1:tfhd["payload"]+4]=(flags&~0x020000).to_bytes(3,"big");return bytes(changed)


def main():
    parser=argparse.ArgumentParser();parser.add_argument("--ffmpeg",default=shutil.which("ffmpeg"));args=parser.parse_args()
    if not args.ffmpeg:raise SystemExit("ffmpeg not found")
    if OUT.exists() or MANIFEST.exists():raise SystemExit("CP25 corpus already exists; refusing to overwrite it")
    OUT.mkdir(parents=True);source=SOURCE.read_bytes()
    cases={
        "00_fragmented_two_contiguous_media_edits.m4a":[edit(26460,1024),edit(26460,27484)],
        "01_fragmented_three_reordered_media_edits.m4a":[edit(17640,18664),edit(17640,1024),edit(17640,36304)],
        "02_fragmented_empty_then_media_edit.m4a":[edit(4410,-1),edit(48510,1024)],
        "03_fragmented_second_edit_rate_unsupported.m4a":[edit(26460,1024),edit(26460,27484,(0,0))],
        "04_fragmented_second_edit_outside_media.m4a":[edit(26460,1024),edit(26460,40000)],
    }
    for name,entries in cases.items():(OUT/name).write_bytes(add_edit_list(source,entries))
    healthy=(OUT/"00_fragmented_two_contiguous_media_edits.m4a").read_bytes()
    (OUT/"05_fragmented_edit_with_ambiguous_data_base.m4a").write_bytes(patch_ambiguous_fragment_base(healthy))

    from app.external import decode_to_raw_file
    from app.mp4_aac_timeline import audit
    from formats.mp4_aac import analyze
    recorded={}
    for path in sorted(OUT.glob("*.m4a")):
        parsed=analyze(path);track=parsed["facts"]["tracks"][0];window=track["presentation_window"];fragment=parsed["facts"]["fragmented_mp4"]
        recorded[path.name]={"sha256":hashlib.sha256(path.read_bytes()).hexdigest(),"expected_issues":[issue.code for issue in parsed["issues"]],
            "expected_mapping_complete":fragment["mapping_complete"],"expected_window_determined":window["determined"],
            "expected_window_model":window.get("presentation_model"),"expected_window_reason":window.get("reason"),
            "expected_presentation_sample_count":window.get("presentation_sample_count")}
        if window["determined"]:
            timeline=audit(path,track,args.ffmpeg)
            with tempfile.TemporaryDirectory() as directory:
                raw=Path(directory)/"direct.s32le";decoded=decode_to_raw_file(path,raw,args.ffmpeg,300)
                if not timeline.get("validated") or not decoded.get("passed"):raise RuntimeError(f"PCM evidence failed for {path.name}")
                channels=track["sample_descriptions"][0]["channels"]
                recorded[path.name].update({"expected_canonical_pcm_sha256":timeline["canonical_presentation_pcm_s32le_sha256"],
                    "expected_aac_essence_sha256":timeline["aac_access_unit_essence_sha256"],
                    "expected_direct_decoder_samples":raw.stat().st_size//(channels*4),
                    "expected_direct_decoder_pcm_sha256":hashlib.sha256(raw.read_bytes()).hexdigest(),
                    "expected_segment_pcm_sha256":[segment["pcm_sha256"] for segment in timeline["segments"]]})
    manifest={"checkpoint":"CP25","policy":"MP4_AAC_FRAGMENTED_EDIT_LIST_PRESENTATION","authority":"EXACT_COMBINED_PROVENANCE_REQUIRED",
        "source":SOURCE.relative_to(ROOT).as_posix(),"cases":recorded}
    MANIFEST.write_text(json.dumps(manifest,indent=2,sort_keys=True)+"\n",encoding="utf-8",newline="\n")


if __name__=="__main__":main()
