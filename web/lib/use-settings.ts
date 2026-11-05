'use client';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useAccount } from '../components/auth-gate';
import { DEFAULT_SETTINGS, loadSettings, saveSettings, settingsKey, type LearnerSettings } from './settings-core.ts';

const browserStore = () => { try { return typeof window === 'undefined' ? null : window.localStorage; } catch { return null; } };
const CHANGED = 'audli-settings-changed';

/** The signed-in learner's preferences, read synchronously so the player starts at the right speed without a flash. Saved on every change. */
export function useLearnerSettings(): { settings: LearnerSettings; update: (patch: Partial<LearnerSettings>) => void; saved: boolean | null } {
  const { scope } = useAccount();
  const [settings, setSettings] = useState<LearnerSettings>(() => loadSettings(browserStore(), scope));
  const [saved, setSaved] = useState<boolean | null>(null);
  useEffect(() => {
    const onChange = (e: Event) => { if (!(e instanceof StorageEvent) || e.key === settingsKey(scope)) setSettings(loadSettings(browserStore(), scope)); };
    window.addEventListener('storage', onChange); window.addEventListener(CHANGED, onChange);
    return () => { window.removeEventListener('storage', onChange); window.removeEventListener(CHANGED, onChange); };
  }, [scope]);
  const latest = useRef(settings);
  useEffect(() => { latest.current = settings; }, [settings]);
  const update = useCallback((patch: Partial<LearnerSettings>) => {
    const next = { ...latest.current, ...patch };
    latest.current = next;
    setSettings(next);
    setSaved(saveSettings(browserStore(), scope, next));
    window.dispatchEvent(new Event(CHANGED));
  }, [scope]);
  return { settings, update, saved };
}
export { DEFAULT_SETTINGS };
