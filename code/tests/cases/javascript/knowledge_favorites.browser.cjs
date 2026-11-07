'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const root = path.resolve(__dirname, '../../..');
const source = name => fs.readFileSync(path.join(root, 'management_portal/static', name), 'utf8');
const extract = (text, start, end) => {
  const from = text.indexOf(start), to = text.indexOf(end, from);
  assert.ok(from >= 0 && to > from);
  return text.slice(from, to);
};
const id = 'concept_123456789abc', other = 'insight_abcdef123456';

(async () => {
  const browser = await chromium.launch({ headless: true });
  const saved = new Set(), requests = [], unexpected = [], errors = [];
  let failNext = false, releasePost;
  const html = fs.readFileSync(path.join(root, 'knowledge_board_lan/knowledge_board.html'), 'utf8')
    .replace(/<script\b[^>]*>[\s\S]*?<\/script>/g, '').replace(/<link\b[^>]*>/g, '');
  try {
    const page = await browser.newPage({ viewport: { width: 1200, height: 850 } });
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', async route => {
      const request = route.request(), url = new URL(request.url());
      if (url.pathname === '/') return route.fulfill({contentType: 'text/html', body: html});
      if (url.pathname !== '/api/resources/favorites') {
        unexpected.push(request.url()); return route.abort();
      }
      requests.push({method: request.method(), body: request.postDataJSON()});
      if (request.method() === 'POST') {
        if (releasePost) await new Promise(resolve => { releasePost = resolve; });
        if (failNext) {
          failNext = false;
          return route.fulfill({status: 403, contentType: 'application/json', body: JSON.stringify({error: 'Not allowed'})});
        }
        const { address, favorite } = request.postDataJSON();
        if (favorite) saved.add(address); else saved.delete(address);
        return route.fulfill({json: {address, favorite}});
      }
      return route.fulfill({json: {items: [...saved].map(address => ({address, title: '知识收藏测试', status: 'available'}))}});
    });
    await page.goto('http://knowledge.test/');
    for (const name of ['portal_design_system.css', 'portal_components.css', 'knowledge_board.css']) {
      await page.addStyleTag({content: source(name)});
    }
    await page.evaluate(() => {
      window.PortalI18n = {t: x => x, message: x => x, requestLanguage: () => 'zh-CN',
        html: (parts, ...values) => String.raw({raw: parts}, ...values),
        text: (parts, ...values) => String.raw({raw: parts}, ...values)};
      window.toasts = []; window.PortalUI = {toast: message => toasts.push(message)};
      window.navigations = [];
      window.openDetail = id => navigations.push(id);
      window.knowledgeBoardShell = {formatDateTime: () => '2026-11-08'};
      window.el = Object.fromEntries(['mFavorite', 'mResourceIdentity', 'modal'].map(id => [id, document.getElementById(id)]));
      window.state = {};
      window.detailDocumentSession = window.detailResourceGuest = null;
      window.detailHistory = {close() {}};
      window.disposeDetailPreview = window.resetDetailGraph = window.resetDetailChat = () => {};
      document.body.classList.remove('knowledgeInitializing');
    });
    const render = source('knowledge_board_render.js'), detail = source('knowledge_board_detail.js');
    await page.addScriptTag({content: extract(render, 'function createKnowledgeCard(', 'function getProjectColumnCount(')
      + extract(detail, 'function renderKnowledgeResourceIdentity(', 'function initialKnowledgeItemId(')
      + extract(detail, 'function finalizeDetailClose(', 'function syncDetailButtons(')});
    await page.evaluate(({id}) => {
      const card = createKnowledgeCard({kb_id: id, title: '知识收藏：卡片与详情同步', type: 'concept', importance: 8, preview: '共享模块异步加载后，两个入口保持同一收藏状态。'});
      document.getElementById('grid').replaceChildren(card);
      renderKnowledgeResourceIdentity({kb_id: id});
    }, {id});
    // Load the actual shared owners after Knowledge has already declared containers.
    for (const name of ['portal_api.js', 'portal_ui_primitives.js', 'portal_resource_header.js', 'portal_resource_favorites.js']) {
      await page.addScriptTag({content: source(name)});
    }
    await page.evaluate(({id}) => {
      // The detail payload refreshes its identity after the asynchronous shell loads.
      renderKnowledgeResourceIdentity({kb_id: id, title: '知识收藏：卡片与详情同步', type: 'concept'});
      document.getElementById('grid').style.maxWidth = '360px';
    }, {id});
    const cardStar = page.locator('.card .resourceFavoriteButton');
    const detailStar = page.locator('#mFavorite .resourceFavoriteButton');
    await page.waitForFunction(() => document.querySelector('.card .resourceFavoriteButton')?.disabled === false);
    assert.equal(await cardStar.getAttribute('aria-pressed'), 'false');
    releasePost = true;
    await cardStar.click();
    await page.waitForFunction(() => document.querySelector('.card .resourceFavoriteButton').disabled);
    assert.equal(await cardStar.getAttribute('aria-pressed'), 'false', 'no optimistic success before server commit');
    releasePost(); releasePost = null;
    await page.waitForFunction(() => document.querySelector('.card .resourceFavoriteButton').getAttribute('aria-pressed') === 'true');
    assert.equal(await detailStar.getAttribute('aria-pressed'), 'true');
    assert.equal(await cardStar.getAttribute('aria-label'), '取消收藏此资源');
    assert.deepEqual(await page.evaluate(() => navigations), [], 'star click cannot open the card');
    assert.ok(saved.has(id));
    const artifacts = process.env.KNOWLEDGE_FAVORITES_ARTIFACT_DIR;
    if (artifacts) {
      fs.mkdirSync(artifacts, {recursive: true});
      await page.locator('.card').screenshot({path: path.join(artifacts, 'knowledge-favorite-card.png')});
      await page.evaluate(() => { el.modal.hidden = false; document.getElementById('mSub').textContent = 'AI员工 · concept'; });
      await page.locator('#modal .portalDetailHeader').screenshot({path: path.join(artifacts, 'knowledge-favorite-detail.png')});
    }
    await detailStar.click({force: true});
    await page.waitForFunction(() => document.querySelector('.card .resourceFavoriteButton').getAttribute('aria-pressed') === 'false');
    assert.equal(saved.size, 0);
    await page.evaluate(({other}) => {
      window.oldFavoriteButton = document.querySelector('#mFavorite button');
      renderKnowledgeResourceIdentity({kb_id: other});
    }, {other});
    await page.waitForFunction(() => document.querySelector('#mFavorite button')?.getAttribute('aria-pressed') === 'false');
    await detailStar.click({force: true});
    await page.waitForFunction(() => document.querySelector('#mFavorite button').disabled === false
      && document.querySelector('#mFavorite button').getAttribute('aria-pressed') === 'true');
    assert.ok(saved.has(other));
    assert.equal(await cardStar.getAttribute('aria-pressed'), 'false');
    assert.equal(await page.evaluate(() => oldFavoriteButton.isConnected), false);
    assert.equal(await page.evaluate(() => oldFavoriteButton.getAttribute('aria-pressed')), 'false', 'removed binding stops receiving updates');
    await page.evaluate(() => finalizeDetailClose());
    assert.equal(await detailStar.count(), 0);
    await page.evaluate(({other}) => renderKnowledgeResourceIdentity({kb_id: other}), {other});
    await page.waitForFunction(() => document.querySelector('#mFavorite button')?.getAttribute('aria-pressed') === 'true');
    failNext = true;
    await detailStar.click({force: true});
    await page.waitForFunction(() => toasts.length === 1 && document.querySelector('#mFavorite button').disabled === false);
    assert.equal(await detailStar.getAttribute('aria-pressed'), 'true', 'failed cancellation preserves server state');
    assert.ok(saved.has(other));
    await page.evaluate(() => el.modal.hidden = true);
    await page.locator('.cardTitle').click();
    assert.deepEqual(await page.evaluate(() => navigations), [id]);
    assert.deepEqual(unexpected, [], 'network is isolated to the favorites endpoint; no knowledge access expansion');
    assert.deepEqual(errors, []);
    assert.deepEqual(requests.filter(r => r.method === 'POST').map(r => r.body), [
      {address: id, favorite: true}, {address: id, favorite: false},
      {address: other, favorite: true}, {address: other, favorite: false},
    ]);
    console.log('PASS: real primitives + favorites + HTTP client: late mounting, committed state, card/detail sync, click isolation, switch/close cleanup, reopen and denied mutation.');
    if (artifacts) console.log('Screenshots:', artifacts);
  } finally { await browser.close(); }
})().catch(error => {console.error(error); process.exitCode = 1;});
