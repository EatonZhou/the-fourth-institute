const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || '../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const {portalWorkbenchScripts} = require('./portal_workbench_scripts.cjs');
const portal = path.resolve(__dirname, '../../../management_portal');
const parentId = 'lineage_aaaaaaaaaaaa', childId = 'lineage_bbbbbbbbbbbb';
const facadeId = 'resident_bbbbbbbbbbbb', leaderId = 'resident_aaaaaaaaaaaa';
const parent = {lineage_id: parentId, title: 'Project overview', revision: 2, leader_resident_id: leaderId,
  nodes: [{id: leaderId, title: 'Project leader', x: 20, y: 35},
    {id: facadeId, title: 'Research Family', x: 20, y: 55, hierarchy_reference: true, accessible: true, home_lineage_id: childId}],
  program: {components: [], wires: []}, function_blocks: [], edges: []};
const child = {...parent, lineage_id: childId, title: 'Research Family', revision: 36, leader_resident_id: facadeId,
  nodes: [{id: facadeId, title: 'Research Family', x: 20, y: 35}],
  function_blocks: [{id: 'fbi_aaaaaaaaaaaa', title: 'Research Family', elements: [],
    family: {facade: facadeId, steward: '', router: '', members: []}}]};

async function run({route: extraRoute, beforeReady, afterReady} = {}) {
  const browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1500, height: 1000}});
    page.setDefaultTimeout(5000);
    const errors = [], reads = [];
    page.on('pageerror', error => {errors.push(error.message); console.error('pageerror:', error.stack);});
    await page.route('**/*', async route => {
      const req = route.request(), url = new URL(req.url());
      if (extraRoute && await extraRoute(route, url)) return;
      const body = req.postData() ? req.postDataJSON() : null;
      if (body?.instruction) {
        assert.match(body.instruction, /^(Get|List|Search)/, 'fixture never changes business state');
        reads.push(body.instruction);
        const result = body.instruction === 'GetLineage' ? {lineage: body.arguments.lineage_id === childId ? child : parent}
          : body.instruction === 'GetResident' ? {resident: {resident_id: facadeId, lineage_id: childId, family: {role: 'facade', family_id: 'fbi_aaaaaaaaaaaa'}}}
          : body.instruction === 'ListLineages' ? {lineages: [parent, child]} : {};
        return route.fulfill({contentType: 'application/json', body: JSON.stringify({status: 'committed', result})});
      }
      if (url.pathname === '/fixture') return route.fulfill({contentType: 'text/html; charset=utf-8',
        body: fs.readFileSync(path.join(portal, 'lineage.html'), 'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '')});
      const file = path.join(portal, url.pathname);
      if (fs.existsSync(file) && fs.statSync(file).isFile()) return route.fulfill({path: file});
      return route.fulfill({contentType: 'application/json', body: '{}'});
    });
    await page.goto('http://fixture.local/fixture');
    await page.addScriptTag({content: fs.readFileSync(path.join(portal, 'static/portal_site.js'), 'utf8')
      + '({site_name:"HATG test",storage_namespace:"hatg-test",paths:{}});'});
    const groups = JSON.parse(fs.readFileSync(path.join(portal, 'script_groups.json'), 'utf8'));
    const groupNames = name => groups[name].map(file => path.basename(file, '.js'));
    const scripts = [...groupNames('portal-foundation'), 'portal_page_state', 'portal_api',
      'portal_instruction_client', 'portal_resident_service', 'portal_identity', 'portal_society_service', 'portal_resident_discovery_service', 'portal_lineage_catalogs',
      // Site navigation owns unrelated resource routes; this fixture mounts the panel only.
      ...groupNames('portal-shell').filter(name => name !== 'portal_nav'), 'portal_workbench_context',
      ...portalWorkbenchScripts(path.join(portal, 'static')).map(file => file.replace(/\.js$/, '')),
      'lineage_workbench_controller', 'portal_resource_header'];
    for (const name of scripts) await page.addScriptTag({path: path.join(portal, 'static', name + '.js')});
    for (const [, file] of fs.readFileSync(path.join(portal, 'static/portal_hatg_loader.js'), 'utf8').matchAll(/"(\/static\/[^" ]+\.js)"/g)) {
      await page.addScriptTag({path: path.join(portal, file)});
    }
    await page.evaluate(async lineageId => {
      const root = document.createElement('section'); root.style.cssText = 'height:900px;width:1100px'; document.body.replaceChildren(root);
      window.opened = [];
      window.panel = PortalHatgPanel.mountHatgPanel({root, lineageId,
        pageState: {read: () => ({}), write() {}, writeSoon() {}}, waitForServiceReady: async () => {},
        onLocationChange() {}, onDeleteLineage() {},
        emit(type, payload) {if (type === 'open-resource') window.opened.push(payload.resource);},
      });
    }, parentId);
    if (beforeReady) await beforeReady(page);
    await page.evaluate(() => panel.ready);
    if (afterReady) await afterReady(page);
    assert.equal(await page.locator('#lineageLoadError').innerText(), '');
    const card = page.locator(`.hatgNode[data-node-id="${facadeId}"]`);
    await card.locator('.nodeTitle').waitFor({state: 'attached'});
    await card.locator('.familyFacadeHeading').waitFor({state: 'attached'});
    assert.match(await card.innerText(), /FAMILY/);
    assert.match(await card.getAttribute('class'), /family/);
    assert.equal(await page.locator('.hatgNode').count(), 2, 'no child members imported into parent');
    await card.locator('.nodeTitle').click();
    await page.waitForFunction(() => window.opened.length === 1);
    const selected = await page.evaluate(() => opened[0]);
    assert.equal(selected.lineage_id, childId);
    assert.equal(selected.resident_id, facadeId);
    await page.evaluate(async target => panel.setTarget(target), {lineageId: childId, residentId: facadeId});
    await card.locator('.familyFacadeHeading').waitFor({state: 'attached'});
    assert.match(await card.innerText(), /FAMILY/, 'canonical view agrees with overview identity');
    await page.evaluate(async lineageId => {window.opened = []; await panel.setTarget({lineageId});}, parentId);
    const disclosure = page.locator(`.hatgNodeDisclosureGroup[data-action-node-id="${facadeId}"] button`).first();
    assert.equal(await page.locator(`.hatgNodeDisclosureGroup[data-action-node-id="${facadeId}"] .hatgNodeDisclosureAction`).count(), 0);
    await disclosure.waitFor({state: 'attached'});
    await disclosure.click();
    await page.waitForFunction(() => window.opened.length === 1);
    assert.equal((await page.evaluate(() => opened[0])).lineage_id, childId);
    assert.deepEqual(errors, []);
    assert.equal(await page.locator('[data-portal-toast-stack]').innerText().catch(() => ''), '');
    if (process.env.HATG_EVIDENCE_DIR) {
      fs.mkdirSync(process.env.HATG_EVIDENCE_DIR, {recursive: true});
      await page.screenshot({path: path.join(process.env.HATG_EVIDENCE_DIR, 'overview.png')});
    }
    console.log(JSON.stringify({initialFamily: true, canonicalFamily: true, cardAndDisclosureOpenHome: true, reads, errors}));
  } finally {await browser.close();}
}
module.exports = {run, portal};
if (require.main === module) {
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  let referenceStarted = false;
  let initialDrawMs;
  const start = Date.now();
  run({
    async route(route) {
      const body = route.request().postData() ? route.request().postDataJSON() : null;
      if (body?.instruction === 'GetResident' && body.arguments.resident_id === facadeId) {
        referenceStarted = true;
        await gate;
      }
      return false;
    },
    async beforeReady(page) {
      try {
        const card = page.locator(`.hatgNode[data-node-id="${facadeId}"]`);
        await card.locator('.nodeTitle').waitFor({state: 'attached'});
        assert.equal(referenceStarted, true, 'the reference lookup is still held');
        assert.match(await card.innerText(), /跨 Lineage 引用/);
        assert.equal(await card.locator('.familyFacadeHeading').count(), 0);
        await page.evaluate(() => {
          window.PortalNav = {openResidentMenu: options => { window.requestedMenu = options; }};
        });
        await card.dispatchEvent('dblclick');
        assert.deepEqual(await page.evaluate(() => requestedMenu), {residentId: facadeId},
          'settings resolve the canonical Resident while its reference type is unknown');
        initialDrawMs = Date.now() - start;
      } finally {
        release();
      }
    },
  }).then(() => console.log(JSON.stringify({baseGraphDrawnBeforeReferenceResponse: true, fixtureElapsedMs: initialDrawMs})))
    .catch(error => {console.error(error); process.exitCode = 1;});
}
