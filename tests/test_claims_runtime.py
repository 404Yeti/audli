"""Startup controls do not initialize providers or mutate schemas when disabled."""
from types import SimpleNamespace
import asyncio
import pytest
from app.config import Settings
from app.claims.runtime import configure, Pause


def test_disabled_default_has_no_provider_or_schema_side_effects():
    settings=Settings(provider='demo',_env_file=None)
    assert settings.durable_operations=='off' and not settings.durable_fleet_drained
    app=SimpleNamespace(state=SimpleNamespace(repository=SimpleNamespace(engine=SimpleNamespace(dialect=SimpleNamespace(name='sqlite')))))
    configure(app,settings,lambda:pytest.fail('Disabled ownership initialized a provider'))


def test_sqlite_activation_fails_closed():
    app=SimpleNamespace(state=SimpleNamespace(repository=SimpleNamespace(engine=SimpleNamespace(dialect=SimpleNamespace(name='sqlite')))))
    with pytest.raises(ValueError):configure(app,Settings(provider='demo',durable_operations='claims',_env_file=None),lambda:None)


def test_pause_blocks_initialization_writes_and_allows_only_status():
    async def run():
        seen=[]
        async def downstream(scope,receive,send):seen.append(scope['path'])
        async def receive():return {'type':'http.request','body':b''}
        messages=[]
        async def send(message):messages.append(message)
        pause=Pause(downstream)
        for method,path in [('POST','/api/exercises/x/attempts'),('GET','/api/plan'),('GET','/api/lessons/x'),('POST','/api/account/delete')]:
            await pause({'type':'http','method':method,'path':path},receive,send)
            assert messages[-2]['status']==503
        assert not seen
        for path in ['/api/health','/api/operations/abc']:
            await pause({'type':'http','method':'GET','path':path},receive,send)
        assert seen==['/api/health','/api/operations/abc']
    asyncio.run(run())


def test_invalid_control_is_rejected():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):Settings(provider='demo',durable_operations='automatic',_env_file=None)


def test_runtime_imports_have_no_experimental_dependencies():
    import ast
    from pathlib import Path
    for source in (Path(__file__).resolve().parents[1]/'app/claims').rglob('*.py'):
        tree=ast.parse(source.read_text())
        for node in ast.walk(tree):
            if isinstance(node,ast.ImportFrom):assert not (node.module or '').startswith('experiments')
            elif isinstance(node,ast.Import):assert all(not name.name.startswith('experiments') for name in node.names)


def test_grant_generator_is_identifier_safe_and_scoped():
    from app.claims.privileges import runtime_grants
    with pytest.raises(ValueError):runtime_grants('role;drop schema public')
    statements=runtime_grants('reviewed_backend')
    assert any('GRANT SELECT ON public.schema_migrations' in value for value in statements)
    assert any('FOR SELECT' in value and 'schema_migrations' in value for value in statements)
    assert any('UPDATE(budget_day,characters)' in value for value in statements)
    assert all('BYPASSRLS' not in value and 'SECURITY DEFINER' not in value and 'TRUNCATE' not in value for value in statements)
