"""Privileged CLI for shared state. No HTTP control endpoint, no feature auto-enable."""
import argparse
import json
from app.config import Settings
from app.storage.adapters import create_repository
from app.storage.accent_controls import SharedAccentControls
from app.storage import schema as s
from sqlalchemy import select, func


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('status','disable','configure','enable'))
    parser.add_argument('--daily-characters', type=int)
    parser.add_argument('--concurrent', type=int)
    args = parser.parse_args()
    # Emergency disable/status must work even if voice evidence was damaged.
    settings = Settings() if args.action == 'enable' else Settings(accent_practice_enabled=False, passage_voices={})
    repo = create_repository(settings)
    controls = SharedAccentControls(repo.engine, settings)
    try:
        if args.action == 'enable':
            if settings.environment != 'production' or not settings.available_passage_voices:
                raise ValueError('Enable requires production settings and formally validated voice mapping')
            controls.configure(enabled=True, daily_limit=args.daily_characters, concurrent_limit=args.concurrent)
        elif args.action == 'configure':
            controls.configure(enabled=False, daily_limit=args.daily_characters, concurrent_limit=args.concurrent)
        elif args.action == 'disable':
            controls.configure(enabled=False)
        row = controls.read()
        with controls.engine.connect() as db:
            states = db.execute(select(s.accent_reservations.c.state,func.count(),func.sum(s.accent_reservations.c.characters))
                .group_by(s.accent_reservations.c.state)).all()
            row['leased_active_count'] = db.execute(select(func.count()).select_from(s.accent_reservations)
                .where(s.accent_reservations.c.lease_until > controls.clock(db))).scalar_one()
        row['updated_at'] = row['updated_at'].isoformat()
        row['usage_by_state'] = [{'state':state,'reservations':count,'reserved_characters':chars} for state,count,chars in states]
        print(json.dumps(row, sort_keys=True))
    finally:
        controls.close()
        repo.close()


if __name__ == '__main__':
    try:
        main()
    except Exception:
        raise SystemExit('Shared accent control failed; inspect database access/schema and validated configuration. No credentials or learner content logged.') from None
