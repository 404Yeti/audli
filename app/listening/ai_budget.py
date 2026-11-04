"""Global spending ceiling and kill switch for every Audli 2.0 AI request (transcription, assessment and any future generation).

* **Reserve before you spend.** Immediately before an AI request is sent, its worst-case cost is *reserved* against the day's ceiling with one
  conditional UPDATE. The database decides, so any number of workers and processes together can never reserve past the ceiling, and a request
  that does not fit is never sent.
* **Fail closed.** No price known for the model, an implausible estimate, a database error, a missing row that cannot be created: all of them
  refuse the request. The only thing that lets a request through is a successful reservation.
* **Kill switch.** `AUDLI_LISTENING_AI_ENABLED=false` (needs a restart) or the database halt flag (`python -m app.listening.ai_budget halt`, takes
  effect on the next request, no deploy). Halted means no new upload is accepted and no request is dispatched. In-flight requests finish.
* **Settlement.** After a call the reservation is replaced by the cost computed from the usage the provider reported; a request the provider
  refused outright (4xx) is released; anything that may have been billed (timeouts, 5xx) stays reserved.

These are ESTIMATES made from a price table and token limits, not the provider's invoice. They protect against bugs and runaway loops; they are not
a substitute for a spend limit set at the provider (set one on the API key's project), which remains the real backstop.
"""
import json
import sys
from datetime import datetime

from sqlalchemy import case, insert, select, update
from sqlalchemy.exc import IntegrityError

from app.listening import schema as t
from app.listening.models import AUDIO_PRICES, TEXT_PRICES
from app.listening.providers import AssessmentInput, ProviderPermanent, ProviderTemporary, Transcript
from app.storage.repository import utcnow

MICRO = 1_000_000                    # reservations are integers in millionths of a US dollar
MAX_CALL_MICRO = 250_000             # one request is never allowed to reserve more than US$0.25: a larger estimate is a bug
MIN_ANSWER_MICRO = 10_000            # an upload is accepted only if at least US$0.01 is left (a worst-case answer is about US$0.006)
MAX_AUDIO_SECONDS = 60               # recordings.MAX_SECONDS: the longest answer that can reach the provider
CONTROL = 'control'


class BudgetRefused(ProviderTemporary):
    """The request was not sent. A temporary condition: the job queue retries it a few times, then the answer shows as unavailable."""


def usd_to_micro(usd: float) -> int:
    return round(usd * MICRO)


def transcription_micro(model: str, seconds: float = MAX_AUDIO_SECONDS) -> int:
    if model not in AUDIO_PRICES:
        raise BudgetRefused(f'no price is known for {model}', 'ai_budget')
    return -(-int(AUDIO_PRICES[model] * seconds / 60 * MICRO * 1000) // 1000)          # ceiling


def text_micro(model: str, input_chars: int, max_output_tokens: int) -> int:
    """Worst case: 1 token per 2.5 input characters plus overhead, and the whole output cap used (hidden reasoning counts as output)."""
    if model not in TEXT_PRICES:
        raise BudgetRefused(f'no price is known for {model}', 'ai_budget')
    price_in, price_out = TEXT_PRICES[model]
    tokens_in = int(input_chars / 2.5) + 300
    return -(-int((tokens_in * price_in + max_output_tokens * price_out) * 1000) // 1000)


class Reservation:
    def __init__(self, day: str, micro: int, task: str):
        self.day, self.micro, self.task = day, micro, task


class AiGuard:
    def __init__(self, engine, settings, clock=utcnow):
        self.engine, self.settings, self.clock = engine, settings, clock
        self.cap = usd_to_micro(settings.listening_ai_daily_budget_usd)

    def _day(self) -> str:
        return self.clock().strftime('%Y-%m-%d')

    def _row(self, db, scope: str) -> None:
        if db.execute(select(t.ai_limits.c.scope).where(t.ai_limits.c.scope == scope)).first() is None:
            try:
                with db.begin_nested():
                    db.execute(insert(t.ai_limits).values(scope=scope, reserved_micro=0, calls=0, halted=False, updated_at=self.clock()))
            except IntegrityError:
                pass                                              # a concurrent worker created it first

    def halted(self) -> bool:
        """True when AI is switched off by environment or by the database flag. Unknown state counts as halted."""
        if not self.settings.listening_ai_enabled:
            return True
        try:
            with self.engine.begin() as db:
                self._row(db, CONTROL)
                return bool(db.execute(select(t.ai_limits.c.halted).where(t.ai_limits.c.scope == CONTROL)).scalar_one())
        except Exception:
            return True

    def set_halted(self, value: bool) -> None:
        with self.engine.begin() as db:
            self._row(db, CONTROL)
            db.execute(update(t.ai_limits).where(t.ai_limits.c.scope == CONTROL).values(halted=value, updated_at=self.clock()))

    def status(self) -> dict:
        with self.engine.begin() as db:
            self._row(db, self._day())
            row = db.execute(select(t.ai_limits).where(t.ai_limits.c.scope == self._day())).mappings().one()
        halted = self.halted()
        return {'day': row['scope'], 'reserved_usd': row['reserved_micro'] / MICRO, 'calls': row['calls'], 'daily_ceiling_usd': self.cap / MICRO,
                'remaining_usd': max(0, self.cap - row['reserved_micro']) / MICRO, 'halted': halted, 'enabled_by_environment': self.settings.listening_ai_enabled,
                'note': 'estimated reservations, not billing; the provider spend limit is the real backstop'}

    def accepting(self) -> tuple[bool, str]:
        """May the API take a new spoken answer? No when halted or when too little of today's ceiling is left for one."""
        if self.halted():
            return False, 'paused'
        if self.settings.listening_provider == 'fake':
            return True, ''
        try:
            with self.engine.begin() as db:
                self._row(db, self._day())
                used = db.execute(select(t.ai_limits.c.reserved_micro).where(t.ai_limits.c.scope == self._day())).scalar_one()
        except Exception:
            return False, 'unavailable'
        return (True, '') if self.cap - used >= MIN_ANSWER_MICRO else (False, 'budget')

    def reserve(self, task: str, micro: int) -> Reservation:
        if not self.settings.listening_ai_enabled:
            raise BudgetRefused('AI requests are switched off', 'ai_halted')
        if not 0 < micro <= MAX_CALL_MICRO:
            raise BudgetRefused('implausible cost estimate', 'ai_budget')
        day = self._day()
        try:
            with self.engine.begin() as db:
                self._row(db, CONTROL)
                if db.execute(select(t.ai_limits.c.halted).where(t.ai_limits.c.scope == CONTROL)).scalar_one():
                    raise BudgetRefused('AI requests are halted', 'ai_halted')
                self._row(db, day)
                reserved = db.execute(update(t.ai_limits).where((t.ai_limits.c.scope == day) & (t.ai_limits.c.reserved_micro + micro <= self.cap))
                                      .values(reserved_micro=t.ai_limits.c.reserved_micro + micro, calls=t.ai_limits.c.calls + 1, updated_at=self.clock())).rowcount
                if reserved != 1:
                    raise BudgetRefused("today's AI budget is used up", 'ai_budget')
        except BudgetRefused:
            raise
        except Exception:
            raise BudgetRefused('budget could not be checked', 'ai_budget') from None        # fail closed
        return Reservation(day, micro, task)

    def settle(self, reservation: Reservation, actual_micro: int | None = None, release: bool = False) -> None:
        """Replace the reservation by the measured cost (or drop it if the provider refused the request). Never raises: a failure here keeps the safe, higher number."""
        delta = -reservation.micro if release else (actual_micro - reservation.micro if actual_micro is not None else 0)
        if delta == 0:
            return
        try:
            with self.engine.begin() as db:
                total = t.ai_limits.c.reserved_micro + delta
                db.execute(update(t.ai_limits).where(t.ai_limits.c.scope == reservation.day).values(reserved_micro=case((total >= 0, total), else_=0)))
        except Exception:
            return


class GuardedTranscriber:
    def __init__(self, inner, guard: AiGuard, model: str):
        self.inner, self.guard, self.model = inner, guard, model

    def transcribe(self, audio: bytes, content_type: str) -> Transcript:
        reservation = self.guard.reserve('transcription', transcription_micro(self.model))
        try:
            result = self.inner.transcribe(audio, content_type)
        except ProviderPermanent:
            self.guard.settle(reservation, release=True)                  # refused by the provider: not billed
            raise
        self.guard.settle(reservation, getattr(self.inner, 'last_cost_micro', None))
        return result


class GuardedAssessor:
    def __init__(self, inner, guard: AiGuard, model: str, max_output_tokens: int):
        self.inner, self.guard, self.model, self.max_output_tokens = inner, guard, model, max_output_tokens

    def assess(self, request: AssessmentInput) -> list[dict]:
        size = len(json.dumps({'script': request.script, 'question': request.question, 'points': request.points, 'text': request.transcript, 'title': request.title}))
        reservation = self.guard.reserve('assessment', text_micro(self.model, size, self.max_output_tokens))
        try:
            result = self.inner.assess(request)
        except ProviderPermanent:
            self.guard.settle(reservation, release=True)
            raise
        self.guard.settle(reservation, getattr(self.inner, 'last_cost_micro', None))
        return result


def main(argv: list[str]) -> int:
    """python -m app.listening.ai_budget status|halt|resume   (operator tool; prints no secrets)"""
    from app.config import Settings
    from app.repository import create_repository
    action = argv[0] if argv else 'status'
    if action not in ('status', 'halt', 'resume'):
        print('usage: python -m app.listening.ai_budget status|halt|resume', file=sys.stderr)
        return 2
    settings = Settings()
    guard = AiGuard(create_repository(settings).engine, settings)
    if action != 'status':
        guard.set_halted(action == 'halt')
    print(json.dumps(guard.status(), indent=1))
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
