import { useCallback, useEffect, useRef, useState } from 'react';
import type { RenderClipStatus } from '../types';
import { socialApi, uploadReceiptUrl } from '../services/socialUploads';
import type { CreatorInfo, SocialAccount, SocialJob, SocialPlatform, SocialTarget } from '../services/socialUploads';
import './SocialUploadModal.css';

interface Props { batchId?: string; clip?: RenderClipStatus; onClose: () => void }
const names = { youtube: 'YouTube Shorts', tiktok: 'TikTok' };
const privacyLabels: Record<string,string> = {
  PUBLIC_TO_EVERYONE:'Everyone', MUTUAL_FOLLOW_FRIENDS:'Friends', FOLLOWER_OF_CREATOR:'Followers', SELF_ONLY:'Only me',
};

export function SocialUploadModal({ batchId, clip, onClose }: Props) {
  const accountsOnly = !batchId || !clip;
  const [accounts,setAccounts] = useState<SocialAccount[]>([]);
  const [jobs,setJobs] = useState<SocialJob[]>([]);
  const [title,setTitle] = useState((clip?.base_title || clip?.title || '').slice(0,100));
  const [caption,setCaption] = useState('');
  const [youtube,setYoutube] = useState(false);
  const [tiktok,setTiktok] = useState(false);
  const [youtubePrivacy,setYoutubePrivacy] = useState('private');
  const [kids,setKids] = useState('');
  const [synthetic,setSynthetic] = useState(false);
  const [mode,setMode] = useState<'draft'|'direct'>('draft');
  const [creatorState,setCreatorState] = useState<{accountId:string;info:CreatorInfo}|null>(null);
  const [tiktokPrivacy,setTiktokPrivacy] = useState('');
  const [comment,setComment] = useState(false);
  const [duet,setDuet] = useState(false);
  const [stitch,setStitch] = useState(false);
  const [commercial,setCommercial] = useState(false);
  const [ownBrand,setOwnBrand] = useState(false);
  const [branded,setBranded] = useState(false);
  const [music,setMusic] = useState(false);
  const [confirmation,setConfirmation] = useState<string|null>(null);
  const [busy,setBusy] = useState(false);
  const [accountBusy,setAccountBusy] = useState(false);
  const [loading,setLoading] = useState(true);
  const [historyLoaded,setHistoryLoaded] = useState(false);
  const [error,setError] = useState('');
  const [notice,setNotice] = useState('');
  const mounted = useRef(true);
  const submitLock = useRef(false);
  const attempt = useRef<{ signature: string; id: string } | null>(null);
  const historyPath = batchId && clip ? `uploads?batch_id=${encodeURIComponent(batchId)}&clip_index=${clip.clip_index}` : null;
  const accountIdentity = accounts.map(a=>`${a.platform}:${a.account_id}`).join('|');
  const tiktokAccountId = accounts.find(a=>a.platform==='tiktok')?.account_id;
  const creator = creatorState?.accountId === tiktokAccountId ? creatorState?.info : null;
  const confirmationKey = JSON.stringify([title,caption,youtube,tiktok,youtubePrivacy,kids,synthetic,mode,tiktokPrivacy,comment,duet,stitch,commercial,ownBrand,branded,music,accountIdentity]);
  const confirmed = confirmation === confirmationKey;

  const refreshAccounts = useCallback(() =>
    socialApi<{accounts:SocialAccount[]}>('accounts').then(data => {
      if (mounted.current) setAccounts(data.accounts);
    }).catch(e => { if (mounted.current) setError(e instanceof Error ? e.message : 'Tidak dapat memuat akun.'); })
      .finally(() => { if (mounted.current) { setAccountBusy(false); setLoading(false); } }), []);
  const refreshJobs = useCallback(() =>
    historyPath ? socialApi<{uploads:SocialJob[]}>(historyPath).then(data => {
      if (mounted.current) { setJobs(data.uploads); setHistoryLoaded(true); }
    }).catch(e => { if (mounted.current) setError(e instanceof Error ? e.message : 'Riwayat belum dapat diperiksa.'); }) : Promise.resolve(), [historyPath]);
  useEffect(() => {
    mounted.current = true;
    void refreshAccounts();
    void refreshJobs();
    const focus = () => { void refreshAccounts(); void refreshJobs(); };
    window.addEventListener('focus',focus);
    const escape = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown',escape);
    return () => { mounted.current = false; window.removeEventListener('focus',focus); window.removeEventListener('keydown',escape); };
  }, [onClose,refreshAccounts,refreshJobs]);
  useEffect(() => {
    if (!jobs.some(job => job.targets.some(t => ['queued','uploading','processing'].includes(t.status)))) return;
    const timer = window.setTimeout(() => {
      void (async () => {
        for (const job of jobs.filter(j=>j.targets.some(t=>t.platform==='tiktok' && t.status==='processing'))) {
          try { await socialApi(`uploads/${job.request_id}/refresh`,{method:'POST'}); }
          catch { /* Keep the receipt; a status check never re-uploads the file. */ }
        }
        if (mounted.current) await refreshJobs();
      })();
    },5000);
    return () => window.clearTimeout(timer);
  }, [jobs,refreshJobs]);
  useEffect(() => {
    if (!tiktok || mode !== 'direct') return;
    let canceled = false;
    void socialApi<CreatorInfo>('tiktok/creator-info').then(data => {
      if (!canceled && tiktokAccountId) {
        setCreatorState({accountId:tiktokAccountId,info:data});
        setTiktokPrivacy(''); setComment(false); setDuet(false); setStitch(false); setMusic(false);
      }
    })
      .catch(e => { if (!canceled) setError(e instanceof Error ? e.message : 'Info kreator tidak tersedia.'); });
    return () => { canceled = true; };
  }, [tiktok,mode,tiktokAccountId]);

  const connect = async (platform: SocialPlatform) => {
    const popup = window.open('about:blank','void-cut-'+platform,'popup,width=560,height=760');
    if (!popup) { setError('Izinkan popup untuk menghubungkan akun.'); return; }
    popup.opener = null;
    setAccountBusy(true); setError('');
    try {
      const data = await socialApi<{authorization_url:string}>(`accounts/${platform}/connect`,{method:'POST'});
      const target = new URL(data.authorization_url);
      if (target.protocol !== 'https:' || !['accounts.google.com','www.tiktok.com'].includes(target.hostname)) throw new Error('Alamat login tidak valid.');
      popup.location.href = target.href;
      setNotice('Selesaikan login pada tab baru, lalu klik Perbarui akun.');
    } catch (e) { popup.close(); setError(e instanceof Error ? e.message : 'Koneksi akun gagal.'); }
    finally { if (mounted.current) setAccountBusy(false); }
  };
  const disconnect = async (platform: SocialPlatform) => {
    setAccountBusy(true); setError('');
    try {
      await socialApi(`accounts/${platform}`,{method:'DELETE'});
      if (platform === 'youtube') setYoutube(false); else setTiktok(false);
      setNotice('Koneksi lokal dihapus. Izin aplikasi juga dapat dicabut dari pengaturan akun platform.');
      await refreshAccounts();
    } catch (e) { setError(e instanceof Error ? e.message : 'Koneksi tidak dapat dihapus.'); }
    finally { if (mounted.current) setAccountBusy(false); }
  };
  const submit = async () => {
    if (!batchId || !clip || submitLock.current) return;
    const targets: SocialTarget[] = [];
    if (youtube) targets.push({platform:'youtube',account_id:accounts.find(a=>a.platform==='youtube')!.account_id,mode:'upload',privacy:youtubePrivacy,made_for_kids:kids==='yes',contains_synthetic_media:synthetic});
    if (tiktok) targets.push({platform:'tiktok',account_id:accounts.find(a=>a.platform==='tiktok')!.account_id,mode,privacy:tiktokPrivacy,contains_synthetic_media:synthetic,
      allow_comment:comment,allow_duet:duet,allow_stitch:stitch,commercial,own_brand:ownBrand,branded_content:branded,music_consent:music});
    const body = {batch_id:batchId,clip_index:clip.clip_index,title:title.trim(),caption:caption.trim(),targets,confirmed:true};
    const signature = JSON.stringify(body);
    submitLock.current = true; setBusy(true); setError('');
    try {
      if (!attempt.current || attempt.current.signature !== signature) attempt.current = {signature,id:crypto.randomUUID()};
      const job = await socialApi<SocialJob>('uploads',{method:'POST',body:JSON.stringify({...body,request_id:attempt.current.id})});
      if (mounted.current) {
        setJobs(old => [job,...old.filter(j=>j.request_id !== job.request_id)]);
        setConfirmation(null);
        setNotice('Upload masuk antrean. Anda dapat menutup form ini; status tersimpan di backend.');
      }
    } catch (e) {
      if (mounted.current) {
        setError((e instanceof Error ? e.message : 'Pengiriman belum dapat dikonfirmasi.')+' Klik Periksa riwayat sebelum mencoba kembali.');
        void refreshJobs();
      }
    } finally { submitLock.current = false; if (mounted.current) setBusy(false); }
  };
  const refreshRemote = async (job: SocialJob) => {
    setError('');
    try {
      const updated = await socialApi<SocialJob>(`uploads/${job.request_id}/refresh`,{method:'POST'});
      if (mounted.current) setJobs(old=>old.map(j=>j.request_id===updated.request_id?updated:j));
    } catch (e) { setError(e instanceof Error ? e.message : 'Status belum dapat diperiksa.'); }
  };
  const selectedAccounts = accounts.filter(a=>a.platform==='youtube'?youtube:tiktok);
  const accountsReady = selectedAccounts.length === Number(youtube)+Number(tiktok) && selectedAccounts.every(a=>a.connected);
  const directValid = mode !== 'direct' || !tiktok || (creator && tiktokPrivacy && music &&
    (!commercial || ownBrand || branded) && !(branded && tiktokPrivacy==='SELF_ONLY') &&
    (title.trim()+'\n\n'+caption.trim()).length <= 2200);
  const blockedPlatform = (platform: SocialPlatform) => jobs.some(job=>job.targets.some(t=>t.platform===platform && t.account_id===accounts.find(a=>a.platform===platform)?.account_id && t.status!=='failed'));
  const ready = title.trim() && (youtube||tiktok) && accountsReady && (!youtube || kids) && directValid &&
    confirmed && !busy && !loading && historyLoaded && (!youtube || !blockedPlatform('youtube')) && (!tiktok || !blockedPlatform('tiktok'));

  return <div className="social-modal-backdrop">
    <section className="social-modal" role="dialog" aria-modal="true" aria-labelledby="social-upload-title">
      <header><div><span className="social-eyebrow">{accountsOnly?'KONEKSI AKUN':'SETELAH RENDER'}</span><h2 id="social-upload-title">{accountsOnly?'Login YouTube / TikTok':'Upload ke platform'}</h2>
        {clip?<p>#{clip.clip_index+1} · {clip.title}</p>:<p>Hubungkan akun sekarang. Pilih video untuk upload setelah render selesai.</p>}</div><button type="button" onClick={onClose} aria-label="Tutup upload">Tutup</button></header>
      {clip&&<video controls preload="metadata" src={clip.download_url} aria-label="Preview video yang akan diupload" />}
      <div className="social-accounts-header"><h3>Akun tujuan</h3><button type="button" disabled={accountBusy} onClick={()=>void refreshAccounts()}>Perbarui akun</button></div>
      {loading && <p role="status">Memuat akun...</p>}
      <div className="social-account-grid">{accounts.map(account=><article key={account.platform}>
        <strong>{names[account.platform]}</strong><p>{account.connected?account.account_name:account.configured?'Belum terhubung':'Perlu setup developer'}</p>
        {account.error && <p role="alert">{account.error}</p>}
        <button type="button" disabled={!account.configured||accountBusy} onClick={()=>void connect(account.platform)}>{account.connected?'Ganti akun':'Hubungkan akun'}</button>
        {account.connected && <button type="button" disabled={accountBusy} onClick={()=>void disconnect(account.platform)}>Putuskan koneksi lokal</button>}
      </article>)}</div>
      <details className="social-setup" open={!loading && accounts.every(a=>!a.configured)}><summary>Panduan setup pertama kali</summary>
        <p>Buat aplikasi di <a href="https://console.cloud.google.com/apis/library/youtube.googleapis.com" target="_blank" rel="noopener noreferrer">Google Cloud</a> dan <a href="https://developers.tiktok.com/" target="_blank" rel="noopener noreferrer">TikTok for Developers</a>. Masukkan Client ID/Key, Client Secret, redirect URI, dan kunci enkripsi ke backend/.env. Panduan lengkap: docs/social-upload-setup.md.</p>
        <p>Redirect YouTube: <code>{window.location.origin}/social/oauth/youtube/callback</code><br/>Redirect TikTok (HTTPS): <code>{window.location.origin}/social/oauth/tiktok/callback</code></p>
        <p>TikTok memerlukan persetujuan scope video.upload. Draf perlu diselesaikan dari inbox TikTok. Direct Post memiliki batasan dan review tersendiri.</p>
      </details>
      {!accountsOnly&&<>
      <label>Judul <span>{title.length}/100</span><input value={title} maxLength={100} onChange={e=>setTitle(e.target.value)} /></label>
      <label>Caption / deskripsi + hashtag<textarea rows={4} value={caption} maxLength={5000} onChange={e=>setCaption(e.target.value)} /></label>
      <div className="social-row"><button type="button" onClick={()=>{void navigator.clipboard.writeText([title,caption].filter(Boolean).join('\n\n')).then(()=>setNotice('Judul dan caption disalin.')).catch(()=>setError('Clipboard tidak tersedia. Salin teks dari kolom di atas.'));}}>Salin judul + caption</button>
        <label className="social-check"><input type="checkbox" checked={synthetic} onChange={e=>setSynthetic(e.target.checked)}/> Konten AI/sintetis realistis</label></div>
      <fieldset><legend>Pilih platform</legend>
        <label className="social-check"><input type="checkbox" checked={youtube} onChange={e=>setYoutube(e.target.checked)} disabled={!accounts.find(a=>a.platform==='youtube')?.connected||blockedPlatform('youtube')}/> YouTube Shorts</label>
        {youtube && <div className="social-platform-options">
          <label>Visibilitas<select value={youtubePrivacy} onChange={e=>setYoutubePrivacy(e.target.value)}><option value="private">Private</option><option value="unlisted">Unlisted</option><option value="public">Public</option></select></label>
          <label>Apakah video ini dibuat untuk anak-anak?<select value={kids} onChange={e=>setKids(e.target.value)}><option value="">Pilih...</option><option value="no">Tidak dibuat untuk anak-anak</option><option value="yes">Ya, dibuat untuk anak-anak</option></select></label>
          <p>Video persegi/vertikal, maksimal 3 menit. Pembatasan project Google dapat memengaruhi visibilitas aktual.</p>
        </div>}
        <label className="social-check"><input type="checkbox" checked={tiktok} onChange={e=>{setTiktok(e.target.checked);setCreatorState(null);}} disabled={!accounts.find(a=>a.platform==='tiktok')?.connected||blockedPlatform('tiktok')}/> TikTok</label>
        {tiktok && <div className="social-platform-options"><label>Cara upload<select value={mode} onChange={e=>{setMode(e.target.value as 'draft'|'direct');setCreatorState(null);}}><option value="draft">Kirim ke draf / inbox TikTok</option>
          {accounts.find(a=>a.platform==='tiktok')?.direct_enabled && <option value="direct">Direct Post</option>}</select></label>
          {mode==='draft'?<p>Video dikirim ke inbox TikTok. API draf video tidak menerima judul/caption; salin teks di atas dan tempel saat menyelesaikan posting di aplikasi TikTok.</p>:<>
            {!accounts.find(a=>a.platform==='tiktok')?.direct_audited && <p>Client belum ditandai audited: hanya Only me. Akun TikTok juga harus private saat posting.</p>}
            <p>Akun: <strong>{creator?.creator_nickname||'Memuat info kreator...'}</strong> · Batas durasi: {creator?.max_video_post_duration_sec||'—'} detik</p>
            <label>Privasi<select value={tiktokPrivacy} onChange={e=>setTiktokPrivacy(e.target.value)}><option value="">Pilih privasi...</option>{creator?.privacy_level_options.map(v=><option key={v} value={v} disabled={branded&&!['PUBLIC_TO_EVERYONE','MUTUAL_FOLLOW_FRIENDS'].includes(v)}>{privacyLabels[v]||v}</option>)}</select></label>
            <div className="social-row">{([
              ['Komentar',comment,setComment,creator?.comment_disabled],['Duet',duet,setDuet,creator?.duet_disabled],['Stitch',stitch,setStitch,creator?.stitch_disabled],
            ] as const).map(([label,checked,set,disabled])=><label key={label} className="social-check"><input type="checkbox" checked={checked} onChange={e=>set(e.target.checked)} disabled={!creator||disabled}/> Izinkan {label}</label>)}</div>
            <label className="social-check"><input type="checkbox" checked={commercial} onChange={e=>{setCommercial(e.target.checked);setOwnBrand(false);setBranded(false);}}/> Konten komersial</label>
            {commercial&&<div><label className="social-check"><input type="checkbox" checked={ownBrand} onChange={e=>setOwnBrand(e.target.checked)}/> Brand sendiri (Promotional content)</label>
              <label className="social-check"><input type="checkbox" checked={branded} disabled={!!tiktokPrivacy&&!['PUBLIC_TO_EVERYONE','MUTUAL_FOLLOW_FRIENDS'].includes(tiktokPrivacy)} onChange={e=>setBranded(e.target.checked)}/> Brand pihak lain (Paid partnership)</label></div>}
            <label className="social-check"><input type="checkbox" checked={music} onChange={e=>setMusic(e.target.checked)}/> Dengan posting, saya menyetujui <a href="https://www.tiktok.com/legal/page/global/music-usage-confirmation/en" target="_blank" rel="noopener noreferrer">Music Usage Confirmation</a>{commercial&&branded&&<> dan <a href="https://www.tiktok.com/legal/page/global/bc-policy/en" target="_blank" rel="noopener noreferrer">Branded Content Policy</a></>} TikTok.</label>
            <p>{(title.trim()+'\n\n'+caption.trim()).length}/2200 karakter caption TikTok</p>
          </>}
        </div>}
      </fieldset>
      <label className="social-check social-confirm"><input type="checkbox" checked={confirmed} onChange={e=>setConfirmation(e.target.checked?confirmationKey:null)}/> Saya telah memeriksa video, teks, akun tujuan, dan pengaturan di atas serta menyetujui pengirimannya.</label>
      {notice&&<p className="social-notice" role="status">{notice}</p>}{error&&<p className="social-error" role="alert">{error}</p>}
      {!historyLoaded&&<p role="status">Memeriksa riwayat untuk mencegah upload ganda. Jika gagal, klik Periksa riwayat.</p>}
      <div className="social-row"><button className="social-primary" type="button" disabled={!ready} onClick={()=>void submit()}>{busy?'Mengirim permintaan...':'Mulai upload ke platform terpilih'}</button><button type="button" onClick={()=>void refreshJobs()}>Periksa riwayat</button></div>
      {jobs.length>0&&<section className="social-history"><h3>Riwayat upload klip ini</h3>{jobs.map(job=><article key={job.request_id}><strong>{job.title}</strong>{job.targets.map(target=><div key={target.platform}>
        <p><b>{names[target.platform]}</b> · {target.status} · {target.progress}%</p><progress max={100} value={target.progress}/><p>{target.message}</p>
        {uploadReceiptUrl(target.url)&&<a href={uploadReceiptUrl(target.url)} target="_blank" rel="noopener noreferrer">Lihat di {names[target.platform]}</a>}
        {target.platform==='tiktok'&&['processing','uncertain'].includes(target.status)&&<button type="button" onClick={()=>void refreshRemote(job)}>Periksa status TikTok</button>}
        {target.status==='failed'&&<button type="button" onClick={()=>{attempt.current=null;setConfirmation(null);setNotice('Pilih platform yang gagal, periksa pengaturan lalu konfirmasi kembali. Platform yang sudah terkirim tidak diulang.');}}>Siapkan percobaan baru</button>}
      </div>)}</article>)}</section>}
      </>}
      {accountsOnly&&<>{notice&&<p className="social-notice" role="status">{notice}</p>}{error&&<p className="social-error" role="alert">{error}</p>}</>}
    </section>
  </div>;
}
