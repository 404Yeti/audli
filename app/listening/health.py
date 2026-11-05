"""Operational health of the Audli 2.0 background system, as numbers only (no learner id, transcript, audio name or provider message).

    python -m app.listening.health          # prints JSON; exit status 1 if any alert fires, so a cron/uptime monitor can page on it
    GET /api/listening/ops/health           # same JSON for an external monitor; needs `Authorization: Bearer $AUDLI_LISTENING_OPS_TOKEN`, absent if unset

Alerts: a job waiting longer than the limit (the worker is down or stuck), a lease that expired without a taker, jobs that failed in the last day,
an answer pending longer than the limit, a recording held past the limit (the sweep or the worker is not running), the kill switch on, or no
AI budget left. Thresholds are arguments so they can be tuned without code changes.
"""
import json
import sys
from datetime import datetime, timedelta

from sqlalchemy import and_, func, select

from app.listening import schema as t
from app.storage.repository import utcnow

QUEUE_AGE_LIMIT = 300         # seconds a ready job may wait before someone should be told
PENDING_LIMIT = 600           # seconds an answer may stay "being checked"
RECORDING_LIMIT = 7200        # seconds a temporary recording may exist


def _age(now: datetime, then: datetime | None) -> int:
    if then is None:
        return 0
    if then.tzinfo is None and now.tzinfo is not None:
        then = then.replace(tzinfo=now.tzinfo)
    return max(0, int((now - then).total_seconds()))


def collect(engine, now: datetime | None = None) -> dict:
    now = now or utcnow()
    with engine.connect() as db:
        by_status = dict(db.execute(select(t.jobs.c.status, func.count()).group_by(t.jobs.c.status)).all())
        oldest = db.execute(select(func.min(t.jobs.c.run_after)).where(and_(t.jobs.c.status == 'queued', t.jobs.c.run_after <= now))).scalar_one()
        expired = db.execute(select(func.count()).where(and_(t.jobs.c.status == 'running', t.jobs.c.lease_expires_at < now))).scalar_one()
        failed_day = db.execute(select(func.count()).where(and_(t.jobs.c.status == 'failed', t.jobs.c.finished_at > now - timedelta(hours=24)))).scalar_one()
        pending = db.execute(select(func.count(), func.min(t.spoken.c.updated_at)).where(t.spoken.c.status == 'pending')).one()
        held = db.execute(select(func.count(), func.min(t.spoken.c.updated_at)).where(t.spoken.c.recording_key.is_not(None))).one()
        today = db.execute(select(t.ai_limits.c.reserved_micro, t.ai_limits.c.calls).where(t.ai_limits.c.scope == now.strftime('%Y-%m-%d'))).first()
        halted = db.execute(select(t.ai_limits.c.halted).where(t.ai_limits.c.scope == 'control')).scalar_one_or_none()
    return {'at': now.isoformat(timespec='seconds'),
            'jobs': {'queued': by_status.get('queued', 0), 'running': by_status.get('running', 0), 'failed_total': by_status.get('failed', 0),
                     'failed_last_24h': failed_day, 'oldest_ready_seconds': _age(now, oldest), 'expired_leases': expired},
            'answers': {'pending': pending[0], 'oldest_pending_seconds': _age(now, pending[1])},
            'recordings': {'held': held[0], 'oldest_held_seconds': _age(now, held[1])},
            'ai': {'reserved_micro_today': today[0] if today else 0, 'calls_today': today[1] if today else 0, 'halted': bool(halted)}}


def alerts(report: dict, daily_ceiling_micro: int | None = None, queue_age_limit: int = QUEUE_AGE_LIMIT, pending_limit: int = PENDING_LIMIT,
           recording_limit: int = RECORDING_LIMIT) -> list[str]:
    found = []
    if report['jobs']['oldest_ready_seconds'] > queue_age_limit:
        found.append(f"a job has waited {report['jobs']['oldest_ready_seconds']}s: is the worker running?")
    if report['jobs']['expired_leases']:
        found.append(f"{report['jobs']['expired_leases']} job lease(s) expired with no worker taking over")
    if report['jobs']['failed_last_24h']:
        found.append(f"{report['jobs']['failed_last_24h']} job(s) failed in the last 24 hours")
    if report['answers']['oldest_pending_seconds'] > pending_limit:
        found.append(f"an answer has been pending {report['answers']['oldest_pending_seconds']}s")
    if report['recordings']['oldest_held_seconds'] > recording_limit:
        found.append(f"a temporary recording has existed {report['recordings']['oldest_held_seconds']}s: the cleanup is not running")
    if report['ai']['halted']:
        found.append('the AI kill switch is on')
    if daily_ceiling_micro is not None and report['ai']['reserved_micro_today'] >= daily_ceiling_micro:
        found.append("today's AI budget is used up")
    return found


def report_with_alerts(engine, settings=None, now: datetime | None = None) -> dict:
    from app.listening.ai_budget import usd_to_micro
    report = collect(engine, now)
    ceiling = usd_to_micro(settings.listening_ai_daily_budget_usd) if settings is not None else None
    report['alerts'] = alerts(report, ceiling)
    if settings is not None and not settings.listening_ai_enabled:
        report['alerts'].append('AUDLI_LISTENING_AI_ENABLED is false in this process')
    return report


def main(argv: list[str]) -> int:
    from app.config import Settings
    from app.listening.worker import make_engine
    settings = Settings()
    report = report_with_alerts(make_engine(settings), settings)
    print(json.dumps(report, indent=1))
    return 1 if report['alerts'] else 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
