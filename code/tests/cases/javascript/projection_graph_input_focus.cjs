'use strict';
// Real graph, host, SDK and Workbench in one Chromium. The adjacent composer
// is a plain textarea; all HTTP stays in this local fixture (no business APIs).
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const crypto = require('node:crypto');
const repo = path.resolve(__dirname, '../../..');
const appRoot = path.join(repo, 'management_portal/workbench_projections/development_app');
const staticRoot = path.join(repo, 'management_portal/static');
const { build } = require(path.join(appRoot, 'node_modules/esbuild'));
const { chromium } = require(path.join(repo, 'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const modules = ['portal_text.js', 'portal_state.js', 'portal_i18n.js', 'portal_ui.js', 'portal_ui_primitives.js',
  'portal_command_registry.js', 'portal_editor_open_service.js', 'portal_workbench_context.js',
  'portal_workbench.js', 'portal_projections.js', 'projection_host.js'];
const hash = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const object = id => ({ id, kind: 'module', title: id, responsibility: 'Focus fixture', child_ids: [], mechanism_ids: [], host_ids: [] });
const content = { root: { ...object('scope'), child_ids: ['a', 'b'] }, children: [object('a'), object('b')],
  internalRelations: [{ id: 'ab', from: 'a', to: 'b', label: 'request', kind: 'calls' }], boundaryRelations: [] };
const evidence = { checks: [], requests: [], errors: [], singleWorker: true,
  boundary: 'production graph/host/SDK/Workbench; local synthetic content and adjacent textarea; no live Chat or business requests' };
function check(name, actual, expected) {
  evidence.checks.push({ name, actual, expected });
  assert.deepEqual(actual, expected, name);
}
(async () => {
  const bundle = await build({ stdin: { resolveDir: appRoot, loader: 'js', contents:
    `import {createExplorationGraph} from './src/architecture/exploration-graph.js';
     window.mountGraph = initialState => {
       window.graphController?.destroy();
       window.graphController = createExplorationGraph(document.getElementById('graph'), {
         content: ${JSON.stringify(content)}, initialState, ports: {select: value => window.selected = value}
       });
     };` }, bundle: true, format: 'iife', write: false, outfile: '/fixture/graph.js' });
  const files = { 'architecture.html': '<!doctype html><html><head><link rel="stylesheet" href="graph.css"></head><body><div id="graph" style="width:600px;height:480px"></div><script src="graph.js"></script></body></html>',
    'graph.js': bundle.outputFiles.find(f => f.path.endsWith('.js')).text,
    'graph.css': bundle.outputFiles.find(f => f.path.endsWith('.css')).text };
  const app = { workbench_id: 'workbench_111111111111', revision: 1, published_revision: 1, title: 'Graph focus', bindings: {},
    manifest: { schema: 'tfi.projection.v2', sidebar: [{ id: 'architecture', title: 'Architecture', source: 'package:architecture' }], context: [] },
    application_build: { status: 'ready', artifact_hash: hash(JSON.stringify(files)), entries: { architecture: 'architecture.html' },
      files: Object.fromEntries(Object.entries(files).map(([name, bytes]) => [name, { sha256: hash(bytes), size: Buffer.byteLength(bytes) }])) } };
  evidence.graphSha256 = hash(fs.readFileSync(path.join(appRoot, 'src/architecture/exploration-graph.js')));
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://fixture'); evidence.requests.push({ method: req.method, path: url.pathname });
    let bytes, type = 'text/javascript';
    if (url.pathname === '/') {
      type = 'text/html'; bytes = '<!doctype html><html><body><div style="display:flex"><main id="root" style="width:740px;height:650px"></main><textarea id="composer" aria-label="Chat input"></textarea></div>' + modules.map(name => `<script src="/static/${name}"></script>`).join('') + '</body></html>';
    } else if (url.pathname.startsWith('/static/')) {
      const name = url.pathname.slice(8);
      if ([...modules, 'projection_sdk.js', 'portal_components.css', 'portal_design_system.css', 'portal_workbench.css'].includes(name)) {
        bytes = fs.readFileSync(path.join(staticRoot, name)); if (name.endsWith('.css')) type = 'text/css';
      }
    } else {
      const prefix = `/api/workbenches/assets/${app.workbench_id}/1/${app.application_build.artifact_hash}/`;
      if (url.pathname.startsWith(prefix)) { const name = url.pathname.slice(prefix.length); bytes = files[name]; type = name.endsWith('.html') ? 'text/html' : name.endsWith('.css') ? 'text/css' : type; }
    }
    res.writeHead(bytes === undefined ? 404 : 200, { 'Content-Type': type }); res.end(bytes ?? 'unexpected request');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = 'http://127.0.0.1:' + server.address().port;
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1400, height: 850 } });
    page.on('pageerror', e => evidence.errors.push(e.message));
    await page.route('**/*', route => route.request().url().startsWith(origin + '/') ? route.continue() : route.abort());
    await page.goto(origin);
    await page.evaluate(async app => {
      window.PortalIdentity = { username: () => 'graph-focus-fixture' };
      window.controller = await PortalProjectionHost.mount(document.getElementById('root'), app);
      window.blurs = 0; document.getElementById('composer').addEventListener('blur', () => window.blurs++);
    }, app);
    const frame = page.frames().find(f => f.parentFrame()?.parentFrame());
    await frame.waitForFunction(() => typeof mountGraph === 'function');
    const compose = async () => {
      await page.locator('#composer').fill('正在输入 draft');
      await page.locator('#composer').click();
      await page.evaluate(() => { const e = document.getElementById('composer'); e.setSelectionRange(2, 4); window.blurs = 0; });
    };
    const inputState = () => page.evaluate(() => { const e = document.getElementById('composer'); return { focused: document.activeElement === e, blurs, value: e.value, selection: [e.selectionStart, e.selectionEnd] }; });
    const expected = { focused: true, blurs: 0, value: '正在输入 draft', selection: [2, 4] };
    const settle = () => frame.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    await compose(); await frame.evaluate(() => mountGraph(null)); await settle();
    check('initial graph load keeps composer focus and selection', await inputState(), expected);
    await frame.evaluate(() => mountGraph({ rootId: 'scope', focused: { objectId: 'a' } })); await settle();
    check('saved graph state cannot claim keyboard focus', await inputState(), expected);
    await frame.locator('[data-object-id="a"]').click();
    await frame.evaluate(() => { document.getElementById('graph').style.width = '520px'; }); await settle();
    check('resize preserves actual graph keyboard focus', await frame.evaluate(() => document.activeElement.dataset.objectId), 'a');
    await page.keyboard.press('Enter');
    check('graph keyboard selection still works', await frame.evaluate(() => window.selected), { objectId: 'a' });
    await compose();
    await frame.evaluate(() => { document.getElementById('graph').style.width = '560px'; }); await settle();
    check('resize after switching to composer does not steal focus', await inputState(), expected);
    await frame.evaluate(() => graphController.restoreViewState(graphController.getViewState())); await settle();
    check('state restore while typing does not steal focus', await inputState(), expected);
    await frame.evaluate(() => { graphController.hide(); document.getElementById('graph').style.width = '580px'; graphController.show(); }); await settle();
    check('hidden graph recovery does not steal focus', await inputState(), expected);
    await page.keyboard.type('X');
    check('typing continues in the same selection', await page.locator('#composer').inputValue(), '正在X draft');
    await compose();
    await frame.evaluate(() => graphController.restoreViewState({ rootId: 'scope', focused: { objectId: 'missing' } })); await settle();
    check('invalid saved focus cannot match an unrelated node', await inputState(), expected);
    check('no browser errors', evidence.errors, []);
    await page.evaluate(() => controller.destroy());
    evidence.passed = true;
  } finally {
    await browser.close(); await new Promise(resolve => server.close(resolve));
    if (process.env.PROJECTION_FOCUS_REPORT) fs.writeFileSync(process.env.PROJECTION_FOCUS_REPORT, JSON.stringify(evidence, null, 2) + '\n');
    console.log(JSON.stringify(evidence));
  }
})().catch(error => { console.error(error.stack); process.exitCode = 1; });
