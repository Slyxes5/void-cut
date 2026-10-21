import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import ts from 'typescript';

const file = new URL('../src/services/batchRecovery.ts', import.meta.url);
const code = existsSync(file) ? ts.transpileModule(readFileSync(file, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText : 'export {}';
const recovery = await import('data:text/javascript;base64,' + Buffer.from(code).toString('base64'));
const id = 'batch_1_abcdef';
const snapshot = (status = 'running') => ({ batch_id: id, total_clips: 1, current_clip_index: 0,
  overall_status: status, clips: [{ clip_index: 0, title: 'Layout test clip',
    status: status === 'completed' ? 'completed' : 'rendering', progress_percent: status === 'completed' ? 100 : 70,
    download_available: status === 'completed', ...(status === 'completed' ? { download_url: '/api/download-rendered/clip_1_' + id + '.mp4' } : {}) }],
  zip_available: status === 'completed', ...(status === 'completed' ? { zip_url: '/api/download-batch-zip/' + id } : {}) });
const flush = () => new Promise(resolve => setImmediate(resolve));
function setup(responses = [snapshot()], sharedStorage = null, overrides = {}) {
  assert.equal(typeof recovery.BatchRecovery, 'function', 'BatchRecovery controller must exist');
  const data = new Map(); const storage = { getItem: k => data.get(k) ?? null, setItem: (k,v) => data.set(k,v), removeItem: k => data.delete(k) };
  const events = []; const requests = []; const batches = []; const connections = []; const timers = new Map(); let timerId = 0;
  const controller = new recovery.BatchRecovery({ storage: sharedStorage || storage, onBatch: b => batches.push(b), onConnection: c => connections.push(c),
    createRequestId: () => 'request-test-12345678',
    fetcher: async (url) => { requests.push(url); const next = responses.length > 1 ? responses.shift() : responses[0];
      if (next instanceof Error) throw next; return new Response(JSON.stringify(next), { status: next?.httpStatus || 200 }); },
    makeEventSource: url => { const source = { url, close() { this.closed = true; }, closed: false, onmessage: null, onerror: null, onopen: null }; events.push(source); return source; },
    schedule: fn => { const key = ++timerId; timers.set(key, fn); return key; }, cancel: key => timers.delete(key),
    ...overrides,
  });
  return { controller, data, storage, events, requests, batches, connections, timers };
}

test('SSE failure falls back to status GET and keeps completed download actions', async () => {
  const h = setup([snapshot(), snapshot('completed')]);
  h.controller.watch(id); await flush();
  assert.equal(h.batches.at(-1).overall_status, 'running');
  h.events[0].onerror(new Event('error')); await flush();
  assert.equal(h.events[0].closed, true);
  assert.equal(h.batches.at(-1).overall_status, 'completed');
  assert.equal(h.batches.at(-1).clips[0].download_available, true);
  assert.equal(h.batches.at(-1).zip_available, true);
  assert.ok(h.requests.every(url => url.startsWith('/api/render-status/')));
  assert.equal(h.timers.size, 0);
  h.controller.stop();
});

test('reload restores only a saved batch ID and revalidates completed results', async () => {
  const first = setup(); first.controller.watch(id); await flush(); first.controller.stop();
  const saved = JSON.parse(first.storage.getItem(recovery.BATCH_RECOVERY_KEY));
  assert.deepEqual(saved, { batch_id: id });
  const next = setup([snapshot('completed')], first.storage);
  assert.equal(typeof next.controller.restore, 'function', 'reload recovery must exist');
  next.controller.restore(); await flush();
  assert.equal(next.batches.at(-1).overall_status, 'completed');
  assert.equal(next.batches.at(-1).clips[0].download_available, true);
  next.controller.stop();
});

test('submission lock survives lost POST response and blocks accidental duplicates', async () => {
  const h = setup([snapshot('completed')]);
  assert.equal(typeof h.controller.beginSubmission, 'function', 'durable submission guard must exist');
  const requestId = h.controller.beginSubmission();
  assert.equal(requestId, 'request-test-12345678');
  assert.equal(h.controller.beginSubmission(), null);
  assert.deepEqual(JSON.parse(h.storage.getItem(recovery.BATCH_RECOVERY_KEY)), { request_id: requestId });
  h.controller.resume(); await flush();
  assert.ok(h.requests[0].startsWith('/api/render-request/'));
  assert.equal(h.batches.at(-1).overall_status, 'completed');
  assert.equal(h.controller.isBlocked(), false);
  h.controller.dismiss();
  assert.equal(h.storage.getItem(recovery.BATCH_RECOVERY_KEY), null);
  h.controller.stop();
});

test('a failed stream does not reconnect in a tight loop while polling running jobs', async () => {
  const h = setup(); h.controller.watch(id); await flush();
  h.events[0].onerror(new Event('error')); await flush();
  assert.equal(h.events.length, 1, 'do not recreate broken SSE on every status request');
  assert.equal(h.connections.at(-1).mode, 'polling');
  h.controller.stop();
});

test('network failure keeps the batch locked and foreground resume recovers completion', async () => {
  const h = setup([new Error('offline'), snapshot('completed')]);
  h.controller.watch(id); await flush();
  assert.equal(h.connections.at(-1).mode, 'offline');
  assert.equal(h.controller.isBlocked(), true);
  h.controller.resume(); await flush();
  assert.equal(h.batches.at(-1).overall_status, 'completed');
  assert.equal(h.controller.isBlocked(), false);
  h.controller.stop();
});

test('stale HTTP response cannot overwrite a newer terminal SSE snapshot', async () => {
  let resolve;
  const pending = new Promise(r => { resolve = r; });
  const h = setup([], null, { fetcher: () => pending });
  h.controller.watch(id);
  h.events[0].onmessage({ data: JSON.stringify(snapshot('completed')) });
  resolve(new Response(JSON.stringify(snapshot()))); await flush();
  assert.equal(h.batches.at(-1).overall_status, 'completed');
  assert.equal(h.timers.size, 0);
  h.controller.stop();
});

test('missing job after refresh removes previously confirmed stale download links', async () => {
  const h = setup([snapshot('completed'), { httpStatus:404 }]);
  h.controller.watch(id); await flush();
  assert.equal(h.batches.at(-1).clips[0].download_available, true);
  h.controller.resume(); await flush();
  assert.equal(h.connections.at(-1).mode, 'missing');
  assert.equal(h.batches.at(-1).clips[0].download_available, false);
  assert.equal(h.batches.at(-1).zip_available, false);
  h.controller.stop();
});

test('malformed SSE switches to polling without declaring render failure', async () => {
  const h = setup(); h.controller.watch(id); await flush();
  h.events[0].onmessage({ data:'not-json' }); await flush();
  assert.equal(h.batches.at(-1).overall_status, 'running');
  assert.equal(h.connections.at(-1).mode, 'polling');
  h.controller.stop();
});

test('blocked browser storage and unavailable EventSource still allow polling', async () => {
  const h = setup([snapshot('completed')], { getItem(){throw new Error('blocked');}, setItem(){throw new Error('blocked');}, removeItem(){} },
    { makeEventSource(){throw new Error('unsupported');} });
  h.controller.restore(); h.controller.watch(id); await flush();
  assert.equal(h.batches.at(-1).overall_status, 'completed');
  h.controller.stop();
});

test('download links require backend confirmation and safe same-origin URLs', async () => {
  const value = snapshot('completed'); value.clips[0].download_url = 'javascript:alert(1)';
  value.zip_available = false;
  const h = setup([value]); h.controller.watch(id); await flush();
  assert.equal(h.batches.at(-1).clips[0].download_url, undefined);
  assert.equal(h.batches.at(-1).zip_url, undefined);
  h.controller.stop();
});

test('request IDs fall back to getRandomValues when randomUUID is unavailable', () => {
  const original = Object.getOwnPropertyDescriptor(globalThis, 'crypto');
  Object.defineProperty(globalThis, 'crypto', { configurable:true, value:{ getRandomValues(bytes){ return bytes.fill(7); } } });
  const h=setup([snapshot()],null,{createRequestId:undefined});
  try { assert.match(h.controller.beginSubmission(), /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/); }
  finally { h.controller.stop(); Object.defineProperty(globalThis,'crypto',original); }
});

test('dismissal and selecting a different batch revoke ownership of an old submission', () => {
  const h=setup(); const requestId=h.controller.beginSubmission();
  assert.equal(h.controller.ownsRequest(requestId),true);
  h.controller.dismiss(); assert.equal(h.controller.ownsRequest(requestId),false);
  h.controller.watch(id); assert.equal(h.controller.ownsRequest(requestId),false);
  assert.equal(h.controller.isWatching(id),true); h.controller.stop();
});
