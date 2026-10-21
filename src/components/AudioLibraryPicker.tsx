import { useEffect, useRef, useState } from 'react';

type AudioLibraryPickerProps = {
  kind: 'bgm' | 'sfx';
  onChoose: (file: File) => Promise<void>;
  busy: boolean;
  active?: boolean;
};

/** Browser-local asset picker: no automatic disk scanning or upload until selected. */
export default function AudioLibraryPicker({ kind, onChoose, busy, active = true }: AudioLibraryPickerProps) {
  const [files, setFiles] = useState<File[]>([]);
  const [playing, setPlaying] = useState<string | null>(null);
  const [error, setError] = useState('');
  const audio = useRef<HTMLAudioElement | null>(null);
  const url = useRef<string | null>(null);
  const input = useRef<HTMLInputElement | null>(null);
  useEffect(() => { if (!active) audio.current?.pause(); }, [active]);
  const stop = () => {
    if (audio.current) { audio.current.pause(); audio.current.src = ''; audio.current = null; }
    if (url.current) { URL.revokeObjectURL(url.current); url.current = null; }
    setPlaying(null);
  };
  useEffect(() => () => {
    if (audio.current) { audio.current.pause(); audio.current.src = ''; }
    if (url.current) URL.revokeObjectURL(url.current);
  }, []);
  const addFiles = (items: FileList | null) => {
    if (!items) return;
    stop();
    const valid = Array.from(items).filter(f => /\.(mp3|wav|m4a|aac|ogg|flac)$/i.test(f.name) && f.size <= 30 * 1024 * 1024);
    const rejected = items.length - valid.length;
    setError(rejected ? `${rejected} file dilewati (format tidak didukung atau lebih dari 30 MB).` : '');
    setFiles(old => {
      const map = new Map(old.map(f => [`${f.name}-${f.size}`, f]));
      valid.forEach(f => map.set(`${f.name}-${f.size}`, f));
      return Array.from(map.values()).sort((a,b) => a.name.localeCompare(b.name));
    });
  };
  const play = (file: File) => {
    if (playing === `${file.name}-${file.size}`) { stop(); return; }
    stop();
    const objectUrl = URL.createObjectURL(file);
    url.current = objectUrl;
    const player = new Audio(objectUrl);
    audio.current = player;
    player.volume = kind === 'bgm' ? 0.2 : 0.5;
    player.onended = stop;
    player.onpause = () => setPlaying(null);
    player.play().then(() => { if (!player.paused) setPlaying(`${file.name}-${file.size}`); }).catch(() => { stop(); setError('Gagal memutar audio ini.'); });
  };
  return (
    <div style={{ marginTop: '0.65rem', padding: '0.8rem', border: '1px solid rgba(148,163,184,.3)', borderRadius: '10px', background: 'rgba(148,163,184,.05)' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '0.5rem' }}>
        <div>
          <strong style={{ fontSize: '0.8rem' }}>🎧 Library {kind === 'bgm' ? 'BGM' : 'Meme SFX'} Lokal</strong>
          <div style={{ fontSize: '0.67rem', opacity: 0.75 }}>Pilih file dari folder koleksi, dengarkan, lalu gunakan.</div>
        </div>
        <button type="button" className="pill-btn" onClick={() => input.current?.click()} disabled={busy}>+ Pilih Audio (bisa banyak)</button>
        <input ref={input} type="file" accept=".mp3,.wav,.m4a,.aac,.ogg,.flac,audio/*" multiple style={{ display: 'none' }} onChange={e => { addFiles(e.target.files); e.target.value = ''; }} />
      </div>
      {error && <div role="alert" style={{ color: '#fda4af', fontSize: '0.72rem', marginTop: '0.3rem' }}>{error}</div>}
      {files.length === 0 ? (
        <p style={{ fontSize: '0.7rem', opacity: 0.72, margin: '0.65rem 0 0' }}>Belum ada file terpilih. Klik Pilih Audio untuk membuka folder SFX/BGM yang Anda simpan.</p>
      ) : (
        <div style={{ marginTop: '0.6rem', maxHeight: '240px', overflowY: 'auto' }}>
          {files.map(file => {
            const key = `${file.name}-${file.size}`;
            return <div key={key} style={{ display: 'flex', gap: '0.4rem', alignItems: 'center', padding: '0.35rem 0', borderBottom: '1px solid rgba(148,163,184,.12)' }}>
              <span title={file.name} style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontSize: '0.72rem' }}>{file.name}</span>
              <button type="button" className="pill-btn" onClick={() => play(file)} aria-label={`Dengarkan ${file.name}`}>{playing === key ? '⏹ Stop' : '▶ Dengar'}</button>
              <button type="button" className="pill-btn" disabled={busy} onClick={() => { stop(); void onChoose(file); }}>{busy ? '...' : '✓ Gunakan'}</button>
            </div>;
          })}
        </div>
      )}
      <p style={{ fontSize: '0.66rem', opacity: 0.72, margin: '0.55rem 0 0' }}>Saat ini pilihan SFX berlaku untuk hook di awal klip. Penempatan efek pada detik bebas dan rekomendasi AI akan dibuat pada tahap selanjutnya.</p>
    </div>
  );
}
