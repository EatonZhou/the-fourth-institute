'use strict';
const assert = require('node:assert/strict');
const path = require('node:path');
module.exports = async ({ page, output }) => {
  await page.evaluate(() => {
    window.PortalIdentity = { load: async () => {}, username: () => 'reader' };
    window.calls = []; window.pending = []; window.gated = false;
    window.PortalResidentProjection = { load: async id => ({ resident: { resident_id: id },
      chat_workspace: { schema: 'portal.chat_workspace.v3', target: { kind: 'resident', id }, composer: { can_send: true } } }), subscribe: () => () => {} };
    window.PortalResidentService = {
      snapshot: async (id, { historyLimit }) => {
        calls.push({ id, historyLimit });
        const value = { resident: { resident_id: id }, cursor: 1, state: { work_state: 'idle' }, controls: { can_send: true },
          messages: Array.from({ length: 20 }, (_, n) => ({ message_id: id + '-message' + (n || ''), role: 'user', content_parts: [{ type: 'text', text: '已打开的会话 ' + id + '\n' + '可恢复的阅读位置\n'.repeat(6) }] })),
          input_states: [], queue: { items: [] }, activities: [], next_page_token: '' };
        return gated ? new Promise((resolve, reject) => pending.push({ resolve, reject, value })) : value;
      }, inputHistory: async () => ({ entries: [] }), runtimeSettings: async () => ({ settings: {} }), runtimeCatalog: async () => ({})
    };
    window.panel = PortalChatWorkspace.panelPackage.create({ root: document.getElementById('chat'), id: 'resume', autoLoad: false });
    window.open = id => panel.setResident({ resident_id: id }, { load: true });
  });
  await page.evaluate(() => open('resident_111111111111'));
  await page.locator('[data-message-key="resident_111111111111-message8"]').scrollIntoViewIfNeeded();
  await page.locator('.paneBody').first().hover(); await page.mouse.wheel(0, -20); await page.waitForTimeout(100);
  const anchorY = await page.locator('[data-message-key="resident_111111111111-message8"]').evaluate(n => n.getBoundingClientRect().top);
  await page.evaluate(() => open('resident_222222222222'));
  await page.evaluate(() => open('resident_333333333333'));
  const restored = await page.evaluate(async () => {
    gated = true; const start = performance.now(); await open('resident_111111111111');
    return { elapsed: performance.now() - start, cached: panel.protocol.snapshot().cache_restored, calls: calls.length, pending: pending.length };
  });
  assert.equal(restored.cached, true); assert.equal(restored.pending, 1); assert.equal(restored.calls, 4);
  assert.equal(await page.evaluate(() => panel.protocol.snapshot().controls.can_send), false);
  assert(Math.abs(await page.locator('[data-message-key="resident_111111111111-message8"]').evaluate(n => n.getBoundingClientRect().top) - anchorY) < 2);
  assert(await page.locator('[data-message-key="resident_111111111111-message"]').count());
  if (output) await page.screenshot({ path: path.join(output, 'cache-before-network.png') });
  for (const id of ['resident_222222222222', 'resident_333333333333', 'resident_111111111111',
    'resident_222222222222', 'resident_333333333333', 'resident_111111111111']) {
    await page.evaluate(id => open(id), id);
    assert.equal(await page.evaluate(() => panel.protocol.snapshot().cache_restored), true);
    assert.equal(await page.evaluate(() => panel.protocol.snapshot().controls.can_send), false);
  }
  await page.evaluate(() => pending.splice(0, pending.length - 1).forEach(p => p.resolve(p.value)));
  await page.waitForTimeout(50);
  assert.equal(await page.evaluate(() => panel.protocol.snapshot().cache_restored), true, 'abandoned refreshes cannot authorize the current target');
  assert(Math.abs(await page.locator('[data-message-key="resident_111111111111-message8"]').evaluate(n => n.getBoundingClientRect().top) - anchorY) < 2);
  await page.evaluate(() => { const p = pending.shift(); p.value.messages[0].content_parts[0].text = '权威更新后的内容'; p.resolve(p.value); });
  await page.waitForFunction(() => !panel.protocol.snapshot().cache_restored);
  assert((await page.locator('[data-message-key="resident_111111111111-message"]').textContent()).includes('权威更新'));
  // Return to B, then revoke its access during the asynchronous refresh.
  await page.evaluate(() => open('resident_222222222222'));
  await page.evaluate(() => pending.shift().reject(Object.assign(new Error('denied'), { status: 403 })));
  await page.waitForFunction(() => panel.protocol === null);
  assert.equal(await page.locator('[data-message-key="resident_222222222222-message"]').count(), 0);
  // A rejected target must perform a new read, never restore its cached body.
  await page.evaluate(() => { window.reopen = open('resident_222222222222').catch(e => e.message); });
  await page.waitForFunction(() => pending.length === 1);
  assert.equal(await page.evaluate(() => panel.protocol?.snapshot()?.cache_restored || false), false);
  await page.evaluate(() => { const p = pending.shift(); p.resolve(p.value); });
  await page.evaluate(() => reopen);
  await page.evaluate(() => { panel.protocol.destroy(); panel.protocol = null; window.dispatchEvent(new Event('portal:identity-changed')); window.reopen = open('resident_111111111111'); });
  await page.waitForFunction(() => pending.length === 1);
  assert.equal(await page.evaluate(() => panel.protocol?.snapshot()?.cache_restored || false), false);
  await page.evaluate(() => { const p = pending.shift(); p.resolve(p.value); });
  await page.evaluate(() => reopen);
  const boundaries = await page.evaluate(async () => {
    gated = false;
    const acquire = id => PortalResidentProtocol.acquire(id, () => {}, { load: false });
    // Idle snapshots are bounded independently of state-only subscriptions.
    for (let n = 0; n < 10; n++) {
      const sub = acquire('cache-' + n); await sub.load({ historyLimit: 2 }); sub.destroy();
    }
    let before = calls.length;
    let sub = acquire('cache-0'); await sub.load({ resume: true, historyLimit: 2 });
    const evicted = calls.length === before + 1; sub.destroy();
    const now = Date.now; Date.now = () => now() + 6 * 60 * 1000;
    before = calls.length; sub = acquire('cache-9'); await sub.load({ resume: true, historyLimit: 2 });
    const expired = calls.length === before + 1; sub.destroy(); Date.now = now;
    sub = acquire('expiry-repeat'); await sub.load({ historyLimit: 2 }); sub.destroy();
    Date.now = () => now() + 4 * 60 * 1000;
    sub = acquire('expiry-repeat'); await sub.load({ resume: true, historyLimit: 2 }); sub.destroy();
    Date.now = () => now() + 6 * 60 * 1000;
    before = calls.length; sub = acquire('expiry-repeat'); await sub.load({ resume: true, historyLimit: 2 });
    const repeatExpired = calls.length === before + 1; sub.destroy(); Date.now = now;
    // A refresh finishing after navigation cannot replace the new window.
    sub = acquire('late'); await sub.load({ historyLimit: 2 }); sub.destroy();
    sub = acquire('late'); await sub.load({ resume: true, historyLimit: 2 });
    gated = true; const late = sub.load({ force: true }).catch(e => e.name);
    await new Promise(resolve => setTimeout(resolve, 0)); sub.destroy();
    const p = pending.shift(); p.resolve(p.value);
    const result = await late; gated = false;
    return { evicted, expired, repeatExpired, late: result };
  });
  assert.deepEqual(boundaries, { evicted: true, expired: true, repeatExpired: true, late: 'AbortError' });
  console.log(JSON.stringify({ restored, checks: ['A-B-A visible before network', 'one background read', 'fresh body replaces cache', '403 removes body and cache', 'identity clears retained snapshots'] }));
};
