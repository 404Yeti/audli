"""Deterministic outlines; learner feedback guides practice, never scores or difficulty."""
import re
from datetime import datetime
from typing import Literal
from pydantic import Field
from app.models import StrictModel, Difficulty, Dimension, Transcription, ListeningProfile, AccentPreferences, Adaptation
from app.lesson import safe_label

CHECKIN_PROMPT = 'Would you like to keep your listening topics, or try something different? You can say keep them or name a topic.'

class PlannedLesson(StrictModel):
    number: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=200)
    topic: str = Field(min_length=1, max_length=160)
    focus: Dimension
    objective: str
    difficulty: Difficulty
    accent_focus: str | None = None

class PlannerFeedback(StrictModel):
    activation_at: datetime | None = None
    after_lesson: int = Field(ge=3)
    status: Literal['answered', 'skipped']
    response: str = Field(default='', max_length=8000)
    interests: list[str] | None = None
    focus: Dimension | None = None

class PlanningContext(StrictModel):
    goal: str
    interests: list[str]
    situations: list[str]
    accents: AccentPreferences
    comprehension: ListeningProfile
    difficulty: Difficulty
    focus: Dimension
    onboarding_status: Literal['not_started', 'in_progress', 'profile_saved', 'complete']
    recent_adaptations: list[Adaptation]

class Roadmap(StrictModel):
    offered_checkins: list[int] = Field(default_factory=list)
    version: Literal[1] = 1
    revision: int = Field(default=0, ge=0)
    activation_at: datetime | None = None
    baseline_ids: list[str] = Field(default_factory=list)
    completed_ids: list[str] = Field(default_factory=list)
    upcoming: list[PlannedLesson] = Field(default_factory=list, max_length=3)
    feedback: list[PlannerFeedback] = Field(default_factory=list)
    pending: Transcription | None = None
    context: PlanningContext | None = None

def qualifying_count(roadmap: Roadmap) -> int:
    return len(set(roadmap.completed_ids) - set(roadmap.baseline_ids))

def requested_focus(text):
    # Optional preference only. No inference about comprehension from self-report.
    for dimension, words in [('main_idea', r'main (idea|point)|gist'), ('details', r'details|dates|times'),
                             ('vocabulary', r'vocabulary|words in context'), ('inference', r'implied|inference')]:
        if re.search(words, text, re.I):
            return dimension
    return None

# Three contexts within the same evidence-selected focus; no difficulty changes.
CONTEXTS = ('a short update', 'practical instructions', 'a personal story')
LISTENING_TASKS = {
    'main_idea': ('Explain the main change and why it matters', 'Identify the purpose of the advice', 'Summarize the experience and its main point'),
    'details': ('Catch the key names, times and changes', 'Follow the important steps in order', 'Notice what happened and which details explain it'),
    'vocabulary': ('Use surrounding details to understand an unfamiliar phrase', 'Work out an action word from the steps being described', 'Use the situation to understand how a phrase expresses a feeling'),
    'inference': ('Work out why the speaker shares this update', 'Infer why a step or precaution matters', 'Work out how the speaker feels from what they describe'),
}

def preferred_topic(preference: str, planned_topic: str | None) -> str:
    """Retain a new plan's context on topic choice; legacy topics stay unchanged."""
    context = next((item for item in CONTEXTS
                    if planned_topic and planned_topic.endswith(': ' + item)), None)
    selected = f'{preference}: {context}' if context else preference
    return selected if len(selected) <= 160 else preference

def outlines(profile, count, feedback, context):
    latest = next((item for item in reversed(feedback) if item.status == 'answered'), None)
    interests = latest.interests if latest and latest.interests is not None else profile.interests
    topics = [label for item in interests + profile.target_situations
              if (label := safe_label(item)) and len(label) <= 100]
    topics = list(dict.fromkeys(topics)) or ['everyday conversations']
    focus = latest.focus if latest and latest.focus else profile.focus
    recent = context.recent_adaptations[:3] if context else []
    support = ' Take your time as you build confidence at your current pace.' if any(event.decision == 'easier' for event in recent) else ''
    accents = profile.accent_preferences.accents if profile.accent_preferences.status == 'preferred' else []
    return [PlannedLesson(number=count+i+1,
        title=f'{topics[(count+i)%len(topics)].capitalize()}: {CONTEXTS[(count+i)%3]}',
        # This saved context reaches on-demand generation via lesson.plan_topic.
        topic=f'{topics[(count+i)%len(topics)]}: {CONTEXTS[(count+i)%3]}', focus=focus,
        objective=f'{LISTENING_TASKS[focus][(count+i)%3]} in {CONTEXTS[(count+i)%3]} about {topics[(count+i)%len(topics)]}. This supports your goal: {profile.goal}.' + support,
        difficulty=profile.difficulty.model_copy(deep=True), accent_focus=accents[i%len(accents)] if accents else None)
        for i in range(3)]
