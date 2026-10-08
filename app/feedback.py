"""Terminal coaching copy, separate from evaluator notes and follow-up questions.

Compose from validated final evidence and the deterministic adaptation event.
Never forward arbitrary evaluator prose to the spoken closing turn.
"""
import re

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


def final_feedback(evaluation, event, maximum_words: int = 50, content=None, previous_advice=()) -> str:
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
    from app.lesson import safe_label, evidence_gaps, evidence_tip
    data = evaluation.model_dump() if hasattr(evaluation, 'model_dump') else vars(evaluation)
    gaps = evidence_gaps(data, content.model_dump() if content else None)
    specific = next((item for item in gaps if evidence_tip(*item) not in previous_advice), None)
    transition = evidence_tip(*specific) if specific else 'We’ll build on this listening practice.'
    if content is not None and main == 'demonstrated':
        label = safe_label(content.main_idea)
        if label:
            candidate = f'You understood that {label[0].lower() + label[1:]}. {transition}'
            try:
                validate_final_feedback(candidate, maximum_words)
                observation = candidate[:-len(transition)].strip()
            except ValueError:
                pass
    if evaluation.misunderstood or gaps:
        label = (specific or (gaps[0] if gaps else (evaluation.misunderstood[0].strip().rstrip('.!'), 'details')))[0]
        framing = 'One correction' if any(safe_label(value) == label for value in evaluation.misunderstood) else 'One part to build on'
        candidate = f'{observation} {framing}: {label}. {transition}'
        try:
            validate_final_feedback(candidate, maximum_words)
            return candidate
        except ValueError:
            observation = ('Some of the meaning came through, but there was a misunderstanding.' if evaluation.understood
                           else 'That was a challenging listen.') if evaluation.misunderstood else 'Some meaning came through; there is more to practice.'
    elif dimensions and not unknown and all(
            status == 'demonstrated' for status in dimensions.values()):
        observation = 'Nice work. You caught the main idea and the important details.'
        if content is not None:
            label = next((safe_label(value) for value in content.important_details if safe_label(value)), None)
            if label:
                candidate = f'You caught the main idea and important details, including that {label[0].lower() + label[1:]}. {transition}'
                try:
                    validate_final_feedback(candidate, maximum_words)
                    return candidate
                except ValueError:
                    pass
    text = f'{observation} {transition}'
    validate_final_feedback(text, maximum_words)
    return text
