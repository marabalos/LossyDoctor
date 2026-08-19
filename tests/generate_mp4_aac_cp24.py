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
OUT=ROOT/"samples"/"mp4_aac_cp24"
MANIFEST=ROOT/"samples"/"mp4_aac_cp24_manifest.json"


def main():
    parser=argparse.ArgumentParser();parser.add_argument("--ffmpeg",default=shutil.which("ffmpeg"));args=parser.parse_args()
    if not args.ffmpeg:raise SystemExit("ffmpeg not found")
    if OUT.exists() or MANIFEST.exists():raise SystemExit("CP24 corpus already exists; refusing to overwrite it")
    OUT.mkdir(parents=True);target=OUT/"00_two_aac_audio_tracks.m4a"
    command=[args.ffmpeg,"-v","error","-n","-i",str(SOURCE),"-map","0:a:0","-map","0:a:0","-c","copy",str(target)]
    subprocess.run(command,check=True)
    from formats.identify import identify
    from formats.mp4_aac import analyze
    parsed=analyze(target);identification=parsed["facts"]["identification"];identified=identify(target)
    manifest={"checkpoint":"CP24","policy":"MP4_AAC_MULTIPLE_AUDIO_TRACKS_INCOMPATIBLE","authority":"EXPLICIT_UNSUPPORTED_NO_OUTPUT",
        "generator":{"ffmpeg_arguments":["-v","error","-n","-i","<source>","-map","0:a:0","-map","0:a:0","-c","copy","<output>"],"source":SOURCE.relative_to(ROOT).as_posix()},
        "cases":{target.name:{"sha256":hashlib.sha256(target.read_bytes()).hexdigest(),"audio_track_count":identification["audio_track_count"],
            "aac_track_count":identification["aac_track_count"],"supported":identification["supported"],"skip_reason":identified["reason"]}}}
    MANIFEST.write_text(json.dumps(manifest,indent=2,sort_keys=True)+"\n",encoding="utf-8",newline="\n")


if __name__=="__main__":main()
