'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const root = path.resolve(__dirname, '../../..');
const html = fs.readFileSync(path.join(root, 'management_portal/article_workspace.html'), 'utf8')
  .replace(/<script\b[^>]*>[\s\S]*?<\/script>/g, '').replace(/<link\b[^>]*>/g, '');
const article = {id: 'article_123456789abc', title: '可打开的稿件'};
const missingId = 'article_abcdef123456';
// /api/resources/resolve returns ResourceService's flat HTTP document;
// the HATG resource/target envelope is a separate transport contract.
const resolvedArticle = {
  schema: 'tfi.resource_resolution.v2', resource_type: 'article', root_id: missingId,
  target_kind: 'article', exists: true,
  record: {resource_id: missingId, title: '部分恢复的稿件', primary_file: 'main.md'},
};

(async () => {
  const browser = await chromium.launch({headless: true});
  const errors = [];
  let list = [], failList = false, releaseList, pendingList;
  let resolution = {status: 404, json: {error: 'Article 不存在或无法读取'}};
  try {
    const page = await browser.newPage();
    page.setDefaultTimeout(5000);
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (url.pathname === '/resource/article') return route.fulfill({contentType: 'text/html', body: html});
      if (url.pathname === '/api/articles') {
        if (pendingList) await pendingList;
        return route.fulfill(failList
          ? {status: 503, json: {error: '暂时无法读取列表'}}
          : {json: {articles: list}});
      }
      if (url.pathname === '/api/resources/resolve') return route.fulfill(resolution);
      return route.abort();
    });
    async function setup(search = '') {
      await page.goto('http://article.test/resource/article' + search);
      await page.evaluate(() => {
        window.PortalI18n = {t: text => text,
          text: (parts, ...values) => String.raw({raw: parts}, ...values),
          html: (parts, ...values) => String.raw({raw: parts}, ...values)};
        window.PortalSite = {setTitle() {}};
        window.PortalIdentity = {savePreferences: async () => {}};
      });
      for (const name of ['portal_ui_primitives.js', 'portal_resource_header.js', 'article_workspace_catalog.js']) {
        await page.addScriptTag({path: path.join(root, 'management_portal/static', name)});
      }
      await page.evaluate(() => {
        const $ = id => document.getElementById(id), noop = () => {};
        window.state = {articles: [], selectedId: '', lastSelectedId: '', openFile: '',
          compilingArticles: new Set(), filesByPath: new Map(), texlabVersion: 0};
        window.guestFailures = [];
        window.restoredDocuments = [];
        window.chatLoads = []; window.gitLoads = [];
        window.catalog = PortalArticleWorkspaceCatalog.createCatalog({
          ...Object.fromEntries(PortalArticleWorkspaceCatalog.dependencies.map(name => [name, noop])), $, state,
          selectedArticle: () => state.articles.find(item => item.id === state.selectedId),
          articleProjects: () => [], articleStateHtml: text => text, esc: PortalUIPrimitives.escapeHtml,
          clearEditor: () => { state.openFile = ''; $('articleEditorState').textContent = ''; },
          queryState: () => new URLSearchParams(location.search),
          updateUrl: id => history.replaceState(null, '', '/resource/article' + (id ? '?item=' + id : '')),
          detailHistory: {open: id => history.pushState(null, '', '?item=' + id)},
          detailResourceGuest: {failed: error => guestFailures.push(error), ready: noop, setResource: noop},
          readingPosition: {persist: noop, prepare: noop,
            restoreDocuments: async (id, primary) => { restoredDocuments.push({id, primary}); }},
          loadBuild: async () => {}, loadArticleGit: async options => { gitLoads.push(options); },
          loadArticleChat: async () => { chatLoads.push(state.selectedId); },
          request: async url => {
            const response = await fetch(url), data = await response.json();
            if (!response.ok) throw new Error(data.error);
            return data;
          },
        });
        window.startLoad = options => { window.loadDone = catalog.loadArticles(options).then(() => '', error => error.message); };
      });
    }
    const title = () => page.locator('#articleProjectTitle').innerText();
    const busy = () => page.locator('#articleProjectButton').getAttribute('aria-busy');

    await setup();
    assert.equal(await title(), '选择 Article', 'static empty shell must not claim a request is running');
    pendingList = new Promise(resolve => { releaseList = resolve; });
    await page.evaluate(() => startLoad());
    assert.equal(await title(), '正在加载 Article…');
    assert.equal(await busy(), 'true');
    releaseList(); pendingList = null;
    assert.equal(await page.evaluate(() => loadDone), '');
    assert.equal(await title(), 'Select an Article project');
    assert.equal(await busy(), 'false');
    assert.equal(await page.locator('#articleCompile').isDisabled(), true);

    failList = true;
    await setup();
    await page.evaluate(() => startLoad());
    assert.equal(await page.evaluate(() => loadDone), '暂时无法读取列表');
    assert.equal(await title(), 'Article 列表加载失败');
    assert.equal(await busy(), 'false');
    assert.match(await page.locator('#articleEditorState').innerText(), /暂时无法读取列表/);
    failList = false;
    await page.evaluate(() => startLoad());
    await page.evaluate(() => loadDone);
    assert.equal(await title(), 'Select an Article project', 'successful retry clears the error');

    resolution = {json: resolvedArticle};
    await setup('?item=' + missingId);
    await page.evaluate(() => startLoad());
    assert.equal(await page.evaluate(() => loadDone), '', 'flat HTTP resolution opens an Article absent from the list');
    assert.equal(await title(), resolvedArticle.record.title);
    assert.equal(await page.evaluate(() => state.selectedId), missingId);
    assert.deepEqual(await page.evaluate(() => restoredDocuments), [{id: missingId, primary: 'main.md'}]);
    assert.deepEqual(await page.evaluate(() => guestFailures), []);
    assert.equal(await page.locator('#articleCompile').isDisabled(), false);

    for (const invalid of [
      {...resolvedArticle, resource_type: 'repo'},
      {...resolvedArticle, root_id: article.id},
      {...resolvedArticle, target_kind: 'file'},
      {...resolvedArticle, exists: false},
      {target: resolvedArticle, resource: resolvedArticle.record},
    ]) {
      resolution = {json: invalid};
      await setup('?item=' + missingId);
      await page.evaluate(() => startLoad());
      assert.equal(await page.evaluate(() => loadDone), '这个地址不是可打开的 Article。');
      assert.equal(await page.evaluate(() => state.selectedId), '');
      assert.deepEqual(await page.evaluate(() => restoredDocuments), []);
      assert.equal(await page.evaluate(() => guestFailures.length), 1);
    }
    resolution = {status: 403, json: {error: '无权读取 Article'}};
    await setup('?item=' + missingId);
    await page.evaluate(() => startLoad());
    assert.equal(await page.evaluate(() => loadDone), '无权读取 Article');
    assert.equal(await page.evaluate(() => state.selectedId), '');
    assert.deepEqual(await page.evaluate(() => restoredDocuments), []);

    resolution = {status: 404, json: {error: 'Article 不存在或无法读取'}};
    list = [article];
    await setup('?item=' + missingId);
    await page.evaluate(() => startLoad());
    assert.equal(await page.evaluate(() => loadDone), 'Article 不存在或无法读取');
    assert.equal(await title(), '无法打开 Article');
    assert.equal(await busy(), 'false');
    assert.equal(await page.evaluate(() => state.selectedId), '');
    assert.match(page.url(), new RegExp(missingId + '$'), 'keep the failed explicit address');
    assert.equal(await page.locator('#articleMenuList [data-article-select]').count(), 1);
    assert.equal(await page.evaluate(() => guestFailures.length), 1);
    await page.evaluate(id => {
      state.sideView = 'files';
      state.visibleTools = new Set(['chat', 'git', 'files']);
      return catalog.selectArticle(id);
    }, article.id);
    assert.equal(await title(), article.title, 'another available Article can still be opened');
    assert.equal(await page.locator('#articleCompile').isDisabled(), false);
    assert.equal(await page.locator('#articleEditorState').innerText(), '');
    assert.equal(await page.evaluate(() => chatLoads.at(-1)), article.id, 'detached Chat follows Article changes while Files is selected');
    assert.equal(await page.evaluate(() => gitLoads.at(-1).includeHistory), true, 'detached Git refreshes its graph');

    failList = true;
    await page.evaluate(() => {
      state.openFile = 'main.tex';
      document.getElementById('articleEditorState').textContent = '未保存修改';
      startLoad({reloadSelected: false});
    });
    await page.evaluate(() => loadDone);
    assert.equal(await title(), article.title, 'refresh failure retains an already open Article');
    assert.equal(await busy(), 'false');
    assert.equal(await page.evaluate(() => state.openFile), 'main.tex');
    assert.equal(await page.locator('#articleEditorState').innerText(), '未保存修改');
    assert.deepEqual(errors, []);
    console.log('PASS: flat HTTP fallback, exact root/type/existence, denied reads, loading and active-document preservation');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
