"""List the speech voices this account can use per accent and (with --synthesize) check each chosen voice against the generation speech model.

    ELEVENLABS_API_KEY=... python -m scripts.verify_generation_voices [--synthesize] [--ids american=ID1,ID2 british=...]

Reads the key from the environment (or ~/.config/audli/elevenlabs.env) and never prints it. --synthesize makes ONE short request per voice
(about 45 characters, a few credits each) and checks that a playable MP3 of a plausible length comes back.
"""
import argparse
import os
import sys
from pathlib import Path

import httpx

from app.listening.audio_build import decode
from app.listening.generation import ACCENTS, CREDIT_MULTIPLIER, DEFAULT_VOICES

LINE = 'Good morning, could I get a large coffee, please?'


def key() -> str:
    if os.environ.get('ELEVENLABS_API_KEY'):
        return os.environ['ELEVENLABS_API_KEY']
    path = Path.home() / '.config/audli/elevenlabs.env'
    for line in path.read_text().splitlines() if path.exists() else []:
        line = line.strip().removeprefix('export ')
        if line.startswith('ELEVENLABS_API_KEY='):
            return line.split('=', 1)[1].strip().strip('"\'')
    sys.exit('Set ELEVENLABS_API_KEY.')


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--synthesize', action='store_true')
    parser.add_argument('--model', default='eleven_flash_v2_5', choices=sorted(CREDIT_MULTIPLIER))
    parser.add_argument('--ids', nargs='*', default=[], metavar='ACCENT=ID1,ID2')
    args = parser.parse_args(argv)
    headers = {'xi-api-key': key()}
    voices = httpx.get('https://api.elevenlabs.io/v1/voices', headers=headers, timeout=30).json().get('voices', [])
    print(f'{len(voices)} voices on this account')
    for v in voices:
        labels = v.get('labels') or {}
        if labels.get('accent', '').lower() in ACCENTS and labels.get('language', 'en') in ('en', ''):
            print(f"  {labels['accent']:<10} {labels.get('gender', '?'):<7} {v['voice_id']}  {v['name']}  ({v.get('category', '?')})")
    chosen = {a: tuple(DEFAULT_VOICES[a]) for a in ACCENTS}
    for item in args.ids:
        accent, ids = item.split('=', 1)
        chosen[accent] = tuple(ids.split(','))
    if not args.synthesize:
        return 0
    bad = 0
    for accent, ids in chosen.items():
        for voice_id in ids:
            response = httpx.post(f'https://api.elevenlabs.io/v1/text-to-speech/{voice_id}', headers=headers, params={'output_format': 'mp3_44100_128'}, timeout=60,
                                  json={'text': LINE, 'model_id': args.model, 'voice_settings': {'stability': 0.5, 'similarity_boost': 0.75, 'style': 0.0, 'use_speaker_boost': True}})
            ok, seconds = response.status_code == 200, 0.0
            if ok:
                try:
                    seconds = len(decode(response.content)) / 44100
                    ok = 1.5 <= seconds <= 8
                except Exception:
                    ok = False
            bad += not ok
            print(f"{'OK ' if ok else 'BAD'} {accent:<10} {voice_id}  status={response.status_code} seconds={seconds:.1f} charge={response.headers.get('character-cost', '?')}")
    return 1 if bad else 0


if __name__ == '__main__':
    raise SystemExit(main())
