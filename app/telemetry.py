"""Numeric-only latency aggregation. No learner IDs, text, audio, URLs or exception messages.

Stage names are code constants (``operation(settings, 'turn.assessment')``), never user data.
Cardinality is still bounded so a future mistake cannot create unbounded memory or log volume.
Quantiles are histogram upper bounds, i.e. conservative estimates, not exact samples.
"""
import asyncio
import itertools
import json
import logging
import re
import threading
from bisect import bisect_left

from app import turntrace

BUCKETS_MS = (25, 50, 100, 250, 500, 1000, 2000, 3000, 5000, 8000, 12000, 20000, 30000, 60000)
MAX_STAGES = 160
MAX_DEPTH = 16
MAX_GAUGES = 16
MAX_GAUGE_VALUE = 32
MAX_ROUTES = 128
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
            self._routes: dict[str, list] = {}   # name -> [calls, queries_sum, queries_max, checkouts_sum, checkouts_max, tx_sum, tx_max]
            self._stages: dict[str, _Stage] = {}
            self._depth = [0] * (MAX_DEPTH + 1)
            self._gauges: dict[str, list[int]] = {}

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

    def record_gauge(self, name: str, value: int) -> None:
        """Distribution of an integer level observed at event time (e.g. concurrent upstream calls). Bounded."""
        key = name if STAGE_NAME.match(name) else 'other'
        with self._lock:
            if key not in self._gauges and len(self._gauges) >= MAX_GAUGES:
                key = 'other'
            levels = self._gauges.setdefault(key, [0] * (MAX_GAUGE_VALUE + 1))
            levels[max(0, min(MAX_GAUGE_VALUE, int(value)))] += 1

    # Pause measurements are distributions of a level, not latencies: the longest silent gap inside a spoken answer in
    # 100 ms steps (a 1.2 s pause lands in level 12) and the first-to-last-voice span in whole seconds. Both cap at 32.
    CLIENT_LEVEL_STAGES = {'longest_pause': ('client.longest_pause_100ms', 100), 'speech_span': ('client.speech_span_s', 1000)}

    def record_client(self, stage: str, value_ms: int) -> None:
        """Browser-reported fixed-enum stage: a latency histogram, or a bounded level distribution for pause measurements."""
        level = self.CLIENT_LEVEL_STAGES.get(stage)
        if level:
            self.record_gauge(level[0], int(value_ms) // level[1])
        else:
            self.record('client.' + stage, value_ms)

    def record_client_trace(self, turn, requests, stages) -> None:
        """Log the browser's per-turn spans keyed by the random turn id. No-op unless tracing is on."""
        if turn and turntrace.enabled:
            turntrace.log_client(turn, [item.model_dump() for item in requests], stages)

    def record_route(self, name: str, queries: int, checkouts: int, transactions: int) -> None:
        """Database work of one request, keyed by route template (never a concrete path). Bounded; overflow joins 'other'."""
        key = name if STAGE_NAME.match(name) else 'other'
        with self._lock:
            row = self._routes.get(key)
            if row is None:
                if len(self._routes) >= MAX_ROUTES:
                    key = 'other'
                row = self._routes.setdefault(key, [0, 0, 0, 0, 0, 0, 0])
            row[0] += 1
            row[1] += queries; row[2] = max(row[2], queries)
            row[3] += checkouts; row[4] = max(row[4], checkouts)
            row[5] += transactions; row[6] = max(row[6], transactions)

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
            gauges = {name: {str(i): n for i, n in enumerate(levels) if n} for name, levels in sorted(self._gauges.items())}
            routes = {name: {'calls': r[0], 'queries_mean': round(r[1] / r[0], 1), 'queries_max': r[2],
                             'checkouts_mean': round(r[3] / r[0], 1), 'checkouts_max': r[4],
                             'transactions_mean': round(r[5] / r[0], 1), 'transactions_max': r[6]}
                      for name, r in sorted(self._routes.items()) if r[0]}
            if reset:
                self._stages, self._depth, self._gauges, self._routes = {}, [0] * (MAX_DEPTH + 1), {}, {}
        return {'version': 1, 'buckets_ms': list(BUCKETS_MS), 'stages': stages, 'gauges': gauges, 'routes': routes,
                'lock_queue_depth_at_arrival': depth}


aggregator = LatencyAggregator()
_logger = logging.getLogger('uvicorn.error')


MAX_LINE_STAGES = 24   # ~4-5 KB per line: well below any log pipeline's line limit
_windows = itertools.count(1)


def emit_summary() -> None:
    """Log the window as small, self-describing lines: {window, part, parts, stages, ...}; part 1 carries the rest."""
    snapshot = aggregator.snapshot(reset=True)
    stages = snapshot.pop('stages')
    routes = snapshot.pop('routes')
    if not stages and not routes and not snapshot['lock_queue_depth_at_arrival'] and not snapshot['gauges']:
        return
    names, route_names = sorted(stages), sorted(routes)
    chunks = [('stages', names[i:i + MAX_LINE_STAGES]) for i in range(0, len(names), MAX_LINE_STAGES)] or [('stages', [])]
    chunks += [('routes', route_names[i:i + MAX_LINE_STAGES]) for i in range(0, len(route_names), MAX_LINE_STAGES)]
    window = next(_windows)
    for index, (kind, chunk) in enumerate(chunks, 1):
        # Every line keeps a 'stages' object (empty on route lines) so existing readers need no change.
        part = {'window': window, 'part': index, 'parts': len(chunks),
                'stages': {name: stages[name] for name in chunk} if kind == 'stages' else {}}
        if kind == 'routes':
            part['routes'] = {name: routes[name] for name in chunk}
        if index == 1:
            part.update(snapshot)
        _logger.info('Latency summary %s', json.dumps(part, sort_keys=True, separators=(',', ':')))


LAG_INTERVAL_SECONDS = 0.1


async def loop_lag_loop(interval: float = LAG_INTERVAL_SECONDS) -> None:
    """Record how late a timer fires: time the event loop spent blocked (synchronous database calls, CPU work).

    The stage 'loop.lag' holds the lateness in ms of every wake-up; its max and upper-bound quantiles show stalls.
    Numeric only; ten wake-ups per second."""
    loop = asyncio.get_running_loop()
    while True:
        expected = loop.time() + interval
        await asyncio.sleep(interval)
        lag = max(0.0, (loop.time() - expected) * 1000)
        aggregator.record('loop.lag', lag)
        turntrace.report_stall(lag)


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
