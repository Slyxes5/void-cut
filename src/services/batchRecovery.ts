import type { BatchRenderProgress } from '../types';

export const BATCH_RECOVERY_KEY = 'cheat_clip_render_recovery_v1';
export interface RecoveryConnection {
  mode: 'idle' | 'recovering' | 'live' | 'polling' | 'offline' | 'missing';
  message: string;
}
interface Pointer { batch_id?: string; request_id?: string }
interface EventStream {
  onmessage: ((event: MessageEvent) => void) | null;
  onerror: ((event: Event) => void) | null;
  onopen: ((event: Event) => void) | null;
  close(): void;
}
interface Options {
  storage: Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;
  onBatch: (batch: BatchRenderProgress | null) => void;
  onConnection: (connection: RecoveryConnection) => void;
  fetcher?: typeof fetch;
  makeEventSource?: (url: string) => EventStream;
  schedule?: (callback: () => void, delay: number) => number;
  cancel?: (timer: number) => void;
  createRequestId?: () => string;
}
const batchPattern = /^batch_\d+_[a-f0-9]{6}$/;
const requestPattern = /^[a-zA-Z0-9_-]{8,128}$/;
const terminal = (batch: BatchRenderProgress | null) => !!batch && batch.overall_status !== 'running';

/** A connection failure is not a render failure. The backend owns job state. */
export class BatchRecovery {
  private pointer: Pointer | null = null;
  private batch: BatchRenderProgress | null = null;
  private source: EventStream | null = null;
  private timer: number | null = null;
  private abort: AbortController | null = null;
  private generation = 0;
  private revision = 0;
  private streamFailed = false;
  private fetcher: typeof fetch;
  private schedule: (callback: () => void, delay: number) => number;
  private cancel: (timer: number) => void;
  private makeSource: (url: string) => EventStream;
  private options: Options;

  constructor(options: Options) {
    this.options = options;
    this.fetcher = options.fetcher ?? fetch;
    this.schedule = options.schedule ?? ((callback, delay) => window.setTimeout(callback, delay));
    this.cancel = options.cancel ?? (timer => window.clearTimeout(timer));
    this.makeSource = options.makeEventSource ?? (url => new EventSource(url));
  }

  private connection(mode: RecoveryConnection['mode'], message: string) {
    this.options.onConnection({ mode, message });
  }

  private persist() {
    try {
      if (this.pointer) this.options.storage.setItem(BATCH_RECOVERY_KEY, JSON.stringify(this.pointer));
      else this.options.storage.removeItem(BATCH_RECOVERY_KEY);
    } catch {
      // Private mode / full storage must not interrupt an accepted render.
    }
  }

  private closeStream() {
    this.source?.close();
    this.source = null;
  }

  private clearTimer() {
    if (this.timer !== null) this.cancel(this.timer);
    this.timer = null;
  }

  stop() {
    this.generation++;
    this.closeStream();
    this.clearTimer();
    this.abort?.abort();
    this.abort = null;
  }

  private accept(value: unknown): boolean {
    const data = value as BatchRenderProgress;
    if (!data || !batchPattern.test(data.batch_id) || !['running', 'completed', 'error'].includes(data.overall_status)
      || !Number.isInteger(data.total_clips) || data.total_clips < 0 || !Array.isArray(data.clips)
      || data.clips.some(c => !c || typeof c.title !== 'string' || !Number.isInteger(c.clip_index)
        || !['pending', 'downloading', 'transcribing', 'tracking', 'rendering', 'completed', 'error'].includes(c.status)
        || !Number.isFinite(c.progress_percent))) return false;
    if (this.pointer?.batch_id && data.batch_id !== this.pointer.batch_id) return false;
    // Only server-confirmed, same-origin export URLs become clickable.
    const batch: BatchRenderProgress = {
      ...data,
      clips: data.clips.map(c => ({ ...c, progress_percent: Math.max(0, Math.min(100, c.progress_percent)),
        download_url: c.status === 'completed' && c.download_available === true
          && c.download_url?.startsWith('/api/download-rendered/') ? c.download_url : undefined })),
      zip_url: data.zip_available === true && data.zip_url?.startsWith('/api/download-batch-zip/') ? data.zip_url : undefined,
    };
    this.batch = batch;
    this.pointer = { ...this.pointer, batch_id: batch.batch_id };
    this.persist();
    this.revision++;
    this.options.onBatch(batch);
    if (terminal(batch)) {
      this.closeStream();
      this.clearTimer();
      this.connection('idle', 'Render results confirmed by the laptop.');
    }
    return true;
  }

  private openStream() {
    if (!this.pointer?.batch_id || this.source || this.streamFailed || terminal(this.batch)) return;
    const generation = this.generation;
    try {
      const source = this.makeSource(`/api/render-progress/${encodeURIComponent(this.pointer.batch_id)}`);
      this.source = source;
      source.onopen = () => {
        if (generation === this.generation) this.connection('live', 'Live progress from the laptop.');
      };
      source.onmessage = event => {
        if (generation !== this.generation || source !== this.source) return;
        try {
          if (!this.accept(JSON.parse(event.data))) throw new Error('Invalid progress');
        } catch {
          this.streamFailed = true;
          this.closeStream();
          this.connection('polling', 'Checking progress with status polling.');
          void this.refresh();
        }
      };
      source.onerror = () => {
        if (generation !== this.generation || source !== this.source) return;
        this.streamFailed = true;
        this.closeStream();
        this.connection('polling', 'Live connection interrupted. Status polling is active.');
        void this.refresh();
      };
    } catch {
      this.streamFailed = true;
      this.connection('polling', 'Using status polling for render progress.');
    }
  }

  watch(batchId: string, requestId?: string) {
    if (!batchPattern.test(batchId)) throw new Error('Invalid batch ID');
    this.stop();
    this.batch = null;
    this.streamFailed = false;
    this.pointer = { batch_id: batchId, ...(requestId ? { request_id: requestId } : {}) };
    this.persist();
    this.connection('recovering', 'Checking the render batch on the laptop.');
    this.openStream();
    void this.refresh();
  }

  restore() {
    try {
      const value = JSON.parse(this.options.storage.getItem(BATCH_RECOVERY_KEY) || 'null') as Pointer | null;
      if (!value || (!batchPattern.test(value.batch_id || '') && !requestPattern.test(value.request_id || ''))) return;
      this.pointer = {
        ...(batchPattern.test(value.batch_id || '') ? { batch_id: value.batch_id } : {}),
        ...(requestPattern.test(value.request_id || '') ? { request_id: value.request_id } : {}),
      };
      this.connection('recovering', 'Restoring the last render batch from the laptop.');
      this.openStream();
      void this.refresh();
    } catch {
      // Invalid/blocked browser storage must not break the dashboard.
    }
  }

  isBlocked() {
    return this.pointer !== null && !terminal(this.batch);
  }

  ownsRequest(requestId: string) {
    return this.pointer?.request_id === requestId;
  }

  isWatching(batchId: string) {
    return this.pointer?.batch_id === batchId;
  }

  beginSubmission(): string | null {
    if (this.isBlocked()) return null;
    const randomId = () => {
      if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
      if (!globalThis.crypto?.getRandomValues) throw new Error('Use private HTTPS to create a safe render request ID.');
      const bytes = globalThis.crypto.getRandomValues(new Uint8Array(16));
      bytes[6] = (bytes[6] & 0x0f) | 0x40;
      bytes[8] = (bytes[8] & 0x3f) | 0x80;
      const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('');
      return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
    };
    const requestId = this.options.createRequestId?.() ?? randomId();
    if (!requestPattern.test(requestId)) throw new Error('Invalid render request ID');
    this.stop();
    this.batch = null;
    this.pointer = { request_id: requestId };
    this.persist();
    this.connection('recovering', 'Submitting the batch. Do not start a second render.');
    return requestId;
  }

  /** Foreground/online/pageshow: discard a suspended request and recheck now. */
  resume() {
    if (!this.pointer) return;
    this.stop();
    this.streamFailed = false;
    this.openStream();
    void this.refresh();
  }

  dismiss() {
    this.stop();
    this.pointer = null;
    this.batch = null;
    this.persist();
    this.options.onBatch(null);
    this.connection('idle', '');
  }

  async refresh() {
    if (!this.pointer || this.abort) return;
    this.clearTimer();
    const generation = this.generation;
    const revision = this.revision;
    const abort = new AbortController();
    this.abort = abort;
    const timeout = this.schedule(() => abort.abort(), 8000);
    const path = this.pointer.batch_id ? `/api/render-status/${encodeURIComponent(this.pointer.batch_id)}`
      : `/api/render-request/${encodeURIComponent(this.pointer.request_id || '')}`;
    try {
      const response = await this.fetcher(path, { cache: 'no-store', signal: abort.signal });
      if (generation !== this.generation || revision !== this.revision) return;
      if (response.status === 404) {
        this.closeStream();
        if (this.batch) {
          this.batch = { ...this.batch, zip_available: false, zip_url: undefined,
            clips: this.batch.clips.map(clip => ({ ...clip, download_available: false, download_url: undefined })) };
          this.revision++;
          this.options.onBatch(this.batch);
        }
        this.connection('missing', 'The laptop cannot find this job. It may have restarted or removed expired exports. Check recent renders before starting another batch.');
        return;
      }
      if (!response.ok) throw new Error('Status unavailable');
      const data: unknown = await response.json();
      if (generation !== this.generation || revision !== this.revision) return;
      if (!this.accept(data)) throw new Error('Invalid status response');
      if (!terminal(this.batch)) {
        this.connection(this.source ? 'live' : 'polling', this.source ? 'Progress is being verified with the laptop.' : 'Status polling is active.');
        this.openStream();
      }
    } catch {
      if (generation === this.generation) this.connection('offline', 'Connection interrupted. Rendering may still be running on the laptop. Reconnecting automatically; do not start a duplicate batch.');
    } finally {
      this.cancel(timeout);
      if (generation === this.generation && this.abort === abort) {
        this.abort = null;
        if (this.pointer && !terminal(this.batch)) this.timer = this.schedule(() => { this.timer = null; void this.refresh(); }, 3000);
      }
    }
  }
}
