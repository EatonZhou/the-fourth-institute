const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const source = fs.readFileSync(path.resolve(__dirname, '../../../management_portal/static/workbench_studio.js'), 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));
const tag = (parts, ...values) => parts.reduce((text, part, i) => text + part + (values[i] ?? ''), '');

async function fixture(loadLineage) {
  const dom = new JSDOM('<div class="projectionHeading"><h1 id="pageTitle"></h1><p id="pageDescription"></p></div><div id="projectionContent"></div><div id="projectionError" hidden></div>', {
    url: 'https://portal.test/workbenches?panel=settings', runScripts: 'outside-only',
  });
  const w = dom.window;
  Object.assign(w, {
    PortalProjections: { request: async () => { throw new Error('inspection must not mutate or generate'); } },
    PortalSocietyService: { createClient: () => ({ listLineages: async () => ({ lineages: [] }), loadLineage }) },
    PortalUIPrimitives: { escapeHtml: String }, PortalI18n: { markup: String, t: String, text: tag, html: tag },
    PortalSite: { setTitle() {} },
  });
  w.eval(source);
  await tick();
  const get = id => w.document.getElementById(id);
  assert.equal(get('projectionError').textContent, '');
  const select = id => { get('targetLineage').value = id; get('targetLineage').dispatchEvent(new w.Event('input')); };
  const inspect = () => get('inspect').onclick({ currentTarget: get('inspect') });
  return { w, get, select, inspect, close: () => w.close() };
}

test('team inspection renders exact member names and identities without treating titles as HTML', async () => {
  const f = await fixture(async () => ({ title: 'Team', nodes: [{ id: 'resident_111111111111', title: '<img src=x onerror=bad()>' }, { id: 'resident_222222222222', title: 'Second' }] }));
  try {
    f.select('lineage_111111111111');
    await f.inspect();
    const team = f.get('teamInfo');
    assert.equal(team.dataset.lineageId, 'lineage_111111111111');
    assert.equal(team.getAttribute('aria-busy'), 'false');
    assert.equal(team.querySelectorAll('li').length, 2);
    assert.equal(team.querySelector('li span').textContent, '<img src=x onerror=bad()>');
    assert.equal(team.querySelector('li code').textContent, 'resident_111111111111');
    assert.equal(team.querySelector('img'), null);
    assert.equal(team.querySelector('ul').tabIndex, 0);
  } finally { f.close(); }
});

test('changing the selected team invalidates an outstanding inspection and its suggested title', async () => {
  let resolve;
  const f = await fixture(() => new Promise(done => { resolve = done; }));
  try {
    f.select('lineage_old');
    const pending = f.inspect();
    assert.equal(f.get('teamInfo').getAttribute('aria-busy'), 'true');
    f.select('lineage_new');
    resolve({ title: 'Old', nodes: [{ id: 'resident_old', title: 'Old member' }] });
    await pending;
    assert.equal(f.get('teamInfo').hidden, true);
    assert.equal(f.get('teamInfo').textContent, '');
    assert.equal(f.get('appTitle').value, '');
  } finally { f.close(); }
});

test('empty teams and failed reads have distinct visible outcomes and allow another read', async () => {
  let fail = false;
  const f = await fixture(async () => {
    if (fail) throw new Error('access denied');
    return { title: 'Empty', nodes: [] };
  });
  try {
    f.select('lineage_empty'); await f.inspect();
    assert.match(f.get('teamInfo').textContent, /团队暂无成员/);
    fail = true; await f.inspect();
    assert.match(f.get('teamInfo').textContent, /团队读取失败/);
    assert.match(f.get('projectionError').textContent, /access denied/);
    assert.equal(f.get('teamInfo').hasAttribute('data-lineage-id'), false);
    assert.equal(f.get('teamInfo').getAttribute('aria-busy'), 'false');
    assert.equal(f.get('inspect').disabled, false);
  } finally { f.close(); }
});
