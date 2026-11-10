const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const {chromium} = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');

const root = path.resolve(__dirname, '../../../management_portal');
const groups = JSON.parse(fs.readFileSync(path.join(root, 'script_groups.json'), 'utf8'));
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8')
  .replace('<script src="/static/home.js"></script>',
    fs.readFileSync(path.join(root, 'notice_board_home.html'), 'utf8') + '<script src="/static/home.js"></script>')
  .replace(/<script data-portal-script-group="([^"]+)"><\/script>/g,
    (_, id) => groups[id].map(src => `<script src="${src}"></script>`).join(''));
const evidence = process.env.HOME_ALIGNED_EVIDENCE;
if (evidence) fs.mkdirSync(evidence, {recursive: true});
const calls = [];
const server = http.createServer(async (request, response) => {
  const url = new URL(request.url, 'http://localhost');
  if (url.pathname === '/') {
    response.setHeader('Content-Type', 'text/html');
    return response.end(html);
  }
  if (url.pathname === '/static/portal_resource_routes.js') {
    response.setHeader('Content-Type', 'text/javascript');
    return response.end('window.PortalResourceRoutes={schema:"tfi.resource-portal-routes.v1",routes:[],text_resource_types:[],identity_parameters:[]};');
  }
  if (url.pathname.startsWith('/api/')) {
    let body = '';
    for await (const chunk of request) body += chunk;
    const payload = body ? JSON.parse(body) : {};
    calls.push({path: url.pathname, instruction: payload.instruction});
    response.setHeader('Content-Type', 'application/json');
    if (url.pathname === '/api/daily-news') return response.end(JSON.stringify({status: 'missing'}));
    const data = url.pathname === '/api/me'
      ? {username: 'aligned-fixture', principal: {kind: 'user', id: 'aligned-fixture'}, access: {allows: {}}}
      : {items: [], posts: [], accounts: [], result: {}, schema: 'tfi.hatg-instruction-result.v2', status: 'committed'};
    return response.end(JSON.stringify(data));
  }
  const file = path.resolve(root, '.' + url.pathname);
  if (!file.startsWith(root + '/static/') || !fs.existsSync(file)) {
    response.statusCode = 404;
    return response.end();
  }
  response.setHeader('Content-Type', file.endsWith('.css') ? 'text/css' : 'text/javascript');
  response.end(fs.readFileSync(file));
});

(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({headless: true});
  const results = [];
  try {
    for (const viewport of [{width: 1440, height: 1000}, {width: 390, height: 844}]) {
      const page = await browser.newPage({viewport});
      page.setDefaultTimeout(5000);
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.goto(`http://127.0.0.1:${server.address().port}/`);
      await page.waitForFunction(() => window.PortalWorkbench?.get('home-get-me-aligned'));
      await page.waitForFunction(() => window.PortalIdentity?.username?.() === 'aligned-fixture');
      await page.evaluate(() => {
        const {stack} = PortalWorkbench.model;
        PortalWorkbench.get('home').applyWorkspace({
          name: 'Aligned fixture', layout: stack(['get-me-aligned']),
          hidden: ['dialogue-display', 'my-pulse-publish', 'agent-status'],
        });
        PortalWorkbench.get('home').focusPanel('get-me-aligned');
        PortalWorkbench.get('home-get-me-aligned').reset();
        PortalWorkbench.get('home-get-me-aligned').focusPanel('daily-news');
      });
      const aligned = page.locator('#getMeAlignedWorkbench');
      await aligned.locator('[data-home-component="daily-news"]').waitFor({state: 'visible'});
      assert.equal(await page.locator('[data-agent-access], [data-home-component="agent-access"]').count(), 0);
      assert.equal(await page.locator('script[src*="home_agent_access"]').count(), 0);
      const state = await page.evaluate(() => PortalWorkbench.get('home-get-me-aligned').getState());
      assert.deepEqual([...state.panelIds].sort(), ['daily-news', 'notice-board']);
      assert.equal(JSON.stringify(state.layout).includes('agent-access'), false);
      if (viewport.width > 600) {
        const daily = await aligned.locator('[data-home-component="daily-news"]').boundingBox();
        const notice = await aligned.locator('[data-home-component="notice-board"]').boundingBox();
        assert.ok(daily && notice);
        assert.ok(Math.abs(daily.y - notice.y) < 2);
        assert.ok(Math.abs(daily.height - notice.height) < 2);
      }
      await page.evaluate(() => {
        const {split, stack} = PortalWorkbench.model;
        PortalWorkbench.get('home-get-me-aligned').applyWorkspace({
          name: 'Legacy saved layout', hidden: [],
          layout: split('vertical', [
            split('horizontal', [stack(['notice-board']), stack(['daily-news'])], [1, 1]),
            stack(['agent-access']),
          ], [3, 2]),
        });
      });
      const restored = await page.evaluate(() => PortalWorkbench.get('home-get-me-aligned').getState().layout);
      assert.equal(restored.orientation, 'horizontal', 'obsolete-only split must collapse');
      assert.equal(JSON.stringify(restored).includes('agent-access'), false);
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      if (evidence) await page.screenshot({path: path.join(evidence, `aligned-${viewport.width}.png`)});
      assert.deepEqual(errors, []);
      results.push({viewport, panels: state.panelIds, restoredLayout: restored});
      await page.close();
    }
    assert.equal(calls.some(call => /agent-access|summaries\/batch/.test(call.path)), false);
    assert.equal(calls.some(call => call.instruction === 'DeliverMessage'), false);
    console.log(JSON.stringify(results));
  } finally {
    await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
