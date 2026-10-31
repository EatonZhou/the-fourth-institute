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
  esc:x=>String(x??''), formatDateTime:()=>'', executionAccounts:d=>d.accounts,
  currentAgentStatusData:()=>({accounts:[account]}),accountIntakePending:new Map(),
  syncAccountDetailsButtons:()=>{}, loadAgentStatus:()=>{},loadAccountDetails:()=>actions.push('refresh'),
  changeAccountResidentsIntake:(_,action)=>actions.push(action),
  accountDetails:{accountId:'api',panel:document.querySelector('#panel'),snapshot,
    popover:{isOpen:()=>true,reposition:()=>{},close:()=>{}}}});
vm.runInContext(homeStatements().find(({node:n})=>n.type==='FunctionDeclaration' && n.id.name==='renderAccountDetails').source,context);
try {
  context.renderAccountDetails();
  assert.equal(document.querySelector('[data-account-metric="total"]').textContent,'8');
  assert.equal(document.querySelector('[data-account-metric="queued_messages"]').textContent,'3');
  assert.equal(document.querySelector('[data-account-metric="running"]').textContent,'2');
  assert.equal(document.querySelector('form, [data-account-balance], [data-account-credits], [data-account-quota-window], .account-details-quota, [data-account-action="backend"]'),null);
  assert.doesNotMatch(document.body.textContent,/500 CNY|调度设置|Credits/);
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
  assert(document.querySelector('[data-account-open-residents]').disabled);
  context.accountDetails.loading=false; context.accountDetails.error='read failed';
  context.renderAccountDetails();
  assert.match(document.querySelector('[role=alert]').textContent,/read failed/);
  assert(document.querySelector('[data-account-close-residents]').disabled);
  assert(document.querySelector('[data-account-action="billing"]'), 'billing remains usable when node state cannot load');
  for(const url of ['', 'javascript:alert(1)', 'data:text/html,hello', '/relative']) {
    account.balance.billing_url=url; context.renderAccountDetails();
    assert.equal(document.querySelector('[data-account-action="billing"]'),null);
  }
  console.log('Node details retain actions and safely expose known billing links, even during read failures');
} finally {dom.window.close();}
