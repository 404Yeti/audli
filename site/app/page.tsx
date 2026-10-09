const accessHref = 'https://app.audli.ai';
const feedbackHref = 'https://tally.so/r/pblj5V';
const reportHref = 'https://tally.so/r/5By6QM';

const steps = [
  { number: '01', title: 'Listen to real life', copy: 'Everyday conversations, stories and accents you actually encounter.', tone: 'blue' },
  { number: '02', title: 'Show what you understood', copy: 'Respond naturally by voice. Audli checks your comprehension.', tone: 'coral' },
  { number: '03', title: 'Grow at your pace', copy: 'Your next session adjusts to what you need, not a fixed textbook.', tone: 'yellow' },
];

const benefits = [
  { title: 'Real-world listening', copy: 'Practice useful spoken English.', tone: 'blue' },
  { title: 'Adaptive difficulty', copy: 'Find the right pace for you.', tone: 'teal' },
  { title: 'Voice-first learning', copy: 'Speak your answer, not type it.', tone: 'coral' },
];

function Mascot({ compact = false }: { compact?: boolean }) {
  return <div className={`mascot ${compact ? 'compact' : ''}`} aria-label="Audli listening mascot" role="img">
    <span className="headband"/><span className="opening"/><span className="ear ear-left"/><span className="ear ear-right"/>
    <span className="sound sound-one"/><span className="sound sound-two"/>
  </div>;
}

function Preview() {
  return <div className="phone" aria-label="Audli app preview">
    <div className="phone-top"><span>9:41</span><span>● ● ●</span></div>
    <div className="phone-brand">audli</div>
    <div className="preview-copy"><strong>Ready to train<br/>your ears?</strong><span>Your next listening session is ready.</span></div>
    <Mascot compact/>
    <button type="button" tabIndex={-1}>Start listening</button>
    <div className="phone-nav"><span>⌂<small>Home</small></span><span>◉<small>Practice</small></span><span>◎<small>Progress</small></span></div>
  </div>;
}

export default function Home() {
  return <main>
    <header className="site-header">
      <a className="wordmark" href="#top" aria-label="Audli home">audli</a>
      <nav aria-label="Main navigation"><a href="#how">How it works</a><a href="#why">Why Audli</a></nav>
      <a className="button button-dark header-cta" href={accessHref}>Try the early beta</a>
    </header>

    <section className="hero" id="top">
      <div className="hero-copy">
        <p className="eyebrow">LISTEN FIRST. LEARN NATURALLY.</p>
        <h1>Eyes optional.<br/>Ears essential.</h1>
        <p className="lede">Understand real spoken English with short, personalized AI listening sessions that adapt to your pace.</p>
        <a className="button button-teal" href={accessHref}>Try the early beta <span aria-hidden="true">→</span></a>
        <p className="note beta-note">Audli is in early beta. Try a listening lesson and help us improve. <a href={feedbackHref}>Send feedback →</a></p>
      </div>
      <Mascot/>
    </section>

    <section className="steps section" id="how">
      <div className="section-heading"><h2>A little listening goes a long way.</h2><p>Three simple steps. One more confident you.</p></div>
      <div className="step-grid">{steps.map(step => <article className="step-card" key={step.number}>
        <span className={`number ${step.tone}`}>{step.number}</span><h3>{step.title}</h3><p>{step.copy}</p>
      </article>)}</div>
    </section>

    <section className="lesson" aria-labelledby="lesson-title">
      <div><h2 id="lesson-title">A listening lesson,<br/>made for you.</h2><p>From slower speech to everyday accents, Audli helps you understand what you hear — and know what to practice next.</p></div>
      <Preview/>
    </section>

    <section className="benefits section" id="why">
      <div className="section-heading"><h2>Built for ears, not screen time.</h2><p>Short sessions. Personal feedback. More confidence in real conversations.</p></div>
      <div className="benefit-grid">{benefits.map(item => <article key={item.title}>
        <span className={`benefit-dot ${item.tone}`}/><h3>{item.title}</h3><p>{item.copy}</p>
      </article>)}</div>
    </section>

    <section className="testimonial section" aria-labelledby="testimonial-title">
      <div className="section-heading"><p className="eyebrow">FROM OUR EARLY LEARNERS</p><h2 id="testimonial-title">A first listen. A little feedback.</h2></div>
      <figure className="testimonial-card">
        <blockquote>“I think its a great app and i am very happy to use it Robert, thank you very much!”</blockquote>
        <figcaption><span className="learner-mark" aria-hidden="true">♡</span><div><strong>Early beta learner</strong><span>Feedback from a student testing Audli</span></div></figcaption>
      </figure>
    </section>

    <section className="beta-feedback section" aria-labelledby="feedback-title">
      <div className="section-heading"><h2 id="feedback-title">Help shape Audli.</h2><p>Trying the beta? We’d love to hear how it went.</p></div>
      <div className="feedback-grid">
        <a className="feedback-card" href={feedbackHref}><h3>Early beta feedback <span aria-hidden="true">↗</span></h3><p>Tell us what worked, what felt confusing, and how your listening lesson went.</p></a>
        <a className="feedback-card" href={reportHref}><h3>Report a bug or suggest a feature <span aria-hidden="true">↗</span></h3><p>Something not working? Have an idea? Help us make Audli better.</p></a>
      </div>
    </section>

    <section className="footer-cta"><div><h2>Ready to hear the difference?</h2><p>Try the early beta. Tell us what works and what could be better.</p><a className="feedback-link" href={feedbackHref}>Send feedback →</a></div><a className="button button-white" href={accessHref}>Try the early beta</a></section>
    <footer><span>Audli · Eyes optional. Ears essential.</span><div className="footer-links"><a href={feedbackHref}>Early beta feedback</a><a href={reportHref}>Report a bug or suggest a feature</a><a href="mailto:robbie@audli.ai">Contact</a></div></footer>
  </main>;
}
