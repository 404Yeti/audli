from typing import Annotated, Literal
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field

Score = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
Text = Annotated[str, Field(min_length=1, max_length=4000)]
Level = Literal['A2', 'B1', 'B2', 'C1']
Dimension = Literal['main_idea', 'details', 'vocabulary', 'inference']

class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')

class Difficulty(StrictModel):
    speech_rate: float = Field(default=.75, ge=.6, le=1.15)
    duration_seconds: int = Field(default=45, ge=30, le=90)
    vocabulary_level: Level = 'B1'
    information_density: int = Field(default=1, ge=1, le=3)
    speaker_count: Literal[1] = 1
    accent_complexity: Literal[1] = 1
    background_noise: Literal[0] = 0

class ListeningProfile(StrictModel):
    # Starting priors, not observed scores: initial_listening_profile is null
    # until a fully evidenced exercise assessment establishes a baseline.
    overall: Score = .7
    main_idea: Score = .7
    details: Score = .7
    vocabulary: Score = .7
    inference: Score = .7
    natural_speed: Score | None = None

class Transcription(StrictModel):
    text: str = Field(max_length=8000)
    confidence: Score | None = None
    uncertainty: list[str] = Field(default_factory=list)
    source: Literal['openai', 'demo_manual', 'learner_confirmed'] = 'openai'

class OnboardingProgress(StrictModel):
    version: Literal[1] = 1
    stage: Literal['identity', 'needs', 'interests', 'review'] = 'identity'
    revision: int = Field(default=0, ge=0)
    pending: Transcription | None = None

class LearnerProfile(StrictModel):
    name: str = Field(default='Listener', min_length=1, max_length=80)
    target_language: Literal['en'] = 'en'
    goal: str = Field(default='Understand everyday English', min_length=1, max_length=500)
    interests: list[str] = Field(default_factory=lambda: ['technology'])
    target_situations: list[str] = Field(default_factory=list, max_length=30)
    onboarding_status: Literal['not_started', 'in_progress', 'profile_saved', 'complete'] = 'not_started'
    onboarding: OnboardingProgress = Field(default_factory=OnboardingProgress)
    initial_listening_profile: ListeningProfile | None = None
    listening_profile: ListeningProfile = Field(default_factory=ListeningProfile)
    difficulty: Difficulty = Field(default_factory=Difficulty)
    focus: Dimension = 'details'
    completed_attempts: int = 0

class TrainingSession(StrictModel):
    id: str
    learner_id: str
    status: Literal['active', 'completed']
    started_at: datetime
    completed_at: datetime | None = None
    starting_difficulty: Difficulty

class Question(StrictModel):
    question: Text
    expected_information: list[Text] = Field(min_length=1, max_length=5)

class VocabularyItem(StrictModel):
    phrase: Text
    meaning_in_context: Text

class ExerciseContent(StrictModel):
    title: Text
    topic: Text
    script: str = Field(min_length=80, max_length=5000)
    target_duration_seconds: int = Field(ge=30, le=90)
    vocabulary_level: Level
    speech_rate: float = Field(ge=.6, le=1.15)
    information_density: int = Field(ge=1, le=3)
    main_idea: Text
    important_details: list[Text] = Field(min_length=3, max_length=8)
    inference_points: list[Text] = Field(min_length=1, max_length=3)
    vocabulary_items: list[VocabularyItem] = Field(min_length=2, max_length=5)
    questions: list[Question] = Field(min_length=1, max_length=3)

class UnitJudgment(StrictModel):
    dimension: Dimension
    index: int = Field(ge=0)
    status: Literal['understood', 'partial', 'missed', 'misunderstood']
    evidence: str = Field(max_length=1000)

class EvaluationJudgments(StrictModel):
    units: list[UnitJudgment] = Field(min_length=1, max_length=30)
    feedback: str = Field(min_length=1, max_length=600)
    transcription_concern: bool
    concern_reason: str = Field(max_length=500)

class Evaluation(StrictModel):
    main_idea: Score
    details: Score
    vocabulary: Score
    inference: Score
    overall: Score
    understood: list[str]
    missed: list[str]
    misunderstood: list[str]
    feedback: str
    units: list[UnitJudgment]

class Adaptation(StrictModel):
    previous_score: Score | None
    decision: Literal['harder', 'same', 'easier']
    changed_variable: str | None
    old_value: float | int | str | None
    new_value: float | int | str | None
    reason: str
    focus: Dimension
    old_difficulty: Difficulty
    new_difficulty: Difficulty
    teacher_decision: Literal['harder', 'same', 'easier'] | None = None
    policy_version: str = 'v0.1'
    edge_low: Score
    edge_high: Score

# V0.2 schemas are separate so existing V0.1 results retain their original meaning.
EvidenceStatus = Literal['demonstrated', 'partially_demonstrated', 'misunderstood', 'insufficient_evidence']
ConversationState = Literal['LISTENING', 'AWAITING_SUMMARY', 'ASSESSING', 'AWAITING_FOLLOWUP',
                            'ASSESSING_FOLLOWUP', 'GIVING_FEEDBACK', 'READY_FOR_NEXT']

class EvidenceUnit(StrictModel):
    dimension: Dimension
    index: int = Field(ge=0)
    status: EvidenceStatus
    evidence: str = Field(max_length=1000)
    question: str = Field(max_length=200)

class EvidenceAssessment(StrictModel):
    units: list[EvidenceUnit] = Field(min_length=1, max_length=30)
    feedback: str = Field(min_length=1, max_length=600)
    transcription_concern: bool
    concern_reason: str = Field(max_length=500)

class Followup(StrictModel):
    id: str
    dimension: Dimension
    index: int = Field(ge=0)
    question: str = Field(min_length=1, max_length=200)

class ConversationTurn(StrictModel):
    attempt_id: str
    text: str = Field(min_length=1, max_length=8000)
    followup: Followup | None = None

class Conversation(StrictModel):
    exercise_id: str
    state: ConversationState = 'AWAITING_SUMMARY'
    turns: list[ConversationTurn] = Field(default_factory=list)
    assessment: EvidenceAssessment | None = None
    followups: list[Followup] = Field(default_factory=list, max_length=2)
    active_followup: Followup | None = None
    pending_attempt_id: str | None = None
    pending_text: str | None = None

class EvidenceEvaluation(StrictModel):
    version: Literal['v0.2'] = 'v0.2'
    main_idea: Score | None
    details: Score | None
    vocabulary: Score | None
    inference: Score | None
    overall: Score | None
    coverage: Score
    dimensions: dict[Dimension, EvidenceStatus]
    units: list[EvidenceUnit]
    understood: list[str]
    insufficient_evidence: list[str]
    misunderstood: list[str]
    feedback: str
