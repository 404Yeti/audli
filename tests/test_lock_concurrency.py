"""Characterization of the process-wide ``asyncio.Lock`` (AUD-68 Phase 0 finding).

These assertions document CURRENT behavior: provider-bound work for different learners is serialized.
When per-learner locking lands (Phase 4), invert ``test_global_lock_serializes_two_learners``.
"""
import asyncio

from lock_concurrency import measure
from test_persistence import repository_factory  # noqa: F401  (fixture)


def test_global_lock_serializes_two_learners(repository_factory):
    result = asyncio.run(measure(repository_factory(None), delay=0.3))
    # Two simultaneous uploads take about two provider delays, not one.
    assert result['serialization_ratio'] >= 1.7, result
    # A cheap lock-taking route for learner B waits behind learner A's STT...
    assert result['b_locked_listened_s'] >= 0.15, result
    # ...while a route that does not take the lock is unaffected.
    assert result['b_unlocked_profile_read_s'] < 0.15, result
    assert result['turn_upload_lock_wait']['max_ms'] >= 200, result
    # Depth counts the holder: B arrived behind A (>=1), and sole requests saw 0.
    assert any(int(depth) >= 1 for depth in result['lock_queue_depth_at_arrival']), result
