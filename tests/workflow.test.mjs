import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';

const code = ts.transpileModule(readFileSync(new URL('../src/services/workflow.ts', import.meta.url), 'utf8'), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
}).outputText;
const { readWorkflowPage, workflowUrl } = await import('data:text/javascript;base64,' + Buffer.from(code).toString('base64'));

test('analysis pages require a loaded project while exports remain available after refresh', () => {
  for (const page of ['clips', 'studio']) {
    assert.equal(readWorkflowPage(`?view=${page}`, false), 'source');
    assert.equal(readWorkflowPage(`?view=${page}`, true), page);
  }
  assert.equal(readWorkflowPage('?view=exports', false), 'exports');
  assert.equal(readWorkflowPage('?view=invalid', true), 'source');
  assert.equal(readWorkflowPage('', false), 'source');
});

test('navigation preserves private handoff fragments, deployment paths and unrelated query parameters', () => {
  const base = 'https://example.test/editor?campaign=test#clip=0123456789abcdef0123456789abcdef';
  const first = workflowUrl(base, 'clips');
  assert.equal(first, '/editor?campaign=test&view=clips#clip=0123456789abcdef0123456789abcdef');
  assert.equal(workflowUrl(new URL(first, base).href, 'studio'), '/editor?campaign=test&view=studio#clip=0123456789abcdef0123456789abcdef');
});
