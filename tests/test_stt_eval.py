"""STT evaluation harness: metrics, manifest safety and a numbers-only report (plumbing, not accuracy)."""
import json
import wave
from pathlib import Path

import pytest

from scripts import evaluate_stt as ev

PROMPTS = Path(__file__).parent / 'fixtures/stt_eval/prompts.vi-en.json'


def silent_wav(path):
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000); w.writeframes(b'\0\0' * 800)


def manifest(tmp_path, samples):
    (tmp_path / 'audio').mkdir(exist_ok=True)
    for s in samples:
        if 'audio' in s and not s['audio'].startswith('..'):
            silent_wav(tmp_path / s['audio'])
    path = tmp_path / 'manifest.json'; path.write_text(json.dumps({'samples': samples})); return path


def sample(sid, prompt='R01', **extra):
    base = {'id': sid, 'prompt_id': prompt, 'audio': f'audio/{sid}.wav', 'speaker': 'spk-1', 'provenance': 'consented', 'consent_record': 'C-1'}
    return base | extra


def test_wer_basics_and_normalization():
    assert ev.wer('The team rolled back.', 'the TEAM rolled back') == 0
    assert ev.wer('a b c d', 'a x c') == pytest.approx(.5)      # one substitution + one deletion
    assert ev.wer('hello', '') == 1
    with pytest.raises(ValueError):
        ev.wer('', 'x')


def test_prompt_set_covers_required_categories_and_poor_grammar():
    prompts = json.loads(PROMPTS.read_text())['prompts']
    assert {p['category'] for p in prompts} == {'R', 'G', 'S', 'N'}
    assert len({p['id'] for p in prompts}) == len(prompts)
    assert sum(p['category'] == 'G' for p in prompts) >= 6        # AGENTS.md: poor grammar, correct meaning
    assert any(len(p['text'].split()) < 3 for p in prompts if p['category'] == 'S')   # exercises the <3-word rule


@pytest.mark.parametrize('bad, message', [
    (dict(provenance='production'), 'provenance'),
    (dict(consent_record=''), 'consent_record'),
    (dict(prompt_id='ZZ99'), 'prompt_id'),
    (dict(audio='../outside.wav'), 'inside the manifest'),
    (dict(audio='audio/x.exe'), 'supported file'),
])
def test_manifest_rejects_unsafe_samples(tmp_path, bad, message):
    path = manifest(tmp_path, [sample('s1') | bad])
    with pytest.raises(ValueError, match=message):
        ev.load_manifest(path, PROMPTS, 10_000_000)


def test_synthetic_requires_source_and_duplicates_rejected(tmp_path):
    with pytest.raises(ValueError, match='synthetic_source'):
        ev.load_manifest(manifest(tmp_path, [sample('s1', provenance='synthetic')]), PROMPTS, 10**7)
    with pytest.raises(ValueError, match='duplicate'):
        ev.load_manifest(manifest(tmp_path, [sample('s1'), sample('s1')]), PROMPTS, 10**7)


def test_dry_run_report_is_numbers_only_and_flags_plumbing(tmp_path, capsys):
    samples = [sample(f's{i}-{pid}', pid, speaker=f'spk-{i % 2}') for i, pid in enumerate(['R01', 'G01', 'S01', 'N01', 'R02', 'S02'])]
    path = manifest(tmp_path, samples)
    out = tmp_path / 'report.json'
    assert ev.main(['--manifest', str(path), '--provider', 'fake', '--out', str(out)]) == 0
    report = json.loads(out.read_text())
    assert report['plumbing_only'] is True and report['contains_transcripts'] is False
    blob = out.read_text().lower()
    assert 'rolled back' not in blob and 'hypothesis' not in blob       # no transcripts or reference text
    assert report['summary']['overall']['n'] == 6
    sweep = report['summary']['threshold_sweep']
    assert [s['pass_rate'] for s in sweep] == sorted((s['pass_rate'] for s in sweep), reverse=True)  # stricter gate never admits more
    assert set(report['summary']['by_category']) == {'R', 'G', 'S', 'N'}


def test_include_text_is_explicit_opt_in(tmp_path):
    path = manifest(tmp_path, [sample('s1-R01', 'R01')])
    out = tmp_path / 'r.json'
    ev.main(['--manifest', str(path), '--provider', 'fake', '--include-text', '--out', str(out)])
    assert json.loads(out.read_text())['samples'][0]['hypothesis']
