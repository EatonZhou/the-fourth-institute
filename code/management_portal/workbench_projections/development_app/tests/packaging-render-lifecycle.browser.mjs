import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {build} from 'esbuild';
const require=createRequire(import.meta.url),{chromium}=require('../../../test_framework/tau_runtime/node_modules/playwright');
const code=await build({stdin:{contents:"export {createSelectionEditor} from './src/packaging/selection-editor.js';export {featureStateEditor} from './src/packaging/feature-states.js'",resolveDir:process.cwd()},bundle:true,format:'iife',globalName:'UI',write:false});
const browser=await chromium.launch({headless:true});
try{
 const page=await browser.newPage();await page.setContent('<main></main>');await page.addScriptTag({content:code.outputFiles[0].text});
 await page.evaluate(async()=>{
  window.readCalls=[];
  const sdk={hatg:async(op,args)=>{readCalls.push(op);return op==='ListResourceTypes'?{types:[{type:'idea',title:'Idea'}]}:{items:args?.resource_type==='workbench'?Array.from({length:8},(_,i)=>({resource_id:`workbench_${String(i).padStart(12,'0')}`,title:'候选 '+i})):[]};}};
  window.editor=UI.createSelectionEditor(document.querySelector('main'),{sdk,signal:new AbortController().signal,value:{selection_version:3,resource_types:[{type:'idea',instances:[]}]},onChange:()=>{}});await editor.ready();
 });
 assert.equal(await page.evaluate(()=>readCalls.filter(op=>op==='ResolveResource'||op==='GetProject').length),0,'Unselected Workbench discovery must not hydrate bindings');
 const cdp=await page.context().newCDPSession(page);
 const counters=async()=>{await cdp.send('HeapProfiler.collectGarbage');return cdp.send('Memory.getDOMCounters');};
 const before=await counters();
 await page.evaluate(()=>{for(let n=0;n<80;n++)editor.setDocuments({});});
 const after=await counters();
 assert(after.jsEventListeners-before.jsEventListeners<30,`Detached editor listeners retained: ${before.jsEventListeners} -> ${after.jsEventListeners}`);
 assert(after.nodes-before.nodes<100,`Detached editor DOM retained: ${before.nodes} -> ${after.nodes}`);
 const requests=await page.evaluate(async()=>{
  let snapshot={marker:0,feature_states:{}},active=0,maxActive=0,calls=0,finalMarker;
  const root=document.createElement('section');document.body.append(root);
  const f=UI.featureStateEditor(root,{signal:new AbortController().signal,value:snapshot,getSnapshot:()=>snapshot,onChange:()=>{},sdk:{release:async(_,args)=>{
   active++;maxActive=Math.max(maxActive,active);calls++;
   await new Promise(resolve=>setTimeout(resolve,120));active--;finalMarker=args.arguments.snapshot.marker;
   return {features:[],resolution:{states:{}}};
  }}});
  void f.ready();await new Promise(resolve=>setTimeout(resolve,20));
  for(let marker=1;marker<=40;marker++){snapshot={...snapshot,marker};void f.refresh();}
  await f.settle();return {calls,maxActive,finalMarker};
 });
 assert.equal(requests.maxActive,1);assert.equal(requests.finalMarker,40);assert.equal(requests.calls,2);
 console.log(JSON.stringify({ok:true,before,after,redraws:80,requests}));
}finally{await browser.close();}
