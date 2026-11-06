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
# 'approved' stays None until the OWNER has listened and approved a second voice in writing; then it is set to that voice's id (one line, reviewed in a PR) and the accent's
# same-accent two-speaker conversation opens (still behind AUDLI_LISTENING_FORMATS_ENABLED and the accent being opened in AUDLI_LISTENING_FORMAT_ACCENTS).
# Each row: (voice_id, name, provider-labelled gender/style). The first row is the proposal; later rows are alternates. Metadata only: nothing here is verified by ear.
LISTENING_CANDIDATES = {
    'irish': {'first': 'DbwWo4rVEd5NrejHYUnm', 'approved': None, 'second': [
        ('BOt7zZh6gzfWlIUYnyPz', 'Darragh - Warm, Friendly, Calm and Clear', 'male, neutral Irish, casual conversation'),
        ('fNG1TqnY9mWgIFIExmZA', 'Andrew EMTS', 'male, clear teaching style')]},
    'indian': {'first': 'fydPJq00SigRIaPxWgiS', 'approved': None, 'second': [
        ('eH0983ZXSi0hJkuHbC3i', 'Vishesh - Friendly Indian English', 'male, light accent, clear and conversational'),
        ('fSTPf9bYe9OOsdVPpz21', 'Lavkush - Calm, Warm, and Articulate', 'male, clear diction')]},
    'scottish': {'first': 'csXxiUN2BUFflsCaDxPM', 'approved': None, 'second': [
        ('TVmbglAk3F1GkiCoOq47', 'Isla Skye - Authentic Scottish Female', 'female, Edinburgh, e-learning'),
        ('2Cg6E8rBwJfseh8Mk7sj', 'Nicola - Warm & Neutral Scottish Voice', 'female, West of Scotland, conversational')]},
    'canadian': {'first': 'RorawgRgSS2YxWqy31vY', 'approved': None, 'second': [
        ('bE3COCGjWQ0g9MBhv2cf', 'Jeff - Smooth Canadian', 'male, neutral Canadian accent'),
        ('D7u5tN2bpWtEVjAD7c1M', 'Aaron - Educational', 'male, educational')]},
    'new_zealand': {'first': 'pDZ0CqONaFi2LrK1f413', 'approved': None, 'second': [                    # speaker 1 (Torey Slatter) is male, so the second speaker is female
        ('XcScRctIXQsuUPQQwzsz', 'Becca - Calm Friendly Kiwi', 'female, conversational'),
        ('7R9vnLAE0ZSpY0xWp0mv', 'Jenna - NZ Female, clear', 'female, young, clear narration')]},
    'chinese': {'first': '1a0nAYA3FcNQcMMfbddY', 'approved': None, 'second': [                        # speaker 1 (Ziyu) is female
        ('mBoVD3461U2BagYEwjeo', "Lee's voice", 'male, young, Chinese-Mandarin accent'),
        ('NxUQ5SgevJvRsX9HsM0O', 'Wenchang-Chinese accent', 'calm, clear, steady, instructional (gender not labelled)')]},
    'japanese': {'first': 'DiHqGqDdN8tWkUxlsPeg', 'approved': None, 'second': [                        # speaker 1 (Yuki) is female
        ('69Na567Zr0bPvmBYuGdc', 'Tatsuya Watanabe - Narration, Japanese male', 'male, young, Japanese accent'),
        ('1MPZGta71qtdTm2BpYUl', 'Tahm - Deep narration & Storytelling', 'male, young, labelled Japanese accent')]},
    'filipino': {'first': '7IA6NDxeqmtXCKLKb5nf', 'approved': None, 'second': [                        # speaker 1 (Amara) is female
        ('84RXm9MY2lATjoJi5K8P', 'JC - Calm & Friendly', 'male, young, Filipino accent'),
        ('DBL96vXYYbWbRYCYG3wC', 'Jabby - Informational, Casual, Friendly', 'male, Filipino accent')]},
    'nigerian': {'first': '8P18CIVcRlwP98FOjZDm', 'approved': None, 'second': [                        # speaker 1 (Ola) is male
        ('2vbhUP8zyKg4dEZaTWGn', 'Stella - warm, natural and conversational', 'female, young, Nigerian accent'),
        ('E7AdVOKapxpnuchBH9Fa', 'Taiwo O', 'female, middle-aged, warm and assertive')]},
    'southern': {'first': '9XNfIsDIvdnSURk8DW4z', 'approved': None, 'second': [                        # speaker 1 (Wade) is male
        ('BVA1oNX6xZt6o7QaUwxr', 'Dr. Kendall', 'female, calm, soft Southern accent, steady pace'),
        ('5xZj4vEjtKaYKe3vCreA', 'Leah - Warm Southern voice', 'female, middle-aged, subtle Southern accent')]},
    'new_york': {'first': 'QzTKubutNn9TjrB7Xb2Q', 'approved': None, 'second': [                        # speaker 1 (Jerry B.) is male
        ('DZ2ULBbuhpX6zJYCQic4', 'Alise - Calm Urban City Alto', 'female, young, calm, subtle NYC tone'),
        ('C3x1TEM7scV4p2AXJyrp', 'Zuri - Professional New Yorker', 'female, middle-aged New York')]},
    'boston': {'first': 'UZvBfqEdvCFLqsBOo9Zr', 'approved': None, 'second': [                            # speaker 1 (Jerry B.) is male
        ('6B43gMdWDCu6YLdbVZFc', 'Shelly - Boston Native', 'female, middle-aged, Boston')]},
}
# No candidate and no 1.x voice: welsh, south_african (refused until a voice is approved).


def enabled_listening_accents() -> tuple[str, ...]:
    return tuple(accent for accent, pair in LISTENING_PAIRS.items() if pair['status'] == 'enabled')


def approved_second_voices() -> dict:
    """accent -> the owner-approved second voice id. Empty until the owner approves; each entry must be one of that accent's listed candidates and differ from speaker 1."""
    out = {}
    for accent, row in LISTENING_CANDIDATES.items():
        if row.get('approved'):
            if row['approved'] not in [v for v, _, _ in row['second']] or row['approved'] == row['first']:
                raise ValueError(f'{accent}: an approved second voice must be a listed candidate and differ from speaker 1')
            out[accent] = row['approved']
    return out


def approved_pairs() -> dict:
    """accent -> (speaker 1, speaker 2) for accents whose second voice the owner has approved: two genuinely different voices, speaker 1 always the owner's 1.x voice."""
    return {accent: (LISTENING_CANDIDATES[accent]['first'], voice) for accent, voice in approved_second_voices().items()}


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
