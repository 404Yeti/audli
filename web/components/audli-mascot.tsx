'use client';
import type { CSSProperties } from 'react';

export type MascotState = 'Idle' | 'Listening' | 'Thinking' | 'Speaking' | 'Success' | 'Retry';

/** Geometry from Figma Mascot System, node 7:23; state marks follow its storyboard. */
export function AudliMascot({ state = 'Idle', activity = false, small = false }: { state?: MascotState; activity?: boolean; small?: boolean }) {
  return <div className={`audli-mascot ${small ? 'small' : ''}`} data-state={state} data-active={activity} style={{ '--voice-activity': activity ? 1 : .35 } as CSSProperties} aria-hidden="true">
    <svg viewBox="0 0 172.8 151.2" fill="none" xmlns="http://www.w3.org/2000/svg">
      <g className="mascot-body">
        <ellipse cx="86.4" cy="55.44" rx="54" ry="42.48" fill="#14B8A6"/>
        <ellipse cx="86.04" cy="65.52" rx="37.8" ry="29.52" fill="white"/>
        <circle cx="47.52" cy="97.92" r="22.32" fill="#2563EB"/>
        <ellipse cx="123.665" cy="99.9292" rx="22.32" ry="28.08" transform="rotate(10 123.665 99.9292)" fill="#FF6B5B"/>
      </g>
      {state === 'Listening' && <g className="listening-marks" fill="#FACC15"><ellipse cx="146.7" cy="57.6" rx="4.8" ry="10.8" transform="rotate(25 146.7 57.6)"/><ellipse cx="154.8" cy="72" rx="7.2" ry="3.6" transform="rotate(-30 154.8 72)"/></g>}
      {state === 'Thinking' && <g fill="#FACC15">{[70.5,88.5,106.5].map((cx,i) => <circle className="thought-dot" key={cx} cx={cx} cy="5" r={3.6+i*.6} style={{ animationDelay: `${i*.3}s` }}/>)}</g>}
      {state === 'Speaking' && <g className="sound-waves" stroke="#FACC15" strokeWidth="1.8"><ellipse cx="10.8" cy="96" rx="9" ry="20"/><ellipse cx="10.8" cy="96" rx="5" ry="15"/><path d="M166 77c-12 2-12 36 0 38m6-40c-16 2-16 41 0 43"/></g>}
      {state === 'Success' && <g fill="#FACC15"><ellipse cx="128" cy="28" rx="3" ry="8" transform="rotate(25 128 28)"/><ellipse cx="149" cy="33" rx="3" ry="8"/></g>}
      {state === 'Retry' && <text x="142" y="52" fill="#FACC15" fontSize="22" fontWeight="700">↻</text>}
    </svg>
  </div>;
}
