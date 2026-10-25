#!/usr/bin/env python3
"""AUD-89 P0: what does the upload trace middleware cost?

Drives the ASGI middleware directly with a body delivered in 64 KB chunks and a handler that consumes it, with tracing off
(the wrapper returns immediately) and on, and reports the added time per request and per MB. Also the largest buffer the
wrapper itself ever holds (it holds none: it only counts the messages that pass through).

    python scripts/bench_upload_trace.py [--mb 1] [--reps 300]
Synthetic data only.
"""
import argparse
import asyncio
import statistics
import sys
import tracemalloc
from pathlib import Path
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import turntrace  # noqa: E402
from app.uploadtrace import UploadTrace  # noqa: E402

CHUNK = 64 * 1024


async def handler(scope, receive, send):
    while True:
        message = await receive()
        if not message.get('more_body', False):
            break
    await send({'type': 'http.response.start', 'status': 200, 'headers': []})
    await send({'type': 'http.response.body', 'body': b'{}'})


async def one(app, chunks):
    queue = list(chunks)

    async def receive():
        return queue.pop(0)

    async def send(message):
        pass
    scope = {'type': 'http', 'method': 'POST', 'path': '/api/exercises/00000000-0000-4000-8000-000000000000/attempts',
             'headers': [(b'content-length', str(sum(len(c['body']) for c in chunks)).encode()), (b'x-audli-upload-id', b'0123456789abcdef')]}
    await app(scope, receive, send)


async def measure(app, chunks, reps):
    samples = []
    for _ in range(reps):
        started = perf_counter()
        await one(app, chunks)
        samples.append((perf_counter() - started) * 1e6)
    return samples


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mb', type=float, default=1.0)
    parser.add_argument('--reps', type=int, default=300)
    args = parser.parse_args()
    total = int(args.mb * 1024 * 1024)
    pieces = [b'x' * CHUNK] * (total // CHUNK) + ([b'x' * (total % CHUNK)] if total % CHUNK else [])
    chunks = [{'type': 'http.request', 'body': body, 'more_body': index < len(pieces) - 1} for index, body in enumerate(pieces)]
    import logging
    logging.getLogger('uvicorn.error').setLevel(logging.WARNING)       # the on-state log line is measured separately below
    results = {}
    for label, enabled, logged in (('bare handler', False, False), ('tracing off', False, True), ('tracing on', True, True)):
        turntrace.enabled = enabled
        app = handler if label == 'bare handler' else UploadTrace(handler)
        if logged and enabled:
            logging.getLogger('uvicorn.error').setLevel(logging.INFO)
            logging.getLogger('uvicorn.error').handlers = [logging.NullHandler()]
        asyncio.run(measure(app, chunks, 20))                            # warm up
        results[label] = statistics.median(asyncio.run(measure(app, chunks, args.reps)))
    tracemalloc.start()
    turntrace.enabled = True
    asyncio.run(one(UploadTrace(handler), chunks))
    peak = tracemalloc.get_traced_memory()[1]
    base = results['bare handler']
    print(f'body {args.mb:g} MB in {len(chunks)} chunks, median of {args.reps} requests')
    for label, value in results.items():
        print(f'  {label:14s} {value:9.1f} us   (+{value - base:7.1f} us vs bare handler)')
    print(f'  peak extra memory while tracing: {peak / 1024:.1f} KB for a {args.mb:g} MB body')


if __name__ == '__main__':
    main()
