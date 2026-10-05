# Recording lifecycle fix

Confirmed defect: the preview rendered `src=""` between setting the Blob and creating its object URL. The empty-source warning alone does not establish a failed transcription upload. A visible playable recording indicates captured audio; inspect the upload status to locate any downstream failure.

The client now collects the final chunk before constructing the Blob, chooses supported recorder MIME types, falls back to chunk MIME, validates upload size/type, uploads the original Blob with a matching filename, retains it after failed transcription, and clears it only after a usable transcription response. Preview URLs belong to their exact Blob and are revoked on replacement/unmount. Failed microphone acquisition/start preserves the previous recording. Disposed recorder callbacks cannot publish stale audio. Both media elements require a valid source.

Development-only Console diagnostics (`[Audli recording]`, enable Verbose) report recording/chunks/Blob metadata, preview lifecycle, upload status, and evaluation progress. They contain no audio bytes or transcript contents. Production omits them.

## Commands and results

- `source .venv/bin/activate && pytest -q`: 87 passed.
- `node --experimental-strip-types tests/recording.test.mjs`: 11 passed.
- `node --experimental-strip-types tests/frontend.test.mjs`: 6 passed.
- `cd web && npm run typecheck`: passed.
- `cd web && npm run build`: failed twice inside Next.js with `Could not parse output from TypeScript's --showConfig`. Direct `npm exec tsc -- --showConfig` succeeds.

Node helper tests use mocked recorder/URL/fetch implementations. They do not verify actual microphone capture, codec compatibility, or the real OpenAI loop. No browser automation tool is available here; launching Windows Chrome fails with WSL `UtilBindVsockAnyPort: socket failed`.

## Chrome manual verification

1. Run `.venv/bin/uvicorn app.main:app --reload --host 127.0.0.1 --port 8000` and, in another terminal, `cd web && npm run dev`. Open localhost:3000. Enable DevTools Console Verbose and Network Preserve log.
2. Play a generated exercise to the end. Record a 15-second summary, stop, and play “Your recording.” Expect nonzero Blob size, supported MIME, object URL created, and local playback diagnostics; no empty-src warning.
3. Click **Transcribe my summary**. Expect multipart POST `/api/exercises/{id}/attempts`, with `audio` and a matching filename, and status 200. Review/correct the recognized summary, check “This matches what I said,” and click **See what I understood**. Expect evaluation POST 200 and feedback. Ordinary summaries of three or more words must enable that button after confirmation.
4. Record again before transcription: verify the previous preview URL is revoked and only the new response plays/uploads. Deny microphone permission during a retry: the previous recording should remain usable.
5. Simulate a failed upload (DevTools offline), click transcription, then restore the network and retry. The recording and local playback must survive the error.
6. Repeat the successful path. Confirm the original exercise transcript remains unavailable before evaluation and opens with **Show transcript** afterward.

If the path fails, collect the metadata diagnostics and request status/error (redact transcripts and credentials). A missing upload means the failure is still client-side; a non-200 upload identifies the backend/provider boundary. V0.1 is not declared complete without the real browser loop.
