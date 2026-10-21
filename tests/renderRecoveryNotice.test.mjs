import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import ts from 'typescript';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';

const source=readFileSync(new URL('../src/components/RenderRecoveryNotice.tsx',import.meta.url),'utf8');
let code=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.ReactJSX}}).outputText;
for(const name of ['react/jsx-runtime','react']) code=code.replaceAll('from "'+name+'"','from '+JSON.stringify(import.meta.resolve(name)));
const {RenderRecoveryNotice}=await import('data:text/javascript;base64,'+Buffer.from(code).toString('base64'));

test('missing render shows a nearby recovery action instead of a permanently disabled launch label',()=>{
 const html=renderToStaticMarkup(React.createElement(RenderRecoveryNotice,{connection:{mode:'missing',message:''},launching:false,onCheck(){},onForget(){}}));
 assert.match(html,/Render sebelumnya tidak ditemukan/);
 assert.match(html,/Cek status render/);
 assert.match(html,/Lepaskan status render lama/);
 assert.doesNotMatch(html,/disabled|Launching Render Queue/);
});

test('recovery cannot be discarded while the original POST is still pending',()=>{
 const html=renderToStaticMarkup(React.createElement(RenderRecoveryNotice,{connection:{mode:'recovering',message:''},launching:true,onCheck(){},onForget(){}}));
 assert.match(html,/<button[^>]*disabled=""[^>]*>Lepaskan status render lama/);
});
