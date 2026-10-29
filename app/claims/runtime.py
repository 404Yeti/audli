"""Explicit startup ownership controls; never applies DDL or modifies settings.

`off` preserves the existing workflow only on schemas preceding ownership guards.
Schema 8 requires guard-aware bridge/claims/paused code, including during rollback.
"""
from sqlalchemy import text
from starlette.responses import JSONResponse


class Pause:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get('path', '')
        safe = (scope.get('method') in ('GET', 'HEAD', 'OPTIONS') and
                (path in ('/api/health', '/api/upload-policy', '/api/operation-capabilities')
                 or path.startswith('/api/operations/')))
        if scope['type'] == 'http' and path.startswith('/api/') and not safe:
            return await JSONResponse({'detail': 'Processing is paused. Keep your recording and check saved progress.'},
                                      status_code=503, headers={'Retry-After': '1'})(scope, receive, send)
        return await self.app(scope, receive, send)


def configure(app, settings, ai):
    mode = settings.durable_operations
    engine = app.state.repository.engine
    if engine.dialect.name != 'postgresql':
        if mode != 'off':
            raise ValueError('Durable operations require PostgreSQL and verified authentication')
        return
    with engine.connect() as db:
        version = db.execute(text('SELECT max(version) FROM schema_migrations')).scalar_one()
    if mode == 'off':
        if version >= 8:
            raise ValueError('Schema 8 requires guard-aware bridge, claims or paused mode')
        return
    if settings.auth_mode != 'supabase' or version != 8:
        raise ValueError('Durable operations require authenticated schema 8')
    if not settings.upload_idempotency:
        raise ValueError('Durable operations require stable upload idempotency')
    if mode == 'claims' and not settings.durable_fleet_drained:
        raise ValueError('Claims activation requires independently verified fleet drain')
    with engine.connect() as db:
        unsafe = db.execute(text("SELECT rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb OR rolreplication FROM pg_roles WHERE rolname=current_user")).scalar_one()
        destructive = db.execute(text("SELECT EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind='r' AND (has_table_privilege(current_user,c.oid,'TRUNCATE,TRIGGER,REFERENCES,MAINTAIN') OR c.relowner=(SELECT oid FROM pg_roles WHERE rolname=current_user)))")).scalar_one()
        if unsafe or destructive or db.execute(text("SELECT has_schema_privilege(current_user,'public','CREATE')")).scalar_one():
            raise ValueError('Durable operations require a restricted non-owner backend role')
        if db.execute(text("SELECT has_table_privilege(current_user,'public.schema_migrations','INSERT,UPDATE,DELETE') OR has_table_privilege(current_user,'public.poc_recovery_audit','INSERT,UPDATE')")).scalar_one():
            raise ValueError('Backend role must not mutate migration history or forge recovery audit')
        from .assessment_claims.schema import TABLES
        permissions = {table.name: ('SELECT','INSERT','UPDATE','DELETE') for table in TABLES}
        permissions.update({
            'schema_migrations': ('SELECT',),
            'poc_account_slots': ('SELECT','INSERT','UPDATE','DELETE'),
            'poc_upload_operations': ('SELECT','INSERT','UPDATE','DELETE'),
            'poc_stage_operations': ('SELECT','INSERT','UPDATE','DELETE'),
            'poc_recovery_audit': ('SELECT','DELETE'),
            'accent_control': ('SELECT',),
            'accent_reservations': ('SELECT','INSERT','UPDATE','DELETE'),
            'homework_discovery_budget': ('SELECT','INSERT','UPDATE'),
            'listening_content': ('SELECT','INSERT','UPDATE'),
        })
        for table, privileges in permissions.items():
            for privilege in privileges:
                if not db.execute(text('SELECT has_table_privilege(current_user,:table,:privilege)'),
                                  {'table':'public.'+table,'privilege':privilege}).scalar_one():
                    raise ValueError('Durable operations are missing reviewed runtime table privileges')
        for column in ('budget_day','characters'):
            if not db.execute(text("SELECT has_column_privilege(current_user,'public.accent_control',:column,'UPDATE')"),
                              {'column':column}).scalar_one():
                raise ValueError('Durable operations are missing shared budget privileges')
        if db.execute(text("SELECT count(*) FROM pg_proc WHERE pronamespace='public'::regnamespace AND proname IN ('poc_stage_claim','poc_stage_apply','poc_stage_read','poc_claim_guard','poc_checkpoint_parent_guard') AND has_function_privilege(current_user,oid,'EXECUTE')")).scalar_one()!=5:
            raise ValueError('Durable operations are missing reviewed runtime function privileges')
    # Repository startup has already checked the complete guard/schema contract.
    # Only this engine is authorized; DDL/recovery tools retain loopback checks.
    engine._audli_validated_claim_runtime = True
    from .assessment_claims.integration import install
    install(app, settings, ai(), mode='bridge' if mode == 'bridge' else 'claims')
    if mode == 'paused':
        # Wrap outside authentication: initialization itself can write profiles.
        from app.auth import LearnerAuthentication
        parent = None
        current = app.middleware_stack
        while not isinstance(current, LearnerAuthentication):
            parent, current = current, current.app
        if parent is None:
            app.middleware_stack = Pause(current)
        else:
            parent.app = Pause(current)
    app.state.durable_mode = mode
