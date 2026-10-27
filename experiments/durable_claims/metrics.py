"""Numeric-only local instrumentation; never record statements or parameters."""
from contextvars import ContextVar
from threading import Lock
from time import perf_counter
from sqlalchemy import event

request_key = ContextVar('poc_request', default=None)
recording_key = ContextVar('poc_recording', default=None)


class Meter:
    def __init__(self, engines):
        self.lock = Lock()
        self.physical = self.held = 0
        self.by_request = {}
        for engine in engines:
            event.listen(engine, 'connect', self.connect)
            event.listen(engine, 'close', self.close)
            event.listen(engine, 'checkout', self.checkout)
            event.listen(engine, 'checkin', self.checkin)
            event.listen(engine, 'before_cursor_execute', self.query)
            queue = getattr(engine.pool, '_pool', None)
            if queue is not None:
                original = queue.get
                def get(block=True, timeout=None, original=original):
                    started = perf_counter()
                    try:
                        return original(block, timeout)
                    finally:
                        elapsed = perf_counter() - started
                        if block and elapsed > .001:
                            with self.lock: self.waits.append(elapsed * 1000)
                queue.get = get
        self.reset()

    def reset(self):
        with self.lock:
            assert self.held == 0
            self.peak_physical = self.physical
            self.peak_held = self.queries = self.connections = self.checkouts = 0
            self.connection_seconds = 0
            self.waits = []
            self.stt_held = []

    def connect(self, *args):
        with self.lock:
            self.physical += 1; self.connections += 1
            self.peak_physical = max(self.peak_physical, self.physical)

    def close(self, *args):
        with self.lock: self.physical -= 1

    def checkout(self, connection, record, proxy):
        with self.lock:
            self.held += 1; self.checkouts += 1
            self.peak_held = max(self.peak_held, self.held)
            key = request_key.get()
            record.info['poc_meter'] = (perf_counter(), key)
            self.by_request[key] = self.by_request.get(key, 0) + 1

    def checkin(self, connection, record):
        with self.lock:
            started, key = record.info.pop('poc_meter')
            self.held -= 1
            self.by_request[key] -= 1
            self.connection_seconds += perf_counter() - started

    def query(self, *args):
        with self.lock: self.queries += 1

    def stt(self):
        with self.lock:
            held = self.by_request.get(request_key.get(), 0)
            self.stt_held.append(held)
            return held

    def summary(self):
        from bench_turns import describe
        with self.lock:
            return dict(connection_seconds=round(self.connection_seconds,3), peak_physical_sessions=self.peak_physical,
                peak_checked_out=self.peak_held, queries=self.queries, new_connections=self.connections,
                checkouts=self.checkouts, pool_wait_count=len(self.waits), pool_wait_ms=describe(self.waits),
                max_request_connections_at_stt=max(self.stt_held,default=0), outstanding=self.held)


class RequestContext:
    def __init__(self, app): self.app = app
    async def __call__(self, scope, receive, send):
        token = request_key.set(object())
        # Synthetic benchmark identifiers stay in memory and never enter result files.
        headers = dict(scope.get('headers', []))
        recording = recording_key.set((headers.get(b'x-audli-upload-id'), headers.get(b'authorization'), scope.get('path')))
        try: await self.app(scope, receive, send)
        finally:
            recording_key.reset(recording)
            request_key.reset(token)
