'use client';
import { AudliMascot } from './audli-mascot';

export type RiveMascotState = 'idle' | 'celebrate';

/** Static integration boundary until the authentic asset exists. See docs/RIVE-MASCOT.md.
 * Reuse the approved vectors directly: no replacement artwork, runtime or asset request.
 * A static pose also prevents replay on rerenders/restoration and respects reduced motion.
 */
export function RiveMascot({ state = 'idle' }: { state?: RiveMascotState }) {
  return <AudliMascot state={state === 'celebrate' ? 'Success' : 'Idle'} staticPose/>;
}
