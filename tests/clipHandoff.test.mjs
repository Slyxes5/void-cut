import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import ts from 'typescript';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

const file = new URL('../src/services/clipHandoff.ts', import.meta.url);
const code = existsSync(file) ? ts.transpileModule(readFileSync(file, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText : 'export {}';
const handoff = await import('data:text/javascript;base64,' + Buffer.from(code).toString('base64'));
const id = '0123456789abcdef0123456789abcdef';
const videoId = 'dQw4w9WgXcQ';
const sourceUrl = `https://www.youtube.com/watch?v=${videoId}`;
const now = 1800000000;
const payload = () => ({ job_id: id, source_url: sourceUrl,
  preset: { duration: '30s', model: 'gemini-3.8-flash', target_clip_count: 'auto', range_start: null, range_end: null },
  created_at: now - 60, expires_at: now + 60,
  analysis: { video_id: videoId, title: 'Imported video', duration: 90, summary: 'Summary',
    heatmap: [{ start_time: 0, end_time: 30, value: 80 }],
    clips: [{ title: 'First clip', start_time: 1, end_time: 31, virality_score: 85, key_quotes: ['quote'], transcript: 'words' }],
    transcript: [{ start: 1, end: 3, text: 'words' }] } });
function setup(data = payload(), options = {}) {
  const values = new Map([['cheat_clip_cache_old_30s', '{"old":"edits"}'], ['cheat_clip_ts_old_30s', 'old timestamp']]);
  const imports = [], errors = [], requests = [];
  const storage = { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) };
  const run = () => {
    assert.equal(typeof handoff.importClipHandoff, 'function', 'authenticated handoff importer must exist');
    return handoff.importClipHandoff({ hash: `#clip=${id}`, storage, now: () => now,
      fetcher: async (url, init) => { requests.push([url, init]); return Response.json(data); },
      onImport: value => imports.push(value), onError: message => errors.push(message), ...options });
  };
  return { values, storage, imports, errors, requests, run };
}

test('hydrates a completed handoff with only one authenticated GET and compatible isolated history', async () => {
  const h = setup(); await h.run();
  assert.equal(h.imports.length, 1);
  assert.deepEqual(h.imports[0].analysis, payload().analysis);
  assert.equal(h.imports[0].source_url, sourceUrl);
  assert.deepEqual(h.requests.map(([url]) => url), [`/api/clip/handoffs/${id}`]);
  assert.equal(h.requests[0][1].method, 'GET');
  assert.equal(h.requests[0][1].credentials, 'same-origin');
  assert.equal(h.requests[0][1].cache, 'no-store');
  const suffix = `30s_model_gemini-3.8-flash_clips_auto_handoff_${id}`;
  assert.deepEqual(JSON.parse(h.values.get(`cheat_clip_cache_${videoId}_${suffix}`)), payload().analysis);
  assert.equal(h.values.get(`cheat_clip_ts_${videoId}_${suffix}`), new Date((now - 60) * 1000).toISOString());
  assert.equal(h.values.get('cheat_clip_cache_old_30s'), '{"old":"edits"}');
  assert.equal(h.values.get('cheat_clip_ts_old_30s'), 'old timestamp');
  assert.deepEqual(h.errors, []);
});

test('ignores unrelated fragments and rejects malformed private IDs without fetching or touching history', async () => {
  for (const hash of ['', '#other=x', '#clip=', `#clip=${id.toUpperCase()}`, `#clip=${id}0`, '#clip=../secret', `#clip=${id}&token=secret`, `#clip=${id}%20`]) {
    const h = setup(payload(), { hash }); const before = [...h.values]; await h.run();
    assert.deepEqual(h.requests, [], hash);
    assert.deepEqual(h.imports, [], hash);
    assert.deepEqual([...h.values], before, hash);
    if (hash.startsWith('#clip=')) assert.match(h.errors[0], /invalid.*handoff/i);
    else assert.deepEqual(h.errors, []);
  }
});

test('invalid and expired payloads preserve every old cache entry and never import', async () => {
  const mutations = [
    d => { d.job_id = 'f'.repeat(32); }, d => { d.source_url = 'https://evil.example/watch?v=' + videoId; },
    d => { d.source_url += '&api_key=secret'; }, d => { d.preset.model = 'other'; },
    d => { d.preset.duration = '60s'; }, d => { d.preset.target_clip_count = 10; },
    d => { d.preset.range_start = 0; }, d => { delete d.preset.range_end; },
    d => { d.created_at = 'yesterday'; }, d => { d.created_at = now + 1; },
    d => { d.expires_at = now; }, d => { d.expires_at = d.created_at; },
    d => { d.analysis.video_id = 'differentID'; }, d => { d.analysis.duration = -1; },
    d => { d.analysis.title = {}; }, d => { d.analysis.summary = []; },
    d => { d.analysis.clips = {}; }, d => { d.analysis.clips[0].start_time = -1; },
    d => { d.analysis.clips[0].end_time = 100; }, d => { d.analysis.clips[0].end_time = 1; },
    d => { d.analysis.clips[0].virality_score = '85'; }, d => { d.analysis.clips[0].key_quotes = [5]; },
    d => { d.analysis.clips[0].transcript = null; }, d => { d.analysis.clips[0].title_suggestion = []; },
    d => { d.analysis.clips[0].edited_subtitle_words = [{ word: 3 }]; },
    d => { d.analysis.heatmap[0].value = null; }, d => { d.analysis.heatmap[0].end_time = 1000; },
    d => { d.analysis.transcript = [{ start: 0, end: 3, text: {} }]; },
    d => { d.analysis.source_type = 'upload'; }, d => { d.analysis.video_url = 'https://evil.example/video'; },
  ];
  for (const mutate of mutations) {
    const d = payload(); mutate(d); const h = setup(d); const before = [...h.values]; await h.run();
    assert.deepEqual(h.imports, [], mutate.toString());
    assert.deepEqual([...h.values], before);
    assert.match(h.errors[0], /invalid|expired/i);
  }
  for (const data of [null, [], {}, { ...payload(), analysis: null }]) {
    const h = setup(data); await h.run(); assert.equal(h.imports.length, 0); assert.match(h.errors[0], /invalid/i);
  }
});

test('accepts the real backend nullable fields and fractional Unix timestamps', async () => {
  const d = payload();
  d.created_at += 0.125; d.expires_at += 0.125;
  Object.assign(d.analysis, { channel: null, model: null, video_url: null });
  d.analysis.transcript[0].engagement = null;
  const h = setup(d); await h.run();
  assert.equal(h.imports.length, 1);
  assert.deepEqual(h.errors, []);
  assert.equal(h.imports[0].analysis.video_url, undefined);
  assert.equal(h.imports[0].analysis.transcript[0].engagement, undefined);
  const noTranscript = setup({ ...d, analysis: { ...d.analysis, transcript: null } });
  await noTranscript.run(); assert.equal(noTranscript.imports.length, 1);
  assert.equal(noTranscript.imports[0].analysis.transcript, undefined);
});

test('accepts real caption boundary overshoot without rejecting completed analysis', async () => {
  const d = payload(); d.analysis.transcript = [{ start: 89, end: 90.25, text: 'Final caption' }];
  const h = setup(d); await h.run();
  assert.equal(h.imports.length, 1);
  assert.deepEqual(h.errors, []);
  assert.equal(h.imports[0].analysis.transcript[0].end, 90);
});

test('canonical YouTube video_url is normalized to the existing iframe player path', async () => {
  const d = payload(); d.analysis.video_url = sourceUrl; d.analysis.source_type = 'youtube';
  const h = setup(d); await h.run();
  assert.equal(h.imports[0].analysis.video_url, undefined);
  assert.equal(h.imports[0].analysis.source_type, 'youtube');
});

test('missing, expired, unauthorized, malformed JSON and network failures preserve workspace/history', async () => {
  for (const status of [404, 410, 401, 403, 500]) {
    const h = setup(payload(), { fetcher: async () => Response.json(payload(), { status }) });
    const before = [...h.values]; await h.run();
    assert.deepEqual(h.imports, []); assert.deepEqual([...h.values], before);
    assert.match(h.errors[0], status === 404 ? /not found/i : status === 410 ? /expired/i : status === 401 || status === 403 ? /sign in|permission/i : /unavailable/i);
  }
  for (const fetcher of [async () => { throw new Error('network offline'); }, async () => new Response('{bad json')]) {
    const h = setup(payload(), { fetcher }); const before = [...h.values]; await h.run();
    assert.deepEqual(h.imports, []); assert.deepEqual([...h.values], before);
    assert.match(h.errors[0], /connection|invalid/i);
  }
});

test('quota failures leave the imported editor usable and roll back partial new history', async () => {
  for (const failAt of [1, 2]) {
    const h = setup(); const before = [...h.values]; let writes = 0;
    const set = h.storage.setItem;
    h.storage.setItem = (key, value) => { if (++writes === failAt) throw new DOMException('full', 'QuotaExceededError'); set(key, value); };
    await h.run();
    assert.equal(h.imports.length, 1);
    assert.match(h.errors[0], /storage.*full|quota/i);
    assert.match(h.errors[0], /editor.*usable|continue editing/i);
    assert.deepEqual([...h.values], before);
  }
});

test('reopening the same handoff never overwrites its cached edits or original timestamp', async () => {
  const h = setup(); const key = `cheat_clip_cache_${videoId}_30s_model_gemini-3.8-flash_clips_auto_handoff_${id}`;
  h.values.set(key, '{"existing":"edited analysis"}'); h.values.set(key.replace('cache_', 'ts_'), 'old timestamp');
  const before = [...h.values]; await h.run(); assert.deepEqual([...h.values], before); assert.equal(h.imports.length, 1);
});

test('cancellation before fetch and after either await prevents all state and history changes', async () => {
  for (const stage of ['before', 'fetch', 'json']) {
    const controller = new AbortController(); let calls = 0;
    const h = setup(payload(), { signal: controller.signal, fetcher: async () => {
      calls++; if (stage === 'fetch') controller.abort();
      return { ok: true, json: async () => { if (stage === 'json') controller.abort(); return payload(); } };
    } });
    if (stage === 'before') controller.abort();
    const before = [...h.values]; await h.run();
    assert.deepEqual(h.imports, []); assert.deepEqual(h.errors, []); assert.deepEqual([...h.values], before);
    if (stage === 'before') assert.equal(calls, 0);
  }
});

// Exercise App's real mount effects and state setters without a DOM renderer.
// Only JSX's final return is removed; the importer, history scanner and player code stay real.
const appSource = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8');
const appAst = ts.createSourceFile('App.tsx', appSource, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const appNode = appAst.statements.find(n => ts.isFunctionDeclaration(n) && n.name?.text === 'App');
const returnNode = appNode.body.statements.find(ts.isReturnStatement);
const stateNames = appNode.body.statements.filter(ts.isVariableStatement).flatMap(s => s.declarationList.declarations)
  .filter(d => d.initializer && ts.isCallExpression(d.initializer) && d.initializer.expression.getText(appAst) === 'useState')
  .map(d => d.name.elements[0].name.text);
const body = appSource.slice(appNode.getStart(appAst), returnNode.getStart(appAst)).replace('export default ', '') + '\nreturn { loadFromHistory };\n}';
const appCode = ts.transpileModule(body, { fileName: 'App.tsx', compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None, jsx: ts.JsxEmit.React } }).outputText;
function mountApp({ hash = `#clip=${id}`, fetcher, quota = false } = {}) {
  const values = new Map([['cheat_clip_gemini_api_key', 'fake-test-key-not-a-real-credential'], ['cheat_clip_cache_old_30s', JSON.stringify({ ...payload().analysis, video_id: 'old' })]]);
  const states = {}, refs = [], effects = [], timers = new Map(), requests = [], players = [], navigation = [];
  let stateIndex, refIndex, effectIndex, timerId = 0;
  const storage = { getItem: key => values.get(key) ?? null, key: index => [...values.keys()][index] ?? null,
    get length() { return values.size; }, setItem: (key, value) => { if (quota && key.includes('_handoff_')) throw new DOMException('full', 'QuotaExceededError'); values.set(key, value); }, removeItem: key => values.delete(key) };
  const schedule = fn => { timers.set(++timerId, fn); return timerId; };
  const window = { location: { hash, hostname: 'localhost' }, setTimeout: schedule, clearTimeout: key => timers.delete(key),
    YT: { Player: function(_target, options) { players.push(options.videoId); } } };
  const request = async (...args) => { requests.push(args); return fetcher ? fetcher(...args) : Response.json(payload()); };
  const hooks = {
    useWorkflowNavigation: () => ({page: 'source', navigate: page => navigation.push(page)}),
    useState(initial) { const name = stateNames[stateIndex++]; if (!(name in states)) states[name] = typeof initial === 'function' ? initial() : initial;
      return [states[name], next => { states[name] = typeof next === 'function' ? next(states[name]) : next; }]; },
    useRef(initial) { const i = refIndex++; return refs[i] ??= { current: initial }; },
    useEffect(fn, deps) { const i = effectIndex++; const old = effects[i];
      if (!old || deps?.some((d, j) => d !== old.deps?.[j])) effects[i] = { fn, deps, pending: true, cleanup: old?.cleanup }; },
    useMemo: fn => fn(), useCallback: fn => fn(), useLanguage: () => ({ t: { loading: { loadingFromHistory: 'history', restoringHotspots: 'restore', reconstructingTimestamps: 'timestamps' }, form: { historyLoadFailed: 'History load failed' } } }), hasStoredCookies: () => false,
    localStorage: storage, window, document: { getElementById: () => ({ innerHTML: '' }) },
    setTimeout: schedule, clearTimeout: key => timers.delete(key),
    resilientFetch: request,
    importClipHandoff: opts => handoff.importClipHandoff({ ...opts, fetcher: request, now: () => now }),
  };
  const App = new Function(...Object.keys(hooks), appCode + '\nreturn App;')(...Object.values(hooks));
  const actions = {};
  const render = () => {
    stateIndex = refIndex = effectIndex = 0; Object.assign(actions, App());
    for (const effect of effects) {
      if (effect.pending && /importClipHandoff|fetchModels/.test(effect.fn.toString())) {
        effect.cleanup?.(); effect.cleanup = effect.fn(); effect.pending = false;
      }
    }
  };
  const tick = async () => { await new Promise(resolve => setImmediate(resolve)); const queued = [...timers.values()]; timers.clear(); queued.forEach(fn => fn()); await new Promise(resolve => setImmediate(resolve)); };
  render();
  return { states, values, requests, players, effects, window, render, tick, actions, navigation,
    unmount: () => effects.forEach(effect => effect.cleanup?.()) };
}

test('App mount hydrates existing editor, iframe, ClipStudio and history with no analysis or model requests', async () => {
  const app = mountApp(); await app.tick();
  assert.equal(app.states.url, sourceUrl);
  assert.equal(app.states.sourceMode, 'youtube');
  assert.deepEqual(app.states.result, payload().analysis);
  assert.deepEqual(app.states.activeClip, payload().analysis.clips[0]);
  assert.equal(app.states.durationPref, '30s'); assert.equal(app.states.selectedModel, 'gemini-3.8-flash');
  assert.equal(app.states.clipCountMode, 'auto'); assert.equal(app.states.rangeType, 'entire');
  assert.equal(app.states.customRangeStart, ''); assert.equal(app.states.customRangeEnd, '');
  assert.equal(app.states.loading, false); assert.equal(app.states.currentStep, 4);
  assert.equal(app.states.overallProgress, 100); assert.deepEqual(app.states.stepProgress, { 1: 100, 2: 100, 3: 100, 4: 100 });
  assert.equal(app.states.showHistory, false); assert.equal(app.states.history.length, 2);
  const entry = app.states.history.find(e => e.video_id === videoId);
  assert.ok(entry.range_suffix.endsWith(`_handoff_${id}`)); assert.equal(entry.duration_pref, '30s');
  assert.equal(entry.analyzed_at, new Date((now - 60) * 1000).toISOString());
  assert.deepEqual(app.players, [videoId]);
  assert.deepEqual(app.requests.map(([url]) => url), [`/api/clip/handoffs/${id}`]);
  assert.match(appSource, /videoUrl=\{result\.video_url \|\| url\}/);
  assert.match(appSource, /allClips=\{result\.clips\}/);
  assert.match(appSource, /activeClip=\{activeClip\}/);
  app.states.result = { unsaved: 'editor changes' };
  app.window.location.hash = '#clip=' + 'f'.repeat(32); app.render(); await app.tick();
  assert.deepEqual(app.states.result, { unsaved: 'editor changes' });
  assert.equal(app.requests.length, 1, 'same-tab hash changes must not replace unsaved work');
  app.unmount();
});

test('imported cache loads through the unchanged history restore path without analysis', async () => {
  const app = mountApp(); await app.tick();
  const entry = app.states.history.find(e => e.video_id === videoId);
  app.actions.loadFromHistory(entry);
  await app.tick(); await app.tick(); await app.tick();
  assert.deepEqual(app.states.result, payload().analysis);
  assert.deepEqual(app.states.activeClip, payload().analysis.clips[0]);
  assert.equal(app.states.clipCountMode, 'auto'); assert.equal(app.states.rangeType, 'entire');
  assert.equal(app.states.url, sourceUrl); assert.equal(app.requests.length, 1); app.unmount();
});

test('App failures retain the workspace, suppress model discovery, and never fall back to analysis', async () => {
  for (const config of [
    { hash: '#clip=INVALID' },
    { fetcher: async () => Response.json({}, { status: 404 }) },
    { fetcher: async () => Response.json({}, { status: 410 }) },
    { fetcher: async () => Response.json({}) },
    { fetcher: async () => { throw new Error('offline'); } },
  ]) {
    const app = mountApp(config); const before = [...app.values];
    app.states.result = { unsaved: true }; app.states.activeClip = { unsaved: 'clip' };
    await app.tick();
    assert.deepEqual(app.states.result, { unsaved: true }); assert.deepEqual(app.states.activeClip, { unsaved: 'clip' });
    assert.deepEqual([...app.values], before); assert.match(app.states.error, /handoff/i);
    assert.ok(app.requests.every(([url]) => url.startsWith('/api/clip/handoffs/'))); app.unmount();
  }
});

test('App quota warning does not clear the imported result or disable its player', async () => {
  const app = mountApp({ quota: true }); await app.tick();
  assert.deepEqual(app.states.result, payload().analysis); assert.deepEqual(app.players, [videoId]);
  assert.match(app.states.error, /quota/i); assert.equal(app.states.loading, false);
  assert.equal(app.states.history.length, 1); app.unmount();
});

test('App cleanup cancels an in-flight import before it can change state/history or start a player', async () => {
  let resolve;
  const app = mountApp({ fetcher: () => new Promise(done => { resolve = done; }) });
  const before = [...app.values]; app.unmount(); resolve(Response.json(payload())); await app.tick();
  assert.equal(app.requests[0][1].signal.aborted, true);
  assert.equal(app.states.result, null); assert.deepEqual([...app.values], before); assert.deepEqual(app.players, []);
});

test('normal non-handoff mounts retain existing auto model discovery', async () => {
  const app = mountApp({ hash: '', fetcher: async () => Response.json({ models: ['test-flash'] }) }); await app.tick();
  assert.deepEqual(app.requests.map(([url]) => url), ['/api/models']); app.unmount();
});

test('AuthGate preserves the fragment through login and hydration uses the installed authenticated fetch', async () => {
  function compile(path) {
    let text = ts.transpileModule(readFileSync(new URL(path, import.meta.url), 'utf8'), {
      fileName: path, compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
    }).outputText;
    for (const name of ['react/jsx-runtime', 'react']) text = text.replaceAll(`from "${name}"`, `from ${JSON.stringify(import.meta.resolve(name))}`).replaceAll(`from '${name}'`, `from ${JSON.stringify(import.meta.resolve(name))}`);
    return text;
  }
  const moduleUrl = text => 'data:text/javascript;base64,' + Buffer.from(text).toString('base64');
  const clientUrl = moduleUrl(compile('../src/auth/client.ts'));
  const { AuthSession, createAuthFetch } = await import(clientUrl);
  const { AuthGate } = await import(moduleUrl(compile('../src/auth/AuthGate.tsx').replace(/from ['"]\.\/client['"]/g, `from ${JSON.stringify(clientUrl)}`)));
  const requests = [], originalWindow = globalThis.window, originalFetch = globalThis.fetch;
  const location = new URL(`https://private.example/#clip=${id}`); const originalUrl = location.href;
  globalThis.window = { location };
  const session = new AuthSession(async () => Response.json({ configured: true, authenticated: true, csrf_token: 'test-csrf' }));
  const render = () => renderToStaticMarkup(React.createElement(AuthGate, { session }, React.createElement('div', null, 'handoff-editor')));
  try {
    assert.doesNotMatch(render(), /handoff-editor/);
    await session.login('test-only-password'); assert.match(render(), /handoff-editor/);
    assert.equal(location.href, originalUrl);
    globalThis.fetch = createAuthFetch(async (url, init) => { requests.push([url, init]); return Response.json(payload()); }, location.origin, () => 'test-csrf', () => assert.fail('unexpected expiry'));
    const h = setup(payload(), { fetcher: undefined, hash: location.hash }); await h.run();
    assert.equal(h.imports.length, 1); assert.equal(requests.length, 1);
    assert.equal(requests[0][0], `/api/clip/handoffs/${id}`);
    assert.equal(requests[0][1].credentials, 'same-origin'); assert.equal(location.href, originalUrl);
  } finally { globalThis.fetch = originalFetch; if (originalWindow === undefined) delete globalThis.window; else globalThis.window = originalWindow; }
});

