"""Automated quality gates for a Daily Challenge candidate. A candidate is published only if EVERY gate passes; any failure rejects it permanently.

Five deterministic gates run first and cost nothing: content validity, answer consistency, audio readiness, safety and duplicate prevention. Only a candidate that
survives them reaches the one bounded semantic review (a small model call, reserved against its own budget): it is the check that deterministic rules cannot do
(is this the same story in different words, is it coherent, is it safe in context). If that review cannot run, the candidate is NOT published (fail closed).

Everything here is a pure function of a Snapshot, a Corpus and a verdict, so it is tested without a database or a provider. `load_snapshot`/`load_corpus` read
the database. Only SHARED lessons and earlier daily challenges form the corpus: another learner's private lesson is never compared and never sent to a model.
"""
import hashlib
import math
import re
from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import and_, select

from app.listening import schema as t
from app.listening.domain import BLANK, normalize_text

REQUIRED_KINDS = ('multiple_choice', 'spoken', 'fill_blank', 'sequencing')
SHINGLE = 4
NEAR_DUPLICATE_SHINGLES = 0.25           # share of 4-word runs two scripts have in common: above this it is a copy or a light rewrite
COSINE_REJECT, COSINE_REVIEW = 0.62, 0.30  # content-word similarity: reject outright above the first, ask the semantic review about neighbours above the second
TITLE_SIMILAR = 0.75
SPEECH_WPM = (60, 330)                   # a lesson whose audio length implies a speaking rate outside this is mis-timed or truncated
SPEAKER_ACCENTS_REQUIRED = True

STOP = frozenset('a an the and or but if so to of in on at for with from by is are was were be been am do does did i you he she it we they me my your his her our their this that these those '
                 'there here what which who how when where why not no yes can could will would should may might have has had just very too also then than as up down out about into over '
                 'please thank thanks hello hi okay ok well really some any one two three get got go going say said let us'.split())

# Content the daily pipeline must never publish. Deliberately blunt: a false positive only costs one regenerated lesson, a false negative reaches every learner.
UNSAFE = re.compile(r"\b(kill(?:ed|ing)?|murder\w*|suicide|self[- ]harm|rape\w*|sex(?:ual|y)?|porn\w*|nude|naked|terroris\w*|bomb(?:ing|s)?|gun(?:s|shot)?|shoot(?:ing)?|"
                    r"cocaine|heroin|meth\b|marijuana|overdose|racis\w*|slur|nazi|genocide|abuse[sd]?|gambl\w*|casino|vote for|election campaign|republican|democrat|trump|biden|"
                    r"god told|jesus|allah|prophet|diagnos\w*|prescri\w+ you|lawsuit|sue you)\b", re.IGNORECASE)
PII = re.compile(r"(https?://|www\.|@\w+\.\w+|\b\d{3}[ -]\d{3,4}[ -]\d{3,4}\b|\b\d{9,}\b)", re.IGNORECASE)


def words(text: str) -> list[str]:
    return normalize_text(text).split()


def content_words(text: str) -> list[str]:
    return [w for w in words(text) if w not in STOP and len(w) > 2]


def fingerprint(sentences: list[str]) -> str:
    """Stable hash of the script itself (case, punctuation and spacing do not matter), so a re-voiced or re-cased copy is the same fingerprint."""
    return hashlib.sha256(' '.join(w for s in sentences for w in words(s)).encode()).hexdigest()


def shingles(sentences: list[str], n: int = SHINGLE) -> set[tuple[str, ...]]:
    seq = [w for s in sentences for w in words(s)]
    return {tuple(seq[i:i + n]) for i in range(max(0, len(seq) - n + 1))}


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


@dataclass(frozen=True)
class Snapshot:
    lesson_id: str
    version_id: str
    title: str
    topic: str
    cefr: str
    listen_for: str
    content_hash: str
    speakers: list
    sentences: list[str]
    exercises: list[dict]            # {kind, prompt, options, answer} - answer is the private key/rubric, used only for these gates
    audio: list[dict]

    def text(self) -> str:
        return ' '.join(self.sentences)

    def exercise_texts(self) -> list[str]:
        out = []
        for e in self.exercises:
            out += [e['prompt'], *[o.get('text', '') for o in e['options']]]
            answer = e.get('answer') or {}
            out += [str(answer.get('explanation') or ''), *[p.get('text', '') for p in answer.get('points', []) or []], *[str(a) for a in answer.get('accepted', []) or []]]
        return [x for x in out if x]


@dataclass(frozen=True)
class Doc:
    lesson_id: str
    title: str
    topic: str
    cefr: str
    sentences: list[str]
    audio_shas: frozenset
    content_hash: str

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.sentences)


@dataclass
class Result:
    ok: bool = True
    problems: list[str] = field(default_factory=list)

    def fail(self, message: str) -> None:
        self.ok = False
        self.problems.append(message)


# ---- reading the database ------------------------------------------------------------------------------------------------------------

def load_snapshot(db, lesson_id: str) -> Snapshot:
    version = db.execute(select(t.versions).where(and_(t.versions.c.lesson_id == lesson_id, t.versions.c.status == 'published'))
                         .order_by(t.versions.c.version_no.desc()).limit(1)).mappings().first()
    if version is None:
        raise LookupError('no published version')
    rows = db.execute(select(t.exercises).where(t.exercises.c.version_id == version['id']).order_by(t.exercises.c.position)).mappings().all()
    ids = [r['id'] for r in rows]
    answers = {k.exercise_id: {'correct_option_id': k.correct_option_id, 'explanation': k.explanation} for k in db.execute(select(t.keys).where(t.keys.c.exercise_id.in_(ids)))} if ids else {}
    if ids:
        answers.update({r.exercise_id: dict(r.data) for r in db.execute(select(t.rubrics).where(t.rubrics.c.exercise_id.in_(ids)))})
    audio = [dict(a) for a in db.execute(select(t.audio.c.accent, t.audio.c.voice, t.audio.c.object_key, t.audio.c.sha256, t.audio.c.size_bytes, t.audio.c.duration_ms,
                                                t.audio.c.alignment).where(t.audio.c.version_id == version['id'])).mappings()]
    return Snapshot(lesson_id, version['id'], version['title'], version['topic'], version['cefr'], version['listen_for'], version['content_hash'], version['speakers'] or [],
                    [s.get('text', '') if isinstance(s, dict) else str(s) for s in version['sentences']],
                    [{'kind': r['kind'], 'prompt': r['prompt'], 'options': r['options'] or [], 'answer': answers.get(r['id'])} for r in rows], audio)


def load_corpus(db, exclude_lesson_id: str | None = None) -> list[Doc]:
    """Every SHARED lesson (no owner row) plus every lesson already used by a daily challenge, all versions. Never a learner's private lesson."""
    owned = select(t.lesson_owners.c.lesson_id).where(t.lesson_owners.c.lesson_id == t.versions.c.lesson_id)
    daily = select(t.daily_challenges.c.lesson_id).where(t.daily_challenges.c.lesson_id.is_not(None))
    rows = db.execute(select(t.versions.c.id, t.versions.c.lesson_id, t.versions.c.title, t.versions.c.topic, t.versions.c.cefr, t.versions.c.sentences, t.versions.c.content_hash)
                      .where(and_(t.versions.c.status == 'published', (~owned.exists()) | t.versions.c.lesson_id.in_(daily)))).all()
    shas: dict[str, set] = {}
    for version_id, sha in db.execute(select(t.audio.c.version_id, t.audio.c.sha256)):
        shas.setdefault(version_id, set()).add(sha)
    return [Doc(r.lesson_id, r.title, r.topic, r.cefr, [s.get('text', '') if isinstance(s, dict) else str(s) for s in r.sentences], frozenset(shas.get(r.id, ())), r.content_hash)
            for r in rows if r.lesson_id != exclude_lesson_id]


# ---- the five deterministic gates ----------------------------------------------------------------------------------------------------

def content_validity(snap: Snapshot, plan: dict, allowed_levels: tuple[str, ...]) -> Result:
    r = Result()
    if snap.cefr not in allowed_levels:
        r.fail(f'level {snap.cefr} is not an allowed daily level')
    if plan.get('cefr') and snap.cefr != plan['cefr']:
        r.fail(f'level {snap.cefr} is not the planned level {plan["cefr"]}')
    if len(snap.sentences) < 6:
        r.fail('the transcript has fewer than 6 sentences')
    if any(not s.strip() for s in snap.sentences):
        r.fail('the transcript has an empty sentence')
    if not snap.title.strip() or not snap.listen_for.strip():
        r.fail('title or listening goal is empty')
    kinds = sorted(e['kind'] for e in snap.exercises)
    if kinds != sorted(REQUIRED_KINDS):
        r.fail(f'exercises are {kinds}, expected one each of {list(REQUIRED_KINDS)}')
    declared = [s.get('accent') for s in snap.speakers if isinstance(s, dict)]
    want = [plan.get('accent'), plan.get('accent2') or plan.get('accent')] if plan.get('kind') != 'monologue' else [plan.get('accent')]
    if plan and declared and sorted(a for a in declared if a) != sorted(a for a in want if a):
        r.fail('the speakers’ accents are not the planned accents')
    if plan.get('kind') == 'monologue' and len(snap.speakers) != 1 or plan.get('kind') in ('conversation', 'mixed_accent_conversation') and len(snap.speakers) != 2:
        r.fail('the number of speakers does not match the planned format')
    return r


def answer_consistency(snap: Snapshot) -> Result:
    r = Result()
    spoken = ' '.join(normalize_text(s) for s in snap.sentences)
    corpus_words = set(content_words(snap.text()))
    for e in snap.exercises:
        a, kind = e.get('answer') or {}, e['kind']
        if not a:
            r.fail(f'{kind} has no answer key or rubric')
            continue
        if kind == 'multiple_choice':
            ids = [o.get('id') for o in e['options']]
            if a.get('correct_option_id') not in ids:
                r.fail('the multiple-choice key is not one of its options')
            if len(ids) != len(set(ids)) or len({normalize_text(o.get('text', '')) for o in e['options']}) != len(ids):
                r.fail('multiple-choice options are not distinct')
            right = next((o for o in e['options'] if o.get('id') == a.get('correct_option_id')), None)
            if right and not (set(content_words(right.get('text', ''))) & corpus_words):
                r.fail('the correct multiple-choice option shares no content word with the script')
        elif kind == 'fill_blank':
            prompt = e['prompt']
            if prompt.count(BLANK) != 1:
                r.fail('the fill-in-the-blank prompt does not have exactly one gap')
                continue
            before, after = (normalize_text(x) for x in prompt.split(BLANK))
            hits = [ans for ans in a.get('accepted', []) if normalize_text(' '.join([before, normalize_text(ans), after])) in spoken]
            if not hits:
                r.fail('no accepted blank answer reproduces a sentence of the script')
        elif kind == 'sequencing':
            item_ids = [o.get('id') for o in e['options']]
            if sorted(a.get('order', [])) != sorted(item_ids) or len(item_ids) < 3:
                r.fail('the sequencing key does not order exactly the listed items')
            elif a.get('order') == item_ids:
                r.fail('the sequencing items are already in the correct order')
            item_words = {o.get('id'): set(content_words(o.get('text', ''))) for o in e['options']}
            if any(not (w & corpus_words) for w in item_words.values()):
                r.fail('a sequencing item shares no content word with the script')
        elif kind == 'spoken':
            points = a.get('points') or []
            if not points:
                r.fail('the spoken question has no key points')
            if any(not (set(content_words(p.get('text', ''))) & corpus_words) for p in points):
                r.fail('a spoken key point is not grounded in the script')
    return r


def audio_readiness(snap: Snapshot, plan: dict, probe=None) -> Result:
    """Metadata-level checks that need no bucket credential, plus an optional `probe(object_key, size)` -> bool for deployments that can reach the bucket."""
    r = Result()
    if not snap.audio:
        r.fail('no audio variant')
        return r
    n, spoken_words = len(snap.sentences), sum(len(words(s)) for s in snap.sentences)
    if plan.get('accent') and plan['accent'] not in {a['accent'] for a in snap.audio}:
        r.fail('no audio variant in the planned accent')
    for a in snap.audio:
        if not re.fullmatch(r'[0-9a-f]{64}', a['sha256'] or '') or a['size_bytes'] <= 0 or a['duration_ms'] <= 0 or not a['object_key']:
            r.fail('an audio variant has an invalid checksum, size, duration or storage key')
            continue
        cues = a['alignment'] or []
        if len(cues) != n:
            r.fail('the audio is not aligned to every sentence')
            continue
        last = 0
        for i, c in enumerate(cues):
            if c['index'] != i or c['start_ms'] < last or c['end_ms'] <= c['start_ms']:
                r.fail('the sentence timings are not ordered')
                break
            last = c['end_ms']
        if last > a['duration_ms'] + 50:
            r.fail('the sentence timings run past the end of the audio')
        wpm = spoken_words / (a['duration_ms'] / 60000)
        if not SPEECH_WPM[0] <= wpm <= SPEECH_WPM[1]:
            r.fail(f'the audio length implies {wpm:.0f} words per minute, which is not natural speech')
        if probe is not None and not probe(a['object_key'], a['size_bytes']):
            r.fail('the audio object is missing or has a different size in storage')
    return r


def safety(snap: Snapshot) -> Result:
    r = Result()
    for label, texts in (('script', [snap.title, snap.topic, snap.listen_for, *snap.sentences]), ('questions', snap.exercise_texts())):
        for text in texts:
            hit = UNSAFE.search(text)
            if hit:
                r.fail(f'the {label} contains a word that is not allowed in daily lessons ("{hit.group(0).lower()}")')
                break
            if PII.search(text):
                r.fail(f'the {label} contains a link, email address or number that looks like personal data')
                break
    return r


class Similarity:
    """Content-word TF-IDF cosine over the corpus plus the candidate. Small corpus (tens to hundreds of lessons), so this is computed on demand."""
    def __init__(self, corpus: list[Doc], candidate_text: str):
        self.docs = corpus
        texts = [(c.lesson_id, content_words(' '.join(c.sentences + [c.title]))) for c in corpus]
        self.n = len(texts) + 1
        df = Counter(w for _, ws in texts for w in set(ws))
        cand = content_words(candidate_text)
        for w in set(cand):
            df[w] += 1
        self.idf = {w: math.log((1 + self.n) / (1 + d)) + 1 for w, d in df.items()}
        self.vec = {lid: self._vector(ws) for lid, ws in texts}
        self.cand = self._vector(cand)

    def _vector(self, ws: list[str]) -> dict[str, float]:
        counts = Counter(ws)
        v = {w: (1 + math.log(c)) * self.idf.get(w, 1.0) for w, c in counts.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {w: x / norm for w, x in v.items()}

    def ranked(self, k: int = 3) -> list[tuple[str, float]]:
        scores = [(lid, sum(x * v.get(w, 0.0) for w, x in self.cand.items())) for lid, v in self.vec.items()]
        return sorted(scores, key=lambda s: -s[1])[:k]


def duplicate_prevention(snap: Snapshot, corpus: list[Doc], used_fingerprints: set[str], used_lessons: set[str], plan: dict) -> tuple[Result, list[tuple[str, float]]]:
    """Exact (lesson id, fingerprint, audio checksum, content hash), near (shared 4-word runs, title) and content-word similarity. Returns (result, nearest neighbours)."""
    r = Result()
    fp, mine = fingerprint(snap.sentences), shingles(snap.sentences)
    if snap.lesson_id in used_lessons:
        r.fail('this lesson was already used as a challenge')
    if fp in used_fingerprints:
        r.fail('this exact script was already used as a challenge')
    my_audio = {a['sha256'] for a in snap.audio}
    title = set(content_words(snap.title)) or set(words(snap.title))
    for d in corpus:
        if d.lesson_id == snap.lesson_id:
            continue
        if d.fingerprint == fp or d.content_hash == snap.content_hash:
            r.fail(f'the script is identical to an existing lesson ("{d.title}")')
        if my_audio & d.audio_shas:
            r.fail(f'the audio is identical to an existing lesson ("{d.title}")')
        overlap = jaccard(mine, shingles(d.sentences))
        if overlap >= NEAR_DUPLICATE_SHINGLES:
            r.fail(f'{overlap:.0%} of the script’s phrases repeat an existing lesson ("{d.title}")')
        dt = set(content_words(d.title)) or set(words(d.title))
        if normalize_text(d.title) == normalize_text(snap.title) or (title and jaccard(title, dt) >= TITLE_SIMILAR):
            r.fail(f'the title is the same as an existing lesson ("{d.title}")')
    sim = Similarity(corpus, ' '.join([snap.title, snap.topic, *snap.sentences])) if corpus else None
    nearest = sim.ranked(3) if sim else []
    if nearest and nearest[0][1] >= COSINE_REJECT:
        title_of = {d.lesson_id: d.title for d in corpus}
        r.fail(f'the content is {nearest[0][1]:.0%} similar to an existing lesson ("{title_of[nearest[0][0]]}")')
    return r, nearest


def needs_semantic_review(nearest: list[tuple[str, float]]) -> bool:
    return True                  # every candidate is reviewed once: safety and coherence in context cannot be decided by rules; the neighbours make the duplicate question concrete


def semantic_review(verdict: dict, neighbour_ids: set[str]) -> Result:
    r = Result()
    if not verdict.get('safe'):
        r.fail('the semantic review flagged unsafe content')
    if not verdict.get('coherent'):
        r.fail('the semantic review found the lesson incoherent or its questions unanswerable')
    if not verdict.get('level_ok'):
        r.fail('the semantic review found the language wrong for the level')
    same = verdict.get('same_scenario_as')
    if same:
        r.fail('the semantic review found the same scenario as an existing lesson' + (' (one of the nearest)' if same in neighbour_ids else ''))
    for issue in verdict.get('issues', []):
        if not r.ok:
            r.problems.append(f'review note: {issue}')
    return r


def neighbour_payload(corpus: list[Doc], nearest: list[tuple[str, float]], limit_chars: int = 700) -> list[dict]:
    by_id = {d.lesson_id: d for d in corpus}
    return [{'id': lid, 'title': by_id[lid].title, 'topic': by_id[lid].topic, 'similarity': round(score, 2), 'excerpt': ' '.join(by_id[lid].sentences)[:limit_chars]}
            for lid, score in nearest if lid in by_id]


def candidate_payload(snap: Snapshot, plan: dict, limit_chars: int = 2600) -> dict:
    questions = [{'kind': e['kind'], 'prompt': e['prompt'], 'options': [o.get('text') for o in e['options']]} for e in snap.exercises]
    return {'title': snap.title, 'topic': snap.topic, 'cefr': snap.cefr, 'format': plan.get('kind'), 'script': ' '.join(snap.sentences)[:limit_chars], 'questions': questions}
