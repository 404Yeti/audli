# Recording consent, handling, retention and deletion

Applies to any recording used in this experiment beyond the synthetic clips. **Nothing is collected until the owner approves this protocol and, where required, a legal review of local personal-data rules** (a voice recording is personal data; Vietnam's personal-data decree and any rules where a speaker lives should be checked). This document is a working protocol, not legal advice.

## Who and what
* **Adults only** (18+), volunteers who speak Vietnamese as a first language and are comfortable reading short English prompts. Not Audli learners' production audio, ever. No minors.
* Each speaker records, on their own phone or laptop, the scripted prompt set in `tests/fixtures/stt_eval/prompts.vi-en.json` plus the pause / hesitation / self-correction tasks in `hesitation_set.json`, in quiet and moderately noisy rooms.
* Pilot size: 5 speakers (about 45 clips each). A pilot can reveal gross problems; a go / no-go needs about 20 speakers.

## Consent record (one per speaker, kept separately from the audio)
Record ID `CONSENT-YYYY-NNN`, date, a pseudonymous speaker ID (`spk-001`), and the speaker's explicit, separate yes to each:
1. I am 18 or older and take part voluntarily; I may stop at any time.
2. My recordings will be stored on one encrypted computer controlled by the project owner, identified only by my speaker ID (no name, face or contact details in filenames or metadata).
3. The recordings will be sent to OpenAI's speech-to-text services to measure accuracy and delay, under the project's API terms (the owner confirms the account's data-retention setting before any upload and states it on this form).
4. The recordings will **not** be used to train any model, shared with anyone else, or used for marketing.
5. They will be deleted by the date on this form (default: 30 days after recording, or earlier when the experiment ends), and sooner on request.
6. I may withdraw at any time before deletion; my clips are then deleted within 7 days and excluded from any further results.
Optional: self-reported English level (A2 to C1), device, and room (quiet / noisy).

## Handling
* Audio and manifests live **outside the git repository** (default `~/audli-realtime-experiment`, mode 0700), never in the repo, chats, tickets or cloud-synced folders. The repository rejects audio files in `experiments/` (checked by a test).
* Results are numbers only. Transcript text is written only with `--include-text`, only for consented research data, and is deleted with the audio.
* Manifests for consented clips must carry the consent record ID (the existing loader refuses otherwise) and `provenance: "consented"`.
* Only the owner (and the assistant working under the owner's direction) has access. No upload to any service other than the transcription providers named above.
* Paid provider calls are made only through `run_experiment.py`, which reserves every call against the spend ledger first (cap $5; synthetic work cannot touch the pilot reserve).

## Retention and deletion
1. Delete date is on each consent record. At the earlier of that date or experiment closure: securely delete the audio, manifests with clip references, `raw_results.json` and any transcript output (`shred -u` on files, then remove the directory; on SSD / managed storage also remove device backups).
2. Verify: list the directory (empty), and record the deletion date, who did it and a file count before and after in the consent register.
3. Withdrawal: delete that speaker's clips within 7 days, re-run the report without them, and note it in the register.
4. Keep only the consent register (no audio), the numbers-only reports and this protocol.
5. Provider side: confirm whether the provider retained the audio under the account's settings and, if so, how to request deletion; record the answer in the register.

## Synthetic data
The generated clips (`corpus.py`) contain no personal data. TTS told to "sound Vietnamese" is a stress variety and **not** evidence about real Vietnamese-accented learners.
