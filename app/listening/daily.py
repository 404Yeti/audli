"""The Daily Challenge scheduler: `python -m app.listening.daily run` (a third sidecar process beside the API, generator and worker; own database role).

One lesson per local calendar day (default Asia/Ho_Chi_Minh), generated the evening before, validated by automated gates, published at the day boundary.

    planned --> generating --> built --> scheduled --> active          (failed is terminal until an operator runs `retry`)
       ^            |            |
       +------------+------------+   a failed or REJECTED attempt returns to `planned` while attempts remain (a new topic each time), else `failed`

Safety properties (each is tested):
* Idempotent under concurrency, restarts and partial failures. The day row is claimed with a lease, every transition is a compare-and-set on (state, lease owner),
  the generation request id is deterministic per (date, attempt), `lesson_id` and `fingerprint` are UNIQUE across all days, and publishing is ONE transaction
  that locks the day row and the library entry. A second scheduler can only wait, and a crash at any line resumes at the same state.
* Never a replay. A rejected candidate is recorded and stays a private draft forever. If every attempt fails the day is `failed`, learners see "unavailable", and the
  operator is alerted. Yesterday's lesson is never reused; it moves to Discover by the clock.
* Autonomous publication is the SAME curator workflow (library.register / approve / publish), run by one service account that must be on the curator list, under its
  own least-privilege role, with the reviewer recorded as `auto:daily-gates-v1` and bound to a digest of the exact script, exercises and audio. It is off unless
  AUDLI_LISTENING_DAILY_PUBLISH=true, and a DB pause row (`pause` command) stops generation AND publication at once. The global AI kill switch stops generation.
* Spend is capped per challenge date for generation (reservations) and for semantic review/topic proposals, on top of the global generation and AI ceilings.
"""
import json
import logging
import re
import socket
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from uuid import uuid4

from sqlalchemy import and_, insert, select, update
from sqlalchemy.exc import IntegrityError

from app.listening import daily_gates as gates, daily_planner as planner, daily_read as read, generation_jobs as genjobs, library, schema as t
from app.listening.ai_budget import AiGuard, text_micro
from app.listening.daily_taxonomy import BY_KEY, CATEGORIES, LIBRARY_CATEGORY, LEVELS, Subtopic, TAXONOMY
from app.listening.generation import GenerationRefused, Spec, clamp_spec, format_availability, spec_problem
from app.listening.providers import ProviderPermanent, ProviderTemporary
from app.listening.store import Conflict, Invalid, NotFound
from app.storage.repository import utcnow

log = logging.getLogger('audli.listening.daily')
REVIEWER = 'auto:daily-gates-v1'
LEASE_SECONDS = 300
EVENT_CAP = 60
STALL_MINUTES = 90               # a generation request that has not finished by now is abandoned (its lesson, if it ever appears, is never used)
REVIEW_FAILURES_BEFORE_ALERT = 3
LIVE = genjobs.LIVE_PHASES


class LeaseLost(Exception):
    pass


@dataclass
class Daily:
    engine: object
    settings: object
    ledger: object
    judge: object
    proposer: object
    clock: object = utcnow
    audio_probe: object = None
    notify: object = None                      # callable(message) for the operator; the log line is always written
    owner: str = field(default_factory=lambda: f'{socket.gethostname()}-{uuid4().hex[:8]}'[:60])
    guard: object = None

    def __post_init__(self):
        self.guard = self.guard or AiGuard(self.engine, self.settings)

    @property
    def service_id(self) -> str:
        return self.settings.listening_daily_service_id.strip().lower()

    @property
    def curators(self) -> frozenset:
        """The ONLY accounts the library workflow will act for: the service account, and only while it is still on the operator's curator list."""
        return frozenset({self.service_id}) & self.settings.listening_curator_set

    @property
    def levels(self) -> tuple[str, ...]:
        return tuple(l for l in (p.strip() for p in self.settings.listening_daily_levels.split(',')) if l in LEVELS) or LEVELS


# ---- small helpers ---------------------------------------------------------------------------------------------------------------------

def _event(events: list, name: str, detail: str = '', now: datetime | None = None) -> list:
    return [*events, {'at': (now or utcnow()).isoformat(timespec='seconds'), 'event': name, 'detail': detail[:200]}][-EVENT_CAP:]


def allowed_attempts(row, settings) -> int:
    """The configured attempts per date plus whatever an operator granted with `retry` (recorded in the audit trail)."""
    granted = sum(int(e['detail']) for e in row['events'] if e['event'] == 'operator_retry' and str(e['detail']).isdigit())
    return settings.listening_daily_max_attempts + granted


def _row(db, d: str, lock: bool = False):
    q = select(t.daily_challenges).where(t.daily_challenges.c.challenge_date == d)
    return db.execute(q.with_for_update() if lock and db.dialect.name == 'postgresql' else q).mappings().first()


def ensure_row(db, d: str, now: datetime) -> None:
    if _row(db, d) is None:
        try:
            with db.begin_nested():
                db.execute(insert(t.daily_challenges).values(challenge_date=d, state='planned', attempts=0, rejected=[], events=_event([], 'created', now=now), review_micro=0,
                                                              created_at=now, updated_at=now))
        except IntegrityError:
            pass


def acquire(engine, d: str, owner: str, now: datetime) -> bool:
    with engine.begin() as db:
        ensure_row(db, d, now)
        return db.execute(update(t.daily_challenges).where(and_(t.daily_challenges.c.challenge_date == d,
              (t.daily_challenges.c.lease_until.is_(None)) | (t.daily_challenges.c.lease_until < now) | (t.daily_challenges.c.lease_owner == owner)))
              .values(lease_owner=owner, lease_until=now + timedelta(seconds=LEASE_SECONDS), updated_at=now)).rowcount == 1


def release(engine, d: str, owner: str) -> None:
    with engine.begin() as db:
        db.execute(update(t.daily_challenges).where(and_(t.daily_challenges.c.challenge_date == d, t.daily_challenges.c.lease_owner == owner))
                   .values(lease_owner=None, lease_until=None))


def _cas(db, daily: Daily, d: str, expected: str, now: datetime, **values) -> None:
    """Move a day forward only if it is still in `expected` and this process still holds the lease. Anything else means another process won: stop."""
    if db.execute(update(t.daily_challenges).where(and_(t.daily_challenges.c.challenge_date == d, t.daily_challenges.c.state == expected,
            t.daily_challenges.c.lease_owner == daily.owner, t.daily_challenges.c.lease_until >= now)).values(updated_at=now, **values)).rowcount != 1:
        raise LeaseLost(d)


def alert_once(daily: Daily, d: str, code: str, message: str) -> None:
    """Log at ERROR and, when configured, post to the webhook - once per (date, code), so a stuck day does not page every minute."""
    now = daily.clock()
    with daily.engine.begin() as db:
        row = _row(db, d)
        if row is None or any(e['event'] == f'alert:{code}' for e in row['events']):
            return
        db.execute(update(t.daily_challenges).where(t.daily_challenges.c.challenge_date == d).values(events=_event(row['events'], f'alert:{code}', message, now)))
    log.error('DAILY CHALLENGE ALERT %s [%s]: %s', d, code, message)
    hook = daily.settings.listening_daily_alert_webhook
    if daily.notify:
        daily.notify(f'Audli daily challenge {d}: {message}')
    elif hook:
        try:
            req = urllib.request.Request(hook.get_secret_value(), data=json.dumps({'text': f'Audli daily challenge {d}: {message}'}).encode(),
                                         headers={'Content-Type': 'application/json'}, method='POST')
            urllib.request.urlopen(req, timeout=5).read()
        except Exception:
            log.warning('the alert webhook could not be reached')


def generation_cost(db, daily: Daily, d: str) -> int:
    from app.listening.curate import cost_micro
    r = t.generation_requests.c
    rows = db.execute(select(r.reserved_micro, r.phase, r.actual_credits).where(and_(r.kind == 'generate', r.client_request_id.like(f'daily:{d}:%')))).all()
    return sum(cost_micro(x, daily.settings) for x in rows)


def history(db) -> list[dict]:
    """Accepted challenges (validated or published), newest first, in the shape the planner wants."""
    rows = db.execute(select(t.daily_challenges.c.challenge_date, t.daily_challenges.c.plan).where(and_(t.daily_challenges.c.lesson_id.is_not(None),
            t.daily_challenges.c.state.in_(('scheduled', 'active')))).order_by(t.daily_challenges.c.challenge_date.desc())).all()
    return [dict(r.plan) for r in rows if r.plan]


def used_topic_keys(db) -> set[str]:
    """Every topic ever accepted or ever rejected: a rejected topic is not offered again."""
    keys = set()
    for state, topic_key, rejected in db.execute(select(t.daily_challenges.c.state, t.daily_challenges.c.topic_key, t.daily_challenges.c.rejected)):
        if topic_key:
            keys.add(topic_key)
        keys |= {r.get('topic_key') for r in rejected or [] if r.get('topic_key')}
    return keys


# ---- topic proposals (AI fallback) -----------------------------------------------------------------------------------------------------

def _slug(value: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', value.lower()).strip('-')[:34]


def validate_proposals(raw: list[dict], corpus_texts: list[str], taken: set[str]) -> list[Subtopic]:
    """Keep only proposals that are well-formed, safe, in a known category, and not a rewording of any existing title, topic or scenario."""
    existing = [set(gates.content_words(x)) for x in corpus_texts]
    out = []
    for item in raw:
        try:
            category, topic, scenario = item['category'], item['topic'].strip(), item['scenario'].strip()
            key = 'ai-' + _slug(item['key'] or topic)
            lo, hi = item.get('lo', 'A2'), item.get('hi', 'B1')
            if category not in CATEGORIES or key in taken or key in BY_KEY or len(key) < 6 or not 3 <= len(topic) <= 120 or not 10 <= len(scenario) <= 160:
                continue
            if lo not in LEVELS or hi not in LEVELS or LEVELS.index(lo) > LEVELS.index(hi):
                continue
            if gates.UNSAFE.search(topic + ' ' + scenario) or gates.PII.search(topic + ' ' + scenario):
                continue
            mine = set(gates.content_words(topic + ' ' + scenario))
            if mine and any(gates.jaccard(mine, other) >= 0.5 for other in existing):
                continue
            out.append(Subtopic(key, category, topic, scenario, lo, hi))
        except (KeyError, AttributeError, TypeError):
            continue
    return out


def _review_budget_left(row, daily: Daily) -> int:
    return round(daily.settings.listening_daily_review_max_usd * 1_000_000) - row['review_micro']


def _spend_review(daily: Daily, d: str, estimate: int, actual: int | None) -> None:
    with daily.engine.begin() as db:
        db.execute(update(t.daily_challenges).where(t.daily_challenges.c.challenge_date == d)
                   .values(review_micro=t.daily_challenges.c.review_micro + (actual if actual is not None else estimate)))


def propose_topics(daily: Daily, d: str, row, history_rows: list[dict]) -> list[Subtopic]:
    from app.listening.models import ModelPlan
    model = ModelPlan.from_settings(daily.settings).daily
    context_titles = []
    with daily.engine.connect() as db:
        corpus = gates.load_corpus(db)
        taken = used_topic_keys(db)
    context_titles = [{'title': c.title, 'topic': c.topic} for c in corpus][-60:]
    dist = planner.breakdown(history_rows)['category']
    payload = {'categories': list(CATEGORIES), 'over_represented': sorted(planner.OVERUSED), 'recent_category_counts': dist,
               'existing': context_titles, 'known_scenarios': [s.scenario for s in TAXONOMY][:60], 'how_many': 3}
    estimate = text_micro(model.model, len(json.dumps(payload)), model.max_output_tokens)
    if estimate > _review_budget_left(row, daily) or daily.guard.halted() or not daily.settings.listening_ai_enabled:
        return []
    try:
        raw = daily.proposer.propose(payload)
    except (ProviderTemporary, ProviderPermanent) as error:
        _spend_review(daily, d, 0, 0)
        log.warning('topic proposal failed: %s', getattr(error, 'code', 'provider'))
        return []
    _spend_review(daily, d, estimate, getattr(daily.proposer, 'last_cost_micro', None))
    texts = [c.title for c in corpus] + [c.topic for c in corpus] + [s.topic + ' ' + s.scenario for s in TAXONOMY]
    return validate_proposals(raw, texts, taken)


# ---- the state machine -----------------------------------------------------------------------------------------------------------------

def _fail_attempt(daily: Daily, d: str, expected: str, reasons: list[str], lesson_id: str | None = None, now: datetime | None = None) -> str:
    """Record a failed or rejected attempt. The lesson (if any) is never used again. Returns the next state."""
    now = now or daily.clock()
    with daily.engine.begin() as db:
        row = _row(db, d, lock=True)
        record = {'attempt': row['attempts'], 'lesson_id': lesson_id or row['lesson_id'], 'topic_key': (row['plan'] or {}).get('topic_key'), 'reasons': [r[:200] for r in reasons[:6]]}
        exhausted = row['attempts'] >= allowed_attempts(row, daily.settings)
        values = dict(rejected=[*row['rejected'], record], plan=None, request_id=None, lesson_id=None, version_id=None, fingerprint=None, gate_report=None,
                      topic_key=None, category=None, events=_event(row['events'], 'attempt_failed', '; '.join(reasons)[:180], now))
        if exhausted:
            values.update(state='failed', failure_code=(reasons[0] if reasons else 'failed')[:40].replace(' ', '_'))
        else:
            values.update(state='planned')
        _cas(db, daily, d, expected, now, **values)
    nxt = 'failed' if exhausted else 'planned'
    if exhausted:
        alert_once(daily, d, f'exhausted{record["attempt"]}', f'all {record['attempt']} attempt(s) failed ({"; ".join(reasons)[:150]}). Learners see "unavailable" until an operator runs `retry {d}`.')
    else:
        alert_once(daily, d, f'attempt{record["attempt"]}', f'attempt {record["attempt"]} was rejected or failed ({"; ".join(reasons)[:150]}); trying again with a different topic.')
    return nxt


def _step_planned(daily: Daily, d: str, row, now: datetime) -> str | None:
    s = daily.settings
    if row['attempts'] >= allowed_attempts(row, s):
        return _fail_attempt(daily, d, 'planned', ['attempts_exhausted'], now=now)
    with daily.engine.connect() as db:
        hist, used = history(db), used_topic_keys(db)
    day = date.fromisoformat(d)
    minutes = min(s.listening_daily_minutes, s.listening_gen_max_minutes)
    remaining = planner.pool_remaining(used)
    if remaining < s.listening_daily_pool_low:
        alert_once(daily, d, 'pool_low', f'only {remaining} reviewed topics are unused; the AI will propose new ones. Add curated topics to app/listening/daily_taxonomy.py.')
    use_ai = remaining == 0 or (remaining < s.listening_daily_pool_low and day.toordinal() % 2 == 0)
    extra = tuple(propose_topics(daily, d, row, hist)) if use_ai else ()
    availability = format_availability(s)
    plan = planner.choose(day, hist, availability, rejected_keys=used, minutes=minutes, levels=daily.levels, extra=extra, force_extra=bool(extra))
    if plan is None and extra:
        plan = planner.choose(day, hist, availability, rejected_keys=used, minutes=minutes, levels=daily.levels)
    if plan is None:
        return _fail_attempt(daily, d, 'planned', ['no_topic_available'], now=now)
    spec = Spec.model_validate(plan.spec())
    problem = spec_problem(spec, s)
    clamped, notes = clamp_spec(spec.model_dump(), s.listening_gen_max_minutes, s)
    if problem or notes or any(clamped.get(k) != v for k, v in spec.model_dump().items()):
        return _fail_attempt(daily, d, 'planned', [f'unsupported_combination: {problem or "; ".join(notes) or "spec adjusted"}'], now=now)
    with daily.engine.begin() as db:
        _cas(db, daily, d, 'planned', now, state='generating', attempts=row['attempts'] + 1, plan=plan.as_json(), topic_key=plan.topic_key, category=plan.category,
             events=_event(row['events'], 'planned', f'attempt {row["attempts"] + 1}: {plan.topic_key} {plan.cefr} {plan.kind} {plan.accent}', now))
    return 'generating'


def _step_generating(daily: Daily, d: str, row, now: datetime) -> str | None:
    s = daily.settings
    plan = planner.Plan.from_json(row['plan'])
    spec = Spec.model_validate(plan.spec())
    crid = f'daily:{d}:a{row["attempts"]}'
    with daily.engine.begin() as db:
        request_id, created = genjobs.create_generation(db, daily.service_id, crid, spec, [], None)
        if row['request_id'] != request_id:
            _cas(db, daily, d, 'generating', now, request_id=request_id)
    with daily.engine.connect() as db:
        req = db.execute(select(t.generation_requests).where(t.generation_requests.c.id == request_id)).mappings().one()
        spent = generation_cost(db, daily, d)
    if req['phase'] == 'unavailable':                      # recorded but never reserved (first time, or refused earlier): reserve and queue now
        estimate = genjobs.reservation_for(s, spec)[1]
        if spent + estimate > round(s.listening_daily_max_usd * 1_000_000):
            alert_once(daily, d, 'daily_cap', f'the per-challenge generation cap of ${s.listening_daily_max_usd:.2f} would be passed (${spent / 1e6:.3f} counted + ${estimate / 1e6:.3f} for the next attempt); nothing was spent.')
            return None
        if daily.guard.halted() or not s.listening_ai_enabled:
            return None
        try:
            genjobs.finalize_reservation(daily.engine, daily.ledger, s, daily.service_id, request_id, spec)
        except GenerationRefused as refused:
            hint = ' Set AUDLI_LISTENING_GEN_CURATOR_DAILY (up to 5) so the service account can make one lesson per attempt.' if refused.reason == 'daily_limit' else ''
            alert_once(daily, d, f'refused_{refused.reason}', f'generation was refused ({refused.reason}); it will be retried on the next pass.{hint}')
            return None
        _log(daily, d, now, 'queued', f'attempt {row["attempts"]}')
        return None
    if req['phase'] == 'ready' and req['lesson_id']:
        with daily.engine.begin() as db:
            _cas(db, daily, d, 'generating', now, state='built', lesson_id=req['lesson_id'], events=_event(row['events'], 'built', 'generation ready', now))
        return 'built'
    if req['phase'] in ('failed', 'rejected'):
        return _fail_attempt(daily, d, 'generating', [f'generation_{req["phase"]}:{req["failure_code"] or "unknown"}'], now=now)
    age = (now - read_aware(req['updated_at'])).total_seconds() / 60
    if age > STALL_MINUTES and req['phase'] in LIVE + ('queued',):
        return _fail_attempt(daily, d, 'generating', ['generation_stalled'], now=now)
    return None


def read_aware(value):
    from app.storage.repository import aware
    return aware(value)


def _log(daily: Daily, d: str, now: datetime, name: str, detail: str = '') -> None:
    with daily.engine.begin() as db:
        row = _row(db, d)
        db.execute(update(t.daily_challenges).where(t.daily_challenges.c.challenge_date == d).values(events=_event(row['events'], name, detail, now)))


def _step_built(daily: Daily, d: str, row, now: datetime) -> str | None:
    s, lesson_id = daily.settings, row['lesson_id']
    plan = planner.Plan.from_json(row['plan'])
    curators = daily.curators
    # 1. Same authorization as a human curator: the lesson must be owned and generated by the listed service account.
    try:
        with daily.engine.begin() as db:
            if db.execute(select(t.library_entries.c.lesson_id).where(t.library_entries.c.lesson_id == lesson_id)).first() is None:
                library.register(db, lesson_id, LIBRARY_CATEGORY[plan.category], curators)
    except (Conflict, NotFound, Invalid) as problem:
        alert_once(daily, d, 'not_authorized', f'the service account cannot register this lesson ({problem}). Is AUDLI_LISTENING_DAILY_SERVICE_ID on AUDLI_LISTENING_CURATOR_IDS?')
        return None
    # 2. Deterministic gates.
    with daily.engine.connect() as db:
        snap = gates.load_snapshot(db, lesson_id)
        corpus = gates.load_corpus(db, exclude_lesson_id=lesson_id)
        used_fp = {r[0] for r in db.execute(select(t.daily_challenges.c.fingerprint).where(t.daily_challenges.c.fingerprint.is_not(None)))}
        used_lessons = {r[0] for r in db.execute(select(t.daily_challenges.c.lesson_id).where(and_(t.daily_challenges.c.lesson_id.is_not(None),
                                                                                                   t.daily_challenges.c.challenge_date != d)))}
        for rejected in db.execute(select(t.daily_challenges.c.rejected)).scalars():          # a lesson that was ever rejected can never become a challenge
            used_lessons |= {r.get('lesson_id') for r in rejected or [] if r.get('lesson_id')}
    pj = plan.as_json()
    report = {'content': gates.content_validity(snap, pj, daily.levels), 'answers': gates.answer_consistency(snap), 'audio': gates.audio_readiness(snap, pj, daily.audio_probe),
              'safety': gates.safety(snap)}
    dup, nearest = gates.duplicate_prevention(snap, corpus, used_fp, used_lessons, pj)
    report['duplicates'] = dup
    failed = [f'{name}: {p}' for name, r in report.items() for p in r.problems if not r.ok]
    summary = {name: {'ok': r.ok, 'problems': r.problems} for name, r in report.items()}
    summary['nearest'] = [{'lesson_id': lid, 'similarity': round(score, 2)} for lid, score in nearest]
    if failed:
        return _fail_attempt(daily, d, 'built', failed, lesson_id=lesson_id, now=now)
    # 3. The one bounded semantic review. Fail closed: if it cannot run, nothing is published; the candidate waits.
    review = _semantic(daily, d, row, snap, pj, corpus, nearest, now)
    if review is None:
        return None
    summary['semantic'] = {'ok': review.ok, 'problems': review.problems}
    if not review.ok:
        return _fail_attempt(daily, d, 'built', [f'semantic: {p}' for p in review.problems] or ['semantic: rejected'], lesson_id=lesson_id, now=now)
    # 4. Approve exactly like a human reviewer (bound to a digest of script, exercises and audio) and schedule.
    note = json.dumps({'gates': {k: v['ok'] for k, v in summary.items() if isinstance(v, dict) and 'ok' in v}}, separators=(',', ':'))[:480]
    fp = gates.fingerprint(snap.sentences)
    try:
        with daily.engine.begin() as db:
            library.approve(db, lesson_id, REVIEWER, note, curators)
            _cas(db, daily, d, 'built', now, state='scheduled', version_id=snap.version_id, fingerprint=fp, gate_report=summary,
                 events=_event(row['events'], 'scheduled', 'all gates passed', now))
    except IntegrityError:
        return _fail_attempt(daily, d, 'built', ['duplicate: this script or lesson already belongs to another challenge day'], lesson_id=lesson_id, now=now)
    except (Conflict, Invalid) as problem:
        return _fail_attempt(daily, d, 'built', [f'approval refused: {problem}'], lesson_id=lesson_id, now=now)
    return 'scheduled'


def _semantic(daily: Daily, d: str, row, snap, plan: dict, corpus, nearest, now: datetime):
    from app.listening.models import ModelPlan
    model = ModelPlan.from_settings(daily.settings).daily
    neighbours = gates.neighbour_payload(corpus, nearest)
    payload = {'candidate': gates.candidate_payload(snap, plan), 'neighbours': neighbours}
    estimate = text_micro(model.model, len(json.dumps(payload)), model.max_output_tokens)
    failures = sum(1 for e in row['events'] if e['event'] == 'review_unavailable')
    if estimate > _review_budget_left(row, daily):
        alert_once(daily, d, 'review_cap', 'the per-challenge review budget is used up; the candidate was not published.')
        return None
    if daily.guard.halted() or not daily.settings.listening_ai_enabled:
        return None
    try:
        verdict = daily.judge.review(payload['candidate'], neighbours)
    except ProviderPermanent:
        _spend_review(daily, d, 0, 0)
        r = gates.Result()
        r.fail('the reviewer refused to assess it')
        return r
    except ProviderTemporary:
        _log(daily, d, now, 'review_unavailable', 'provider')
        if failures + 1 >= REVIEW_FAILURES_BEFORE_ALERT:
            alert_once(daily, d, 'review_down', 'the semantic review has failed repeatedly; the validated lesson is waiting and nothing is published until it can run.')
        return None
    _spend_review(daily, d, estimate, getattr(daily.judge, 'last_cost_micro', None))
    return gates.semantic_review(verdict, {n['id'] for n in neighbours})


def _step_scheduled(daily: Daily, d: str, row, now: datetime) -> str | None:
    """Publish at the day boundary: the challenge date is today (or already past) and publishing is switched on and not paused. One transaction."""
    s = daily.settings
    if not s.listening_daily_publish or date.fromisoformat(d) > read.local_date(s, now):
        return None
    try:
        with daily.engine.begin() as db:
            current = _row(db, d, lock=True)
            if current['state'] != 'scheduled' or read.paused(db):
                return None
            library.publish(db, current['lesson_id'], daily.curators)
            _cas(db, daily, d, 'scheduled', now, state='active', published_at=now, events=_event(current['events'], 'published', 'live', now))
    except (Conflict, Invalid, NotFound) as problem:
        return _fail_attempt(daily, d, 'scheduled', [f'publish refused: {problem}'], now=now)
    log.info('daily challenge %s is live: lesson %s', d, row['lesson_id'])
    return 'active'


STEPS = {'planned': _step_planned, 'generating': _step_generating, 'built': _step_built, 'scheduled': _step_scheduled}


def advance(daily: Daily, d: str, now: datetime | None = None) -> str:
    """Run one challenge date as far as it can go right now; returns the state it stopped in. Safe to call concurrently and repeatedly."""
    now = now or daily.clock()
    if not acquire(daily.engine, d, daily.owner, now):
        return 'busy'
    state = 'planned'
    if not daily.curators:
        release(daily.engine, d, daily.owner)
        alert_once(daily, d, 'not_authorized', 'the service account is not on AUDLI_LISTENING_CURATOR_IDS, so nothing is generated, approved or published. No attempt was used.')
        with daily.engine.connect() as db:
            return _row(db, d)['state']
    try:
        for _ in range(8):                                  # planned -> generating -> built -> scheduled -> active is at most 4 moves
            with daily.engine.connect() as db:
                row = _row(db, d)
                paused = read.paused(db)
            state = row['state']
            if state not in STEPS or paused:
                break
            nxt = STEPS[state](daily, d, row, daily.clock())
            if nxt is None or nxt == state:
                break
    except LeaseLost:
        log.warning('lost the lease on %s; another scheduler took over', d)
    finally:
        release(daily.engine, d, daily.owner)
    with daily.engine.connect() as db:
        return _row(db, d)['state']


def target_dates(settings, now: datetime, open_dates: list[str]) -> list[str]:
    """Today (generated late if it does not exist yet), tomorrow once the generation hour has passed, and any unfinished day still open."""
    today = read.local_date(settings, now)
    local_hour = now.astimezone(read.zone(settings)).hour
    wanted = {today.isoformat()}
    if local_hour >= settings.listening_daily_generate_hour:
        wanted.add((today + timedelta(days=1)).isoformat())
    return sorted(wanted | set(open_dates))


def tick(daily: Daily, now: datetime | None = None) -> dict:
    now = now or daily.clock()
    s = daily.settings
    out = {'states': {}}
    with daily.engine.connect() as db:
        if read.paused(db):
            return {'paused': True, 'states': {}}
        open_dates = [r[0] for r in db.execute(select(t.daily_challenges.c.challenge_date).where(t.daily_challenges.c.state.in_(('planned', 'generating', 'built', 'scheduled'))))]
    for d in target_dates(s, now, open_dates):
        out['states'][d] = advance(daily, d, now)
    today = read.local_date(s, now).isoformat()
    if out['states'].get(today) == 'failed':
        pass                                                # already alerted at the moment it failed
    return out


# ---- operator commands -----------------------------------------------------------------------------------------------------------------

def set_paused(engine, paused: bool, note: str = '') -> None:
    now = utcnow()
    with engine.begin() as db:
        if db.execute(update(t.daily_control).where(t.daily_control.c.scope == 'control').values(paused=paused, note=note[:200] or None, updated_at=now)).rowcount != 1:
            db.execute(insert(t.daily_control).values(scope='control', paused=paused, note=note[:200] or None, updated_at=now))


def retry_day(engine, d: str, extra_attempts: int = 1) -> bool:
    """Operator override for a failed day: grant more attempts. Spend is still capped per date and nothing already rejected is reused."""
    with engine.begin() as db:
        row = _row(db, d, lock=True)
        if row is None or row['state'] != 'failed':
            return False
        db.execute(update(t.daily_challenges).where(t.daily_challenges.c.challenge_date == d).values(
            state='planned', failure_code=None, events=_event(row['events'], 'operator_retry', str(extra_attempts)), updated_at=utcnow()))
    return True


def summary(engine, settings, now: datetime | None = None) -> dict:
    """Numbers and states only (no lesson text, no learner data): what an operator needs to see generation, validation, scheduling, publication and failures."""
    now = now or utcnow()
    today = read.local_date(settings, now)
    with engine.connect() as db:
        if not read.table_readable(db):
            return {'enabled': settings.listening_daily_enabled, 'migrated': False}
        rows = db.execute(select(t.daily_challenges).order_by(t.daily_challenges.c.challenge_date.desc()).limit(10)).mappings().all()
        hist = history(db)
        used = used_topic_keys(db)
        paused = read.paused(db)
    days = [{'date': r['challenge_date'], 'state': r['state'], 'attempts': r['attempts'], 'topic': r['topic_key'], 'category': r['category'], 'failure': r['failure_code'],
             'rejected': len(r['rejected']), 'review_micro': r['review_micro'], 'published_at': r['published_at'].isoformat() if r['published_at'] else None,
             'last_events': [f"{e['at']} {e['event']}" for e in r['events'][-3:]]} for r in rows]
    todays = next((r for r in rows if r['challenge_date'] == today.isoformat()), None)
    return {'enabled': settings.listening_daily_enabled, 'migrated': True, 'publishing': settings.listening_daily_publish and not paused, 'paused': paused, 'today': today.isoformat(),
            'today_state': todays['state'] if todays else 'none', 'pool_unused': planner.pool_remaining(used), 'pool_low_at': settings.listening_daily_pool_low,
            'diversity': planner.breakdown(hist), 'days': days}


def alerts(report: dict, now_local_hour: int | None = None) -> list[str]:
    found = []
    if not report.get('enabled') or not report.get('migrated'):
        return found
    if report.get('paused'):
        found.append('the daily challenge is paused by an operator')
    if report.get('today_state') in ('failed',):
        found.append("today's daily challenge failed; learners see 'unavailable'")
    if report.get('today_state') in ('none', 'planned', 'generating', 'built', 'scheduled') and report.get('publishing') and now_local_hour is not None and now_local_hour >= 1:
        found.append(f"today's daily challenge is not live (state {report['today_state']})")
    if report.get('pool_unused', 99) < report.get('pool_low_at', 0):
        found.append(f"only {report['pool_unused']} reviewed daily topics are unused")
    return found


# ---- process wiring --------------------------------------------------------------------------------------------------------------------

def build(settings, engine) -> Daily:
    from app.listening.generation import Ledger
    from app.listening.models import ModelPlan
    from app.listening.providers import FakeDailyJudge, FakeTopicProposer, OpenAIDailyJudge, OpenAITopicProposer, make_client
    if not settings.listening_daily_service_id.strip():
        raise RuntimeError('AUDLI_LISTENING_DAILY_SERVICE_ID is required (the curator-listed account that owns daily drafts).')
    if settings.listening_daily_service_id.strip().lower() not in settings.listening_curator_set:
        raise RuntimeError('AUDLI_LISTENING_DAILY_SERVICE_ID must also be on AUDLI_LISTENING_CURATOR_IDS: autonomous publication uses the existing curator authorization.')
    if settings.listening_provider == 'fake':
        if settings.environment == 'production':
            raise RuntimeError('The fake daily providers cannot run in production.')
        judge, proposer = FakeDailyJudge(), FakeTopicProposer()
    else:
        if settings.openai_api_key is None or not settings.openai_api_key.get_secret_value().strip():
            raise RuntimeError('OPENAI_API_KEY is required for the daily semantic review.')
        plan = ModelPlan.from_settings(settings).daily
        client = make_client(settings.openai_api_key.get_secret_value(), settings.listening_provider_timeout_seconds)
        judge, proposer = OpenAIDailyJudge(client, plan.model, plan.timeout, plan.max_output_tokens), OpenAITopicProposer(client, plan.model, plan.timeout, plan.max_output_tokens)
    return Daily(engine=engine, settings=settings, ledger=Ledger(engine, settings), judge=judge, proposer=proposer)


def main(argv: list[str]) -> int:
    import argparse
    from app.config import Settings
    from app.listening.worker import make_engine, operator_settings
    parser = argparse.ArgumentParser(prog='python -m app.listening.daily', description='Daily Challenge scheduler and operator commands.')
    sub = parser.add_subparsers(dest='action', required=True)
    run = sub.add_parser('run'); run.add_argument('--once', action='store_true'); run.add_argument('--poll', type=float, default=60.0)
    for name in ('status', 'resume'):
        sub.add_parser(name)
    pause = sub.add_parser('pause'); pause.add_argument('--note', default='')
    retry = sub.add_parser('retry'); retry.add_argument('date'); retry.add_argument('--extra', type=int, default=1)
    plan = sub.add_parser('plan'); plan.add_argument('--days', type=int, default=7)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
    settings = Settings() if args.action == 'run' else operator_settings()
    engine = make_engine(settings)
    if args.action == 'run':
        if not settings.listening_enabled or not settings.listening_daily_enabled:
            print('The daily challenge is off (AUDLI_LISTENING_ENABLED and AUDLI_LISTENING_DAILY_ENABLED); nothing to run.', file=sys.stderr)
            return 1
        from app.listening.api import prepare
        prepare(engine, daily=True)
        daily = build(settings, engine)
        log.info('daily challenge scheduler started: publish=%s timezone=%s generate_from=%02d:00', settings.listening_daily_publish, settings.listening_daily_timezone,
                 settings.listening_daily_generate_hour)
        while True:
            try:
                tick(daily)
            except Exception:
                log.exception('daily tick failed')
            if args.once:
                return 0
            time.sleep(args.poll)
    if args.action == 'status':
        print(json.dumps(summary(engine, settings), indent=1, default=str))
        return 0
    if args.action in ('pause', 'resume'):
        set_paused(engine, args.action == 'pause', getattr(args, 'note', ''))
        print(f'daily challenge {"paused" if args.action == "pause" else "resumed"}')
        return 0
    if args.action == 'retry':
        print('reset for retry' if retry_day(engine, args.date, args.extra) else 'nothing to retry: that date is not in the failed state')
        return 0
    with engine.connect() as db:                       # plan: read-only preview, no spend, no writes
        hist, used = history(db), used_topic_keys(db)
    day, preview = read.local_date(settings), []
    for n in range(1, args.days + 1):
        p = planner.choose(day + timedelta(days=n), hist, format_availability(settings), used, settings.listening_daily_minutes, tuple(l for l in settings.listening_daily_levels.split(',') if l in LEVELS))
        if p:
            preview.append({'date': (day + timedelta(days=n)).isoformat(), **p.as_json()})
            hist = [p.as_json(), *hist]
            used = used | {p.topic_key}
    print(json.dumps(preview, indent=1))
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
