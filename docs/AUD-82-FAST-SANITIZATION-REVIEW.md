# AUD-82 — Fast tutor-audio sanitization: security review

Status: **review for owner decision. The feature is OFF (`AUDLI_TUTOR_AUDIO_FAST_SANITIZE` unset) and must stay off until approved.**

## What changes

`TutorTTSProvider.attempt` currently runs `sanitize_generated_audio`: ffmpeg decodes the provider MP3 and re-encodes it with all metadata dropped. The flagged path instead copies the MPEG frames unchanged (`-c:a copy`, no ID3v2/ID3v1/Xing) and then requires the output to be an unbroken chain of Layer III frames (`mpeg_layer3_frames_cover`). Any failure falls back to the full re-encode. Passages (`sanitize_generated_audio` in `main.py`, `default_passage.py`, `accent_practice.py`, `tutor_preview.py`) are not touched.

## Why it matters (measured)

* Production, 39 tutor syntheses: post-synthesis time (sanitize + validate) p50 **507 ms**, p90 1,139 ms, max 2,102 ms, against 254 ms median synthesis.
* Local CPU, synthetic 6 s MP3: re-encode 131 ms CPU, stream-copy 11 ms CPU (`scripts/benchmark_sanitization.py`). Cheaper re-encode settings (mono, 22–24 kHz, VBR) save only ~20% CPU, so they are not a substitute.

## Guarantees compared

| Guarantee | Re-encode (today) | Strip + frame check (proposed) |
|---|---|---|
| ID3v2/ID3v1/APE/other tags and container metadata removed | yes | yes (tested with an embedded secret) |
| Trailing bytes or data between frames | removed | rejected (falls back to re-encode) |
| Output is valid MP3 | yes | yes, structurally (frame headers/lengths) |
| Every frame decodes without error | implied by decode | **not checked** (the browser decodes at playback) |
| Hidden bits **inside** a frame (ancillary data, bit-reservoir padding) | destroyed | **preserved** |
| Duration cap | `-t` | `-t` |

## Requirements it is judged against (AGENTS.md)

1. *Never expose scripts or expected answers before a successfully evaluated spoken attempt.* The sanitizer's purpose is to stop a hidden script riding in generated audio. Tutor cues (check-ins, follow-up questions, feedback) contain only text the app already shows or is about to speak; the server gate on `feedback` cues is unchanged. Exercise **passages**, whose script is the protected asset, keep the full re-encode. The in-frame channel would need a malicious or compromised TTS provider to exploit and could only carry text the tutor already speaks.
2. *Validate audio size, type, signature, decodability and duration.* This applies to **learner uploads** (`validate_upload`, unchanged). Generated tutor audio still passes size, signature and structure checks, but not a full decode.

## Assessment

For tutor speech from ElevenLabs the required guarantee (no hidden-script channel in metadata) is preserved; two defence-in-depth properties are weakened (in-frame bits, full-decode check). I do not consider that a violation of a stated requirement, but it is a judgement the owner should make. Passages must never use this path.

## Options

1. **Keep off** (default). Keep today's behaviour; take the CPU benefit from the Starter instance only.
2. **Enable for tutor speech only** after the owner accepts the residual risk. Compare `sanitize_ms` (new field in `Tutor TTS` logs, `sanitization=strip|reencode`) before and after; roll back by unsetting the flag.
3. **Strip for first playback, full re-encode in the background** before the clip is cached for replay. Removes most of the wait while ensuring any stored/replayed copy is fully sanitized; the first play still carries the residual risk, so this is a smaller change in risk than it looks. Not implemented.

## Verification

`tests/test_audio.py` (tags stripped, copy is byte-identical audio, trailing/garbage/empty rejected, MPEG-1 and MPEG-2 rates) and `tests/test_tutor_tts.py` (flag selects the path, tag stripped, fallback to re-encode when the strip refuses). Full backend suite: 1,737 passed, 44 skipped.
