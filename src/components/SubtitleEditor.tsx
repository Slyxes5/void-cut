import React, { useEffect, useMemo, useState } from 'react';
import type { ViralClip, TranscriptLine, EditedSubtitleWord } from '../types';
import { wordsFromTranscript } from '../utils/subtitleTiming';

type Props = {
  clip: ViralClip;
  transcript: TranscriptLine[];
  initialWords?: EditedSubtitleWord[];
  onSave: (words: EditedSubtitleWord[]) => void;
  onClose: () => void;
  onSync?: () => Promise<EditedSubtitleWord[]>;
  isSyncing?: boolean;
};
const number = (n: number) => Number(n.toFixed(3));
const css: React.CSSProperties = { padding: '0.5rem', background: '#101624', color: '#fff', border: '1px solid #364254', borderRadius: 6 };
export const SubtitleEditor: React.FC<Props> = ({clip, transcript, initialWords, onSave, onClose, onSync, isSyncing = false}) => {
  const [words, setWords] = useState<EditedSubtitleWord[]>(() => initialWords ? initialWords.map(x=>({...x})) : wordsFromTranscript(clip, transcript));
  const [error, setError] = useState('');
  const [audioSynced, setAudioSynced] = useState(false);
  const syncAudio = async () => {
    if (!onSync || isSyncing) return;
    setError('');
    try { setWords(await onSync()); setAudioSynced(true); }
    catch (e) { setError(e instanceof Error ? e.message : 'Sinkronisasi gagal.'); }
  };
  const groups = useMemo(() => Array.from(new Set<number>(words.map(w => w.group))).sort((a: number,b: number)=>a-b), [words]);
  useEffect(() => { const fn = (e: KeyboardEvent) => { if(e.key === 'Escape') onClose(); }; window.addEventListener('keydown',fn); return()=>window.removeEventListener('keydown',fn); },[onClose]);
  const change = (id: string, patch: Partial<EditedSubtitleWord>) => setWords(old=>old.map(w=>w.id===id?{...w,...patch}:w));
  const save = () => {
    const ordered = [...words].sort((a,b)=>a.start-b.start);
    const bad = ordered.some(w=>!w.word.trim() || !Number.isFinite(w.start) || !Number.isFinite(w.end) || w.start < clip.start_time || w.end > clip.end_time || w.end <= w.start);
    if(bad) { setError('Periksa teks dan timing: semua kata harus berada di dalam rentang klip dan End harus lebih besar dari Start.'); return; }
    setError(''); onSave(ordered.map(w=>({...w,word:w.word.trim()})));
  };
  const updateGroup = (group: number, text: string) => {
    const current = words.filter(w=>w.group===group).sort((a,b)=>a.start-b.start);
    const parts = text.trim().split(/\s+/).filter(Boolean);
    if(!current.length) return;
    // Correcting spelling must not redistribute existing acoustic timestamps.
    if (parts.length === current.length) {
      const replacements = new Map(current.map((w, i) => [w.id, parts[i]]));
      setWords(old => old.map(w => replacements.has(w.id) ? {...w, word: replacements.get(w.id)!} : w));
      return;
    }
    const begin = Math.min(...current.map(w=>w.start)); const end=Math.max(...current.map(w=>w.end));
    const generated = parts.map((word,i)=>({id:`g${group}-${i}-${Date.now()}`,group,word,start:number(begin+(end-begin)*i/parts.length),end:number(begin+(end-begin)*(i+1)/parts.length)}));
    setWords(old => [...old.filter(w=>w.group!==group),...generated].sort((a,b)=>a.start-b.start));
    setAudioSynced(false);
    setError('Jumlah kata berubah: timing kalimat ini diperkirakan ulang. Periksa timing kata baru dengan audio.');
  };
  const addGroup = () => {
    const last = Math.max(clip.start_time,...words.map(w=>w.end));
    const begin=Math.min(last,clip.end_time-0.25);
    setWords(old=>[...old,{id:`new-${Date.now()}`,group:Math.max(-1,...groups)+1,word:'Baru',start:number(begin),end:number(Math.min(clip.end_time,begin+0.25))}]);
  };
  return <div role="dialog" aria-modal="true" aria-label="Editable Transcript" style={{position:'fixed',inset:0,zIndex:10000,background:'rgba(0,0,0,.82)',display:'flex',alignItems:'center',justifyContent:'center',padding:16}}>
    <div style={{width:'min(960px,100%)',maxHeight:'92vh',overflowY:'auto',background:'#171c2b',border:'1px solid #455064',borderRadius:14,padding:20,color:'#f5f5f5'}}>
      <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',gap:12}}><h2 style={{margin:0}}>📝 Editable Transcript — {clip.title}</h2><button onClick={onClose} style={css}>✕ Tutup</button></div>
      <p style={{color:'#aab9d0'}}>Edit satu kalimat sekaligus atau kata per kata. Timestamp menggunakan DETIK ABSOLUT video sumber. Gunakan Sinkronkan dari audio untuk mengambil timing kata dari suara klip. Timing dari baris transkrip tanpa data per kata masih berupa perkiraan.</p>
      {onSync && <div style={{display:'flex',gap:12,alignItems:'center',marginBottom:12}}>
        <button type="button" style={css} onClick={()=>void syncAudio()} disabled={isSyncing}>
          {isSyncing ? 'Menyinkronkan audio...' : 'Sinkronkan dari audio'}
        </button>
        <small>{audioSynced ? 'Timing dari audio siap. Apply Transcript untuk preview dan render.' : 'Mengganti teks dan timing di editor ini. Apply Transcript untuk menyimpan.'}</small>
      </div>}
      {!words.length && <p style={{color:'#ffcd87'}}>Tidak ada transkrip untuk rentang klip ini. Tambahkan kalimat baru secara manual.</p>}
      {groups.map(group=>{const members=words.filter(w=>w.group===group).sort((a,b)=>a.start-b.start);return <section key={group} style={{border:'1px solid #364254',borderRadius:8,padding:12,margin:'12px 0'}}>
        <div style={{display:'flex',gap:8,alignItems:'center',justifyContent:'space-between'}}><strong>Kalimat {group+1}</strong><button style={css} onClick={()=>setWords(old=>old.filter(w=>w.group!==group))}>Hapus kalimat</button></div>
        <textarea aria-label={`Kalimat ${group+1}`} key={members.map(w=>w.id).join(',')} defaultValue={members.map(w=>w.word).join(' ')} onBlur={e=>{if(e.target.value!==members.map(w=>w.word).join(' ')) updateGroup(group,e.target.value)}} style={{...css,width:'100%',boxSizing:'border-box',minHeight:54,marginTop:8}} />
        <small style={{color:'#aab9d0'}}>Untuk mengubah jumlah kata, edit kalimat lalu klik di luar kolom. Setelah itu sesuaikan timing tiap kata.</small>
        <div style={{display:'grid',gridTemplateColumns:'minmax(90px,1fr) 110px 110px 36px',gap:8,marginTop:10}}>
          {members.map(w=><React.Fragment key={w.id}>
            <input aria-label="Kata" style={css} value={w.word} onChange={e=>change(w.id,{word:e.target.value})}/>
            <input aria-label="Start detik" type="number" step="0.01" style={{...css,width:'100%',boxSizing:'border-box'}} value={w.start} onChange={e=>change(w.id,{start:Number(e.target.value)})}/>
            <input aria-label="End detik" type="number" step="0.01" style={{...css,width:'100%',boxSizing:'border-box'}} value={w.end} onChange={e=>change(w.id,{end:Number(e.target.value)})}/>
            <button style={css} aria-label="Hapus kata" onClick={()=>setWords(old=>old.filter(x=>x.id!==w.id))}>×</button>
          </React.Fragment>)}
        </div>
      </section>})}
      <button style={css} onClick={addGroup}>+ Tambah kalimat</button>
      {error && <p role="alert" style={{color:'#ff8282'}}>{error}</p>}
      <div style={{display:'flex',gap:10,justifyContent:'flex-end',marginTop:20}}><button style={css} onClick={onClose}>Batal</button><button disabled={isSyncing} onClick={save} style={{...css,background:'#12864c',fontWeight:700}}>✓ Apply Transcript</button></div>
    </div>
  </div>;
};
