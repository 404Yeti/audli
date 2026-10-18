'use client';
import dynamic from 'next/dynamic';
import { Component, useEffect, useSyncExternalStore, type ReactNode } from 'react';
import { AudliMascot } from './audli-mascot';
import { celebrationKey, claimCelebration, presentationStorage } from '../lib/mascot-celebration';

export type RiveMascotState = 'idle' | 'celebrate';
export type RiveMascotProps = { completionId: string; scope: string; state?: RiveMascotState; reducedMotion?: boolean; onComplete?: () => void };

const Runtime = dynamic(() => import('./rive-mascot-runtime'), {
  ssr: false,
  loading: () => <AudliMascot state="Success" staticPose/>,
});
function subscribeMotion(changed: () => void) {
  const preference = window.matchMedia('(prefers-reduced-motion: reduce)');
  preference.addEventListener('change', changed);
  return () => preference.removeEventListener('change', changed);
}
const motionSnapshot = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;
const serverMotion = (): boolean | undefined => undefined;

class MascotBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() { return this.state.failed ? <AudliMascot state="Success" staticPose/> : this.props.children; }
}

export function RiveMascot({ completionId, scope, state = 'idle', reducedMotion = false, onComplete }: RiveMascotProps) {
  const systemReduced = useSyncExternalStore(subscribeMotion, motionSnapshot, serverMotion);
  const key = celebrationKey(scope, completionId);
  useEffect(() => {
    // Enabling motion later must not replay a celebration that was suppressed.
    if (state === 'celebrate' && (reducedMotion || systemReduced === true)) {
      claimCelebration(key, presentationStorage());
      onComplete?.();
    }
  }, [key, state, reducedMotion, systemReduced, onComplete]);
  if (systemReduced !== false || reducedMotion) return <AudliMascot state="Success" staticPose/>;
  return <MascotBoundary key={key}><Runtime identity={key} celebrate={state === 'celebrate'} onComplete={onComplete}/></MascotBoundary>;
}
