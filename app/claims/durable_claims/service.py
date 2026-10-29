"""Request-bound mocked-STT processor. No durable audio and no background job queue."""
import asyncio
from time import monotonic




async def database_call(function, *args):
    """Wait for a thread transaction to settle on cancellation; never release ownership early.

    A two-second cancellation wait is bounded. If outcome remains unknown the operation
    stays fenced and the retry reads it; the worker thread is NOT assumed cancelled.
    """
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        deadline = monotonic() + 2
        while not task.done() and monotonic() < deadline:
            try:
                await asyncio.wait({task}, timeout=max(0, deadline - monotonic()))
            except asyncio.CancelledError:
                continue
        # Keep a strong reference until completion and retrieve exceptions.
        if task.done():
            if not task.cancelled():
                task.exception()
        else:
            pending.add(task)
            task.add_done_callback(completed)
        raise


pending = set()


def completed(task):
    pending.discard(task)
    if not task.cancelled():
        task.exception()
