"""One interpretation of learner speech, before any preference/consent mutation.

Precedence: explicit current practice request; scoped question suppression;
offer acceptance/refusal; disclosure/none; ambiguity; unrelated conversation.
Availability and passage authorization are application decisions, not parsing.
"""
import re
from typing import Literal
from app.models import StrictModel, AccentPreferences
from app.accents import ALIASES, normalize


class AccentInterpretation(StrictModel):
    kind: Literal['request', 'accept', 'decline', 'suppress', 'preference',
                  'no_preference', 'ambiguous', 'unsupported', 'topic']
    accent: str | None = None
    preferences: AccentPreferences | None = None
    topic_text: str | None = None


_VARIETY = '(?:' + '|'.join(ALIASES.values()) + ')'
_AFFIRMATIVE = r'(?:yes|yeah|yep|sure|okay|ok|absolutely|sounds good)'
_POLITE = r'(?: please| thanks| thank you)?'
_PREFIX = re.compile(
    r"^(?:please )?(?:(?:can|could|would) (?:we|you|i) (?:please )?(?:use|try|practice|practise|do|hear|listen to)|"
    r"(?:i want|i'd like|i would like) to (?:use|try|practice|practise|do|hear|listen to)|"
    r"(?:i want|i'd like|i would like)(?= .+\b(?:practice|practise|passage|exercise|recording)\b)|"
    r"let's (?:use|try|practice|practise|do|hear|listen to)|use|try|practice|practise|switch(?: to)?|listen to|hear)\s+(.+)$")
_OBJECT = re.compile(
    r'(?:an? |the )?(?P<variety>' + _VARIETY + r')'
    r'(?: english)?(?: (?:listening )?(?:accents?|voice|recording|passage|exercise))?(?: practi[cs]e)?'
    r'(?: for (?:(?:the|our) )?(?:next|new) (?:listening )?(?:passage|exercise|recording))?'
    r'(?: (?:today|now|instead))?' + _POLITE + r'(?: about (?P<topic>.+))?$')
_ACCEPT = re.compile(
    rf'(?:{_AFFIRMATIVE}{_POLITE}|(?:{_AFFIRMATIVE} )?'
    r"(?:let's (?:do|try) (?:it|that)|(?:(?:i'd|i would) like|i want) to (?:try|practice|practise) (?:it|that)|"
    r'that sounds good))' + _POLITE + '$')
_DECLINE = re.compile(
    r"(?:no(?: thanks)?(?: not today)?|not today|skip(?: it| that)?|nope|cancel|"
    r"(?:no )?(?:i'd |i would |i )?rather not|(?:i )?(?:changed|change) my mind|"
    r"(?:use |try )?(?:the |our )?(?:usual|default|normal) (?:passage )?voice|"
    r"(?:i )?(?:don't|do not) want(?: (?:it|that|this)|(?: to (?:try|practice|use))? "
    r"(?:an? |the |any )?(?:" + _VARIETY + r" )?(?:accents?|english|voice))?)"
    + _POLITE + '$')
_SUPPRESS = re.compile(
    r"^(?:please |i want you to |i'd like you to |i would like you to )?(?:(?:stop|don't|do not) (?:asking|ask|suggesting|suggest|remembering|remember)"
    r'.*\baccents?\b|forget.*\baccent(?:s| preferences)?\b|no more accent(?:s| questions| suggestions)\b)')
_UNCERTAIN = re.compile(r"\b(?:maybe|perhaps|not sure|don't know|do not know|tomorrow|later|might|if)\b")
_TOPIC = re.compile(r"^(?:let's (?:talk|listen)|(?:can|could) we talk|i (?:enjoy|like))\b")


def _preference(text, accents=None, status='preferred'):
    return AccentPreferences(response=text.strip(), accents=accents or [], status=status)


def _unknown_variety(text):
    text = re.sub(r'\b' + _VARIETY + r'\b', 'known', text)
    names = re.findall(r'\b([a-z]+)\s+(?:english\s+)?(?:accents?|english)\b', text)
    return any(name not in ('known', 'an', 'the', 'another', 'different', 'one', 'any',
                           'usual', 'normal', 'default', 'english', 'requested', 'fast', 'slow', 'spoken')
               and not normalize(name) for name in names)


def _bare_choice(text):
    remaining = re.sub(_VARIETY, ' ', text)
    remaining = re.sub(r'\b(?:yes|yeah|yep|sure|okay|ok|please|thanks|an|a|the|english|accents?|and|or|instead|not sure|maybe|perhaps)\b', ' ', remaining)
    return not re.search(r'[a-z0-9]', remaining)


def interpret(text: str, *, offered: str | None = None, discovering: bool = False,
              practice_question: bool = False) -> AccentInterpretation:
    """Context disambiguates bare replies; never overrides an explicit request.

The complete result is consumed once. A preference is never a practice grant.
Compatible compound intents may suppress future questions AND request practice
now; they are represented together rather than competing downstream classifiers.
"""
    normalized = text.lower().replace('’', "'")
    answer = ' '.join(re.sub(r'[,!.?;]', ' ', normalized).split())
    clauses = [' '.join(clause.strip(' ,').split()) for clause in
               re.split(r'[.!?;]|\bbut\b|,\s*(?=not\b)', normalized)]
    conversation_topic = next((clause for clause in clauses if _TOPIC.search(clause)), None)
    accent_answer = ' '.join(clause for clause in clauses if not _TOPIC.search(clause))
    accent_answer = ' '.join(re.sub(r'[,!.?;]', ' ', accent_answer).split())
    def result(*, kind, **kwargs):
        kwargs.setdefault('topic_text', conversation_topic)
        return AccentInterpretation(kind=kind, **kwargs)
    suppress = any(_SUPPRESS.search(' '.join(clause.strip(' ,').split())) for clause in clauses)
    suppression = _preference(text, status='no_preference') if suppress else None
    candidates = []
    unsupported = False
    uncertain_request = False
    unrelated_request = False
    for position, clause in enumerate(clauses):
        clause = ' '.join(clause.strip(' ,').split())
        clause = re.sub(r'^' + _AFFIRMATIVE + r'[, ]+', '', clause)
        tentative = bool(re.match(r'^(?:maybe|perhaps) ', clause))
        request = _PREFIX.fullmatch(re.sub(r'^(?:maybe|perhaps) ', '', clause))
        if not request:
            continue
        target = request[1]
        accents = normalize(target)
        if not accents and not _unknown_variety(target):
            unrelated_request = True
        if tentative or _UNCERTAIN.search(clause) or len(accents) > 1:
            uncertain_request |= bool(accents or _unknown_variety(target))
            # Unknown alternatives must not be silently substituted.
            unsupported |= _unknown_variety(target)
            continue
        match = _OBJECT.fullmatch(target)
        if match:
            candidates.append((normalize(match['variety'])[0], match['topic'], position))
        elif _unknown_variety(target):
            unsupported = True
        elif accents and re.search(r'\b(?:accent|english|passage|recording|exercise)\b', target):
            uncertain_request = True
    if unsupported:
        return result(kind='unsupported', preferences=suppression)
    if uncertain_request or len({accent for accent, _, _ in candidates}) > 1:
        return result(kind='ambiguous', accent=normalize(answer)[0] if normalize(answer) else None, preferences=suppression)
    if candidates:
        accent, topic, position = candidates[-1]
        if any(index > position and _DECLINE.fullmatch(' '.join(clause.replace(',', ' ').split()))
               and (not normalize(clause) or accent in normalize(clause))
               for index, clause in enumerate(clauses)):
            return result(kind='ambiguous', accent=accent, preferences=suppression)
        return result(kind='request', accent=accent,
                      preferences=suppression, topic_text=topic or conversation_topic)
    if suppress:
        return result(kind='suppress', preferences=suppression)
    accents = normalize(accent_answer)
    uncertain = bool(_UNCERTAIN.search(accent_answer))
    if not uncertain and len(accents) == 1 and _bare_choice(accent_answer) and re.search(r'\binstead\b', answer):
        return result(kind='request', accent=accents[0])
    if not uncertain and (offered or practice_question):
        if len(accents) == 1 and _bare_choice(accent_answer):
            return result(kind='accept', accent=accents[0])
        if offered and _ACCEPT.fullmatch(accent_answer):
            return result(kind='accept', accent=offered)
    if any(_DECLINE.fullmatch(' '.join(re.sub(r'[,]', ' ', clause).split())) for clause in clauses) or answer == 'not really':
        if discovering and not (offered or practice_question):
            return result(kind='no_preference', preferences=_preference(text, status='no_preference'))
        return result(kind='decline')
    if (discovering and accent_answer in ('none', 'any accent', 'no preference') or
            re.search(r'\b(?:no accent preference|no particular accent difficulty)\b', answer)):
        return result(kind='no_preference', preferences=_preference(text, status='no_preference'))
    # Named tentative disclosures are preferences, never consent. Bare uncertainty
    # is unresolved, never an instruction to stop future questions.
    disclosure = bool(re.search(r'\b(?:prefer|preference|difficult|difficulty|tricky|struggle|remember|interest|interested)\b', accent_answer))
    speech = bool(re.search(r'\b(?:' + _VARIETY + r')\s+(?:(?:and|or) (?:' + _VARIETY + r') )?(?:accents?|english|speech|voice|audio|recordings?)\b', accent_answer))
    if (accents and (disclosure or _bare_choice(accent_answer) or speech) or
            _unknown_variety(accent_answer) and re.search(r'\b(?:accents?|english)\b', accent_answer)):
        topic = conversation_topic
        mixed = re.fullmatch(r'(.+?),? with (.+)', answer)
        if mixed and not disclosure:
            topic = mixed[1].rstrip(',')
        return result(kind='preference', preferences=_preference(text, accents), topic_text=topic)
    if _TOPIC.search(answer):
        return result(kind='topic', topic_text=text)
    if uncertain and not unrelated_request or _ACCEPT.fullmatch(accent_answer):
        return result(kind='ambiguous')
    return result(kind='topic')
