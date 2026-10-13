"""Learner-stated interests only; no ability inference or voice selection."""
import re
from app.models import AccentPreferences

from app.accent_registry import LABELS, ALIASES
UNAVAILABLE = 'Accent-specific recordings aren’t available yet, so today’s audio will use our usual voice.'

def normalize(text: str) -> list[str]:
    result = []
    for key, aliases in ALIASES.items():
        for match in re.finditer(r'\b(?:' + aliases + r')\b', text, re.I):
            if not re.search(r'\b(?:not|no|except|rather than)\s+$', text[:match.start()], re.I):
                result.append(key)
                break
    if any(key in result for key in ('southern', 'new_york', 'boston')):
        result = [key for key in result if key != 'american']
    return result

def no_preference(text: str) -> bool:
    from app.accent_intent import interpret
    return interpret(text, discovering=True).kind in ('no_preference', 'suppress')

def extract(text: str) -> AccentPreferences | None:
    from app.accent_intent import interpret
    parsed = interpret(text, discovering=True)
    # Onboarding has no active lesson/passage to authorize. A practice request
    # can disclose interest here; lesson consent is obtained in the lesson itself.
    if parsed.kind in ('request', 'accept') and parsed.accent:
        return AccentPreferences(response=text.strip(), accents=[parsed.accent], status='preferred')
    return parsed.preferences

def only_accent_request(text: str) -> bool:
    from app.accent_intent import interpret
    parsed = interpret(text, discovering=True)
    return parsed.kind != 'topic' and parsed.topic_text is None

def lesson_preference(text: str, suggested: str | None = None) -> AccentPreferences | None:
    from app.accent_intent import interpret
    return interpret(text, discovering=bool(suggested)).preferences

def acknowledgment(preferences: AccentPreferences) -> str:
    if preferences.status == 'no_preference':
        return 'I won’t suggest an accent preference. We’ll continue with our usual listening practice.'
    labels = ', '.join(LABELS[item] for item in preferences.accents)
    return (f'I’ll remember your interest in {labels}. ' if labels else 'I’ll remember that accent preference. ') + UNAVAILABLE
