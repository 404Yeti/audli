"""AUD-92: worker-local, no-queue account-lease admission. No identity or audio stored."""
import asyncio
import logging
from collections.abc import Awaitable
from threading import Lock
from time import monotonic as _cleanup_time

CLEANUP_TIMEOUT_SECONDS = 2.0
MAX_JSON_ACK_BYTES = 64 * 1024


class CleanupUnconfirmed(TimeoutError):
    """Cleanup exceeded its response deadline; its permit remains quarantined."""



class LeaseAdmission:
    def __init__(self, enabled: bool, limit: int) -> None:
        self.enabled, self.limit = enabled, limit
        self._lock = Lock()
        self.in_use = 0
        self.quarantined = 0
        self.rejected = 0
        self.cleanup_tasks: set[asyncio.Future] = set()

    def try_acquire(self) -> "Permit | None":
        if not self.enabled:
            return None
        # No await, semaphore waiter, connection checkout or account lookup.
        with self._lock:
            if self.in_use >= self.limit:
                self.rejected += 1
                return None
            self.in_use += 1
        return Permit(self)

    def snapshot(self) -> dict[str, bool | int]:
        with self._lock:
            return {'enabled': self.enabled, 'limit': self.limit, 'in_use': self.in_use,
                    'quarantined': self.quarantined, 'rejected': self.rejected}


class Permit:
    def __init__(self, admission: LeaseAdmission) -> None:
        self.admission = admission
        self.failed = self.released = False

    def quarantine(self) -> None:
        # May run in the connection worker thread. Failed cleanup is not proof
        # that a DB socket/transaction was released: never recycle its slot.
        with self.admission._lock:
            if self.failed:
                return
            self.failed = True
            self.admission.quarantined += 1
        logging.getLogger('audli').error('lease_admission_cleanup_failed; permit quarantined')

    def release(self) -> None:
        with self.admission._lock:
            if self.released:
                return
            self.released = True
            if not self.failed:
                self.admission.in_use -= 1


async def finish_cleanup(awaitable: Awaitable[object], permit: Permit) -> None:
    """Bound the response wait, never the underlying close operation or its permit.

    Repeated cancellation does not reset the deadline. On expiry the worker keeps
    the quarantined reservation, and the tracked task drains/observes cleanup.
    """
    task = asyncio.ensure_future(awaitable)
    tasks = permit.admission.cleanup_tasks
    tasks.add(task)
    def observed(done):
        tasks.discard(done)
        if not done.cancelled():
            done.exception()  # retrieve late failures without logging private details
    task.add_done_callback(observed)
    deadline = _cleanup_time() + CLEANUP_TIMEOUT_SECONDS
    cancelled = False
    while not task.done():
        remaining = deadline - _cleanup_time()
        if remaining <= 0:
            permit.quarantine()
            if cancelled:
                raise asyncio.CancelledError
            raise CleanupUnconfirmed('Lease cleanup could not be confirmed.')
        try:
            await asyncio.wait((task,), timeout=remaining)  # does not cancel task on outer cancellation/expiry
        except asyncio.CancelledError:
            cancelled = True
    try:
        task.result()
    except BaseException:
        permit.quarantine()
        raise
    if cancelled:
        raise asyncio.CancelledError


async def close_admitted(connection, permit: Permit) -> None:
    # Even with general DB offload disabled, a synchronous close must not freeze
    # the event loop and prevent the acknowledgment deadline from firing.
    await finish_cleanup(asyncio.get_running_loop().run_in_executor(None, connection.close), permit)


async def take_admitted(function, key, permit: Permit):
    """Acquire in a thread; cancellation drains acquisition AND close with a bound.

    An acquisition still in flight after cancellation may return a connection
    later. The tracked discard task closes that resource without entering a route.
    """
    task = asyncio.create_task(asyncio.to_thread(function, key, permit))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        async def discard():
            connection, _ = await asyncio.shield(task)
            await asyncio.to_thread(connection.close)
        try:
            await finish_cleanup(discard(), permit)
        finally:
            raise asyncio.CancelledError


class JSONAcknowledgment:
    """Defer only a small, single-body, declared-length successful JSON response.

    Streaming/oversized/undeclared/trailer responses pass through, preserving their
    original lifetime and backpressure. No audio is copied or retained here.
    """
    def __init__(self, send):
        self.send = send
        self.start = self.body = None
        self.length = None

    async def _send(self, message):
        await self.send(message)

    async def __call__(self, message):
        if message['type'] == 'http.response.start':
            raw_headers = list(message.get('headers', ()))
            message = {**message, 'headers': raw_headers}
            headers = {key.lower(): value for key, value in raw_headers}
            length = headers.get(b'content-length', b'')
            if (200 <= message['status'] < 300 and not message.get('trailers')
                    and headers.get(b'content-type', b'').split(b';')[0].strip().lower() == b'application/json'
                    and len(length) <= 8 and length.isdigit() and int(length) <= MAX_JSON_ACK_BYTES):
                self.start = message
                self.length = int(length)
                return
        elif self.start is not None and message['type'] == 'http.response.body':
            if (not message.get('more_body', False) and self.body is None
                    and len(message.get('body', b'')) == self.length):
                self.body = dict(message)  # freeze the envelope; retain immutable JSON bytes
                return
        # An unexpected stream/extension is not converted into a buffered response.
        await self.flush()
        await self._send(message)

    async def flush(self):
        if self.start is not None:
            start, body = self.start, self.body
            self.start = self.body = None
            await self._send(start)
            if body is not None:
                await self._send(body)

    async def release_to_client(self):
        if self.start is not None and self.body is None:
            raise RuntimeError('Incomplete JSON acknowledgment.')
        await self.flush()
