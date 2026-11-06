# Release runbook: the Audli 1.x accent library in Audli 2.0 (AUD-108/111/112)

Code is deployed (PR #140 `c18c3e0`, #141 `8e3797a`) with the feature **OFF**. Production behaviour is today's exactly until the two settings below are set.

## What turning it on does
| Format | Accents | Rule |
|---|---|---|
| Monologue | 15: American, British, Australian + Scottish, Indian, Irish, Canadian, New Zealand, Chinese, Japanese, Filipino, Nigerian, Southern US, New York, Boston | one approved voice (the owner's Audli 1.x voice; originals unchanged) |
| Mixed-accent conversation | the same 15, any two different accents | one approved voice per accent; the two voices always differ |
| Same-accent conversation | American, British, Australian only | needs two distinct approved voices |
| Unavailable | Welsh, South African | no approved voice: kept in the request, explained, learner must choose an alternative |
Nothing is substituted: an unavailable request is kept, explained with alternatives, and nothing is generated until the learner picks one. Budgets (2 lessons/learner/day, $5/day, 100,000 credits/month, $2 assessment guard), worker, models and the original voice mappings are unchanged.

## Exact settings (Render service `audli-api`, Environment)
```
AUDLI_LISTENING_FORMATS_ENABLED=true
AUDLI_LISTENING_FORMAT_ACCENTS=scottish,indian,irish,canadian,new_zealand,chinese,japanese,filipino,nigerian,southern,new_york,boston
```
(the original three are always open and need not be listed). Saving restarts the service (the API, the spoken worker and the generator share the container).

## Verify without a login
`curl -s https://audli-api.onrender.com/api/health` -> `listening_formats` must show `enabled: true`, `monologue` and `mixed_accent_conversation` with 15 accents each,
`conversation` with `american, british, australian`. Ids only; no voice ids or learner data. Then the logs show no errors.

## Controlled acceptance (signed in; uses the normal 2-lessons-per-day limit, about US$0.1 per lesson)
1. Create lesson page: choose Story; confirm 15 accent cards (Scottish ... Boston). Choose Dialogue; confirm 3 cards and the line listing other accents. Turn on "Different accent for the second speaker": both groups list 15.
2. Describe **"Create a Scottish monologue about the weekend"**, press Fill in the details: accent Scottish, Story. Create one lesson; play it: one voice, transcript lines follow the audio, four exercises, finish and score normally.
3. Describe **"Create a conversation between an Irish speaker and an American speaker about a shop"**: Irish + American, two voices. Create one lesson; check each speaker's voice and the transcript sync.
4. Describe **"Create a Welsh monologue"**: the page keeps Welsh, explains it is unavailable, offers alternatives, and the Create button stays disabled until you pick one.
5. Existing American/British/Australian conversations still generate and play. Check the ledger and logs afterwards.
That is two paid lessons (about US$0.2); further accents can be sampled one at a time.

## Rollback (seconds)
Set `AUDLI_LISTENING_FORMATS_ENABLED=false` (or delete the two variables): availability returns to the original three accents; lessons already made keep playing.
