'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const repo = path.resolve(__dirname, '../../..');
const { chromium } = require(path.join(repo, 'management_portal/test_framework/tau_runtime/node_modules/playwright'));

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1000, height: 600 } });
    await page.setContent('<style>body{margin:0}header{height:20px}#meta{height:0;overflow:hidden}#empty{height:800px;padding-top:100px;box-sizing:border-box}</style>'
      + '<header></header><div id="meta"></div><div id="grid"></div><div id="empty">No matching resources</div>');
    await page.addStyleTag({ content: fs.readFileSync(path.join(repo, 'management_portal/static/knowledge_board.css'), 'utf8') });
    await page.evaluate(() => {
      window.el = Object.fromEntries(['meta', 'empty', 'grid'].map(id => [id, document.getElementById(id)]));
      window.state = { raw: { items: [] }, indexPaging: {}, exactQuery: {}, knowledgeMap: {} };
      window.filter = {};
      window.PortalI18n = { t: value => value, text: (parts, ...args) => String.raw({ raw: parts }, ...args) };
      window.matchesFilter = window.matchesQuery = () => true;
    });
    await page.addScriptTag({ content: fs.readFileSync(path.join(repo, 'management_portal/static/knowledge_board_render.js'), 'utf8') });
    await page.evaluate(() => {
      // Force browser layout while committing the replacement, as the real
      // virtual renderer does when calculating the visible project window.
      window.renderProjects = items => {
        document.documentElement.getBoundingClientRect();
        el.grid.style.height = items.length ? '1000px' : '0px';
        document.documentElement.getBoundingClientRect();
      };
      window.renderKnowledgeRadar = window.syncKnowledgeView = window.syncKnowledgeMatrixSelection = () => {};
      renderAll();
      scrollTo(0, 50);
    });
    for (const items of [[{ kb_id: 'loaded-page-result' }], []]) {
      // Paging arrives after the browser has selected an anchor for the old
      // content, rather than in the same frame as the history scroll.
      await page.waitForTimeout(150);
      const result = await page.evaluate(async items => {
        state.raw.items = items;
        renderAll();
        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
        return { y: scrollY, emptyVisible: getComputedStyle(el.empty).display !== 'none' };
      }, items);
      assert.equal(result.y, 50, items.length ? 'paged cards preserve the restored position' : 'empty result preserves the position');
      assert.equal(result.emptyVisible, !items.length);
    }
    console.log('Knowledge content transitions preserve browser scroll in both directions.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
