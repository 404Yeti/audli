"""AUD-25 local pilot only: metadata inspection then capped synthesis, no retries."""
import argparse
import asyncio
import csv
import hashlib
import json
import math
import os
import random
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import dotenv_values
from imageio_ffmpeg import get_ffmpeg_exe

from scripts.plan_voice_evaluation import ROOT, plan

MAX_REQUESTS = 22
MAX_CHARACTERS = 6024
MAX_ELEVEN_CREDITS = 10000
MAX_METADATA_REQUESTS = 50


def write_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def credentials(path):
    values = dict(dotenv_values(ROOT / '.env'))
    values.update(dotenv_values(path))
    values.update(os.environ)
    eleven = values.get('ELEVENLABS_API_KEY') or values.get('XI_API_KEY')
    coral = values.get('OPENAI_API_KEY')
    if not eleven or not coral:
        missing = 'ElevenLabs' if not eleven else 'OpenAI'
        raise ValueError(f'{missing} credential unavailable; no API calls made')
    return eleven, coral


def compact_voice(value):
    # Deliberate allowlist: never persist private samples, email allowlists or tokens.
    fields = ('voice_id', 'name', 'category', 'description', 'labels', 'preview_url',
        'high_quality_base_model_ids', 'available_for_tiers', 'is_legacy', 'verified_languages')
    result = {k: value.get(k) for k in fields}
    share = value.get('sharing') or value
    result['sharing'] = {k: share.get(k) for k in ('public_owner_id', 'original_voice_id',
        'status', 'notice_period', 'disable_at_unix', 'rate', 'live_moderation_enabled')}
    return result


def normalized(value):
    return re.sub(r'[^a-z0-9]', '', value.lower())


async def inspect(client, output, key):
    output.mkdir(parents=True, exist_ok=False)
    reads = 0

    async def get(path, params=None, allow_missing=False):
        nonlocal reads
        reads += 1
        if reads > MAX_METADATA_REQUESTS:
            raise ValueError('Metadata request cap reached')
        response = await client.get('https://api.elevenlabs.io' + path,
            headers={'xi-api-key': key}, params=params)
        if response.status_code != 200:
            detail = response.json().get('detail', {})
            if allow_missing and isinstance(detail, dict) and detail.get('status') == 'voice_not_found':
                return None
            message = str(detail).replace(key, '[REDACTED]')[:500]
            raise ValueError(f'Metadata HTTP {response.status_code} on {path}: {message}; no synthesis performed')
        return response.json()

    candidates = json.loads((ROOT / 'docs/voice-evaluation/candidates.json').read_text())
    resolved = []
    for candidate in candidates['tutors'][:3] + candidates['accents']:
        search = candidate['name'].split(' - ')[0].split(' | ')[0]
        local = await get('/v2/voices', {'search': search, 'page_size': 100, 'include_custom_rates': 'false'})
        choices = local.get('voices', [])
        match_name = candidate['name'] if 'target' in candidate else candidate['name'] + ' - ' + candidate['description']
        matches = [v for v in choices if normalized(v['name']) == normalized(match_name)]
        source = 'account'
        if not matches:
            shared = await get('/v1/shared-voices', {'search': search, 'include_custom_rates': 'false'})
            choices = shared.get('voices', [])
            matches = [v for v in choices if normalized(v['name']) == normalized(match_name)]
            source = 'shared'
        row = dict(candidate=candidate['name'], target=candidate.get('target'),
            status='resolved' if len(matches) == 1 else 'missing_or_ambiguous',
            search_source=source, matches=[compact_voice(v) for v in matches],
            alternatives=[compact_voice(v) for v in choices[:10]], authenticity='unverified')
        if len(matches) == 1:
            voice = await get('/v1/voices/' + matches[0]['voice_id'], allow_missing=source == 'shared')
            row['account_access'] = voice is not None
            voice = voice or matches[0]
            row['voice'] = compact_voice(voice)
            if normalized(voice['name']) != normalized(match_name):
                row['status'] = 'name_changed_or_mismatched'
            if (voice.get('sharing') or {}).get('disable_at_unix'):
                row['status'] = 'removal_scheduled'
        resolved.append(row)
        print(candidate['name'], row['status'], flush=True)
    subscription = await get('/v1/user/subscription')
    safe_subscription = {k: subscription.get(k) for k in
        ('tier', 'character_count', 'character_limit', 'status', 'max_credit_limit_extension')}
    models = await get('/v1/models')
    model_rows = [{k: m.get(k) for k in ('model_id', 'can_do_text_to_speech', 'token_cost_factor')}
        for m in models if m.get('model_id') in ('eleven_flash_v2_5', 'eleven_multilingual_v2')]
    write_json(output / 'discovery.json', dict(utc=datetime.now(timezone.utc).isoformat(),
        metadata_requests=reads, synthesis_requests=0, voices=resolved,
        subscription=safe_subscription, models=model_rows))


def checked_jobs(discovery):
    texts, jobs = plan('pilot')
    if discovery.get('custom_voice'):
        if not discovery.get('eddie_official_id_confirmed'):
            raise ValueError('Official Eddie ID not confirmed; no synthesis allowed')
        # Four matched tutor samples per voice; retain two resolved accent probes.
        tutor_jobs = [j for j in jobs if j['script'] != 'accent_passage']
        custom_jobs = []
        for template in [j for j in tutor_jobs if j['provider'] == 'openai']:
            custom_jobs.append(template | {'provider': 'elevenlabs', 'voice': 'Audli custom',
                'voice_id': None, 'model': 'eleven_flash_v2_5'})
        accent_jobs = [j for j in jobs if j.get('target') in ('Australian English', 'Irish English')]
        jobs = tutor_jobs + custom_jobs + accent_jobs
        for i, job in enumerate(jobs, 1):
            job['sample_id'] = f'S{i:03d}'
    resolved = {v['candidate']: v for v in discovery['voices']}
    if discovery.get('custom_voice'):
        resolved['Audli custom'] = discovery['custom_voice']
    if any(resolved[t].get('status') != 'resolved' for t in ('Talia', 'Eddie', 'Maisie')):
        raise ValueError('Exact tutor IDs are unresolved; no synthesis allowed')
    sub = discovery['subscription']
    for job in jobs:
        if job['provider'] == 'elevenlabs':
            candidate = resolved[job['voice']]
            if candidate['status'] != 'resolved':
                job['status'] = 'skipped_unresolved_voice'
                continue
            voice = candidate['voice']
            if (voice.get('sharing') or {}).get('rate') not in (None, 0, 1, 1.0):
                raise ValueError('Voice price multiplier exceeds reviewed base rate')
            if candidate.get('account_access') is False:
                raise ValueError('Voice is not saved and accessible in this account; no synthesis allowed')
            job['voice_id'] = voice['voice_id']
            supported = voice.get('high_quality_base_model_ids') or []
            if supported and job['model'] not in supported:
                if candidate['target'] is None:
                    raise ValueError('Tutor model compatibility not verified; no synthesis allowed')
                job['status'] = 'skipped_model_unverified'
            job['estimated_usd'] = job['characters'] / 1000 * (0.08 if job['model'] == 'eleven_multilingual_v2' else 0.04)
    active = [j for j in jobs if j['status'] == 'not_generated']
    characters = sum(j['characters'] for j in active)
    eleven_chars = sum(j['characters'] for j in active if j['provider'] == 'elevenlabs')
    if len(jobs) != 22 or len(active) > MAX_REQUESTS or characters > MAX_CHARACTERS:
        raise ValueError('Pilot request/character cap exceeded')
    estimated = sum(j.get('estimated_usd', 0) for j in active)
    budget = min(0.32232, float(os.environ.get('AUD25_MAX_ELEVEN_USD', '0.32232')))
    if not math.isfinite(budget) or budget < 0 or estimated > budget + 1e-9:
        raise ValueError('Pilot ElevenLabs base-rate estimate cap exceeded')
    for model in discovery.get('models', []):
        if model.get('token_cost_factor', 1) > 1:
            raise ValueError('Model price factor exceeds reviewed estimate')
    if sub['character_limit'] - sub['character_count'] < eleven_chars:
        raise ValueError('Insufficient included quota; refuse overage')
    ids = list(range(1, len(jobs) + 1))
    random.Random(252026).shuffle(ids)
    for job, number in zip(jobs, ids):
        job['blind_id'] = f'B{number:03d}'
    return texts, jobs, estimated


async def execute(client, output, eleven, coral):
    discovery = json.loads((output / 'discovery.json').read_text())
    texts, jobs, estimated = checked_jobs(discovery)
    # Refresh quota before spending; inspection can be stale. Never enable overages.
    subscription = await client.get('https://api.elevenlabs.io/v1/user/subscription',
        headers={'xi-api-key': eleven})
    if subscription.status_code != 200:
        raise ValueError('Cannot verify live included quota; no synthesis allowed')
    current = subscription.json()
    required = sum(j['characters'] for j in jobs if j['provider'] == 'elevenlabs' and j['status'] == 'not_generated')
    if current['character_limit'] - current['character_count'] < required:
        raise ValueError('Live included quota insufficient; no synthesis allowed')
    # Exclusive creation makes a second invocation impossible, even after failure.
    (output / 'started.lock').open('x').close()
    blind = output / 'blind'
    blind.mkdir()
    ledger = dict(utc=datetime.now(timezone.utc).isoformat(), max_requests=MAX_REQUESTS,
        max_input_characters=MAX_CHARACTERS, max_eleven_credits=MAX_ELEVEN_CREDITS,
        estimated_eleven_base_usd=estimated, attempted=0, reserved_characters=0,
        observed_eleven_character_cost=0, jobs=jobs, measurements=[])
    ledger['eleven_account_usage_before'] = current['character_count']
    ledger['account_tier'] = current.get('tier')
    ledger['commercial_rights'] = 'unverified; free-tier output is noncommercial research only'
    destination = output / 'results.json'
    write_json(destination, ledger)
    for job in jobs:
        if job['status'] != 'not_generated':
            continue
        if ledger['attempted'] >= MAX_REQUESTS or ledger['reserved_characters'] + job['characters'] > MAX_CHARACTERS:
            raise ValueError('Hard usage cap reached')
        if ledger['observed_eleven_character_cost'] + job['characters'] > MAX_ELEVEN_CREDITS:
            raise ValueError('ElevenLabs observed usage cap reached')
        ledger['attempted'] += 1
        ledger['reserved_characters'] += job['characters']
        job['status'] = 'attempt_reserved'
        write_json(destination, ledger)  # Reserve before network; ambiguous failures are never repeated.
        if job['provider'] == 'elevenlabs':
            url = 'https://api.elevenlabs.io/v1/text-to-speech/' + job['voice_id'] + '/stream'
            headers = {'xi-api-key': eleven}
            payload = dict(text=job['text'], model_id=job['model'],
                voice_settings=texts['elevenlabs_starting_settings'] | {'speed': job['speed']})
            params = {'output_format': 'mp3_44100_128'}
        else:
            url = 'https://api.openai.com/v1/audio/speech'
            headers = {'Authorization': 'Bearer ' + coral}
            payload = dict(model=job['model'], voice='coral', input=job['text'], speed=job['speed'],
                response_format='mp3', instructions=texts['coral_instructions'])
            params = None
        row = dict(sample_id=job['sample_id'], blind_id=job['blind_id'], voice=job['voice'],
            model=job['model'], rate=job['speed'], characters=job['characters'], retry_count=0,
            billed_usd=None, provider_token_usage=None)
        started = time.perf_counter()
        try:
            async with asyncio.timeout(90):
                async with client.stream('POST', url, headers=headers, json=payload, params=params) as response:
                    elapsed = lambda: (time.perf_counter() - started) * 1000
                    row['headers_ms'] = elapsed()
                    row['http_status'] = response.status_code
                    row['request_id'] = response.headers.get('request-id') or response.headers.get('x-request-id')
                    char_cost = response.headers.get('character-cost')
                    row['character_cost'] = float(char_cost) if char_cost is not None else None
                    if row['character_cost'] is not None:
                        if not math.isfinite(row['character_cost']) or row['character_cost'] < 0:
                            raise ValueError('Invalid provider usage header')
                        ledger['observed_eleven_character_cost'] += row['character_cost']
                    if response.status_code != 200:
                        raise ValueError('Provider rejected synthesis')
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        if chunk:
                            row.setdefault('first_audio_bytes_ms', elapsed())
                            raw.extend(chunk)
                        if len(raw) > 2 * 1024 * 1024:
                            raise ValueError('Audio byte cap exceeded')
                    row['complete_ms'] = elapsed()
            # Strip metadata; validate locally with bundled ffmpeg, no app imports.
            file = blind / (job['blind_id'] + '.mp3')
            process = subprocess.run([get_ffmpeg_exe(), '-v', 'error', '-nostdin', '-i', 'pipe:0',
                '-vn', '-map_metadata', '-1', '-codec:a', 'libmp3lame', '-id3v2_version', '0',
                '-write_id3v1', '0', str(file)], input=bytes(raw), capture_output=True, timeout=30)
            if process.returncode:
                raise ValueError('Audio decode validation failed')
            decoded = subprocess.run([get_ffmpeg_exe(), '-v', 'error', '-nostdin', '-i', str(file),
                '-f', 's16le', '-ac', '1', '-ar', '16000', 'pipe:1'], capture_output=True, timeout=30)
            if decoded.returncode or not decoded.stdout:
                raise ValueError('Empty or invalid audio')
            row.update(duration_s=len(decoded.stdout) / 32000, bytes=len(raw),
                audio_sha256=hashlib.sha256(file.read_bytes()).hexdigest(),
                validated_ready_ms=(time.perf_counter() - started) * 1000)
            job.update(status='generated', local_audio=str(file))
        except Exception as error:
            # Exception class only: do not leak provider response bodies or credentials.
            row['failure_kind'] = type(error).__name__
            job['status'] = 'failed_no_retry'
            ledger['measurements'].append(row)
            write_json(destination, ledger)
            raise ValueError(f"Stopped after {job['blind_id']}: {row['failure_kind']}; no retry") from None
        ledger['measurements'].append(row)
        write_json(destination, ledger)
        print(job['blind_id'], 'saved', flush=True)
    with (blind / 'ratings.csv').open('w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(['blind_id', 'rater', 'learner_level', 'warmth', 'naturalness', 'intelligibility',
            'pronunciation', 'expression', 'pace', 'consistency', 'accent_notes', 'notes'])
        writer.writerows([[j['blind_id']] for j in sorted(jobs, key=lambda j: j['blind_id']) if j['status'] == 'generated'])
    subscription = await client.get('https://api.elevenlabs.io/v1/user/subscription', headers={'xi-api-key': eleven})
    if subscription.status_code == 200:
        ledger['eleven_account_usage_after'] = subscription.json().get('character_count')
        if ledger['eleven_account_usage_after'] is not None:
            ledger['eleven_account_usage_delta'] = ledger['eleven_account_usage_after'] - ledger['eleven_account_usage_before']
    else:
        ledger['eleven_account_usage_after'] = None
    ledger['usage_note'] = 'Account delta may include concurrent activity or lag. Header usage is per call; OpenAI MP3 response does not establish billed tokens/dollars.'
    write_json(destination, ledger)
    print('Completed', ledger['attempted'], 'synthesis requests')


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['inspect', 'run'])
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--env-file', type=Path, default=ROOT / '.env')
    args = parser.parse_args()
    eleven, coral = credentials(args.env_file)
    async with httpx.AsyncClient(timeout=30, follow_redirects=False,
        transport=httpx.AsyncHTTPTransport(retries=0)) as client:
        if args.mode == 'inspect':
            await inspect(client, args.output, eleven)
        else:
            await execute(client, args.output, eleven, coral)


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except (ValueError, httpx.HTTPError, OSError) as error:
        # HTTP/OS errors may embed paths or request internals; redact them.
        print(str(error) if isinstance(error, ValueError) else type(error).__name__, file=sys.stderr)
        sys.exit(1)
