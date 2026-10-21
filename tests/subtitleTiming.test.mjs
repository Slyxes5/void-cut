import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';

async function load(path) {
  const source = readFileSync(new URL(path, import.meta.url), 'utf8');
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
  return import('data:text/javascript;base64,' + Buffer.from(code).toString('base64'));
}
const timing = await load('../src/utils/subtitleTiming.ts');
const clip = {start_time: 10, end_time: 12};
const word = (id, text, start, end) => ({id, word: text, start, end, group: 0});

test('preview preserves rapid words, pauses, punctuation, and chunk boundaries', () => {
  const words = [word('a', 'Halo', 10, 10.06), word('b', 'ini.', 10.06, 10.12),
    word('c', 'berikut', 10.12, 10.18), word('d', 'jeda', 11, 11.1)];
  const frames = timing.buildCaptionFrames(words, clip, false, 25);
  assert.deepEqual(frames.map(f => [f.start, f.end]), [[0, .06], [.06, .12], [.12, .18], [1, 1.1]]);
  assert.deepEqual(frames.map(f => f.words.map(w => w.id)), [['a','b'], ['a','b'], ['c'], ['d']]);
  assert.equal(frames.find(f => .5 >= f.start && .5 < f.end), undefined);
  assert.equal(frames.find(f => .06 >= f.start && .06 < f.end).activeId, 'b');
});

test('line-only editor fallback crops after distributing the complete line', () => {
  const words = timing.wordsFromTranscript({start_time: 5, end_time: 7}, [
    {start: 0, end: 10, text: 'zero one two three four five six seven eight nine'},
  ]);
  assert.deepEqual(words.map(w => [w.word, w.start, w.end]), [['five',5,6], ['six',6,7]]);
});

test('editor uses acoustic timings when available and does not mutate input', () => {
  const transcript = [{start: 10, end: 12, text: 'Halo semua', words: [
    {word: 'Halo', start: 10.2, end: 10.3}, {word: 'semua', start: 10.6, end: 10.8},
  ]}];
  const original = structuredClone(transcript);
  assert.deepEqual(timing.wordsFromTranscript(clip, transcript).map(w => w.start), [10.2,10.6]);
  assert.deepEqual(transcript, original);
});

test('overlap is trimmed without changing next onset; clip bounds and ASS rounding agree', () => {
  const words = [word('a','a',9,10.3), word('b','b',10.1,13)];
  assert.deepEqual(timing.normalizeSubtitleWords(words, clip).map(w => [w.start,w.end]), [[10,10.1],[10.1,12]]);
  const fractional = {start_time: 10.123, end_time: 12};
  const frames = timing.buildCaptionFrames([word('x','x',10.124,11.122)], fractional, false,25);
  assert.deepEqual(frames.map(f => [f.start,f.end]), [[0,1]]);
});

test('plain preview breaks at speech pauses and sentence endings', () => {
  const frames = timing.buildCaptionFrames([word('a','Satu.',10,10.2),word('b','Dua',10.2,10.3),
    word('c','Tiga',11,11.2)], clip, true,25);
  assert.deepEqual(frames.map(f => f.words.map(w=>w.id)), [['a'],['b'],['c']]);
});

test('sync submits exact clip bounds and refuses approximate or malformed responses', async () => {
  const service = await load('../src/services/subtitleSync.ts');
  const original = globalThis.fetch;
  try {
    let request;
    globalThis.fetch = async (url, init) => { request = [url,init]; return Response.json({
      timing_source:'audio', words:[word('a','Halo',10.1,10.2)],
    }); };
    const result = await service.syncSubtitleAudio('https://youtu.be/example', clip, 'test-only-cookie');
    assert.equal(result[0].start,10.1);
    assert.equal(request[0],'/api/subtitles/sync');
    assert.equal(request[1].credentials,'same-origin');
    assert.deepEqual(JSON.parse(request[1].body), {video_url:'https://youtu.be/example',start_time:10,end_time:12,cookies:'test-only-cookie'});
    globalThis.fetch = async () => Response.json({timing_source:'estimate',words:[]});
    await assert.rejects(service.syncSubtitleAudio('source',clip),/tidak valid/);
    globalThis.fetch = async () => Response.json({detail:'Whisper unavailable'},{status:500});
    await assert.rejects(service.syncSubtitleAudio('source',clip),/Whisper unavailable/);
  } finally { globalThis.fetch = original; }
});
