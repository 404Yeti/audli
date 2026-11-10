"""Which OpenAI model does which Audli 2.0 task, in one place.

Nothing else in app/listening reads a model name from settings. Environment variables (AUDLI_LISTENING_*_MODEL and friends) override
every value, so moving to a new model or rolling back to an old one is a configuration change plus a restart, never a code change.
1.x keeps its own settings (llm_model, transcription_model, speech_model) and is deliberately not touched here: see docs/OPENAI-MODEL-AUDIT.md.

Prices are USD per 1M tokens (text) or per audio minute (transcription) from OpenAI's public pricing and model pages as read on 2026-11-04
through a page-summarising tool. They only size budgets and are never used to bill or limit anyone; re-check them before approving spend.
"""
from dataclasses import dataclass
from typing import Literal

# model id -> (input per 1M, output per 1M)
TEXT_PRICES = {
    'gpt-6-luna': (0.10, 0.50),
    'gpt-6.1-terra': (1.00, 5.00),
    'gpt-6.1-sol': (2.00, 10.00),
    'gpt-6-astra': (10.00, 50.00),
    'gpt-4.1-mini': (0.40, 1.60),       # rollback baseline; listed for shutdown 2027-01-14
    'gpt-4o-mini': (0.15, 0.60),        # listed for shutdown 2027-01-14
}
# model id -> USD per audio minute
AUDIO_PRICES = {
    'gpt-transcribe': 0.0045,
    'gpt-live-transcribe': 0.017,       # realtime streaming; not a fit for recorded answers
    'gpt-4o-mini-transcribe': 0.003,    # estimate from the pricing page; listed for shutdown 2027-02-26
}
# Shutdown dates published for models Audli has used or defaulted to.
RETIRING = {'gpt-4o-mini-transcribe': '2027-02-26', 'gpt-4o-transcribe': '2027-02-26', 'whisper-1': '2027-02-26',
            'gpt-4o-mini': '2027-01-14', 'gpt-4.1-mini': '2027-01-14', 'gpt-4o-mini-tts': '2027-01-06', 'tts-1': '2027-01-06', 'tts-1-hd': '2027-01-06'}


@dataclass(frozen=True)
class TranscriptionModel:
    model: str
    timeout: float

    @property
    def language_parameter(self) -> str:
        """gpt-transcribe takes `languages` (a list); older models take `language`. Sending both is an error."""
        return 'languages' if self.model.startswith('gpt-transcribe') else 'language'


@dataclass(frozen=True)
class AssessmentModel:
    model: str
    api: Literal['responses', 'chat']
    reasoning: str          # '' = omit
    timeout: float
    max_output_tokens: int = 700


@dataclass(frozen=True)
class GenerationModel:
    model: str
    timeout: float
    max_output_tokens: int = 4000


@dataclass(frozen=True)
class ModelPlan:
    transcription: TranscriptionModel
    assessment: AssessmentModel
    generation: GenerationModel
    daily: GenerationModel | None = None      # Daily Challenge semantic review and topic proposals (small, cheap model; never writes learner-facing text)

    @classmethod
    def from_settings(cls, settings) -> 'ModelPlan':
        timeout = settings.listening_provider_timeout_seconds
        return cls(TranscriptionModel(settings.listening_transcription_model, timeout),
                   AssessmentModel(settings.listening_assessment_model, settings.listening_assessment_api, settings.listening_assessment_reasoning, timeout),
                   GenerationModel(settings.listening_generation_model, timeout),
                   GenerationModel(settings.listening_daily_review_model, timeout, 900))

    def retiring(self) -> dict[str, str]:
        """Configured models with a published shutdown date, for a startup warning."""
        return {m: RETIRING[m] for m in (self.transcription.model, self.assessment.model, self.generation.model) if m in RETIRING}


def text_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    price_in, price_out = TEXT_PRICES[model]
    return (input_tokens * price_in + output_tokens * price_out) / 1_000_000


def audio_cost(model: str, seconds: float) -> float:
    return AUDIO_PRICES[model] * seconds / 60
