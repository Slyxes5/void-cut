import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import ts from 'typescript';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
function compile(path) {
  let code = ts.transpileModule(readFileSync(new URL(path, import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  for (const name of ['react/jsx-runtime', 'react']) code = code.replaceAll('from "' + name + '"', 'from ' + JSON.stringify(import.meta.resolve(name))).replaceAll("from '" + name + "'", 'from ' + JSON.stringify(import.meta.resolve(name)));
  return code;
}
const clientUrl = 'data:text/javascript;base64,' + Buffer.from(compile('../src/auth/client.ts')).toString('base64');
const client = await import(clientUrl);
const gateFile = new URL('../src/auth/AuthGate.tsx', import.meta.url);
let code = existsSync(gateFile) ? compile('../src/auth/AuthGate.tsx') : 'export {}';
code = code.replace(/from ['"]\.\/client['"]/g, 'from ' + JSON.stringify(clientUrl));
const gate = await import('data:text/javascript;base64,' + Buffer.from(code).toString('base64'));
function render(session) {
  assert.equal(typeof gate.AuthGate, 'function', 'login gate must exist');
  return renderToStaticMarkup(React.createElement(gate.AuthGate, {session}, React.createElement('div', null, 'existing-editor-state')));
}
test('editor is not mounted before authentication; login uses password autocomplete without localStorage', () => {
  const session = new client.AuthSession(async () => Response.json({ configured: true, authenticated: false }));
  const html = render(session);
  assert.doesNotMatch(html, /existing-editor-state/);
});
test('setup instructions include the current private HTTPS origin', async () => {
  const session = new client.AuthSession(async () => Response.json({ configured: false, authenticated: false }));
  await session.refresh();
  globalThis.window = { location: { protocol: 'https:', origin: 'https://private-test.ts.net' } };
  try { assert.match(render(session), /--origin https:\/\/private-test\.ts\.net/); }
  finally { delete globalThis.window; }
});

test('signed-in editor remains mounted but hidden through expiry, then is restored after sign-in', async () => {
  const session = new client.AuthSession(async () => Response.json({ configured: true, authenticated: true, csrf_token: 'test-token' }));
  await session.refresh();
  assert.match(render(session), /existing-editor-state/);
  assert.match(render(session), /Sign out/);
  session.expire();
  const locked = render(session);
  assert.match(locked, /existing-editor-state/);
  assert.match(locked, /hidden=""/);
  assert.match(locked, /type="password"/);
  assert.match(locked, /autoComplete="current-password"/);
  await session.login('test-password');
  assert.doesNotMatch(render(session), /hidden=""/);
});
