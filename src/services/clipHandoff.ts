import type { AnalyzeResponse } from '../types';

type ClipHandoff = {
  job_id: string;
  source_url: string;
  preset: { duration: '30s'; model: 'gemini-3.8-flash'; target_clip_count: 'auto'; range_start: null; range_end: null };
  created_at: number;
  expires_at: number;
  analysis: AnalyzeResponse;
};

type ImportOptions = {
  hash: string;
  storage: Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;
  onImport: (handoff: ClipHandoff) => void;
  onError: (message: string) => void;
  fetcher?: typeof fetch;
  now?: () => number;
  signal?: AbortSignal;
};

const record = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);
const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
const optionalString = (value: unknown) => value === undefined || typeof value === 'string';
const interval = (start: unknown, end: unknown, duration: number) =>
  finite(start) && finite(end) && start >= 0 && end > start && end <= duration;

function validHandoff(value: unknown, id: string, now: number): value is ClipHandoff {
  if (!record(value) || value.job_id !== id || !record(value.preset) || !record(value.analysis)) return false;
  const p = value.preset;
  const a = value.analysis;
  if (p.duration !== '30s' || p.model !== 'gemini-3.8-flash' || p.target_clip_count !== 'auto'
    || p.range_start !== null || p.range_end !== null) return false;
  if (!finite(value.created_at) || value.created_at < 0 || value.created_at > now
    || !finite(value.expires_at)
    || value.expires_at <= now || value.expires_at <= value.created_at) return false;
  if (typeof a.video_id !== 'string' || !/^[A-Za-z0-9_-]{11}$/.test(a.video_id)
    || value.source_url !== `https://www.youtube.com/watch?v=${a.video_id}`
    || (a.source_type !== undefined && a.source_type !== 'youtube')
    || (a.video_url != null && a.video_url !== value.source_url)
    || (a.model != null && typeof a.model !== 'string') || typeof a.title !== 'string' || typeof a.summary !== 'string'
    || !finite(a.duration) || a.duration <= 0) return false;
  const duration = a.duration;
  if (!Array.isArray(a.clips) || !a.clips.every(c => record(c)
    && typeof c.title === 'string' && typeof c.transcript === 'string'
    && interval(c.start_time, c.end_time, duration) && finite(c.virality_score)
    && Array.isArray(c.key_quotes) && c.key_quotes.every(q => typeof q === 'string')
    && ['title_suggestion', 'caption_suggestion', 'hashtag_suggestion'].every(key => optionalString(c[key]))
    && (c.hook_time === undefined || (finite(c.hook_time) && c.hook_time >= 0 && c.hook_time <= duration))
    && (c.edited_subtitle_words === undefined || (Array.isArray(c.edited_subtitle_words)
      && c.edited_subtitle_words.every(w => record(w) && typeof w.id === 'string' && finite(w.group)
        && typeof w.word === 'string' && interval(w.start, w.end, duration)))))) return false;
  if (!Array.isArray(a.heatmap) || !a.heatmap.every(h => record(h)
    && interval(h.start_time, h.end_time, duration) && finite(h.value))) return false;
  return a.transcript == null || (Array.isArray(a.transcript) && a.transcript.every(line => record(line)
    && finite(line.start) && finite(line.end) && line.start >= 0 && line.end > line.start && typeof line.text === 'string'
    && (line.engagement == null || finite(line.engagement))));
}

/** Uses the installed session fetch wrapper; never submits analysis or model discovery. */
export async function importClipHandoff(options: ImportOptions): Promise<void> {
  const { hash, storage, onImport, onError, signal } = options;
  if (signal?.aborted || !hash.startsWith('#clip=')) return;
  if (!/^#clip=[0-9a-f]{32}$/.test(hash)) {
    onError('Invalid clip handoff link. Your existing workspace is unchanged.');
    return;
  }
  const id = hash.substring('#clip='.length);
  let data: unknown;
  try {
    const response = await (options.fetcher ?? fetch)(`/api/clip/handoffs/${id}`, {
      method: 'GET', credentials: 'same-origin', cache: 'no-store', signal,
    });
    if (signal?.aborted) return;
    if (!response.ok) {
      onError(response.status === 404 ? 'Clip handoff not found. Your existing workspace is unchanged.'
        : response.status === 410 ? 'Clip handoff expired. Your existing workspace is unchanged.'
        : response.status === 401 || response.status === 403 ? 'Sign in with permission to open this clip handoff. Your existing workspace is unchanged.'
        : 'Clip handoff service unavailable. Your existing workspace is unchanged.');
      return;
    }
    data = await response.json();
  } catch (error) {
    if (!signal?.aborted) onError(error instanceof SyntaxError
      ? 'Invalid clip handoff response. Your existing workspace is unchanged.'
      : 'Could not load clip handoff. Check your connection; your existing workspace is unchanged.');
    return;
  }
  if (signal?.aborted) return;
  if (!validHandoff(data, id, (options.now ?? (() => Date.now() / 1000))())) {
    onError('Invalid or expired clip handoff. Your existing workspace is unchanged.');
    return;
  }
  // A YouTube watch URL is not a direct video stream: retain the existing iframe path.
  const { video_url: _videoUrl, ...rawAnalysis } = data.analysis;
  // Pydantic emits optional fields as null; the existing React types use absence.
  const analysis: AnalyzeResponse = { ...rawAnalysis };
  for (const key of ['model', 'transcript'] as const) {
    if (analysis[key] == null) delete analysis[key];
  }
  if (analysis.transcript) analysis.transcript = analysis.transcript.filter(line => line.start < analysis.duration)
    .map(({ engagement, ...line }) => {
      const bounded = { ...line, end: Math.min(line.end, analysis.duration) };
      return engagement == null ? bounded : { ...bounded, engagement };
    });
  const handoff: ClipHandoff = { ...data, analysis };
  const suffix = `${analysis.video_id}_30s_model_gemini-3.8-flash_clips_auto_handoff_${id}`;
  const cacheKey = `cheat_clip_cache_${suffix}`;
  const tsKey = `cheat_clip_ts_${suffix}`;
  let storageFailed = false;
  try {
    // Reopening must not overwrite previously cached edits, even for the same job.
    if (storage.getItem(cacheKey) === null) {
      const previousTimestamp = storage.getItem(tsKey);
      try {
        storage.setItem(cacheKey, JSON.stringify(analysis));
        storage.setItem(tsKey, new Date(handoff.created_at * 1000).toISOString());
      } catch (error) {
        // Best-effort rollback touches only this import, never older analyses.
        try {
          storage.removeItem(cacheKey);
          if (previousTimestamp === null) storage.removeItem(tsKey);
          else storage.setItem(tsKey, previousTimestamp);
        } catch { /* Storage may be disabled entirely; the in-memory editor still works. */ }
        throw error;
      }
    }
  } catch { storageFailed = true; }
  onImport(handoff);
  if (storageFailed) onError('Browser storage is full or unavailable (quota). The imported editor remains usable; continue editing, but this import could not be saved to history.');
}
