"""Run existing deterministic safety cases against packaged runtime primitives."""
from . import test_remediation as remediation
from experiments.assessment_claims import test_stage as stage
from experiments.assessment_claims import test_integration as integration
from app.claims.assessment_claims import store, bridge, lifecycle
from app.claims.assessment_claims.integration import install
from app.claims.release_readiness.account_erasure import erase
from app.claims.durable_claims.store import Conflict
import pytest

# Parameterizing the existing assertions avoids maintaining a weaker copied suite.
CASES=[(stage,name) for name in (
    'test_atomic_assessment_followup_final_adaptation_and_lesson_progress',
    'test_concurrent_duplicates_and_finalizers',
    'test_transaction_failure_preserves_pending_evidence',
    'test_concurrent_learners_and_account_isolation',
    'test_wrong_reset_revision_rolls_back_invalidation',
    'test_stale_expired_claim_cannot_commit',
    'test_cancel_during_provider_preserves_evidence_and_releases_claim',
    'test_cancel_during_final_transaction_keeps_ownership_until_settled',
    'test_expiry_invalidates_inflight_and_preserves_recording',
    'test_wrong_question_changed_input_and_bad_provider_evidence',
    'test_owner_reassignment_and_lock_inversion_fail_closed',
)]

CASES += [(integration,name) for name in ('test_bridge_fences_direct_engine_writes_and_expiration','test_retry_storm_and_concurrent_accounts_claim_once','test_full_http_checkin_duplicate_processing_status_and_no_provider_connections','test_full_http_closing_is_persisted_once','test_cancelled_legacy_provider_stays_quarantined_without_duplicate_stt','test_disabled_retry_policy_still_has_stable_native_upload_identity')]

@pytest.mark.parametrize('module,name',CASES,ids=[name for _,name in CASES])
def test_packaged_safety(module,name,request,monkeypatch):
    monkeypatch.setattr(stage,'StageStore',store.StageStore)
    monkeypatch.setattr(stage,'Conflict',Conflict)
    for symbol in ('BridgeStore','writer','install_transactions'):
        monkeypatch.setattr(integration,symbol,getattr(bridge,symbol))
    monkeypatch.setattr(integration,'Conflict',Conflict)
    import experiments.assessment_claims.integration as old
    monkeypatch.setattr(old,'install',install)
    function=getattr(module,name)
    arguments={key:request.getfixturevalue(key) for key in __import__('inspect').signature(function).parameters}
    function(**arguments)

# Import the fixture so its repository is initialized by the same harness.
fixture=stage.fixture


def test_packaged_planner_recovery(fixture,tmp_path,monkeypatch):
    monkeypatch.setattr(remediation,'BridgeStore',bridge.BridgeStore)
    import experiments.assessment_claims.integration as old
    monkeypatch.setattr(old,'install',install)
    remediation.test_planner_get_admitted_and_does_not_displace_active_or_expired_claim(fixture,tmp_path,monkeypatch)


@pytest.mark.parametrize('first',['bridge','claims'])
def test_packaged_bridge_overlap(fixture,tmp_path,monkeypatch,first):
    import experiments.assessment_claims.integration as old
    monkeypatch.setattr(old,'install',install)
    integration.test_guard_aware_bridge_and_claims_worker_share_one_upload_owner(fixture,tmp_path,monkeypatch,first)
