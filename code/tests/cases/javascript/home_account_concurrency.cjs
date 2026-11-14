const assert = require('node:assert/strict'), vm = require('node:vm');
const {JSDOM} = require('jsdom');
const {homeStatements} = require('./home_modules.cjs');
const dom = new JSDOM('<section id="panel"></section>'), document = dom.window.document;
const tag = (parts,...args) => parts.reduce((s,p,i) => s+p+(args[i]??''),'');
const route = (id, limit) => ({account_id:id,max_concurrency:limit,dispatch_interval_min_seconds:2,dispatch_interval_max_seconds:6,
  close_threshold:{metric:id==='subscription'?'percent':'balance',unit:id==='subscription'?'%':'CNY',value:2,supported:true}});
const account = {account_id:'api',details_scope:'model_api',execution_accounts:[route('direct',4),route('harness',8)]};
const data = {accounts:[account,route('subscription',3)]}, calls=[];
let canWrite=true, reject=false, release, changedCurrency=false;
const context=vm.createContext({document,URL,console,AbortController,
  window:{PortalI18n:{t:x=>x,html:tag,text:tag,markup:x=>x},PortalUI:{errorMessage:(_,e)=>e.message},
    PortalInstructionClient:{execute:async(name,args,options)=>{
      calls.push({name,args,options});
      if(name==='ListProviderAccounts') {
        const current={...route(args.account_id,4),dispatch_interval_min_seconds:9,dispatch_interval_max_seconds:12};
        if(changedCurrency) current.close_threshold.unit='USD';
        return {accounts:[current]};
      }
      if(reject) throw new Error('permission denied');
      await new Promise(resolve=>{release=resolve;});
      return {settings:{...args,close_threshold:{...route(args.account_id,4).close_threshold,value:args.close_threshold_percent??args.close_threshold_amount??2}}};
    }}},PortalUIPrimitives:{randomUUID:()=>String(calls.length)},
  esc:x=>String(x??'').replaceAll('&','&amp;').replaceAll('"','&quot;').replaceAll('<','&lt;'),formatDateTime:()=>'',
  currentAgentStatusData:()=>data,currentCanSetScheduler:()=>canWrite,
  accountIntervalEdits:new Map(),accountConcurrencyEdits:new Map(),accountThresholdEdits:new Map(),accountIntakePending:new Map(),syncAccountDetailsButtons:()=>{},
  homeApi:{get:async()=>{throw new Error('stats unavailable');}},
  accountDetails:{accountId:'api',panel:document.querySelector('#panel'),snapshot:{total:1},popover:{isOpen:()=>true,reposition:()=>{},close:()=>{}}}});
for(const name of ['executionAccounts','accountQuotaWindowIsStale','accountQuotaWindows','accountSummaryQuotaWindows','accountQuotaWarning','accountSubscriptionHtml','accountCreditsHtml','accountConcurrencyRows','accountConcurrencyHtml','accountIntervalHtml','accountSettingsPending','saveAccountInterval','saveAccountConcurrency','accountThresholdHtml','loadAccountThresholds','saveAccountThreshold','loadAccountDetails','renderAccountDetails','refreshAccountQuotaWindows']) {
  vm.runInContext(homeStatements().find(({node:n})=>n.type==='FunctionDeclaration'&&n.id.name===name).source,context);
}
const form = id=>[...document.querySelectorAll('form')].find(el=>el.dataset.accountConcurrency===id);
const input=(id,value)=>{const el=form(id).elements.max_concurrency;el.value=value;el.focus();el.dispatchEvent(new dom.window.Event('input',{bubbles:true}));};
const threshold=id=>[...document.querySelectorAll('[data-account-threshold]')].find(el=>el.dataset.accountThreshold===id);
const thresholdInput=(id,value)=>{const el=threshold(id).elements.close_threshold;el.value=value;el.focus();el.dispatchEvent(new dom.window.Event('input',{bubbles:true}));};
const flush=()=>new Promise(resolve=>setImmediate(resolve));
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
  await flush();
  assert(form('harness').elements.max_concurrency.disabled);
  assert(threshold('harness').elements.close_threshold.disabled,'same-route settings cannot race a concurrency save');
  await context.saveAccountThreshold(threshold('harness'));
  await context.saveAccountConcurrency(form('harness'));
  assert.equal(calls.length,2,'a pending save cannot be duplicated');
  assert.deepEqual(JSON.parse(JSON.stringify(calls[1].args)),{account_id:'harness',max_concurrency:11,dispatch_interval_min_seconds:9,dispatch_interval_max_seconds:12});
  assert(!('close_threshold_percent' in calls[1].args));
  release();await saving;
  assert.equal(account.execution_accounts[1].max_concurrency,11);
  assert.equal(account.execution_accounts[0].max_concurrency,4,'other API routes are untouched');
  assert.match(form('harness').textContent,/已保存/);
  const interval=id=>[...document.querySelectorAll('[data-account-interval]')].find(el=>el.dataset.accountInterval===id);
  const setInterval=(min,max)=>{interval('harness').elements.minimum.value=min;interval('harness').elements.maximum.value=max;};
  let prior=calls.length;
  setInterval('4','2');await context.saveAccountInterval(interval('harness'));
  assert.equal(calls.length,prior);assert.match(interval('harness').textContent,/最小派发间隔不能大于/);
  setInterval('0','0');const savingInterval=context.saveAccountInterval(interval('harness'));await flush();
  assert(form('harness').elements.max_concurrency.disabled);
  await context.saveAccountInterval(interval('harness'));assert.equal(calls.length,prior+2);
  assert.deepEqual(JSON.parse(JSON.stringify(calls.at(-1).args)),{account_id:'harness',max_concurrency:4,dispatch_interval_min_seconds:0,dispatch_interval_max_seconds:0});
  release();await savingInterval;
  assert.equal(account.execution_accounts[1].close_threshold.value,2);
  assert.equal(interval('harness').elements.minimum.value,'0');
  reject=true;setInterval('0.25','1.5');await context.saveAccountInterval(interval('harness'));
  assert.match(interval('harness').textContent,/permission denied/);
  assert.equal(interval('harness').elements.minimum.value,'0.25');
  reject=true;input('direct','10');await context.saveAccountConcurrency(form('direct'));
  assert.equal(form('direct').elements.max_concurrency.value,'10');
  assert.match(form('direct').querySelector('[role=alert]').textContent,/permission denied/);
  assert.equal(account.execution_accounts[0].max_concurrency,4);
  canWrite=false;context.renderAccountDetails();
  assert.equal(document.querySelectorAll('form button').length,0);
  const count=calls.length;await context.saveAccountConcurrency(form('direct'));assert.equal(calls.length,count);
  canWrite=true;context.accountDetails.accountId='subscription';context.renderAccountDetails();
  assert.equal(document.querySelectorAll('[data-account-concurrency]').length,1);
  assert.equal(form('subscription').elements.max_concurrency.value,'3');
  reject=false;
  assert.equal(threshold('subscription').elements.close_threshold.value,'2');
  assert.equal(threshold('subscription').elements.close_threshold.max,'100');
  let countBefore=calls.length;
  for(const invalid of ['','-1','101']) {thresholdInput('subscription',invalid);await context.saveAccountThreshold(threshold('subscription'));}
  assert.equal(calls.length,countBefore,'invalid percentage thresholds cannot be saved');
  thresholdInput('subscription','0');
  context.refreshAccountQuotaWindows();
  assert.equal(threshold('subscription').elements.close_threshold.value,'0');
  assert.equal(document.activeElement,threshold('subscription').elements.close_threshold,'polling preserves threshold draft/focus');
  let pending=context.saveAccountThreshold(threshold('subscription'));
  await flush();
  assert(threshold('subscription').elements.close_threshold.disabled);
  assert(form('subscription').elements.max_concurrency.disabled,'same-route settings cannot race a threshold save');
  await context.saveAccountConcurrency(form('subscription'));
  await context.saveAccountThreshold(threshold('subscription'));
  assert.equal(calls.length,countBefore+2,'pending threshold save is deduplicated');
  assert.deepEqual(JSON.parse(JSON.stringify(calls.at(-1).args)),{account_id:'subscription',max_concurrency:4,dispatch_interval_min_seconds:9,dispatch_interval_max_seconds:12,close_threshold_percent:0});
  release();await pending;
  assert.equal(threshold('subscription').elements.close_threshold.value,'0','zero disables protection without defaulting back to two');
  assert.match(threshold('subscription').textContent,/已保存/);
  context.accountDetails.accountId='api';context.renderAccountDetails();
  assert.equal(threshold('harness').elements.close_threshold.max,'');
  thresholdInput('harness','120.25');
  pending=context.saveAccountThreshold(threshold('harness'));
  await flush();
  assert.equal(calls.at(-1).args.close_threshold_amount,120.25);
  assert(!('close_threshold_percent' in calls.at(-1).args));
  release();await pending;
  assert.equal(account.execution_accounts[1].close_threshold.value,120.25);
  assert.equal(account.execution_accounts[0].close_threshold.value,2,'saving one route never changes sibling thresholds');
  reject=true;thresholdInput('direct','5');await context.saveAccountThreshold(threshold('direct'));
  assert.equal(threshold('direct').elements.close_threshold.value,'5');
  assert.match(threshold('direct').querySelector('[role=alert]').textContent,/permission denied/);
  reject=false;changedCurrency=true;countBefore=calls.length;
  await context.saveAccountThreshold(threshold('direct'));
  assert.equal(calls.length,countBefore+1,'currency changes block update after fresh route read');
  assert.match(threshold('direct').textContent,/单位不可用或已变化/);
  changedCurrency=false;
  canWrite=false;context.renderAccountDetails();
  assert.equal(threshold('direct').querySelector('button'),null);
  countBefore=calls.length;await context.saveAccountThreshold(threshold('direct'));assert.equal(calls.length,countBefore);
  canWrite=true;
  account.execution_accounts[0].close_threshold.supported=false;context.renderAccountDetails();
  assert(threshold('direct').elements.close_threshold.disabled);
  assert.match(threshold('direct').textContent,/暂不可用/);
  context.accountDetails.requestVersion=0;
  await context.loadAccountDetails(context.accountDetails);
  await flush();
  assert.match(context.accountDetails.error,/stats unavailable/);
  assert.equal(context.accountDetails.thresholds.size,2,'API details fetch each execution route');
  assert.equal(threshold('harness').elements.close_threshold.value,'2','refresh reads the persisted threshold');
  assert(!threshold('harness').elements.close_threshold.disabled,'node statistics failure does not block account settings');
  const originalExecute=context.window.PortalInstructionClient.execute;
  const delayed=[];
  context.window.PortalInstructionClient.execute=(_,args)=>new Promise(resolve=>delayed.push(()=>resolve({accounts:[route(args.account_id,1)]})));
  const oldLoad=context.loadAccountThresholds(context.accountDetails,context.accountDetails.requestVersion);
  context.accountDetails.requestVersion++;
  context.accountDetails.thresholds=new Map();
  delayed.forEach(resolve=>resolve());await oldLoad;
  assert.equal(context.accountDetails.thresholds.size,0,'late reads from a closed/replaced popover cannot restore stale settings');
  context.window.PortalInstructionClient.execute=originalExecute;
  console.log('Account settings: isolated routes, percentage/balance/zero thresholds, fresh settings, validation, currency changes, drafts, permissions, failures and stale reads pass');
} finally {dom.window.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
