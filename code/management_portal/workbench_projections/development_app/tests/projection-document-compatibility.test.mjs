import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const staticRoot = new URL('../../../static/', import.meta.url);
const state = readFileSync(new URL('portal_state.js', staticRoot), 'utf8');
const host = readFileSync(new URL('projection_host.js', staticRoot), 'utf8');
const exportLine = 'window.PortalProjectionHost = Object.freeze({ open, mount, createPanel: createContentPanel });';
function context(site = {}) {
  const value = vm.createContext({__PORTAL_SITE__: site, addEventListener() {}, console, setTimeout, clearTimeout,
    location: {pathname: '/fixture', search: '', hash: ''},
    document: {title: 'fixture', readyState: 'loading', addEventListener() {}},
    MutationObserver: class {observe() {}}, matchMedia: () => ({addEventListener() {}}),
    PortalWorkbench: {registerPanelPackage() {}}, PortalI18n: {t: text => text}, Element: class {}});
  value.window = value;
  return value;
}
function bootstrap(site) {
  const parent = context(site);
  vm.runInContext(host.replace(exportLine, 'window.bootstrapTest = stateBootstrap;' + exportLine), parent);
  return parent.bootstrapTest({createElement: tag => ({tag, textContent: ''})}, state);
}

test('opaque documents receive empty compatibility before synchronous state and SDK execution', () => {
  const scripts = bootstrap({site_name: 'example', paths: {share_root: 'private-root'}, secret: 'private-token',
    compatibility: {browser_identifiers: {theme: 'archive.theme', demo_frame_scope: 'archive.frame', secret: 'private-value'},
      format_namespaces: ['archive.'], environment_aliases: {PORTAL_TEST: ['ARCHIVE_PRIVATE']}, domain_values: {token: 'private-token'}}});
  assert.equal(scripts.length, 2);
  assert.equal(scripts[1].textContent, state);
  assert.doesNotMatch(scripts[0].textContent, /archive|private-|ARCHIVE_PRIVATE|environment_aliases|domain_values|site_name|share_root/);
  const child = context();
  scripts.forEach(script => vm.runInContext(script.textContent, child));
  assert.equal(child.PortalCompatibility.identifier('theme'), 'tfi.portal.theme');
  assert.equal(child.PortalCompatibility.identifier('demo_frame_scope'), 'tfi.portal-demo-frame.v1');
  assert.equal(child.PortalCompatibility.schemaMatches('tfi.projection.v2', 'tfi.projection.v2'), true);
  assert.equal(child.PortalCompatibility.schemaMatches('archive.projection.v2', 'tfi.projection.v2'), false);
  assert.equal(child.PortalCompatibility.schemaMatches('archive.projection.v1', 'tfi.projection.v2'), false);
  assert.equal(Object.isFrozen(child.__PORTAL_SITE__.compatibility.browser_identifiers), true);
  assert.equal(Object.isFrozen(child.__PORTAL_SITE__.compatibility.format_namespaces), true);
  assert.equal(JSON.stringify(child.__PORTAL_SITE__), '{"compatibility":{"browser_identifiers":{},"format_namespaces":[]}}');
  assert.match(host, /head\.prepend\(policy, shared, theme, \.\.\.stateBootstrap\(documentSource, state\), bootstrap\)/);
});

test('absent or malformed parent configuration cannot influence generated documents', () => {
  const child = context();
  bootstrap({}).forEach(script => vm.runInContext(script.textContent, child));
  assert.equal(child.PortalCompatibility.identifier('theme'), 'tfi.portal.theme');
  assert.equal(child.PortalCompatibility.schemaMatches('archive.projection.v2', 'tfi.projection.v2'), false);
  for (const value of [['tfi.'], ['archive.', 'archive.'], ['</script>'], Array.from({length: 33}, (_, i) => `archive${i}.`)]) {
    assert.equal(bootstrap({compatibility: {format_namespaces: value}})[0].textContent, bootstrap({})[0].textContent);
  }
  assert.doesNotThrow(() => bootstrap({get compatibility() {throw new Error('must not read parent configuration');}}));
});
