import type { Rive } from '@rive-app/react-canvas';

type PresentationStorage = Pick<Storage, 'getItem' | 'setItem'> | null;

export function createCelebrationGate() {
  const consumed = new Set<string>();
  return (key: string, storage: PresentationStorage) => {
    if (consumed.has(key)) return false;
    consumed.add(key);
    try {
      if (storage?.getItem(key)) return false;
      storage?.setItem(key, '1');
    } catch { /* Fresh-completion flag and in-memory guard still prevent replay. */ }
    return true;
  };
}

export const claimCelebration = createCelebrationGate();
export function celebrationKey(scope: string, lesson: string) {
  return 'audli-mascot-v1:' + JSON.stringify([scope, lesson]);
}
export function presentationStorage(): PresentationStorage {
  try { return window.sessionStorage; } catch { return null; }
}

/** Bind the named ViewModel, never the unrelated classic state-machine inputs. */
export function startMascot(
  rive: Pick<Rive, 'viewModelByName' | 'setViewModelInstance' | 'bind' | 'play'>,
  key: string,
  celebrate: boolean,
  claim = claimCelebration,
  storage = presentationStorage(),
  whenInitialized: (fire: () => boolean) => void = fire => fire(),
) {
  const instance = rive.viewModelByName('AudliMascotControls')?.defaultInstance();
  const trigger = instance?.trigger('celebrate');
  if (!instance || !trigger) throw new Error('Mascot ViewModel trigger unavailable');
  rive.setViewModelInstance(instance);
  rive.bind();
  rive.play('AudliMascotMachine');
  if (celebrate) whenInitialized(() => {
    if (!claim(key, storage)) return false;
    trigger.trigger();
    return true;
  });
}

let prefetched = false;
/** Warm the celebration's runtime, wasm and artwork while the learner is still in wrap-up. Idempotent and best-effort:
 *  presentation only, never throws, skipped when no animation will play. It does not change what is rendered. */
export function prefetchCelebration(reducedMotion: boolean) {
  if (prefetched || reducedMotion || typeof window === 'undefined') return;
  try {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    if ((navigator as Navigator & { connection?: { saveData?: boolean } }).connection?.saveData) return;
  } catch { return; }
  prefetched = true;
  void import('../components/rive-mascot-runtime').catch(() => {});
  for (const url of ['/audli/rive.wasm', '/audli/audli-mascot.riv']) void fetch(url).catch(() => {});
}
