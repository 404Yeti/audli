"""Build natural lesson audio (one voice per speaker, every sentence synthesised and measured separately).

    .venv/bin/python -m scripts.build_listening_audio --lesson all --provider fake --out build/listening-audio          # offline rehearsal, free
    ELEVENLABS_API_KEY=... .venv/bin/python -m scripts.build_listening_audio --lesson all --provider elevenlabs \\
        --approved-chars 2500 --voice sam=<id> --voice maria=<id> --voice lena=<id> --voice tom=<id> --out build/listening-audio

The ElevenLabs path is a PAID provider: it refuses to run without an explicit --approved-chars cap (the characters you approved), counts every
character before each request, and stops at the cap. Nothing is published here; seed the result with
    AUDLI_LISTENING_ENABLED=true .venv/bin/python -m scripts.seed_listening_lesson --audio-root build/listening-audio --revision 2
which publishes it as a new immutable version beside the old one.
"""
import argparse
import json
import os
import sys
from pathlib import Path

from app.listening.audio_build import AudioQualityError, build_audio
from app.listening.seed import FIXTURES, LESSONS
from app.listening.domain import LessonContent
from app.listening.speech import ElevenLabsSpeech, FakeSpeech, SpeechBudget, SpeechBudgetExceeded, SpeechPermanent, SpeechTemporary


def chars_needed(names) -> int:
    return sum(len(s.text) for n in names for s in LessonContent.model_validate_json((FIXTURES / n / 'lesson.json').read_text()).sentences)


def main(argv=None, client=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--lesson', default='all', help='restaurant_b1, pharmacy_a2 or all')
    parser.add_argument('--provider', choices=('fake', 'elevenlabs'), default='fake')
    parser.add_argument('--voice', action='append', default=[], metavar='SPEAKER=VOICE', help='repeat per speaker (voice ids only for elevenlabs)')
    parser.add_argument('--approved-chars', type=int, help='REQUIRED for elevenlabs: the number of characters the owner approved')
    parser.add_argument('--model', default='eleven_multilingual_v2')
    parser.add_argument('--out', type=Path, default=Path('build/listening-audio'))
    args = parser.parse_args(argv)
    names = LESSONS if args.lesson == 'all' else (args.lesson,)
    if any(n not in LESSONS for n in names):
        raise SystemExit(f'Unknown lesson. Choose from: {", ".join(LESSONS)} or all')
    voices = dict(v.split('=', 1) for v in args.voice)
    if args.provider == 'elevenlabs':
        key = os.environ.get('ELEVENLABS_API_KEY', '')
        if not key:
            raise SystemExit('Refusing to run: ELEVENLABS_API_KEY is not set in the environment.')
        if not args.approved_chars:
            raise SystemExit('Refusing to run: --approved-chars N is required for a paid provider.')
        need = chars_needed(names)
        if need > args.approved_chars:
            raise SystemExit(f'Refusing to run: these lessons need {need} characters but only {args.approved_chars} are approved.')
        provider = ElevenLabsSpeech(key, args.model, SpeechBudget(args.approved_chars), client=client)
    else:
        provider = FakeSpeech()
        voices = {**{'sam': 'fake-a', 'maria': 'fake-b', 'lena': 'fake-c', 'tom': 'fake-d'}, **voices}
    for name in names:
        content = LessonContent.model_validate_json((FIXTURES / name / 'lesson.json').read_text())
        try:
            built = build_audio(content, provider, voices)
        except (AudioQualityError, SpeechBudgetExceeded, SpeechPermanent, SpeechTemporary, ValueError) as error:
            print(f'{name}: NOT built: {type(error).__name__}: {error}', file=sys.stderr)
            return 1
        target = args.out / name
        target.mkdir(parents=True, exist_ok=True)
        (target / 'audio.mp3').write_bytes(built.data)
        (target / 'audio.json').write_text(json.dumps(built.meta(provider.name), indent=2) + '\n')
        print(f'{name}: {len(content.sentences)} sentences, {built.duration_ms / 1000:.1f}s, {len(built.data) / 1024:.0f} KiB -> {target}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
