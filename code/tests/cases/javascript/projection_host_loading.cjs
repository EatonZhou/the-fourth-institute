'use strict';
// One Chromium instance, sequential pages; complete production host/SDK/Workbench.
// All HTTP is a loopback fixture. No live API, chat, model, or business operations.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const crypto = require('node:crypto');
const { chromium } = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const repo = path.resolve(__dirname, '../../..');
const staticRoot = path.join(repo, 'management_portal/static');
const modules = ['portal_text.js', 'portal_state.js', 'portal_i18n.js', 'portal_ui.js', 'portal_ui_primitives.js',
  'portal_command_registry.js', 'portal_editor_open_service.js', 'portal_workbench_context.js', 'portal_workbench.js',
  'portal_projections.js', 'projection_host.js'];
const hash = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const hostPath = process.env.PROJECTION_HOST_SOURCE || path.join(staticRoot, 'projection_host.js');
const hostSource = fs.readFileSync(hostPath, 'utf8');
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const measureOnly = process.argv.includes('--measure');
let scenario, origin;
const reports = [];
function fixture(options = {}) {
  const files = {};
  for (const region of ['architecture', 'progress', 'evolution']) {
    const count = options.many && region === 'architecture' ? 8 : 1;
    files[region + '.html'] = '<!doctype html><html><head>' +
      `<link rel="stylesheet" href="./${region}.css"><style>body{--cascade:final}</style></head><body>` +
      '<input id="draft" value="preserved">' + Array.from({ length: count }, (_, i) =>
        `<script ${i === count - 1 ? 'type="module"' : ''} src="./${region}-${i}.js"></script>`).join('') + '</body></html>';
    files[region + '.css'] = 'body{--cascade:first}';
    for (let i = 0; i < count; i++) files[`${region}-${i}.js`] =
      `window.order=(window.order||[]);window.order.push(${i});` + (i === count - 1 ?
        `Projection.context().then(context=>parent.parent.postMessage({fixture:'loaded',region:${JSON.stringify(region)},order:window.order,visible:context.visible},'*'));` : '');
  }
  const build = { status: 'ready', artifact_hash: hash(JSON.stringify(files)), entries: {}, files: {} };
  for (const region of ['architecture', 'progress', 'evolution']) build.entries[region] = region + '.html';
  for (const [name, content] of Object.entries(files)) build.files[name] = { sha256: hash(content), size: Buffer.byteLength(content) };
  return { options, files, calls: [], pending: 0, peak: 0,
    app: { workbench_id: 'workbench_111111111111', revision: 56, published_revision: 56, title: 'Loading fixture', bindings: {},
      application_build: build, manifest: { schema: 'tfi.projection.v2', sidebar:
        ['architecture', 'progress', 'evolution'].map(id => ({ id, title: id, source: 'package:' + id })), context: [] } } };
}
const server = http.createServer(async (req, res) => {
  const s = scenario, url = new URL(req.url, 'http://fixture');
  const started = performance.now();
  const call = { path: url.pathname, query: url.search, method: req.method, started };
  s.calls.push(call);
  let body, type = 'text/plain', status = 200, delay = 0;
  if (url.pathname === '/') {
    type = 'text/html'; body = '<!doctype html><html><head><meta charset="utf-8"></head><body><main id="root"></main>' +
      modules.map(name => `<script src="/static/${name}"></script>`).join('') + '</body></html>';
  } else if (url.pathname.startsWith('/static/')) {
    const name = url.pathname.slice(8);
    if (![...modules, 'projection_sdk.js', 'portal_components.css', 'portal_design_system.css', 'portal_workbench.css'].includes(name)) { status = 404; body = 'unknown static'; }
    else { body = name === 'projection_host.js' ? hostSource : fs.readFileSync(path.join(staticRoot, name)); type = name.endsWith('.css') ? 'text/css' : 'text/javascript'; }
    if (name === 'projection_sdk.js' || name.endsWith('.css')) delay = s.options.delays ? 120 : 0;
  } else if (url.pathname === '/api/workbenches/detail') { body = JSON.stringify(s.app); type = 'application/json'; }
  else if (url.pathname === '/api/workbenches/facade') { body = JSON.stringify({ code: 'projection.resident_unbound', error: 'No chat in fixture' }); type = 'application/json'; status = 404; }
  else if (url.pathname.startsWith('/api/workbenches/assets/')) {
    const prefix = `/api/workbenches/assets/${s.app.workbench_id}/${s.app.revision}/${s.app.application_build.artifact_hash}/`;
    assert.ok(url.pathname.startsWith(prefix), 'exact revision/hash URL');
    const name = url.pathname.slice(prefix.length);
    body = s.files[name]; assert.notEqual(body, undefined, 'only indexed assets requested');
    type = name.endsWith('.html') ? 'text/html' : name.endsWith('.css') ? 'text/css' : 'text/javascript';
    delay = s.options.delays ? (name.endsWith('.html') ? 40 : 160) : 10;
    if (name.startsWith(s.options.slow || 'NO-SLOW')) delay = 550;
    if (name === s.options.deny) { status = 403; body = 'Denied fixture asset'; }
    s.pending++; s.peak = Math.max(s.peak, s.pending);
    call.asset = true;
  } else { body = 'Unexpected request'; status = 404; call.unexpected = true; }
  await sleep(delay);
  call.finished = performance.now(); call.status = status; call.bytes = Buffer.byteLength(body || '');
  if (call.asset) s.pending--;
  res.writeHead(status, { 'Content-Type': type, 'Cache-Control': 'no-store' }); res.end(body);
});
async function pageFor(browser, options = {}, name) {
  scenario = fixture(options);
  const context = await browser.newContext();
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', route => route.request().url().startsWith(origin + '/') ? route.continue() : route.abort());
  await page.goto(origin);
  await page.evaluate(() => {
    window.PortalIdentity = { username: () => 'host-loading-fixture' };
    window.loaded = []; window.panelReady = {}; window.createdFrames = 0;
    addEventListener('message', event => { if (event.data?.fixture === 'loaded') loaded.push({ ...event.data, at: performance.now() }); });
    new MutationObserver(records => { for (const r of records) for (const node of r.addedNodes) if (node.nodeName === 'IFRAME') createdFrames++; }).observe(document.getElementById('root'), { childList: true, subtree: true });
    window.start = (options = {}) => {
      window.started = performance.now(); window.settled = false;
      window.abort = new AbortController();
      window.openPromise = PortalProjectionHost.open(document.getElementById('root'), 'workbench_111111111111', {
        signal: abort.signal, ...options, onMounted: controller => { window.controller = controller; window.mountedAt = performance.now(); for (const id of ['architecture', 'progress', 'evolution']) controller.workbench.getPanel(id)?.ready.then(() => { panelReady[id] = performance.now(); }, () => {}); },
      }).then(() => { window.openedAt = performance.now(); window.settled = true; }, error => {
        window.openError = { code: error.code, message: error.message }; window.settled = true;
      });
    };
  });
  const s = scenario;
  return { page, s, async close() {
    await page.evaluate(() => { window.abort?.abort(); window.controller?.destroy(); });
    await page.waitForTimeout(30);
    assert.deepEqual(errors, [], 'no unhandled errors');
    reports.push({ name, options, peakAssetRequests: s.peak, calls: s.calls });
    await context.close();
    // Complete old server requests before the next sequential case.
    while (s.pending) await sleep(10);
  } };
}
async function timing(browser) {
  const h = await pageFor(browser, { delays: true, slow: 'evolution' }, 'cold-open');
  try {
    await h.page.evaluate(() => start());
    await h.page.waitForFunction(() => settled);
    assert.equal(await h.page.evaluate(() => window.openError), undefined);
    await h.page.waitForFunction(() => loaded.length === 3);
    const marks = await h.page.evaluate(() => ({ openedMs: openedAt - started, shellMs: mountedAt - started, readyMs: Object.fromEntries(Object.entries(panelReady).map(([k,v])=>[k,v-started])), loaded: loaded.map(x => ({ ...x, at: x.at - started })) }));
    h.s.marks = marks;
    reports.push({ name: 'timings', ...marks });
    const calls = h.s.calls;
    const a = suffix => calls.find(x => x.path.endsWith(suffix));
    const comparison = { scriptCssOverlap: a('architecture.css').started < a('architecture-0.js').finished,
      entrySharedOverlap: a('architecture.html').started < a('projection_sdk.js').finished,
      opensBeforeHiddenReady: marks.openedMs < marks.readyMs.evolution };
    reports.push({ name: 'overlap', ...comparison });
    if (!measureOnly) assert.deepEqual(comparison, { scriptCssOverlap: true, entrySharedOverlap: true, opensBeforeHiddenReady: true });
  } finally { await h.close(); }
}
async function contracts(browser) {
  let h;
  if (!process.argv.includes('--lifecycle-only')) {
  h = await pageFor(browser, { many: true }, 'order-csp-visibility');
  try {
    await h.page.evaluate(() => start());
    await h.page.waitForFunction(() => loaded.length === 3 && settled, null, { timeout: 5000 });
    const result = await h.page.evaluate(async () => {
      const runtime = controller.workbench.getPanel('architecture');
      const outer = runtime.root.querySelector('iframe'); const inner = outer.contentDocument.querySelector('iframe');
      const before = inner.contentWindow;
      controller.workbench.focusPanel('progress'); controller.workbench.focusPanel('architecture');
      controller.setVisible(false); controller.setVisible(true);
      return { order: loaded.find(x => x.region === 'architecture').order,
        otherVisible: loaded.filter(x => x.region !== 'architecture').map(x => x.visible),
        sandbox: inner.getAttribute('sandbox'), srcdoc: inner.srcdoc,
        preserved: before === outer.contentDocument.querySelector('iframe').contentWindow };
    });
    assert.deepEqual(result.order, [0,1,2,3,4,5,6,7]);
    assert.deepEqual(result.otherVisible, [false, false]);
    assert.equal(result.sandbox, 'allow-scripts allow-forms'); assert.equal(result.preserved, true);
    assert.match(result.srcdoc, /connect-src 'none'/); assert.match(result.srcdoc, /worker-src blob:/);
    const arch = h.s.calls.filter(x => x.asset && /architecture-(\d+)\.js|architecture\.css/.test(x.path));
    for (const c of arch) assert.ok(arch.filter(x => x.started <= c.started && x.finished > c.started).length <= 4, 'four document asset reads maximum');
    const frame = h.page.frames().find(f => f.parentFrame()?.parentFrame());
    assert.ok(frame, 'opaque sandbox child');
    const isolation = await frame.evaluate(async () => {
      const ctx = await Projection.context();
      let blocked = false; try { await fetch('https://forbidden.invalid/'); } catch { blocked = true; }
      return { blocked, context: ctx.workbench_id, cascade: getComputedStyle(document.body).getPropertyValue('--cascade').trim() };
    });
    assert.deepEqual(isolation, { blocked: true, context: 'workbench_111111111111', cascade: 'final' });
    h.s.options.slow = 'architecture';
    await h.page.evaluate(() => { controller.workbench.focusPanel('progress'); controller.destroy(); start(); });
    await h.page.waitForFunction(() => settled);
    assert.equal(await h.page.evaluate(() => controller.workbench.getPanel('progress').isVisible()), true, 'saved active tab restored');
    assert.equal(await h.page.evaluate(() => controller.workbench.getPanel('architecture').root.querySelector('iframe')), null, 'restored selection does not wait for default background tab');
  } finally { await h.close(); }
  }
  for (const selected of (process.argv.includes('--lifecycle-only') ? [true] : [false, true])) {
    h = await pageFor(browser, { deny: (selected ? 'architecture' : 'evolution') + '-0.js', slow: 'progress' }, selected ? 'selected-denied' : 'background-denied');
    try {
      await h.page.evaluate(() => start()); await h.page.waitForFunction(() => settled);
      if (selected) {
        assert.equal(await h.page.evaluate(() => openError.code), 'projection.asset');
        await h.page.waitForTimeout(1700);
        assert.equal(await h.page.locator('#root iframe').count(), 0, 'late assets cannot mount after destruction');
      } else {
        await h.page.waitForFunction(() => controller.workbench.getPanel('evolution').root.querySelector('[role=alert]'));
        assert.equal(await h.page.evaluate(() => window.openError), undefined);
        assert.ok(await h.page.locator('#root iframe').count());
      }
    } finally { await h.close(); }
  }
  h = await pageFor(browser, { delays: true }, 'abort-loading');
  try {
    await h.page.evaluate(() => start()); await h.page.waitForFunction(() => window.controller);
    await h.page.evaluate(() => abort.abort()); await h.page.waitForTimeout(800);
    assert.equal(await h.page.evaluate(() => createdFrames), 0, 'no late injection after abort');
    assert.equal(await h.page.locator('#root iframe').count(), 0);
  } finally { await h.close(); }
  h = await pageFor(browser, {}, 'legacy-v1');
  h.s.app = { workbench_id: 'workbench_111111111111', revision: 1, title: 'v1 fixture', bindings: {},
    html: '<main>legacy</main><script>Projection.context().then(c=>parent.postMessage({fixture:"loaded",region:"legacy",visible:c.visible},"*"))</script>' };
  try {
    await h.page.evaluate(() => start());
    await h.page.waitForFunction(() => settled && loaded.length === 1);
    assert.equal(await h.page.evaluate(() => window.openError), undefined);
    assert.equal(await h.page.evaluate(() => loaded[0].visible), true);
    assert.equal(await h.page.locator('#root iframe').getAttribute('sandbox'), 'allow-scripts allow-forms');
    assert.equal(h.s.calls.some(call => call.asset), false, 'v1 does not require package assets');
  } finally { await h.close(); }
}
(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  origin = 'http://127.0.0.1:' + server.address().port;
  const browser = await chromium.launch({ headless: true });
  let passed = false;
  try { if (!process.argv.includes('--contracts-only') && !process.argv.includes('--lifecycle-only')) await timing(browser); if (!measureOnly) await contracts(browser); passed = true; }
  finally {
    await browser.close(); await new Promise(resolve => server.close(resolve));
    const report = { passed, hostSource: hostPath, hostSha256: hash(hostSource), singleWorker: true, boundary: 'loopback fixture; production host/SDK/Workbench; no live application execution', reports };
    if (process.env.PROJECTION_LOADING_REPORT) fs.writeFileSync(process.env.PROJECTION_LOADING_REPORT, JSON.stringify(report, null, 2) + '\n');
    console.log(JSON.stringify(report.reports.filter(x => x.name === 'timings' || x.name === 'overlap')));
  }
})().catch(error => { console.error(error.stack); process.exitCode = 1; });
