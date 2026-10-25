'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const { chatWorkspaceScripts } = require('./chat_workspace_modules.cjs');
const ROOT = path.resolve(__dirname, '../../..');
const FACADE = 'resident_111111111111';
const MEMBER = 'resident_222222222222';
const REQUEST = 'msg_facade_request';
const COPY = 'msg_member_copy';
const tick = () => new Promise(resolve => setTimeout(resolve, 20));

async function main() {
  const dom = new JSDOM('<!doctype html><body><main id="chat"></main></body>', {
    url: 'https://family-queue.fixture.invalid/', runScripts: 'outside-only', pretendToBeVisual: true,
  });
  const w = dom.window;
  const observers = [];
  const Observer = w.MutationObserver;
  w.MutationObserver = class extends Observer { constructor(callback) { super(callback); observers.push(this); } };
  const calls = [], errors = [], notices = [];
  let rejectMerge = false, holdRead = null, holdMerge = null, rejectRead = false;
  let mailbox = {can_organize:true,pending:[]};
  w.CSS = { escape: value => String(value).replace(/[^a-zA-Z0-9_-]/g, c => `\\${c}`) };
  w.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
  w.matchMedia = media => Object.assign(new w.EventTarget(), { media, matches: false });
  w.PortalIdentity = { username: () => 'fixture-user' };
  w.PortalAPI = { get: async url => {
    assert.match(url, /session-restart/, 'only session restart configuration is requested');
    return { config: {}, restart: null };
  } };
  const template = (strings, ...values) => strings.reduce((s, v, i) => s + v + (values[i] ?? ''), '');
  w.PortalI18n = { t: x => x, text: template, html: template, markup: x => x };
  w.eval(require('node:child_process').execFileSync('python3', ['-c',
    'from management_portal.resources.portal import portal_route_script; print(portal_route_script().decode())'],
  { cwd: ROOT, encoding: 'utf8' }));
  // Only the instruction transport is a fixture. Service, protocol, panel,
  // rendering, DOM click delegation and action locking use production code.
  w.PortalInstructionClient = { execute: async (instruction, args) => {
    calls.push({ instruction, args: JSON.parse(JSON.stringify(args)) });
    if (instruction === 'ListInputHistory') return { entries: [] };
    if (instruction === 'GetMailbox') {
      if (holdRead) await holdRead;
      if (rejectRead) throw new Error('fixture: read failed');
      return JSON.parse(JSON.stringify(mailbox));
    }
    if (instruction === 'MergeMailboxItems') {
      if (holdMerge) await holdMerge;
      if (rejectMerge) throw new Error('fixture: item already started');
      return {resident_id:args.resident_id,message_id:'msg_merged'};
    }
    throw new Error(`Unexpected instruction: ${instruction}`);
  } };
  for (const name of ['portal_state.js', 'portal_page_state.js', 'portal_ui_primitives.js', 'portal_ui.js',
    'portal_overlay.js', 'portal_resource_chips.js', 'portal_image_editor.js', 'portal_editor_open_service.js',
    'portal_command_registry.js', 'portal_workbench_context.js', 'portal_workbench.js',
    'portal_resident_service.js', 'resident_protocol.js', ...chatWorkspaceScripts()]) {
    w.eval(fs.readFileSync(path.join(ROOT, 'management_portal/static', name), 'utf8'));
  }
  w.PortalUI.toast = (text, kind) => notices.push({ text, kind });
  const panel = w.PortalChatWorkspace.panelPackage.create({ root: w.document.getElementById('chat'),
    id: 'familyQueueFixture', autoLoad: false, target: { resident_id: FACADE }, onError: error => errors.push(error.message) });
  let refreshes = 0;
  panel.protocol = { snapshot: () => ({}), load: async () => { refreshes++; }, destroy() {} };
  const items = [{message_id:'msg_a', kind:'request', message:'first'}, {message_id:'msg_group',kind:'group',message:'existing group'}];
  function render(queueItems=items) {
    const composer = {queue:{items:queueItems,active_turn_input_supported:true}};
    panel.detail = {chat_workspace:{composer}};
    panel.workspace.applyContract({schema:'portal.chat_workspace.v3',target:{kind:'resident',id:panel.target().resident_id},header:{title:'Merge fixture'},messages:[],composer});
    panel.syncActions();
  }
  const button = () => panel.workspace.root.querySelector('[data-workspace-queue-merge-all]');
  const merges = () => calls.filter(c => c.instruction === 'MergeMailboxItems');
  const click = async () => { button().click(); await tick(); };
  try {
    render();
    assert.ok(button(), 'Resident Chat queued header exposes Merge all');
    assert.equal(button().textContent, '合并所有');
    mailbox.pending = [...items, ...Array.from({length:60},(_,i)=>({message_id:'msg_extra_'+i,kind:'request'})), {message_id:'msg_info',kind:'info'}];
    let releaseRead, releaseMerge;
    holdRead = new Promise(resolve=>releaseRead=resolve);
    holdMerge = new Promise(resolve=>releaseMerge=resolve);
    button().click(); button().click();
    assert.equal(button().disabled,true,'locks actions while loading full fresh Mailbox');
    assert.equal(calls.filter(c=>c.instruction==='GetMailbox').length,1);
    assert.equal(merges().length,0);
    releaseRead();holdRead=null;await tick();
    assert.deepEqual(merges()[0].args,{resident_id:FACADE,message_ids:mailbox.pending.filter(i=>i.kind!=='info').map(i=>i.message_id)}, 'same ordered selection as Mailbox select all, including hidden requests and whole groups, excluding info');
    assert.equal(refreshes,0,'no optimistic queue replacement');
    assert.equal(panel.workspace.root.querySelectorAll('.workspaceQueueItem').length,2);
    assert.equal(button().disabled,true,'remains locked through merge acknowledgement');
    releaseMerge();holdMerge=null;await tick();
    assert.equal(refreshes,1,'refresh only after confirmed merge');
    assert.equal(button().disabled,false);
    assert.ok(notices.some(n=>/合并/.test(n.text)));

    rejectMerge=true;await click();
    assert.match(errors.at(-1),/item already started/);
    assert.equal(refreshes,1);assert.equal(button().disabled,false);
    assert.equal(panel.workspace.root.querySelectorAll('.workspaceQueueItem').length,2);
    rejectMerge=false;await click();assert.equal(refreshes,2,'retry uses a new current mailbox read');
    let count=merges().length;
    rejectRead=true;await click();assert.match(errors.at(-1),/read failed/);assert.equal(merges().length,count);rejectRead=false;
    mailbox.can_organize=false;await click();assert.equal(merges().length,count);assert.match(errors.at(-1),/权限/);
    mailbox.can_organize=true;mailbox.pending=[items[1],{message_id:'msg_info',kind:'info'}];
    await click();assert.equal(merges().length,count,'single group is not remerged');assert.match(notices.at(-1).text,/至少两/);
    render([items[1]]);assert.equal(button().disabled,true,'single displayed group shows disabled Merge all');
    render([]);assert.equal(button(),null,'empty queue has no toolbar');
    render(); mailbox.pending=items;
    holdRead=new Promise(resolve=>releaseRead=resolve);button().click();
    panel.targetRevision++;panel.workspace.setTarget({resident_id:MEMBER});
    releaseRead();holdRead=null;await tick();
    assert.equal(merges().length,count,'switch during preflight cancels unsent merge');
    panel.workspace.setTarget({resident_id:FACADE});render();
    holdMerge=new Promise(resolve=>releaseMerge=resolve);button().click();await tick();
    const noticeCount=notices.length,refreshCount=refreshes;
    panel.targetRevision++;panel.workspace.setTarget({resident_id:MEMBER});
    releaseMerge();holdMerge=null;await tick();
    assert.equal(refreshes,refreshCount,'old acknowledgement does not refresh new chat');
    assert.equal(notices.length,noticeCount,'old acknowledgement does not announce success in new chat');
    panel.workspace.setTarget({resident_id:FACADE});render();
    rejectMerge=true;holdMerge=new Promise(resolve=>releaseMerge=resolve);button().click();await tick();
    const errorCount=errors.length;
    panel.targetRevision++;panel.workspace.setTarget({resident_id:MEMBER});releaseMerge();holdMerge=null;await tick();
    assert.equal(errors.length,errorCount,'old failure does not appear in new chat');
    rejectMerge=false;
    const other=w.PortalChatWorkspace.createWorkspace({root:w.document.body.appendChild(w.document.createElement('div')),id:'plainChat',refs:false});
    other.applyContract({schema:'portal.chat_workspace.v3',target:{kind:'chatroom',id:'chatroom_fixture'},header:{title:'Other'},messages:[],composer:{queue:{items}}});assert.equal(other.root.querySelector('[data-workspace-queue-merge-all]'),null,'non-Resident host has no merge capability');other.destroy();
    console.log(JSON.stringify({status:'passed',scope:'real Chat DOM/Panel/Service/Protocol; transport only fixture',mergeCalls:merges().length,refreshes}));
  } finally { await tick(); panel.destroy(); for(const observer of observers) observer.disconnect(); await tick(); w.close(); }
}
main().catch(error=>{console.error(error);process.exitCode=1;});
