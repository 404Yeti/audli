"""Numeric-only latency aggregation. No learner IDs, text, audio, URLs or exception messages.

Stage names are code constants (``operation(settings, 'turn.assessment')``), never user data.
Cardinality is still bounded so a future mistake cannot create unbounded memory or log volume.
Quantiles are histogram upper bounds, i.e. conservative estimates, not exact samples.
"""
import asyncio
import json
import logging
import re
import threading
from bisect import bisect_left

BUCKETS_MS = (25, 50, 100, 250, 500, 1000, 2000, 3000, 5000, 8000, 12000, 20000, 30000, 60000)
MAX_STAGES = 160
MAX_DEPTH = 16
# Code constants and route templates such as 'http.POST /api/lessons/{lesson_id}/heard'; never request values.
STAGE_NAME = re.compile(r'^[A-Za-z][A-Za-z0-9_.:/{} -]{0,95}$')


class _Stage:
    __slots__ = ('count', 'errors', 'total', 'peak', 'buckets')

    def __init__(self):
        self.count = self.errors = 0
        self.total = self.peak = 0.0
        self.buckets = [0] * (len(BUCKETS_MS) + 1)


def _quantile(stage: _Stage, q: float):
    if not stage.count:
        return None
    target = q * stage.count
    seen = 0
    for index, size in enumerate(stage.buckets):
        seen += size
        if seen >= target:
            # Overflow bucket reports the observed peak instead of an invented bound.
            return float(BUCKETS_MS[index]) if index < len(BUCKETS_MS) else round(stage.peak, 1)
    return round(stage.peak, 1)


class LatencyAggregator:
    def __init__(self):
        self._lock = threading.Lock()
        self.reset()

    def reset(self):
        with self._lock:
            self._stages: dict[str, _Stage] = {}
            self._depth = [0] * (MAX_DEPTH + 1)

    def record(self, name: str, elapsed_ms: float, ok: bool = True) -> None:
        if not (elapsed_ms >= 0 and elapsed_ms < 1e9):  # also rejects NaN
            return
        key = name if STAGE_NAME.match(name) else 'other'
        with self._lock:
            stage = self._stages.get(key)
            if stage is None:
                if len(self._stages) >= MAX_STAGES:
                    key = 'other'
                    stage = self._stages.setdefault(key, _Stage())
                else:
                    stage = self._stages[key] = _Stage()
            stage.count += 1
            stage.errors += 0 if ok else 1
            stage.total += elapsed_ms
            stage.peak = max(stage.peak, elapsed_ms)
            stage.buckets[bisect_left(BUCKETS_MS, elapsed_ms)] += 1

    def record_queue_depth(self, depth: int) -> None:
        """Requests ahead (current holder plus waiters) on the shared lock when a new one arrives."""
        with self._lock:
            self._depth[max(0, min(MAX_DEPTH, int(depth)))] += 1

    def snapshot(self, reset: bool = False) -> dict:
        with self._lock:
            stages = {name: {
                'count': stage.count, 'errors': stage.errors,
                'mean_ms': round(stage.total / stage.count, 1) if stage.count else None,
                'p50_ms_upper': _quantile(stage, .5), 'p90_ms_upper': _quantile(stage, .9),
                'p99_ms_upper': _quantile(stage, .99), 'max_ms': round(stage.peak, 1),
            } for name, stage in sorted(self._stages.items())}
            depth = {str(i): n for i, n in enumerate(self._depth) if n}
            if reset:
                self._stages, self._depth = {}, [0] * (MAX_DEPTH + 1)
        return {'version': 1, 'buckets_ms': list(BUCKETS_MS), 'stages': stages, 'lock_queue_depth_at_arrival': depth}


aggregator = LatencyAggregator()
_logger = logging.getLogger('uvicorn.error')


def emit_summary() -> None:
    snapshot = aggregator.snapshot(reset=True)
    if snapshot['stages']:
        _logger.info('Latency summary %s', json.dumps(snapshot, sort_keys=True, separators=(',', ':')))


async def summary_loop(interval_seconds: float) -> None:
    try:
        while True:
            await asyncio.sleep(interval_seconds)
            try:
                emit_summary()
            except Exception:  # Diagnostics must never take the API down or stop reporting.
                _logger.warning('Latency summary failed')
    except asyncio.CancelledError:
        try:
            emit_summary()  # Do not lose the final partial window on shutdown.
        except Exception:
            pass
        raise
