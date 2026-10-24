"""AUD-87: run a synchronous database section off the event loop (AUDLI_DB_OFFLOAD, default off).

The repository is synchronous and every call crosses a ~72 ms link, so a handler's database sections (tens of round trips)
freeze the whole process while they run: measured loop stalls of 3-4.5 s, health checks unanswered for 8 s, other requests delayed
even though they are independent. With the flag on, those sections run in a worker thread and the coroutine awaits them, so the
loop keeps serving other requests. The request's context (verified learner scope, per-request accumulators) is copied into the
thread; sections stay strictly sequential within a request, so ordering, locks and transactions are exactly as before.
With the flag off ``run`` calls the function inline: identical to the previous code path.
"""
import asyncio


class Offload:
    def __init__(self, enabled: bool = False):
        self.enabled = bool(enabled)

    async def run(self, function, *args):
        if not self.enabled:
            return function(*args)
        return await asyncio.to_thread(function, *args)

    async def release(self, connection):
        """Close a held lease connection (one rollback round trip) even if the request is cancelled meanwhile."""
        if not self.enabled:
            return connection.close()
        future = asyncio.get_running_loop().run_in_executor(None, connection.close)
        await asyncio.shield(future)       # cancellation of this request must not skip the close: the thread still runs it
