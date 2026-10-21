import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';
const source = ts.createSourceFile('App.tsx', readFileSync(new URL('../src/App.tsx', import.meta.url),'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
function handler(name, bindings) {
 let expression;
 const visit = node => { if (ts.isVariableDeclaration(node) && node.name.getText(source) === name) expression=node.initializer.getText(source); ts.forEachChild(node,visit); };
 visit(source); assert.ok(expression, 'actual App handler exists');
 const code = ts.transpileModule('const subject = '+expression+';', {compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText;
 return Function(...Object.keys(bindings), code+'; return subject;')(...Object.values(bindings));
}
function context(overrides={}) {
 const updates=[],alerts=[],launches=[]; const lock={current:false};
 const controller={isBlocked:()=>false,beginSubmission:()=> 'request-test-12345678',ownsRequest:()=>true,isWatching:()=>true,dismiss(){},resume(){}};
 const bindings={result:{video_id:'fixture',transcript:[]}, batchProgress:null,renderLaunchLock:lock,batchRecoveryRef:{current:controller},
  isCurrentVideoYouTube:()=>false,setIsLaunchingRender:value=>launches.push(value),setBatchProgress:value=>updates.push(value),
  navigate:()=>{},
  alert:message=>alerts.push(message),getStoredCookies:()=>'',subtitleEdits:{},subtitleClipKey:()=> 'fixture',url:'fixture',listenToBatchProgress:id=>updates.push({watch:id}),
  ...overrides};
 return {bindings,controller,updates,alerts,launches,lock};
}
const settings={hardwareAccel:'nvenc',renderEngine:'server',selectedClips:[{start_time:0,end_time:10,title:'Test clip'}]};

test('render uses the analyzed local source even when the URL field contains another video',async()=>{
 let body;
 const h=context({result:{video_id:'upload_fixture',video_url:'/api/video/upload_fixture.mp4',transcript:[]},url:'https://youtube.com/watch?v=other',
  fetch:async(_url,init)=>{body=JSON.parse(init.body);return Response.json({batch_id:'batch_1_abcdef'});}});
 await handler('handleStartBatchRender',h.bindings)(settings,true);
 assert.equal(body.video_url,'/api/video/upload_fixture.mp4');
 assert.equal(h.lock.current,false);
});

test('an unresolved recovery cannot submit a new render',async()=>{
 let submissions=0;const h=context({fetch:async()=>{submissions++;return Response.json({});}});
 h.controller.isBlocked=()=>true;
 await handler('handleStartBatchRender',h.bindings)(settings,true);
 assert.equal(submissions,0);
 assert.equal(h.lock.current,false);
});

test('request-ID initialization failure always releases the launch lock', async () => {
 const h=context(); h.controller.beginSubmission=()=>{throw new Error('Unavailable randomUUID');};
 await handler('handleStartBatchRender',h.bindings)(settings,true);
 assert.equal(h.lock.current,false); assert.equal(h.launches.at(-1),false); assert.equal(h.alerts.length,1);
});

test('retry cannot submit while recovery is unresolved even with a stale terminal React state', async () => {
 let submissions=0; const h=context({batchProgress:{batch_id:'batch_1_abcdef',overall_status:'error',can_retry:true},fetch:async()=>{submissions++;return new Response('{}');}});
 h.controller.isBlocked=()=>true;
 await handler('handleRetryBatchClip',h.bindings)();
 assert.equal(submissions,0);
});

for(const status of [200,400]) test('late POST '+status+' cannot overwrite or dismiss a newly selected recovery target', async () => {
 let resolve; const waiting=new Promise(r=>{resolve=r;}); const h=context({fetch:()=>waiting});
 let owned=true,dismissals=0; h.controller.ownsRequest=()=>owned; h.controller.dismiss=()=>dismissals++;
 const pending=handler('handleStartBatchRender',h.bindings)(settings,true);
 await Promise.resolve(); const initialUpdates=h.updates.length; owned=false;
 resolve(new Response(JSON.stringify(status===200?{batch_id:'batch_1_abcdef'}:{detail:'Rejected'}),{status}));
 await pending;
 assert.equal(h.updates.length,initialUpdates); assert.equal(dismissals,0); assert.equal(h.lock.current,false);
});
