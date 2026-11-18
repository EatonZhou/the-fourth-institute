'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const root = path.resolve(__dirname, '../../..');
const { portalWorkspaceSurfaceScripts } = require('./portal_workspace_surface_scripts.cjs');
const workbenchScripts = JSON.parse(fs.readFileSync(path.join(root, 'management_portal/script_groups.json'), 'utf8'))['portal-workbench'].map(s => s.replace('/static/', ''));
const source = name => fs.readFileSync(path.join(root, 'management_portal/static', name), 'utf8');
const id = 'article_123456789abc', other = 'article_abcdef123456', knowledge = 'concept_123456789abc';
const title = '面向低批量 MoE 推理的多芯粒系统设计';
const articles = [{id, title}, {id: other, title: '另一篇文章'}];
const html = fs.readFileSync(path.join(root, 'management_portal/article_workspace.html'), 'utf8')
  .replace(/<script\b[^>]*>[\s\S]*?<\/script>/g, '').replace(/<link\b[^>]*>/g, '');

(async () => {
  const browser = await chromium.launch({headless: true});
  const saved = {alice: new Set([knowledge]), bob: new Set([other])};
  let actor = 'alice', failNext = false, denied = false, releaseRead;
  const errors = [], references = [];
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 900}});
    page.setDefaultTimeout(8000);
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (url.pathname.startsWith('/static/')) {
        const file = path.join(root, 'management_portal', url.pathname);
        if (fs.existsSync(file)) return route.fulfill({path: file});
      }
      if (url.pathname === '/resource/article') return route.fulfill({contentType: 'text/html', body: html});
      if (url.pathname === '/api/articles') return route.fulfill({json: {articles}});
      if (url.pathname === '/api/resources/reference') {
        references.push(url.searchParams.get('address'));
        return route.fulfill({json: {title, url: '/resource/article?item=' + url.searchParams.get('address')}});
      }
      if (url.pathname === '/api/resources/favorites') {
        if (route.request().method() === 'POST') {
          if (failNext) { failNext = false; return route.fulfill({status: 403, json: {error: 'Not allowed'}}); }
          const {address, favorite} = route.request().postDataJSON();
          if (favorite) saved[actor].add(address); else saved[actor].delete(address);
          return route.fulfill({json: {address, favorite}});
        }
        const items = [...saved[actor]].map(address => denied && address === id
          ? {address, status: 'denied'}
          : {address, title: articles.find(a => a.id === address)?.title || '知识条目', status: 'available'});
        if (releaseRead) await new Promise(resolve => { releaseRead = resolve; });
        return route.fulfill({json: {items}});
      }
      return route.abort();
    });
    const setup = async () => {
      const document = fs.readFileSync(path.join(root, 'management_portal/article_workspace.html'), 'utf8');
      for (const match of document.matchAll(/<link[^>]+href="([^"]+\.css)"/g)) {
        await page.addStyleTag({url: match[1]});
      }
      await page.evaluate(() => {
        document.documentElement.dataset.portalTheme = 'auto';
        window.PortalCompatibility = {identifier: x => x};
        window.PortalI18n = {t: x => x, message: x => x, requestLanguage: () => 'zh-CN',
          text: (parts, ...values) => String.raw({raw: parts}, ...values), html: (parts, ...values) => String.raw({raw: parts}, ...values)};
        window.toasts = []; window.PortalUI = {toast: message => toasts.push(message)};
        window.PortalSite = {setTitle() {}};
        window.PortalResourceRoutes = {schema: 'tfi.resource-portal-routes.v1', routes: [
          {resource_type: 'concept', detail_kind: 'knowledge', path: '/resource/knowledge', parameter: 'item'},
          {resource_type: 'article', detail_kind: 'article', path: '/resource/article', parameter: 'item'},
        ], text_resource_types: ['concept'], navigation_resource_types: ['article'], identity_parameters: []};
      });
      for (const name of ['portal_state.js', 'portal_page_state.js', 'portal_text.js', 'portal_api.js', 'portal_ui_primitives.js', 'portal_overlay.js',
        'portal_command_registry.js', 'portal_workbench_context.js', 'portal_editor_open_service.js',
        ...workbenchScripts, ...portalWorkspaceSurfaceScripts(),
        'portal_preview_contract.js', 'portal_resource_chips.js', 'portal_resource_header.js', 'portal_resource_favorites.js',
        'article_workspace_catalog.js', 'article_workspace_popovers.js', 'article_workspace_tools.js', 'article_workspace_layout.js',
        'article_workspace_events.js']) await page.addScriptTag({content: source(name)});
      await page.evaluate(async ({id}) => {
        const $ = name => document.getElementById(name), noop = () => {};
        const deps = module => Object.fromEntries(module.dependencies.map(k => [k, noop]));
        window.state = {selectedId: new URLSearchParams(location.search).get('item') || id, articles: [], compilingArticles: new Set(), monacoDisposables: []};
        window.catalog = PortalArticleWorkspaceCatalog.createCatalog({ ...deps(PortalArticleWorkspaceCatalog), $, state,
          selectedArticle: () => state.articles.find(a => a.id === state.selectedId), articleProjects: () => [],
          esc: PortalUIPrimitives.escapeHtml, queryState: () => new URLSearchParams(location.search),
          request: url => PortalAPI.get(url),
        });
        const popovers = PortalArticleWorkspacePopovers.createPopovers({...deps(PortalArticleWorkspacePopovers), $, state});
        window.layout = PortalArticleWorkspaceLayout.createLayout({...deps(PortalArticleWorkspaceLayout), $, state,
          articleLayoutStore: PortalWorkbench.layout.createStore({storageKey: 'article-favorites-test-layout'}),
        });
        layout.initArticleWorkbench();
        layout.currentArticleWorkbench().hidePanel('editor');
        layout.restoreLayout();
        PortalArticleWorkspaceEvents.createEvents({...deps(PortalArticleWorkspaceEvents), $, state, ...layout, ...popovers,
          articleGitRefresh: {stop: noop}, articleContentRefresh: {stop: noop},
        });
        await catalog.loadArticles({reloadSelected: false});
      }, {id});
      await page.waitForFunction(() => document.querySelector('#articleCurrentFavorite button')?.disabled === false);
    };
    await page.goto('http://article.test/resource/article?item=' + id); await setup();
    const star = page.locator('#articleCurrentFavorite button');
    const rows = page.locator('#articleFavoritesPanel .resourceFavoriteRow');
    assert.equal(await page.locator('[data-portal-board-actions] #articleCurrentFavorite, [data-portal-board-actions] #articleFavoritesButton').count(), 0);
    assert.equal(await page.locator('#articleBuildSummary + #articleCurrentFavorite:last-child button').count(), 1, 'star ends the PDF toolbar');
    const assertBorderless = async () => assert.deepEqual(await star.evaluate(el => {
      const style = getComputedStyle(el);
      return {border: style.borderTopWidth, background: style.backgroundColor, shadow: style.boxShadow};
    }), {border: '0px', background: 'rgba(0, 0, 0, 0)', shadow: 'none'});
    await assertBorderless();
    assert.equal(await star.innerText(), '☆');
    await page.locator('#articleFavoritesButton').click();
    assert.match(await page.locator('#articleFavoritesPanel').innerText(), /暂无 Article 收藏/);
    await page.locator('#articleFavoritesButton').click();
    assert.equal(await page.locator('#articleFavoritesPanel').isVisible(), true, 'activity rail focuses an already visible tool');
    await page.evaluate(() => layout.setToolVisible(false, 'favorites'));
    assert.equal(await page.locator('#articleFavoritesPanel').isVisible(), false);
    await page.locator('#articleFavoritesButton').click();
    assert.equal(await page.locator('#articleFavoritesPanel').isVisible(), true);
    await star.click();
    await page.waitForFunction(() => document.querySelector('#articleCurrentFavorite button').getAttribute('aria-pressed') === 'true');
    await assertBorderless();
    assert.equal(await star.innerText(), '★');
    await page.reload(); await setup();
    assert.equal(await star.getAttribute('aria-pressed'), 'true', 'reload restores committed state');
    assert.equal(await page.locator('#articleFavoritesPanel').isVisible(), true, 'reload restores the Favorites side view');
    assert.equal(await rows.count(), 1, 'Article list excludes Knowledge');
    assert.equal(await rows.locator('.resourceFavoriteTitle').innerText(), title);
    const artifacts = process.env.ARTICLE_FAVORITES_ARTIFACT_DIR;
    if (artifacts) {
      fs.mkdirSync(artifacts, {recursive: true});
      await page.screenshot({path: path.join(artifacts, 'article-favorites.png')});
    }
    await Promise.all([page.waitForEvent('domcontentloaded'), rows.locator('.resourceFavoriteOpen').click()]).catch(async error => {
      throw new Error(`${error.message}: ${JSON.stringify({references, errors, toasts: await page.evaluate(() => toasts)})}`);
    });
    await page.waitForURL('**/resource/article?item=' + id);
    await page.waitForLoadState(); await setup();
    assert.deepEqual(references, [id], 'open revalidates through shared resource reference');
    // A failed mutation leaves the star and committed list untouched.
    failNext = true; await star.click();
    await page.waitForFunction(() => toasts.length === 1 && !document.querySelector('#articleCurrentFavorite button').disabled);
    assert.equal(await star.getAttribute('aria-pressed'), 'true');
    await page.evaluate(async ({other}) => {
      state.selectedId = other;
      await catalog.loadArticles({reloadSelected: false});
    }, {other});
    await page.waitForFunction(() => document.querySelector('#articleCurrentFavorite button').getAttribute('aria-pressed') === 'false');
    await page.evaluate(async ({id}) => {
      state.selectedId = id;
      await catalog.loadArticles({reloadSelected: false});
    }, {id});
    await page.waitForFunction(() => document.querySelector('#articleCurrentFavorite button').getAttribute('aria-pressed') === 'true');
    // Identity changes discard an older account's in-flight list response.
    releaseRead = true;
    await page.evaluate(() => { void PortalResourceFavorites.refresh(); });
    while (typeof releaseRead !== 'function') await new Promise(r => setTimeout(r, 10));
    const finishOldRead = releaseRead; releaseRead = null; actor = 'bob';
    await page.evaluate(() => dispatchEvent(new Event('portal:identity-changed')));
    await page.waitForFunction(() => document.querySelector('#articleCurrentFavorite button')?.getAttribute('aria-pressed') === 'false');
    finishOldRead();
    if (!await page.locator('#articleFavoritesPanel').isVisible()) await page.locator('#articleFavoritesButton').click();
    await page.waitForFunction(() => document.querySelector('#articleFavoritesPanel .resourceFavoriteTitle')?.textContent === '另一篇文章');
    assert.equal(await rows.count(), 1);
    actor = 'alice'; denied = true;
    await page.evaluate(() => dispatchEvent(new Event('portal:identity-changed')));
    await page.waitForFunction(() => document.querySelector('#articleFavoritesPanel .resourceFavoriteOpen')?.disabled === true);
    assert.doesNotMatch(await rows.innerText(), new RegExp(title));
    await rows.locator('.resourceFavoriteButton').click();
    await page.waitForFunction(() => document.querySelectorAll('#articleFavoritesPanel .resourceFavoriteRow').length === 0);
    assert.equal(saved.alice.has(id), false, 'denied article can still be removed');
    assert.deepEqual(errors, []);
    await page.locator('[data-article-view="files"]').click();
    assert.equal(await page.locator('#articleFavoritesPanel').isVisible(), false, 'other side views still work');
    console.log('PASS: borderless Article star, Favorites side view and restoration, filtered titles, persistence, canonical opening, failure, identity isolation and denied removal.');
  } finally { await browser.close(); }
})().catch(error => {console.error(error); process.exitCode = 1;});
