'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { createHarness } = require('./projection_watch_contracts.cjs');
const settle = p => p.then(value => ({ value }), error => ({ code: error.code }));
async function dialog(h) {
  const deadline = Date.now() + 3000;
  while (!h.window.document.querySelector('dialog')) {
    assert.ok(Date.now() < deadline);
    await new Promise(resolve => setTimeout(resolve, 5));
  }
  return h.window.document.querySelector('dialog');
}
async function fixture() {
  const h = await createHarness({ externalLinks: {
    music: { title: '<Music>', url: 'http://127.0.0.1:8765/' },
    unsafe: { title: 'Bad stale manifest', url: 'javascript:alert(1)' },
  } });
  h.window.PortalI18n = { t: x => x, markup: x => x };
  h.window.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  h.opens = [];
  h.window.open = (...args) => { h.opens.push(args); return null; };
  return h;
}
test('only a declared link opens after trusted confirmation, without opener or credentials', async () => {
  const h = await fixture();
  try {
    assert.equal((await settle(h.sdk.openExternal('http://evil.invalid'))).code, 'projection.external_unknown');
    assert.equal((await settle(h.sdk.openExternal('toString'))).code, 'projection.external_unknown');
    assert.equal((await settle(h.sdk.openExternal('unsafe'))).code, 'projection.external_invalid');
    const result = settle(h.sdk.openExternal('music'));
    const d = await dialog(h);
    assert.equal(d.querySelector('h3').textContent, '<Music>');
    assert.equal(d.querySelector('pre').textContent, 'http://127.0.0.1:8765/');
    assert.equal(h.opens.length, 0);
    assert.equal((await settle(h.sdk.openExternal('music'))).code, 'projection.confirmation_pending');
    d.querySelector('[data-open-external]').click();
    assert.equal((await result).value.navigation, 'requested');
    assert.deepEqual(h.opens, [['http://127.0.0.1:8765/', '_blank', 'noopener,noreferrer']]);
    assert.equal(h.window.document.querySelector('dialog'), null);
  } finally { await h.close(); }
});
for (const kind of ['cancel', 'hide', 'destroy']) test(`external navigation is revoked on ${kind}`, async () => {
  const h = await fixture();
  try {
    const result = settle(h.sdk.openExternal('music'));
    const d = await dialog(h);
    const accept = d.querySelector('[data-open-external]');
    if (kind === 'cancel') d.dispatchEvent(new h.window.Event('close'));
    else if (kind === 'hide') h.host.setVisible(false);
    else h.host.destroy();
    accept.click();
    if (kind !== 'destroy') assert.equal((await result).code, 'projection.cancelled');
    assert.equal(h.opens.length, 0);
    assert.equal(h.window.document.querySelector('dialog'), null);
  } finally { await h.close(); }
});
