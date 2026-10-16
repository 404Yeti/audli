"""Server-owned accent consent and passage routing; preferences are interest only."""
import asyncio
import logging
import httpx
from httpx import HTTPStatusError, TimeoutException
from pydantic import Field
from app.models import StrictModel, PassageVoice
from app.accents import LABELS
from app.accent_intent import AccentInterpretation, interpret


def discover(preferences, history, voices):
    """At most two discovery prompts, separated by three new lesson starts."""
    if preferences.status != 'unspecified' or not voices:
        return False
    # Merely scheduling a welcome does not establish a discovery attempt.
    # Abandoned starts must not exhaust the learner's two conversational chances.
    asked = [index for index, item in enumerate(history)
             if item.accent_discovery and (item.accent_discovery_resolved or item.accent_resolved)]
    return len(asked) < 2 and (not asked or asked[0] >= 2)


def discovery_response(text):
    parsed = text if isinstance(text, AccentInterpretation) else interpret(text, discovering=True)
    preferences = parsed.preferences
    if parsed.kind in ('request', 'accept', 'unsupported'):
        return preferences, None  # Never convert practice into discovery refusal.
    if preferences is None:
        return None, 'We can leave accent preferences for another time and continue with our usual listening practice.'
    if preferences.status == 'no_preference':
        return preferences, ('Of course. I won’t suggest accent practice or ask about accent preferences again.' if parsed.kind == 'suppress'
                             else 'Got it. No particular accent difficulty.') + ' We’ll continue with our usual listening practice.'
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
    # Compatibility adapter for older callers; no second classifier.
    parsed = interpret(text, offered=offered)
    return ({'request': 'select', 'accept': 'select', 'decline': 'decline',
             'unsupported': 'unavailable'}.get(parsed.kind), parsed.accent)


def resolve(lesson, text, voices):
    from app.lesson import AccentConsent, now
    confirmation = lesson.accent_confirmation
    context = (lesson.accent_consent.accent if confirmation
               else None if lesson.accent_resolved else lesson.accent_offered)
    parsed = (text if isinstance(text, AccentInterpretation) else
              interpret(text, offered=context, practice_question=confirmation))
    action = {'request': 'select', 'accept': 'select', 'decline': 'decline',
              'unsupported': 'unavailable'}.get(parsed.kind)
    accent = parsed.accent
    if parsed.kind in ('suppress', 'no_preference'):
        expire(lesson)
        lesson.accent_resolved = True
        return ('Of course. I won’t suggest accent practice or ask about accent preferences again. We’ll use our usual passage voice.'
                if parsed.kind == 'suppress' else
                'Got it. No particular accent difficulty. We’ll continue with our usual listening practice.')
    if action is None and parsed.kind == 'topic' and not (context or confirmation):
        return None  # An unrelated check-in does not resolve accent discovery.
    already_resolved = lesson.accent_resolved
    lesson.accent_resolved = True
    if action is None:
        if lesson.accent_consent.state == 'accepted' and parsed.kind == 'topic':
            # Old checkpoints may still contain the redundant confirmation.
            # A topic answer never revokes a grant already explicitly accepted.
            lesson.accent_confirmation = False
            return None
        if (context or confirmation or parsed.kind == 'ambiguous' and parsed.accent) and not already_resolved:
            # Existing flags encode one bounded clarification: resolved plus
            # confirmation with no grant. Preferences still cannot authorize it.
            lesson.accent_consent.accent = parsed.accent or context
            lesson.accent_confirmation = True
            return None
        expire(lesson)
        return ('We can leave accent practice for another time. We’ll use our usual passage voice.'
                if confirmation or parsed.kind == 'ambiguous' else None)
    lesson.accent_confirmation = False
    if action == 'decline' or accent not in voices:
        lesson.accent_consent = AccentConsent(state='declined')
        lesson.passage_accent = None
        return ('Of course. We’ll use our usual passage voice today.' if action == 'decline'
                else 'That accent isn’t available for practice yet. We’ll use our usual passage voice today.')
    lesson.accent_consent = AccentConsent(state='accepted', accent=accent,
        accepted_at=now(), exercise_number=lesson.exercises_completed)
    lesson.passage_accent = accent  # Compatibility display only; never authorizes routing.
    return f'Perfect, we’ll practice {LABELS[accent]} for the next new passage only.'


async def passage_audio(settings, provider, content, accent, limits=None, *, owner=None, lesson=None):
    voice = settings.available_passage_voices.get(accent)
    reservation = None
    # Audition sample rates (.75/1.0) are not an eligibility whitelist.
    # Preserve the adaptive rate exactly within the provider's documented range;
    # never clamp a slower lesson or change its difficulty to get accent audio.
    supported_rate = .7 <= content.speech_rate <= 1.2
    if (voice and settings.provider != 'demo' and settings.elevenlabs_api_key
            and supported_rate):
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
            except (httpx.HTTPError, ValueError, TimeoutError) as error:
                # Fixed categories only: no provider bodies, scripts, IDs, or secrets.
                from app.services.tutor_tts import failure_reason
                status = error.response.status_code if isinstance(error, HTTPStatusError) else None
                reason = (await failure_reason(error.response) if isinstance(error, HTTPStatusError)
                          else 'timeout' if isinstance(error, (TimeoutError, TimeoutException))
                          else 'network' if isinstance(error, httpx.HTTPError) else 'invalid_audio')
                logging.getLogger('uvicorn.error').warning('Accent passage outcome=provider_fallback reason=%s status=%s', reason, status)
            finally:
                await asyncio.shield(limits.call(limits.finish, reservation[0], outcome))
        else:
            logging.getLogger('audli').warning('Accent passage outcome=limit_fallback')
    elif accent:
        reason = ('disabled' if not settings.accent_practice_enabled else
                  'missing_voice' if not voice else
                  'missing_credentials' if not settings.elevenlabs_api_key else
                  'unsupported_rate' if not supported_rate else 'provider_disabled')
        logging.getLogger('audli').warning('Accent passage outcome=eligibility_fallback reason=%s', reason)
    return await provider.speech(content), PassageVoice(provider=settings.provider,
        voice_id=settings.voice, fallback=bool(accent))
