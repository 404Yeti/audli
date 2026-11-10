"""Learner-side reads for the Daily Challenge. Read-only: the API process never writes a daily table.

* today's challenge is the `active` row for today's date in the schedule time zone (Asia/Ho_Chi_Minh by default), the same boundary for every learner;
* if there is none (not generated yet, failed) the answer is "unavailable". It is NEVER an older lesson: a challenge is never replayed or substituted;
* a challenge stays out of Discover until its day is over (`hidden_lesson_ids`), then it is simply an ordinary shared lesson, forever;
* completion credit is DERIVED from immutable attempts: an attempt on the challenge's lesson that was completed on the challenge's own day. Nothing is written
  in the learner's request, so a retry, a refresh or two tabs cannot credit a day twice, existing sessions are untouched, and a deployment without the daily
  tables answers exactly as before. Streaks are consecutive credited days ending today (or yesterday while today is still open).
"""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import and_, select, text
from sqlalchemy.exc import DBAPIError

from app.listening import schema as t
from app.storage.repository import aware, utcnow

_TABLE_SEEN = False


def zone(settings) -> ZoneInfo:
    return ZoneInfo(settings.listening_daily_timezone)


def local_date(settings, now: datetime | None = None) -> date:
    return (now or utcnow()).astimezone(zone(settings)).date()


def next_boundary(settings, now: datetime | None = None) -> datetime:
    """The next local midnight, as UTC."""
    z = zone(settings)
    local = (now or utcnow()).astimezone(z)
    return datetime.combine(local.date() + timedelta(days=1), datetime.min.time(), tzinfo=z).astimezone(timezone.utc)


def table_readable(db) -> bool:
    """True when migration 015 is applied and this role may read it. Plain SELECTs only (the catalog connection is read-only and rejects SAVEPOINT)."""
    global _TABLE_SEEN
    if _TABLE_SEEN:
        return True
    name = t.daily_challenges.name
    if db.dialect.name == 'postgresql':
        if not db.execute(text("SELECT CASE WHEN to_regclass(:n) IS NULL THEN false ELSE has_table_privilege(current_user, :n, 'SELECT') END"), {'n': name}).scalar():
            return False
    else:
        try:
            db.execute(select(t.daily_challenges.c.challenge_date).limit(1)).first()
        except DBAPIError:
            return False
    _TABLE_SEEN = True
    return True


def hidden_lesson_ids(db, settings, now: datetime | None = None) -> set[str]:
    """Lessons that belong to today's or a later (scheduled) day: not shown in Discover until the day has passed."""
    if not table_readable(db):
        return set()
    today = local_date(settings, now).isoformat()
    return {r[0] for r in db.execute(select(t.daily_challenges.c.lesson_id).where(and_(t.daily_challenges.c.lesson_id.is_not(None),
            t.daily_challenges.c.state.in_(('scheduled', 'active')), t.daily_challenges.c.challenge_date >= today)))}


def paused(db) -> bool:
    return bool(db.execute(select(t.daily_control.c.paused).where(t.daily_control.c.scope == 'control')).scalar_one_or_none())


# ---- streaks (pure) ------------------------------------------------------------------------------------------------------------------

def streaks(days: set[date], today: date) -> dict:
    """current = consecutive credited days ending today, or ending yesterday while today is still open; longest over all history."""
    anchor = today if today in days else today - timedelta(days=1)
    current, d = 0, anchor
    while d in days:
        current += 1
        d -= timedelta(days=1)
    longest, run, previous = 0, 0, None
    for d in sorted(days):
        run = run + 1 if previous is not None and d - previous == timedelta(days=1) else 1
        longest, previous = max(longest, run), d
    return {'current': current, 'longest': longest, 'days_completed': len(days)}


def credited_days(db, settings, user_id: str) -> set[date]:
    """Challenge days this learner earned: a completed attempt on the challenge's lesson whose completion falls on the challenge's own local day."""
    rows = db.execute(select(t.daily_challenges.c.challenge_date, t.attempts.c.completed_at)
                      .join(t.versions, t.versions.c.lesson_id == t.daily_challenges.c.lesson_id)
                      .join(t.attempts, t.attempts.c.version_id == t.versions.c.id)
                      .where(and_(t.attempts.c.user_id == user_id, t.attempts.c.status == 'completed', t.attempts.c.completed_at.is_not(None),
                                  t.daily_challenges.c.state == 'active'))).all()
    z, out = zone(settings), set()
    for challenge_date, completed_at in rows:
        if aware(completed_at).astimezone(z).date().isoformat() == challenge_date:
            out.add(date.fromisoformat(challenge_date))
    return out


def today_view(db, settings, user_id: str, catalog_item_for, now: datetime | None = None) -> dict:
    """`catalog_item_for(lesson_id)` returns the learner's CatalogItem dict for that lesson (store.list_catalog without hiding, filtered)."""
    now = now or utcnow()
    today = local_date(settings, now)
    base = {'date': today.isoformat(), 'next_at': next_boundary(settings, now)}
    if not table_readable(db):                                  # the scheduler's pause switch is deliberately NOT read here: it stops generation and publication, not a live challenge
        return {**base, 'status': 'unavailable', 'lesson': None, 'completed_today': False, 'streak': streaks(set(), today)}
    row = db.execute(select(t.daily_challenges.c.lesson_id, t.daily_challenges.c.category, t.daily_challenges.c.published_at).where(and_(
        t.daily_challenges.c.challenge_date == today.isoformat(), t.daily_challenges.c.state == 'active'))).first()
    days = credited_days(db, settings, user_id)
    if row is None or row.lesson_id is None:
        return {**base, 'status': 'unavailable', 'lesson': None, 'completed_today': False, 'streak': streaks(days, today)}
    item = catalog_item_for(row.lesson_id)
    if item is None:
        return {**base, 'status': 'unavailable', 'lesson': None, 'completed_today': False, 'streak': streaks(days, today)}
    return {**base, 'status': 'ready', 'lesson': item, 'category': row.category, 'completed_today': today in days, 'streak': streaks(days, today)}
