const assert = require('node:assert/strict'), vm = require('node:vm');
const {JSDOM} = require('jsdom');
const {homeStatements} = require('./home_modules.cjs');
const dom = new JSDOM('<section id="panel"></section>'), document = dom.window.document;
const tag = (parts,...args) => parts.reduce((s,p,i) => s+p+(args[i]??''),'');
const actions = [];
const snapshot = {total:8, queued_messages:3, intake:{open:5,closed:3}, work:{running:2,idle:6},
  without_session:1, latest_input:{failed:2}, open_resident_ids:['r1'],closed_resident_ids:['r2'],
  quota:{status:'reserved',remaining:1}};
const account = {account_id:'api',capabilities:{resident_details:true,scheduler_settings:true},
  balance:{display:'500 CNY',billing_url:'https://billing.invalid'},credits:{balance:10},rate_limits:[{remaining_percent:5}]};
const context = vm.createContext({document,URL,window:{PortalI18n:{t:x=>x,html:tag,text:tag,markup:x=>x}},
  esc:x=>String(x??'').replaceAll('&','&amp;').replaceAll('"','&quot;').replaceAll('<','&lt;'), formatDateTime:()=>'', executionAccounts:d=>d.accounts,
  currentAgentStatusData:()=>({accounts:[account]}),accountIntakePending:new Map(),
  currentCanSetScheduler:()=>false,accountConcurrencyEdits:new Map(),accountThresholdEdits:new Map(),accountIntervalEdits:new Map(),
  syncAccountDetailsButtons:()=>{}, loadAgentStatus:()=>{},loadAccountDetails:()=>actions.push('refresh'),
  changeAccountResidentsIntake:(_,action)=>actions.push(action),
  accountDetails:{accountId:'api',panel:document.querySelector('#panel'),snapshot,
    popover:{isOpen:()=>true,reposition:()=>{},close:()=>{}}}});
for (const name of ['accountConcurrencyRows', 'accountConcurrencyHtml', 'accountThresholdHtml', 'accountIntervalHtml', 'accountSettingsPending', 'accountQuotaWindowIsStale', 'accountQuotaWindows', 'accountSummaryQuotaWindows', 'accountQuotaWarning', 'accountSubscriptionHtml','accountCreditsHtml', 'renderAccountDetails', 'refreshAccountQuotaWindows']) {
  vm.runInContext(homeStatements().find(({node:n})=>n.type==='FunctionDeclaration' && n.id.name===name).source,context);
}
try {
  context.renderAccountDetails();
  Object.assign(account, {kind:'subscription_login',provider:'codex',subscription:{tier:{name:'promax'}},
    refresh_cards:{available_count:3,cards:[{title:'Weekly <reset>',expiresAt:2000,status:'available'}]}});
  context.formatDateTime = value => value ? new Date(value).toISOString() : '';
  context.renderAccountDetails();
  assert.equal(document.querySelector('[data-account-tier]').textContent,'promax');
  assert.equal(document.querySelector('[data-account-refresh-cards]').textContent,'3');
  assert.match(document.querySelector('[data-account-refresh-cards]').title,/Weekly <reset>.*1970-01-01T00:33:20/);
  assert.match(document.querySelector('[data-account-refresh-cards]').title,/部分刷新卡明细/);
  assert.equal(document.querySelector('[data-account-refresh-cards] reset'),null);
  account.refresh_cards={available_count:0,cards:[]};context.renderAccountDetails();
  assert.equal(document.querySelector('[data-account-refresh-cards]').textContent,'0');
  account.refresh_cards={available_count:null,cards:null};context.renderAccountDetails();
  assert.equal(document.querySelector('[data-account-refresh-cards]').textContent,'—');
  assert.match(document.querySelector('[data-account-refresh-cards]').title,/未提供刷新卡明细/);
  assert.equal(document.querySelector('[data-account-metric="total"]').textContent,'8');
  assert.equal(document.querySelector('[data-account-metric="queued_messages"]').textContent,'3');
  assert.equal(document.querySelector('[data-account-metric="running"]').textContent,'2');
  assert.equal(document.querySelector('[data-account-balance], [data-account-quota-window], .account-details-quota, [data-account-action="backend"]'),null);
  assert(document.querySelector('[name=max_concurrency]').disabled, 'viewers cannot edit concurrency');
  assert.doesNotMatch(document.body.textContent,/500 CNY|调度设置/);
  const creditValue = () => document.querySelector('[data-account-credits] dd')?.textContent;
  assert.equal(creditValue(),'10');
  for (const [credits, expected] of [
    [{balance:44904.190574}, (44904.190574).toLocaleString(undefined,{maximumFractionDigits:2})],
    [{balance:0},'0'], [{balance:12.5,stale:true},'12.5*'],
    [{balance:null},'—'], [{balance:NaN},'—'], [{balance:Infinity},'—'],
    [{balance:'12'},'—'], [{unlimited:true},'不限'], [{unlimited:true,stale:true},'不限*'],
    [undefined,undefined],
  ]) {
    account.credits=credits; context.renderAccountDetails(); assert.equal(creditValue(),expected);
  }
  account.credits={balance:0,stale:true}; context.renderAccountDetails();
  assert.equal(creditValue(),'0*');
  assert.match(document.querySelector('[data-account-credits]').title,/数据已过期/);
  account.credits={balance:9}; context.refreshAccountQuotaWindows();
  assert.equal(creditValue(),'9', 'summary refresh updates open Credits');
  account.status='unavailable'; context.renderAccountDetails(); assert.equal(creditValue(),'9*');
  account.status='ok'; account.error='<img src=x onerror=alert(1)>'; context.renderAccountDetails();
  assert.equal(document.querySelector('[data-account-credits] img'),null);
  delete account.error;
  const billing=document.querySelector('[data-account-action="billing"]');
  assert.equal(billing.href,'https://billing.invalid/');
  assert.equal(billing.target,'_blank');
  assert.equal(billing.rel,'noopener noreferrer');
  document.querySelector('[data-account-open-residents]').click();
  document.querySelector('[data-account-close-residents]').click();
  assert.deepEqual(actions,['open','close']);
  document.querySelector('.account-details-more').open=true;
  context.renderAccountDetails();
  assert.equal(document.querySelector('.account-details-more').open,true);
  context.accountDetails.loading=true;
  context.renderAccountDetails();
  assert.equal(creditValue(),'9', 'node reads do not hide known credits');
  assert(document.querySelector('[data-account-open-residents]').disabled);
  context.accountDetails.loading=false; context.accountDetails.error='read failed';
  context.renderAccountDetails();
  assert.match(document.querySelector('[role=alert]').textContent,/read failed/);
  assert(document.querySelector('[data-account-close-residents]').disabled);
  assert.equal(creditValue(),'9', 'node read failures do not hide known credits');
  assert(document.querySelector('[data-account-action="billing"]'), 'billing remains usable when node state cannot load');
  for(const url of ['', 'javascript:alert(1)', 'data:text/html,hello', '/relative']) {
    account.balance.billing_url=url; context.renderAccountDetails();
    assert.equal(document.querySelector('[data-account-action="billing"]'),null);
  }
  console.log('Node details retain actions and safely expose known billing links, even during read failures');
} finally {dom.window.close();}
