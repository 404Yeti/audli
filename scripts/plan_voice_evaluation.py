"""Offline AUD-25 experiment planner. No network, credentials or app imports."""
import argparse
import csv
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def plan(stage):
    texts = json.loads((ROOT / 'docs/voice-evaluation/scripts.json').read_text())
    candidates = json.loads((ROOT / 'docs/voice-evaluation/candidates.json').read_text())
    tutors = candidates['tutors'][:3] if stage == 'pilot' else candidates['tutors']
    scripts = ['welcome', 'mistake'] if stage == 'pilot' else list(texts['tutor'])
    jobs = []
    for script in scripts:
        for rate in texts['rates']:
            # Interleave providers/candidates; do not run all of one provider first.
            for candidate in [{'name': 'coral', 'voice_id': 'coral'}] + tutors:
                provider = 'openai' if candidate['name'] == 'coral' else 'elevenlabs'
                jobs.append(dict(provider=provider, voice=candidate['name'], voice_id=candidate['voice_id'],
                    model='gpt-4o-mini-tts' if provider == 'openai' else 'eleven_flash_v2_5',
                    script=script, speed=rate, text=texts['tutor'][script]))
    for candidate in candidates['accents']:
        jobs.append(dict(provider='elevenlabs', voice=candidate['name'], voice_id=candidate['voice_id'],
            model='eleven_multilingual_v2', script='accent_passage', target=candidate['target'],
            speed=1.0, text=texts['accent_passage']))
    for index, job in enumerate(jobs, 1):
        job.update(sample_id=f'S{index:03d}', characters=len(job['text']),
            text_sha256=hashlib.sha256(job['text'].encode()).hexdigest(),
            sample_path=f'audio/S{index:03d}.mp3', status='not_generated')
    return texts, jobs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', choices=['pilot', 'full'], default='pilot')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--max-characters', type=int, default=10000)
    args = parser.parse_args()
    texts, jobs = plan(args.stage)
    characters = sum(j['characters'] for j in jobs)
    if args.max_characters <= 0 or characters > args.max_characters:
        parser.error(f'{characters} characters exceeds the positive cap; explicitly choose a reviewed larger cap')
    # Refuse overwrites; preserve completed ratings and prior manifests.
    args.output.mkdir(parents=True, exist_ok=False)
    eleven_estimate = sum(j['characters'] / 1000 * (0.08 if j['model'] == 'eleven_multilingual_v2' else 0.04)
        for j in jobs if j['provider'] == 'elevenlabs')
    manifest = dict(version=texts['version'], stage=args.stage, requests=len(jobs), characters=characters,
        network_requests_performed=0, estimated_elevenlabs_usd=round(eleven_estimate, 6),
        estimate_note='2026-10-09 API list rates; excludes tax, subscription minimum and voice surcharges; OpenAI not included',
        settings=texts['elevenlabs_starting_settings'], coral_instructions=texts['coral_instructions'], jobs=jobs)
    (args.output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    columns = ['sample_id', 'rater_id', 'learner_level', 'device', 'warmth', 'naturalness', 'intelligibility',
        'pronunciation', 'expression', 'pace', 'consistency', 'claimed_accent_matches', 'regional_notes',
        'insertions_omissions', 'notes']
    with (args.output / 'ratings.csv').open('w', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        writer.writerows({'sample_id': j['sample_id']} for j in jobs)
    metrics = ['sample_id', 'trial', 'utc', 'region', 'model', 'voice_id', 'rate', 'characters', 'connection_state',
        'headers_ms', 'first_audio_bytes_ms', 'complete_ms', 'validated_ready_ms', 'browser_playing_ms',
        'duration_s', 'bytes', 'http_status', 'retry_count', 'request_id', 'billed_usd', 'audio_sha256', 'failure_kind']
    with (args.output / 'measurements.csv').open('w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(metrics)
    print(json.dumps({k: manifest[k] for k in ('stage', 'requests', 'characters', 'network_requests_performed', 'estimated_elevenlabs_usd')}))


if __name__ == '__main__':
    main()
