'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const { chatWorkspaceScripts } = require('./chat_workspace_modules.cjs');
const { portalWorkbenchScripts } = require('./portal_workbench_scripts.cjs');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const dom = new JSDOM('<section id="fixture"></section>', {
  url: 'https://chat.fixture.invalid/lineage', pretendToBeVisual: true, runScripts: 'outside-only',
});
const w = dom.window;
const observers = new Set();
const NativeMutationObserver = w.MutationObserver;
w.MutationObserver = class extends NativeMutationObserver {
  constructor(callback) { super(callback); observers.add(this); }
};
w.CSS = { escape: value => String(value).replace(/[^a-zA-Z0-9_-]/g, char => `\\${char}`) };
w.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
w.matchMedia = query => Object.assign(new w.EventTarget(), { media: query, matches: false });
w.PortalIdentity = { username: () => 'sample_admin' };
w.eval(input.routeScript);
for (const name of ['portal_state.js', 'portal_page_state.js', 'portal_text.js', 'portal_ui_primitives.js', 'portal_ui.js',
  'portal_overlay.js', 'portal_resource_chips.js', 'portal_image_editor.js', 'portal_editor_open_service.js',
  'portal_command_registry.js', 'portal_workbench_context.js', ...portalWorkbenchScripts(), 'resident_protocol.js', ...chatWorkspaceScripts()]) {
  w.eval(fs.readFileSync(path.resolve(__dirname, '../../../management_portal/static', name), 'utf8'));
}
w.eval(fs.readFileSync(path.resolve(__dirname, '../../../management_portal/static/portal_resident_service.js'), 'utf8'));
w.PortalResidentService = {...w.PortalResidentService};
const row = (id, group = 'one') => ({input_message_id:id, label:'Authorized paper',
  created_at:'2026-11-11T12:00:00Z', state:'completed', business_context:{application:'paper.review',
  subject_ref:'paper_fixture', group_ref:group, attempt_ref:id}});
async function main() {
  let wire;
  const client = w.PortalResidentService.createClient({execute:async (name,args) => {
    wire={name,args}; return {attempts:[],next_cursor:'older-wire'};
  }});
  const page=await client.businessHistory('resident_111111111111');
  assert.equal(wire.name,'ListMessages'); assert.equal(wire.args.view,'attempts');
  assert.equal(wire.args.cursor,''); assert.equal(page.next_page_token,'older-wire');
  const panel = w.PortalChatWorkspace.panelPackage.create({root:w.document.querySelector('#fixture'),
    autoLoad:false, target:{resident_id:'resident_111111111111'}});
  const messages = panel.workspace.element('messages'); assert.ok(messages);
  let calls = [];
  w.PortalResidentService.businessHistory = async (_id, options) => {
    calls.push(options);
    if (options.inputMessageId) return {conversation:{messages:[{role:'user',content_parts:[{type:'text',text:'Long private input'}]},
      {role:'agent',content_parts:[{type:'text',text:'Saved reply'},{type:'image',source:'owned/figure.png'}]}]}};
    return {attempts: options.pageToken ? [row('second')] : [row('first')], next_page_token:options.pageToken?'':'older'};
  };
  await panel.loadBusinessHistory();
  assert.equal(panel.businessHistory.mode, 'attempts'); assert.equal(messages.hidden,true);
  assert.equal(panel.businessHistory.panel.querySelectorAll(':scope > details').length,1);
  panel.businessHistory.panel.querySelector('button').click();
  await new Promise(resolve => setTimeout(resolve,0));
  assert.match(panel.businessHistory.panel.textContent,/Saved reply/);
  assert.match(panel.businessHistory.panel.textContent,/owned\/figure.png/,'structured artifact references are retained');
  assert.ok([...panel.businessHistory.panel.querySelectorAll('details details')].every(item=>!item.open),'long inputs collapsed');
  await panel.loadBusinessHistory({older:true});
  assert.equal(panel.businessHistory.rows.length,2);
  assert.equal(panel.businessHistory.panel.querySelectorAll(':scope > details').length,1,'same subject version grouped');
  panel.businessHistory.toolbar.querySelectorAll('button')[1].click();
  assert.equal(messages.hidden,false,'raw conversation preserved');
  let release;
  w.PortalResidentService.businessHistory = () => new Promise(resolve => {release=resolve;});
  const late = panel.loadBusinessHistory(); panel.targetRevision += 1;
  release({attempts:[row('stale-account')]}); await late;
  assert.ok(!panel.businessHistory.rows.some(item=>item.input_message_id==='stale-account'));
  w.PortalResidentService.businessHistory = async () => {throw new Error('Authorization owner unavailable');};
  await panel.loadBusinessHistory();
  assert.equal(panel.businessHistory.rows.length,0,'failed authorization refresh clears prior rows');
  assert.match(panel.businessHistory.panel.textContent,/Authorization owner unavailable/);
  w.PortalResidentService.businessHistory = async () => ({attempts:[]});
  await panel.loadBusinessHistory();
  assert.ok(!panel.businessHistory.panel.textContent.includes('Authorization owner unavailable'));
  panel.businessHistory.mode=null;
  w.PortalResidentService.businessHistory = async () => ({attempts:[row('mixed')],default_view:'messages'});
  await panel.loadBusinessHistory();
  assert.equal(panel.businessHistory.mode,'messages','continuous/mixed targets honor the original conversation default');
  assert.equal(messages.hidden,false);
  assert.ok(calls.every(call=>!('prompt' in call)));
  panel.destroy(); for (const observer of observers) observer.disconnect();
  await new Promise(resolve => setTimeout(resolve, 20)); dom.window.close();
  console.log('Shared full Chat: grouping, detail, raw toggle, paging, late identity, ACL failure/recovery passed');
}
main().catch(error=>{console.error(error);dom.window.close();process.exitCode=1;});
