const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {JSDOM} = require('jsdom');
const {homeStatements} = require('./home_modules.cjs');

const root = path.resolve(__dirname, '../../../management_portal/static');
const dom = new JSDOM('<main></main>', {url:'http://portal.invalid/'});
const tag = (parts, ...args) => parts.reduce((s, p, i) => s + p + (args[i] ?? ''), '');
const context = vm.createContext({
  window: {location:dom.window.location, PortalI18n:{t:x=>x, html:tag, text:tag, markup:x=>x},
    PortalResidentProtocol:{stateSnapshot:()=>null}, PortalConversationStore:{entries:()=>[]}},
  esc: x=>String(x ?? '').replaceAll('&','&amp;').replaceAll('"','&quot;').replaceAll('<','&lt;'),
  URL, URLSearchParams, agentStatusData:{}, currentAgentStatusData:()=>context.agentStatusData, executionAccounts:()=>[],
});
vm.runInContext(fs.readFileSync(path.join(root,'portal_resident_avatar.js'),'utf8'),context);
const names=new Set(['residentNavigationUrl','agentStatusRunHref','agentStatusCreature','agentStatusCreatureGroup','agentStatusAccountDivider','agentStatusRunboard']);
for(const {node,source} of homeStatements()) if(node.type==='FunctionDeclaration'&&names.has(node.id.name)) vm.runInContext(source,context);

const run={type:'resident',resident_id:'resident_000000000051',lineage_id:'lineage_000000000001',title:'Waiting Agent'};
const main=dom.window.document.querySelector('main');
for(const avatar of [{},{sprite_index:-1},{sprite_index:12,sprite_rarity:'SR'}]) {
  main.innerHTML=context.agentStatusCreatureGroup(60,'queued',0,Array.from({length:48},()=>({...run,...avatar})));
  assert.equal(main.querySelectorAll('a.agent-status-creature').length,18);
  assert.equal(main.querySelectorAll('span.agent-status-creature').length,0);
  assert.equal(main.querySelector('.agent-status-creature-more').textContent,'+42');
  for(const link of main.querySelectorAll('a')) {
    const url=new URL(link.href);
    assert.equal(url.searchParams.get('resident'),run.resident_id);
    assert.equal(url.searchParams.get('lineage'),run.lineage_id);
    assert.equal(url.searchParams.get('open_chat'),'1');
    assert.equal(link.tabIndex,0);
    assert.equal(link.dataset.agentStatusResident,run.resident_id);
  }
}
main.innerHTML=context.agentStatusCreatureGroup(20,'queued',0,[run]);
assert.equal(main.querySelectorAll('.agent-status-creature').length,1,'Counts cannot manufacture targets');
assert.equal(main.querySelector('.agent-status-creature-more').textContent,'+19');
main.innerHTML=context.agentStatusRunboard({running:1,queued:80,queued_display_count:0,account_groups:[
  {account_id:'zero',running:1,queued:80,queued_display_count:0,running_runs:[run],queued_runs:[]},
]});
assert.equal(main.querySelectorAll('a.agent-status-creature').length,1);
assert.equal(main.querySelector('.agent-status-account-icons a')?.dataset.agentStatusResident,run.resident_id);
assert.equal(main.querySelector('.agent-status-account-label').previousElementSibling.className,'agent-status-account-icons');
assert.equal(main.querySelectorAll('.agent-status-creature.queued').length,0);
assert.match(main.querySelector('.agent-status-creature-strip').getAttribute('aria-label'),/80 queued/);
main.innerHTML=context.agentStatusCreatureGroup(1,'queued',0,[{type:'resident',title:'Agent',sprite_index:0}]);
assert.equal(main.querySelectorAll('a').length,0,'Anonymized entries must not gain an identity link');
main.innerHTML=context.agentStatusCreatureGroup(1,'running',0,[{...run,avatar_url:'/api/account/avatar/image?resident_id='+run.resident_id+'&v=upload'}]);
assert.equal(main.querySelector('.is-image').tagName,'A');
assert.match(main.querySelector('.is-image').getAttribute('style'),/avatar\/image/);
assert.equal(main.querySelector('[class*=rarity-]'),null);
// Each tooltip reads the Resident's full waiting count, not the bounded icon
// list or the account-wide queue total; the same renderer handles state updates.
let snapshot = {ready:true, state:{queued_count:80, intake_state:'open'}};
let stateReads = 0;
context.window.PortalResidentProtocol.stateSnapshot = id => {
  stateReads += 1;
  assert.equal(id, run.resident_id);
  return snapshot;
};
for (const state of ['running', 'queued', 'unread']) {
  for (const count of [80, 3, 0]) {
    snapshot.state.queued_count = count;
    main.innerHTML = context.agentStatusCreatureGroup(1, state, 0, [{...run, sprite_index:12, sprite_rarity:'SR'}]);
    const link = main.querySelector('a');
    assert.match(link.title, new RegExp(`Mailbox 排队：${count} 条`));
    assert.equal(link.getAttribute('aria-label'), link.title);
    assert.match(link.title, /Waiting Agent/);
    assert.match(link.title, /SR/);
  }
}
snapshot = {ready:true, state:{queued_count:2, intake_state:'closed'}};
main.innerHTML = context.agentStatusCreatureGroup(1, 'running', 0, [run]);
assert.match(main.querySelector('a').title, /运行中 · 接单关闭 · Mailbox 排队：2 条/);
for (const unknown of [
  {ready:false, state:{queued_count:0}}, {ready:true, state:null},
  {ready:true, state:{}}, {ready:true, state:{queued_count:-1}},
]) {
  snapshot = unknown;
  main.innerHTML = context.agentStatusCreatureGroup(1, 'running', 0, [run]);
  assert.match(main.querySelector('a').title, /Mailbox 排队：待更新/);
}
const readsBeforeAnonymous = stateReads;
main.innerHTML = context.agentStatusCreatureGroup(1, 'queued', 0, [{type:'resident',title:'Agent',sprite_index:0}]);
assert.equal(stateReads, readsBeforeAnonymous, 'Anonymous activities must not read a Resident state');
assert.doesNotMatch(main.querySelector('.agent-status-creature').title, /Mailbox/);
console.log('Home activity: real targets, fallback-avatar links, queue counts, overflow and privacy passed');
dom.window.close();
