"""Development exception diagnostics without headers, locals or audio payloads."""
import json
import logging
import os
import re
import traceback
from contextlib import contextmanager
from pydantic import ValidationError
from app.config import Settings


def redact(text: str, settings: Settings) -> str:
    secrets = [value for name, value in os.environ.items()
               if value and re.search(r'(api.?key|token|password|secret|authorization)', name, re.I)]
    if settings.openai_api_key:
        secrets.append(settings.openai_api_key.get_secret_value())
    for secret in sorted(set(secrets), key=len, reverse=True):
        text = text.replace(secret, '[REDACTED]')
    text = re.sub(r'(?i)(authorization\s*[=:]\s*)[^\n]+', r'\1[REDACTED]', text)
    text = re.sub(r'(?i)\bBearer\s+[^\s\"\',;]+', 'Bearer [REDACTED]', text)
    text = re.sub(r'\bsk-[A-Za-z0-9_*-]+', '[REDACTED]', text)
    text = re.sub(
        r'''(?i)((?:api[_-]?key|password|secret|access[_-]?token|refresh[_-]?token)["']?\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s,;]+)''',
        r'\1[REDACTED]', text)
    # Never emit bytes reprs (upload/audio payloads) from exception messages.
    text = re.sub(r'''\bb(["'])(?:\\.|(?!\1).)*\1''', '[BINARY PAYLOAD REDACTED]', text, flags=re.S)
    return text


def report_failure(settings: Settings, operation: str, exc: Exception):
    logger = logging.getLogger('audli')
    qualified_type = f'{type(exc).__module__}.{type(exc).__qualname__}'
    if settings.environment != 'development':
        logger.error('Operation failed: %s; exception=%s', operation, qualified_type)
        return
    parts = [f'Operation failed: {operation}']
    current = exc
    seen = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, ValidationError):
            errors = current.errors(include_input=False, include_context=False)
            for error in errors:
                # Literal validation messages contain allowed values. Evaluator
                # enums include private learner excerpts, even with input omitted.
                if error['type'] == 'literal_error':
                    error['msg'] = 'Input must match a permitted literal value'
            message = json.dumps(errors)
        else:
            message = str(current)
        parts.append(f'{type(current).__module__}.{type(current).__qualname__}: {message}')
        # Source snippets can themselves contain literal credentials/audio. Keep every
        # stack frame's path, function and line number, without source text or locals.
        frames = traceback.extract_tb(current.__traceback__)
        parts.append('Traceback (no source payloads or locals):\n' + ''.join(
            f'  File "{frame.filename}", line {frame.lineno}, in {frame.name}\n' for frame in frames))
        current = current.__cause__ or (current.__context__ if not current.__suppress_context__ else None)
    logger.error('%s', redact('\n'.join(parts), settings))


@contextmanager
def operation(settings: Settings, name: str):
    """Log at the precise failure boundary and preserve the original exception."""
    try:
        yield
    except Exception as exc:
        report_failure(settings, name, exc)
        raise
