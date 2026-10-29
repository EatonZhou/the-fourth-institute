'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {mount} = require('./message_state_family.cjs');

(async () => {
  const app = mount(fs.readFileSync(process.argv[2], 'utf8'), []);
  const {window} = app;
  let panel;
  const active = new Set(), calls = [];
  let releaseFirst;
  const firstLoad = new Promise(resolve => {releaseFirst = resolve;});
  const facade = 'resident_222222222222', first = 'resident_111111111111', second = 'resident_333333333333';
  const memberSnapshot = {state: {work_state: 'idle', intake_state: 'open'}, messages: []};
  const detail = {resident: {backend: 'codex'}, allows: {}, runtime_settings: {}, chat_workspace: {actions: {}}};
  try {
    window.PortalAPI = {get: async () => ({accounts: []})};
    window.PortalInstructionClient = {execute: async instruction => {
      // The roster reads the viewer's M/S routing preferences on each load.
      if (instruction === 'GetFamilyChatPreferences') return {muted_member_ids: [], solo_member_id: ''};
      assert.equal(instruction, 'ListInputHistory'); return {entries: []};
    }};
    window.PortalResidentService = {
      projection: async ({residentId}) => {calls.push(residentId); if (residentId === first) await firstLoad; return detail;},
      inputHistory: async () => ({entries: []}),
      runtimeSettings: async () => ({settings: {}}), runtimeCatalog: async () => ({}),
      observeSessionRestart: () => () => {},
    };
    window.eval(fs.readFileSync(path.resolve(__dirname,
      '../../../management_portal/static/portal_resident_runtime_controller.js'), 'utf8'));
    window.PortalResidentRuntimeController = window.PortalResidentRuntimeController.create({
      service: window.PortalResidentService,
      dialogs: {settings: () => ({destroy() {}, close() {}}), restart: () => ({destroy() {}, close() {}})},
    });
    window.PortalResidentProtocol = {...window.PortalResidentProtocol, acquire: id => {
      active.add(id);
      return {load: async () => memberSnapshot, destroy: () => active.delete(id)};
    }};
    const root = window.document.createElement('section'); window.document.body.append(root);
    panel = window.PortalChatWorkspace.panelPackage.create({root, id: 'liveRoster', autoLoad: false,
      target: {resident_id: facade}, onError: error => {throw error;}});
    panel.residentProjection = {resident: {resident_id: facade},
      chat_workspace: {schema: 'portal.chat_workspace.v3', target: {kind: 'resident', id: facade},
        header: {title: 'Family'}, messages: [], composer: {can_send: true}}};
    let snap = {messages: [], controls: {}, state: {work_state: 'idle'},
      family_facade: {family_id: 'family_fixture', title: 'Family', nodes: [], members: []}};
    panel.protocol = {snapshot: () => snap, destroy() {}};
    const update = ids => {
      snap = {...snap, family_facade: {...snap.family_facade,
        members: ids.map(resident_id => ({resident_id, title: resident_id})),
        nodes: ids.map(resident_id => ({resident_id, title: resident_id, role: 'member'}))}};
      panel.applySnapshot(snap);
    };
    update([first]);
    const button = window.document.createElement('button'); window.document.body.append(button);
    panel.openFamilyStatus(button);
    await new Promise(resolve => setImmediate(resolve));
    assert.ok(window.document.querySelector(`[data-family-status-row="${first}"]`));
    update([first, second]); // Arrives while the first member's transport is still loading.
    releaseFirst();
    for (let tick = 0; tick < 10; tick++) await new Promise(resolve => setImmediate(resolve));
    assert.ok(window.document.querySelector(`[data-family-status-row="${second}"]`), 'open roster follows new snapshot without closing/reopening');
    assert.equal(panel.detail.family_facade.members.length, 2);
    const reads = calls.length;
    panel.applySnapshot(snap);
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(calls.length, reads, 'ordinary conversation snapshots do not reload member details');
    update([second]);
    for (let tick = 0; tick < 5; tick++) await new Promise(resolve => setImmediate(resolve));
    assert.equal(window.document.querySelector(`[data-family-status-row="${first}"]`), null);
    assert.deepEqual([...active], [second], 'removed member subscription is released');
    panel.updateFamilyStatus(null);
    assert.equal(active.size, 0, 'facade switch releases remaining subscriptions');
    console.log('Family snapshot, concurrent load, live additions/removals and cleanup passed');
  } finally {
    releaseFirst();
    panel?.destroy();
    await app.close();
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
