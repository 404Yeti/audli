# AUD-82: realtime transcription experiment (isolated)

Question: can streaming transcription (`gpt-live-transcribe`) cut the time from end of speech to a usable transcript, without changing who decides anything? Pip (ElevenLabs) still speaks every line through the production pipeline and the backend still owns evidence, scoring, adaptation, transcript gates and lesson progression.

**Isolation:** nothing in `app/` or `web/` imports this directory; it is not deployed, not in the Docker image, and uses no production account, database or learner data. Audio, manifests, raw results and the spend ledger live **outside the repository** (`~/audli-realtime-experiment`). Browser transcripts are treated as untrusted: the reference design is **dual-path**, where the authoritative transcript is the server-side REST result (arm A0) and streaming text is only an early hint.

## Files
| | |
|---|---|
| `verify_capabilities.py` | Free probe: models exist, session fields and `delay` values accepted, client-secret flow, browser-style WebSocket auth. |
| `corpus.py` | Builds the SYNTHETIC corpus (TTS with scripted pauses, hesitations, self-corrections, short answers, non-speech). |
| `run_experiment.py` | Runs A0 (current REST path), A1 (`gpt-live-transcribe`, several delay / endpointing settings), A2 (`gpt-transcribe`); writes a numbers-only report. |
| `vad.py`, `analysis.py`, `streaming.py`, `spend.py` | Detector port of `web/lib/hands-free.ts`, scoring, WebSocket client, spend ledger. |
| `browser/` | Safari / iPhone test page + local helper that mints short-lived client secrets (`SAFARI.md`). |
| `CONSENT.md` | Recording consent, handling, retention and deletion. |

## Run
```
pip install -r experiments/realtime-transcription/requirements.txt     # websockets (not a production dependency)
python experiments/realtime-transcription/verify_capabilities.py --env-file <env>
python experiments/realtime-transcription/corpus.py --env-file <env>
python experiments/realtime-transcription/run_experiment.py --env-file <env> --plan-only   # prints projected cost
python experiments/realtime-transcription/run_experiment.py --env-file <env>
```
Only `OPENAI_API_KEY` is read from the env file. Tests: `pytest tests/test_realtime_experiment.py`, `node --test tests/realtime-experiment.test.mjs`.

## Spend control
Hard cap **$5.00**. `spend.Ledger` persists every charge; a step is refused if its worst-case cost would push the estimated total past $4.50, and synthetic work cannot use the $2.50 reserved for the consented pilot. Prices and their provenance are in `spend.py` (`assumed` rows are deliberately high).

## Limits to keep in mind
* Synthetic speech cannot say anything about real Vietnamese-accented accuracy. It validates plumbing, latency, endpointing and safeguard mechanics.
* Replays run in real time on this machine's network, not on Render or an iPhone. Compare arms with each other, not with production absolutes.
* `gpt-live-transcribe` returns no confidence scores. See the report for what replaces the logprob gate.
