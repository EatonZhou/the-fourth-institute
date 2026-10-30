'use strict';
// The real HATG runtime mounted in jsdom: the panel template, its module parts
// and the scripts the loader runs before them. Requests read the files from
// disk; every other server read answers an empty object. jsdom has no layout,
// so ResizeObserver delivery and animation frames are driven by the case.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const { JSDOM, VirtualConsole } = require('jsdom');
const { STATIC, hatgSources, startHatgRuntime } = require('./hatg_modules.cjs');
const { portalWorkbenchScripts } = require('./portal_workbench_scripts.cjs');

const PORTAL = path.dirname(STATIC);
// Page scripts the runtime uses, in lineage.html's order.
const PAGE_SCRIPTS = [
  'portal_state.js', 'portal_text.js', 'portal_translations.js', 'portal_i18n.js', 'portal_page_state.js',
  'portal_api.js', 'portal_instruction_client.js', 'portal_identity.js', 'portal_society_service.js',
  'portal_resident_discovery_service.js', 'portal_overlay.js', 'portal_ui_primitives.js', 'portal_ui.js',
  'portal_workbench_context.js', ...portalWorkbenchScripts(STATIC), 'lineage_workbench_controller.js',
];
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

async function mountPanel() {
  const errors = [], observers = [], frames = [];
  const virtualConsole = new VirtualConsole();
  virtualConsole.on('jsdomError', error => errors.push(String(error.message || error)));
  const dom = new JSDOM('<!doctype html><html><body><div id="hatgRoot"></div></body></html>', {
    url: 'https://hatg-mount.fixture.invalid/lineage.html', runScripts: 'outside-only', pretendToBeVisual: true, virtualConsole,
  });
  const { window } = dom;
  let closed = false;
  // A closed page runs no more microtasks; jsdom cannot report their errors.
  window.queueMicrotask = callback => queueMicrotask(() => {
    if (closed) return;
    try { callback(); } catch (error) { errors.push(String(error?.stack || error)); }
  });
  window.ResizeObserver = class {
    constructor(callback) { this.callback = callback; this.targets = []; this.disconnected = false; observers.push(this); }
    observe(target) { this.targets.push(target); }
    unobserve() {}
    disconnect() { this.disconnected = true; }
  };
  window.requestAnimationFrame = callback => frames.push(callback);
  window.cancelAnimationFrame = id => { if (id) frames[id - 1] = null; };
  window.CSS = { escape: value => String(value) };
  window.matchMedia = query => Object.assign(new window.EventTarget(), { media: query, matches: false });
  window.fetch = async input => {
    const { pathname } = new URL(String(input), window.location.href);
    const file = path.join(PORTAL, pathname);
    if (pathname !== '/' && file.startsWith(PORTAL + path.sep) && fs.existsSync(file)) {
      return new Response(fs.readFileSync(file, 'utf8'), { status: 200 });
    }
    return new Response('{}', { status: 200, headers: { 'content-type': 'application/json' } });
  };
  const sources = hatgSources();
  const loader = fs.readFileSync(path.join(STATIC, 'portal_hatg_loader.js'), 'utf8');
  const assets = [...loader.matchAll(/"\/static\/([^"]+\.js)"/g)].map(match => match[1])
    .filter(file => !Object.hasOwn(sources, file));
  for (const file of [...PAGE_SCRIPTS, ...assets]) {
    window.eval(fs.readFileSync(path.join(STATIC, file), 'utf8') + `\n//# sourceURL=${file}`);
  }
  // The template's stylesheet has no effect without layout.
  window.PortalUIPrimitives = Object.freeze({ ...window.PortalUIPrimitives, loadAsset: async () => {} });
  const { runtime } = startHatgRuntime(dom.getInternalVMContext(), sources);
  const panel = runtime.mount({
    root: '#hatgRoot', pageState: { read: () => ({}), write() {}, writeSoon() {} },
    waitForServiceReady: async () => {}, onLocationChange() {}, onDeleteLineage() {},
  });
  const $id = id => window.document.getElementById(id);
  await panel.ready;
  assert.ok($id('graphNavigationBar'), `HATG template mounted: ${$id('hatgRoot').dataset.hatgError || ''}`);
  return {
    panel, errors, $id,
    observing: element => observers.filter(observer => observer.targets.includes(element)),
    runFrames: () => frames.splice(0).forEach(frame => frame?.()),
    close: async () => { await pause(20); closed = true; window.close(); },
  };
}

test('the mounted panel reserves the measured toolbar height and stops measuring on destroy', async () => {
  const page = await mountPanel();
  const toolbar = page.$id('graphNavigationBar'), viewport = page.$id('hatgViewport');
  const [measuring, ...others] = page.observing(toolbar);
  assert.ok(measuring, 'mounting starts measuring the canvas toolbar');
  assert.equal(others.length, 0);
  const deliver = height => measuring.callback([{ target: toolbar, borderBoxSize: [{ blockSize: height }], contentRect: { height } }]);
  deliver(77);
  assert.equal(viewport.style.getPropertyValue('--hatg-navigation-height'), '', 'the height is written after delivery');
  page.runFrames();
  assert.equal(viewport.style.getPropertyValue('--hatg-navigation-height'), '77px');
  deliver(99);
  page.panel.destroy();
  page.runFrames();
  assert.equal(measuring.disconnected, true, 'destroy stops measuring');
  assert.equal(viewport.style.getPropertyValue('--hatg-navigation-height'), '77px', 'destroy drops an unapplied height');
  await page.close();
  assert.deepEqual(page.errors, []);
});
