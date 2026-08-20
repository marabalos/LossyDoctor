# LossyDoctor

[English](README.md) | [Español](README.es.md) | [中文](README.zh-CN.md) | [Русский](README.ru.md) | [हिन्दी](README.hi.md)

---

## First: what *lossy* means

MP3, AAC, Vorbis, Opus, and other *lossy* formats reduce file size by irreversibly discarding audio information.

For that reason, **a lossy file should NEVER be used as a master source, a preservation format, or an interchange format**. Re-encoding it to another lossy format only adds another generation of loss. Re-encoding it to a lossless format only increases file size without restoring the data that was already discarded.

Nevertheless, a vast amount of music, recordings, broadcasts, bootlegs, historical files, and digitally distributed material **exists only—or circulates only—in lossy formats**.

LossyDoctor exists for those cases.

**It is a tool designed for lossy collections, but built with an audiophile mindset and a conservative methodology: preserve as much as possible of what still exists, without degrading it again in order to “repair” it.**

## What is LossyDoctor?

LossyDoctor audits lossy audio files to detect corruption, structural anomalies, bitstream problems, timeline issues, and decoding failures.

Its fundamental principle is simple:

> **Never re-encode with loss, not even to repair.**

If a file can be corrected while preserving its original compressed audio, LossyDoctor creates a repaired copy and verifies it.

If that is no longer possible, but the genuinely recoverable PCM can still be established exactly, LossyDoctor can preserve it as **lossless FLAC**. The resulting file will be larger, but it introduces no additional loss: it preserves exactly that recovered PCM, making playable an audio file that otherwise would not be.

The original always remains untouched.

## What it does

- Identifies the format from the file contents, not only from the extension.
- Audits structure, bitstream, timeline, and decoding.
- Detects corrupted files even when they can still be played.
- Repairs only when a demonstrable correction exists.
- Always preserves the original compressed bitstream.
- Verifies every repaired file again.
- Can preserve genuinely recoverable PCM as FLAC when repairing the original is no longer safe.
- Processes individual files or entire collections.
- Never modifies or overwrites the source file.

Version 1.1.0 covers, within the proven authority for each family, MPEG Layer II/III, AAC/ADTS, single-track MP4/AAC, Ogg/Opus, Ogg/Vorbis, and ASF/WMA.

## What it does NOT do

- **It does not re-encode MP3 to MP3, AAC to AAC, or perform any repair through new lossy compression.**
- It does not improve sound quality lost during the original encoding.
- It does not invent or reconstruct audio whose existence cannot be demonstrated.
- It does not consider a file healthy merely because a decoder can play it.
- It does not promise to repair every kind of corruption.
- It does not automatically convert every problematic file to FLAC: lossless recovery is a preservation alternative when the original bitstream can no longer be preserved correctly without re-encoding.

## What kinds of problems can it find?

A file may still contain recoverable audio and yet exhibit glitches, incomplete playback, incorrect duration, seeking problems, or even be unreadable by certain players.

Among other cases, LossyDoctor can detect:

- truncated MPEG frames or loss of synchronization;
- inconsistent headers or Xing/Info or VBRI indexes;
- unexpected bytes or incorrect padding;
- CRC errors;
- corrupted or out-of-sequence Ogg pages;
- timestamp or continuity problems;
- incorrect MP4/AAC tables, offsets, or durations;
- inconsistent ADTS headers;
- incomplete ASF/WMA packets or fragments.

**Detecting a problem does not automatically mean it can be repaired.**

When a single safe repair exists, LossyDoctor can apply it. When the bitstream can no longer be preserved but demonstrably genuine PCM still exists, it can recover that audio without introducing a new generation of loss. When neither can be proven, it reports the damage and does not fabricate a solution.

## Use cases

**Legacy collections**  
Audit thousands of MP3, AAC, WMA, Vorbis, or Opus files accumulated over many years and originating from different sources.

**Files with glitches**  
Determine whether a skip, dropout, or playback failure comes from a repairable structural anomaly or from audio that is actually missing.

**Files that no longer play correctly**  
Try to preserve the original bitstream when a demonstrable repair exists or, in extreme cases, rescue genuine PCM that can still be established with certainty.

**Preservation**  
Verify lossy material before adding it to a permanent collection, without degrading it through another generation of compression.

LossyDoctor does not try to be the repair tool that modifies the largest number of files.

Its goal is different:

> **Preserve everything authentic that still exists in a lossy file, without introducing another generation of loss. Repair when the repair can be demonstrated. Recover to lossless when that is the only safe alternative. And leave untouched what cannot be determined with certainty.**
