'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const tick = () => new Promise(resolve => setImmediate(resolve));
const A = 'workbench_aaaaaaaaaaaa', B = 'workbench_bbbbbbbbbbbb';

function fixture() {
  const dom = new JSDOM('<body><main></main></body>', { url: 'https://fixture.invalid/workbenches', runScripts: 'outside-only' });
  const w = dom.window, requests = [], errors = [];
  const tag = (parts, ...values) => String.raw({ raw: parts }, ...values);
  w.PortalI18n = { t: value => value, text: tag, html: tag };
  w.PortalUIPrimitives = {
    escapeHtml: value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])),
    mountIcons() {},
  };
  w.PortalUI = { toast: message => errors.push(message), formatDateTime: () => '' };
  w.CSS = { escape: value => value };
  w.eval(fs.readFileSync(path.resolve(__dirname, '../../../management_portal/static/portal_workbench_catalog.js'), 'utf8'));
  const root = w.document.querySelector('main');
  const api = w.PortalWorkbenchCatalog.mount(root, {
    workbenches: [A, B].map(id => ({ workbench_id: id, title: id, published_revision: 1, project_ids: [], bindings: {} })),
    favorites: [],
    onFavorite: (id, favorite) => new Promise((resolve, reject) => requests.push({ id, favorite, resolve, reject })),
  });
  const button = id => root.querySelector(`[data-workbench-favorite="${id}"]`);
  return { root, api, requests, errors, window: w,
    click(id) { button(id).focus(); button(id).click(); },
    on: id => button(id).getAttribute('aria-pressed') === 'true',
    focus: () => w.document.activeElement.dataset.workbenchFavorite,
    close() { api.destroy(); dom.window.close(); },
  };
}

test('toggle earlier success cannot undo the latest unstar intent', async () => {
  const h = fixture();
  try {
    h.click(A); h.click(A);
    assert.equal(h.on(A), false);
    h.requests[0].resolve([A]); await tick();
    assert.equal(h.on(A), false, 'first success must not undo the pending unstar');
    assert.deepEqual(h.requests.map(({ id, favorite }) => [id, favorite]), [[A, true], [A, false]]);
    h.requests[1].resolve([]); await tick();
    assert.equal(h.on(A), false);
    assert.equal(h.focus(), A);
    assert.equal(h.errors.length, 0);
  } finally { h.close(); }
});

test('failure rolls back only its own intent and keeps another pending favorite focused', async () => {
  const h = fixture();
  try {
    h.click(A); h.click(B);
    h.requests[0].reject(Error('fixture permission denied')); await tick();
    assert.equal(h.on(A), false);
    assert.equal(h.on(B), true, 'A failure must preserve B optimistic intent');
    assert.equal(h.focus(), B, 'old request completion must not steal focus');
    assert.equal(h.errors.length, 1);
    assert.equal(h.requests[1].id, B);
    h.requests[1].resolve([B]); await tick();
    assert.equal(h.on(B), true);
    h.click(A);
    h.requests[2].resolve([B, A]); await tick();
    assert.equal(h.on(A), true, 'explicit retry remains possible');
    assert.equal(h.on(B), true);
    assert.equal(h.requests.length, 3, 'no automatic retry');
  } finally { h.close(); }
});

test('destroy stops queued favorite writes and ignores the in-flight result', async () => {
  const h = fixture();
  try {
    h.click(A); h.click(B);
    assert.equal(h.requests.length, 1, 'favorite mutations must be ordered before dispatch');
    h.api.destroy();
    h.requests[0].resolve([A]); await tick();
    assert.equal(h.requests.length, 1, 'unstarted write is discarded on teardown');
    assert.equal(h.root.childElementCount, 0);
    assert.equal(h.errors.length, 0);
  } finally { h.close(); }
});
