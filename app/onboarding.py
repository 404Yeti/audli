"""Preferences only: no listening score or adaptation is inferred from speech."""
from typing import Literal
from pydantic import Field, field_validator
from app.models import StrictModel

class ProfileExtraction(StrictModel):
    name: str | None = Field(max_length=80)
    target_language: str | None = Field(max_length=80)
    goal: str | None = Field(max_length=500)
    target_situations: list[str] | None = Field(max_length=12)
    interests: list[str] | None = Field(max_length=12)

    @field_validator('name', 'target_language', 'goal')
    @classmethod
    def nonempty(cls, value):
        if value is not None and not value.strip():
            raise ValueError('Empty preference')
        return value.strip() if value is not None else None

    @field_validator('target_situations', 'interests')
    @classmethod
    def bounded_labels(cls, values):
        if values is not None and any(not v.strip() or len(v) > 160 for v in values):
            raise ValueError('Invalid preference labels')
        return [v.strip() for v in values] if values is not None else None

PROMPTS = {
    'identity': "Before we start, what should I call you, and what language do you want to train? Audli currently trains English listening.",
    'needs': "Why are you learning English, and what do you most want to understand better? Tell me about situations where listening is hardest or most important for you.",
    'interests': "What topics do you enjoy listening to? A few interests are enough, or say you have no preference.",
    'review': "Your listening preferences are saved. You're ready to train your ears. Your first listening exercise will help us find your starting point.",
}

class Revision(StrictModel):
    revision: int = Field(ge=0)

class Answer(Revision):
    text: str = Field(min_length=1, max_length=8000)
    confirmed: Literal[True]
    hands_free: bool = False


def apply_extraction(profile, extraction):
    extraction = ProfileExtraction.model_validate(extraction)
    stage = profile.onboarding.stage
    if stage == 'identity':
        if not extraction.name or not extraction.target_language:
            raise ValueError('Please tell Audli your preferred name and training language.')
        if extraction.target_language.casefold() not in ('en', 'english'):
            raise ValueError('Audli currently supports English listening. Please confirm English to continue.')
        profile.name, profile.target_language = extraction.name, 'en'
        profile.onboarding.stage = 'needs'
    elif stage == 'needs':
        if not extraction.goal or not extraction.target_situations:
            raise ValueError('Please explain your reason for learning and a listening situation that matters to you.')
        profile.goal, profile.target_situations = extraction.goal, extraction.target_situations
        profile.onboarding.stage = 'interests'
    elif stage == 'interests':
        if extraction.interests is None:
            raise ValueError('Please share an interest, or say you have no preference.')
        profile.interests = extraction.interests
        profile.onboarding.stage = 'review'
        profile.onboarding_status = 'profile_saved'
    else:
        raise ValueError('Your profile is ready for confirmation.')
    profile.onboarding.pending = None
    return profile


def application_destination(profile):
    """One authoritative lifecycle decision, independent of exercise existence."""
    return 'session_ready' if profile.onboarding_status == 'complete' else 'onboarding'
