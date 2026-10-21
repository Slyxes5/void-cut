import { useState } from 'react';
import type { BatchRenderProgress, RenderClipStatus } from '../types';
import type { RecoveryConnection } from '../services/batchRecovery';

interface Props {
  batch: BatchRenderProgress | null;
  connection: RecoveryConnection;
  onRefresh: () => void;
  onRestore: (batchId: string) => void;
  onDismiss: () => void;
  onRetry?: (clipIndex?: number) => void;
  onUpload?: (batchId: string, clip: RenderClipStatus) => void;
}

/** Outside the analysis/editor gate so a reloaded phone can always find results. */
export function BatchRenderRecoveryPanel({ batch, connection, onRefresh, onRestore, onDismiss, onRetry, onUpload }: Props) {
  const [recent, setRecent] = useState<BatchRenderProgress[]>([]);
  const [finding, setFinding] = useState(false);
  const [notice, setNotice] = useState('');
  const isRunning = batch?.overall_status === 'running';
  const isClient = batch?.batch_id.startsWith('client_') === true;
  const total = batch?.clips.length || 1;
  const percentage = batch ? Math.round(batch.clips.reduce((sum, clip) => sum + clip.progress_percent, 0) / total) : 0;
  const completed = batch?.clips.filter(clip => clip.status === 'completed').length || 0;

  const findRecent = async () => {
    if (finding) return;
    setFinding(true);
    setNotice('');
    try {
      const response = await fetch('/api/render-batches', { cache: 'no-store', signal: AbortSignal.timeout(8000) });
      if (!response.ok) throw new Error('Unavailable');
      const data = await response.json();
      if (!Array.isArray(data.batches)) throw new Error('Invalid response');
      const batches = data.batches.filter((item: BatchRenderProgress) => /^batch_\d+_[a-f0-9]{6}$/.test(item?.batch_id));
      setRecent(batches);
      if (!batches.length) setNotice('No recoverable batches or exported files were found on the laptop.');
    } catch {
      setNotice('Could not check recent renders. Reconnect to the laptop and try again.');
    } finally {
      setFinding(false);
    }
  };

  return (
    <section id="render-results" className="glass-panel render-recovery-panel" aria-labelledby="render-results-title">
      <div className="render-recovery-heading">
        <div>
          <h2 id="render-results-title">{batch?.recovered_from_files ? 'Recovered rendered files' : 'Batch render results'}</h2>
          <p>{batch?.recovered_from_files ? `${completed} available MP4 files · Original batch state unavailable`
            : batch ? `${completed} of ${batch.total_clips} clips completed${isRunning ? (isClient ? ' · Rendering in this browser' : ' · Rendering on the laptop') : ''}`
            : 'Reconnect to a batch without starting another render.'}</p>
        </div>
        <div className="render-recovery-actions">
          <button type="button" onClick={findRecent} disabled={finding || (isClient && isRunning)}>{finding ? 'Checking…' : 'Find recent renders'}</button>
          {!isClient && (batch || connection.mode !== 'idle') && <button type="button" onClick={onRefresh}>Refresh status</button>}
        </div>
      </div>
      {!isClient && connection.message && <p className={`render-connection mode-${connection.mode}`} role="status">{connection.message}</p>}
      {isClient && <p className="render-client-notice">Rendered in this browser. Download your files before closing or refreshing this tab.</p>}
      {notice && <p role="status">{notice}</p>}
      {recent.length > 0 && (
        <ul className="render-recent-batches" aria-label="Recent render batches">
          {recent.map(item => <li key={item.batch_id}>
            <button type="button" onClick={() => {
              if (isRunning && batch?.batch_id !== item.batch_id && !window.confirm('Another batch is still running. Switch the displayed batch? This does not cancel it.')) return;
              onRestore(item.batch_id); setRecent([]);
            }}>Restore {item.batch_id} · {item.total_clips} {item.recovered_from_files ? 'available files' : 'clips'} · {item.overall_status}</button>
          </li>)}
        </ul>
      )}
      {batch && <>
        {batch.warning_message && <p className="render-recovery-warning">{batch.warning_message}</p>}
        {batch.error_message && <p role="alert">{batch.error_message}</p>}
        <div className="render-overall-progress">
          <span>{percentage}% · {batch.overall_status === 'error' ? 'Batch finished with errors' : isRunning ? 'Batch in progress' : 'Results available'}</span>
          <progress max={100} value={percentage} aria-label="Overall batch progress" />
        </div>
        <ol className="render-recovery-clips">
          {batch.clips.map(clip => <li key={clip.clip_index} data-clip-status={clip.status}>
            <div className="render-clip-details">
              <h3>#{clip.clip_index + 1} · {clip.title}</h3>
              <p>{clip.status} · {clip.progress_percent}%</p>
              <progress max={100} value={clip.progress_percent} aria-label={`Progress for clip ${clip.clip_index + 1}`} />
              {(clip.error_message || clip.error) && <p className="render-recovery-warning">{clip.error_message || clip.error}</p>}
              {clip.status === 'completed' && !isClient && clip.download_available !== true && <p>Output no longer available on the laptop.</p>}
            </div>
            <div className="render-recovery-actions">
              {isClient && clip.status === 'completed' && clip.download_url?.startsWith('blob:') && (
                <a href={clip.download_url} download={`${clip.title.replace(/[\\/*?:"<>|]/g, '') || 'void_cut_clip'}.mp4`}>Download MP4</a>
              )}
              {!isClient && clip.status === 'completed' && clip.download_available === true && clip.download_url?.startsWith('/api/download-rendered/') && (
                <a href={clip.download_url} download>Download MP4</a>
              )}
              {!isClient && onUpload && clip.status === 'completed' && clip.download_available === true && clip.download_url?.startsWith('/api/download-rendered/') && (
                <button type="button" onClick={() => onUpload(batch.batch_id, clip)}>Upload ke TikTok / YouTube</button>
              )}
              {!isClient && clip.status === 'error' && batch.can_retry && onRetry && !isRunning && (
                <button type="button" onClick={() => onRetry(clip.clip_index)}>Retry clip</button>
              )}
            </div>
          </li>)}
        </ol>
        <div className="render-recovery-actions render-recovery-footer">
          {!isClient && batch.zip_available === true && batch.zip_url?.startsWith('/api/download-batch-zip/') && (
            <a className="render-zip-download" href={batch.zip_url} download>Download ZIP</a>
          )}
          {!isClient && !isRunning && batch.can_retry && onRetry && batch.clips.some(clip => clip.status === 'error') && (
            <button type="button" onClick={() => onRetry()}>Retry failed clips</button>
          )}
          {!isRunning && <button type="button" onClick={onDismiss}>Dismiss results</button>}
        </div>
      </>}
      {!isClient && ((!batch && connection.mode !== 'idle') || connection.mode === 'missing') && <button type="button" className="render-forget-batch" onClick={onDismiss}>Forget recovery / allow a new batch</button>}
    </section>
  );
}
