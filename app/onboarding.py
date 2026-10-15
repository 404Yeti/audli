"""Preferences only: no listening score or adaptation is inferred from speech."""
from typing import Literal
import unicodedata
import re
from pydantic import Field, field_validator
from app.models import StrictModel
from app.accents import extract as extract_accents

WELCOME = {
    'introduction': "Hi! I’m Audli. I help you improve your English listening with short audio stories and conversations. You’ll listen, tell me what you understood, and I’ll help you improve.",
    'status': "My status bubbles say ‘Audli is speaking’, ‘Listening to you’, or ‘Thinking’. You don’t need to watch them: I’ll finish speaking before I listen. When I’m listening, that’s your turn to talk!",
}

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
    'needs': "What would you like to understand better in English? You can tell me your goal, type it here, or skip for now.",
    'interests': "What topics do you enjoy listening to? A few interests are enough, or say you have no preference.",
    'accents': "Are there any English accents you find tricky? Maybe British, American, Australian, Scottish, Indian, or something else? It’s okay if you’re not sure, or you can say skip.",
    'review': "Your listening preferences are saved. You're ready to train your ears. Your first listening exercise will help us find your starting point.",
}

class Revision(StrictModel):
    revision: int = Field(ge=0)

def normalize_name(value):
    value = unicodedata.normalize('NFC', value)
    if any(unicodedata.category(c).startswith('C') or c in '<>{}[]\\/' for c in value):
        raise ValueError('Please enter a name without control characters or markup.')
    value = ' '.join(value.split())
    if not value or len(value) > 80 or not any(unicodedata.category(c).startswith('L') for c in value):
        raise ValueError('Please enter a name between 1 and 80 characters.')
    return value


class TypedName(StrictModel):
    question_revision: int = Field(ge=0)
    name: str = Field(min_length=1, max_length=320)

    @field_validator('name')
    @classmethod
    def safe_name(cls, value):
        return normalize_name(value)


def name_entry_available(profile):
    return (profile.onboarding_status == 'in_progress' and not profile.onboarding.welcome
            and profile.onboarding.stage == 'identity'
            and 'name' not in profile.onboarding.answered_fields)


class WalkthroughUpdate(Revision):
    step: Literal[0, 1, 2, 'complete', 'skipped']

    @field_validator('step', mode='before')
    @classmethod
    def valid_step(cls, value):
        if isinstance(value, bool) or isinstance(value, float):
            raise ValueError('Choose a walkthrough step or completion action.')
        return value


class Answer(Revision):
    text: str = Field(min_length=1, max_length=8000)
    confirmed: Literal[True]
    hands_free: bool = False


CLARIFICATIONS = {
    'name': 'What should I call you? You can say your name or type it here.',
    'target_language': 'What language do you want to train? Audli currently trains English listening.',
    'goal': 'Why are you learning English?',
    'target_situations': 'What listening situation matters most to you?',
    'interests': 'What topics do you enjoy listening to? You can also say you have no preference.',
    'accents': 'Which English accent would you like me to remember? You can say more than one, no preference, or skip.',
}

def onboarding_prompt(profile):
    if profile.onboarding.welcome:
        return WELCOME[profile.onboarding.welcome]
    prompt = CLARIFICATIONS.get(profile.onboarding.clarification, PROMPTS[profile.onboarding.stage])
    if profile.onboarding.stage == 'interests' and profile.onboarding.goal_rescued:
        return "Thanks for sharing! I focus on listening, so we’ll start with understanding everyday English and explore topics you enjoy. " + prompt
    return prompt

def apply_accent_answer(profile, text):
    profile = profile.model_copy(deep=True)
    preferences = extract_accents(text)
    progress = profile.onboarding
    progress.pending = None
    if preferences is None:
        progress.clarification = 'accents'
        if progress.clarification_count == 2:
            progress.clarification_paused = True
        else:
            progress.clarification_count += 1
        return profile
    profile.accent_preferences = preferences
    progress.accents_asked = True
    progress.stage = 'review'
    progress.clarification = None
    progress.clarification_count = 0
    progress.clarification_paused = False
    profile.onboarding_status = 'profile_saved'
    return profile

def apply_extraction(profile, extraction):
    extraction = ProfileExtraction.model_validate(extraction)
    stage = profile.onboarding.stage
    if stage == 'needs':
        return apply_goal(profile, extraction.goal, extraction.target_situations)
    fields = {'identity': ('name', 'target_language'), 'needs': ('goal', 'target_situations'),
              'interests': ('interests',)}.get(stage)
    if fields is None:
        raise ValueError('Your profile is ready for confirmation.')
    if stage == 'identity' and extraction.target_language is not None and extraction.target_language.casefold() not in ('en', 'english'):
        raise ValueError('Audli currently supports English listening. Please confirm English to continue.')
    profile = profile.model_copy(deep=True)
    progress = profile.onboarding
    # Defaults and previous setup are not evidence for the current spoken stage.
    # Only fields actually supplied in this stage can satisfy its requirements.
    for field in fields:
        if field == 'name' and (progress.typed_name or 'name' in progress.answered_fields):
            continue
        value = getattr(extraction, field)
        if value is None or (field == 'target_situations' and not value):
            continue
        setattr(profile, field, 'en' if field == 'target_language' else value)
        if field not in progress.answered_fields:
            progress.answered_fields.append(field)
    profile.onboarding.pending = None
    missing = next((field for field in fields if field not in progress.answered_fields), None)
    if missing:
        progress.clarification = missing
        # Two automatic fresh-answer clarifications, then a persisted pause.
        # Refresh cannot reset this budget; explicit Retry starts a new cycle.
        if progress.clarification_count == 2:
            progress.clarification_paused = True
        else:
            progress.clarification_count += 1
        return profile
    progress.stage = {'identity': 'needs', 'needs': 'interests', 'interests': 'accents'}[stage]
    if progress.stage == 'accents' and (progress.accents_asked or profile.accent_preferences.status != 'unspecified'):
        progress.stage = 'review'
    if progress.stage == 'needs':
        progress.goal_question_revision = progress.revision + 1
        progress.typed_goal = False
    progress.answered_fields = []
    progress.clarification = None
    progress.clarification_count = 0
    progress.clarification_paused = False
    if progress.stage == 'review':
        profile.onboarding_status = 'profile_saved'
    return profile


def application_destination(profile):
    """One authoritative lifecycle decision, independent of exercise existence."""
    return 'session_ready' if profile.onboarding_status == 'complete' else 'onboarding'


class GoalAnswer(StrictModel):
    question_revision: int = Field(ge=0)
    text: str = Field(default='', max_length=500)
    skip: bool = False

    @field_validator('text')
    @classmethod
    def goal_text(cls, value):
        # Preserve international writing, including combining marks and joiners.
        if any(unicodedata.category(c) == 'Cc' and c not in '\n\r\t' for c in value):
            raise ValueError('Please enter a goal without control characters.')
        return value.strip()


def apply_goal(profile, goal=None, situations=None):
    profile = profile.model_copy(deep=True)
    progress = profile.onboarding
    if progress.goal_question_revision is None:
        progress.goal_question_revision = 0  # Preserve legacy question identity across saves.
    # A goal is a preference, never a prerequisite or evidence of ability.
    profile.goal = goal or profile.goal
    profile.target_situations = situations or profile.target_situations or ['everyday conversations']
    progress.goal_rescued = not goal or not situations or bool(re.search(r'\b(writ\w*|speak\w*|grammar|pronunciation)\b', goal, re.I))
    progress.stage = 'interests'
    progress.pending = None
    progress.answered_fields = []
    progress.clarification = None
    progress.clarification_count = 0
    progress.clarification_paused = False
    return profile
