from pathlib import Path
from typing import Literal
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix='AUDLI_', env_file='.env', extra='ignore')
    openai_api_key: SecretStr | None = Field(default=None, validation_alias='OPENAI_API_KEY')
    environment: Literal['development', 'production'] = 'development'
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

    @model_validator(mode='after')
    def thresholds(self):
        if not self.low_score < self.edge_low < self.edge_high:
            raise ValueError('Expected low_score < edge_low < edge_high')
        return self
