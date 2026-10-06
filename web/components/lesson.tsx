'use client';
import type { ButtonHTMLAttributes, ReactNode } from 'react';
import { motion, useReducedMotion } from 'motion/react';
import { tracks } from './motion-tracks';

export type VoiceState = 'Speaking' | 'Listening' | 'Recording' | 'Thinking';
export function AudliLogo({ small = false, state = 'Idle' }: { small?: boolean; state?: 'Idle' | 'Listening' | 'Thinking' | 'Speaking' | 'Success' | 'Review' }) {
  return <span className={small ? 'logo-small' : 'logo'}><img src={small ? '/audli/greeting.svg' : '/audli/' + (state === 'Review' ? 'reviewmark' : state.toLowerCase()) + '.svg'} alt="" /></span>;
}
export function AudliMotion({ state }: { state: VoiceState }) {
  const reduced = useReducedMotion() !== false;
  const listening = state === 'Listening';
  const ids = listening ? ['97:6','97:7','97:8'] : state === 'Recording' ? ['89:85','89:86','89:87'] : ['89:20','89:21','89:22'];
  const pieces = ['left','band','right'];
  const prefix = state === 'Speaking' ? '' : state.toLowerCase() + '-';
  return <div className="motion-slot" aria-hidden="true"><motion.div className="motion-pieces" {...(reduced || !listening ? {} : tracks['97:4'])}>
    {pieces.map((piece, i) => <motion.div key={piece} className={'piece ' + piece} {...(reduced ? {} : tracks[ids[i]])}><img src={'/audli/' + prefix + piece + '.svg'} alt="" /></motion.div>)}
    {state === 'Thinking' && [1,2,3].map((n) => <motion.div key={n} className={'piece dot dot-' + n} {...(reduced ? {} : tracks['89:' + (152+n)])}><img src={'/audli/dot'+n+'.svg'} alt="" /></motion.div>)}
  </motion.div></div>;
}
export function VoiceSession({ state }: { state: VoiceState | 'Idle' | 'Success' | 'Review' }) {
  return <div className={'voice-session ' + state.toLowerCase()}>{state === 'Idle' || state === 'Success' || state === 'Review' ? <div className="motion-slot"><div className="static-mark"><AudliLogo state={state}/></div></div> : <AudliMotion key={state} state={state}/>}<p role="status">{({Speaking:'Speaking…',Listening:'Listening…',Recording:'Listening to you…',Thinking:'Thinking…',Idle:'Ready to listen',Success:'Ready for the next activity',Review:'Check what I heard'})[state]}</p></div>;
}
export function PrimaryButton({ className = '', ...props }: ButtonHTMLAttributes<HTMLButtonElement>) {
  return <button {...props} className={'primary ' + className}/>;
}
export function LessonCard({ children, duration }: { children: ReactNode; duration?: number }) {
  return <section className="lesson-card"><p className="eyebrow">TODAY’S LISTENING</p><h2>One step closer to understanding.</h2><div className="lesson-meta"><span>English</span><span>At your pace</span><span>{duration ? duration + ' sec' : 'Listening'}</span></div>{children}</section>;
}
export function FeedbackCard({ outcome, children, terminal = false }: { outcome: 'Gap' | 'Misunderstood' | 'Understood'; children: ReactNode; terminal?: boolean }) {
  return <div className={'feedback-card ' + outcome.toLowerCase()}><p className="eyebrow">{outcome === 'Gap' ? 'NOT ENOUGH EVIDENCE' : outcome.toUpperCase()}</p><h3>{outcome === 'Gap' ? terminal ? 'Some details are still unclear.' : 'Let’s check one detail.' : outcome === 'Misunderstood' ? 'Let’s untangle one part.' : 'Let’s keep listening.'}</h3>{children}</div>;
}
export function LoadingState({ children }: { children: ReactNode }) { return <p className="status" role="status">{children}</p>; }
export function RecoveryState({ children }: { children: ReactNode }) { return <div role="alert" className="error">{children}</div>; }
export function MicrophoneControl({ recording, processing, retry, disabled, elapsed, onStart, onFinish, onCancel }: {
  recording: boolean; processing: boolean; retry: boolean; disabled: boolean; elapsed: number;
  onStart: () => void; onFinish: () => void; onCancel: () => void;
}) {
  return <div className={'microphone-control ' + (recording || retry ? 'coral' : processing ? 'processing' : '')}>
    <button className="microphone-icon" aria-label={recording ? 'Stop recording' : 'Start recording'} disabled={disabled || processing} onClick={recording ? onFinish : onStart}><img src={'/audli/' + (recording ? 'stop' : processing ? 'processing' : 'mic') + '.svg'} alt=""/></button>
    <div className="microphone-copy"><strong>{recording ? 'Listening to you…' : processing ? 'Checking your answer' : retry ? 'Try your microphone again' : 'Ready when you are'}</strong><span>{recording ? `${Math.floor(elapsed/60).toString().padStart(2,'0')}:${(elapsed%60).toString().padStart(2,'0')} / 01:59` : processing ? 'Audli is listening for meaning' : 'Tap to speak'}</span></div>
    {recording ? <div className="microphone-actions"><button onClick={onCancel}>Cancel</button><button onClick={onFinish}>Finish</button></div> : !processing && <button className="mic-retry" onClick={onStart} disabled={disabled}>{retry ? 'Retry' : 'AUTO'}</button>}
  </div>;
}
export function HomeNavigation() {
  return <nav className="bottom-nav" aria-label="Main navigation">{['Home','Plan','Review','Settings'].map((name) => <button key={name} aria-current={name === 'Home' ? 'page' : undefined} disabled={name !== 'Home'}><img src={'/audli/'+name.toLowerCase()+'.svg'} alt=""/><span>{name}</span></button>)}</nav>;
}
