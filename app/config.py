from pathlib import Path
from typing import Literal
from uuid import UUID
from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from app.accent_practice import VerifiedPassageVoice
from app.accents import LABELS
from app.accent_registry import routing_allowed
from app.security import parse_browser_origin
from sqlalchemy.engine import make_url

class Settings(BaseSettings):
    homework_enabled: bool = False
    youtube_api_key: SecretStr | None = None
    homework_daily_search_limit: int = Field(default=0, ge=0, le=100)
    homework_region: str | None = Field(default=None, pattern=r'^[A-Z]{2}$')
    planner_enabled: bool = True
    model_config = SettingsConfigDict(env_prefix='AUDLI_', env_file='.env', extra='ignore', hide_input_in_errors=True)
    openai_api_key: SecretStr | None = Field(default=None, validation_alias='OPENAI_API_KEY')
    environment: Literal['development', 'production'] = 'development'
    allowed_origins: str = ''
    persistence: Literal['sqlite', 'postgres'] = 'sqlite'
    database_url: SecretStr | None = None
    learner_id: UUID | None = None
    auth_mode: Literal['local', 'supabase'] = 'local'
    supabase_url: str = ''
    supabase_publishable_key: SecretStr | None = None
    provider: Literal['openai', 'demo'] = 'openai'
    latency_telemetry: bool = False
    # Numeric-only aggregate timings logged periodically; no per-request headers, IDs or content.
    latency_aggregates: bool = False
    latency_summary_seconds: float = Field(default=300, ge=30, le=3600)
    # AUD-70: one lock per verified learner instead of one process-wide lock. Off = previous behavior.
    per_learner_locks: bool = False
    llm_model: str = 'gpt-4.1-mini'
    transcription_model: str = 'gpt-4o-mini-transcribe'
    speech_model: str = 'gpt-4o-mini-tts'
    voice: str = 'coral'
    accent_practice_enabled: bool = False
    expanded_accent_library_enabled: bool = False
    expanded_passage_review_dir: Path = Path('data/voice-evaluation/aud38-approved')
    passage_review_dir: Path = Path('data/voice-evaluation/aud20-validation-20261010')
    passage_tts_model: Literal['eleven_flash_v2_5'] = 'eleven_flash_v2_5'
    passage_tts_timeout_seconds: float = Field(default=20, gt=0, le=60)
    passage_tts_max_concurrent: int = Field(default=2, ge=1, le=16)
    passage_tts_max_characters: int = Field(default=4000, ge=1, le=4000)
    passage_tts_daily_characters: int = Field(default=0, ge=0)
    passage_voices: dict[str, VerifiedPassageVoice] = Field(default_factory=dict)
    # AUD-74 rollout flag: ordinary (non-accent) passages use the tutor's ElevenLabs voice ID. OFF = previous OpenAI route.
    default_passage_elevenlabs_enabled: bool = False
    tutor_tts_provider: Literal['openai', 'elevenlabs'] = 'openai'
    elevenlabs_api_key: SecretStr | None = Field(default=None, validation_alias='ELEVENLABS_API_KEY')
    elevenlabs_model: Literal['eleven_flash_v2_5', 'eleven_multilingual_v2'] = 'eleven_flash_v2_5'
    elevenlabs_voice_id: str = Field(default='yKYzqEa22xh5PdidhN70', pattern=r'^[A-Za-z0-9]{20}$')
    elevenlabs_stability: float = Field(default=.5, ge=0, le=1)
    elevenlabs_similarity_boost: float = Field(default=.75, ge=0, le=1)
    elevenlabs_style: float = Field(default=0, ge=0, le=1)
    elevenlabs_speaker_boost: bool = True
    tutor_tts_timeout_seconds: float = Field(default=15, gt=0, le=60)
    tutor_tts_fallback_timeout_seconds: float = Field(default=20, gt=0, le=60)
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
    def available_passage_voices(self) -> dict[str, VerifiedPassageVoice]:
        return {accent: voice for accent, voice in self.passage_voices.items()
            if routing_allowed(accent, voice.voice_id)} if self.accent_practice_enabled else {}

    @property
    def browser_origins(self) -> tuple[str, ...]:
        return tuple(self.allowed_origins.split(',')) if self.allowed_origins else ()

    @model_validator(mode='after')
    def thresholds(self):
        if self.expanded_accent_library_enabled:
            from app.passage_validation import approved_configuration
            from app.accent_registry import SELECTED_VOICES
            try:
                expanded = approved_configuration(self.expanded_passage_review_dir)
                if set(expanded) != set(SELECTED_VOICES):
                    raise ValueError('Incomplete expanded approval')
                for accent, evidence in expanded.items():
                    configured = self.passage_voices.get(accent)
                    if configured and configured.voice_id != evidence['voice_id']:
                        raise ValueError('Expanded voice identity mismatch')
                    self.passage_voices[accent] = VerifiedPassageVoice(**evidence)
            except (OSError, ValueError, KeyError, TypeError):
                raise ValueError('Expanded accent library requires complete owner-approved sample hashes') from None
        if any(key not in LABELS for key in self.passage_voices):
            raise ValueError('Unknown passage accent')
        if self.passage_voices and (self.provider == 'demo' or not self.elevenlabs_api_key
                or not self.elevenlabs_api_key.get_secret_value().strip()):
            raise ValueError('Configured passage voices require live provider and ElevenLabs credentials')
        if self.accent_practice_enabled and self.passage_tts_daily_characters <= 0:
            raise ValueError('Accent practice requires an explicit positive daily character budget ceiling')
        if self.accent_practice_enabled and self.environment == 'production':
            from app.passage_validation import approved_configuration
            try:
                approved = approved_configuration(self.passage_review_dir)
                if self.expanded_passage_review_dir.exists():
                    approved.update(approved_configuration(self.expanded_passage_review_dir))
            except (OSError, ValueError, KeyError, TypeError):
                raise ValueError('Readable complete human passage review evidence is required') from None
            if not self.passage_voices or any(approved.get(accent, {}).get('voice_id') != voice.voice_id
                    for accent, voice in self.passage_voices.items()):
                raise ValueError('Configured passage voices have not passed human approval/hash validation')
        if self.default_passage_elevenlabs_enabled:
            if self.provider == 'demo':
                raise ValueError('ElevenLabs default passages require AUDLI_PROVIDER=openai')
            if not self.elevenlabs_api_key or not self.elevenlabs_api_key.get_secret_value().strip():
                raise ValueError('ElevenLabs default passages require ELEVENLABS_API_KEY')
            if self.passage_tts_daily_characters <= 0:
                raise ValueError('ElevenLabs default passages require a positive AUDLI_PASSAGE_TTS_DAILY_CHARACTERS ceiling')
        if self.tutor_tts_provider == 'elevenlabs':
            if self.provider == 'demo':
                raise ValueError('ElevenLabs tutor TTS requires AUDLI_PROVIDER=openai')
            if not self.elevenlabs_api_key or not self.elevenlabs_api_key.get_secret_value().strip():
                raise ValueError('ElevenLabs tutor TTS requires ELEVENLABS_API_KEY')
        if not self.low_score < self.edge_low < self.edge_high:
            raise ValueError('Expected low_score < edge_low < edge_high')
        if self.environment == 'production' and self.persistence != 'postgres':
            raise ValueError('Production requires AUDLI_PERSISTENCE=postgres; SQLite is for local development/tests')
        if self.environment == 'production' and self.auth_mode != 'supabase':
            raise ValueError('Production requires AUDLI_AUTH_MODE=supabase; local identity is development-only')
        if self.auth_mode == 'supabase':
            if self.learner_id is not None:
                raise ValueError('Remove AUDLI_LEARNER_ID: Supabase Auth supplies the learner identity')
            try:
                if parse_browser_origin(self.supabase_url)[0] != 'https':
                    raise ValueError()
            except ValueError:
                raise ValueError('AUDLI_SUPABASE_URL must be an exact HTTPS project origin') from None
            if not self.supabase_publishable_key or not self.supabase_publishable_key.get_secret_value().startswith('sb_publishable_'):
                raise ValueError('AUDLI_SUPABASE_PUBLISHABLE_KEY requires a Supabase publishable key (never a secret/service-role key)')
        if self.persistence == 'postgres':
            if not self.database_url or (self.auth_mode == 'local' and not self.learner_id):
                raise ValueError('Postgres requires AUDLI_DATABASE_URL; local mode also requires AUDLI_LEARNER_ID')
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
