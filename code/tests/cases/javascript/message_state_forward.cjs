'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { mount } = require('./message_state_family.cjs');
const drain = () => new Promise(resolve => setImmediate(resolve));
const plain = value => JSON.parse(JSON.stringify(value));

// Actual forward dialog, native DOM events, PortalResidentService serialization,
// and production CSS. Only the external instruction responses/latency are inputs.
async function check(routeScript, options = {}) {
  const app = mount(routeScript, [], options);
  const { window } = app;
  const calls = [];
  let mode = 'rooms', finishForward, finishResidentLoad, residentOnlyBody;
  window.PortalIdentity = { load: async () => ({ username: 'CASE-A' }), userId: async () => 'CASE-A' };
  window.PortalInstructionClient = { execute: async (name, args, controls) => {
    if (name === 'ListChatrooms') {
      assert.deepEqual(plain(args), { user_id: 'CASE-A' });
      if (mode === 'error') throw new Error('聊天室暂不可用');
      return { chatrooms: mode === 'empty' ? [] : [
        { chatroom_id: 'chatroom_111111111111', title: '讨论组', permissions: { can_post: true } },
        { chatroom_id: 'chatroom_222222222222', title: '另一组', permissions: { can_post: true } },
        { chatroom_id: 'chatroom_333333333333', title: '不可发送', permissions: { can_post: false } },
      ] };
    }
    assert.equal(name, 'ForwardMessages');
    calls.push(plain({ name, args, controls }));
    return new Promise(resolve => { finishForward = () => resolve({ message_id: 'forwarded' }); });
  } };
  const serviceFile = 'portal_resident_service.js';
  const serviceSource = fs.readFileSync(path.resolve(__dirname, '../../../management_portal/static', serviceFile), 'utf8');
  window.eval((options.transform ? options.transform(serviceFile, serviceSource) : serviceSource) + `\n//# sourceURL=${serviceFile}`);
  const items = [
    { resident_id: 'resident_111111111111', message_id: 'one', content: '第一条' },
    { resident_id: 'resident_222222222222', message_id: 'two', content: '第二条' },
  ];
  const open = (slow = false) => window.PortalChatWorkspace.openResidentForwardDialog(items, {
    loadTargets: () => slow ? new Promise(resolve => { finishResidentLoad = resolve; }) : Promise.resolve({ scopes: [] }),
    loadChatrooms: () => window.PortalResidentService.forwardChatrooms(),
    submit: body => window.PortalResidentService.forward(body),
  });
  const change = (node, value) => {
    node.value = value;
    node.dispatchEvent(new window.Event('change', { bubbles: true }));
  };
  try {
    const opening = open(true);
    const overlay = window.document.querySelector('.residentForwardOverlay');
    const dialog = overlay.querySelector('[role="dialog"]');
    const get = selector => {
      const nodes = dialog.querySelectorAll(selector);
      assert.equal(nodes.length, 1, `one ${selector}`);
      return nodes[0];
    };
    const destination = get('[data-forward-destination]');
    const rooms = get('[data-forward-chatroom]');
    const submit = get('[data-resident-forward-submit]');
    change(destination, 'chatroom');
    await drain();
    assert.deepEqual([...rooms.options].map(option => option.textContent), ['讨论组', '另一组']);
    change(rooms, 'chatroom_222222222222');
    get('[data-resident-forward-message]').value = '供大家讨论';
    submit.click();
    finishResidentLoad({ scopes: [] });
    await opening;
    assert.equal(submit.textContent, '发送中…');
    assert.equal(submit.disabled, true, 'slow Resident result must not re-enable an active Chatroom send');
    assert.equal(typeof finishForward, 'function');
    finishForward();
    await drain();
    app.display(overlay, 'none');
    assert.equal(calls.length, 1);
    assert.deepEqual(calls[0].args, { target_chatroom_id: 'chatroom_222222222222', note: '供大家讨论', messages: [
      { resident_id: 'resident_111111111111', message_id: 'one' },
      { resident_id: 'resident_222222222222', message_id: 'two' },
    ] });
    assert.ok(calls[0].controls.idempotencyKey);

    mode = 'empty';
    const reopening = open();
    finishResidentLoad({ scopes: [] });
    await reopening;
    change(destination, 'chatroom');
    await drain();
    assert.equal(rooms.disabled, true);
    assert.match(rooms.textContent, /暂无可发送的聊天室/);
    assert.equal(submit.disabled, true);
    change(destination, 'resident');
    mode = 'error';
    change(destination, 'chatroom');
    await drain();
    assert.equal(get('[data-forward-chatroom-hint]').textContent, '聊天室暂不可用');
    assert.equal(submit.disabled, true);
    get('[data-resident-forward-cancel]').click();

    await window.PortalChatWorkspace.openResidentForwardDialog(items, {
      selectTargetOnly: true,
      loadTargets: async query => query.residentId
        ? { target: { resident_id: query.residentId, title: '助手', lineage_id: 'lineage_111111111111' } } : { scopes: [] },
      submit: async body => { residentOnlyBody = body; },
    });
    app.display(destination.closest('.residentForwardField'), 'none');
    app.display(get('[data-resident-forward-message]').closest('.residentForwardField'), 'none');
    get('[data-resident-forward-direct-id]').value = 'resident_111111111111';
    get('[data-resident-forward-direct-locate]').click();
    await drain();
    submit.click();
    await drain();
    assert.equal(residentOnlyBody.target_resident_id, 'resident_111111111111');
    assert.equal(residentOnlyBody.target_chatroom_id, undefined);
  } finally { await app.close(); }
}

module.exports = { check };
if (require.main === module) {
  check(fs.readFileSync(0, 'utf8')).then(() => process.stdout.write('PASS forward-chatroom\n'));
}
