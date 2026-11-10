import assert from 'node:assert/strict';
import {createPersistence} from '../../../management_portal/paper_reader_app/zotero_prototype/persistence.js';
const endpoint=process.argv[2];let op=0,drop=false;
const scope={actorKey:'server-bound-a',paper_id:'paper_fixture',attachment_id:'attachment_fixture',attachment_version:'v1'};
async function call(method,body){const r=await fetch(endpoint+'/'+method,{method:'POST',body:JSON.stringify(body)});const result=await r.json();if(!r.ok)throw Object.assign(Error(result.error),{status:r.status});if(method==='commit'&&drop){drop=false;throw Error('lost response');}return result;}
const transport={actorKey:scope.actorKey,read:d=>call('read',d),commit:b=>call('commit',b)};
const session=()=>createPersistence({scope,transport,operationId:()=>`bridge-${++op}`});
const a=session();await a.load();const full={id:'A',type:'highlight',position:{pageIndex:0,rects:[[1,2,30,12]],nextPageRects:[[2,3,20,13]]},image:'retained-image',tags:[{name:'keep'}],text:'original',comment:'first'};
await a.save([full]);const b=session();await b.load();
await a.save([{id:'A',onlyTextOrComment:true,comment:'changed',position:{pageIndex:9,rects:[[9,9,10,10]]},tags:[]}]);
let view=await call('read',Object.fromEntries(Object.entries(scope).filter(([k])=>k!=='actorKey')));assert.deepEqual(view.annotations[0].position,full.position);assert.deepEqual(view.annotations[0].tags,full.tags);assert.equal(view.annotations[0].image,full.image);
drop=true;await assert.rejects(a.save([{id:'A',onlyTextOrComment:true,comment:'committed but lost'}]),/lost/);
await a.save([{id:'A',onlyTextOrComment:true,text:'new text',comment:'newer edit'}]);
await assert.rejects(b.save([{...full,comment:'stale'}]),e=>e.status===409);assert(b.snapshot().pending);
// Partial updates queued behind a full update use that confirmed geometry.
await Promise.all([a.save([{...full,position:{pageIndex:1,rects:[[2,3,9,11]]}}]),a.save([{id:'A',onlyTextOrComment:true,comment:'queued comment'}])]);
view=await call('read',Object.fromEntries(Object.entries(scope).filter(([k])=>k!=='actorKey')));assert.equal(view.annotations[0].position.pageIndex,1);assert.equal(view.annotations[0].comment,'queued comment');assert.equal(view.annotations[0].image,full.image);
await a.remove(['A']);await assert.rejects(a.save([{id:'A',onlyTextOrComment:true,comment:'no baseline'}]),/confirmed full record/);
view=await call('read',Object.fromEntries(Object.entries(scope).filter(([k])=>k!=='actorKey')));assert.equal(view.annotations.length,0);assert.equal(view.tombstones[0].id,'A');
await assert.rejects(call('commit',{owner:'browser-owner'}));
console.log('PASS real PG bridge: materialization, queued geometry, delete, lost response/new edit replay, CAS, server-bound authentication');
