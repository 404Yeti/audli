#!/usr/bin/env python3
"""AUD-85: turn the 'Turn trace' and 'Loop stall' log lines (Render logs, or any text with those lines) into per-turn timelines.

Usage:  python scripts/analyze_turn_trace.py render-logs.txt [--json]

For each turn id it prints the browser's view (when each request began, how long it waited for its token, time to first
byte) next to the server's view (when the handler started, lease wait, event-loop time blocked by synchronous database
calls, provider time), and the time between them that neither side can attribute (network, proxy, an event loop that
was busy before the server timer started). Browser and server clocks differ, so the offset is chosen so the least-delayed
request of the turn has no delay: delays are relative, never absolute.
"""
import argparse
import json
import re
import sys
from collections import defaultdict

LINE = re.compile(r'(Turn trace|Loop stall) (\{.*\})')
PROVIDER_PREFIXES = ('OpenAI.', 'turn.transcription', 'turn.tts_provider', 'lesson.tts_provider', 'SpeechService.', 'ComprehensionEvaluator.')


def parse(lines):
    server, client, stalls = defaultdict(list), {}, []
    for text in lines:
        match = LINE.search(text)
        if not match:
            continue
        try:
            record = json.loads(match.group(2))
        except ValueError:
            continue
        if match.group(1) == 'Loop stall':
            stalls.append(record)
        elif record.get('kind') == 'client':
            client[record['turn']] = record
        elif record.get('kind') == 'server':
            server[record['turn']].append(record)
    return server, client, stalls


def span_ms(record, prefixes):
    return sum(dur for name, _, dur in record.get('spans', []) if name.startswith(prefixes))


def analyse_turn(turn, requests, browser, stalls):
    requests = sorted(requests, key=lambda r: r['start_ms'])
    rows = []
    for record in requests:
        rows.append({
            'route': record['route'], 'start_ms': record['start_ms'], 'end_ms': record['end_ms'], 'total_ms': record['total_ms'],
            'status': record['status'], 'lease_wait_ms': span_ms(record, ('auth.lease',)), 'lease_attempts': record.get('lease_attempts', 0),
            'waited_on': record.get('waited_on'), 'loop_blocked_ms': record['loop_blocked_ms'],
            'verify_ms': span_ms(record, ('auth.verify',)), 'scope_ms': span_ms(record, ('auth.scope',)),
            'provider_ms': span_ms(record, PROVIDER_PREFIXES), 'db': record.get('db', {})})
    kinds = {'upload': re.compile(r'/(?:exercises|lessons)/\{[^}]+\}/attempts$'), 'assess': re.compile(r'/assess$'),
             'ack': re.compile(r'/acknowledgment-audio$'), 'coach': re.compile(r'/coach-audio$')}
    if browser:
        used = set()
        offset_samples = []
        for span in browser['requests']:
            row = next((r for i, r in enumerate(rows) if i not in used and kinds[span['kind']].search(r['route'])), None)
            if row is None:
                continue
            used.add(rows.index(row))
            row['browser'] = span
            offset_samples.append(row['start_ms'] - (span['start'] + span['token']))      # server start - browser send, offset by clock
        if offset_samples:
            base = min(offset_samples)
            for row in rows:
                span = row.get('browser')
                if span:
                    row['pre_server_delay_ms'] = row['start_ms'] - (span['start'] + span['token']) - base
                    row['ttfb_minus_server_ms'] = span['ttfb'] - row['total_ms']
    first, last = (requests[0]['start_ms'], requests[-1]['end_ms']) if requests else (0, 0)
    overlapping = [s for s in stalls if s['end_ms'] >= first and s['end_ms'] - s['lag_ms'] <= last]
    return {'turn': turn, 'requests': rows, 'span_ms': last - first,
            'stalls': [{'lag_ms': s['lag_ms'], 'explained_ms': s['explained_ms'], 'by_kind': s['by_kind'], 'by_turn': s['by_turn']} for s in overlapping],
            'loop_blocked_ms': sum(r['loop_blocked_ms'] for r in rows), 'lease_wait_ms': sum(r['lease_wait_ms'] for r in rows)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('path', nargs='?')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    handle = open(args.path, encoding='utf-8', errors='replace') if args.path else sys.stdin
    server, client, stalls = parse(handle)
    turns = [analyse_turn(turn, requests, client.get(turn), stalls) for turn, requests in sorted(server.items(), key=lambda item: item[1][0]['start_ms'])]
    if args.json:
        print(json.dumps(turns, indent=1))
        return 0
    for turn in turns:
        print(f"turn {turn['turn']}: server span {turn['span_ms']} ms, lease wait {turn['lease_wait_ms']} ms, loop blocked by own sync work {turn['loop_blocked_ms']} ms")
        for r in turn['requests']:
            extra = ''
            if 'pre_server_delay_ms' in r:
                b = r['browser']
                extra = f" | browser: token {b['token']} ms, pre-server delay {r['pre_server_delay_ms']} ms, ttfb-server {r['ttfb_minus_server_ms']} ms"
            waited = f" waited on {r['waited_on']['route']} (turn {r['waited_on']['turn']})" if r['waited_on'] else ''
            print(f"  {r['route']:<58} total {r['total_ms']:>6} lease {r['lease_wait_ms']:>6} ({r['lease_attempts']} tries){waited} "
                  f"provider {r['provider_ms']:>5} blocked {r['loop_blocked_ms']:>5}{extra}")
        for stall in turn['stalls']:
            print(f"  loop stall {stall['lag_ms']} ms, explained {stall['explained_ms']} ms by {stall['by_kind']}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
