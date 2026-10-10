"""Verify spending guards with synthetic metadata and a non-network transport."""
import asyncio
import json

import httpx
import pytest

from scripts.run_voice_evaluation import checked_jobs, execute
from scripts.plan_voice_evaluation import ROOT


def discovery():
    candidates = json.loads((ROOT / 'docs/voice-evaluation/candidates.json').read_text())
    return {'subscription': {'tier': 'starter', 'character_limit': 30000, 'character_count': 0},
        'voices': [{'candidate': c['name'], 'status': 'resolved',
            'voice': {'voice_id': f'fixture-{i}', 'high_quality_base_model_ids': ['eleven_flash_v2_5', 'eleven_multilingual_v2']}}
            for i, c in enumerate(candidates['tutors'][:3] + candidates['accents'])]}


def test_pilot_limits_and_unresolved_tutor_fail_closed():
    data = discovery()
    _, jobs, estimate = checked_jobs(data)
    assert len(jobs) == 22
    assert sum(j['characters'] for j in jobs) == 6024
    assert estimate == pytest.approx(0.32232)
    data['voices'][0]['status'] = 'missing_or_ambiguous'
    with pytest.raises(ValueError, match='Exact tutor IDs'):
        checked_jobs(data)


def test_insufficient_quota_fails_closed():
    data = discovery()
    data['subscription']['character_limit'] = 1
    with pytest.raises(ValueError, match='Insufficient included quota'):
        checked_jobs(data)


def test_missing_accent_is_skipped_without_substitution():
    data = discovery()
    data['voices'][-1]['status'] = 'missing_or_ambiguous'
    _, jobs, _ = checked_jobs(data)
    assert jobs[-1]['status'] == 'skipped_unresolved_voice'
    assert jobs[-1]['voice_id'] is None


def test_custom_pilot_stays_inside_original_budget():
    data = discovery()
    data['custom_voice'] = {'candidate': 'Audli custom', 'status': 'resolved',
        'target': None, 'voice': {'voice_id': 'fixture-custom'}}
    data['eddie_official_id_confirmed'] = True
    _, jobs, estimate = checked_jobs(data)
    assert len(jobs) == 22
    assert sum(j['provider'] == 'elevenlabs' for j in jobs) == 18
    assert sum(j['characters'] for j in jobs) == 4934
    assert estimate == pytest.approx(0.2032)
    assert sum(j['voice'] == 'Audli custom' for j in jobs) == 4
    data['eddie_official_id_confirmed'] = False
    with pytest.raises(ValueError, match='Official Eddie ID'):
        checked_jobs(data)


def test_price_multiplier_and_lower_configured_budget_stop_spending(monkeypatch):
    data = discovery()
    data['voices'][0]['voice']['sharing'] = {'rate': 2}
    with pytest.raises(ValueError, match='price multiplier'):
        checked_jobs(data)
    data['voices'][0]['voice']['sharing']['rate'] = 1
    monkeypatch.setenv('AUD25_MAX_ELEVEN_USD', '0.10')
    with pytest.raises(ValueError, match='estimate cap'):
        checked_jobs(data)


def test_failed_call_is_reserved_and_rerun_never_synthesizes(tmp_path):
    (tmp_path / 'discovery.json').write_text(json.dumps(discovery()))
    requests = []

    def transport(request):
        requests.append(request.method)
        if request.method == 'GET':
            return httpx.Response(200, json=discovery()['subscription'])
        return httpx.Response(429, json={'detail': 'not logged'})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            with pytest.raises(ValueError, match='no retry'):
                await execute(client, tmp_path, 'fixture-eleven', 'fixture-openai')
            with pytest.raises(FileExistsError):
                await execute(client, tmp_path, 'fixture-eleven', 'fixture-openai')

    asyncio.run(run())
    ledger = json.loads((tmp_path / 'results.json').read_text())
    assert requests.count('POST') == 1
    assert ledger['attempted'] == 1
    assert ledger['reserved_characters'] == 193
    assert ledger['jobs'][0]['status'] == 'failed_no_retry'
    assert ledger['measurements'][0]['http_status'] == 429
    assert 'fixture-eleven' not in (tmp_path / 'results.json').read_text()
