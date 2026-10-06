from pathlib import Path
from typing import Literal
from uuid import UUID
from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from app.security import parse_browser_origin
from sqlalchemy.engine import make_url

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix='AUDLI_', env_file='.env', extra='ignore', hide_input_in_errors=True)
    openai_api_key: SecretStr | None = Field(default=None, validation_alias='OPENAI_API_KEY')
    environment: Literal['development', 'production'] = 'development'
    allowed_origins: str = ''
    persistence: Literal['sqlite', 'postgres'] = 'sqlite'
    database_url: SecretStr | None = None
    learner_id: UUID | None = None
    provider: Literal['openai', 'demo'] = 'openai'
    llm_model: str = 'gpt-4.1-mini'
    transcription_model: str = 'gpt-4o-mini-transcribe'
    speech_model: str = 'gpt-4o-mini-tts'
    voice: str = 'coral'
    data_dir: Path = Path('data')
    edge_low: float = Field(default=.70, gt=0, lt=1)
    edge_high: float = Field(default=.85, gt=0, lt=1)
    low_score: float = Field(default=.55, gt=0, lt=1)
    min_transcription_confidence: float = Field(default=.65, ge=0, le=1)
    max_audio_bytes: int = 12 * 1024 * 1024
    max_recording_seconds: int = 120
    max_feedback_words: int = Field(default=50, ge=30, le=50)

    @field_validator('database_url', 'learner_id', mode='before')
    @classmethod
    def empty_persistence_values(cls, value):
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator('allowed_origins')
    @classmethod
    def validate_allowed_origins(cls, value: str) -> str:
        origins = [origin.strip() for origin in value.split(',') if origin.strip()]
        for origin in origins:
            parse_browser_origin(origin)
        return ','.join(origins)

    @property
    def browser_origins(self) -> tuple[str, ...]:
        return tuple(self.allowed_origins.split(',')) if self.allowed_origins else ()

    @model_validator(mode='after')
    def thresholds(self):
        if not self.low_score < self.edge_low < self.edge_high:
            raise ValueError('Expected low_score < edge_low < edge_high')
        if self.environment == 'production' and self.persistence != 'postgres':
            raise ValueError('Production requires AUDLI_PERSISTENCE=postgres; SQLite is for local development/tests')
        if self.persistence == 'postgres':
            if not self.database_url or not self.learner_id:
                raise ValueError('Postgres requires AUDLI_DATABASE_URL and a stable AUDLI_LEARNER_ID UUID')
            try:
                url = make_url(self.database_url.get_secret_value())
                valid = (url.drivername in ('postgres', 'postgresql', 'postgresql+psycopg')
                    and url.host and url.database and url.username and url.password
                    and url.port != 0 and not url.query.get('options'))
                if self.environment == 'production':
                    valid = valid and url.query.get('sslmode') in ('require', 'verify-ca', 'verify-full')
                if not valid:
                    raise ValueError()
            except Exception:
                raise ValueError('AUDLI_DATABASE_URL must be a complete Postgres URI; production requires sslmode=require or stronger') from None
        elif self.database_url:
            raise ValueError('Set AUDLI_PERSISTENCE=postgres to use AUDLI_DATABASE_URL')
        return self
