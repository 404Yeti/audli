'use client';
import { useEffect, useRef, useState } from 'react';
import { LessonLifetime } from '../lib/api';

export type AccentPreferences = { status: 'unspecified' | 'preferred' | 'no_preference'; accents: string[] };
const choices = [['british', 'British'], ['australian', 'Australian'], ['irish', 'Irish']] as const;

export function AccentSettings({ preferences, onSaved }: { preferences?: AccentPreferences; onSaved: (value: AccentPreferences) => void }) {
  const [selected, setSelected] = useState<string[]>(preferences?.accents ?? []);
  const [none, setNone] = useState(preferences?.status === 'no_preference');
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const lifetime = useRef<LessonLifetime | null>(null);
  useEffect(() => {
    const owner = new LessonLifetime(); lifetime.current = owner;
    return () => owner.close();
  }, []);
  const saved = preferences?.status === 'no_preference' ? 'No particular accent difficulty'
    : preferences?.status === 'preferred' ? preferences.accents.map(accent => choices.find(([key]) => key === accent)?.[1] ?? accent.replaceAll('_', ' ')).join(', ') || 'Another accent'
    : 'Not answered yet';
  async function save() {
    if (!lifetime.current?.current) return;
    const operation = lifetime.current.begin();
    setSaving(true); setMessage(''); setError('');
    try {
      const value = await operation.api<AccentPreferences>('/profile/accent-preferences', {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ accents: none ? [] : choices.map(([accent]) => accent).filter(accent => selected.includes(accent)) }),
      });
      operation.assertCurrent(); onSaved(value); setSelected(value.accents); setNone(value.status === 'no_preference');
      setMessage('Accent preferences saved.');
    } catch (cause) {
      if (operation.current) setError((cause as Error).message);
    } finally {
      if (operation.current) setSaving(false);
      operation.release();
    }
  }
  return <div className="accent-settings">
    <p>Saved: {saved}.</p>
    <fieldset disabled={saving}><legend>Which accents do you find difficult?</legend>
      {choices.map(([accent, label]) => <label className="toggle" key={accent}><input type="checkbox" checked={!none && selected.includes(accent)} onChange={event => {
        setNone(false); setMessage('');
        setSelected(event.target.checked ? [...selected, accent] : selected.filter(value => value !== accent));
      }}/>{label}</label>)}
      <label className="toggle"><input type="checkbox" checked={none} onChange={event => {
        setNone(event.target.checked); if (event.target.checked) setSelected([]); setMessage('');
      }}/>No particular accent difficulty</label>
    </fieldset>
    <p>Audli may offer practice with these accents. You choose whether to use one for the next passage.</p>
    <button className="secondary" disabled={saving || (!none && !choices.some(([accent]) => selected.includes(accent)))} onClick={() => void save()}>{saving ? 'Saving…' : 'Save accent preferences'}</button>
    {message && <p role="status">{message}</p>}{error && <p role="alert">{error}</p>}
  </div>;
}
