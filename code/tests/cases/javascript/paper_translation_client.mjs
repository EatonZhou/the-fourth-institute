import assert from 'node:assert/strict';
import {translateSelection} from '../../../management_portal/paper_reader_app/translation_client.js';
const calls=[];
globalThis.fetch=async (path, options)=>{
  calls.push({path, method:options.method});
  return {ok:true, json:async()=>calls.length===1
    ? {task_id:'bound',status:'queued',cache:'miss'}
    : {task_id:'unexpected',status:'running'}};
};
await assert.rejects(translateSelection({selection:{text:'synthetic',document:{}},mode:'auto',operationId:'one',principal:'fixture',signal:new AbortController().signal}),/任务编号发生变化/);
assert.deepEqual(calls.map(c=>c.method),['POST','GET']);
assert(calls[1].path.endsWith('task_id=bound'));
