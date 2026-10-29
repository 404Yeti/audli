"""Reviewable least-privilege SQL generation. Never executes SQL."""
import re
from .assessment_claims.schema import TABLES

FUNCTIONS = (
    'poc_stage_read(uuid,uuid,uuid)',
    'poc_stage_apply(uuid,uuid,integer,jsonb,boolean,boolean)',
    'poc_stage_claim(uuid,uuid,uuid,text,text,integer,timestamp with time zone,jsonb,jsonb,jsonb)',
    'poc_claim_guard()',
    'poc_checkpoint_parent_guard()',
)


def role_identifier(role):
    if not re.fullmatch('[a-z][a-z0-9_]{1,40}',role):
        raise ValueError('Invalid reviewed backend role')
    return '"'+role+'"'


def table_statements(role):
    role=role_identifier(role)
    statements=[f'GRANT USAGE ON SCHEMA public TO {role}',
                f'GRANT SELECT ON public.schema_migrations TO {role}',
                f'CREATE POLICY audit_schema_read ON public.schema_migrations FOR SELECT TO {role} USING (true)']
    shared={
        'accent_control': ('SELECT','UPDATE'),
        'accent_reservations': ('SELECT','INSERT','UPDATE','DELETE'),
        'homework_discovery_budget': ('SELECT','INSERT','UPDATE'),
        'listening_content': ('SELECT','INSERT','UPDATE'),
    }
    for name,privileges in shared.items():
        grants=','.join('UPDATE(budget_day,characters)' if name=='accent_control' and p=='UPDATE' else p for p in privileges)
        statements.append(f'GRANT {grants} ON public."{name}" TO {role}')
        for privilege in privileges:
            predicate='WITH CHECK (true)' if privilege=='INSERT' else 'USING (true) WITH CHECK (true)' if privilege=='UPDATE' else 'USING (true)'
            statements.append(f'CREATE POLICY audit_shared_{privilege.lower()} ON public."{name}" FOR {privilege} TO {role} {predicate}')
    for name in [*(table.name for table in TABLES),'poc_account_slots','poc_upload_operations','poc_stage_operations']:
        statements.extend((f'GRANT SELECT,INSERT,UPDATE,DELETE ON public."{name}" TO {role}',
                           f'CREATE POLICY audit_internal_server ON public."{name}" TO {role} USING (true) WITH CHECK (true)'))
    statements.extend((f'GRANT SELECT,DELETE ON public.poc_recovery_audit TO {role}',
                       f'CREATE POLICY account_erasure ON public.poc_recovery_audit TO {role} USING (true) WITH CHECK (false)'))
    return statements


def runtime_grants(role):
    """Fresh reviewed role only. Existing duplicate policies fail atomically."""
    statements=table_statements(role)
    statements.extend(f'GRANT EXECUTE ON FUNCTION public.{signature} TO {role_identifier(role)}' for signature in FUNCTIONS)
    return statements
