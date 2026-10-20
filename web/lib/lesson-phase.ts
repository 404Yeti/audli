/** The one lesson lifecycle the learner sees. Pure; React owns media, never ordering.
 *
 *  HOME → TOPIC_SETUP? → SMALL_TALK → HOMEWORK_PREFLIGHT → LESSON → FEEDBACK
 *       → HOMEWORK → SUCCESS → HOME
 *
 *  Server checkpoints are authoritative while a lesson is open. After the lesson completes the
 *  server has nothing left to say, so HOMEWORK/SUCCESS are browser presentation phases.
 *  HOMEWORK is the new-homework query; SUCCESS (celebration + final statistics on one screen)
 *  cannot start until it is resolved and lasts until the learner returns Home.
 *  Phases only move forward within one lesson. Topic preferences exist solely in TOPIC_SETUP,
 *  which is reachable only from HOME, before any lesson exists. */
export type ServerPhase = 'WELCOME' | 'WELCOME_ACK' | 'REVIEW' | 'REVIEW_ACK' | 'TRANSITION' | 'EXERCISE' | 'CLOSING' | 'COMPLETED';
export type LessonPhase = 'HOME' | 'TOPIC_SETUP' | 'SMALL_TALK' | 'HOMEWORK_PREFLIGHT' | 'LESSON' | 'FEEDBACK' | 'HOMEWORK' | 'SUCCESS';
export type PhaseState = { phase: LessonPhase; lessonId: string | null };

export const INITIAL_PHASE: PhaseState = { phase: 'HOME', lessonId: null };

const order: LessonPhase[] = ['HOME', 'TOPIC_SETUP', 'SMALL_TALK', 'HOMEWORK_PREFLIGHT', 'LESSON', 'FEEDBACK', 'HOMEWORK', 'SUCCESS'];
const rank = (phase: LessonPhase) => order.indexOf(phase);
const fromServer: Record<ServerPhase, LessonPhase> = {
  WELCOME: 'SMALL_TALK', WELCOME_ACK: 'SMALL_TALK', REVIEW: 'SMALL_TALK', REVIEW_ACK: 'SMALL_TALK',
  TRANSITION: 'HOMEWORK_PREFLIGHT', EXERCISE: 'LESSON', CLOSING: 'FEEDBACK', COMPLETED: 'HOMEWORK',
};

export type PhaseEvent =
  | { type: 'topic_setup' }
  | { type: 'topic_resolved' }
  | { type: 'checkpoint'; lessonId: string; phase: ServerPhase }
  /** Saved completed lesson restored in a new page lifetime; its celebration is never replayed. */
  | { type: 'restored'; lessonId: string; homeworkResolved: boolean }
  /** The new-homework decision is persisted (accepted, declined, unavailable) or explicitly skipped. */
  | { type: 'homework_resolved'; lessonId: string }
  | { type: 'home' };

/** Phases from which a lesson's instructional content is over. Preferences can never return here. */
export const after = (state: PhaseState, phase: LessonPhase) => rank(state.phase) >= rank(phase);
export const topicPreferencesAllowed = (state: PhaseState) => state.phase === 'TOPIC_SETUP';

export function lessonPhase(state: PhaseState, event: PhaseEvent): PhaseState {
  const move = (phase: LessonPhase, lessonId: string | null = state.lessonId): PhaseState =>
    phase === state.phase && lessonId === state.lessonId ? state : { phase, lessonId };
  switch (event.type) {
    case 'topic_setup': return state.phase === 'HOME' ? move('TOPIC_SETUP', null) : state;
    case 'topic_resolved': return state.phase === 'TOPIC_SETUP' ? move('HOME', null) : state;
    case 'home': return move('HOME', null);
    case 'restored': return state.phase === 'HOME' ? move(event.homeworkResolved ? 'SUCCESS' : 'HOMEWORK', event.lessonId) : state;
    case 'checkpoint': {
      const next = fromServer[event.phase];
      // A different lesson may begin only from HOME or setup; otherwise the event is stale.
      if (state.lessonId !== event.lessonId) return rank(state.phase) <= rank('TOPIC_SETUP') ? move(next, event.lessonId) : state;
      // Duplicate or delayed checkpoints cannot move a lesson backwards. HOMEWORK+ is presentation-only.
      return rank(next) > rank(state.phase) ? move(next) : state;
    }
    case 'homework_resolved': return state.lessonId === event.lessonId && state.phase === 'HOMEWORK' ? move('SUCCESS') : state;
    default: return state;
  }
}
