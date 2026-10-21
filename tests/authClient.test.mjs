import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import ts from 'typescript';

async function load(relative) {
  const file = new URL(relative, import.meta.url);
  const source = existsSync(file) ? readFileSync(file, 'utf8') : 'export {}';
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
  return import('data:text/javascript;base64,' + Buffer.from(code).toString('base64'));
}
const auth = await load('../src/auth/client.ts');
test('cross-origin and non-API fetches are forwarded untouched, including init identity', async () => {
  const h = setup();
  for (const url of ['https://other.example/api/upload', '//other.example/api/upload', '/assets/font.woff']) {
    const init = { method: 'POST', headers: { 'X-Custom': 'caller' }, credentials: 'omit' };
    await h.fetcher(url, init);
    assert.equal(h.calls.at(-1)[0], url);
    assert.equal(h.calls.at(-1)[1], init);
  }
});
test('Request method, body, headers and init overrides follow native fetch semantics', async () => {
  const h = setup();
  const request = new Request('http://localhost:5173/api/render', { method: 'PATCH', headers: { 'X-Original': 'yes' }, body: 'original' });
  await h.fetcher(request);
  assert.equal(h.calls[0][0], request);
  assert.equal(new Headers(h.calls[0][1].headers).get('X-Original'), 'yes');
  assert.equal(new Headers(h.calls[0][1].headers).get('X-CSRF-Token'), 'memory-only-token');
  assert.equal(await request.clone().text(), 'original');
  await h.fetcher(request, { method: 'GET', headers: { 'X-Override': 'yes' } });
  const options = h.calls[1][1];
  assert.equal(options.method, 'GET');
  assert.equal(new Headers(options.headers).get('X-CSRF-Token'), null);
  assert.equal(new Headers(options.headers).get('X-Original'), null);
  assert.equal(new Headers(options.headers).get('X-Override'), 'yes');
  await h.fetcher(new Request('http://localhost:5173/api/render'), { method: 'DELETE', headers: { 'X-CSRF-Token': 'stale' } });
  assert.equal(new Headers(h.calls[2][1].headers).get('X-CSRF-Token'), 'memory-only-token');
});
test('401 emits expiry once per request without replay; auth routes and cross-origin are excluded', async () => {
  const h = setup(401);
  const response = await h.fetcher('/api/render', { method: 'POST', body: 'not-replayed' });
  assert.equal(response.status, 401);
  assert.equal(h.calls.length, 1);
  assert.deepEqual(h.events, ['expired']);
  for (const url of ['/api/auth/login', '/api/auth/status', '/api/auth/logout', 'https://other.example/api/render']) await h.fetcher(url, { method: 'POST' });
  assert.deepEqual(h.events, ['expired']);
  assert.equal(new Headers(h.calls[1][1].headers).get('X-CSRF-Token'), null);
  assert.equal(new Headers(h.calls[3][1].headers).get('X-CSRF-Token'), 'memory-only-token');
});
test('session starts locked, status refresh rotates CSRF, and expiry preserves the mounted editor flag', async () => {
  assert.equal(typeof auth.AuthSession, 'function', 'session controller exists');
  const replies = [
    { configured: true, authenticated: true, csrf_token: 'first' },
    { configured: true, authenticated: true, csrf_token: 'second' },
    { configured: true, authenticated: false },
  ];
  const calls = [];
  const session = new auth.AuthSession(async (...args) => { calls.push(args); return Response.json(replies.shift()); });
  assert.equal(session.getSnapshot().authenticated, false);
  assert.equal(session.getSnapshot().everAuthenticated, false);
  assert.equal(session.getSnapshot().phase, 'checking');
  let updates = 0; const unsubscribe = session.subscribe(() => updates++);
  await session.refresh();
  assert.equal(session.getSnapshot().authenticated, true);
  assert.equal(auth.getCsrfToken(), 'first');
  await session.refresh();
  assert.equal(auth.getCsrfToken(), 'second');
  await session.refresh();
  assert.equal(auth.getCsrfToken(), undefined);
  assert.equal(session.getSnapshot().authenticated, false);
  assert.equal(session.getSnapshot().everAuthenticated, true);
  assert.ok(updates >= 3);
  assert.ok(calls.every(([url, options]) => url === '/api/auth/status' && options.cache === 'no-store'));
  unsubscribe();
});
test('login sends password only in JSON once, authenticates on success, and sanitizes failures without retry', async () => {
  for (const status of [200, 401, 429, 503, 500]) {
    const calls = [];
    const session = new auth.AuthSession(async (...args) => { calls.push(args); return Response.json(status === 200
      ? { configured: true, authenticated: true, csrf_token: 'login-token' }
      : { detail: 'DO-NOT-EXPOSE-secret' }, { status }); });
    await session.login('test-password');
    assert.equal(calls.length, 1);
    assert.equal(calls[0][0], '/api/auth/login');
    assert.equal(calls[0][1].method, 'POST');
    assert.equal(new Headers(calls[0][1].headers).get('Content-Type'), 'application/json');
    assert.deepEqual(JSON.parse(calls[0][1].body), { password: 'test-password' });
    assert.equal(session.getSnapshot().authenticated, status === 200);
    assert.equal(session.getSnapshot().busy, false);
    assert.doesNotMatch(session.getSnapshot().message, /secret|test-password/);
    if (status === 401) assert.match(session.getSnapshot().message, /Invalid password/);
    if (status === 429) assert.match(session.getSnapshot().message, /Too many/);
    if (status === 503) assert.equal(session.getSnapshot().configured, false);
  }
});
test('failed status fails closed with a safe message; stale status cannot undo expiry or login', async () => {
  const failed = new auth.AuthSession(async () => { throw new Error('DO-NOT-EXPOSE-secret'); });
  await failed.refresh();
  assert.equal(failed.getSnapshot().phase, 'ready');
  assert.equal(failed.getSnapshot().authenticated, false);
  assert.match(failed.getSnapshot().message, /Could not check/);
  assert.doesNotMatch(failed.getSnapshot().message, /secret/);
  let resolve;
  const session = new auth.AuthSession(() => new Promise(done => { resolve = done; }));
  const pending = session.refresh();
  session.expire();
  resolve(Response.json({ configured: true, authenticated: true, csrf_token: 'stale' }));
  await pending;
  assert.equal(session.getSnapshot().authenticated, false);
  assert.equal(auth.getCsrfToken(), undefined);
});
test('logout locks without clearing editor state and sends one CSRF-protected POST, even on failure', async () => {
  for (const status of [200, 403, 401, 500]) {
    const calls = [];
    const session = new auth.AuthSession(async (...args) => {
      calls.push(args);
      return Response.json(calls.length === 1 ? { configured: true, authenticated: true, csrf_token: 'logout-token' } : { authenticated: false }, { status: calls.length === 1 ? 200 : status });
    });
    await session.refresh();
    await session.logout();
    assert.equal(calls.length, 2);
    assert.equal(calls[1][0], '/api/auth/logout');
    assert.equal(calls[1][1].method, 'POST');
    assert.equal(new Headers(calls[1][1].headers).get('X-CSRF-Token'), 'logout-token');
    assert.equal(session.getSnapshot().authenticated, false);
    assert.equal(session.getSnapshot().everAuthenticated, true);
    assert.equal(auth.getCsrfToken(), undefined);
    if (status === 403 || status === 500) assert.match(session.getSnapshot().message, /could not be confirmed/);
  }
});
test('login invalidates an earlier status request and prevents concurrent password submissions', async () => {
  let resolveStatus, resolveLogin; let calls = 0;
  const session = new auth.AuthSession(async (url) => { calls++; return new Promise(resolve => {
    if (url.endsWith('/status')) resolveStatus = resolve; else resolveLogin = resolve;
  }); });
  const pendingStatus = session.refresh();
  const pendingLogin = session.login('one');
  await session.login('two');
  assert.equal(calls, 2);
  resolveLogin(Response.json({ configured: true, authenticated: true, csrf_token: 'fresh' }));
  await pendingLogin;
  resolveStatus(Response.json({ configured: true, authenticated: false }));
  await pendingStatus;
  assert.equal(session.getSnapshot().authenticated, true);
  assert.equal(auth.getCsrfToken(), 'fresh');
});
test('installed wrapper dispatches session-expired and is installed only once', async () => {
  assert.equal(typeof auth.installAuthFetch, 'function');
  const target = new EventTarget();
  target.location = { origin: 'http://localhost:5173' };
  let calls = 0, expired = 0;
  target.fetch = async () => { calls++; return new Response(null, { status: 401 }); };
  target.addEventListener('session-expired', () => expired++);
  auth.installAuthFetch(target); const installed = target.fetch;
  auth.installAuthFetch(target);
  assert.equal(target.fetch, installed);
  await target.fetch('/api/render', { method: 'POST' });
  assert.equal(calls, 1); assert.equal(expired, 1);
});
test('mount and focus refresh status, visibility refreshes only when visible, cleanup removes listeners', async () => {
  assert.equal(typeof auth.attachAuthSession, 'function');
  const target = new EventTarget(), visibility = new EventTarget();
  visibility.visibilityState = 'visible';
  let count = 0;
  const session = new auth.AuthSession(async () => { count++; return Response.json({ configured: true, authenticated: true, csrf_token: 'rotated-' + count }); });
  const cleanup = auth.attachAuthSession(session, target, visibility);
  await new Promise(setImmediate);
  assert.equal(count, 1);
  target.dispatchEvent(new Event('focus')); await new Promise(setImmediate);
  assert.equal(count, 2); assert.equal(auth.getCsrfToken(), 'rotated-2');
  target.dispatchEvent(new Event('session-expired'));
  assert.equal(session.getSnapshot().authenticated, false);
  visibility.visibilityState = 'hidden'; visibility.dispatchEvent(new Event('visibilitychange'));
  assert.equal(count, 2);
  visibility.visibilityState = 'visible'; visibility.dispatchEvent(new Event('visibilitychange')); await new Promise(setImmediate);
  assert.equal(count, 3);
  cleanup(); target.dispatchEvent(new Event('focus'));
  assert.equal(count, 3);
});
function setup(status = 200) {
  assert.equal(typeof auth.createAuthFetch, 'function', 'auth fetch wrapper must exist');
  const calls = [], events = [];
  const fetcher = auth.createAuthFetch(async (...args) => { calls.push(args); return new Response(null, { status }); },
    'http://localhost:5173', () => 'memory-only-token', () => events.push('expired'));
  return { fetcher, calls, events };
}
test('same-origin unsafe API fetch gets memory CSRF and same-origin credentials without tokens in URL', async () => {
  const h = setup();
  await h.fetcher('/api/render-batch', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
  const [input, init] = h.calls[0];
  assert.equal(input, '/api/render-batch');
  assert.equal(init.credentials, 'same-origin');
  assert.equal(new Headers(init.headers).get('X-CSRF-Token'), 'memory-only-token');
  assert.equal(new Headers(init.headers).get('Content-Type'), 'application/json');
  assert.equal(init.body, '{}');
});
