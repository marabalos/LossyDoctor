from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path


ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
SOURCE=ROOT/"samples"/"mp4_aac_cp8"/"00_healthy_single_edit_44100_stereo.m4a"
OUT=ROOT/"samples"/"mp4_aac_cp21"
MANIFEST=ROOT/"samples"/"mp4_aac_cp21_manifest.json"


def boxes(data:bytes,start=0,end=None):
    end=len(data) if end is None else end;p=start
    while p+8<=end:
        size=int.from_bytes(data[p:p+4],"big")
        if size<8 or p+size>end:raise RuntimeError(f"invalid box at {p}")
        yield {"kind":data[p+4:p+8],"start":p,"payload":p+8,"end":p+size};p+=size


def child(data:bytes,node:dict,kind:bytes):
    found=[x for x in boxes(data,node["payload"],node["end"]) if x["kind"]==kind]
    if len(found)!=1:raise RuntimeError(f"expected one {kind!r}")
    return found[0]


def patch(data:bytes,offset:int,width:int,value:int,signed=False):
    changed=bytearray(data);changed[offset:offset+width]=value.to_bytes(width,"big",signed=signed);return bytes(changed)


def main():
    parser=argparse.ArgumentParser();parser.add_argument("--ffmpeg",default=shutil.which("ffmpeg"));args=parser.parse_args()
    if not args.ffmpeg:raise SystemExit("ffmpeg not found")
    if OUT.exists() or MANIFEST.exists():raise SystemExit("CP21 corpus already exists; refusing to overwrite it")
    OUT.mkdir(parents=True);healthy=OUT/"00_healthy_five_fragments.m4a"
    command=[args.ffmpeg,"-v","error","-n","-i",str(SOURCE),"-map","0:a:0","-c:a","copy","-movflags","empty_moov+default_base_moof","-frag_duration","250000","-f","mp4",str(healthy)]
    subprocess.run(command,check=True);data=healthy.read_bytes();moofs=[x for x in boxes(data) if x["kind"]==b"moof"]
    if len(moofs)!=5:raise RuntimeError("expected five deterministic fragments")
    second_mfhd=child(data,moofs[1],b"mfhd");sequence=int.from_bytes(data[second_mfhd["payload"]+4:second_mfhd["payload"]+8],"big")
    (OUT/"01_fragment_sequence_gap.m4a").write_bytes(patch(data,second_mfhd["payload"]+4,4,sequence+1))
    first_traf=child(data,moofs[0],b"traf");first_trun=child(data,first_traf,b"trun");offset=int.from_bytes(data[first_trun["payload"]+8:first_trun["payload"]+12],"big",signed=True)
    (OUT/"02_first_run_data_offset_plus_one.m4a").write_bytes(patch(data,first_trun["payload"]+8,4,offset+1,signed=True))
    second_traf=child(data,moofs[1],b"traf");second_tfdt=child(data,second_traf,b"tfdt");base=int.from_bytes(data[second_tfdt["payload"]+4:second_tfdt["payload"]+12],"big")
    (OUT/"03_second_fragment_decode_time_gap.m4a").write_bytes(patch(data,second_tfdt["payload"]+4,8,base+1))
    first_tfhd=child(data,first_traf,b"tfhd");flags=int.from_bytes(data[first_tfhd["payload"]+1:first_tfhd["payload"]+4],"big")
    (OUT/"04_ambiguous_fragment_data_base.m4a").write_bytes(patch(data,first_tfhd["payload"]+1,3,flags&~0x020000))

    from formats.mp4_aac import analyze
    cases={}
    for path in sorted(OUT.glob("*.m4a")):
        parsed=analyze(path);fragment=parsed["facts"]["fragmented_mp4"];track=parsed["facts"]["tracks"][0];window=track["presentation_window"]
        cases[path.name]={"sha256":hashlib.sha256(path.read_bytes()).hexdigest(),"expected_issues":[x.code for x in parsed["issues"]],
            "expected_fragment_count":fragment["fragment_count"],"expected_mapping_complete":fragment["mapping_complete"],
            "expected_window_determined":window["determined"],"expected_window_reason":window.get("reason")}
    manifest={"checkpoint":"CP21","policy":"MP4_AAC_FRAGMENTED_MP4_AUDIT_ONLY","authority":"AUDIT_ONLY_NO_REPAIR_OR_RECOVERY",
        "generator":{"ffmpeg_arguments":["-v","error","-n","-i","<source>","-map","0:a:0","-c:a","copy","-movflags","empty_moov+default_base_moof","-frag_duration","250000","-f","mp4","<output>"],
            "source":SOURCE.relative_to(ROOT).as_posix()},"cases":cases}
    MANIFEST.write_text(json.dumps(manifest,indent=2,sort_keys=True)+"\n",encoding="utf-8",newline="\n")


if __name__=="__main__":main()
