"""The natural lesson-audio pipeline: per-sentence synthesis with measured transcript cues, quality gates, the ElevenLabs request shape and its hard
character budget (mocked HTTP, no paid call), publishing the result as a new immutable version, and playback of that version through the real API."""
import hashlib
import json
import struct
import wave
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select

from app.config import Settings
from app.listening import schema as l, store
from app.listening.audio_build import AudioQualityError, RATE, build_audio, decode, encode, trim
from app.listening.domain import LessonContent, validate_alignment
from app.listening.objects import LocalObjectStore
from app.listening.seed import FIXTURES, LESSONS, load_fixture, seed
from app.listening.speech import (ElevenLabsSpeech, FakeSpeech, SpeechBudget, SpeechBudgetExceeded, SpeechPermanent, SpeechTemporary)
from scripts import build_listening_audio as cli
from test_listening import make_repository  # noqa: F401  (fixture)
from test_listening_journey import begin, journey  # noqa: F401  (fixtures)

VOICES = {'sam': 'v-sam', 'maria': 'v-maria', 'lena': 'v-lena', 'tom': 'v-tom'}


def lesson(name):
    return LessonContent.model_validate_json((FIXTURES / name / 'lesson.json').read_text())


@pytest.mark.parametrize('name', LESSONS)
def test_every_lesson_builds_with_measured_in_order_cues_that_match_the_audio(name):
    content, provider = lesson(name), FakeSpeech()
    built = build_audio(content, provider, VOICES)
    assert [c.index for c in built.cues] == list(range(len(content.sentences))) and built.sha256 == hashlib.sha256(built.data).hexdigest()
    validate_alignment(built.cues, len(content.sentences), built.duration_ms)
    heard_ms = len(decode(built.data)) * 1000 // RATE
    assert abs(heard_ms - built.duration_ms) <= 150                                           # the cues were measured on the timeline that plays
    for before, after in zip(built.cues, built.cues[1:]):
        assert after.start_ms - before.end_ms >= 300                                          # a real pause between speakers
    assert [text for text, _ in provider.calls] == [s.text for s in content.sentences]         # one request per sentence, in order
    assert [voice for _, voice in provider.calls] == [VOICES[s.speaker] for s in content.sentences]
    assert not built.data.startswith(b'ID3')                                                   # no tags: nothing can leak the script


def test_the_cue_for_each_sentence_lands_on_its_own_sound():
    content = lesson('pharmacy_a2')
    built = build_audio(content, FakeSpeech(), VOICES)
    pcm = decode(built.data)
    for cue in built.cues:
        window = pcm[cue.start_ms * RATE // 1000 + RATE // 20: cue.end_ms * RATE // 1000 - RATE // 20]
        assert max(abs(s) for s in window) > 3000                                              # speech is there between start and end
    gap = pcm[built.cues[0].end_ms * RATE // 1000 + RATE // 20: built.cues[1].start_ms * RATE // 1000 - RATE // 20]
    assert max(abs(s) for s in gap) < 600                                                      # and silence between sentences


class Fixed:
    name = 'fixed'

    def __init__(self, make):
        self.make = make

    def synthesize(self, text, voice):
        return self.make(text)


def wav_mp3(seconds, amplitude=9000):
    pcm = [int(amplitude * (1 if (i // 30) % 2 else -1)) for i in range(int(RATE * seconds))]
    return encode(pcm)


@pytest.mark.parametrize('make,message', [
    (lambda text: wav_mp3(1.0, 0), 'silent'),
    (lambda text: wav_mp3(1.0, 300), 'silent'),
    (lambda text: wav_mp3(0.2), 'implausible length'),
    (lambda text: wav_mp3(30), 'implausible length'),
    (lambda text: b'this is not audio at all' * 40, 'could not be decoded')])
def test_quality_gates_reject_bad_clips_before_anything_is_published(make, message):
    with pytest.raises(AudioQualityError, match=message):
        build_audio(lesson('restaurant_b1'), Fixed(make), VOICES)


def test_a_missing_voice_is_refused_and_edge_silence_is_trimmed():
    with pytest.raises(ValueError, match='No voice chosen for: maria'):
        build_audio(lesson('restaurant_b1'), FakeSpeech(), {'sam': 'a'})
    padded = [0] * (RATE * 2) + [8000] * RATE + [0] * (RATE * 2)
    assert len(trim(padded)) < RATE * 1.5 and max(trim(padded)) == 8000


# ---- ElevenLabs request shape and the hard budget (mocked HTTP) --------------------------------------------------------------------------

def eleven(handler, chars=10_000, **kw):
    return ElevenLabsSpeech('xi-test-key-not-real', budget=SpeechBudget(chars), client=httpx.Client(transport=httpx.MockTransport(handler)), **kw)


def test_the_request_matches_the_shape_1x_already_uses_in_production():
    seen = []
    mp3 = FakeSpeech().synthesize('hello there my friend', 'v')

    def handler(request):
        seen.append((request.url.path, dict(request.url.params), request.headers['xi-api-key'], json.loads(request.content)))
        return httpx.Response(200, content=mp3, headers={'character-cost': '21'})
    provider = eleven(handler)
    assert provider.synthesize('hello there my friend', 'voiceABC') == mp3
    path, params, key, body = seen[0]
    assert path == '/v1/text-to-speech/voiceABC' and params == {'output_format': 'mp3_44100_128'} and key == 'xi-test-key-not-real'
    assert body['text'] == 'hello there my friend' and body['model_id'] == 'eleven_multilingual_v2' and set(body['voice_settings']) >= {'stability', 'similarity_boost'}
    assert provider.last_character_cost == 21 and provider.budget.used == 21


@pytest.mark.parametrize('status,error', [(429, SpeechTemporary), (500, SpeechTemporary), (503, SpeechTemporary), (408, SpeechTemporary),
                                          (401, SpeechPermanent), (403, SpeechPermanent), (404, SpeechPermanent), (422, SpeechPermanent)])
def test_provider_errors_are_classified(status, error):
    with pytest.raises(error):
        eleven(lambda r: httpx.Response(status)).synthesize('hello there', 'v')


def test_network_failures_and_garbage_are_handled():
    def down(request):
        raise httpx.ConnectTimeout('slow')
    with pytest.raises(SpeechTemporary):
        eleven(down).synthesize('hello there', 'v')
    with pytest.raises(SpeechPermanent, match='not an MP3'):
        eleven(lambda r: httpx.Response(200, content=b'<html>error page</html>' * 30)).synthesize('hello there', 'v')
    for bad in ('', '   ', 'x' * 601):
        with pytest.raises(ValueError):
            eleven(lambda r: httpx.Response(200)).synthesize(bad, 'v')


def test_the_character_budget_is_enforced_before_a_request_is_sent():
    sent = []
    mp3 = FakeSpeech().synthesize('hello there my friend', 'v')
    provider = eleven(lambda r: (sent.append(1), httpx.Response(200, content=mp3))[1], chars=30)
    provider.synthesize('twenty characters!!', 'v')                                            # 19 characters
    with pytest.raises(SpeechBudgetExceeded):
        provider.synthesize('this would pass the cap', 'v')
    assert len(sent) == 1 and provider.budget.used == 19
    with pytest.raises(ValueError):
        SpeechBudget(0)
    with pytest.raises(ValueError, match='speech budget is required'):
        ElevenLabsSpeech('key')
    with pytest.raises(ValueError):
        ElevenLabsSpeech('', budget=SpeechBudget(10))


# ---- the build command --------------------------------------------------------------------------------------------------------------------

def test_the_paid_path_refuses_without_a_key_an_approval_and_enough_characters(tmp_path, monkeypatch):
    base = ['--lesson', 'all', '--provider', 'elevenlabs', '--out', str(tmp_path), '--voice', 'sam=a', '--voice', 'maria=b', '--voice', 'lena=c', '--voice', 'tom=d']
    monkeypatch.delenv('ELEVENLABS_API_KEY', raising=False)
    with pytest.raises(SystemExit, match='ELEVENLABS_API_KEY'):
        cli.main(base)
    monkeypatch.setenv('ELEVENLABS_API_KEY', 'xi-test-key-not-real')
    with pytest.raises(SystemExit, match='--approved-chars'):
        cli.main(base)
    need = cli.chars_needed(LESSONS)
    with pytest.raises(SystemExit, match=f'need {need} characters but only {need - 1}'):
        cli.main(base + ['--approved-chars', str(need - 1)])
    assert list(tmp_path.iterdir()) == []
    with pytest.raises(SystemExit, match='Unknown lesson'):
        cli.main(['--lesson', 'nope'])


def test_a_full_build_with_a_mocked_paid_provider_writes_both_lessons_and_spends_exactly_the_script(tmp_path, monkeypatch):
    monkeypatch.setenv('ELEVENLABS_API_KEY', 'xi-test-key-not-real')
    fake, requests = FakeSpeech(), []

    def handler(request):
        body = json.loads(request.content)
        requests.append(len(body['text']))
        return httpx.Response(200, content=fake.synthesize(body['text'], request.url.path))
    need = cli.chars_needed(LESSONS)
    code = cli.main(['--lesson', 'all', '--provider', 'elevenlabs', '--approved-chars', str(need), '--out', str(tmp_path), '--voice', 'sam=a', '--voice', 'maria=b',
                     '--voice', 'lena=c', '--voice', 'tom=d'], client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert code == 0 and sum(requests) == need and len(requests) == 16 + 14
    for name in LESSONS:
        meta = json.loads((tmp_path / name / 'audio.json').read_text())
        data = (tmp_path / name / 'audio.mp3').read_bytes()
        assert meta['provider'] == 'elevenlabs' and hashlib.sha256(data).hexdigest() == meta['sha256'] and meta['size_bytes'] == len(data)


def test_a_build_that_fails_a_quality_gate_writes_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, 'FakeSpeech', lambda: Fixed(lambda text: wav_mp3(0.2)))
    assert cli.main(['--lesson', 'restaurant_b1', '--provider', 'fake', '--out', str(tmp_path)]) == 1
    assert 'NOT built' in capsys.readouterr().err and not any(tmp_path.rglob('*.mp3'))


# ---- publishing and playback ----------------------------------------------------------------------------------------------------------------

def built_dir(tmp_path, name='pharmacy_a2'):
    built = build_audio(lesson(name), FakeSpeech(), VOICES)
    folder = tmp_path / 'built' / name
    folder.mkdir(parents=True)
    (folder / 'audio.mp3').write_bytes(built.data)
    (folder / 'audio.json').write_text(json.dumps(built.meta('elevenlabs')))
    return tmp_path / 'built', built


def test_new_audio_is_a_new_immutable_version_old_attempts_keep_theirs_and_new_ones_play_it(journey):
    j = journey
    lesson_id = j.lesson_id                                                                   # the restaurant lesson (espeak audio, version 1)
    old = begin(j.client, lesson_id)
    root, built = built_dir(j.tmp, 'restaurant_b1')
    lessons_store = LocalObjectStore(j.tmp / 'listening_objects', b'k' * 32)
    version_2, created = seed(j.engine, lessons_store, 'restaurant_b1', audio_dir=root / 'restaurant_b1', revision=2)
    assert created and version_2 != old['lesson']['version_id']
    assert seed(j.engine, lessons_store, 'restaurant_b1', audio_dir=root / 'restaurant_b1', revision=2) == (version_2, False)      # idempotent
    still_old = j.client.get(f"/api/listening/attempts/{old['attempt']['id']}").json()
    assert still_old['lesson']['version_id'] == old['lesson']['version_id'] and still_old['audio']['duration_ms'] == old['audio']['duration_ms']
    # finish the old attempt so a new one can start, then the next attempt gets the natural audio
    with j.engine.begin() as db:
        from sqlalchemy import update
        db.execute(update(l.attempts).where(l.attempts.c.id == old['attempt']['id']).values(status='completed', correct=0, completed_at=l.attempts.c.created_at))
    fresh = begin(j.client, lesson_id)
    assert fresh['lesson']['version_id'] == version_2 and fresh['lesson']['version_no'] == 2
    assert fresh['audio']['duration_ms'] == built.duration_ms and fresh['audio']['cues'] == [c.model_dump() for c in built.cues]    # transcript sync data
    signed = j.client.get(f"/api/listening/attempts/{fresh['attempt']['id']}/audio").json()
    media = j.client.get(signed['url'])
    assert media.status_code == 200 and hashlib.sha256(media.content).hexdigest() == built.sha256 and signed['duration_ms'] == built.duration_ms
    ranged = j.client.get(signed['url'], headers={'Range': 'bytes=0-99'})
    assert ranged.status_code == 206 and len(ranged.content) == 100
    with j.engine.connect() as db:
        providers = {r.provider for r in db.execute(select(l.audio.c.provider)).all()}
    assert providers == {'fixture', 'elevenlabs'}


def test_a_revision_changes_the_version_and_must_be_at_least_two():
    base, _, _ = load_fixture('pharmacy_a2')
    revised, _, _ = load_fixture('pharmacy_a2', revision=2)
    assert base.content_hash() != revised.content_hash() and load_fixture('pharmacy_a2', revision=2)[0].content_hash() == revised.content_hash()
    for bad in (0, 1, 1001):
        with pytest.raises(Exception):
            load_fixture('pharmacy_a2', revision=bad)


def test_development_audio_can_never_be_published_in_production(journey, tmp_path):
    j = journey
    lessons_store = LocalObjectStore(tmp_path / 'prod_objects', b'k' * 32)
    with pytest.raises(ValueError, match='fixture or fake'):
        seed(j.engine, lessons_store, 'pharmacy_a2', production=True)
    root, built = built_dir(tmp_path)
    (root / 'pharmacy_a2' / 'audio.json').write_text(json.dumps(built.meta('fake')))
    with pytest.raises(ValueError, match='fixture or fake'):
        seed(j.engine, lessons_store, 'pharmacy_a2', audio_dir=root / 'pharmacy_a2', revision=2, production=True)
    (root / 'pharmacy_a2' / 'audio.json').write_text(json.dumps(built.meta('elevenlabs')))
    assert seed(j.engine, lessons_store, 'pharmacy_a2', audio_dir=root / 'pharmacy_a2', revision=2, production=True)[1] is True      # natural audio is allowed
