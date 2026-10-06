"""Terminal coaching copy, separate from evaluator notes and follow-up questions.

Compose from validated final evidence and the deterministic adaptation event.
Never forward arbitrary evaluator prose to the spoken closing turn.
"""
import re
from app.conversation import next_message

_REQUEST = re.compile(
    r"\b(could you|can you|would you|tell me|what does|what did|why do|"
    r"try to|remember to|find more|identify more|please answer)\b", re.I)


def validate_final_feedback(text: str, maximum_words: int = 50) -> None:
    if not text.strip() or len(text.split()) > maximum_words:
        raise ValueError('Terminal feedback exceeds its word budget or is empty')
    if any(mark in text for mark in ('?', '？')) or _REQUEST.search(text):
        raise ValueError('Terminal feedback must not request learner evidence')
    if re.search(r'\d|%|\bpercent(?:age)?\b', text, re.I):
        raise ValueError('Terminal feedback must not read numerical metrics')
    if len(re.findall(r'[.!]+(?:\s|$)', text)) > 3:
        raise ValueError('Terminal feedback exceeds its sentence budget')


def final_feedback(evaluation, event, maximum_words: int = 50) -> str:
    """One observation, optionally one grounded correction, then the next action.

    Unknown units never enter the correction path. If a passage-specific label
    cannot safely fit, choose a complete shorter message, never truncate prose.
    """
    dimensions = getattr(evaluation, 'dimensions', {})
    unknown = getattr(evaluation, 'insufficient_evidence', [])
    main = dimensions.get('main_idea', 'insufficient_evidence')
    if main == 'demonstrated':
        observation = 'You caught the main idea.'
    elif main == 'partially_demonstrated':
        observation = 'You caught part of the main idea.'
    elif main == 'insufficient_evidence':
        observation = 'Thanks for sharing what you understood.'
    else:
        observation = 'That was a challenging listen.'
    transition = next_message(event)
    if evaluation.misunderstood:
        label = evaluation.misunderstood[0].strip().rstrip('.!')
        candidate = f'{observation} One correction: {label}. {transition}'
        try:
            validate_final_feedback(candidate, maximum_words)
            return candidate
        except ValueError:
            observation = 'Some of the meaning came through, but there was a misunderstanding.' if evaluation.understood else 'That was a challenging listen.'
    elif dimensions and not unknown and all(
            status == 'demonstrated' for status in dimensions.values()):
        observation = 'Nice work. You caught the main idea and the important details.'
    text = f'{observation} {transition}'
    validate_final_feedback(text, maximum_words)
    return text
