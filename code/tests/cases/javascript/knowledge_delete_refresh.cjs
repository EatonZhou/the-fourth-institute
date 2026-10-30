const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const script = name => fs.readFileSync(path.resolve(__dirname, '../../../management_portal/static', name), 'utf8');
// From `start` to `end`, or to the end of the script.
const extract = (source, start, end) => source.slice(source.indexOf(start), end ? source.indexOf(end, source.indexOf(start)) : undefined);
const deletion = extract(script('knowledge_board_detail.js'), 'async function deleteDetail()', 'async function openDetailFile(');
const loading = extract(script('knowledge_board_loading.js'), 'function applyIndexPage(');
function harness(receipt = {deleted:true, kb_id:'information_1234'}) {
  const errors = [], renders = [];
  const state = {raw:{items:[{kb_id:'information_1234'},{kb_id:'information_5678'}]},
    deletedKnowledgeIds:new Set(), detailFileId:'information_1234', indexPaging:{requestSeq:1,total:2},
    exactQuery:{seq:1}, selected:null};
  const context = vm.createContext({state, URLSearchParams, setTimeout,
    window:{scrollY:0,requestAnimationFrame:()=>{},PortalUI:{confirmAction:async()=>true},
      PortalI18n:{t:s=>s,text:(s,...a)=>s.reduce((v,t,i)=>v+t+(a[i]??''),'')}},
    el:{meta:{textContent:''}}, filter:{type:null},
    byId:id=>state.raw.items.find(x=>x.kb_id===id), syncDetailButtons:()=>{},
    detailResourceGuest:null,closeModal:()=>{state.detailFileId='';}, alertError:e=>errors.push(e),
    renderAll:()=>renders.push(state.raw.items.map(x=>x.kb_id)),
    normalizeFilterStateForIndex:()=>{},buildFilterOptions:()=>{},syncKnowledgeFilterInputs:()=>{},
    knowledgeBoardShell:{syncFilterState:()=>{}},indexRequestParams:()=>'',waitForRefreshedIndex:()=>{},
    knowledgeApi:{request:async()=>receipt,requireJson:r=>r.payload,requestResult:async()=>({response:{ok:false,status:503}})}});
  vm.runInContext(loading+deletion,context);
  return {context,state,errors,renders};
}
test('acknowledged deletion removes card before refresh finishes; stale pages cannot resurrect it',async()=>{
  const h=harness(); let finish;
  h.context.knowledgeApi.requestResult=()=>new Promise(resolve=>{finish=resolve;});
  const deleting=h.context.deleteDetail();
  await new Promise(setImmediate);
  assert.deepEqual(h.renders.at(-1),['information_5678']);
  assert.equal(h.state.detailFileId,'');
  assert.equal(h.state.exactQuery.seq,2);
  h.context.applyLoadedIndex({items:[{kb_id:'information_1234'}]},1);
  assert.deepEqual(h.renders.at(-1),['information_5678']);
  finish({response:{ok:true},payload:{items:[{kb_id:'information_1234'},{kb_id:'information_5678'}],page:{total:2}}});
  await deleting;
  assert.deepEqual(h.renders.at(-1),['information_5678']);
});
test('refresh failure preserves deletion and exposes an index error',async()=>{
  const h=harness();await h.context.deleteDetail();
  assert.deepEqual(h.renders.at(-1),['information_5678']);
  assert.equal(h.state.indexPaging.error,'HTTP 503');
  assert.equal(h.errors.length,1);
});
test('unconfirmed or mismatched delete receipt cannot remove a card',async()=>{
  for(const receipt of [{deleted:false,kb_id:'information_1234'},{deleted:true,kb_id:'information_9999'},{}]){
    const h=harness(receipt);await h.context.deleteDetail();
    assert.equal(h.state.raw.items.length,2);assert.equal(h.state.deletedKnowledgeIds.size,0);
    assert.equal(h.state.detailFileId,'information_1234');assert.equal(h.errors.length,1);
  }
});
test('Skill deletion uses its formal skill_id receipt',async()=>{
  const h=harness({deleted:true,skill_id:'information_1234'});await h.context.deleteDetail();
  assert.deepEqual(h.renders.at(-1),['information_5678']);
});
test('failure from obsolete index request cannot replace newer successful state',async()=>{
  const h=harness();let reject;
  h.context.knowledgeApi.requestResult=()=>new Promise((_,r)=>{reject=r;});
  const pending=h.context.loadIndex();h.context.beginIndexLoad();
  h.context.applyLoadedIndex({items:[{kb_id:'information_5678'}]},h.state.indexPaging.requestSeq);
  reject(new Error('stale failure'));await pending;
  assert.equal(h.state.indexPaging.error,'');assert.deepEqual(h.renders.at(-1),['information_5678']);
});
test('first index failure also leaves loading state with visible error',async()=>{
  const h=harness();h.state.raw=null;
  await assert.rejects(h.context.loadIndex(),/503/);
  assert.match(h.context.el.meta.textContent,/503/);
});
