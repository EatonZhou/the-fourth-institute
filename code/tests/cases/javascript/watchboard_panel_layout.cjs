'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
(async () => {
  const browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 900}});
    await page.setContent(`<div class="portalChatWorkspaceRoot" style="width:320px;height:600px">
      <div class="chatWorkspaceBody hasWatchboard"><div class="paneBody"><p>Forked from a long source title. Read this inherited conversation.</p></div>
      <div class="watchboardSplitter open"></div><aside class="watchboard open"><div style="height:400px">Watchboard controls</div></aside></div></div>`);
    for (const file of ['portal_components.css', 'lineage_workbench.css']) {
      await page.addStyleTag({content: fs.readFileSync(path.resolve(__dirname, '../../../management_portal/static', file), 'utf8')});
    }
    const geometry = () => page.evaluate(() => {
      const box = s => {const r=document.querySelector(s).getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height};};
      return {message:box('.paneBody'),board:box('.watchboard'),splitter:box('.watchboardSplitter')};
    });
    let g = await geometry();
    assert.ok(g.message.width >= 300 && g.message.height >= 250, JSON.stringify(g));
    assert.ok(g.board.y >= g.message.y + g.message.height - 1, 'narrow panel stacks below readable messages');
    assert.equal(g.splitter.width,0);
    await page.locator('.watchboard').evaluate(n => n.classList.add('collapsed'));
    g=await geometry();assert.ok(g.board.height <= 38 && g.message.height >= 500);
    await page.locator('.watchboard').evaluate(n => n.classList.remove('collapsed'));
    await page.locator('.portalChatWorkspaceRoot').evaluate(n => n.style.width='1000px');
    g=await geometry();assert.ok(g.board.x >= g.message.x+g.message.width && g.message.width>=600,JSON.stringify(g));
    assert.equal(g.board.y,g.message.y);
    await page.locator('.portalChatWorkspaceRoot').evaluate(n => n.style.width='320px');
    g=await geometry();assert.ok(g.message.width>=300 && g.board.y>g.message.y);
    console.log('Watchboard panel resize, collapse and readable message layout passed');
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
