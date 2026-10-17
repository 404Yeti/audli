# Homework investigation — 2026-10-17 (AUD-64)

Read-only production SQL confirms the latest assignment was offered at 06:30:38
UTC and finalized `unavailable` at 06:30:59, topic `coffee`, attempts `1`, no
content ID and no active lease. Its lesson remains `completed`. The fleet counter
is 2 searches for 2026-10-17. The preceding assignment was successfully assigned
at 03:19 UTC. No production records or configuration were changed.

## What the evidence establishes

- Discovery was admitted and finalized; this is not an unfinished lease or a
  lesson completion rollback. The two-attempt assignment limit was not reached.
- The application daily admission limit did not reject this attempt: admission
  rejection raises 409 before attempts are incremented and cannot finish an
  assignment as unavailable. The counter alone does not establish Google's quota.
- The Google project quota, HTTP outcome and rejected candidates cannot be
  recovered from assignment state. No failure reason or candidate audit is stored.
- No suitable candidate is persisted for this attempt. The database only contains
  three prior selected content records, so it cannot reconstruct rejected videos.

## Code paths reviewed

`app/homework_api.py` handles DiscoveryUnavailable and timeout as an empty candidate
list. `app/storage/homework.py::finish` also returns unavailable when every eligible
candidate has already been assigned to the learner. These outcomes share the same
public message. Existing provider code does not log HTTP status, Google reason,
candidate counts or rejection reasons; its HTTPX request logger intentionally
suppresses YouTube URLs to protect learner topics.

`app/services/youtube.py` performs one search (up to 10 IDs) and one metadata request.
403/429 starts a 15-minute process-local cooldown. Any non-200, malformed/oversized
payload or transport error raises DiscoveryUnavailable. There are no retries.
Filtering requires canonical ID/URL, available public processed non-live content,
no content-rating restriction, 60–600 seconds at this learner's B1 level, declared
English audio, acceptable regional restrictions, topic-word overlap and no flagged
words. Missing English metadata alone can reject otherwise English videos.

For this explicit topic the search planner adds English explanation and the saved
accent hint; it omits the recent lesson topic. Relevance tests title/description
word overlap with `coffee`. Whether the actual ten results were relevant or rejected
is unproven without their metadata. Existing duplicate history was also checked:
the previous assignments concern software engineering and cybersecurity, but that
alone cannot rule out duplicate candidates in this search.

## Remaining evidence needed

Render log access awaits the connector-required confirmation of `robert's
workspace` (`tea-dauqlhd9fdbs739vmkv0`). Inspect the 06:30:38–06:31:05 UTC window
for decision endpoint outcomes, timeouts and errors. Even if HTTP access logs are
available, the current code may not contain enough diagnostics to distinguish
provider failure from candidate rejection retrospectively. Google Cloud project
quota was not independently available. No additional quota-consuming search was
run; a later replay would not prove the original result.

The actual provider/filter cause is therefore **not yet established**. No
recommendation defect has been confirmed and no recommendation safeguard, quota,
query, state or selection code was changed. Do not report a quota outage or loosen
language, relevance, safety or duration filters based on this message alone.
