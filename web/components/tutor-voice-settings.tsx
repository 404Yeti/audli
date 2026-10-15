'use client';
import { useEffect, useRef, useState } from 'react';
import { LessonLifetime } from '../lib/api';

type Voice = 'original' | 'male';
type Preference = { voice: Voice; available: boolean };
const choices = [{ value: 'original', label: 'Audli — Original' }, { value: 'male', label: 'Audli — Male' }] as const;

export function TutorVoiceSettings() {
  const [selected, setSelected] = useState<Voice>('original');
  const [saved, setSaved] = useState<Voice>('original');
  const [available, setAvailable] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [previewing, setPreviewing] = useState<Voice | null>(null);
  const [previewLabel, setPreviewLabel] = useState('');
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const lifetime = useRef<LessonLifetime | null>(null);
  const audio = useRef<HTMLAudioElement>(null);
  const previewUrl = useRef<string | null>(null);
  const savePending = useRef(false);
  const previewPending = useRef(false);
  useEffect(() => {
    const owner = new LessonLifetime(); lifetime.current = owner;
    const operation = owner.begin();
    void operation.api<Preference>('/profile/tutor-voice').then(value => {
      operation.assertCurrent(); setSelected(value.voice); setSaved(value.voice); setAvailable(value.available);
    }).catch(cause => { if (operation.current) setError((cause as Error).message); })
      .finally(() => { if (operation.current) setLoading(false); operation.release(); });
    const player = audio.current;
    return () => { owner.close(); player?.pause(); if (previewUrl.current) URL.revokeObjectURL(previewUrl.current); };
  }, []);
  async function save() {
    if (!lifetime.current?.current || savePending.current) return;
    savePending.current = true; setSaving(true); setMessage(''); setError('');
    const operation = lifetime.current.begin();
    try {
      const value = await operation.api<Preference>('/profile/tutor-voice', {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ voice: selected }),
      });
      operation.assertCurrent(); setSaved(value.voice); setSelected(value.voice); setAvailable(value.available);
      setMessage('Tutor voice saved. It will apply the next time Audli speaks.');
    } catch (cause) { if (operation.current) setError((cause as Error).message); }
    finally { if (operation.current) { savePending.current = false; setSaving(false); } operation.release(); }
  }
  async function preview(voice: Voice) {
    if (!lifetime.current?.current || previewPending.current) return;
    previewPending.current = true; setPreviewing(voice); setError('');
    const operation = lifetime.current.begin();
    try {
      const url = await operation.audio(`/api/profile/tutor-voice/preview/${voice}`);
      operation.assertCurrent(() => URL.revokeObjectURL(url));
      const player = audio.current;
      if (!player) { URL.revokeObjectURL(url); return; }
      player.pause();
      if (previewUrl.current) URL.revokeObjectURL(previewUrl.current);
      previewUrl.current = url; player.src = url;
      setPreviewLabel(choices.find(choice => choice.value === voice)!.label);
      await player.play(); operation.assertCurrent();
    } catch { if (operation.current) setError('Voice preview is unavailable. Your saved preference is unchanged. Try again later.'); }
    finally { if (operation.current) { previewPending.current = false; setPreviewing(null); } operation.release(); }
  }
  return <div className="tutor-voice-settings">
    <fieldset disabled={loading || saving}><legend>Audli tutor voice</legend>
      <p id="tutor-voice-help">Choose who guides your practice. Listening-passage accents are separate.</p>
      {choices.map(choice => <div className="tutor-voice-choice" key={choice.value}>
        <label><input type="radio" name="tutor-voice" value={choice.value} checked={selected === choice.value} aria-describedby="tutor-voice-help" onChange={() => { setSelected(choice.value); setMessage(''); }}/><span>{choice.label}{choice.value === 'original' && <small>Default</small>}</span></label>
        <button type="button" className="text-button" aria-label={`Preview ${choice.label}`} disabled={!available || previewing !== null} onClick={() => void preview(choice.value)}>{previewing === choice.value ? 'Loading…' : 'Preview'}</button>
      </div>)}
    </fieldset>
    <button className="secondary" disabled={loading || saving || selected === saved} onClick={() => void save()}>{saving ? 'Saving…' : 'Save tutor voice'}</button>
    {loading && <p role="status">Loading voice preference…</p>}
    {!loading && !available && <p>Voice options are temporarily unavailable. Your preference stays saved.</p>}
    <p className="hint">Your choice applies to future tutor speech. If a voice is unavailable, Audli uses its fallback voice.</p>
    <audio ref={audio} controls hidden={!previewLabel} aria-label={`${previewLabel} preview`} onTimeUpdate={event => { if (event.currentTarget.currentTime >= 12) event.currentTarget.pause(); }}/>
    {previewLabel && <p className="hint">Preview: {previewLabel}. Up to 12 seconds.</p>}
    {message && <p role="status">{message}</p>}{error && <p role="alert">{error}</p>}
  </div>;
}
