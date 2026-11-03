const assert = require('node:assert/strict'), vm = require('node:vm');
const {JSDOM} = require('jsdom');
const {homeStatements} = require('./home_modules.cjs');
const dom = new JSDOM('<section id="panel"></section>'), document = dom.window.document;
const tag = (parts,...args) => parts.reduce((s,p,i) => s+p+(args[i]??''),'');
const route = (id, limit) => ({account_id:id,max_concurrency:limit,dispatch_interval_min_seconds:2,dispatch_interval_max_seconds:6});
const account = {account_id:'api',details_scope:'model_api',execution_accounts:[route('direct',4),route('harness',8)]};
const data = {accounts:[account,route('subscription',3)]}, calls=[];
let canWrite=true, reject=false, release;
const context=vm.createContext({document,URL,console,
  window:{PortalI18n:{t:x=>x,html:tag,text:tag,markup:x=>x},PortalUI:{errorMessage:(_,e)=>e.message},
    PortalInstructionClient:{execute:async(name,args,options)=>{
      calls.push({name,args,options});
      if(name==='ListProviderAccounts') return {accounts:[{...route(args.account_id,4),dispatch_interval_min_seconds:9,dispatch_interval_max_seconds:12}]};
      if(reject) throw new Error('permission denied');
      await new Promise(resolve=>{release=resolve;});
      return {settings:{...args}};
    }}},PortalUIPrimitives:{randomUUID:()=>String(calls.length)},
  esc:x=>String(x??'').replaceAll('&','&amp;').replaceAll('"','&quot;').replaceAll('<','&lt;'),formatDateTime:()=>'',
  currentAgentStatusData:()=>data,currentCanSetScheduler:()=>canWrite,
  accountConcurrencyEdits:new Map(),accountIntakePending:new Map(),syncAccountDetailsButtons:()=>{},
  accountDetails:{accountId:'api',panel:document.querySelector('#panel'),snapshot:{total:1},popover:{isOpen:()=>true,reposition:()=>{},close:()=>{}}}});
for(const name of ['executionAccounts','accountQuotaWindowIsStale','accountQuotaWindows','accountSummaryQuotaWindows','accountQuotaWarning','accountCreditsHtml','accountConcurrencyRows','accountConcurrencyHtml','saveAccountConcurrency','renderAccountDetails','refreshAccountQuotaWindows']) {
  vm.runInContext(homeStatements().find(({node:n})=>n.type==='FunctionDeclaration'&&n.id.name===name).source,context);
}
const form = id=>[...document.querySelectorAll('form')].find(el=>el.dataset.accountConcurrency===id);
const input=(id,value)=>{const el=form(id).elements.max_concurrency;el.value=value;el.focus();el.dispatchEvent(new dom.window.Event('input',{bubbles:true}));};
(async()=>{try {
  context.renderAccountDetails();
  assert.equal(form('direct').elements.max_concurrency.value,'4');
  assert.equal(form('harness').elements.max_concurrency.value,'8');
  input('harness','11');
  context.refreshAccountQuotaWindows();
  assert.equal(form('harness').elements.max_concurrency.value,'11');
  assert.equal(document.activeElement,form('harness').elements.max_concurrency,'polling preserves draft and focus');
  for(const invalid of ['','0','1.5','257']) {input('harness',invalid);await context.saveAccountConcurrency(form('harness'));}
  assert.equal(calls.length,0,'invalid limits never send a request');
  input('harness','11');
  const saving=context.saveAccountConcurrency(form('harness'));
  await Promise.resolve();await Promise.resolve();
  assert(form('harness').elements.max_concurrency.disabled);
  await context.saveAccountConcurrency(form('harness'));
  assert.equal(calls.length,2,'a pending save cannot be duplicated');
  assert.deepEqual(JSON.parse(JSON.stringify(calls[1].args)),{account_id:'harness',max_concurrency:11,dispatch_interval_min_seconds:9,dispatch_interval_max_seconds:12});
  assert(!('close_threshold_percent' in calls[1].args));
  release();await saving;
  assert.equal(account.execution_accounts[1].max_concurrency,11);
  assert.equal(account.execution_accounts[0].max_concurrency,4,'other API routes are untouched');
  assert.match(form('harness').textContent,/已保存/);
  reject=true;input('direct','10');await context.saveAccountConcurrency(form('direct'));
  assert.equal(form('direct').elements.max_concurrency.value,'10');
  assert.match(form('direct').querySelector('[role=alert]').textContent,/permission denied/);
  assert.equal(account.execution_accounts[0].max_concurrency,4);
  canWrite=false;context.renderAccountDetails();
  assert.equal(document.querySelectorAll('form button').length,0);
  const count=calls.length;await context.saveAccountConcurrency(form('direct'));assert.equal(calls.length,count);
  canWrite=true;context.accountDetails.accountId='subscription';context.renderAccountDetails();
  assert.equal(document.querySelectorAll('form').length,1);
  assert.equal(form('subscription').elements.max_concurrency.value,'3');
  console.log('Concurrency: route isolation, fresh intervals, validation, draft/focus, pending, failure and permissions pass');
} finally {dom.window.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
