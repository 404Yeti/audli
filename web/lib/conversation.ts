export type ConversationPhase = 'LISTENING' | 'AWAITING_SUMMARY' | 'ASSESSING' | 'AWAITING_FOLLOWUP' |
  'ASSESSING_FOLLOWUP' | 'GIVING_FEEDBACK' | 'READY_FOR_NEXT';
export type FlowPhase = ConversationPhase | 'RECORDING' | 'REVIEWING_RECORDING' | 'TRANSCRIBING' | 'REVIEWING_TRANSCRIPT';
export type Flow = { phase: FlowPhase; awaiting: 'AWAITING_SUMMARY' | 'AWAITING_FOLLOWUP' };
export type FlowEvent = { type: 'server'; phase: ConversationPhase; hasPending: boolean } |
  { type: 'phase'; phase: FlowPhase } | { type: 'reset' };
export function conversationReducer(state: Flow, event: FlowEvent): Flow {
  if (event.type === 'reset') return { phase: 'LISTENING', awaiting: 'AWAITING_SUMMARY' };
  if (event.type === 'phase') return { ...state, phase: event.phase };
  const awaiting = event.phase === 'AWAITING_FOLLOWUP' || event.phase === 'ASSESSING_FOLLOWUP'
    ? 'AWAITING_FOLLOWUP' : 'AWAITING_SUMMARY';
  return { phase: event.hasPending ? 'REVIEWING_TRANSCRIPT' : event.phase, awaiting };
}
export function canRespond(phase: FlowPhase): boolean {
  return ['AWAITING_SUMMARY', 'AWAITING_FOLLOWUP', 'REVIEWING_RECORDING', 'REVIEWING_TRANSCRIPT', 'RECORDING'].includes(phase);
}
export function scoreLabel(value: number | null | undefined): string {
  return value == null ? 'Not enough evidence' : `${Math.round(value * 100)}%`;
}
