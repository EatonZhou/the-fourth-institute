'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const root = path.resolve(__dirname, '../../../management_portal/static');

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 700, height: 800 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.setContent('<div class="home-overview-section" style="height:650px"><div data-daily-news></div></div>');
    await page.evaluate(() => { window.PortalCompatibility = { identifier: value => value }; });
    for (const file of ['portal_themes.css', 'home.css']) await page.addStyleTag({ path: path.join(root, file) });
    for (const file of ['portal_text.js', 'portal_i18n.js', 'portal_ui_primitives.js', 'markdown.js', 'home_pulse.js']) await page.addScriptTag({ path: path.join(root, file) });
    await page.evaluate(() => {
      window.PortalUI = { errorMessage: (_label, error) => error.message };
      window.calls = [];
      window.replies = {};
      window.mode = 'ok';
      const editions = [{id:'x', title:'X 资讯'}, {id:'embodied', title:'具身智能'}];
      const x = '# X 日报\n\n采集说明，不应挤占摘要。\n\n' + Array.from({length: 7}, (_,i) => `## ${i+1}. 新闻 ${i+1}：事实与影响\n\n- 事实要点：详情 ${i+1}\n- 与我们的关系（判断）：讨论 ${i+1}\n`).join('\n');
      const embodied = '# 具身日报\n\n## 今日摘要\n\n- **机器人新进展**，已有实际演示。\n\n## 1. 详细信息\n\n原文与讨论。';
      window.pulse = window.PortalHomePulse.createPulse({
        esc: value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),
        formatPortalTime: value => value,
        homeApi: { get: async url => {
          calls.push(url);
          const edition = url.includes('edition=embodied') ? 'embodied' : 'x';
          const result = {status: mode, edition, editions, date:'2026-11-13', content: edition === 'x' ? x : embodied};
          if (window.delay) return new Promise(resolve => { replies[edition] = () => resolve(result); });
          return result;
        } },
      });
      return pulse.loadDailyNews();
    });
    assert.deepEqual(await page.evaluate(() => calls), ['/api/daily-news']);
    assert.equal(await page.locator('[aria-selected="true"]').textContent(), 'X 资讯');
    assert.equal(await page.locator('.daily-news-summary li').count(), 5);
    assert.equal(await page.locator('.daily-news-details').getByText('与我们的关系（判断）：讨论 7', {exact:false}).count(), 1);
    const toolbar = await page.locator('.daily-news-toolbar').boundingBox();
    const tabs = await page.locator('.daily-news-editions').boundingBox();
    assert.equal(tabs.x, toolbar.x);
    const summary = await page.locator('.daily-news-summary').boundingBox();
    const details = await page.locator('.daily-news-details').boundingBox();
    assert(summary.y < details.y);
    await page.locator('[data-daily-news-jump]').first().click();
    await page.locator('[data-daily-news-edition="embodied"]').click();
    await page.waitForFunction(() => document.querySelector('.daily-news-summary').textContent.includes('机器人新进展'));
    assert.equal(await page.locator('.daily-news-summary strong').textContent(), '机器人新进展');
    assert(!await page.locator('.daily-news-details').textContent().then(t => t.includes('已有实际演示')));
    await page.evaluate(() => { delay = true; });
    await page.locator('[data-daily-news-edition="x"]').click();
    await page.locator('[data-daily-news-edition="embodied"]').click();
    await page.evaluate(() => replies.embodied());
    await page.evaluate(() => replies.x());
    assert.equal(await page.locator('[aria-selected="true"]').textContent(), '具身智能');
    await page.evaluate(() => { delay = false; mode = 'missing'; return pulse.loadDailyNews(); });
    assert.equal(await page.locator('[data-daily-news-edition]').count(), 2);
    await page.evaluate(() => { mode = 'error'; return pulse.loadDailyNews(); });
    assert.equal(await page.locator('.daily-news-error').count(), 1);
    assert.equal(await page.locator('[data-daily-news-edition]').count(), 2);
    await page.setViewportSize({width: 360, height: 740});
    await page.evaluate(() => { mode = 'ok'; return pulse.loadDailyNews(); });
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    assert.deepEqual(errors, []);
    console.log('PASS: default X, top-left categories, summary/details, summary links, switching, race, missing/error, narrow layout');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
