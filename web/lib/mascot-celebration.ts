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
  whenInitialized: (fire: () => void) => void = fire => fire(),
) {
  const instance = rive.viewModelByName('AudliMascotControls')?.defaultInstance();
  const trigger = instance?.trigger('celebrate');
  if (!instance || !trigger) throw new Error('Mascot ViewModel trigger unavailable');
  rive.setViewModelInstance(instance);
  rive.bind();
  rive.play('AudliMascotMachine');
  if (celebrate) whenInitialized(() => { if (claim(key, storage)) trigger.trigger(); });
}
