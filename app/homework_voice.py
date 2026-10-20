"""Conservative homework intent: recognition is gated before this pure parser.

No model instructions, URL assignment, assessment or learner-state mutation.
"""
import re
from app.homework import Decision
from app.models import Transcription

from app.recognition import SHORT_REPLY_WARNING

SHORT_ASSESSMENT_WARNING = SHORT_REPLY_WARNING   # historical name, one source of truth (AUD-71)


def reliable_choice(transcription: Transcription, minimum: float) -> bool:
    """A short known choice is not comprehension evidence. Keep all other gates."""
    return bool(spoken_decision(transcription.text) is not None
                and transcription.confidence is not None and transcription.confidence >= minimum
                and not [warning for warning in transcription.uncertainty if warning != SHORT_ASSESSMENT_WARNING])


def spoken_decision(text: str) -> Decision | None:
    value = re.sub(r"[^a-z0-9' ]", ' ', text.lower())
    value = ' '.join(value.split())
    # Match complete utterances. Mixed decisions and unrecognized speech need retry.
    if value in {'no', 'no thanks', 'no thank you', 'not today', 'not now', 'maybe later',
                 'skip', 'skip homework', "i don't want homework", 'no homework today', 'maybe next time'}:
        return Decision(accept=False)
    yes = r"(?:yes|yes please|yeah|yeah sure|yes sure|sure|sure please|okay|ok|please do|i would like that|i'd like that|that sounds good)"
    if re.fullmatch(yes, value):
        return Decision(accept=True)
    match = re.fullmatch(r'(?:' + yes + r' (?:about|on)|(?:i would like|i\x27d like|something|let\x27s listen) (?:about|on|to)|(?:can|could) you find (?:me )?something (?:about|on)) ([a-z0-9 ]{1,100}?)(?: instead)?', value)
    if match and not re.search(r'\b(no|not|skip|instead|but)\b', match[1]):
        return Decision(accept=True, topic=match[1])
    return None
