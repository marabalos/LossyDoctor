# LossyDoctor 0.1.0 — example report excerpt

> v0.1 is read-only. No input file was repaired, renamed, moved, transcoded, or modified.

## 01 - Intro.mp3

**Run status:** `SUCCESS`

- Detected: `MPEG_AUDIO` / `mp3`
- Format confidence: `HIGH`
- Playability: `PLAYABLE`
- PCM recovery assessment: `NOT_ASSESSED`
- Strict decode: `PASS`
- Playback decode: `PASS`

No relevant anomaly was detected by the v0.1 policy.

**Final classification:** `OK`

## 02 - Wrong extension.wma

**Run status:** `SUCCESS_WITH_FINDINGS`

- Detected: `MPEG_AUDIO` / `mp3`
- Format confidence: `HIGH`
- Playability: `PLAYABLE`

**Findings**

- `EXTENSION_CONTENT_MISMATCH`: filename says `.wma`; content is coherent MPEG Layer III.

**Final classification:** `ANOMALY_UNCHANGED`

No renamed copy is created in v0.1.

## 03 - Truncated.mp3

**Run status:** `SUCCESS_WITH_FINDINGS`

- Detected: `MPEG_AUDIO` / `mp3`
- Playability: `PLAYABLE`
- PCM recovery assessment: `AMBIGUOUS`

**Findings**

- `TRUNCATED_MPEG_FRAME`: final frame extends beyond the physical end of the file.

The anomaly is reported only. v0.1 does not trim, pad or rebuild it.
