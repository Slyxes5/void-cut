import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import ts from 'typescript';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';

const serviceSource = readFileSync(new URL('../src/services/socialUploads.ts',import.meta.url),'utf8');
const compile = source => ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.ReactJSX}}).outputText;
const dataUrl = source => 'data:text/javascript;base64,'+Buffer.from(source).toString('base64');
const serviceUrl = dataUrl(compile(serviceSource));
const service = await import(serviceUrl);
const componentSource = readFileSync(new URL('../src/components/SocialUploadModal.tsx',import.meta.url),'utf8');
let componentCode = compile(componentSource).replace(/import ['"]\.\/SocialUploadModal\.css['"];?/,'')
  .replace(/from ['"]\.\.\/services\/socialUploads['"]/g,'from '+JSON.stringify(serviceUrl));
for(const name of ['react/jsx-runtime','react']) componentCode=componentCode.replaceAll('from "'+name+'"','from '+JSON.stringify(import.meta.resolve(name))).replaceAll("from '"+name+"'",'from '+JSON.stringify(import.meta.resolve(name)));
const component = await import(dataUrl(componentCode));

test('upload receipts only link to actual HTTPS provider video pages',()=>{
  for(const url of ['javascript:alert(1)','https://evil.example/video/1','https://www.youtube.com.evil.example/shorts/dQw4w9WgXcQ',
    'https://user@www.youtube.com/shorts/dQw4w9WgXcQ','http://www.youtube.com/shorts/dQw4w9WgXcQ']) assert.equal(service.uploadReceiptUrl(url),undefined);
  assert.equal(service.uploadReceiptUrl('https://www.youtube.com/shorts/dQw4w9WgXcQ'),'https://www.youtube.com/shorts/dQw4w9WgXcQ');
  assert.equal(service.uploadReceiptUrl('https://www.tiktok.com/@creator/video/12345'),'https://www.tiktok.com/@creator/video/12345');
});

test('social API uses authenticated same-origin JSON, reports failure, and never retries a POST',async()=>{
  const previous=globalThis.fetch;
  try {
    const calls=[];
    globalThis.fetch=async(url,init)=>{calls.push([url,init]);return Response.json({detail:'Scope not granted'},{status:403});};
    await assert.rejects(service.socialApi('uploads',{method:'POST',body:'{}'}),/Scope not granted/);
    assert.equal(calls.length,1);
    assert.equal(calls[0][0],'/api/social/uploads');
    assert.equal(calls[0][1].credentials,'same-origin');
    assert.equal(calls[0][1].headers['Content-Type'],'application/json');
    globalThis.fetch=async()=>new Response('upstream disconnected',{status:502});
    await assert.rejects(service.socialApi('uploads'),/Periksa status/);
  } finally {globalThis.fetch=previous;}
});

test('opening the upload form previews the rendered file but does not submit anything',()=>{
  const previous=globalThis.window;
  globalThis.window={location:{origin:'https://clip.example.com'}};
  try {
    const html=renderToStaticMarkup(React.createElement(component.SocialUploadModal,{batchId:'batch_1_abcdef',
      clip:{clip_index:0,title:'Rendered title',status:'completed',download_url:'/api/download-rendered/clip_1_batch_1_abcdef.mp4'},onClose(){}}));
    assert.match(html,/Upload ke platform/);
    assert.match(html,/Rendered title/);
    assert.match(html,/<video[^>]+src="\/api\/download-rendered\//);
    assert.match(html,/Salin judul \+ caption/);
    assert.match(html,/docs\/social-upload-setup.md/);
    assert.doesNotMatch(html,/<input[^>]+checked/);
    assert.match(html,/<button[^>]+disabled=""[^>]*>Mulai upload ke platform terpilih<\/button>/);
  } finally {globalThis.window=previous;}
});

test('account login is available before any video has rendered',()=>{
  const previous=globalThis.window;
  globalThis.window={location:{origin:'http://127.0.0.1:8000'}};
  try {
    const html=renderToStaticMarkup(React.createElement(component.SocialUploadModal,{onClose(){}}));
    assert.match(html,/Login YouTube \/ TikTok/);
    assert.match(html,/Hubungkan akun sekarang/);
    assert.match(html,/Panduan setup pertama kali/);
    assert.doesNotMatch(html,/<video|Mulai upload ke platform|Memeriksa riwayat/);
  } finally {globalThis.window=previous;}
});

test('account-only mode never requests a render or upload history',async()=>{
  let requests=0;
  const refresh=handler('refreshJobs',{
    useCallback:fn=>fn,historyPath:null,socialApi:async()=>{requests++;throw new Error('Unexpected request');},
    mounted:{current:true},setJobs(){},setHistoryLoaded(){},setError(){},
  });
  await refresh();
  assert.equal(requests,0);
});

function handler(name,bindings){
  const source=ts.createSourceFile('SocialUploadModal.tsx',componentSource,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
  let expression;
  const visit=node=>{if(ts.isVariableDeclaration(node)&&node.name.getText(source)===name)expression=node.initializer.getText(source);ts.forEachChild(node,visit);};
  visit(source);assert.ok(expression);
  const code=compile('const subject = '+expression+';');
  return Function(...Object.keys(bindings),code+';return subject;')(...Object.values(bindings));
}
function context(){
  const calls=[],busy=[],errors=[],jobs=[];
  const bindings={submitLock:{current:false},accounts:[{platform:'youtube',account_id:'channel-1'},{platform:'tiktok',account_id:'creator-1'}],
    youtube:true,tiktok:true,youtubePrivacy:'private',kids:'no',synthetic:false,mode:'draft',tiktokPrivacy:'',
    comment:false,duet:false,stitch:false,commercial:false,ownBrand:false,branded:false,music:false,
    batchId:'batch_1_abcdef',clip:{clip_index:0},title:'My title',caption:'My caption #test',
    attempt:{current:null},crypto:{randomUUID:()=> '12345678-1234-4123-a123-123456789abc'},mounted:{current:true},
    setBusy:value=>busy.push(value),setError:value=>errors.push(value),setJobs:fn=>jobs.push(fn([])),
    setConfirmation(){},setNotice(){},refreshJobs:async()=>{},
    socialApi:async(path,init)=>{calls.push([path,init]);return {request_id:'receipt',targets:[]};}};
  return {bindings,calls,busy,errors,jobs};
}

test('submit sends the reviewed accounts, exact metadata and the same request ID after a lost response',async()=>{
  const h=context();
  h.bindings.socialApi=async(path,init)=>{
    h.calls.push([path,init]);
    if(h.calls.length===1) throw new Error('Response lost');
    return {request_id:'receipt',targets:[]};
  };
  const submit=handler('submit',h.bindings);
  await submit();await submit();
  const bodies=h.calls.map(([,init])=>JSON.parse(init.body));
  assert.equal(bodies[0].request_id,bodies[1].request_id);
  assert.equal(bodies[0].title,'My title');assert.equal(bodies[0].caption,'My caption #test');
  assert.deepEqual(bodies[0].targets.map(t=>[t.platform,t.account_id,t.mode]),[['youtube','channel-1','upload'],['tiktok','creator-1','draft']]);
  assert.equal(bodies[0].confirmed,true);
});

test('a pending POST blocks a second click and releases the lock even if the connection fails',async()=>{
  const h=context();let reject;
  h.bindings.socialApi=(path,init)=>{h.calls.push([path,init]);return new Promise((_,r)=>{reject=r;});};
  const submit=handler('submit',h.bindings);
  const first=submit();await submit();assert.equal(h.calls.length,1);
  reject(new Error('Network interrupted'));await first;
  assert.equal(h.bindings.submitLock.current,false);assert.equal(h.busy.at(-1),false);
  assert.match(h.errors.at(-1),/Periksa riwayat/);
});
