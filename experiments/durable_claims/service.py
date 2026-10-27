"""Request-bound mocked-STT processor. No durable audio and no background job queue."""
import asyncio
from hashlib import sha256
from time import monotonic

from .store import Store


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


async def process(store: Store, exercise, upload_key, data, filename, provider):
    claim = await database_call(store.claim, exercise, upload_key, sha256(data).hexdigest())
    if claim.kind != 'owned':
        return claim.kind, claim.result
    try:
        transcription = await provider.transcribe(data, filename)
    except BaseException:
        # Provider coroutine has unwound; this cannot prove remote billing stopped.
        await database_call(store.abandon, claim)
        raise
    result = await database_call(store.finalize, claim, exercise, transcription)
    return 'complete', result
