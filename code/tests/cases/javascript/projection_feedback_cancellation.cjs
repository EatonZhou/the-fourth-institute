'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { createHarness } = require('./projection_watch_contracts.cjs');
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(predicate) {
  const deadline = Date.now() + 3000;
  while (!predicate()) { assert.ok(Date.now() < deadline, 'confirmation became observable'); await pause(5); }
}
const object = ref => ({ ref, kind: 'resource', status: 'current', source: '', observed_at: '2026-10-22T00:00:00Z' });
const settle = promise => promise.then(result => ({ result }), error => ({ code: error.code, message: error.message }));

async function fixture({ holdFacade = false } = {}) {
  const h = await createHarness({
    context: [{ channel: 'focus', publishers: ['architecture'], subscribers: ['architecture'] }],
    objectResponse: payload => Response.json(payload.object),
  });
  const fetch = h.window.fetch;
  let release;
  const facade = new Promise(resolve => { release = resolve; });
  h.facadeCalls = 0;
  h.window.fetch = async (url, options) => {
    if (url === '/api/workbenches/facade') {
      h.facadeCalls++;
      if (holdFacade) await facade;
      return Response.json({ resident_id: 'resident_111111111111', lineage_id: 'lineage_111111111111' });
    }
    if (url === '/api/workbenches/resource') {
      assert.equal(JSON.parse(options.body).operation, 'resolve');
      return Response.json({ readable: true, title: '验收门面' });
    }
    return fetch(url, options);
  };
  // jsdom omits the native dialog methods. Only the HTML dialog's event
  // boundary is supplied; the real host, SDK, cancellation and replies run.
  h.window.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  h.window.HTMLDialogElement.prototype.close = function (value) {
    this.returnValue = value; this.open = false;
    this.dispatchEvent(new h.window.Event('close'));
  };
  await h.sdk.focus(object('resource_A'));
  await h.sdk.watch(['resource_A'], () => {});
  h.releaseFacade = release;
  return h;
}

for (const [name, cancel, reason] of [
  ['user confirmation', (h, dialog) => dialog.close('cancel'), /已取消确认，反馈尚未发送/],
  ['explicit region cancellation', h => h.sdk.cancelFeedback(), /当前功能区已取消反馈/],
  ['hidden region', h => h.host.setVisible(false), /反馈功能区已隐藏/],
  ['new selection', h => h.sdk.focus(object('resource_B')), /讨论对象已重新选择/],
  ['resource notification', h => h.refresh('resource_A'), /所观察资源收到更新通知/],
  ['observation resync', h => h.refresh(), /资源观察需要重新核对/],
]) {
  test(`feedback preserves cancellation reason: ${name}`, async () => {
    const h = await fixture();
    try {
      const result = settle(h.sdk.feedback({ ref: 'resource_A', subject: '模块 A', text: '保留这段草稿' }));
      await until(() => h.window.document.querySelector('dialog[open]'));
      const dialog = h.window.document.querySelector('dialog');
      assert.match(dialog.textContent, /模块 A/);
      assert.match(dialog.textContent, /保留这段草稿/);
      await cancel(h, dialog);
      const error = await result;
      assert.equal(error.code, 'projection.cancelled');
      assert.match(error.message, reason);
      assert.equal(h.window.document.querySelector('dialog'), null, 'confirmation closes on cancellation');
      assert.equal(h.facadeCalls, 1, 'cancelled confirmation never enters the native send path');
    } finally { h.releaseFacade(); await h.close(); }
  });
}

test('context invalidation while resolving recipient retains its reason and opens no stale dialog', async () => {
  const h = await fixture({ holdFacade: true });
  try {
    const result = settle(h.sdk.feedback({ ref: 'resource_A', subject: '模块 A', text: '草稿' }));
    await until(() => h.facadeCalls === 1);
    h.host.setVisible(false);
    h.releaseFacade();
    const error = await result;
    assert.equal(error.code, 'projection.cancelled');
    assert.match(error.message, /反馈功能区已隐藏/);
    assert.equal(h.window.document.querySelector('dialog'), null);
    assert.equal(h.facadeCalls, 1);
  } finally { h.releaseFacade(); await h.close(); }
});
