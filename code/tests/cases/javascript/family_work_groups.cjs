const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const { babelParse } = require('./babel_parse.cjs');

const sourcePath = path.resolve(__dirname, '../../../management_portal/static/portal_resource_chips.js');
const MEMBER = 'resident_111111111111';
const CURRENT_LINEAGE = 'lineage_bbbbbbbbbbbb';
const staleHref = `/lineage?resident=${MEMBER}&lineage=lineage_aaaaaaaaaaaa&open_chat=1&message=msg_original#output`;

// Load the complete production module, including its private URL merger,
// reference loader and real bindResourceLinks click handler. The Python entry
// supplies the production-generated route script, not a copied route table.
// Only the anchor interface, API response and navigation effect are fixtures.
// Actual worker-link discovery, mouse navigation and CSS stay in the retained
// "Family progress links to the actual worker" browser case.
async function destination(routeScript, canonicalUrl, source = fs.readFileSync(sourcePath, 'utf8')) {
  const requests = [], navigations = [], notices = [], listeners = [];
  let resolveReference;
  const reference = new Promise(resolve => { resolveReference = resolve; });
  const attributes = new Map([['href', `/lineage?resident=${MEMBER}&open_chat=1`]]);
  const link = {
    dataset: {},
    matches(selector) {
      assert.equal(selector, 'a[href]:not(.portalResourceChip)');
      return true;
    },
    querySelectorAll(selector) {
      assert.equal(selector, 'a[href]:not(.portalResourceChip)');
      return [];
    },
    getAttribute: key => attributes.get(key) ?? null,
    setAttribute: (key, value) => attributes.set(key, String(value)),
    removeAttribute: key => attributes.delete(key),
    addEventListener(type, listener) {
      // The Preview file menu (contextmenu) is not part of click navigation.
      if (type === 'contextmenu') return;
      assert.equal(type, 'click');
      listeners.push(listener);
    },
  };
  const window = {
    location: {
      href: 'http://portal.invalid/lineage', protocol: 'http:', origin: 'http://portal.invalid',
      assign: href => navigations.push(href),
    },
    clearTimeout,
    PortalUIPrimitives: {
      escapeHtml: () => { throw new Error('Unexpected presentation dependency'); },
      registerEnhancer: () => {},
    },
    PortalAPI: { get: url => { requests.push(url); return reference; } },
    PortalUI: { toast: (...args) => notices.push(args) },
    PortalI18n: {
      t: value => value,
      text: (parts, ...values) => parts.reduce((text, part, i) => text + part + (values[i] ?? ''), ''),
    },
  };
  const context = vm.createContext({ window, document: { readyState: 'complete' }, URL, URLSearchParams });
  const identitySource = fs.readFileSync(path.join(path.dirname(sourcePath), 'portal_state.js'), 'utf8');
  const identityModule = babelParse(identitySource, 'portal_state.js').program.body[0];
  vm.runInContext(identitySource.slice(identityModule.start, identityModule.end), context);
  vm.runInContext(routeScript, context, { filename: 'portal_resource_routes.js' });
  vm.runInContext(source, context, { filename: sourcePath });
  window.PortalResourceChips.bindResourceLinks(link);
  assert.equal(listeners.length, 1, 'production binding installs one click handler');

  // Like the old browser cases, change only the saved href after binding.
  link.setAttribute('href', staleHref);
  const event = {
    button: 0, metaKey: false, ctrlKey: false, shiftKey: false, altKey: false,
    prevented: false, stopped: false,
    preventDefault() { this.prevented = true; },
    stopPropagation() { this.stopped = true; },
  };
  const pending = listeners[0].call(link, event);
  assert.equal(event.prevented, true);
  assert.equal(event.stopped, true);
  assert.equal(link.getAttribute('aria-busy'), 'true');
  assert.deepEqual(requests, [`/api/resources/reference?address=${MEMBER}`]);
  assert.deepEqual(navigations, [], 'navigation waits for the resolved canonical reference');
  resolveReference({ resource_id: MEMBER, resource_type: 'resident', title: 'Actual worker', url: canonicalUrl });
  await pending;
  assert.deepEqual(notices, [], 'the production reference/navigation path succeeds');
  assert.equal(link.getAttribute('aria-busy'), null);
  assert.equal(navigations.length, 1, 'the production handler requests exactly one navigation');
  return navigations[0];
}

const checks = {
  // Proposed stable case test_d6a445772594: all six old URL assertions.
  async 'same-resource'(routeScript, source) {
    const actual = await destination(routeScript, `/lineage?resident=${MEMBER}&lineage=${CURRENT_LINEAGE}`, source);
    const url = new URL(actual, 'http://portal.invalid');
    assert.equal(url.pathname, '/lineage', 'destination page');
    assert.deepEqual(url.searchParams.getAll('resident'), [MEMBER], 'resident identity');
    assert.deepEqual(url.searchParams.getAll('lineage'), [CURRENT_LINEAGE], 'canonical ownership');
    assert.equal(url.searchParams.get('open_chat'), '1', 'chat intent');
    assert.equal(url.searchParams.get('message'), 'msg_original', 'message intent');
    assert.equal(url.hash, '#output', 'message anchor');
  },
  // Proposed stable case test_f080ca3e05c1: exact canonical destination.
  async 'different-resource'(routeScript, source) {
    const canonical = `/lineage?resident=resident_444444444444&lineage=${CURRENT_LINEAGE}`;
    const actual = await destination(routeScript, canonical, source);
    assert.equal(actual, canonical, 'another resource inherits no old query parameters or anchor');
  },
};

// Exports allow offline, in-memory negative controls to reuse these assertions.
// Normal pytest entrypoints always read the unchanged production script.
module.exports = { checks, sourcePath };
if (require.main === module) {
  const selected = process.argv[2];
  assert.ok(Object.hasOwn(checks, selected), 'select same-resource or different-resource');
  const routeScript = fs.readFileSync(0, 'utf8');
  test(selected, () => checks[selected](routeScript));
}
