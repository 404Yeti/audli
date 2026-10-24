"""AUD-87: process-level cache for the few fixed acknowledgment cues, so serving one needs no learner-scoped write.

Keyed by the tutor voice signature and the fixed cue text (never by learner), single-flight, bounded, in memory only.
A fallback-voice result is returned but never cached, exactly as the per-learner database cache refuses to store it.
"""
import asyncio

MAX_ENTRIES = 64


class AckCache:
    def __init__(self, limit: int = MAX_ENTRIES):
        self._limit = limit
        self._data: dict = {}
        self._flights: dict = {}

    async def get(self, key, factory):
        """Return ``(value, was_cached)``. ``factory()`` is awaited once for concurrent callers and returns ``(value, cacheable)``."""
        if key in self._data:
            return self._data[key], True
        flight = self._flights.get(key)
        if flight is None:
            flight = self._flights[key] = asyncio.ensure_future(self._produce(key, factory))
        return await asyncio.shield(flight), False      # a cancelled waiter must not cancel the shared generation

    async def _produce(self, key, factory):
        try:
            value, cacheable = await factory()
            if cacheable and len(self._data) < self._limit:
                self._data[key] = value
            return value
        finally:
            self._flights.pop(key, None)

    def __len__(self):
        return len(self._data)
