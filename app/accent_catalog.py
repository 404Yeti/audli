"""The single source of truth for Audli's English accents: vocabulary, labels, spoken aliases and voice identifiers. DATA ONLY.

This module imports nothing from Audli (no pydantic, no 1.x, no 2.0 code), so Audli 1.x (app/accent_registry.py, which builds its typed records from it)
and Audli 2.0 (app/listening, which reads the two-speaker pairs) can both depend on it without depending on each other.

Three kinds of voice data, deliberately kept apart:
* PASSAGE_VOICES: the one voice per accent that Audli 1.x already runs in production (owner-approved, AUD-35/AUD-38). Unchanged.
* LISTENING_PAIRS: the TWO distinguishable voices a Audli 2.0 dialogue uses. Only a pair with status 'enabled' can be generated. Enabling a new accent is a
  reviewed code change to this file after the owner has listened to a real two-speaker lesson, never a configuration accident.
* LISTENING_CANDIDATES: proposed second voices. Metadata only: a provider's accent label is not proof of the accent, so nothing here is ever used for
  generation. They exist so the review has a concrete shortlist.
An accent is never substituted: an accent without an enabled pair is refused, not replaced by another.
"""

ACCENT_IDS = ('british', 'american', 'australian', 'scottish', 'indian', 'irish', 'canadian', 'new_zealand', 'welsh', 'south_african',
              'chinese', 'japanese', 'filipino', 'nigerian', 'southern', 'new_york', 'boston')

LABELS = dict(zip(ACCENT_IDS, ['British English', 'American English', 'Australian English', 'Scottish English', 'Indian English', 'Irish English',
    'Canadian English', 'New Zealand English', 'Welsh English', 'South African English', 'Chinese English', 'Japanese English', 'Filipino English',
    'Nigerian English', 'Southern US English', 'New York English', 'Boston English']))

ALIASES = {'british': r'british|britain|england|uk',
    'american': r'american|america|usa|us accent', 'australian': r'australian|australia|aussie',
    'scottish': r'scottish|scotland|scots', 'indian': r'indian|india', 'irish': r'irish|ireland',
    'canadian': r'canadian|canada', 'new_zealand': r'new zealand|kiwi',
    'welsh': r'welsh|wales', 'south_african': r'south african|south africa',
    'chinese': r'chinese', 'japanese': r'japanese', 'filipino': r'filipino|philippine',
    'nigerian': r'nigerian', 'southern': r'southern(?: (?:us|u\.s\.|american|united states))?|(?:us|american) southern',
    'new_york': r'new york(?:er)?', 'boston': r'boston(?:ian)?'}

# ---- Audli 1.x passage voices (one per accent; unchanged) -------------------------------------------------------------------------------

# (accent, voice_id, status, name, verification_status)
EXISTING_PASSAGE_VOICES = (
    ('british', 'onwK4e9ZLuTAKqWW03F9', 'enabled', 'Existing British voice', 'existing production configuration'),
    ('australian', 'tyepWYJJwJM9TTFIg5U7', 'enabled', 'Existing Australian voice', 'AUD-35 end-to-end verified; owner reports too quiet; replacement pending'),
    ('irish', 'DbwWo4rVEd5NrejHYUnm', 'enabled', 'Existing Irish voice', 'existing production configuration'),
)

SELECTED_VERIFICATION = 'AUD-38 account access and Flash2.5 sample verified; owner listening and production release approved'
# (accent, voice_id, wave, name)
SELECTED_PASSAGE_VOICES = (
    ('indian', 'fydPJq00SigRIaPxWgiS', 1, 'Pooja - Upbeat UGC Creator'),
    ('scottish', 'csXxiUN2BUFflsCaDxPM', 1, 'Chris - Scottish Casual'),
    ('canadian', 'RorawgRgSS2YxWqy31vY', 1, 'Catherine - Educational & Conversational'),
    ('new_zealand', 'pDZ0CqONaFi2LrK1f413', 1, 'Torey Slatter'),
    ('chinese', '1a0nAYA3FcNQcMMfbddY', 2, 'Ziyu - Friendly but Unclear'),
    ('japanese', 'DiHqGqDdN8tWkUxlsPeg', 2, 'Yuki - Gentle and Precise'),
    ('filipino', '7IA6NDxeqmtXCKLKb5nf', 2, 'Amara - English FIlipino Calm Narrator'),
    ('nigerian', '8P18CIVcRlwP98FOjZDm', 2, 'Ola - Nigerian Storyteller'),
    ('southern', '9XNfIsDIvdnSURk8DW4z', 3, 'Wade - Southern Backwoods Neighbor'),
    ('new_york', 'QzTKubutNn9TjrB7Xb2Q', 3, 'Jerry B. - New York Italian Mobster'),
    ('boston', 'UZvBfqEdvCFLqsBOo9Zr', 3, 'Jerry B. - Boston Accent Conversational Voice'),
)

# ---- Audli 2.0 two-speaker pairs ------------------------------------------------------------------------------------------------------------

# accent -> (voice for speaker 1, voice for speaker 2). 'enabled' only after the owner has listened to a real two-speaker lesson in that accent.
# These three are what 2.0 has produced and published since launch; they are the pairs the current generator already uses.
LISTENING_PAIRS = {
    'american': {'voices': ('EXAVITQu4vr4xnSDxMaL', 'CwhRBWXzGAHq8TQ4Fs17'), 'status': 'enabled'},
    'british': {'voices': ('Xb7hH8MSUJpSbSDYk0k2', 'onwK4e9ZLuTAKqWW03F9'), 'status': 'enabled'},
    'australian': {'voices': ('tyepWYJJwJM9TTFIg5U7', 'IKne3meq5aSn9XLyUdCD'), 'status': 'enabled'},
}

# Proposed second voices for accents Audli 1.x already runs with one voice. status 'proposed' = NOT usable. Source: ElevenLabs voice-library metadata
# (labels and descriptions only; no audio was generated and no credits were spent). The first voice is the approved 1.x voice, kept so both versions
# sound the same; each proposal is the opposite gender from it where known, so the two speakers are easy to tell apart. All need an owner listening
# review and a real two-speaker test before status can become 'enabled'.
LISTENING_CANDIDATES = {
    'irish': {'first': 'DbwWo4rVEd5NrejHYUnm', 'status': 'proposed', 'second': [
        ('BOt7zZh6gzfWlIUYnyPz', 'Darragh - Warm, Friendly, Calm and Clear', 'male, neutral Irish, casual conversation'),
        ('fNG1TqnY9mWgIFIExmZA', 'Andrew EMTS', 'male, clear teaching style')]},
    'indian': {'first': 'fydPJq00SigRIaPxWgiS', 'status': 'proposed', 'second': [
        ('eH0983ZXSi0hJkuHbC3i', 'Vishesh - Friendly Indian English', 'male, light accent, clear and conversational'),
        ('fSTPf9bYe9OOsdVPpz21', 'Lavkush - Calm, Warm, and Articulate', 'male, clear diction')]},
    'scottish': {'first': 'csXxiUN2BUFflsCaDxPM', 'status': 'proposed', 'second': [
        ('TVmbglAk3F1GkiCoOq47', 'Isla Skye - Authentic Scottish Female', 'female, Edinburgh, e-learning'),
        ('2Cg6E8rBwJfseh8Mk7sj', 'Nicola - Warm & Neutral Scottish Voice', 'female, West of Scotland, conversational')]},
    'canadian': {'first': 'RorawgRgSS2YxWqy31vY', 'status': 'proposed', 'second': [
        ('bE3COCGjWQ0g9MBhv2cf', 'Jeff - Smooth Canadian', 'male, neutral Canadian accent'),
        ('D7u5tN2bpWtEVjAD7c1M', 'Aaron - Educational', 'male, educational')]},
    'new_zealand': {'first': 'pDZ0CqONaFi2LrK1f413', 'status': 'proposed', 'second': [
        ('XcScRctIXQsuUPQQwzsz', 'Becca - Calm Friendly Kiwi', 'female, conversational (use if the 1.x voice is male)'),
        ('UqC26gfNvU9pUmY9theE', 'Jonty - Professional Warm Kiwi', 'male, e-learning (use if the 1.x voice is female)')]},
}
# No candidate and no 1.x voice: welsh, south_african (refused until a voice is approved). Not proposed for 2.0 dialogue: chinese, japanese, filipino,
# nigerian (accents shaped by a learner's first language, so a separate owner decision), southern, new_york, boston (1.x voices are character voices).


def enabled_listening_accents() -> tuple[str, ...]:
    return tuple(accent for accent, pair in LISTENING_PAIRS.items() if pair['status'] == 'enabled')


def upcoming_listening_accents() -> tuple[str, ...]:
    """Accents Audli can name but not yet generate in two voices: shown as coming soon, never selectable."""
    return tuple(accent for accent in LISTENING_CANDIDATES if accent not in enabled_listening_accents())


# ---- single approved voices (monologues and mixed-accent speakers) ---------------------------------------------------------------------------

def single_voices() -> dict:
    """accent -> the one approved voice Audli 2.0 can use for a monologue or for one speaker of a mixed-accent conversation, for accents that are NOT already
    in LISTENING_PAIRS (those use the first voice of their pair, so production behaviour is unchanged). These are exactly the voices the owner curated and
    approved for Audli 1.x (EXISTING_PASSAGE_VOICES and SELECTED_PASSAGE_VOICES); nothing is invented or substituted. Whether an accent is OFFERED is a deployment
    decision (AUDLI_LISTENING_FORMATS_ENABLED and AUDLI_LISTENING_FORMAT_ACCENTS), not a property of this table."""
    out = {}
    for row in (*EXISTING_PASSAGE_VOICES, *SELECTED_PASSAGE_VOICES):
        if row[0] not in LISTENING_PAIRS:
            out[row[0]] = row[1]
    return out


# Voices kept as the owner curated them but flagged for the listening review (never removed, never silently replaced). The flag says why a human should listen
# before the accent is offered for ESL practice; it is not a verdict.
REVIEW_FLAGS = {
    'australian': ('quiet_voice_aud39',),
    'indian': ('creator_style_voice',),
    'scottish': ('casual_style_voice',),
    'new_zealand': ('speaker_gender_not_recorded',),
    'chinese': ('voice_named_unclear',),
    'nigerian': ('storyteller_style_voice',),
    'southern': ('character_voice',),
    'new_york': ('character_voice',),
    'boston': ('character_voice', 'shares_a_name_with_new_york_voice'),
}


def format_eligible_accents() -> tuple[str, ...]:
    """Every accent with at least one approved voice, in catalog order: the original three plus every owner-curated Audli 1.x voice. These are the accents that
    can be OPENED for monologues and mixed-accent speakers (AUDLI_LISTENING_FORMAT_ACCENTS). Welsh and South African are absent: no approved voice exists."""
    voices = set(single_voices()) | set(LISTENING_PAIRS)
    return tuple(a for a in ACCENT_IDS if a in voices)
