"""AUD-84: TCP proxy that adds a fixed one-way delay in each direction, so a local PostgreSQL behaves like a distant one.

    python scripts/pg_delay_proxy.py --listen 55433 --target 55432 --one-way-ms 36    # 72 ms round trip

Delays are applied per chunk with ordering and pipelining preserved. Measurement tooling only: never point it at production.
"""
import argparse
import asyncio
import time


async def pump(reader, writer, delay):
    queue: asyncio.Queue = asyncio.Queue()

    async def send():
        while True:
            due, chunk = await queue.get()
            if chunk is None:
                break
            await asyncio.sleep(max(0.0, due - time.monotonic()))
            writer.write(chunk)
            await writer.drain()
        writer.close()
    sender = asyncio.create_task(send())
    try:
        while chunk := await reader.read(65536):
            queue.put_nowait((time.monotonic() + delay, chunk))
    finally:
        queue.put_nowait((0, None))
        await sender


async def serve(listen, target, delay):
    async def handle(reader, writer):
        try:
            upstream_reader, upstream_writer = await asyncio.open_connection('127.0.0.1', target)
        except OSError:
            writer.close()
            return
        await asyncio.gather(pump(reader, upstream_writer, delay), pump(upstream_reader, writer, delay), return_exceptions=True)
    server = await asyncio.start_server(handle, '127.0.0.1', listen)
    async with server:
        await server.serve_forever()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--listen', type=int, default=55433)
    parser.add_argument('--target', type=int, default=55432)
    parser.add_argument('--one-way-ms', type=float, default=36.0)
    args = parser.parse_args()
    asyncio.run(serve(args.listen, args.target, args.one_way_ms / 1000))
