import test from 'node:test';
import assert from 'node:assert/strict';
import {platformSelection} from '../src/packaging/platforms.js';
import {normalizeChecklist,requestFor} from '../src/packaging/model.js';
import {newRunRequest} from '../src/packaging/release-runs.js';

test('platform intent is explicit, deduplicated, and unknown targets survive',()=>{
 assert.deepEqual(platformSelection({target_platforms:['linux','windows','windows','future']}),{target_platforms:['linux','windows','future'],target_platforms_source:'explicit'});
 assert.deepEqual(platformSelection({}),{target_platforms:['linux'],target_platforms_source:'default'});
 assert.equal(platformSelection({version:'old'}).target_platforms_source,'legacy-linux');
 assert.deepEqual(platformSelection({target_platforms:[]}).target_platforms,[]);
});
test('draft migration never rewrites historical snapshots; new request and run retain targets',()=>{
 const prior={version:'v1',source:'Dev',destination:'/tmp/out'},submission={text:'old original',key:'old-key',message_id:'msg_old'};
 const frozen=structuredClone({prior,submission});
 const migrated=normalizeChecklist(prior,{validate:false});assert.equal(migrated.target_platforms_source,'legacy-linux');
 assert.deepEqual({prior,submission},frozen);
 const checklist=normalizeChecklist({...prior,target_platforms:['windows','macos']});
 assert.deepEqual(checklist.target_platforms,['windows','macos']);
 assert.match(requestFor('wb',checklist).text,/target_platforms/);
 const run=newRunRequest({config:{config_id:'existing',revision:2,checklist},mode:'full_test'});
 checklist.target_platforms.push('linux');assert.deepEqual(run.checklist_snapshot.target_platforms,['windows','macos']);
 assert.throws(()=>normalizeChecklist({...prior,target_platforms:[]}),/目标平台/);
});
