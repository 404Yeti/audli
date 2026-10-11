"""Server-owned accent consent and passage routing; preferences are interest only."""
import re
import asyncio
import logging
from datetime import datetime, timezone
import httpx
from pydantic import Field
from app.models import StrictModel, PassageVoice
from app.accents import LABELS, normalize, only_accent_request, extract, no_preference, lesson_preference


def discover(preferences, history, voices):
    """At most two discovery prompts, separated by three new lesson starts."""
    if preferences.status != 'unspecified' or not voices:
        return False
    asked = [index for index, item in enumerate(history) if item.accent_discovery]
    return len(asked) < 2 and (not asked or asked[0] >= 2)


def discovery_response(text):
    # A social answer must never become a saved preference or topic request.
    preferences = extract(text) if no_preference(text) or only_accent_request(text) else lesson_preference(text)
    if preferences is None:
        return None, 'We can leave accent preferences for another time and continue with our usual listening practice.'
    if preferences.status == 'no_preference':
        return preferences, 'Of course. I won’t ask about accent preferences again. We’ll continue with our usual listening practice.'
    labels = ', '.join(LABELS[accent] for accent in preferences.accents)
    acknowledgment = f'I’ll remember your interest in {labels}.' if labels else 'I’ll remember that accent interest, although it isn’t available for practice.'
    return preferences, acknowledgment + ' We’ll use our usual passage voice today.'


def offer_after_preference(lesson, preferences, voices):
    """Queue one spoken question, never a consent grant or a repeated offer."""
    if (preferences is None or preferences.status != 'preferred' or lesson.accent_offered
            or lesson.accent_consent.state != 'none'):
        return False
    accent = next((accent for accent in preferences.accents if accent in voices), None)
    if accent is None:
        return False
    lesson.accent_offered = accent
    # Older deployed readers use this target to render the confirmation question.
    # State, timestamp and ordinal still contain no authorization.
    lesson.accent_consent.accent = accent
    lesson.accent_confirmation = True
    lesson.accent_resolved = False
    return True


class VerifiedPassageVoice(StrictModel):
    voice_id: str = Field(pattern=r'^[A-Za-z0-9]{20}$')
    validation: str = Field(min_length=10, max_length=500)
    # Reference to a reviewed listening validation, not vendor metadata.


def offer(preferences, history, voices):
    eligible = [accent for accent in preferences.accents if accent in voices]
    if preferences.status != 'preferred' or not eligible:
        return None
    # Starts, including interrupted lessons, consume cadence. At most every third.
    if len(history) % 3:
        return None
    last = next((item.accent_offered for item in history if item.accent_offered), None)
    return next((accent for accent in eligible if accent != last), eligible[0])


CONSENT_SECONDS = 600


def authorized_accent(lesson, at=None):
    """A preference or legacy passage_accent is never a consent grant."""
    from app.lesson import now
    at = at or now()
    grant = lesson.accent_consent
    if (lesson.status != 'active' or grant.state != 'accepted'
            or grant.exercise_number != lesson.exercises_completed
            or grant.accent not in LABELS or grant.accepted_at is None
            or grant.accepted_at.tzinfo is None):
        return None
    age = (at - grant.accepted_at).total_seconds()
    return grant.accent if 0 <= age < CONSENT_SECONDS else None


def expire(lesson):
    if lesson.accent_consent.state == 'accepted':
        lesson.accent_consent.state = 'expired'
    lesson.passage_accent = None
    lesson.accent_confirmation = False


def intent(text, offered=None):
    text = text.lower().replace('’', "'").strip(' .!?')
    if re.search(r"\b(maybe|perhaps|not sure|don't know|do not know|tomorrow|later|might|if)\b", text):
        return None, None
    if re.search(r"\b(no thanks|not today|skip|nope|rather not|don't want|do not want|cancel|changed my mind|change my mind|usual voice|default voice)\b", text) or text == 'no':
        return ('decline', None) if offered or 'accent' in text else (None, None)
    accents = normalize(text)
    named = re.findall(r'\b([a-z]+)\s+(?:english\s+)?accents?\b', text)
    if any(word not in ('an', 'the', 'another', 'different', 'one', 'any') and not normalize(word) for word in named):
        return 'unavailable', None
    if re.search(r'\b(prefer|preference|difficult|difficulty|remember|interest|interested|struggle)\b', text) and not re.search(r'\b(practice|try|switch)\b', text):
        return None, None
    request = bool(re.search(r"^(?:practice|try|switch|use)\b|\b(?:let's (?:practice|try|do)|(?:can|could) we (?:practice|try|do|listen|hear|use)|instead)\b", text)
                   or re.search(r"\b(want|would like|i'd like)\b.*\b(practice|listen|passage|exercise|accent)\b", text))
    # Topic requests such as British music are not accent-practice requests.
    request = request and only_accent_request(re.sub(r'\b(?:switch|use|next|passage|exercise)\b', '', text.replace("let's", '')))
    acceptance = bool(re.fullmatch(r"(?:yes|yeah|yep|sure|okay|ok|sounds good)(?: please| thanks|, please)?", text))
    from app.accents import ALIASES
    choice = text
    for aliases in ALIASES.values():
        choice = re.sub(r'\b(?:' + aliases + r')\b', '', choice)
    choice = re.sub(r'\b(?:yes|yeah|yep|sure|okay|ok|please|thanks|an|a|the|accent|english)\b', '', choice)
    explicit_choice = not re.search(r'[a-z0-9]', choice)
    if len(accents) > 1:
        return None, None
    if accents and (request or (offered and explicit_choice)):
        return 'select', accents[-1]
    if request and re.search(r'\baccent\b', text) and not accents:
        return 'unavailable', None
    if offered and acceptance:
        return 'select', offered
    return None, None


def resolve(lesson, text, voices):
    from app.lesson import AccentConsent, now
    confirmation = lesson.accent_confirmation
    context = (lesson.accent_consent.accent if confirmation
               else None if lesson.accent_resolved else lesson.accent_offered)
    action, accent = intent(text, context)
    if action is None and lesson.accent_consent.state == 'accepted':
        # A later topic answer is not a new offer; verbal revocation still works.
        if intent(text, lesson.accent_consent.accent)[0] == 'decline':
            action = 'decline'
    lesson.accent_resolved = True
    if action is None:
        if confirmation or lesson.accent_consent.state != 'accepted':
            expire(lesson)
        return ('We’ll use our usual passage voice.' if confirmation else None)
    lesson.accent_confirmation = False
    if action == 'decline' or accent not in voices:
        lesson.accent_consent = AccentConsent(state='declined')
        lesson.passage_accent = None
        return ('Of course. We’ll use our usual passage voice today.' if action == 'decline'
                else 'That accent isn’t available for practice yet. We’ll use our usual passage voice today.')
    lesson.accent_consent = AccentConsent(state='accepted', accent=accent,
        accepted_at=now(), exercise_number=lesson.exercises_completed)
    lesson.passage_accent = accent  # Compatibility display only; never authorizes routing.
    lesson.accent_confirmation = lesson.phase == 'WELCOME'
    return f'Perfect, we’ll practice {LABELS[accent]} for the next passage only.'


async def passage_audio(settings, provider, content, accent, limits=None, *, owner=None, lesson=None):
    voice = settings.available_passage_voices.get(accent)
    reservation = None
    if (voice and settings.provider != 'demo' and settings.elevenlabs_api_key
            and content.speech_rate in (.75, 1.0)):
        if limits is not None and lesson is not None and owner is not None:
            reservation = await limits.call(limits.reserve, owner, lesson.id, lesson.exercises_completed, accent, len(content.script))
        if reservation:
            outcome = 'failed'
            try:
                async def generate():
                    async with httpx.AsyncClient(timeout=settings.passage_tts_timeout_seconds) as client:
                        response = await client.post(f'https://api.elevenlabs.io/v1/text-to-speech/{voice.voice_id}',
                            headers={'xi-api-key': settings.elevenlabs_api_key.get_secret_value()},
                            json={'text': content.script, 'model_id': settings.passage_tts_model,
                                  'voice_settings': {'speed': content.speech_rate}})
                        response.raise_for_status()
                        if not response.content:
                            raise ValueError('Empty passage audio')
                        from app.audio import sanitize_generated_audio
                        return await sanitize_generated_audio(response.content, 'mp3')
                # A committed reservation is an in-flight admission. Recheck immediately
                # before dispatch; disable/re-enable also invalidates the old revision.
                if not await limits.valid(reservation, owner=owner, lesson=lesson, accent=accent):
                    raise ValueError('Accent admission disabled')
                audio = await asyncio.wait_for(generate(), settings.passage_tts_timeout_seconds)
                outcome = 'success'
                if await limits.valid(reservation, owner=owner, lesson=lesson, accent=accent):
                    logging.getLogger('audli').info('Accent passage outcome=success characters=%d', len(content.script))
                    return audio, PassageVoice(accent=accent, provider='elevenlabs',
                        voice_id=voice.voice_id, validation=voice.validation)
            except asyncio.CancelledError:
                outcome = 'cancelled'
                raise
            except (httpx.HTTPError, ValueError, TimeoutError):
                # Fixed categories only: no provider bodies, scripts, IDs, or secrets.
                logging.getLogger('audli').warning('Accent passage outcome=provider_fallback')
            finally:
                await asyncio.shield(limits.call(limits.finish, reservation[0], outcome))
        else:
            logging.getLogger('audli').warning('Accent passage outcome=limit_fallback')
    return await provider.speech(content), PassageVoice(provider=settings.provider,
        voice_id=settings.voice, fallback=bool(accent))
