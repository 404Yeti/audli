'use client';
import { useEffect, useRef, useState, type RefObject } from 'react';
import { LessonLifetime, type LessonOperation } from '../lib/api';
import { ReviewImprovement } from './review-improvement';
import { browserTimezone, reviewDate, type ReviewDashboard, type ReviewLesson, type LessonReport, type PassageReport, type ReviewUnit, type SavedAnswer } from '../lib/review';

const groups: { status: ReviewUnit['status']; title: string; explanation: string }[] = [
  { status: 'demonstrated', title: 'What you understood well', explanation: 'Meaning demonstrated in your responses.' },
  { status: 'misunderstood', title: 'Incorrect understanding', explanation: 'Your response showed a different meaning from the passage.' },
  { status: 'partial', title: 'Incomplete understanding', explanation: 'Your response demonstrated part of this meaning.' },
  { status: 'not_demonstrated', title: 'Information not demonstrated', explanation: 'There was not enough evidence to know whether you understood this information. This does not mean you misunderstood it.' },
];

export function ReviewHub() {
  const owner = useRef<LessonLifetime | null>(null);
  const pending = useRef<LessonOperation | null>(null);
  const [dashboard, setDashboard] = useState<ReviewDashboard | null>(null);
  const [report, setReport] = useState<LessonReport | null>(null);
  const [selected, setSelected] = useState<ReviewLesson | null>(null);
  const [patterns, setPatterns] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const heading = useRef<HTMLHeadingElement | null>(null);
  const returnFocus = useRef<string | null>(null);
  const container = useRef<HTMLElement | null>(null);
  const cursor = useRef<string | null>(null);
  const zone = useRef<string | null>(null);
  const focusInteraction = useRef(0);
  const focusNavigation = useRef(0);

  useEffect(() => {
    const interacted = () => { focusInteraction.current++; };
    // A delayed navigation focus must not override a subsequent keyboard,
    // pointer or focus change, including while the report request is pending.
    for (const event of ['keydown', 'pointerdown', 'focusin']) document.addEventListener(event, interacted, true);
    return () => {
      for (const event of ['keydown', 'pointerdown', 'focusin']) document.removeEventListener(event, interacted, true);
    };
  }, []);

  function navigationFocus(focus: () => void) {
    const navigation = ++focusNavigation.current, interaction = focusInteraction.current;
    return () => requestAnimationFrame(() => {
      if (owner.current?.current && navigation === focusNavigation.current && interaction === focusInteraction.current) focus();
    });
  }

  useEffect(() => {
    const lifetime = new LessonLifetime(); owner.current = lifetime;
    zone.current = browserTimezone();
    const operation = lifetime.begin(); pending.current = operation;
    const query = zone.current ? `?timezone=${encodeURIComponent(zone.current)}` : '';
    void operation.api<ReviewDashboard>(`/review${query}`).then(data => {
      operation.assertCurrent(); setDashboard(data); setLoading(false);
    }).catch(e => { if (operation.current) { setError((e as Error).message); setLoading(false); } }).finally(() => operation.release());
    return () => lifetime.close();
  }, []);

  function operation() {
    if (!owner.current?.current) return null;
    pending.current?.cancel();
    const next = owner.current.begin(); pending.current = next;
    setLoading(true); setError('');
    return next;
  }
  async function load(more = false) {
    const request = operation(); if (!request) return;
    cursor.current = more ? dashboard?.next_cursor ?? null : null;
    const params = new URLSearchParams();
    if (zone.current) params.set('timezone', zone.current);
    if (cursor.current) params.set('cursor', cursor.current);
    try {
      const data = await request.api<ReviewDashboard>(`/review?${params}`);
      request.assertCurrent();
      setDashboard(previous => more && previous ? { ...data, lessons: [...previous.lessons, ...data.lessons.filter(item => !previous.lessons.some(old => old.kind === item.kind && old.id === item.id))] } : data);
    } catch (e) { if (request.current) setError((e as Error).message); }
    finally { if (request.current) setLoading(false); request.release(); }
  }
  async function open(item: ReviewLesson, button?: HTMLButtonElement) {
    if (button) returnFocus.current = `${item.kind}:${item.id}`;
    const request = operation(); if (!request) return;
    const focusReport = navigationFocus(() => { if (request.current && pending.current === request) heading.current?.focus(); });
    setSelected(item); setReport(null); setPatterns(false);
    try {
      const data = await request.api<LessonReport>(`/review/${item.kind}/${encodeURIComponent(item.id)}`);
      request.assertCurrent(); setReport(data);
    } catch (e) { if (request.current) setError((e as Error).message); }
    finally { if (request.current) { setLoading(false); focusReport(); } request.release(); }
  }
  function back() {
    pending.current?.cancel(); setSelected(null); setReport(null); setPatterns(false); setLoading(false); setError('');
    navigationFocus(() => {
      const button = Array.from(container.current?.querySelectorAll<HTMLButtonElement>('[data-review-focus]') ?? []).find(element => element.dataset.reviewFocus === returnFocus.current);
      (button ?? heading.current)?.focus();
    })();
  }
  const timezone = dashboard?.summary.timezone_used ?? 'UTC';
  return <section ref={container} className="shell-view review-hub" aria-label="Listening review">
    {(selected || patterns) && <button className="text-button review-back" onClick={back}>‹ Back to Review</button>}
    <h1 ref={heading} tabIndex={-1}>{selected ? report?.lesson.title ?? selected.title : patterns ? 'Your listening patterns' : 'Your listening journey'}</h1>
    {!selected && !patterns && <p className="review-subtitle">See what you&apos;re understanding and where you&apos;re growing.</p>}
    {loading && <p role="status">{selected ? 'Loading your lesson report…' : 'Loading your listening review…'}</p>}
    {error && <div className="review-card review-error" role="alert"><p>{error}</p><button className="secondary" disabled={loading} onClick={() => selected ? void open(selected) : void load()}>Retry review</button></div>}
    {selected ? report && <>
      <p className="review-subtitle">{report.lesson.completed ? reviewDate(report.lesson.completed_at, timezone) : 'Assessed practice from an unfinished lesson'}{report.lesson.levels.length > 0 && ` · ${report.lesson.levels.join(' / ')} material`}</p>
      {report.lesson.topics.length > 0 && <p>Topic: {report.lesson.topics.join(' · ')}</p>}
      <aside className="review-card review-note"><h2>How Audli knows</h2><p>These observations come from your saved responses and final assessment evidence. Some details may be unavailable for older lessons.</p><p>Material levels describe the passages you practiced, rather than a proficiency certification.</p></aside>
      {!report.passages.length && <div className="review-card"><h2>Limited review</h2><p>Detailed assessment unavailable for this lesson. Your completed lesson is still part of your history.</p></div>}
      {report.passages.map(passage => <Passage key={passage.exercise_id} passage={passage}/>)}
    </> : patterns ? dashboard && <>
      <p className="review-subtitle">Patterns across lessons, grounded in saved evidence.</p>
      <div className="review-card"><h2>What your responses show</h2><p>{dashboard.insight}</p></div>
      <dl className="review-pattern-list">{dashboard.patterns.map(pattern => <div className="review-card" key={pattern.dimension}><dt>{pattern.title}</dt><dd><span className={pattern.label === 'Consistent strength' ? 'review-strength' : ''}>{pattern.label}</span>
        {pattern.observations > 0 && <details><summary>Evidence behind this pattern</summary><p>{pattern.observations} completed lessons across {pattern.practice_days} practice days, from {pattern.first_date} to {pattern.last_date}.</p><p>{pattern.context}</p></details>}
      </dd></div>)}</dl>
      <div className="review-card"><h2>Patterns take time</h2><p>We look at the latest ten completed lessons. A pattern needs at least three fully observed lessons across two days with the same recorded difficulty and passage accent.</p><p>Unknown information is never a failure. These labels describe recurring observations, not a measured improvement in ability.</p><p>Review shows what happened. Your Plan shows what comes next.</p></div>
    </> : dashboard && <>
      <div className="review-card review-summary" aria-label="Practice summary"><dl><div><dt>Completed lessons</dt><dd>{dashboard.summary.completed_lessons}</dd></div><div><dt>Days practiced</dt><dd>{dashboard.summary.practice_days}</dd></div>{dashboard.summary.current_practice_level && <div><dt>Current practice level</dt><dd>{dashboard.summary.current_practice_level}</dd></div>}</dl>
        <details><summary>About your practice totals</summary><p>Lessons can contain several assessed passages. Follow-up answers do not count as additional lessons.</p>
          {dashboard.summary.earlier_practice > 0 && <p>Your total includes {dashboard.summary.structured_lessons} structured lessons and {dashboard.summary.earlier_practice} earlier standalone listening sessions, counted once each.</p>}
          <p>Practice days use completed lesson timestamps in {timezone}{dashboard.summary.timezone_fallback ? ', our UTC fallback because your timezone was unavailable' : ', your current browser timezone'}. Historical travel timezones are not recorded.</p>
          {dashboard.summary.undated_lessons > 0 && <p>{dashboard.summary.undated_lessons} completed lessons have no reliable completion date and are excluded from the day count.</p>}
        </details>
      </div>
      {!dashboard.summary.completed_lessons && <div className="review-card"><h2>Your journey starts here</h2><p>Complete a listening lesson to see your first review.</p></div>}
      <div className="review-card review-insight"><h2>What your responses show</h2><p>{dashboard.insight}</p><button className="text-button" data-review-focus="patterns" onClick={() => { returnFocus.current = 'patterns'; const focusPatterns = navigationFocus(() => heading.current?.focus()); setPatterns(true); focusPatterns(); }}>Explore listening patterns <span aria-hidden="true">›</span></button></div>
      {dashboard.lessons.length > 0 && <section aria-labelledby="review-history-title"><h2 id="review-history-title">Completed lessons</h2><p className="review-caption">Newest first. Passages vary in difficulty, so this history does not combine scores into a progress percentage.</p><LessonList items={dashboard.lessons} timezone={timezone} open={open}/>
        {dashboard.next_cursor && <button className="secondary" disabled={loading} onClick={() => void load(true)}>Show older lessons</button>}
      </section>}
      {dashboard.assessed_practice.length > 0 && <section aria-labelledby="review-practice-title"><h2 id="review-practice-title">Assessed practice</h2><p>These passages are ready to review. Their unfinished lessons are excluded from your completed totals.</p><LessonList items={dashboard.assessed_practice} timezone={timezone} open={open}/></section>}
    </>}
  </section>;
}

function LessonList({ items, timezone, open }: { items: ReviewLesson[]; timezone: string; open: (item: ReviewLesson, button?: HTMLButtonElement) => Promise<void> }) {
  return <ol className="review-lessons">{items.map(item => <li key={`${item.kind}:${item.id}`}><button className="review-card review-lesson-link" data-review-focus={`${item.kind}:${item.id}`} onClick={event => void open(item, event.currentTarget)}>
    <span className="review-caption">{item.completed ? reviewDate(item.completed_at, timezone) : 'Unfinished lesson'}{item.kind === 'earlier_practice' && ' · Earlier listening practice'}</span>
    <span className="review-lesson-title">{item.title}<span aria-hidden="true">›</span></span>
    {item.levels.length > 0 && <span className="review-caption">{item.levels.join(' / ')} material</span>}<span className="review-takeaway">{item.takeaway}</span>
  </button></li>)}</ol>;
}

function Passage({ passage }: { passage: PassageReport }) {
  const owner = useRef<LessonLifetime | null>(null);
  const audio = useRef<HTMLAudioElement | null>(null);
  const clip = useRef<string | null>(null);
  const [transcript, setTranscript] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [transcriptLoading, setTranscriptLoading] = useState(false);
  const [transcriptError, setTranscriptError] = useState('');
  const [audioLoading, setAudioLoading] = useState(false);
  const [audioError, setAudioError] = useState('');
  useEffect(() => {
    const lifetime = new LessonLifetime(); owner.current = lifetime;
    const player = audio.current;
    return () => { lifetime.close(); player?.pause(); if (player) player.removeAttribute('src'); if (clip.current) URL.revokeObjectURL(clip.current); };
  }, []);
  async function readTranscript() {
    if (!owner.current?.current || transcriptLoading) return;
    setExpanded(true); setTranscriptError('');
    if (transcript) return;
    const operation = owner.current.begin(); setTranscriptLoading(true);
    try { const data = await operation.api<{ script: string }>(passage.transcript_url.replace(/^\/api/, '')); operation.assertCurrent(); setTranscript(data.script); }
    catch (e) { if (operation.current) setTranscriptError((e as Error).message); }
    finally { if (operation.current) setTranscriptLoading(false); operation.release(); }
  }
  async function replay() {
    if (!owner.current?.current || audioLoading || !passage.audio_url) return;
    const operation = owner.current.begin(); setAudioLoading(true); setAudioError('');
    try {
      if (!clip.current) clip.current = await operation.audio(passage.audio_url);
      operation.assertCurrent();
      if (audio.current) { audio.current.src = clip.current; await audio.current.play(); operation.assertCurrent(); }
    } catch { if (operation.current) setAudioError('The original recording is unavailable right now. You can retry; Audli will not generate replacement audio.'); }
    finally { if (operation.current) setAudioLoading(false); operation.release(); }
  }
  return <article className="review-passage" aria-labelledby={`passage-${passage.exercise_id}`}>
    <h2 id={`passage-${passage.exercise_id}`}>{passage.title}</h2>
    {(passage.level || passage.accent || passage.accent_fallback) && <p>{passage.level && `${passage.level} material`}{passage.accent && ` · Recorded accent: ${passage.accent}`}{passage.accent_fallback && ' · The requested accent was unavailable; the passage used a fallback voice.'}</p>}
    {passage.answers && <HeardAnswers answers={passage.answers}>{passage.improvement && <ReviewImprovement exerciseId={passage.exercise_id} initial={passage.improvement}/>}</HeardAnswers>}
    <div className="review-card">
      {!passage.assessment_available && <p>Detailed assessment unavailable for this passage. Any available saved evidence appears below.</p>}
      {groups.map(group => {
        const units = passage.units.filter(unit => unit.status === group.status);
        if (!units.length) return null;
        return <section className="review-evidence-group" key={group.status}>{group.status === groups.find(candidate => candidate.status !== 'demonstrated' && passage.units.some(unit => unit.status === candidate.status))?.status && <h3>What needs practice</h3>}<h3 className={group.status === 'demonstrated' ? 'review-strength' : ''}>{group.title}</h3><p className="review-caption">{group.explanation}</p><ul>{units.map(unit => <li key={`${unit.dimension}:${unit.index}`}><p>{unit.meaning}</p>{unit.evidence && <blockquote><span className="review-caption">From your response: </span>{unit.evidence}</blockquote>}</li>)}</ul></section>;
      })}
      <h3>Suggested next focus</h3><p>{passage.next_focus}</p>
      {passage.saved_feedback && <details><summary>Saved assessment feedback</summary><p>{passage.saved_feedback}</p></details>}
      {passage.adaptation_reason && <details><summary>Why the difficulty was adjusted or kept stable</summary><p>{passage.adaptation_reason}</p></details>}
    </div>
    <div className="review-card"><button className="text-button" aria-expanded={expanded} aria-controls={`transcript-${passage.exercise_id}`} onClick={() => expanded ? setExpanded(false) : void readTranscript()}>Passage transcript <span aria-hidden="true">{expanded ? '▴' : '▾'}</span></button>
      {expanded && <div id={`transcript-${passage.exercise_id}`}>{transcriptLoading && <p role="status">Loading transcript…</p>}{transcriptError && <div role="alert"><p>{transcriptError}</p><button className="text-button" onClick={() => void readTranscript()}>Retry transcript</button></div>}{transcript && <p className="review-script">{transcript}</p>}</div>}
    </div>
    <div className="review-card"><h3>Original recording</h3>{passage.audio_url ? <><button className="text-button" disabled={audioLoading} onClick={() => void replay()}>Replay original passage</button>{audioLoading && <p role="status">Loading original recording…</p>}<OriginalPlayer audio={audio} title={passage.title}/></> : <p>The original recording is no longer available. Your transcript and saved assessment remain accessible.</p>}{audioError && <p role="alert">{audioError}</p>}</div>
  </article>;
}

/** The saved spoken response, verbatim. Wording and grammar are shown as heard, never corrected here. */
function HeardAnswers({ answers, children }: { answers: SavedAnswer[]; children?: React.ReactNode }) {
  return <section className="review-card review-heard" aria-label="What Audli heard"><h3>What Audli heard</h3>
    {answers.length ? <>
      {answers.map((answer, index) => <div className="review-answer" key={answer.attempt_id}>
        {answer.question && <p className="review-caption">In reply to: {answer.question}</p>}
        <p className="review-script">{answer.text}</p>
        {answer.uncertain && <p className="review-caption">Speech recognition was unsure about this response, so it may differ from what you said.</p>}
        {index === 0 && children}
      </div>)}
      <p className="review-caption">A transcript of your spoken response. It can differ slightly from your exact words.</p>
    </> : <p>Your saved response isn&apos;t available for this older lesson.</p>}
  </section>;
}

function OriginalPlayer({ audio, title }: { audio: RefObject<HTMLAudioElement | null>; title: string }) {
  return <audio ref={audio} controls preload="none" aria-label={`Original recording: ${title}`}/>;
}
