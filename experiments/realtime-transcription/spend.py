"""Hard spend control for the experiment: every paid call is reserved against a persisted ledger first.

The cap is $5.00 in total. Estimates can be wrong, so nothing is started that would take the *estimated* total past
$4.50 (90%). Prices carry their provenance; token-priced items are charged from the API's reported usage when
available, otherwise from a deliberately high per-minute assumption.
"""
from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import time

CAP_USD = 5.00
STOP_FRACTION = 0.90
PILOT_RESERVE_USD = 2.50   # kept back for the consented-recording pilot; synthetic work cannot use it

# USD per minute of audio. 'basis' says where the number comes from; 'assumed' rows are high on purpose.
PRICES = {
    'gpt-live-transcribe': (0.017, 'published: model page, 2026-10-23'),
    'gpt-transcribe': (0.0045, 'published: model page, 2026-10-23'),
    'gpt-4o-mini-transcribe': (0.006, 'assumed: 2x the usual ~$0.003/min; model page lists tokens only ($1.25/M audio in)'),
    'tts-audio': (0.03, 'assumed: 2x the usual ~$0.015/min for gpt-4o-mini-tts'),
}


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class Entry:
    item: str
    usd: float
    basis: str
    phase: str
    at: float


class Ledger:
    def __init__(self, path: Path, cap: float = CAP_USD, stop_fraction: float = STOP_FRACTION, pilot_reserve: float = PILOT_RESERVE_USD):
        self.path, self.cap, self.stop_fraction, self.pilot_reserve = Path(path), cap, stop_fraction, pilot_reserve
        self.entries: list[dict] = json.loads(self.path.read_text())['entries'] if self.path.exists() else []

    @property
    def spent(self) -> float:
        return round(sum(entry['usd'] for entry in self.entries), 6)

    @property
    def limit(self) -> float:
        return self.cap * self.stop_fraction

    def remaining(self, phase: str = 'synthetic') -> float:
        reserve = self.pilot_reserve if phase == 'synthetic' else 0.0
        return round(self.limit - reserve - self.spent, 6)

    def reserve(self, item: str, usd: float, phase: str = 'synthetic') -> None:
        """Refuse before spending. Call this with the worst-case cost of the next paid step."""
        if usd < 0 or usd > self.remaining(phase):
            raise BudgetExceeded(f'{item}: estimated ${usd:.4f} exceeds the ${self.remaining(phase):.4f} left for the {phase} phase '
                                 f'(cap ${self.cap:.2f}, stop at ${self.limit:.2f}, spent ${self.spent:.4f}).')

    def record(self, item: str, usd: float, basis: str, phase: str = 'synthetic') -> None:
        """Append under a file lock after re-reading the file, so two processes cannot drop each other's charges."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path.with_suffix('.lock'), 'w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if self.path.exists():
                self.entries = json.loads(self.path.read_text())['entries']
            self.entries.append({'item': item, 'usd': round(usd, 6), 'basis': basis, 'phase': phase, 'at': round(time.time())})
            temporary = self.path.with_suffix('.tmp')
            temporary.write_text(json.dumps({'cap_usd': self.cap, 'entries': self.entries}, indent=1))
            os.replace(temporary, self.path)

    def summary(self) -> dict:
        by_item: dict[str, float] = {}
        for entry in self.entries:
            by_item[entry['item']] = round(by_item.get(entry['item'], 0.0) + entry['usd'], 6)
        return {'cap_usd': self.cap, 'stop_at_usd': self.limit, 'spent_usd': self.spent, 'by_item': by_item,
                'remaining_synthetic_usd': self.remaining('synthetic'), 'remaining_pilot_usd': self.remaining('pilot')}


def audio_cost(model: str, seconds: float) -> float:
    return PRICES[model][0] * seconds / 60.0
