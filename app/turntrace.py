"""AUD-85: bounded, privacy-safe per-turn request tracing. Diagnostic only; off unless AUDLI_TURN_TRACE and
AUDLI_LATENCY_AGGREGATES are both on.

A browser-generated random 12-hex ``X-Audli-Turn`` id ties the requests of one spoken turn together. Each traced request
logs ONE ``Turn trace`` line of numbers, route templates and that id: no learner or account identifiers, tokens, SQL,
parameters, URLs with ids, text or audio. Everything is bounded (spans, blocks, ring buffer, log line size).

Besides timing spans it records which synchronous sections ran ON the event-loop thread (database calls, lease
connections): while one runs nothing else in the process makes progress, so these are the direct cause of loop stalls.
``report_stall`` attributes a measured stall to the sections that overlapped it.
"""
import asyncio
import json
import logging
import re
import time
from collections import deque
from contextvars import ContextVar
from time import perf_counter

TURN_ID = re.compile(r'^[0-9a-f]{12}$')
UUID_SEGMENT = re.compile(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}')
MAX_SPANS = 48
MAX_BLOCKS = 12
BLOCK_MIN_MS = 25.0        # shorter synchronous sections are not worth a record
STALL_MIN_MS = 250.0       # loop lateness worth explaining
RING = 512

enabled = False            # set by create_app; module state so the hot paths cost one boolean test when off
_logger = logging.getLogger('uvicorn.error')
request_trace: ContextVar['Trace | None'] = ContextVar('audli_request_trace', default=None)
# Recent synchronous loop sections from ANY request: (wall_end_ms, duration_ms, kind, turn id or None).
_ring: deque = deque(maxlen=RING)
# Account-lease holders in this process, keyed by the (unlogged) advisory-lock key: (turn id, route template, acquired perf).
lease_holders: dict[int, tuple[str | None, str, float]] = {}


def now_ms() -> int:
    return int(time.time() * 1000)


def template(path: str) -> str:
    """Route-shaped path for logs: concrete ids never appear."""
    return UUID_SEGMENT.sub('{id}', path)[:96]


def on_loop() -> bool:
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


class Trace:
    __slots__ = ('turn', 'started', 'started_wall', 'spans', 'blocks', 'blocked_ms', 'extra')

    def __init__(self, turn: str):
        self.turn = turn
        self.started = perf_counter()
        self.started_wall = now_ms()
        self.spans: list = []
        self.blocks: list = []
        self.blocked_ms = 0.0
        self.extra: dict = {}

    def offset(self, at: float) -> int:
        return int(round((at - self.started) * 1000))


def begin(headers) -> 'Trace | None':
    """Start a trace for a request that carries a valid turn id; anything else is ignored."""
    if not enabled:
        return None
    for key, value in headers:
        if key.lower() == b'x-audli-turn':
            try:
                turn = value.decode('ascii')
            except UnicodeError:
                return None
            return Trace(turn) if TURN_ID.match(turn) else None
    return None


def span(name: str, started: float, elapsed_ms: float) -> None:
    trace = request_trace.get()
    if trace is not None and len(trace.spans) < MAX_SPANS:
        trace.spans.append([name, trace.offset(started), int(round(elapsed_ms))])


def sync_section(kind: str, started: float, elapsed_ms: float) -> None:
    """A synchronous section that ran to completion on the calling thread. Only loop-thread sections block the loop."""
    if not enabled or elapsed_ms < BLOCK_MIN_MS or not on_loop():
        return
    trace = request_trace.get()
    _ring.append((now_ms(), elapsed_ms, kind, trace.turn if trace else None))
    if trace is not None:
        trace.blocked_ms += elapsed_ms
        trace.blocks.append([kind, trace.offset(started), int(round(elapsed_ms))])
        if len(trace.blocks) > 4 * MAX_BLOCKS:       # keep memory bounded; the longest are kept on output
            trace.blocks.sort(key=lambda block: -block[2]); del trace.blocks[2 * MAX_BLOCKS:]


def lease_acquired(key: int, trace: 'Trace | None', method: str, path: str) -> None:
    if enabled:
        lease_holders[key] = (trace.turn if trace else None, f'{method} {template(path)}', perf_counter())


def lease_released(key: int) -> None:
    lease_holders.pop(key, None)


def lease_contended(key: int, trace: 'Trace | None') -> None:
    """Remember the first request seen holding the lease while this one had to wait."""
    holder = lease_holders.get(key)
    if trace is not None and holder is not None and 'waited_on' not in trace.extra:
        turn, route, since = holder
        trace.extra['waited_on'] = {'turn': turn, 'route': route, 'held_ms_at_first_wait': int(round((perf_counter() - since) * 1000))}


def finish(trace: Trace, method: str, route: str | None, status: int, total_ms: float, accumulator: dict | None) -> None:
    blocks = sorted(trace.blocks, key=lambda block: -block[2])[:MAX_BLOCKS]
    record = {'v': 1, 'kind': 'server', 'turn': trace.turn, 'route': f'{method} {route or "unmatched"}', 'status': status,
              'start_ms': trace.started_wall, 'end_ms': now_ms(), 'total_ms': int(round(total_ms)),
              'loop_blocked_ms': int(round(trace.blocked_ms)), 'spans': trace.spans, 'loop_blocks': blocks, **trace.extra}
    if accumulator:
        record['db'] = {key: (int(round(value)) if isinstance(value, float) else value) for key, value in accumulator.items()}
    _logger.info('Turn trace %s', json.dumps(record, separators=(',', ':')))


def log_client(turn: str | None, requests: list, stages: dict, deliveries: list | None = None) -> None:
    """The browser's side of one turn: its own request spans (token wait, time to first byte) and stage timings.
    AUD-89: ``deliveries`` are the browser's per-send upload records (bounded, numeric/enum fields only)."""
    record = {'v': 1, 'kind': 'client', 'turn': turn, 'end_ms': now_ms(), 'requests': requests, 'stages': stages}
    if deliveries:
        record['deliveries'] = deliveries
    _logger.info('Turn trace %s', json.dumps(record, separators=(',', ':')))


def report_stall(lag_ms: float) -> None:
    """Explain one measured loop stall from the synchronous sections that overlapped it. Called by the lag sampler,
    which runs right after the stall ended."""
    if not enabled or lag_ms < STALL_MIN_MS:
        return
    end = now_ms()
    start = end - lag_ms
    by_kind: dict[str, float] = {}
    by_turn: dict[str, float] = {}
    for wall_end, duration, kind, turn in list(_ring):
        overlap = min(wall_end, end) - max(wall_end - duration, start)
        if overlap > 0:
            by_kind[kind] = by_kind.get(kind, 0.0) + overlap
            key = turn or '-'
            by_turn[key] = by_turn.get(key, 0.0) + overlap
    explained = sum(by_kind.values())
    _logger.info('Loop stall %s', json.dumps({
        'v': 1, 'end_ms': end, 'lag_ms': int(round(lag_ms)), 'explained_ms': int(round(min(explained, lag_ms))),
        'unexplained_ms': int(round(max(0.0, lag_ms - explained))),
        'by_kind': {k: int(round(v)) for k, v in sorted(by_kind.items(), key=lambda item: -item[1])[:8]},
        'by_turn': {k: int(round(v)) for k, v in sorted(by_turn.items(), key=lambda item: -item[1])[:5]}},
        separators=(',', ':')))


def reset() -> None:
    """Tests only."""
    _ring.clear()
    lease_holders.clear()
