import { useLanguage } from '../locales';
import type { WorkflowPage } from '../services/workflow';

const pages: WorkflowPage[] = ['source', 'clips', 'studio', 'exports'];
const copy = {
  id: {
    nav: 'Alur kerja', steps: ['Sumber video', 'Pilih klip', 'Studio', 'Ekspor'],
    titles: ['Pilih video untuk memulai.', 'Temukan momen pilihanmu.', 'Bentuk klip sesuai gayamu.', 'Klipmu, siap dibagikan.'],
    descriptions: ['Pilih sumber video, tempel tautan atau unggah file, lalu tekan “Cari Klip”. Pengaturan AI ada di panel sebelah kanan.', 'Lihat rekomendasi, lalu pilih klip yang ingin kamu edit.', 'Atur tampilan, subtitle, dan audio sebelum mulai render.', 'Pantau render, unduh hasil, atau lanjutkan ke akun sosialmu.'],
    back: 'Kembali', next: 'Buka Studio', resume: 'Lihat hasil analisis', selected: 'klip dipilih',
    analyzing: 'Menganalisis video…', analyzingDescription: 'Progres analisis tampil di bawah. Hasil akan terbuka otomatis saat selesai.',
    pending: 'Analisis video terlebih dahulu',
  },
  en: {
    nav: 'Workflow', steps: ['Video source', 'Choose clips', 'Studio', 'Exports'],
    titles: ['Choose a video to get started.', 'Find your best moments.', 'Make the cut your own.', 'Your clips, ready to share.'],
    descriptions: ['Choose a source, paste a link or upload a file, then select “Find Clips”. AI settings are in the panel on the right.', 'Review recommendations and select the clips you want to edit.', 'Adjust the framing, captions, and audio before rendering.', 'Track renders, download files, or continue to your social accounts.'],
    back: 'Back', next: 'Open Studio', resume: 'View analysis', selected: 'clips selected',
    analyzing: 'Analyzing your video…', analyzingDescription: 'Follow the progress below. Your results will open automatically when ready.',
    pending: 'Analyze a video first',
  },
};

interface Props {
  page: WorkflowPage;
  onNavigate: (page: WorkflowPage) => void;
  hasAnalysis: boolean;
  selectedCount: number;
  loading: boolean;
  rendering: boolean;
}

export function WorkflowNavigation({ page, onNavigate, hasAnalysis, selectedCount, loading, rendering }: Props) {
  const { language } = useLanguage();
  const text = copy[language];
  const index = pages.indexOf(page);
  return <>
    <nav className="workflow-nav" aria-label={text.nav}>
      {pages.map((step, i) => <button key={step} type="button"
        className={`workflow-step${step === page ? ' is-current' : ''}`}
        aria-current={step === page ? 'step' : undefined}
        disabled={(loading && step !== 'source' && step !== 'exports') || (!hasAnalysis && (step === 'clips' || step === 'studio'))}
        title={!hasAnalysis && (step === 'clips' || step === 'studio') ? text.pending : undefined}
        onClick={() => onNavigate(step)}>
        <span className="workflow-step-number">{String(i + 1).padStart(2, '0')}</span>
        <span>{text.steps[i]}</span>
        {step === 'exports' && rendering && <span className="workflow-live-dot" aria-label={language === 'id' ? 'Render berlangsung' : 'Rendering'} />}
      </button>)}
    </nav>
    <section className="workflow-heading" data-page={page}>
      <div>
        <p className="workflow-eyebrow">VOID CUT <span>/</span> {text.steps[index]}</p>
        <h2 id="workflow-page-title" tabIndex={-1}>{loading && page === 'source' ? text.analyzing : text.titles[index]}</h2>
        <p className="workflow-description">{loading && page === 'source' ? text.analyzingDescription : text.descriptions[index]}</p>
      </div>
      <div className="workflow-heading-actions">
        {page === 'source' && hasAnalysis && !loading && <button type="button" className="workflow-secondary" onClick={() => onNavigate('clips')}>{text.resume} →</button>}
        {(page === 'clips' || page === 'studio') && <button type="button" className="workflow-secondary" onClick={() => onNavigate(page === 'studio' ? 'clips' : 'source')}>← {text.back}</button>}
        {page === 'clips' && <>
          <span className="workflow-selection" role="status">{selectedCount} {text.selected}</span>
          <button type="button" className="workflow-primary" disabled={!selectedCount} onClick={() => onNavigate('studio')}>{text.next} →</button>
        </>}
        {page === 'exports' && hasAnalysis && <button type="button" className="workflow-secondary" onClick={() => onNavigate('studio')}>← Studio</button>}
      </div>
    </section>
  </>;
}
