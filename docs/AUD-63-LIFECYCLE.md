# AUD-63 — lesson lifecycle and quiet classroom

The existing lesson contract is a ten-minute, time-bounded sequence, not exactly
two passages. It starts another passage only while there is room for it and the
recap. This change preserves that rule, assessment/adaptation, completion counts
and planner snapshots. A high-level planned topic is now spoken before listening.

Before that topic, an enabled homework feature can review one outstanding owned
assignment. Existing schema005 homework_reflections stores the review checkpoint,
revision and up to two learner descriptions, marked unverified_self_report.
Application-owned prompts ask whether the learner watched, then one main-idea and
one detail question. One clarification is allowed; not yet/skip has no penalty.
No external content is fetched, transcript assumed, factual grading performed or
scores/planner changed. Owner-row locking makes prepare/answer retries idempotent;
terminal discussed/skipped assignments are excluded from later reviews. A paused
review resumes at its existing checkpoint. Errors/timeouts leave the lesson free
to continue. Cross-account and unauthenticated requests are rejected.

After the evidence recap, the completed lesson is already saved before topic
preference check-in/homework. Assignment confirmation finishes before a separate
short farewell. Only then does the existing completion view show its normal
summary. Closing failures or a bounded playback timeout still reveal that view;
this transition never completes/grades another lesson. Finished presentation is
remembered in account-scoped session storage; reload does not repeat closing.
The verified compact card remains available and is also saved in Plan. No new
completion route/screen, discovery retry or discovery-budget change was added.

Classroom replies are inline fields using onboarding spacing. Redundant classroom
accordions are removed; fallback disclosure is one concise status line. Essential
skip/retry/replay actions remain. Playback returns to Idle when finished; a stale
cancelled clip cannot pause/clear a newer clip on the shared audio element.

Onboarding name typing now stops the voice turn just like goal typing. Drafts are
session-scoped by account, question kind and question token, restored only to the
matching field, and cleared on successful save. Authoritative server checkpoints
still control progression. Page navigation records an intentional pause; returning
resumes onboarding rather than inventing Home/completion. Completed onboarding and
existing preferences are unchanged. No raw audio is added to browser storage.

Validation: focused backend lifecycle/homework/onboarding tests and new browser
cases, with external providers mocked. Two mobile screenshots cover inline input
at320/390px; existing CI provides full validation. Native iPhone Safari keyboard,
playback interruptions and conversation feel remain owner acceptance items.
No production deployment, credential/configuration change, schema migration or
real learner-data mutation is part of this PR. Schema005 and rollback compatibility
are preserved because no persisted Lesson/Profile model fields were added.
