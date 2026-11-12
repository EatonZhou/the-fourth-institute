'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const root = path.resolve(__dirname, '../../../management_portal/static');
const drain = () => new Promise(resolve => setImmediate(resolve));
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

async function panel() {
  const dom = new JSDOM('<main></main>', {runScripts:'outside-only', pretendToBeVisual:true});
  const w = dom.window;
  w.CSS = {escape: x => x};
  let hidden = false;
  Object.defineProperty(w.document, 'hidden', {get: () => hidden});
  const tag = (strings,...values) => strings.reduce((s, v, i) => s + v + (values[i] ?? ''), '');
  w.PortalI18n = {t:x=>x,html:tag,text:tag,locale:()=> 'en'};
  w.PortalUIPrimitives = {escapeHtml:x=>String(x ?? '')};
  w.PortalUI = {createDialogController: options => ({open(){},close(){options.onClose();},destroy(){}})};
  let interest=0, reads=0, chat=0, active=0, peak=0;
  const calls=[];
  const groups=[{message_id:'g',kind:'group',members:Array.from({length:8},(_,i)=>({message_id:'m'+i}))},
    {message_id:'h',kind:'group',members:[{message_id:'m0'},{message_id:'new'}]},
    {message_id:'cancel',kind:'group',members:Array.from({length:6},(_,i)=>({message_id:'cancel'+i}))}];
  w.PortalEvents={watchResident(){interest++;return ()=>interest--;}};
  w.PortalResidentProtocol={
    acquire(){chat++;throw Error('Mailbox must not acquire Chat');},
    async loadMailbox(){reads++;return {pending:groups,recent:[],can_manage:false};},
    loadMessage(rid,id,{signal}){
      active++;peak=Math.max(peak,active);
      return new Promise((resolve,reject)=>{
        const end = () => {active--;signal.removeEventListener('abort',abort);};
        const abort=()=>{end();reject(new w.DOMException('closed','AbortError'));};
        signal.addEventListener('abort',abort,{once:true});
        calls.push({id,resolve(){end();resolve({message_id:id,content_parts:[{type:'text',text:id}]});},signal});
      });
    }
  };
  w.eval(fs.readFileSync(path.join(root,'hatg_mailbox_panel.js'),'utf8'));
  const api=w.PortalMailboxPanel.mount({container:w.document.querySelector('main'),residentId:'resident_test'});
  await drain();
  const change=type=>w.dispatchEvent(new w.CustomEvent('portal:change',{detail:{resident_events:[{resident_id:'resident_test',event_type:type}]}}));
  for(let i=0;i<12;i++)change('resident.activity.changed');
  await sleep(280);assert.equal(reads,1);assert.equal(chat,0);
  for(let i=0;i<12;i++)change('resident.queue.changed');
  await sleep(280);assert.equal(reads,2);
  hidden=true;change('resident.queue.changed');await sleep(280);assert.equal(reads,2);
  hidden=false;w.document.dispatchEvent(new w.Event('visibilitychange'));await sleep(280);assert.equal(reads,3);
  const open=id=>w.document.querySelector(`[data-mailbox-item="${id}"] [data-mailbox-action="open"]`).click();
  open('g');await drain();assert.equal(calls.length,2);
  for(let i=0;i<8;i++){calls[i].resolve();await drain();}
  assert.equal(calls.length,8);assert.equal(peak,2);
  w.document.querySelector('[data-portal-detail-close]').click();
  open('h');await drain();assert.equal(calls.length,9);assert.equal(calls[8].id,'new');
  w.document.querySelector('[data-portal-detail-close]').click();await drain();assert.ok(calls[8].signal.aborted);
  open('g');await drain();assert.equal(calls.length,9,'resolved members reused');
  open('cancel');await drain();assert.equal(calls.length,11);
  w.document.querySelector('[data-portal-detail-close]').click();await drain();
  assert.ok(calls[9].signal.aborted && calls[10].signal.aborted);
  assert.equal(calls.length,11,'four queued requests never start after close');
  w.dispatchEvent(new w.CustomEvent('portal:events-ready'));await sleep(280);assert.equal(reads,4);
  w.dispatchEvent(new w.CustomEvent('portal:events-resubscribed',{detail:{added_resident_ids:['resident_test']}}));
  await sleep(280);assert.equal(reads,5);
  w.dispatchEvent(new w.Event('portal:identity-changed'));await drain();
  assert.equal(interest,0);assert.equal(w.document.querySelector('.mailboxPanel'),null);
  api.destroy();change('resident.queue.changed');await sleep(280);assert.equal(reads,5);
  console.log(JSON.stringify({activity_notifications:12,mailbox_reads:0,queue_notifications:12,coalesced_reads:1,hidden_reads:0,peak_details:peak,group_members:8,cached_overlap_extra_reads:1,chat_loads:chat}));
  w.close();
}
async function transportSignal() {
  const dom = new JSDOM('', {runScripts:'outside-only'});
  const w=dom.window;
  w.PortalUIPrimitives={randomUUID:()=> 'fixture'};
  let received;
  w.PortalInstructionClient={execute:async (name,args,options)=>{
    assert.equal(name,'GetMessage');received=options.signal;return {message:{message_id:args.message_id}};
  }};
  for(const file of ['portal_resident_service.js','resident_protocol.js'])
    w.eval(fs.readFileSync(path.join(root,file),'utf8'));
  const controller=new w.AbortController();
  const result=await w.PortalResidentProtocol.loadMessage('resident_test','m',{signal:controller.signal});
  assert.equal(result.message_id,'m');assert.equal(received,controller.signal);
  w.close();
}
transportSignal().then(panel).catch(error=>{console.error(error);process.exitCode=1;});
