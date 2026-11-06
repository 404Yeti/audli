"""AUD-116: applying the owner's voice-review decisions. Only an explicit "approve" approves; unsure/undecided/rejected change nothing or only the rejected list. No provider is called."""
import shutil
from pathlib import Path

import pytest

from app import accent_catalog as cat
from scripts import apply_voice_review as review

ROOT = Path(__file__).resolve().parents[1]
COPY = """Irish: approve (Darragh (male) BOt7zZh6gzfWlIUYnyPz)
Canadian: reject (Jeff (male) bE3COCGjWQ0g9MBhv2cf)
Scottish: unsure (Isla Skye (female) TVmbglAk3F1GkiCoOq47)
Indian: no decision (Vishesh (male) eH0983ZXSi0hJkuHbC3i)
New Zealand: approve (Becca (female) XcScRctIXQsuUPQQwzsz)
Chinese: reject (Lee's voice (male) mBoVD3461U2BagYEwjeo)
Japanese: approve 1MPZGta71qtdTm2BpYUl
Filipino: no decision (JC (male) 84RXm9MY2lATjoJi5K8P)
Nigerian: no decision (Stella (female) 2vbhUP8zyKg4dEZaTWGn)
Southern US: no decision (Dr. Kendall (female) BVA1oNX6xZt6o7QaUwxr)
New York: no decision (Alise (female) DZ2ULBbuhpX6zJYCQic4)
Boston: no decision (Shelly (female) 6B43gMdWDCu6YLdbVZFc)
"""


@pytest.fixture
def catalog_copy(tmp_path):
    path = tmp_path / 'accent_catalog.py'
    shutil.copy(ROOT / 'app/accent_catalog.py', path)
    return path


def run(catalog_copy, text, *flags, tmp_path=None):
    decisions = catalog_copy.parent / 'decisions.txt'
    decisions.write_text(text)
    return review.main([str(decisions), '--catalog', str(catalog_copy), *flags])


def test_the_page_output_parses_and_only_explicit_approvals_and_rejections_become_changes():
    changes = review.plan(review.parse(COPY), cat)
    assert changes == [{'accent': 'irish', 'action': 'approve', 'voice': 'BOt7zZh6gzfWlIUYnyPz'},
                       {'accent': 'canadian', 'action': 'reject', 'voice': 'bE3COCGjWQ0g9MBhv2cf'},
                       {'accent': 'new_zealand', 'action': 'approve', 'voice': 'XcScRctIXQsuUPQQwzsz'},
                       {'accent': 'chinese', 'action': 'reject', 'voice': 'mBoVD3461U2BagYEwjeo'},
                       {'accent': 'japanese', 'action': 'approve', 'voice': '1MPZGta71qtdTm2BpYUl'}]            # Japanese: an ALTERNATE named by id
    assert not [c for c in changes if c['accent'] in ('scottish', 'indian', 'filipino', 'nigerian', 'southern', 'new_york', 'boston')]    # unsure / no decision: untouched


def test_successful_synthesis_or_silence_never_approves_anything():
    assert review.plan(review.parse('\n'.join(f'{review.NAMES[a]}: no decision' for a in review.NAMES)), cat) == []
    assert review.plan(review.parse(''), cat) == []
    assert review.plan(review.parse('Irish: unsure\nBoston: unsure'), cat) == []


def test_a_dry_run_changes_nothing_and_says_so(catalog_copy, capsys):
    before = catalog_copy.read_text()
    assert run(catalog_copy, COPY) == 0
    assert catalog_copy.read_text() == before
    out = capsys.readouterr().out
    assert 'APPROVE' in out and 'Dry run: nothing was changed.' in out


def test_write_applies_exactly_the_decisions_and_the_edited_catalog_validates(catalog_copy):
    before = catalog_copy.read_text()
    assert run(catalog_copy, COPY, '--write') == 0
    edited = review.load_catalog(catalog_copy)
    assert edited.approved_second_voices() == {'irish': 'BOt7zZh6gzfWlIUYnyPz', 'new_zealand': 'XcScRctIXQsuUPQQwzsz', 'japanese': '1MPZGta71qtdTm2BpYUl'}
    assert edited.LISTENING_CANDIDATES['canadian']['rejected'] == ['bE3COCGjWQ0g9MBhv2cf'] and edited.LISTENING_CANDIDATES['chinese']['rejected'] == ['mBoVD3461U2BagYEwjeo']
    assert edited.approved_pairs()['irish'] == ('DbwWo4rVEd5NrejHYUnm', 'BOt7zZh6gzfWlIUYnyPz')            # speaker 1 is still the owner's 1.x voice
    for accent in ('scottish', 'indian', 'filipino', 'nigerian', 'southern', 'new_york', 'boston'):
        assert edited.LISTENING_CANDIDATES[accent]['approved'] is None
    changed = [l for l in catalog_copy.read_text().splitlines() if l not in before.splitlines()]
    assert len(changed) == 5 and all("'first':" in l for l in changed)                                  # only five header lines differ; every voice id and label is byte-identical


def test_applying_twice_is_idempotent_and_partial_rounds_accumulate(catalog_copy):
    run(catalog_copy, 'Irish: approve', '--write')
    first = catalog_copy.read_text()
    run(catalog_copy, 'Irish: approve', '--write')
    assert catalog_copy.read_text() == first
    run(catalog_copy, 'Boston: approve 6B43gMdWDCu6YLdbVZFc', '--write')                              # a later round adds one accent without touching the first
    assert review.load_catalog(catalog_copy).approved_second_voices() == {'irish': 'BOt7zZh6gzfWlIUYnyPz', 'boston': '6B43gMdWDCu6YLdbVZFc'}


def test_rejecting_a_candidate_moves_the_proposal_to_the_next_listed_one_and_it_can_never_be_approved(catalog_copy):
    run(catalog_copy, "Japanese: reject 69Na567Zr0bPvmBYuGdc", '--write')
    edited = review.load_catalog(catalog_copy)
    assert edited.LISTENING_CANDIDATES['japanese']['rejected'] == ['69Na567Zr0bPvmBYuGdc']
    assert run(catalog_copy, 'Japanese: approve 69Na567Zr0bPvmBYuGdc') == 2                          # cannot approve a rejected voice
    assert run(catalog_copy, 'Japanese: approve') == 0                                                # a bare approve now means the next listed candidate
    plan = review.plan(review.parse('Japanese: approve'), edited)
    assert plan == [{'accent': 'japanese', 'action': 'approve', 'voice': '1MPZGta71qtdTm2BpYUl'}]


@pytest.mark.parametrize('text,fragment', [
    ('Atlantis: approve', 'unknown accent'),
    ('Irish: maybe', 'cannot read'),
    ('Irish: approve\nIrish: reject', 'appears twice'),
    ('Irish: approve ZZZZZZZZZZZZZZZZZZZZ', 'not a listed candidate'),
    ('Irish: approve BOt7zZh6gzfWlIUYnyPz fNG1TqnY9mWgIFIExmZA', 'more than one'),
    ('Irish: approve DbwWo4rVEd5NrejHYUnm', 'not a listed candidate'),
])
def test_ambiguous_or_invalid_decisions_change_nothing(catalog_copy, capsys, text, fragment):
    before = catalog_copy.read_text()
    assert run(catalog_copy, text, '--write') == 2
    assert fragment in capsys.readouterr().out and catalog_copy.read_text() == before


def test_the_real_catalog_still_has_nothing_approved():
    assert cat.approved_second_voices() == {} and all(row['rejected'] == [] for row in cat.LISTENING_CANDIDATES.values())
