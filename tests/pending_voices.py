"""Test helper: the catalog as it was before the owner's second-voice review. Tests of the mechanism run in that state; tests marked owner_approved run on the real catalog."""
import pytest

from app import accent_catalog as cat


def unapprove_all(monkeypatch):
    for row in cat.LISTENING_CANDIDATES.values():
        monkeypatch.setitem(row, 'approved', None)


@pytest.fixture(autouse=True)
def pending_unless_owner_approved(request, monkeypatch):
    if request.node.get_closest_marker('owner_approved'):
        return
    unapprove_all(monkeypatch)
