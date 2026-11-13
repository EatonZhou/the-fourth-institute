'use strict';
// Global attempt-history controls were retired in f84aa87a; the authorized
// ListMessages(attempts) transport remains available to domain consumers.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const context = vm.createContext({window:new EventTarget(), URLSearchParams});
vm.runInContext(fs.readFileSync(path.resolve(__dirname,
  '../../../management_portal/static/portal_resident_service.js'), 'utf8'), context);
async function main() {
  const calls = [];
  let result = {attempts:[{input_message_id:'input_one'}], next_cursor:'older'};
  let error;
  const client = context.window.PortalResidentService.createClient({execute:async (name,args) => {
    calls.push({name,args});
    if (error) throw error;
    return result;
  }});
  const first = await client.businessHistory('resident_111111111111');
  assert.deepEqual(JSON.parse(JSON.stringify(calls[0])), {name:'ListMessages',args:{
    resident_id:'resident_111111111111',view:'attempts',cursor:'',limit:50}});
  assert.equal(first.next_page_token,'older');
  assert.equal(first.attempts[0].input_message_id,'input_one');
  result = {attempts:[], next_cursor:''};
  const older = await client.businessHistory('resident_111111111111',{pageToken:first.next_page_token,limit:10});
  assert.equal(calls[1].args.cursor,'older');
  assert.equal(calls[1].args.limit,10);
  assert.equal(older.next_page_token,'');
  result = {conversation:{messages:[{role:'agent',content_parts:[{type:'text',text:'saved reply'}]}]}};
  const detail = await client.businessHistory('resident_111111111111',{inputMessageId:'input_one'});
  assert.equal(calls[2].args.input_message_id,'input_one');
  assert.equal(detail.conversation.messages[0].content_parts[0].text,'saved reply');
  error = Object.assign(new Error('Authorization owner unavailable'),{status:403});
  await assert.rejects(client.businessHistory('resident_111111111111'),value=>value===error);
  assert.throws(()=>client.businessHistory(''),/resident_id required/);
  assert.ok(calls.every(call=>!('prompt' in call.args)));
  console.log('Resident attempt history: transport, paging, detail and errors passed');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
