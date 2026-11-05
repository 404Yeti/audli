import type { Metadata } from 'next';
import Link from 'next/link';
import '@fontsource/fredoka/600.css';
import '@fontsource/nunito/400.css';
import '@fontsource/nunito/700.css';
import styles from './privacy.module.css';

export const metadata: Metadata = { title: 'Privacy · Audli', description: 'What Audli collects, which providers process it, and how long it is kept.' };

/** Keep in step with docs/PRIVACY-POLICY-REVIEW.md. Change this date only when the text changes. */
const PRIVACY_UPDATED = '5 November 2026';
const PRIVACY_CONTACT = 'robbie@audli.ai';

export default function Privacy() {
  return <main className={styles.page} id="privacy">
    <nav aria-label="Privacy navigation" className={styles.back}><Link href="/">← Back to Audli</Link></nav>
    <h1>Privacy</h1>
    <p className={styles.updated}>Last updated {PRIVACY_UPDATED}. Audli is an early beta, so this page describes what the product does today, not what we might do later.</p>

    <section aria-labelledby="short"><h2 id="short">The short version</h2>
      <ul>
        <li>You listen, answer questions and, if you choose, record a short spoken answer.</li>
        <li>A recording is sent to an AI provider to turn it into text. We delete our copy of the audio as soon as the text is saved. We keep the text.</li>
        <li>Lessons you create with the AI Lesson Generator are private to your account.</li>
        <li>We use outside companies to run Audli. They are listed below, with what each one receives.</li>
        <li>AI helps create lessons, write down your spoken answer and check what it says. Multiple choice, fill in the blank and ordering are scored by Audli’s own code, not by AI. Your spoken answer is never part of a score.</li>
      </ul></section>

    <section aria-labelledby="stored"><h2 id="stored">What Audli stores about you</h2>
      <ul>
        <li><strong>Account:</strong> your email address and sign-in details, held by our sign-in provider (Supabase).</li>
        <li><strong>Lessons you create:</strong> the description you type, the settings you choose, and the lesson that is generated (script, questions and audio). These are visible only to your account.</li>
        <li><strong>Your practice:</strong> which lessons you opened, your answers, results, and your progress.</li>
        <li><strong>Spoken answers:</strong> the text of what you said (the transcript) and the notes from checking it against the lesson. Audli does not keep the audio.</li>
      </ul>
      <p>We use this to run the lessons, show your progress and debrief, keep the service safe, and control costs.</p></section>

    <section aria-labelledby="recordings"><h2 id="recordings">Recordings</h2>
      <ul>
        <li>Recording is optional. You can skip the spoken question.</li>
        <li>Your recording is saved privately, away from lesson audio, only long enough to be transcribed. It cannot be opened through any link.</li>
        <li>Audli deletes the recording as soon as the transcript is saved, or when checking fails. A background cleanup removes any recording that was left behind.</li>
        <li>We have not yet confirmed a storage-level automatic expiry on the recordings bucket, so we do not promise a fixed number of days. We will update this page when it is confirmed.</li>
        <li>The transcript stays in your account until your account is deleted.</li>
      </ul></section>

    <section aria-labelledby="providers"><h2 id="providers">Who processes your data</h2>
      <div className={styles.table} role="table" aria-label="Service providers">
        <div role="row" className={styles.head}><span role="columnheader">Provider</span><span role="columnheader">What it receives</span><span role="columnheader">What we know about retention</span></div>
        <div role="row"><span role="cell">OpenAI</span><span role="cell">Text you type to describe a lesson; lesson settings; the script it writes; your recording (to transcribe it); your transcript and the lesson’s key points (to check your spoken answer).</span><span role="cell">We ask OpenAI not to store responses. OpenAI’s documentation says API data is not used to train its models by default and that it may keep abuse-monitoring logs for up to 30 days, with exceptions that include audio transcription. We have not signed a zero-retention agreement. This can change; see OpenAI’s own terms.</span></div>
        <div role="row"><span role="cell">ElevenLabs</span><span role="cell">The text of the generated dialogue, sentence by sentence, to create the lesson voices.</span><span role="cell">ElevenLabs keeps request history under its standard terms. We do not use its zero-retention mode, which is an enterprise option. We cannot currently delete these copies on your behalf.</span></div>
        <div role="row"><span role="cell">Supabase</span><span role="cell">Your sign-in details and all Audli data listed above (database).</span><span role="cell">Held until deleted. Backups may keep deleted data for a period that we have not yet verified.</span></div>
        <div role="row"><span role="cell">Cloudflare (R2)</span><span role="cell">Generated lesson audio (kept) and temporary spoken-answer recordings (deleted as described above).</span><span role="cell">Private storage; nothing is public.</span></div>
        <div role="row"><span role="cell">Render and Vercel</span><span role="cell">Render runs Audli’s servers; Vercel serves the website. Requests pass through them.</span><span role="cell">Operational logs only; we do not write recordings or lesson text to logs.</span></div>
      </div></section>

    <section aria-labelledby="retention"><h2 id="retention">How long we keep things</h2>
      <ul>
        <li>Recordings: deleted right after transcription, as above.</li>
        <li>Transcripts, answers, progress and the lessons you generated: kept until your account is deleted.</li>
        <li>Copies held by providers follow their own terms, shown in the table.</li>
      </ul></section>

    <section aria-labelledby="rights"><h2 id="rights">Your choices and rights</h2>
      <p>You can ask us what we hold about you, ask us to correct it, or ask us to delete your account and its data. Email <a href={`mailto:${PRIVACY_CONTACT}`}>{PRIVACY_CONTACT}</a> from your account email address. Deleting your account does not delete copies that OpenAI or ElevenLabs hold under their own terms. Self-service account deletion and data download are not available in the app yet.</p></section>

    <section aria-labelledby="versions"><h2 id="versions">Audli 1.x and Audli 2.0</h2>
      <p>Audli 2.0 (the listening lessons) is described above. The earlier conversational tutor (Audli 1.x, at <Link href="/classic">/classic</Link>) follows the same rule for recordings: audio is deleted after it is transcribed. It also uses AI providers to hold the conversation and speak to you, and keeps your progress in your account.</p></section>
  </main>;
}
