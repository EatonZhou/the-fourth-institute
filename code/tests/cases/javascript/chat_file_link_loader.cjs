const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

test('Resident Menu awaits shared file links before opening dynamically loaded Chat', async () => {
  const root = path.resolve(__dirname, '../../../management_portal/static');
  const loaded = [];
  let releaseFileLinks;
  let opened = false;
  const document = {
    readyState: 'loading', addEventListener() {}, querySelectorAll: () => [],
    documentElement: { dataset: {}, style: { setProperty() {} } },
  };
  const window = {
    addEventListener() {},
    dispatchEvent() {},
    PortalState: { storage: { readText: (_key, fallback) => fallback } },
    PortalI18n: { t: text => text, definition: value => value, normalize: language => language || 'original' },
    PortalUI: { toast: message => { throw new Error(message); } },
    PortalResidentMenu: { open() {
      opened = true;
      assert.equal(window.PortalFileLinks.kindForPath('photo.png'), 'image');
      return window.PortalFileLinks.imagePreviewUrl('/srv/uploads/upload_0123456789ab/photo.png');
    } },
    PortalUIPrimitives: {
      registerEnhancer() {},
      async loadAsset(url) {
        loaded.push(url);
        if (url === '/static/portal_file_links.js') {
          await new Promise(resolve => { releaseFileLinks = resolve; });
        }
        if (['/static/portal_file_links.js', '/static/portal_preview_contract.js'].includes(url)) {
          vm.runInContext(fs.readFileSync(path.join(root, path.basename(url)), 'utf8'), context);
        }
      },
    },
  };
  const context = vm.createContext({ window, document, Element: class {}, CustomEvent: class {}, URL, console });
  vm.runInContext(fs.readFileSync(path.join(root, 'portal_theme.js'), 'utf8'), context);
  vm.runInContext(fs.readFileSync(path.join(root, 'portal_nav.js'), 'utf8'), context);
  const opening = window.PortalNav.openResidentMenu({ residentId: 'resident_0123456789ab' });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(typeof releaseFileLinks, 'function', 'the dynamic Chat entry must request file links');
  assert.equal(opened, false, 'the component must wait for its shared dependencies');
  releaseFileLinks();
  assert.equal(await opening, '/api/uploads/upload_0123456789ab');
  assert.equal(loaded.filter(url => url === '/static/portal_file_links.js').length, 1);
});

test('file links and Workspace preview share encoded local-file metadata and content requests', async () => {
  const root = path.resolve(__dirname, '../../../management_portal/static');
  const file = '/srv/文档/a b?#&.md';
  const calls = [];
  const apiGet = async url => {
    const parsed = new URL(url, 'https://portal.invalid');
    calls.push(parsed);
    assert.equal(parsed.pathname, '/api/agent-workbench/link-file');
    assert.equal(parsed.searchParams.get('path'), file);
    assert.equal(parsed.hash, '');
    return { path: file, kind: 'markdown', text: '# Local', allows: { UpdateWorkspaceFile: false } };
  };
  const window = { PortalAPI: { get: apiGet }, PortalResidentService: {}, PortalI18n: { t: value => value } };
  const context = vm.createContext({ window, URL, URLSearchParams, console });
  for (const file of ['portal_preview_contract.js', 'portal_file_links.js', 'portal_agent_preview_source.js']) {
    vm.runInContext(fs.readFileSync(path.join(root, file), 'utf8'), context);
  }
  const links = window.PortalFileLinks;
  const preview = window.PortalAgentPreviewSource.create({
    apiGet, apiPost: () => { throw new Error('read-only fixture'); }, linkFileUrl: links.apiUrl,
  });
  const document = { path: file, source: 'workspace_link' };
  await links.probe(file);
  await preview.fetchMeta(document);
  assert.equal(calls[0].href, calls[1].href);
  assert.equal(calls[1].searchParams.get('meta'), '1');
  await links.load(file);
  const result = await preview.reload(document);
  assert.equal(calls[2].href, calls[3].href);
  assert.equal(calls[3].searchParams.has('meta'), false);
  assert.equal(result.path, file);
  assert.equal(result.source, 'workspace_link');
  assert.equal(preview.canEdit(result), false, 'shared URL ownership must not grant write access');
});
