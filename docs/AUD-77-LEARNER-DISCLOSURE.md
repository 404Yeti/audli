# AUD-77 — learner-facing disclosure for AI language suggestions

Status: **drafted for owner approval; nothing is published.** The in-app copy below ships in the Review UI, which stays invisible until the feature flag is on. The privacy-policy paragraph is a proposal only (the marketing site has no privacy page, and editing it would deploy on merge).

## What happens to a learner's answer (facts, with sources)
| Fact | Basis |
| --- | --- |
| Only the **text** of the learner's saved answer is sent, plus (for the second, independent check) the suggested rewrite. Never the passage, script, expected answers, voice recording, name or account details. | Code and tests: the provider methods take only those strings (`tests/test_review_improvement_migration.py::test_prompts_carry_only_the_learners_words…`). |
| The provider is **OpenAI** (the configured `AUDLI_LLM_MODEL`; the same provider already receives answers for assessment). Requests use `store=false` and a single request each (no hidden retries). | `app/services/openai_provider.py`. |
| OpenAI **may keep the text for up to 30 days** for abuse monitoring, longer only if the law or safety requires it. `store=false` is **not** zero retention. | OpenAI "Data controls" page, fetched 2026-10-22: https://developers.openai.com/api/docs/guides/your-data |
| By default OpenAI **does not use API data to train its models**. | Same page. |
| Whether Audli's OpenAI organization has Zero Data Retention or Modified Abuse Monitoring is **unverified**. | Owner to confirm in the OpenAI organization settings. |
| Audli **saves the suggestion** (text, notes) with the lesson record. It never changes scores, difficulty or the saved answer. | `review_improvements` (migration 007); advisory-only tests. |
| Audli **cannot** remove OpenAI's copy before its own retention ends. | Provider-side; no per-learner deletion API is used. |
| Deleting the account removes Audli's copy **only once** account deletion (AUD-34, PR #68) is enabled; today it is not. | `docs/AUD-34-ACCOUNT-DELETION.md`. |

## In-app copy (exact strings, `web/components/review-improvement.tsx`)
Always visible before the first request:
> Want a more natural way to say it? Audli can suggest one. This is about English only and never changes your progress.

Under "How your answer is handled" (expandable, before any request):
> To do this, Audli sends the text of your answer (not your voice) to its AI provider, OpenAI. OpenAI may keep it for up to 30 days to check for misuse, and by default does not use it to train its models.
> Audli can't remove OpenAI's copy sooner. Only your answer text is sent: never the passage, your voice or your account details. Audli saves the suggestion with your lesson.

Before a retry: "You can try once more. That uses one of your daily language suggestions."

## Must not be claimed
Zero retention, "not stored", "deleted immediately", "private", "never leaves Audli", or any promise about deletion of provider copies. The UI test (`the disclosure is accurate…`) fails if these phrases appear.

## Proposed privacy-policy paragraph (not published)
> **Language suggestions.** If you ask Audli for a more natural version of an answer you gave, we send the text of that answer (not your voice recording, and not the lesson passage) to our AI provider, OpenAI, to generate and check the suggestion. OpenAI may retain this text for up to 30 days for abuse monitoring (longer only where required by law or to protect against harm) and, by default, does not use it to train its models. We store the suggestion with your lesson. We cannot delete OpenAI's copy earlier. When account deletion is available, deleting your account removes the suggestion from our systems; copies in provider logs and in backups follow their own retention schedules.

## Revise this before publishing or enabling when
the owner confirms Zero Data Retention or different OpenAI controls; the provider or model changes (the name in the UI string must change with it); AUD-34 deletion is enabled or its backup/provider limits change; the 30-day figure on OpenAI's page changes (re-verify on each release).
