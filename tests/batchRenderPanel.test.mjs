import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import ts from 'typescript';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
const path = new URL('../src/components/BatchRenderRecoveryPanel.tsx', import.meta.url);
let code = existsSync(path) ? ts.transpileModule(readFileSync(path, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
}).outputText : 'export {}';
for (const name of ['react/jsx-runtime', 'react']) code = code.replaceAll('from "' + name + '"', 'from ' + JSON.stringify(import.meta.resolve(name))).replaceAll("from '" + name + "'", 'from ' + JSON.stringify(import.meta.resolve(name)));
const module = await import('data:text/javascript;base64,' + Buffer.from(code).toString('base64'));
const batch = { batch_id: 'batch_1_abcdef', total_clips: 3, current_clip_index: 3, overall_status: 'completed', zip_available: true,
 zip_url: '/api/download-batch-zip/batch_1_abcdef', clips: [0,1,2].map(i => ({ clip_index:i, title:'Clip '+(i+1), status:'completed', progress_percent:100, download_available:true, download_url:'/api/download-rendered/clip_'+i+'.mp4' })) };
function render(value = batch, connection = { mode:'idle', message:'' }) {
 assert.equal(typeof module.BatchRenderRecoveryPanel, 'function', 'standalone results panel must exist');
 return renderToStaticMarkup(React.createElement(module.BatchRenderRecoveryPanel, { batch:value, connection, onRefresh(){}, onRestore(){}, onDismiss(){} }));
}
test('standalone completed panel renders three confirmed MP4 links and ZIP without analysis state', () => {
 const html=render(); assert.equal((html.match(/>Download MP4</g)||[]).length,3); assert.match(html,/>Download ZIP</); assert.equal((html.match(/<progress/g)||[]).length,4);
});

test('missing files never produce download actions even if old URLs are present', () => {
 const missing = { ...batch, zip_available:false, clips:batch.clips.map(clip=>({...clip,download_available:false})) };
 const html=render(missing); assert.doesNotMatch(html,/>Download MP4</); assert.doesNotMatch(html,/>Download ZIP</); assert.match(html,/Output no longer available/);
});

test('recovered exports are labelled as recovered files rather than durable jobs', () => {
 const html=render({...batch,recovered_from_files:true,warning_message:'Only available exports were recovered.'});
 assert.match(html,/Recovered rendered files/); assert.match(html,/Only available exports were recovered/);
});

test('disconnected recovery remains visible without any React analysis result', () => {
 const html=render(null,{mode:'offline',message:'Rendering may still be running on the laptop.'});
 assert.match(html,/Rendering may still be running/); assert.match(html,/Forget recovery/); assert.doesNotMatch(html,/Download MP4/);
});

test('social upload actions are offered only for completed file-verified server clips', () => {
 const mixed={...batch,clips:[batch.clips[0],{...batch.clips[1],download_available:false},{...batch.clips[2],status:'rendering'}]};
 const html=renderToStaticMarkup(React.createElement(module.BatchRenderRecoveryPanel,{
  batch:mixed,connection:{mode:'idle',message:''},onRefresh(){},onRestore(){},onDismiss(){},onUpload(){},
 }));
 assert.equal((html.match(/>Upload ke TikTok \/ YouTube</g)||[]).length,1);
});

test('browser exports offer blob downloads without claiming server files or exposing social upload', () => {
 const html=render({...batch,batch_id:'client_123',zip_available:false,clips:[
  {...batch.clips[0],download_available:undefined,download_url:'blob:http://localhost/fixture'},
  {...batch.clips[1],download_available:undefined,download_url:'javascript:alert(1)'},
 ]});
 assert.equal((html.match(/>Download MP4</g)||[]).length,1);
 assert.match(html,/before closing or refreshing/);
 assert.doesNotMatch(html,/Output no longer available|javascript:|Refresh status/);
});
