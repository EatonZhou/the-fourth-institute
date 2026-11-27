'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');

const staticDir = path.resolve(__dirname, '../../../management_portal/static');
const origin = 'http://directories.fixture';
const entry = (path, type = 'file') => ({ path, name: path.split('/').at(-1), kind: type, type });
const entries = (prefix, count) => Array.from({length: count}, (_, n) => entry(prefix + String(n).padStart(4, '0')));
const eventually = async (locator, count = 1) => {
  await locator.waitFor({state: count ? 'visible' : 'hidden'});
  assert.equal(await locator.count(), count);
};

async function main() {
  const browser = await chromium.launch({headless: true});
  const calls = [];
  const failures = new Map();
  const held = new Map();
  const checks = [];
  const page = await browser.newPage();
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  page.setDefaultTimeout(4500);
  const failOnce = (key, status = 503) => failures.set(key, status);
  const hold = key => {
    let release, started;
    const gate = new Promise(resolve => { release = resolve; });
    const pending = new Promise(resolve => { started = resolve; });
    held.set(key, async () => { started(); await gate; });
    return { pending, release };
  };

  try {
    await page.route(origin + '/**', async route => {
      const request = route.request();
      const url = new URL(request.url());
      const dir = url.searchParams.get('path') || '';
      const cursor = url.searchParams.get('cursor') || '';
      const respond = (payload, status = 200) => route.fulfill({ status, json: payload });
      if (url.pathname === '/') {
        return route.fulfill({contentType: 'text/html', body: '<main><div id="code"></div><div id="panel"></div><div id="articleFileTree"></div><div id="workspace"></div></main>'});
      }
      let payload, key;
      if (url.pathname.startsWith('/api/repos/')) {
        const repo = url.pathname.split('/')[3];
        key = [repo, dir, cursor].join(':');
        const children = dir === 'docs' ? [entry('docs/guide.md')] : [entry('docs', 'directory'), ...entries('repo-', 999)];
        payload = {path: dir, entries: cursor ? [entry(dir ? 'docs/last.md' : 'repo-last')] : children,
          next_cursor: cursor ? '' : dir || 'repo-2'};
      } else if (url.pathname.startsWith('/api/articles/')) {
        const id = url.pathname.split('/')[3];
        key = [id, dir, cursor].join(':');
        payload = {id, entries: id === 'two' ? [entry('two.tex')] : cursor === 'article-2'
          ? [entry('docs/last.tex')] : [entry('docs', 'directory'), ...entries('doc-', 2499)],
          next_cursor: id === 'one' && !cursor ? 'article-2' : ''};
      } else if (url.pathname === '/instruction') {
        const {name, args} = request.postDataJSON();
        const token = args.cursor || '';
        key = [name, args.address, token].join(':');
        const folder = args.address.endsWith('/docs');
        payload = {entries: folder ? [entry('docs/leaf.txt')] : token ? [entry('resource-last')]
          : [entry('docs', 'directory'), ...entries('resource-', 199)],
          next_cursor: !folder && !token ? 'resource-2' : ''};
      } else {
        throw new Error(url.pathname);
      }
      calls.push(key);
      if (failures.has(key)) {
        const status = failures.get(key);
        failures.delete(key);
        await respond({error: 'retry fixture'}, status);
        return;
      }
      const wait = held.get(key);
      if (wait) { held.delete(key); await wait(); }
      await respond({...payload, truncated: Boolean(payload.next_cursor)});
    });
    await page.goto(origin);
    await page.evaluate(() => {
      const tag = (parts, ...values) => parts.reduce((text, part, i) => text + part + (values[i] ?? ''), '');
      const esc = value => String(value ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('"', '&quot;');
      window.PortalI18n = {html: tag, t: x => x, text: tag};
      window.PortalUIPrimitives = {escapeHtml: esc, iconHtml: () => ''};
      window.PortalIdentity = {listUIAssociations: async () => ({items: []})};
      window.getJson = async (url, options) => {
        const response = await fetch(url, options);
        const data = await response.json();
        if (!response.ok) throw Object.assign(new Error(data.error), {status: response.status});
        return data;
      };
      window.registered = {};
      window.PortalWorkbench = {registeredPanelPackages: () => [], registerPanelPackage: spec => {registered[spec.type] = spec.create;}};
    });
    for (const script of ['portal_file_tree', 'portal_resident_service', 'portal_personal_workspaces',
      'portal_repository_panels', 'repo_workspace_code_view', 'article_workspace_file_tree']) {
      await page.addScriptTag({path: path.join(staticDir, script + '.js')});
    }
    await page.evaluate(() => {
      const $ = id => document.getElementById(id);
      const noop = () => {};
      const esc = PortalUIPrimitives.escapeHtml;
      const repoState = {selectedId: 'code-repo', activeTab: 'code', navigationSequence: 0,
        overview: {name: 'Repo'}, tree: null};
      const code = PortalRepoWorkspaceCodeView.createCodeView({dirname: noop, elements: {workspace: $('code')}, esc,
        formatDateTime: noop, getJson, isMarkdown: noop, joinPath: (...p) => p.join('/'), markdown: noop,
        repoStateHtml: noop, state: repoState, uiIcon: noop, workspaceCapability: () => false});
      window.codeNavigate = async path => {
        repoState.navigationSequence++;
        repoState.tree = await getJson('/api/repos/code-repo/workspace/tree?' + new URLSearchParams({path}));
        $('code').innerHTML = code.renderCodeView();
      };
      PortalRepositoryPanels.register();
      window.panel = registered['repository-files']({root: $('panel'), repoId: 'panel-repo', api: {get: getJson}});
      panel.show();

      const articleState = {selectedId: 'one', files: [], filesByPath: new Map(), linkedFiles: new Map(),
        collapsedDirectories: new Set(), openFile: ''};
      const article = PortalArticleWorkspaceFileTree.createFileTree({$, activeDocument: noop, articleAbsolutePath: noop,
        articleEntryDialog: noop, articleInternalPath: noop, articleStateHtml: x => x, loadSemanticIndex: noop,
        openArticleFile: noop, openArticleLinkedFile: noop, openArticleResourcePreview: noop,
        renderSettings: noop, request: getJson, showToast: noop, state: articleState});
      window.loadArticle = (options = {}) => article.loadFiles(options);
      window.switchArticle = async id => {
        articleState.selectedId = id;
        article.resetFileListing();
        articleState.files = [];
        articleState.filesByPath.clear();
        await article.loadFiles();
      };

      window.PortalResidentService = PortalResidentService.createClient({execute: (name, args) =>
        getJson('/instruction', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({name, args})})});
      window.personal = PortalPersonalWorkspaces.mount({root: $('workspace'), onOpen: noop});
      return Promise.all([codeNavigate(''), loadArticle(), personal.openResource({workspaceId: 'workspace_fixture', path: '/demo'})]);
    });

    const code = page.locator('#code');
    await eventually(code.locator('[data-open-file="repo-0998"]'));
    assert.equal(calls.filter(key => key.startsWith('code-repo::')).length, 1, 'Code opens one page');
    failOnce('code-repo::repo-2', 403);
    await code.getByRole('button', {name: '加载更多', exact: true}).click();
    await eventually(code.getByText('retry fixture'));
    assert.equal(await code.locator('[data-open-file="repo-0998"]').count(), 1);
    await code.getByRole('button', {name: '重试加载更多', exact: true}).click();
    await eventually(code.locator('[data-open-file="repo-last"]'));
    assert.equal(await code.locator('[data-code-tree-more]').count(), 0);
    await page.evaluate(() => codeNavigate(''));
    const staleRepo = hold('code-repo::repo-2');
    await code.getByRole('button', {name: '加载更多', exact: true}).click();
    await staleRepo.pending;
    await page.evaluate(() => codeNavigate('docs'));
    staleRepo.release();
    await eventually(code.locator('[data-open-file="docs/guide.md"]'));
    assert.equal(await code.locator('[data-open-file="repo-last"]').count(), 0);
    checks.push('Repo Code reads past 1000, retains rows on 403, retries, and discards stale directory pages');

    const panel = page.locator('#panel');
    const docs = panel.locator('[data-repository-path="docs"]');
    await docs.click();
    await eventually(panel.locator('[data-repository-path="docs/guide.md"]'));
    const stalePanel = hold('panel-repo:docs:docs');
    await panel.locator('[data-repository-page="docs"]').click();
    await stalePanel.pending;
    await docs.click();
    await docs.click();
    await eventually(panel.locator('[data-repository-page="docs"]'));
    stalePanel.release();
    assert.equal(await panel.locator('[data-repository-path="docs/last.md"]').count(), 0);
    await panel.locator('[data-repository-page="docs"]').click();
    await eventually(panel.locator('[data-repository-path="docs/last.md"]'));
    await panel.locator('[data-repository-page=""]').click();
    await eventually(panel.locator('[data-repository-path="repo-last"]'));
    await page.evaluate(() => window.panel.refresh());
    await eventually(panel.locator('[data-repository-page=""]'));
    assert.equal(await panel.locator('[data-repository-path="repo-last"]').count(), 0);
    checks.push('Develop file tree uses independent cursors and resets them on collapse and refresh');

    const article = page.locator('#articleFileTree');
    await eventually(article.locator('[data-portal-file-path="doc-2498"]'));
    const staleArticle = hold('one::article-2');
    await article.locator('[data-article-file-page]').click();
    await staleArticle.pending;
    await page.evaluate(() => switchArticle('two'));
    staleArticle.release();
    await eventually(article.locator('[data-portal-file-path="two.tex"]'));
    assert.equal(await article.locator('[data-portal-file-path="docs/last.tex"]').count(), 0);
    await page.evaluate(() => switchArticle('one'));
    await article.locator('[data-article-file-page]').click();
    await eventually(article.locator('[data-portal-file-path="docs/last.tex"]'));
    await page.evaluate(() => loadArticle({background: true}));
    assert.equal(await article.locator('[data-portal-file-path="docs/last.tex"]').count(), 1);
    await page.evaluate(() => loadArticle());
    assert.equal(await article.locator('[data-portal-file-path="docs/last.tex"]').count(), 0);
    await eventually(article.locator('[data-article-file-page]'));
    checks.push('Article reaches files beyond 2500, preserves them during polling, resets on reload, and isolates project changes');

    const workspace = page.locator('#workspace');
    await eventually(workspace.locator('[data-portal-file-path="/demo/resource-0198"]'));
    failOnce('ListResourceFiles:workspace_fixture/file:resource-2', 403);
    await workspace.locator('[data-workspace-directory-page="/demo"]').click();
    await eventually(workspace.getByText('读取失败：retry fixture'));
    assert.equal(await workspace.locator('[data-portal-file-path="/demo/resource-0198"]').count(), 1);
    await workspace.locator('[data-workspace-directory-page="/demo"]').click();
    await eventually(workspace.locator('[data-portal-file-path="/demo/resource-last"]'));
    assert.deepEqual(calls.filter(key => key.includes('workspace_fixture/file')), [
      'ReadResource:workspace_fixture/file:',
      'ListResourceFiles:workspace_fixture/file:resource-2',
      'ListResourceFiles:workspace_fixture/file:resource-2',
    ]);
    checks.push('Workspace continuation uses the exact ReadResource address and preserves the loaded entries on denial');

    assert.deepEqual(pageErrors, []);
    console.log(JSON.stringify({checks, calls}, null, 2));
  } finally {
    await browser.close();
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
