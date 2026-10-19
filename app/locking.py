"""Per-learner asyncio locks as a drop-in for the former process-wide ``asyncio.Lock`` (AUD-70).

Why this is safe: the lock only guards read-modify-write sequences that span ``await`` points
(provider calls) for ONE learner's rows. Cross-learner state is DB-atomic (accent budget uses row
locks) or immutable. Same-learner requests still queue FIFO; in production Postgres the auth
middleware additionally holds a cross-worker advisory lease per learner.

``enabled=False`` maps every request to one shared key, i.e. exactly the old global lock, so rollout
and rollback are a configuration flip.

The key must come from the server-verified request scope (never from a client value) and is supplied
by the caller. Entries are reference-counted and removed when idle, so the table cannot grow without
bound, including after cancellation.
"""
import asyncio
from collections.abc import Callable
from contextvars import ContextVar
from typing import Hashable

_GLOBAL = '__global__'


class _Entry:
    __slots__ = ('lock', 'users')

    def __init__(self):
        self.lock = asyncio.Lock()
        self.users = 0   # holder + waiters


class LearnerLocks:
    def __init__(self, enabled: bool, key: Callable[[], Hashable]):
        self._enabled = enabled
        self._key = key
        self._entries: dict[Hashable, _Entry] = {}
        # Keys this task currently holds, so release() needs no argument and ``async with lock`` keeps working.
        self._held: ContextVar[tuple[Hashable, ...]] = ContextVar(f'audli_held_locks_{id(self)}', default=())

    def _current_key(self) -> Hashable:
        return str(self._key()) if self._enabled else _GLOBAL

    def _discard(self, key: Hashable, entry: _Entry) -> None:
        if entry.users == 0 and not entry.lock.locked() and self._entries.get(key) is entry:
            del self._entries[key]

    async def acquire(self) -> bool:
        key = self._current_key()
        entry = self._entries.get(key)
        if entry is None:
            entry = self._entries[key] = _Entry()
        entry.users += 1           # registered before the await so a cancelled waiter is always unwound
        try:
            await entry.lock.acquire()
        except BaseException:
            entry.users -= 1
            self._discard(key, entry)
            raise
        self._held.set(self._held.get() + (key,))
        return True

    def release(self) -> None:
        held = self._held.get()
        if not held:
            raise RuntimeError('Lock released by a context that does not hold it.')
        key = held[-1]
        self._held.set(held[:-1])
        entry = self._entries[key]
        entry.lock.release()
        entry.users -= 1
        self._discard(key, entry)

    def locked(self) -> bool:
        entry = self._entries.get(self._current_key())
        return bool(entry and entry.lock.locked())

    def queue_depth(self) -> int:
        """Requests ahead of a new arrival for the same key (the holder plus its waiters)."""
        entry = self._entries.get(self._current_key())
        return entry.users if entry else 0

    def active_keys(self) -> int:
        return len(self._entries)

    async def __aenter__(self):
        await self.acquire()

    async def __aexit__(self, *exc):
        self.release()
