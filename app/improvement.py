"""Advisory Review language coaching (AUD-77): pure domain rules, no I/O and no provider calls.

Coaching never feeds scores, adaptation, evidence or the daily lesson allowance. It receives only the learner's own
response text. A suggestion is shown only if every layer below accepts it; otherwise it is rejected, never presented
as "improved English":

  1. provider self-report (meaning preserved, nothing added)
  2. structure: bounded size, at most two notes, every edit explained by a note, no net content added or removed
  3. deterministic meaning classes: negation, modality, quantity, comparison, certainty, causality, named entities
  4. an independent provider comparison of the two texts (applied by the API layer after 1-3 pass)
"""
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from hashlib import sha256
from typing import Literal

from pydantic import Field

from app.models import StrictModel

PROMPT_VERSION = 'v1'
MIN_SOURCE_WORDS = 4
MAX_SOURCE_CHARS = 1200
MAX_NOTES = 2


class Note(StrictModel):
    kind: Literal['grammar', 'vocabulary']
    before: str = Field(min_length=1, max_length=160)
    after: str = Field(min_length=1, max_length=160)
    why: str = Field(min_length=1, max_length=240)


class LanguageCoaching(StrictModel):
    """Provider output contract. Both flags are self-reports and only ever make acceptance stricter."""
    improved_text: str = Field(min_length=1, max_length=2400)
    notes: list[Note] = Field(max_length=MAX_NOTES)
    meaning_preserved: bool
    adds_information: bool


class MeaningCheck(StrictModel):
    same_meaning: bool


class DiffSegment(StrictModel):
    op: Literal['equal', 'delete', 'insert']
    text: str


class ImprovementState(StrictModel):
    """What Review shows. `improved_text` exists only when every layer accepted it."""
    state: Literal['unavailable', 'available', 'pending', 'ready', 'rejected']
    reason: str | None = None
    improved_text: str | None = None
    unchanged: bool = False
    notes: list[Note] = Field(default_factory=list)
    diff: list[DiffSegment] = Field(default_factory=list)


def source_hash(text: str) -> str:
    return sha256(text.strip().encode()).hexdigest()


# ---------------------------------------------------------------------------------------------------- eligibility
def reliable(transcription: dict | None, confirmed_text: str | None, minimum_confidence: float) -> bool:
    """A learner-edited transcript is learner-confirmed. An unedited one must have been recognized confidently."""
    transcription = transcription if isinstance(transcription, dict) else {}
    recognized = str(transcription.get('text') or '').strip()
    confirmed = (confirmed_text or '').strip()
    if confirmed and recognized and confirmed != recognized:
        return True
    if transcription.get('uncertainty'):
        return False
    if transcription.get('source', 'openai') == 'openai':
        confidence = transcription.get('confidence')
        if not isinstance(confidence, (int, float)) or confidence < minimum_confidence:
            return False
    return True


def eligibility(text: str | None, transcription: dict | None, confirmed_text: str | None,
                minimum_confidence: float) -> tuple[str | None, str | None]:
    """(text to coach, None) or (None, reason). No saved answer is not an error and has no reason shown."""
    body = (text or '').strip()
    if not body:
        return None, 'no_saved_answer'
    if not reliable(transcription, confirmed_text, minimum_confidence):
        return None, 'unreliable_recognition'
    if len(body.split()) < MIN_SOURCE_WORDS:
        return None, 'too_short'
    if len(body) > MAX_SOURCE_CHARS:
        return None, 'too_long'
    return body, None


# ---------------------------------------------------------------------------------------------------- tokens
_WORD = re.compile(r"[^\W_]+(?:['’][^\W_]+)?")
_TOKEN = re.compile(r"\s+|[^\W_]+(?:['’][^\W_]+)?|.", re.DOTALL)  # Total: every character belongs to exactly one token.


def words(text: str) -> list[str]:
    return [w.replace('’', "'") for w in _WORD.findall(text)]


def lower_words(text: str) -> list[str]:
    return [w.lower() for w in words(text)]


NEGATIONS = {'not', 'no', 'never', 'none', 'nobody', 'nothing', 'nowhere', 'neither', 'nor', 'without', 'cannot', 'nope',
             'barely', 'hardly', 'scarcely', 'cant', 'dont', 'doesnt', 'didnt', 'wont', 'wouldnt', 'couldnt', 'shouldnt',
             'isnt', 'arent', 'wasnt', 'werent', 'hasnt', 'havent', 'hadnt', 'aint', 'mustnt', 'neednt'}
CONTRACTED_MODALS = {"can't": 'can', 'cannot': 'can', "won't": 'will', "shan't": 'shall', "wouldn't": 'would',
                     "couldn't": 'could', "shouldn't": 'should', "mustn't": 'must', "mightn't": 'might', "needn't": 'need',
                     'cant': 'can', 'wont': 'will', 'wouldnt': 'would', 'couldnt': 'could', 'shouldnt': 'should',
                     'mustnt': 'must', 'neednt': 'need'}
MODALS = {'can', 'could', 'may', 'might', 'must', 'should', 'would', 'will', 'shall', 'ought'}

NUMBER_WORDS = {'zero': 0, 'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6, 'seven': 7, 'eight': 8, 'nine': 9,
                'ten': 10, 'eleven': 11, 'twelve': 12, 'thirteen': 13, 'fourteen': 14, 'fifteen': 15, 'sixteen': 16,
                'seventeen': 17, 'eighteen': 18, 'nineteen': 19, 'twenty': 20, 'thirty': 30, 'forty': 40, 'fifty': 50,
                'sixty': 60, 'seventy': 70, 'eighty': 80, 'ninety': 90, 'hundred': 100, 'thousand': 1000, 'million': 10 ** 6,
                'billion': 10 ** 9, 'dozen': 12, 'half': .5, 'double': 2, 'twice': 2, 'once': 1, 'first': 1, 'second': 2,
                'third': 3, 'fourth': 4, 'fifth': 5}
QUANTIFIER_CLASSES = {
    'ALL': {'all', 'every', 'each', 'everybody', 'everyone', 'everything', 'whole', 'entire'},
    'SOME': {'some', 'any', 'several', 'somebody', 'someone', 'something', 'anybody', 'anyone', 'anything', 'certain'},
    'MANY': {'many', 'much', 'lots', 'lot', 'plenty', 'numerous', 'majority'},
    'MOST': {'most'},
    'FEW': {'few', 'little', 'fewer', 'fewest'},
    'BOTH': {'both'},
    'ENOUGH': {'enough', 'sufficient', 'insufficient'},
    'ONLY': {'only', 'just', 'merely', 'solely'},
}
QUANTIFIER_WORDS = set().union(*QUANTIFIER_CLASSES.values())
LESS_WORDS = {'less', 'least', 'fewer', 'fewest', 'lower', 'lowest', 'smaller', 'smallest', 'shorter', 'shortest',
              'slower', 'slowest', 'worse', 'worst', 'weaker', 'weakest', 'cheaper', 'cheapest', 'younger', 'youngest',
              'easier', 'easiest', 'lighter', 'lightest', 'fewer', 'minus'}
COMPARATIVE_FORMS = {'better', 'best', 'bigger', 'biggest', 'larger', 'largest', 'greater', 'greatest', 'higher', 'highest',
                     'longer', 'longest', 'faster', 'fastest', 'harder', 'hardest', 'older', 'oldest', 'stronger',
                     'strongest', 'heavier', 'heaviest', 'richer', 'richest', 'taller', 'tallest', 'wider', 'widest',
                     'deeper', 'deepest', 'cleaner', 'cleanest', 'happier', 'happiest', 'earlier', 'earliest', 'later',
                     'latest', 'newer', 'newest', 'bigger', 'closer', 'closest', 'nearer', 'nearest', 'further',
                     'furthest', 'farther', 'farthest', 'busier', 'busiest', 'safer', 'safest', 'louder', 'loudest'} | LESS_WORDS
CERTAINTY_CLASSES = {
    'BOOST': {'definitely', 'certainly', 'surely', 'absolutely', 'obviously', 'clearly', 'completely', 'totally', 'exactly',
              'sure', 'certain', 'undoubtedly', 'guaranteed', 'must'},
    'HEDGE': {'maybe', 'perhaps', 'possibly', 'probably', 'might', 'seems', 'seem', 'apparently', 'somewhat', 'likely',
              'unlikely', 'suppose', 'guess', 'believe', 'think', 'thinks', 'thought', 'seemed', 'allegedly', 'supposedly',
              'unsure', 'doubt'},
    'ALWAYS': {'always', 'constantly', 'forever'},
    'USUALLY': {'usually', 'normally', 'typically', 'generally'},
    'OFTEN': {'often', 'frequently', 'regularly'},
    'SOMETIMES': {'sometimes', 'occasionally'},
    'RARELY': {'rarely', 'seldom'},
}
CAUSAL_CLASSES = {
    'BECAUSE': {'because', 'since', 'due', 'cause', 'caused', 'causes', 'reason', 'reasons', 'thanks'},
    'RESULT': {'therefore', 'thus', 'hence', 'consequently', 'result', 'resulted', 'results', 'led', 'lead', 'leads'},
    'IF': {'if'}, 'UNLESS': {'unless'}, 'OTHERWISE': {'otherwise'},
    'CONTRAST': {'but', 'however', 'although', 'though', 'whereas', 'while', 'despite', 'yet', 'nevertheless', 'instead'},
}
_SO_RESULT = re.compile(r"(?:^|[.!?,;]\s*)so\b|\bso\s+(?:i|we|he|she|it|they|you|the|my|our|their|there|this|that|a|an)\b", re.I)
_PHRASE_MODALS = (('have to', 'have_to'), ('has to', 'have_to'), ('had to', 'have_to'), ('having to', 'have_to'),
                  ('supposed to', 'supposed_to'), ('going to', 'will'), ('gonna', 'will'), ('able to', 'can'),
                  ('allowed to', 'may'), ('had better', 'should'))

STOPWORDS = {
    'a', 'an', 'the', 'and', 'or', 'of', 'to', 'in', 'on', 'at', 'by', 'for', 'with', 'from', 'up', 'down', 'out', 'off', 'over',
    'into', 'onto', 'about', 'as', 'than', 'that', 'this', 'these', 'those', 'it', 'its', 'he', 'him', 'his', 'she', 'her',
    'hers', 'they', 'them', 'their', 'theirs', 'we', 'us', 'our', 'ours', 'you', 'your', 'yours', 'i', 'me', 'my', 'mine',
    'is', 'am', 'are', 'was', 'were', 'be', 'been', 'being', 'do', 'does', 'did', 'done', 'doing', 'have', 'has', 'had',
    'having', 'will', 'would', 'can', 'could', 'may', 'might', 'must', 'should', 'shall', 'there', 'here', 'then', 'so',
    'very', 'really', 'quite', 'too', 'also', 'um', 'uh', 'er', 'erm', 'hmm', 'well', 'like', 'okay', 'ok', 'yeah', 'actually',
    'basically', 'literally', 'which', 'who', 'whom', 'whose', 'what', 'when', 'where', 'how', 'why', 'if', 'but', 'because',
    'not', 'no', 'nor', 'one', 'ones', 'such', 'own', 'same', 'other', 'another', 'get', 'got', 'gets', 'getting',
    "n't", 's', 't', 'd', 'll', 've', 're', 'm',
    # Light verb: "do a decision" -> "make a decision" is a collocation repair, not new content. Layer 4 still compares meaning.
    'make', 'makes', 'made', 'making',
}


def stem(word: str) -> str:
    word = word.lower().replace('’', "'")
    word = re.sub(r"'s?$", '', word)
    for suffix, replacement in (('ies', 'y'), ('ied', 'y'), ('ing', ''), ('edly', ''), ('ed', ''), ('es', ''), ('ly', ''), ('s', '')):
        if len(word) > len(suffix) + 2 and word.endswith(suffix):
            word = word[:-len(suffix)] + replacement
            break
    return word[:-1] if len(word) > 4 and word.endswith('e') else word


def content_stems(text: str) -> set[str]:
    return {stem(w) for w in lower_words(text) if w not in STOPWORDS and w not in NEGATIONS and not w.isdigit()
            and w not in NUMBER_WORDS and w not in QUANTIFIER_WORDS and not re.match(r"^\w+n't$", w)}


# ---------------------------------------------------------------------------------------------------- diff
def tokens(text: str) -> list[str]:
    return _TOKEN.findall(text)


def diff(source: str, improved: str) -> list[DiffSegment]:
    """Deterministic word-level changes. Whitespace and punctuation travel with the words around them."""
    a, b = tokens(source), tokens(improved)
    segments: list[DiffSegment] = []

    def add(op, parts):
        text = ''.join(parts)
        if not text:
            return
        if segments and segments[-1].op == op:
            segments[-1] = DiffSegment(op=op, text=segments[-1].text + text)
        else:
            segments.append(DiffSegment(op=op, text=text))

    for tag, i1, i2, j1, j2 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == 'equal':
            add('equal', a[i1:i2])
        else:
            add('delete', a[i1:i2]) if i2 > i1 else None
            add('insert', b[j1:j2]) if j2 > j1 else None
    return segments


def changed_words(source: str, improved: str) -> tuple[list[str], list[str]]:
    """Words removed from and added to the learner's text (case-insensitive; whitespace/punctuation excluded)."""
    a, b = words(source), words(improved)
    removed, added = [], []
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, [w.lower() for w in a], [w.lower() for w in b], autojunk=False).get_opcodes():
        if tag != 'equal':
            removed.extend(a[i1:i2])
            added.extend(b[j1:j2])
    return removed, added


# ---------------------------------------------------------------------------------------------------- meaning classes
def negation_present(text: str) -> bool:
    lowered = text.lower().replace('’', "'")
    if re.search(r"\w+n't\b", lowered) or re.search(r'\bno one\b', lowered):
        return True
    return any(w in NEGATIONS for w in lower_words(text))


def modality(text: str) -> set[str]:
    cleaned = text.lower().replace('’', "'")
    lowered = ' ' + ' '.join(re.findall(r"[a-z']+", cleaned)) + ' '
    found = set()
    for word in lower_words(text):
        word = CONTRACTED_MODALS.get(word, word)
        word = re.sub(r"n't$", '', word)
        if word in MODALS:
            found.add(word)
        elif word == 'need' and re.search(r"\bneedn't\b|\bneed not\b", cleaned):
            found.add('need')
    for phrase, name in _PHRASE_MODALS:
        if f' {phrase} ' in lowered:
            found.add(name)
    # Obligation "need to" in any inflection is one modal; the plain verb or noun "need" ("I need help") is not.
    if re.search(r'\b(?:need|needs|needed|needing) to\b', lowered):
        found.add('need')
    return found


def numbers(text: str) -> list[float]:
    values = []
    for word in lower_words(text):
        if word.isdigit():
            values.append(float(word))
        elif word in NUMBER_WORDS:
            values.append(float(NUMBER_WORDS[word]))
    return sorted(values)


def quantifiers(text: str) -> set[str]:
    found = classes(text, QUANTIFIER_CLASSES)
    if negation_present(text):
        found.discard('SOME')  # "no time" and "don't have any time" say the same thing.
    return found


def classes(text: str, mapping: dict[str, set[str]]) -> set[str]:
    present = set(lower_words(text))
    return {name for name, group in mapping.items() if present & group}


def comparison_signals(text: str) -> tuple[bool, set[str], set[str]]:
    present = set(lower_words(text))
    return 'than' in present, present & LESS_WORDS, present & COMPARATIVE_FORMS


def certainty(text: str) -> set[str]:
    found = classes(text, CERTAINTY_CLASSES)
    if re.search(r"\b(?:i|we)\s+(?:do\s*not|don't|dont)\s+(?:know|think)\b", text.lower().replace('’', "'")):
        found.add('HEDGE')
    return found


def causality(text: str) -> set[str]:
    found = classes(text, CAUSAL_CLASSES)
    if _SO_RESULT.search(text.replace('’', "'")):
        found.add('RESULT')
    return found


def named_entities(text: str) -> set[str]:
    """Capitalized words (including a sentence's first content word), acronyms and CamelCase, lowercased.

    A sentence-initial content word cannot be told from a name without a dictionary, so it is treated as one:
    replacing it is rejected rather than risked."""
    found = set()
    for match in _WORD.finditer(text):
        word = match.group().replace('’', "'")
        before = text[:match.start()].rstrip()
        starts = not before or before[-1] in '.!?\n'
        if word == 'I' or re.match(r"^I'", word) or word.isdigit() or word.lower() in STOPWORDS or word.lower() in NEGATIONS:
            continue
        if word[0].isupper() or (len(word) > 1 and word.isupper()) or re.search(r'[a-z][A-Z]', word):
            found.add(word.lower())
    return found


def _present_elsewhere(entities: set[str], other: str) -> bool:
    other_stems = {stem(w) for w in words(other)}
    return all(stem(entity) in other_stems for entity in entities)


# ---------------------------------------------------------------------------------------------------- validation
def _norm(text: str) -> str:
    return ' '.join(lower_words(text))


def reject_reason(source: str, coaching: LanguageCoaching) -> str | None:
    """None when layers 1-3 accept. Otherwise a fixed, non-sensitive rejection code."""
    improved = coaching.improved_text.strip()
    # Layer 1: provider self-report. Either flag makes the result unusable.
    if not coaching.meaning_preserved:
        return 'meaning_not_preserved'
    if coaching.adds_information:
        return 'information_added'
    # Layer 2: structure.
    n_source, n_improved = len(words(source)), len(words(improved))
    if n_improved == 0 or n_improved > n_source * 1.6 + 3 or n_improved < n_source * .6 - 1:
        return 'length_changed'
    if len(coaching.notes) > MAX_NOTES:
        return 'too_many_notes'
    removed, added = changed_words(source, improved)
    changed = bool(removed or added) or _norm(source) != _norm(improved)
    if not changed:  # Capitalization and punctuation only; a note is optional, but an identical answer cannot carry one.
        return 'inconsistent_notes' if coaching.notes and is_unchanged(source, improved) else None
    if not coaching.notes:
        return 'unexplained_changes'
    source_norm, improved_norm = _norm(source), _norm(improved)
    explained: set[str] = set()
    for note in coaching.notes:
        before, after = _norm(note.before), _norm(note.after)
        if not before or not after or before == after or before not in source_norm or after not in improved_norm:
            return 'note_not_grounded'
        explained |= set(before.split()) | set(after.split())
    if any(w.lower() not in explained for w in removed + added):
        return 'unexplained_changes'
    if len(removed) + len(added) > max(8, int(.6 * (n_source + n_improved))):
        return 'too_many_changes'
    old_stems, new_stems = content_stems(source), content_stems(improved)
    gained, lost = new_stems - old_stems, old_stems - new_stems
    if len(gained) > len(lost):
        return 'content_added'
    if len(lost) > len(gained) + 1:
        return 'content_removed'
    # Layer 3: deterministic meaning classes.
    if negation_present(source) != negation_present(improved):
        return 'negation_changed'
    if modality(source) != modality(improved):
        return 'modality_changed'
    if numbers(source) != numbers(improved) or quantifiers(source) != quantifiers(improved):
        return 'quantity_changed'
    than_a, less_a, forms_a = comparison_signals(source)
    than_b, less_b, forms_b = comparison_signals(improved)
    if than_a != than_b or less_a != less_b:
        return 'comparison_changed'
    more_a, more_b = {'more', 'most'} & set(lower_words(source)), {'more', 'most'} & set(lower_words(improved))
    # "more fast" <-> "faster" and a dropped doubled "more taller" are grammar; a swapped comparative adjective is not.
    if not more_a and not more_b and forms_a != forms_b:
        return 'comparison_changed'
    if ('most' in more_a) != ('most' in more_b):
        return 'comparison_changed'
    if certainty(source) != certainty(improved):
        return 'certainty_changed'
    if causality(source) != causality(improved):
        return 'causality_changed'
    entities_a, entities_b = named_entities(source), named_entities(improved)
    if not (_present_elsewhere(entities_a, improved) and _present_elsewhere(entities_b, source)):
        return 'entity_changed'
    return None


def is_unchanged(source: str, improved: str) -> bool:
    return ' '.join(source.split()) == ' '.join(improved.split())


def view(source: str, improved: str, notes: list[Note]) -> ImprovementState:
    unchanged = is_unchanged(source, improved)
    return ImprovementState(state='ready', improved_text=improved, unchanged=unchanged,
        notes=[] if unchanged else notes, diff=[] if unchanged else diff(source, improved))


UNAVAILABLE_REASONS = {'unreliable_recognition', 'too_short', 'too_long'}


def available_state(reason: str | None) -> ImprovementState | None:
    """The state for a passage with no live improvement. `None` hides the feature when there is no saved answer."""
    if reason == 'no_saved_answer':
        return None
    if reason in UNAVAILABLE_REASONS:
        return ImprovementState(state='unavailable', reason=reason)
    return ImprovementState(state='available')


@dataclass
class Outcome:
    """The decision for one answer. `rejection` is a fixed code; a rejected candidate text is never kept."""
    status: Literal['ready', 'rejected']
    improved_text: str | None = None
    notes: list[dict] = field(default_factory=list)
    rejection: str | None = None
    verified: bool = False  # True when layer 4 (the independent comparison) was consulted and agreed.


async def coach(provider, text: str) -> Outcome:
    """The one decision path shared by the API and the evaluation harness. Provider errors propagate to the caller."""
    coaching = await provider.coach_language(text)
    rejection = reject_reason(text, coaching)
    if rejection is not None:
        return Outcome('rejected', rejection=rejection)
    improved = coaching.improved_text.strip()
    verified = False
    if not is_unchanged(text, improved):
        # Layer 4: an independent comparison of the two texts. Both texts only, never the passage.
        if not (await provider.check_meaning(text, improved)).same_meaning:
            return Outcome('rejected', rejection='meaning_check_failed')
        verified = True
    return Outcome('ready', improved_text=improved, notes=[n.model_dump(mode='json') for n in coaching.notes], verified=verified)
