const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const context={window:{},setTimeout,clearTimeout,console};vm.createContext(context);
vm.runInContext(fs.readFileSync('management_portal/static/paper_reviews.js','utf8'),context);
const mod=context.window.PortalPaperReviews;
async function run(){
 let resolve, posts=0,gets=0, states=[];
 const state={selectedId:'p',mode:'reader'};
 const deps=Object.fromEntries(mod.dependencies.map(k=>[k,()=>{}]));
 Object.assign(deps,{state,updateDeepReadControls:()=>states.push(state.deepReadStatus?.status),paperApi:{request:async(url)=>{
  if(url.endsWith('/start')){posts++;return new Promise((r,j)=>resolve={r,j});}
  gets++;return {paper_id:'p',job_id:'one',status:'running',step:'extracting pdf text'};
 }}});
 const api=mod.createReviews(deps);
 const first=api.startDeepRead();assert.equal(state.deepReadStatus.status,'queued');
 await api.startDeepRead();assert.equal(posts,1);
 resolve.j(new Error('HTTP 504'));await first;
 assert.equal(gets,1);assert.equal(state.deepReadStatus.job_id,'one');assert.equal(state.deepReadStatus.status,'running');
 await api.startDeepRead();assert.equal(posts,1);
 state.deepReadStatus={paper_id:'p',status:'error',error:'failed'};
 const retry=api.startDeepRead();resolve.r({paper_id:'p',job_id:'two',status:'queued'});await retry;
 assert.equal(posts,2);assert.equal(state.deepReadStatus.job_id,'two');
 console.log('PASS immediate admission, duplicate suppression, 504 reconciliation, retry');
}run().catch(e=>{console.error(e);process.exitCode=1});
