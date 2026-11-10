// Direct lifecycle/precondition regression; no browser/PDF engine replay.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const {randomUUID} = require('node:crypto');
const root = path.resolve(__dirname, '../../..');
const dom = new JSDOM('<div id="folderTree"></div><div id="workspaceMain"></div>', {
  url: 'http://paper.test/', runScripts: 'outside-only',
});
const w = dom.window;
Object.defineProperty(w.crypto, 'randomUUID', {value: randomUUID});
w.PortalI18n = {t: x => x, html: (s, ...v) => s.reduce((a, x, i) => a + x + (v[i] ?? ''), '')};
const renderers = [], calls = [];
let conflict = false;
w.PortalPreview = {mountZoom: () => ({dispose() {}, setEnabled() {}})};
w.PortalPdfPreview = {render: options => {
  renderers.push(options); return {root: options.root, promise: new Promise(() => {}), cancel() {}};
}};
for (const file of ['paper_reader.js', 'paper_personal.js']) w.eval(fs.readFileSync(path.join(root, 'management_portal/static', file), 'utf8'));
const state = {principalId: 'alice', username: 'Alice display', mode: 'reader', selectedId: 'paper_000000000001', favoriteIds: new Set()};
const deps = Object.fromEntries(w.PortalPaperReader.dependencies.map(key => [key, () => '']));
Object.assign(deps, {state, $: id => w.document.getElementById(id), esc: String, md: String, detailSectionsLoaded: () => true});
const reader = w.PortalPaperReader.createReader(deps);
w.PortalPaperPersonal.mount({state, paperApi: {request: async (url, options) => {
  calls.push({url, options}); if (conflict) throw {status: 409, message: 'session_changed'};
  return {};
}}, loadDetail: async () => {}, setMode() {}});
function open() {
  state.detail = {paper: {id: state.selectedId, title: 'Fixture', pdf_url: '/fixture.pdf'}};
  reader.renderReaderMode(); return renderers.at(-1);
}
(async () => {
  let rendering = open(); rendering.onReady(); rendering.onReady();
  assert.equal(calls.length, 1);
  assert.equal(calls[0].options.body.expected_principal, 'alice');
  assert.equal(calls[0].options.body.actor, undefined);
  rendering = open(); state.principalId = 'bob'; rendering.onReady();
  assert.equal(calls.length, 1, 'changed bootstrap principal rejects a late ready');
  state.principalId = 'alice'; const old = open(); open(); old.onReady();
  assert.equal(calls.length, 1, 'old generation must not reacquire identity');
  // Page still shows A but authentication cookie changed to B: server rejects.
  conflict = true; rendering = renderers.at(-1); rendering.onReady();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(calls.length, 2);
  assert.equal(calls[1].options.body.expected_principal, 'alice');
  assert.ok(w.document.querySelector('[data-status]').textContent.includes('账号已变化'));
  rendering.onReady(); assert.equal(calls.length, 2, '409 does not refresh identity or replay');
  assert.ok(calls.every(call => call.url === '/paper/api/recent-reading/open'));
  conflict = false; state.username = 'Renamed display'; rendering = open(); rendering.onReady();
  assert.equal(calls.at(-1).options.body.expected_principal, 'alice', 'display text is not the stable principal');
  state.principalId = ''; rendering = open(); state.principalId = 'alice'; rendering.onReady();
  assert.equal(calls.length, 3, 'a reading begun before authenticated bootstrap is not rebound later');
  dom.window.close(); console.log('PASS frozen principal, stale ready, 409 no replay, no actor authority');
})().catch(error => {console.error(error); dom.window.close(); process.exitCode = 1;});
