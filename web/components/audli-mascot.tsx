'use client';
import { useId, type CSSProperties } from 'react';

export type MascotState = 'Idle' | 'Listening' | 'Thinking' | 'Speaking' | 'Success' | 'Retry';

// Mirror around the approved body's horizontal bounds (25.2 … 146.18),
// keeping equal clearance from its intentionally asymmetric ear pieces.
const WAVE_MIRROR = 'translate(171.38 0) scale(-1 1)';
function WavePair({ id }: { id: string }) {
  return <g className="sound-wave-motion"><use href={`#${id}`}/><use href={`#${id}`} transform={WAVE_MIRROR}/></g>;
}

/** Geometry from Figma Mascot System, node 7:23; state marks follow its storyboard. */
export function AudliMascot({ state = 'Idle', activity = false, small = false }: { state?: MascotState; activity?: boolean; small?: boolean }) {
  const waveId = useId();
  return <div className={`audli-mascot ${small ? 'small' : ''}`} data-state={state} data-active={activity} style={{ '--voice-activity': activity ? 1 : .35 } as CSSProperties} aria-hidden="true">
    <svg viewBox="0 0 172.8 151.2" fill="none" xmlns="http://www.w3.org/2000/svg">
      <defs><path id={waveId} d="M160 82 C152 84 152 108 160 110 M166 77 C156 80 156 112 166 115" fill="none" stroke="#FACC15" strokeWidth="1.8" strokeLinecap="round"/></defs>
      <g className="mascot-pose"><g className="mascot-body">
        <ellipse cx="86.4" cy="55.44" rx="54" ry="42.48" fill="#14B8A6"/>
        <ellipse cx="86.04" cy="65.52" rx="37.8" ry="29.52" fill="white"/>
        <circle cx="47.52" cy="97.92" r="22.32" fill="#2563EB"/>
        <ellipse cx="123.665" cy="99.9292" rx="22.32" ry="28.08" transform="rotate(10 123.665 99.9292)" fill="#FF6B5B"/>
      </g></g>
      <g data-visible={state === 'Listening'} className="mascot-cue listening-marks"><WavePair id={waveId}/></g>
      <g data-visible={state === 'Thinking'} className="mascot-cue thinking-marks" fill="#FACC15">{[70.5,88.5,106.5].map((cx,i) => <circle className="thought-dot" key={cx} cx={cx} cy="5" r={3.6+i*.6} style={{ animationDelay: `${i*.3}s` }}/>)}</g>
      <g data-visible={state === 'Speaking'} className="mascot-cue sound-waves"><WavePair id={waveId}/></g>
      <g data-visible={state === 'Success'} className="mascot-cue success-marks" fill="#FACC15"><ellipse cx="128" cy="28" rx="3" ry="8" transform="rotate(25 128 28)"/><ellipse cx="149" cy="33" rx="3" ry="8"/></g>
      <g data-visible={state === 'Retry'} className="mascot-cue retry-mark"><text x="142" y="52" fill="#FACC15" fontSize="22" fontWeight="700">↻</text></g>
    </svg>
  </div>;
}
