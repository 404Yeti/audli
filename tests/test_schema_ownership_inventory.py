"""Every table must be consciously classified for account deletion (AUD-34 / AUD-77 audit).

Account deletion lists tables explicitly. A table added without a deletion decision either leaves learner data behind or,
if it references `users`, makes the whole deletion transaction fail on its foreign key. This test fails until a new table
is classified here; when AUD-34 lands, its erasure list must equal LEARNER_OWNED (plus `users` itself).
"""
from app.storage import schema as s

# Rows that belong to one learner and MUST be erased with the account.
LEARNER_OWNED = {
    'users', 'learner_profiles', 'training_sessions', 'exercises', 'attempts', 'evaluation_results', 'adaptation_events',
    'conversations', 'turn_assessments', 'conversation_followups', 'conversation_turns', 'assessment_evidence', 'coach_audio',
    'audio_assets', 'lesson_lifecycles', 'lesson_exercises', 'learner_roadmaps', 'homework_assignments', 'homework_reflections',
    'lesson_entitlements', 'review_improvements',
}
# Aggregate, shared or operational rows: no learner key, deliberately retained (budgets, shared catalog, migration ledger).
NOT_LEARNER_OWNED = {'schema_migrations', 'accent_control', 'accent_reservations', 'homework_discovery_budget', 'listening_content'}
# Child tables with no user_id column: erased through their parent chain (child first), so they need explicit statements.
OWNED_VIA_PARENT = {'attempts', 'evaluation_results', 'adaptation_events', 'conversations', 'turn_assessments', 'conversation_followups',
                    'conversation_turns', 'assessment_evidence', 'coach_audio', 'homework_reflections'}


def reaches_users(table, seen=frozenset()):
    if table.name == 'users':
        return True
    if 'user_id' in table.columns:
        return True
    return any(reaches_users(fk.column.table, seen | {table.name}) for fk in table.foreign_keys if fk.column.table.name not in seen)


def test_every_table_is_classified_for_account_deletion():
    names = {table.name for table in s.metadata.sorted_tables}
    assert names == LEARNER_OWNED | NOT_LEARNER_OWNED, (
        f'Classify new tables for account deletion: {sorted(names - LEARNER_OWNED - NOT_LEARNER_OWNED)}; '
        f'stale entries: {sorted((LEARNER_OWNED | NOT_LEARNER_OWNED) - names)}')
    assert not LEARNER_OWNED & NOT_LEARNER_OWNED


def test_classification_matches_the_actual_ownership_paths():
    tables = {table.name: table for table in s.metadata.tables.values()}
    for name in LEARNER_OWNED:
        assert reaches_users(tables[name]), f'{name} is declared learner-owned but has no path to users'
    for name in NOT_LEARNER_OWNED:
        assert not reaches_users(tables[name]), f'{name} reaches users: it must be erased with the account'
    for name in OWNED_VIA_PARENT:
        assert 'user_id' not in tables[name].columns or name == 'homework_reflections'


def test_review_improvements_blocks_deletion_of_its_parents_until_it_is_erased_first():
    """Why it matters: these foreign keys make 'DELETE FROM exercises/attempts/users' fail while a row remains."""
    parents = {fk.column.table.name for fk in s.review_improvements.foreign_keys}
    assert parents == {'users', 'exercises', 'attempts'}
    assert all(fk.ondelete is None for fk in s.review_improvements.foreign_keys)  # no cascade: deletion order is the caller's job
