"""Numeric-only local instrumentation; never record statements or parameters."""
from contextvars import ContextVar
from threading import Lock
from time import perf_counter
from sqlalchemy import event

request_key = ContextVar('poc_request', default=None)
recording_key = ContextVar('poc_recording', default=None)
stage = ContextVar('poc_stage', default='request')
client_index = ContextVar('poc_index', default=None)
active_meter = None


class Meter:
    def __init__(self, engines):
        self.lock = Lock()
        self.physical = self.held = 0
        self.by_request = {}
        for engine in engines:
            for method_name in ('do_commit','do_rollback'):
                original_method=getattr(engine.dialect,method_name)
                def transaction_call(connection, original=original_method, name=method_name):
                    started=perf_counter()
                    try:return original(connection)
                    finally:self.duration(name+'_ms',(perf_counter()-started)*1000)
                setattr(engine.dialect,method_name,transaction_call)
            event.listen(engine, 'connect', self.connect)
            event.listen(engine, 'close', self.close)
            event.listen(engine, 'checkout', self.checkout)
            event.listen(engine, 'checkin', self.checkin)
            event.listen(engine, 'before_cursor_execute', self.query)
            event.listen(engine, 'after_cursor_execute', self.query_end)
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
                            with self.lock:
                                self.waits.append(elapsed * 1000)
                                self.add('pool_wait_ms',elapsed*1000)
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
            self.inventory = {}
            self.clients = {}

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
            elapsed = perf_counter() - started
            self.connection_seconds += elapsed
            self.add('checkout_ms',elapsed*1000)

    def add(self, name, value):
        index = client_index.get()
        if index is not None:
            row = self.clients.setdefault(index,{})
            row[name] = row.get(name,0) + value

    def duration(self,name,value):
        with self.lock:self.add(name,value)

    def query(self, connection, cursor, statement, parameters, context, many):
        context._poc_started = perf_counter()
        context._poc_label = stage.get()
        with self.lock:
            self.queries += 1
            # SQL templates only. Never interpolate or export bound parameters.
            key=(context._poc_label,statement)
            row=self.inventory.setdefault(key,dict(stage=key[0],sql=statement,calls=0,cursor_ms=0))
            row['calls']+=1

    def query_end(self,connection,cursor,statement,parameters,context,many):
        elapsed=(perf_counter()-context._poc_started)*1000
        with self.lock:
            self.inventory[(context._poc_label,statement)]['cursor_ms']+=elapsed
            self.add('cursor_ms',elapsed)

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
                max_request_connections_at_stt=max(self.stt_held,default=0), outstanding=self.held,
                sql_inventory=list(self.inventory.values()),
                client_profile=[dict(index=i,**row) for i,row in sorted(self.clients.items())])


class RequestContext:
    def __init__(self, app): self.app = app; self.active = 0
    async def __call__(self, scope, receive, send):
        token = request_key.set(object())
        # Synthetic benchmark identifiers stay in memory and never enter result files.
        headers = dict(scope.get('headers', []))
        index = client_index.set(int(headers[b'x-poc-index']) if b'x-poc-index' in headers else None)
        self.active += 1
        recording = recording_key.set((headers.get(b'x-audli-upload-id'), headers.get(b'authorization'), scope.get('path')))
        try: await self.app(scope, receive, send)
        finally:
            self.active -= 1
            client_index.reset(index)
            recording_key.reset(recording)
            request_key.reset(token)
