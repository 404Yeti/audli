/** Learner preferences for Audli 2.0 (Figma D15/M15). Pure and dependency-free so it can be tested with `node --experimental-strip-types`.
 *  Only settings the 2.0 player really honours live here; everything else in the design is shown as unavailable, never as a working control. */
export const SETTINGS_SPEEDS = [0.75, 0.9, 1, 1.25] as const;
export const TRANSCRIPT_MODES = ['challenge', 'guided'] as const;   // "Review" (vocabulary help) needs glossary data that does not exist yet
export const TEXT_SIZES = ['default', 'large'] as const;

export type LearnerSettings = {
  transcriptMode: (typeof TRANSCRIPT_MODES)[number];
  speed: (typeof SETTINGS_SPEEDS)[number];
  textSize: (typeof TEXT_SIZES)[number];
  reduceMotion: boolean;
  highContrast: boolean;
};

/** The Figma frame shows Challenge, 1×, Large; the product default is the plainer Challenge, 1×, default size. */
export const DEFAULT_SETTINGS: LearnerSettings = { transcriptMode: 'challenge', speed: 1, textSize: 'default', reduceMotion: false, highContrast: false };

const KEY_PREFIX = 'audli-settings-v1:';
/** One key per signed-in account, so two people on one browser never share preferences. */
export const settingsKey = (scope: string) => `${KEY_PREFIX}${scope}`;

/** Accepts anything (corrupt storage included) and returns a complete, valid settings object. Unknown or invalid fields fall back to the default. */
export function parseSettings(raw: unknown): LearnerSettings {
  let value: unknown = raw;
  if (typeof raw === 'string') { try { value = JSON.parse(raw); } catch { value = null; } }
  const o = (value && typeof value === 'object' ? value : {}) as Record<string, unknown>;
  const pick = <T extends readonly unknown[]>(list: T, v: unknown, fallback: T[number]): T[number] => (list as readonly unknown[]).includes(v) ? (v as T[number]) : fallback;
  return {
    transcriptMode: pick(TRANSCRIPT_MODES, o.transcriptMode, DEFAULT_SETTINGS.transcriptMode),
    speed: pick(SETTINGS_SPEEDS, o.speed, DEFAULT_SETTINGS.speed),
    textSize: pick(TEXT_SIZES, o.textSize, DEFAULT_SETTINGS.textSize),
    reduceMotion: o.reduceMotion === true,
    highContrast: o.highContrast === true,
  };
}

export const serializeSettings = (s: LearnerSettings) => JSON.stringify(parseSettings(s));

type Store = Pick<Storage, 'getItem' | 'setItem'>;
/** Storage can be missing, full or blocked (private windows); every failure means "use the defaults" or "not saved", never an exception. */
export function loadSettings(store: Store | null | undefined, scope: string): LearnerSettings {
  try { return parseSettings(store?.getItem(settingsKey(scope))); } catch { return { ...DEFAULT_SETTINGS }; }
}
export function saveSettings(store: Store | null | undefined, scope: string, s: LearnerSettings): boolean {
  try { store?.setItem(settingsKey(scope), serializeSettings(s)); return !!store; } catch { return false; }
}

export const SPEED_LABEL = (s: number) => `${s}×`;
