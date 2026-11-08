'use strict';
// Actual search, overlay, resource reader, Home Preview host and document tabs.
// Only network resource records and the unrelated Chat are isolated fixtures.
const fs = require('node:fs'), path = require('node:path'), http = require('node:http');
const assert = require('node:assert/strict');
const { chromium } = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const root = path.resolve(__dirname, '../../../management_portal');
const groups = JSON.parse(fs.readFileSync(path.join(root, 'script_groups.json')));
const scripts = ['portal_state.js', 'portal_page_state.js', 'portal_text.js', 'portal_translations.js', 'portal_i18n.js',
  'portal_ui_primitives.js', 'portal_ui.js', 'portal_overlay.js', 'portal_form_controls.js', 'portal_command_registry.js',
  'portal_resource_routes.js', 'portal_preview_contract.js', 'portal_resource_chips.js',
  'portal_preview_runtime.js', 'portal_pdf_preview.js', 'portal_preview_renderers.js', 'portal_document_statistics.js',
  ...groups['portal-workspace-surface'].map(x => x.replace('/static/', '')),
  'portal_agent_preview_source.js', 'markdown.js', 'portal_display_protocol.js',
  'home_dialogue_state.js', 'portal_file_preview.js', 'home_dialogue.js', 'portal_command_palette.js', 'portal_resource_favorites.js',
  'portal_navigation.js', 'lineage_workbench_sidebar_favorites.js'];
const css = [...fs.readFileSync(path.join(root, process.env.FAVORITES_LAYOUT_ONLY ? 'lineage.html' : 'index.html'), 'utf8').matchAll(/href="(\/static\/[^"?]+\.css)"/g)].map(x => x[1]);
if (process.env.FAVORITES_LAYOUT_ONLY) css.push('/static/home_dialogue.css');
const records = [
  { resource_id: 'thinking_111111111111', title: '第一条检索结果' },
  { resource_id: 'thinking_222222222222', title: '第二条检索结果：面向多场景协作的知识整理与文件浏览设计，长中文标题在窄侧栏中仍然可以阅读' },
  { resource_id: 'resident_333333333333', title: '仅导航的 Resident' },
].map(r => ({ ...r, resource_type: r.resource_id.split('_')[0], address: r.resource_id,
  summary: '隔离测试数据，不读取真实用户资源', url: '/destination/' + r.resource_id,
  content_kind: 'markdown', content_type: 'text/markdown', version: '1', capabilities: { edit: false, save: false } }));
let deny = false;
const reads = [];
const favorites = new Set();
const extraFavorites = [
  { address: 'lineage_aaaaaaaaaaaa', resource_type: 'lineage', title: '组织收藏' },
  { address: 'project_aaaaaaaaaaaa', resource_type: 'project', title: '项目收藏' },
  { address: 'paper_aaaaaaaaaaaa', resource_type: 'paper', title: '论文收藏' },
  { address: 'workbench_aaaaaaaaaaaa', resource_type: 'workbench', title: '工作台收藏' },
  { address: 'workspace_aaaaaaaaaaaa/file/notes.md', resource_type: 'workspace', title: '工作区文件' },
  { address: 'thinking_111111111111/file/notes.md', title: '知识条目附属文件', status: 'available' },
  { address: 'decision_aaaaaaaaaaaa', status: 'denied' },
];
const server = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://fixture');
  const json = value => { res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(value)); };
  if (url.pathname === '/api/resources/favorites') {
    if (req.method === 'POST') {
      let body = ''; req.on('data', chunk => body += chunk);
      req.on('end', () => { const value = JSON.parse(body); if (value.favorite) favorites.add(value.address); else favorites.delete(value.address); json(value); });
      return;
    }
    return json({ items: [...records, ...extraFavorites].filter(r => favorites.has(r.address)).map(r => ({ status: 'available', ...r })) });
  }
  if (url.pathname === '/api/resource-index/status') return json({ resource_types: ['thinking', 'resident'] });
  if (url.pathname === '/api/resource-index/suggest') return json({ resources: records });
  if (url.pathname === '/api/resources/reference') return json(records.find(r => r.address === url.searchParams.get('address')));
  if (url.pathname === '/api/resources/content') {
    const address = url.searchParams.get('address'); reads.push(address);
    if (deny) { res.statusCode = 403; return json({ error: '当前来源不可读' }); }
    res.setHeader('Content-Type', 'text/markdown');
    return res.end('# ' + records.find(r => r.address === address).title + '\n\n预览正文来自所选资源 ' + address);
  }
  if (url.pathname.startsWith('/destination/')) { res.setHeader('Content-Type', 'text/html'); return res.end('原有页面导航'); }
  if (url.pathname === '/static/portal_resource_routes.js') {
    res.setHeader('Content-Type', 'text/javascript');
    return res.end('window.PortalResourceRoutes={schema:"tfi.resource-portal-routes.v1",routes:[{resource_type:"thinking",detail_kind:"knowledge",path:"/resource/knowledge",parameter:"item"},{resource_type:"decision",detail_kind:"knowledge",path:"/resource/knowledge",parameter:"item"}],text_resource_types:["thinking","decision"],navigation_resource_types:["resident"],identity_parameters:[]};');
  }
  if (url.pathname.startsWith('/static/')) {
    const file = path.join(root, url.pathname);
    if (fs.existsSync(file)) { res.setHeader('Content-Type', file.endsWith('.css') ? 'text/css' : 'text/javascript'); return res.end(fs.readFileSync(file)); }
    res.statusCode = 404; return res.end();
  }
  res.setHeader('Content-Type', 'text/html');
  res.end('<!doctype html><html data-portal-theme="light"><meta charset="utf-8">' + css.map(s => `<link rel="stylesheet" href="${s}">`).join('')
    + '<style>body{margin:0}#root{height:90vh;margin:16px}</style>'
    + scripts.map(s => `<script src="/static/${s}"></script>`).join('') + '<main id="root"></main>');
});
(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({ headless: true });
  const output = process.env.SEARCH_PREVIEW_EVIDENCE;
  if (output) fs.mkdirSync(output, { recursive: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    page.setDefaultTimeout(8000);
    const errors = []; page.on('pageerror', e => { errors.push(e.message); console.error('pageerror', e.message); });
    const url = 'http://127.0.0.1:' + server.address().port;
    const prepare = async () => {
      await page.goto(url);
      await page.evaluate(() => {
        PortalI18n.apply('original');
        window.PortalIdentity = { peek: () => ({ username: 'isolated-fixture' }) };
        window.PortalResidentService = {};
        window.PortalAPI = { post: async (url, body) => {
          if (url !== '/api/resources/favorites') throw new Error('Unexpected write');
          return (await fetch(url, { method: 'POST', body: JSON.stringify(body), headers: { 'Content-Type': 'application/json' } })).json();
        }, raw: (url, options) => fetch(url, options), get: async (url, options) => { const response = await fetch(url, options); if (!response.ok) throw new Error('当前来源不可读'); return response.json(); } };
        window.PortalNav = { personalAssistantTarget: async () => ({ resident_id: 'resident_444444444444' }) };
        window.PortalChatWorkspace = { panelPackage: { create: ({ root }) => { root.textContent = 'Chat（隔离测试）'; return { setPresented() {}, destroy() {} }; } } };
        window.controller = PortalHomeDialogue.connect({ root: document.querySelector('#root'), context: { currentUserReady: Promise.resolve() } });
      });
    };
    const search = async () => {
      await page.evaluate(() => PortalCommandPalette.open());
      await page.locator('#portalCommandInput').fill('检索结果');
      await page.locator('[data-command-index="2"]').waitFor();
    };
    const menu = page.locator('[data-preview-file-action="new-tab"]');
    const tabs = page.locator('.homeDialoguePreview [data-portal-document-tab]');
    if (!process.env.FAVORITES_LAYOUT_ONLY) {
    await prepare();
    await page.evaluate(address => PortalPreview.request({ resourceAddress: address }), records[0].address);
    await page.waitForFunction(() => [...document.querySelectorAll('.portalFileDocument')].some(n => n.textContent.includes('预览正文来自所选资源')))
      .catch(async error => { console.error((await page.locator('body').innerText()).slice(-2400)); throw error; });
    await search();
    await page.locator('[data-command-index="1"]').click({ button: 'right' });
    // The shared floating menu must sit above the search Dialog, with Escape closing it first.
    await menu.waitFor();
    assert.equal(await menu.textContent(), '在preview里打开标签页');
    await page.keyboard.press('Escape');
    assert.equal(await menu.isVisible(), false);
    assert.equal(await page.locator('#portalCommandOverlay').isVisible(), true);
    await page.locator('[data-command-index="1"]').click({ button: 'right' });
    if (output) await page.screenshot({ path: path.join(output, 'search-context-menu.png') });
    await menu.click();
    await page.waitForFunction(() => document.querySelector('.portalFileDocument:not([hidden])')?.textContent.includes('thinking_222222222222')
      || [...document.querySelectorAll('.portalFileDocument')].some(n => n.getClientRects().length && n.textContent.includes('thinking_222222222222'))).catch(async error => { console.error('reads', reads, (await page.locator('body').innerText()).slice(-2500)); throw error; });
    assert.equal(await tabs.count(), 2, 'new tab retains the existing document');
    assert.equal(await page.locator('#portalCommandOverlay').isVisible(), false);
    assert.deepEqual(reads.slice(0, 2), records.slice(0, 2).map(r => r.address));
    if (output) await page.screenshot({ path: path.join(output, 'search-preview-tab.png') });
    // Navigation-only resources keep the browser's ordinary context behavior.
    await search();
    await page.locator('[data-command-index="2"]').click({ button: 'right' });
    assert.equal(await menu.isVisible(), false);
    await page.mouse.move(10, 10);
    await page.locator('#portalCommandInput').press('ArrowUp');
    await Promise.all([page.waitForURL('**/destination/thinking_222222222222'), page.locator('#portalCommandInput').press('Enter')]);
    await prepare(); await search();
    await Promise.all([page.waitForURL('**/destination/thinking_111111111111'), page.locator('[data-command-index="0"]').click()]);
    // Exact-ID results use the same path; denial shows the host's existing error tab.
    await prepare();
    await page.evaluate(() => PortalCommandPalette.open());
    await page.locator('#portalCommandInput').fill(records[1].address);
    await page.locator('[data-command-index="0"]').waitFor();
    assert.equal(await page.locator('[data-command-index]').count(), 1);
    deny = true;
    await page.locator('[data-command-index="0"]').click({ button: 'right' }); await menu.click();
    await page.locator('.portalFilePreviewFailure').waitFor();
    assert.equal(await page.locator('.portalFileDocument:visible').count(), 0, 'denial never renders resource content');
    assert((await page.locator('.portalFilePreviewFailure').innerText()).includes('当前账号未获准读取这个文件'));
    }
    // The shared favorites owner: search star -> persistence -> My Favorite -> real Preview.
    deny = false;
    const retained = [records[2].address, ...extraFavorites.map(r => r.address)];
    retained.forEach(address => favorites.add(address));
    await prepare(); await search();
    assert.equal(await page.locator('#portalCommandList [data-resource-favorite="resident_333333333333"] button').count(), 0, 'navigation resources have no generic star');
    const star = page.locator('#portalCommandList [data-resource-favorite="thinking_222222222222"] button');
    await star.click();
    await page.waitForFunction(() => document.querySelector('#portalCommandList [data-resource-favorite="thinking_222222222222"] button')?.getAttribute('aria-pressed') === 'true');
    assert.equal(await page.locator('#portalCommandOverlay').isVisible(), true, 'star does not navigate');
    await prepare(); // reload, without seeding browser favorite state
    if (!process.env.FAVORITES_LAYOUT_ONLY) await page.addStyleTag({path: path.join(root, 'static/lineage_workbench.css')});
    await page.evaluate(() => {
      // Use the production Navigation pane, header, scroll container and favorites controller.
      const root = document.createElement('aside'); root.id = 'navigationHost';
      root.style.cssText = 'position:absolute;left:16px;top:16px;width:310px;height:calc(100vh - 32px);z-index:10';
      document.body.append(root);
      document.querySelector('#root').style.marginLeft = '342px';
      window.navigation = PortalNavigation.mount({ root, variant: 'lineage', activeView: 'favorites' });
      const factory = PortalLineageWorkbenchSidebarFavorites;
      const dependencies = Object.fromEntries(factory.dependencies.map(name => [name, () => {}]));
      window.PortalPrincipalNavigation = { projectGroups: () => [{ title: '示例项目' }] };
      Object.assign(dependencies, { $id: id => document.getElementById(id), esc: x => x,
        state: { favoriteAgentIds: new Set(['project:示例项目']), favoriteStatuses: {}, favoriteGroups: {favorite_lineage: false} },
        writeLineageUiState: value => { window.savedFavoriteState = JSON.parse(JSON.stringify(value)); },
        lineageSidebarRoots: () => [], projectFavoriteSelectionId: () => 'project:示例项目', isFavoriteAgent: () => true,
        residentNavigationShortcutsHtml: () => '<div class="whichResidentWrap"><div class="agentItem"><div class="agentSelect"><span class="agentMain"><span class="agentTitle">Which Resident</span><span class="agentMeta">Resident</span></span></div></div></div><div class="agentItem personalAssistantResident active"><div class="agentSelect"><span class="agentMain"><span class="agentTitle">Ask Me Anything | My Lineage</span><span class="agentMeta">Resident</span></span></div></div>',
        projectCardHtml: () => '<div class="agentItem">示例项目</div>' });
      factory.createSidebarFavorites(dependencies).renderFavoriteList();
    });
    const entries = page.locator('[data-resource-favorites] .resourceFavoriteOpen');
    await page.waitForFunction(() => document.querySelectorAll('[data-resource-favorites] .resourceFavoriteOpen').length === 3);
    assert.equal(await page.locator('[data-resource-favorite-count]').innerText(), '3');
    assert.deepEqual(await page.locator('#favoriteAgentList .residentGroupName').allTextContents(), ['Projects 与 Lineages', 'Knowledge']);
    assert.equal(await entries.filter({hasText: 'decision_aaaaaaaaaaaa'}).isDisabled(), true, 'revoked knowledge can still be removed');
    assert.equal(await entries.filter({hasText: '知识条目附属文件'}).count(), 1, 'nested knowledge file follows root identity');
    assert.equal(await entries.filter({hasText: '工作区文件'}).count(), 0);
    assert.deepEqual([...favorites], [...retained, records[1].address], 'classification does not mutate saved favorites');
    const saved = entries.filter({hasText: records[1].title});
    await saved.waitFor();
    assert((await saved.innerText()).includes(records[1].title));
    // Narrow Navigation and browser-layout scaling: no min-content expansion or clipped controls.
    // CSS zoom is explicit test scaling, not an assertion about the user's browser preference.
    for (const width of [310, 220]) for (const scale of [1, 1.25, 1.5]) {
      await page.evaluate(({width, scale}) => {
        document.body.style.zoom = scale;
        document.querySelector('#navigationHost').style.width = width + 'px';
        document.querySelector('#navigationHost').style.height = `calc(100vh / ${scale} - 32px)`;
      }, {width, scale});
      const geometry = await page.locator('#favoriteAgentList').evaluate(root => {
        const bounds = root.getBoundingClientRect();
        const title = root.querySelector('.resourceFavoriteTitle');
        const text = getComputedStyle(title);
        const headings = [...root.querySelectorAll('.residentGroupHead')].map(n => getComputedStyle(n));
        return {
          width: root.clientWidth, scroll: root.scrollWidth,
          titleFont: parseFloat(text.fontSize), residentFont: parseFloat(getComputedStyle(root.querySelector('.agentTitle')).fontSize),
          singleLine: Math.abs(title.getBoundingClientRect().height - parseFloat(text.lineHeight) * Number(document.body.style.zoom)) < 1,
          ellipsis: text.textOverflow === 'ellipsis' && text.whiteSpace === 'nowrap' && title.scrollWidth > title.clientWidth,
          normalWeight: text.fontWeight === '400',
          originalHeadings: headings.every(s => s.height === '22px' && s.backgroundColor === headings[0].backgroundColor),
          controlsFit: [...root.querySelectorAll('.resourceFavoriteButton')].every(n => {
            const r = n.getBoundingClientRect(); return r.width >= 28 && r.left >= bounds.left && r.right <= bounds.right;
          }),
        };
      });
      assert(geometry.scroll <= geometry.width + 1, JSON.stringify({width, scale, geometry}));
      assert.equal(geometry.titleFont, geometry.residentFont, 'knowledge and resident titles share font scale');
      assert(geometry.singleLine && geometry.ellipsis && geometry.normalWeight && geometry.controlsFit && geometry.originalHeadings, JSON.stringify(geometry));
      assert((await saved.getAttribute('title')).includes(records[1].title), 'hover retains the full title');
      assert((await saved.getAttribute('title')).includes('thinking'), 'type remains available on hover');
      const summary = page.locator('.resourceFavorites > summary');
      await summary.click(); assert.equal(await saved.isVisible(), false, 'Knowledge collapses');
      await summary.focus(); await page.keyboard.press('Enter'); assert.equal(await saved.isVisible(), true, 'keyboard reopens Knowledge');
      if (output && scale === 1.5 && width === 220) await page.screenshot({path: path.join(output, 'favorites-narrow-150.png')});
    }
    await page.evaluate(() => {
      document.body.style.zoom = '';
      document.querySelector('#navigationHost').style.width = '310px';
      document.querySelector('#navigationHost').style.height = 'calc(100vh - 32px)';
    });
    const projects = page.locator('[data-favorite-group="favorite_lineage"] > summary');
    const knowledge = page.locator('.resourceFavorites > summary');
    const marker = locator => locator.evaluate(node => getComputedStyle(node, '::before').content);
    await projects.click();
    await page.waitForFunction(() => window.savedFavoriteState?.favoriteGroups.favorite_lineage === true);
    assert.equal(await marker(projects), await marker(knowledge), 'expanded groups share the filled triangle');
    assert.equal(await page.locator('[data-favorite-group] .residentGroupBody').isVisible(), true);
    await knowledge.click();
    await projects.focus(); await page.keyboard.press('Enter');
    await page.waitForFunction(() => window.savedFavoriteState?.favoriteGroups.favorite_lineage === false);
    assert.equal(await marker(projects), await marker(knowledge), 'collapsed groups share the filled triangle');
    assert.equal(await saved.isVisible(), false, 'toggling Projects retains Knowledge collapse state');
    for (const summary of [projects, knowledge]) {
      const style = await summary.evaluate(node => {
        const s = getComputedStyle(node); return {line: s.borderBottomWidth, lineStyle: s.borderBottomStyle, color: s.borderBottomColor, background: s.backgroundColor};
      });
      assert.equal(style.line, '1px'); assert.equal(style.lineStyle, 'solid');
      assert.notEqual(style.color, style.background, 'visible separator under each group bar');
    }
    await knowledge.focus(); await page.keyboard.press('Space');
    await saved.waitFor();
    await saved.click();
    await page.waitForFunction(() => [...document.querySelectorAll('.portalFileDocument')].some(n => n.getClientRects().length && n.textContent.includes('thinking_222222222222')));
    const previewStar = page.locator('.portalPreviewFavorite button');
    await previewStar.waitFor();
    assert.equal(await previewStar.getAttribute('aria-pressed'), 'true');
    if (output) await page.screenshot({ path: path.join(output, 'knowledge-favorites-preview.png') });
    await previewStar.click();
    await page.waitForFunction(() => document.querySelectorAll('[data-resource-favorites] .resourceFavoriteOpen').length === 2);
    assert.deepEqual([...favorites], retained, 'unfavorite removes only selected knowledge, preserving other categories');
    // Without a Preview host, the bookmark opens the authorized resource detail URL.
    await page.evaluate(() => controller.destroy());
    await Promise.all([page.waitForURL('**/destination/thinking_111111111111'), page.evaluate(() => PortalResourceFavorites.open('thinking_111111111111'))]);
    assert.deepEqual(errors, []);
    console.log('PASS real Navigation/My Favorite host: 310/220px × 100/125/150% CSS zoom; original headings, normal-weight single-line ellipsis, full hover text, visible stars, no overflow, mouse/keyboard collapse; persisted Knowledge -> actual Preview -> unfavorite sync; retained other categories and permission states.');
  } finally { await browser.close(); await new Promise(resolve => server.close(resolve)); }
})().catch(error => { console.error(error); process.exitCode = 1; });
