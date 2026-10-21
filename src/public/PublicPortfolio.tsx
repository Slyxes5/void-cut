import './public-portfolio.css';

const repositoryUrl = 'https://github.com/Slyxes5/void-cut';

const features = [
  {
    number: '01',
    icon: '◉',
    title: 'Find the moment',
    description: 'Review strong moments from long videos and shape them into a focused short-form story.',
  },
  {
    number: '02',
    icon: 'Aa',
    title: 'Make it yours',
    description: 'Refine captions, framing, music, and branding in one editing workspace.',
  },
  {
    number: '03',
    icon: '↗',
    title: 'Ready to share',
    description: 'Render vertical clips and export the finished video for your social channels.',
  },
];

export default function PublicPortfolio() {
  return (
    <main className="portfolio-page">
      <header className="portfolio-nav">
        <a className="portfolio-wordmark" href="#top" aria-label="Void Cut home">
          <img src="/void-cut-logo.png" alt="" />
          <span>VOID CUT</span>
        </a>
        <nav aria-label="Main navigation">
          <a href="#workflow">Workflow</a>
          <a href="#about">About</a>
        </nav>
        <a className="portfolio-source-link" href={repositoryUrl} target="_blank" rel="noreferrer">
          View source <span aria-hidden="true">↗</span>
        </a>
      </header>

      <section className="portfolio-hero" id="top">
        <div className="portfolio-hero-copy">
          <p className="portfolio-eyebrow"><span /> VIDEO WORKFLOW · BUILT FOR CREATORS</p>
          <h1>Make every second <em>worth watching.</em></h1>
          <p className="portfolio-lede">
            Turn long-form video into short clips with assisted moment discovery, expressive captions, and a focused editing studio.
          </p>
          <div className="portfolio-actions">
            <a className="portfolio-button portfolio-button-primary" href="#workflow">Explore the workflow <span aria-hidden="true">↓</span></a>
            <a className="portfolio-button portfolio-button-quiet" href={repositoryUrl} target="_blank" rel="noreferrer">Explore on GitHub <span aria-hidden="true">↗</span></a>
          </div>
          <p className="portfolio-private-note"><span aria-hidden="true">⌑</span> Public project preview · The editing workspace stays private</p>
        </div>

        <div className="portfolio-preview" aria-label="Illustration of the Void Cut editing workflow">
          <div className="preview-window-bar">
            <div className="preview-window-dots"><i /><i /><i /></div>
            <span>VOID CUT <b>/</b> STUDIO PREVIEW</span>
            <span className="preview-window-status"><i /> READY</span>
          </div>
          <div className="preview-content">
            <div className="preview-sidebar">
              <span className="preview-side-mark">VC</span>
              <span className="preview-side-icon preview-side-active">◫</span>
              <span className="preview-side-icon">◷</span>
              <span className="preview-side-icon">⚙</span>
            </div>
            <div className="preview-workspace">
              <div className="preview-heading"><div><small>YOUR PROJECT</small><strong>Ideas worth sharing</strong></div><span>9:16 <b>⌄</b></span></div>
              <div className="preview-video-card">
                <div className="preview-sun" />
                <div className="preview-person"><i /><b /></div>
                <span className="preview-play">▶</span>
                <div className="preview-caption">THIS IS THE<br /><strong>TURNING POINT.</strong></div>
                <div className="preview-time">00:18 <span>/ 00:32</span></div>
              </div>
              <div className="preview-clip-row"><span className="preview-clip-dot" /><div><small>SELECTED MOMENT</small><strong>The turning point</strong></div><b>00:18 — 00:32</b></div>
              <div className="preview-timeline"><div className="preview-timeline-label"><span>CAPTIONS</span><span>00:18 ───────── 00:32</span></div><div className="preview-track"><i /><i /><i /><i /><i /><i /><i /><i /><i /><i /><i /><i /><i /><i /><i /><i /><i /><i /><i /><i /></div><div className="preview-track preview-track-audio"><i /><i /><i /><i /><i /><i /><i /><i /><i /><i /><i /><i /><i /><i /><i /><i /><i /><i /><i /><i /></div></div>
            </div>
          </div>
          <div className="preview-float-card"><span>✳</span><div><small>MOMENT DISCOVERED</small><strong>Strong opening · 92%</strong></div><b>↗</b></div>
        </div>
        <div className="portfolio-orbit portfolio-orbit-one" />
        <div className="portfolio-orbit portfolio-orbit-two" />
      </section>

      <section className="portfolio-workflow" id="workflow">
        <div className="portfolio-section-heading">
          <p className="portfolio-eyebrow">FROM LONG VIDEO TO SHORT STORY</p>
          <h2>A calmer way to cut.</h2>
          <p>Three clear steps take an idea from raw footage to a clip ready for the feed.</p>
        </div>
        <div className="portfolio-feature-grid">
          {features.map((feature) => (
            <article className="portfolio-feature" key={feature.number}>
              <div className="portfolio-feature-top"><span className="portfolio-feature-icon">{feature.icon}</span><span>{feature.number}</span></div>
              <h3>{feature.title}</h3>
              <p>{feature.description}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="portfolio-about" id="about">
        <div className="portfolio-about-mark">VC<span>.</span></div>
        <div>
          <p className="portfolio-eyebrow">A PERSONAL CREATOR TOOL</p>
          <h2>Made to keep the creative work yours.</h2>
          <p>Void Cut is an independent project for turning video into concise, expressive clips. The public page is a portfolio preview; the editor and its processing tools are available only in the owner’s private workspace.</p>
        </div>
        <a className="portfolio-about-link" href={repositoryUrl} target="_blank" rel="noreferrer">See the project on GitHub <span aria-hidden="true">↗</span></a>
      </section>

      <footer className="portfolio-footer">
        <a className="portfolio-wordmark" href="#top"><img src="/void-cut-logo.png" alt="" /><span>VOID CUT</span></a>
        <p>Find the moment. Shape the story.</p>
        <span>Public showcase · Private editor</span>
      </footer>
    </main>
  );
}
