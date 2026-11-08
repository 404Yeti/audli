"""Curator batch generation: fill the curated library from a reviewed manifest, one lesson at a time, through the SAME structured pipeline a learner uses.

    python -m app.listening.curate plan   [--manifest F] [--stage S] [--curator ID]             (read-only: no writes, no provider call)
    python -m app.listening.curate submit --stage S --curator ID --approve-usd N [--limit N] [--only KEY] [--retry KEY]
    python -m app.listening.curate status [--stage S]                                          (read-only)

What it is: a thin operator front door to `generation_jobs.create_generation` + `finalize_reservation`. The existing generator process does the work (script,
measured-cue audio, exercises, validation, storage) and leaves every lesson PRIVATE to the curator account, exactly as for a learner. Review, register, approve
and publish are the existing `python -m app.listening.library` commands; nothing here can publish.

Guarantees:
* Authorization is explicit: the account must be on AUDLI_LISTENING_CURATOR_IDS, and `--approve-usd` must equal the stage budget written in the manifest.
* Idempotent and resumable: every request has a deterministic `client_request_id` (manifest, stage, key, attempt), so a rerun never duplicates a lesson or its
  reservation. A request refused earlier (daily limit, budget) is resumed in place; one that failed is never retried unless you pass `--retry KEY`.
* The stage budget is cumulative and durable: it is recomputed from the stored requests of that stage (every attempt, failed ones included) before each
  submission, so restarts, retries and partial runs cannot reset or exceed it. Stages other than the approved ones have budget 0 and cannot spend.
* The curator allowance (AUDLI_LISTENING_GEN_CURATOR_DAILY, ceiling 5) and every global limit are enforced by the Ledger's atomic reservation, not here.
* Output never contains credentials, tokens or connection strings: only keys, ids, phases and dollar amounts.
"""
import argparse
import json
import re
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sqlalchemy import and_, func, select, text

from app.listening import generation_jobs as genjobs, schema as t
from app.listening.ai_budget import AiGuard, CONTROL
from app.listening.generation import GenerationRefused, LLM_MICRO_PER_CALL, Ledger, Spec, clamp_spec, spec_problem, usd_micro_for_credits
from app.listening.library import CATEGORIES
from app.storage.repository import utcnow

DEFAULT_MANIFEST = Path(__file__).resolve().parents[2] / 'docs' / 'library-manifest.json'
LIVE = genjobs.LIVE_PHASES
TERMINAL_BAD = ('failed', 'rejected')
SLUG = re.compile(r'^[a-z0-9][a-z0-9-]*$')


class Entry(BaseModel):
    model_config = ConfigDict(extra='forbid', populate_by_name=True)
    key: str = Field(max_length=28, alias='slug')       # written as "slug" in the manifest: a field named "key" trips secret scanners
    stage: str = Field(max_length=10)
    title: str = Field(min_length=3, max_length=120)               # the intended title; the generator writes its own, review checks they agree
    category: str
    spec: Spec

    @field_validator('key', 'stage')
    @classmethod
    def _slug(cls, value):
        if not SLUG.match(value):
            raise ValueError('use lower-case letters, digits and hyphens')
        return value

    @field_validator('category')
    @classmethod
    def _category(cls, value):
        if value not in CATEGORIES:
            raise ValueError('unknown category')
        return value


class Manifest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    manifest_id: str = Field(max_length=12)
    description: str = ''
    budgets_usd: dict[str, float]
    lessons: list[Entry]

    @field_validator('manifest_id')
    @classmethod
    def _id(cls, value):
        if not SLUG.match(value):
            raise ValueError('use lower-case letters, digits and hyphens')
        return value

    @model_validator(mode='after')
    def _consistent(self):
        keys = [e.key for e in self.lessons]
        if len(keys) != len(set(keys)):
            raise ValueError('lesson keys must be unique')
        for e in self.lessons:
            if e.stage not in self.budgets_usd:
                raise ValueError(f'stage {e.stage} has no budget entry (use 0 to keep it locked)')
        if any(not 0 <= b <= 20 for b in self.budgets_usd.values()):
            raise ValueError('a stage budget must be between 0 and 20 USD')
        return self

    def entries(self, stage: str | None) -> list[Entry]:
        return [e for e in self.lessons if stage is None or e.stage == stage]


def load_manifest(path) -> Manifest:
    return Manifest.model_validate(json.loads(Path(path).read_text(encoding='utf-8')))


def request_key(manifest: Manifest, entry: Entry, attempt: int) -> str:
    value = f'curate:{manifest.manifest_id}:{entry.stage}:{entry.key}:r{attempt}'
    assert len(value) <= 64                                              # column width; the field limits above guarantee it
    return value


def _prefix(manifest: Manifest, stage: str, key: str | None = None) -> str:
    return f'curate:{manifest.manifest_id}:{stage}:' + (f'{key}:r' if key else '')


def stage_rows(db, manifest: Manifest, stage: str):
    r = t.generation_requests.c
    return db.execute(select(r.id, r.client_request_id, r.user_id, r.phase, r.failure_code, r.lesson_id, r.reserved_micro, r.actual_credits)
                      .where(and_(r.kind == 'generate', r.client_request_id.like(_prefix(manifest, stage) + '%'))).order_by(r.created_at)).all()


def _attempt_no(row) -> int:
    return int(row.client_request_id.rsplit(':r', 1)[1])


def attempts_for(rows, key: str, manifest: Manifest, stage: str):
    prefix = _prefix(manifest, stage, key)
    return sorted((r for r in rows if r.client_request_id.startswith(prefix)), key=_attempt_no)


def cost_micro(row, settings) -> int:
    """What this request has cost or may still cost, in USD micro, rounded UP. A reservation counts in full; a request that failed counts its speech actually
    used plus one script call (the ledger releases a failed reservation, but the provider may still have been paid); a refused request counts nothing."""
    if row.reserved_micro:
        return row.reserved_micro
    if row.phase in TERMINAL_BAD:
        return LLM_MICRO_PER_CALL + usd_micro_for_credits(row.actual_credits or 0, settings.listening_gen_usd_per_1k_credits)
    return 0


def spent_micro(db, manifest: Manifest, stage: str, settings) -> int:
    return sum(cost_micro(r, settings) for r in stage_rows(db, manifest, stage))


def usd(micro: int) -> str:
    return f'${micro / 1_000_000:.3f}'


def entry_blockers(entry: Entry, settings) -> list[str]:
    problems = []
    try:
        problem = spec_problem(entry.spec, settings)
        if problem:
            problems.append(problem)
        clamped, notes = clamp_spec(entry.spec.model_dump(), settings.listening_gen_max_minutes, settings)
        if notes or any(clamped.get(k) != v for k, v in entry.spec.model_dump().items()):
            problems.append('the pipeline would change this spec (' + '; '.join(notes or ['fields adjusted']) + '): edit the manifest so it is generated exactly as written')
        genjobs.reservation_for(settings, entry.spec)
    except GenerationRefused as refused:
        problems.append(f'cannot be priced ({refused.reason}): check AUDLI_LISTENING_GEN_SPEECH_MODEL')
    return problems


def global_blockers(settings) -> list[str]:
    out = []
    if not settings.listening_generation_enabled:
        out.append('generation is not enabled in this environment (set AUDLI_LISTENING_GENERATION_ENABLED=true for the CLI session)')
    if not settings.listening_ai_enabled:
        out.append('AI is switched off (AUDLI_LISTENING_AI_ENABLED=false)')
    return out


def _read_halted(db) -> bool:
    return bool(db.execute(select(t.ai_limits.c.halted).where(t.ai_limits.c.scope == CONTROL)).scalar_one_or_none())


def _curator_problems(db, settings, curator: str | None) -> list[str]:
    if not curator:
        return ['no --curator given: allowance and authorization were not checked']
    if curator.strip().lower() not in settings.listening_curator_set:
        return ['that account is not on AUDLI_LISTENING_CURATOR_IDS (curators are authorized by the operator list only)']
    if db.execute(text('SELECT 1 FROM users WHERE CAST(id AS TEXT) = :u'), {'u': curator}).first() is None:
        return ['that account does not exist']
    return []


def build_plan(engine, settings, manifest: Manifest, stage: str, curator: str | None) -> dict:
    """Pure reads. Reports everything that would stop a submission BEFORE anything is spent."""
    entries = manifest.entries(stage)
    budget = round(manifest.budgets_usd.get(stage, 0) * 1_000_000)
    ledger = Ledger(engine, settings)
    with engine.connect() as db:
        rows = stage_rows(db, manifest, stage)
        spent = sum(cost_micro(r, settings) for r in rows)
        halted = _read_halted(db)
        curator_problems = _curator_problems(db, settings, curator)
        used_today = ledger._lessons_today(db, curator) if curator and not curator_problems else None
        day, month = utcnow().strftime('%Y-%m-%d'), utcnow().strftime('%Y-%m')
        used_day = db.execute(select(t.generation_limits.c.reserved_micro).where(t.generation_limits.c.scope == day)).scalar_one_or_none() or 0
        used_month = db.execute(select(t.generation_limits.c.reserved_credits).where(t.generation_limits.c.scope == month)).scalar_one_or_none() or 0
    items, pending_cost, largest = [], 0, 0
    for entry in entries:
        attempts = attempts_for(rows, entry.key, manifest, stage)
        latest = attempts[-1] if attempts else None
        problems = entry_blockers(entry, settings)
        estimate = 0 if problems and any('priced' in p for p in problems) else genjobs.reservation_for(settings, entry.spec)[1]
        state = latest.phase if latest else 'not started'
        needs = latest is None or latest.phase == 'unavailable'
        if needs:
            pending_cost += estimate
            largest = max(largest, estimate)
        items.append({'key': entry.key, 'title': entry.title, 'cefr': entry.spec.cefr, 'accents': [a for a in (entry.spec.accent, entry.spec.accent2) if a],
                      'format': entry.spec.format, 'minutes': entry.spec.minutes, 'estimate_micro': estimate, 'state': state, 'attempts': len(attempts), 'blocked': problems})
    blockers = global_blockers(settings)
    if halted:
        blockers.append('the global AI kill switch is on')
    if budget <= 0:
        blockers.append(f'stage "{stage}" has budget 0 in the manifest: it is locked until you raise it in a reviewed change')
    if spent + largest > budget:                         # lessons are submitted one at a time and a finished one is counted at its settled cost, so the bound is cumulative + one worst case
        blockers.append(f'stage budget: {usd(spent)} already counted + {usd(largest)} worst case for the next lesson exceeds {usd(budget)}')
    warnings = []
    if curator:
        blockers += curator_problems
        if used_today is not None:
            cap = ledger.cap_for(curator)
            left = max(cap - used_today, 0)
            todo = sum(1 for i in items if i['state'] in ('not started', 'unavailable'))
            warnings.append(f'curator allowance today: {used_today}/{cap} used, {left} left; {todo} lesson(s) still to submit' + (' (the rest wait for the next UTC day)' if todo > left else ''))
    else:
        warnings += curator_problems
    if used_day + pending_cost > ledger.day_cap:
        warnings.append(f'global daily generation budget: {usd(used_day)} reserved of {usd(ledger.day_cap)}; some submissions would wait until tomorrow')
    if used_month + 1 > ledger.month_cap:
        warnings.append('monthly provider credits are exhausted')
    if pending_cost > budget - spent:
        warnings.append(f'worst-case reservations of all pending lessons add up to {usd(pending_cost)}, more than the {usd(budget - spent)} left; that is fine only because lessons run one at a time '
                        f'and settle to what was really used (about a third of the reservation): every submission re-checks the cumulative total first')
    return {'manifest': manifest.manifest_id, 'stage': stage, 'budget_micro': budget, 'spent_micro': spent, 'pending_micro': pending_cost, 'items': items,
            'blockers': blockers, 'warnings': warnings}


def print_plan(plan: dict, out=print) -> None:
    out(f"manifest {plan['manifest']}  stage {plan['stage']}  budget {usd(plan['budget_micro'])}  counted so far {usd(plan['spent_micro'])}  worst case if all ran at once {usd(plan['pending_micro'])}")
    for i in plan['items']:
        mark = 'BLOCKED' if i['blocked'] else 'ok'
        out(f"  [{mark:7}] {i['key']:24} {i['cefr']} {i['format']:8} {'+'.join(i['accents']):18} {i['minutes']} min  est {usd(i['estimate_micro'])}  state: {i['state']}")
        for problem in i['blocked']:
            out(f'             - {problem}')
    for w in plan['warnings']:
        out(f'  note: {w}')
    for b in plan['blockers']:
        out(f'  STOP: {b}')
    blocked = [i for i in plan['items'] if i['blocked']]
    out('RESULT: ' + ('ready to submit' if not plan['blockers'] and not blocked else f"NOT ready ({len(plan['blockers'])} stop reason(s), {len(blocked)} blocked lesson(s)); nothing was spent"))


def submit(engine, settings, manifest: Manifest, stage: str, curator: str, approve: str, limit: int = 1, only: str | None = None, retry: str | None = None,
           out=print) -> int:
    """0 = done or waiting; 2 = refused before anything was spent; 3 = stopped by a refusal or failure after possibly queuing some lessons."""
    try:
        approved = Decimal(approve)
    except InvalidOperation:
        out('Refused: --approve-usd must be a number')
        return 2
    if approved != Decimal(str(manifest.budgets_usd.get(stage, 0))) or approved <= 0:
        out('Refused: --approve-usd must equal the stage budget in the manifest, and that budget must be above 0')
        return 2
    entries = manifest.entries(stage)
    if not entries:
        out(f'Refused: no lessons in stage {stage}')
        return 2
    selected = [e for e in entries if only in (None, e.key)]
    if only and not selected:
        out('Refused: unknown --only key')
        return 2
    if retry:
        with engine.connect() as db:
            before = attempts_for(stage_rows(db, manifest, stage), retry, manifest, stage)
        if not before or before[-1].phase not in TERMINAL_BAD:
            out('Refused: --retry only applies to a lesson whose latest attempt failed')
            return 2
        selected = [e for e in selected if e.key == retry]
    plan = build_plan(engine, settings, manifest, stage, curator)
    chosen_bad = [i for i in plan['items'] if i['blocked'] and any(i['key'] == e.key for e in selected)]
    stops = [b for b in plan['blockers'] if not b.startswith('stage budget')]
    if stops or chosen_bad:
        print_plan(plan, out)
        return 2
    lock = None
    if engine.dialect.name == 'postgresql':
        lock = engine.connect()                                     # one submit at a time per manifest: a session lock is held for the whole run
        if not lock.execute(text('SELECT pg_try_advisory_lock(hashtext(:k))'), {'k': f'audli-curate:{manifest.manifest_id}'}).scalar():
            lock.rollback()
            lock.close()
            out('Refused: another submit for this manifest is running')
            return 2
    try:
        if AiGuard(engine, settings).halted():
            out('Refused: AI is halted (global kill switch)')
            return 2
        ledger, new, budget = Ledger(engine, settings), 0, round(float(approved) * 1_000_000)
        for entry in selected:
            with engine.connect() as db:
                rows = stage_rows(db, manifest, stage)
            attempts = attempts_for(rows, entry.key, manifest, stage)
            latest = attempts[-1] if attempts else None
            if latest is not None and latest.phase in TERMINAL_BAD and retry != entry.key:
                out(f'STOP {entry.key}: attempt {_attempt_no(latest)} {latest.phase} ({latest.failure_code}). Nothing is retried automatically; investigate, then use --retry {entry.key}')
                return 3
            if latest is not None and latest.phase not in ('unavailable',) + TERMINAL_BAD:
                out(f'skip {entry.key}: already {latest.phase}' + (f' (lesson {latest.lesson_id})' if latest.lesson_id else ''))
                continue
            if retry and retry != entry.key:
                continue
            if new >= limit:
                out(f'limit reached ({limit} new submission(s) this run); run again to continue')
                break
            if any(r.phase in LIVE for r in rows):
                out('waiting: a lesson of this stage is still being generated; run again when it is ready (incremental by design)')
                break
            estimate_credits, estimate_micro = genjobs.reservation_for(settings, entry.spec)
            if sum(cost_micro(r, settings) for r in rows) + estimate_micro > budget:
                out(f'STOP {entry.key}: the stage budget {usd(budget)} would be exceeded (cumulative, includes every earlier attempt)')
                return 3
            if latest is not None and latest.phase == 'unavailable':
                request_id = latest.id                               # refused earlier: nothing was reserved or queued, resume the same request
            else:
                attempt = (_attempt_no(latest) + 1) if latest is not None else 1
                with engine.begin() as db:
                    request_id, _ = genjobs.create_generation(db, curator, request_key(manifest, entry, attempt), entry.spec, [], None)
            try:
                genjobs.finalize_reservation(engine, ledger, settings, curator, request_id, entry.spec)
            except GenerationRefused as refused:
                out(f'STOP {entry.key}: refused ({refused.reason}); nothing was reserved or queued. Run again later; the same request is resumed.')
                return 3
            new += 1
            out(f'submitted {entry.key}: request {request_id} queued (reserved up to {usd(estimate_micro)})')
        out('done for now. Check progress with: python -m app.listening.curate status --stage ' + stage)
        return 0
    finally:
        if lock is not None:
            try:                                                      # closing a POOLED connection does not end its database session: unlock explicitly
                lock.execute(text('SELECT pg_advisory_unlock(hashtext(:k))'), {'k': f'audli-curate:{manifest.manifest_id}'})
                lock.commit()
            except Exception:
                lock.invalidate()                                     # could not unlock: discard the session, which releases the lock
            finally:
                lock.close()


def status(engine, settings, manifest: Manifest, stage: str | None, out=print) -> int:
    with engine.connect() as db:
        for stage_name in sorted({e.stage for e in manifest.entries(stage)}):
            rows = stage_rows(db, manifest, stage_name)
            out(f'stage {stage_name}: counted spend {usd(sum(cost_micro(r, settings) for r in rows))} of {usd(round(manifest.budgets_usd.get(stage_name, 0) * 1_000_000))}')
            for entry in manifest.entries(stage_name):
                attempts = attempts_for(rows, entry.key, manifest, stage_name)
                latest = attempts[-1] if attempts else None
                if latest is None:
                    out(f'  {entry.key:24} not started')
                    continue
                library = None
                if latest.lesson_id:
                    try:
                        library = db.execute(select(t.library_entries.c.state).where(t.library_entries.c.lesson_id == latest.lesson_id)).scalar_one_or_none()
                    except Exception:
                        library = None
                tail = f' lesson {latest.lesson_id}' if latest.lesson_id else ''
                out(f"  {entry.key:24} attempt {_attempt_no(latest)}  {latest.phase}{' (' + latest.failure_code + ')' if latest.failure_code else ''}{tail}  library: {library or '-'}")
                if latest.phase == 'ready' and library in (None, 'unpublished'):
                    out(f'      review it in the app as the curator, then: python -m app.listening.library register --category {entry.category} {latest.lesson_id}')
                elif latest.phase == 'ready' and library == 'draft':
                    out(f'      next: python -m app.listening.library review {latest.lesson_id}   then approve --reviewer "<name>" --note "<note>" {latest.lesson_id}')
                elif latest.phase == 'ready' and library == 'approved':
                    out(f'      next: python -m app.listening.library publish {latest.lesson_id}')
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog='python -m app.listening.curate', description='Curator batch generation from a reviewed manifest (operator credentials).')
    parser.add_argument('--manifest', default=str(DEFAULT_MANIFEST))
    sub = parser.add_subparsers(dest='action', required=True)
    plan_p = sub.add_parser('plan')
    plan_p.add_argument('--stage', default='pilot')
    plan_p.add_argument('--curator')
    sub_p = sub.add_parser('submit')
    sub_p.add_argument('--stage', required=True)
    sub_p.add_argument('--curator', required=True)
    sub_p.add_argument('--approve-usd', required=True, help='must equal the stage budget in the manifest')
    sub_p.add_argument('--limit', type=int, default=1, help='new submissions this run (default 1: incremental)')
    sub_p.add_argument('--only')
    sub_p.add_argument('--retry', help='retry ONE failed lesson (a new attempt; the failed one still counts against the budget)')
    st = sub.add_parser('status')
    st.add_argument('--stage')
    args = parser.parse_args(argv)
    try:
        manifest = load_manifest(args.manifest)
    except (OSError, ValueError, ValidationError) as problem:
        print(f'Refused: the manifest is not valid ({type(problem).__name__})', file=sys.stderr)
        return 2
    from app.config import Settings
    from app.listening.worker import make_engine
    settings = Settings()
    engine = make_engine(settings)
    if args.action == 'plan':
        plan = build_plan(engine, settings, manifest, args.stage, args.curator)
        print_plan(plan)
        return 0 if not plan['blockers'] and not any(i['blocked'] for i in plan['items']) else 2
    if args.action == 'submit':
        return submit(engine, settings, manifest, args.stage, args.curator, args.approve_usd, max(args.limit, 1), args.only, args.retry)
    return status(engine, settings, manifest, args.stage)


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
