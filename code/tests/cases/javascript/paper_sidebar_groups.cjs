const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path');
const {chromium} = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const root = path.resolve(__dirname, '../../../management_portal');
(async () => {
  const browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 420, height: 1000}});
    const html = fs.readFileSync(path.join(root, 'paper.html'), 'utf8');
    const {JSDOM} = require('jsdom');
    const source = new JSDOM(html).window.document;
    await page.setContent('<meta charset="utf-8"><div class="leftBody" style="width:280px"></div>');
    for (const link of source.querySelectorAll('link[rel=stylesheet]')) {
      if (link.getAttribute('href').startsWith('/static/')) await page.addStyleTag({path: path.join(root, link.getAttribute('href'))});
    }
    await page.locator('.leftBody').evaluate((el, markup) => {el.innerHTML = markup;}, source.querySelector('.searchWrap').outerHTML);
    await page.evaluate(() => {window.PortalI18n = {t: text => text};});
    for (const file of ['portal_ui_primitives.js', 'portal_form_controls.js', 'portal_board_toolbar.js']) await page.addScriptTag({path:path.join(root, 'static', file)});
    await page.evaluate(() => PortalBoardToolbar.mountAll());
    for (const summary of await page.locator('.paperSearchHistory > summary').all()) {
      assert.deepEqual(await summary.evaluate(el => {const s=getComputedStyle(el);return [s.fontSize,s.fontWeight,s.cursor];}), ['12px','400','pointer']);
      await summary.focus(); await page.keyboard.press('Enter');
      assert(await summary.evaluate(el => el.parentElement.open));
      await page.keyboard.press('Enter');
    }
    for (const selector of ['.collectorBox']) {
      assert.equal(await page.locator(selector).evaluate(el => getComputedStyle(el).borderTopWidth), '1px');
    }
    assert.equal(await page.locator('.filterGrid').evaluate(el=>getComputedStyle(el).borderTopWidth),'0px');
    assert(await page.locator('.paperLibraryQueryGroup').evaluate(el=>el.contains(document.getElementById('semanticSearchBtn')) && el.contains(document.getElementById('yearFilter')) && !el.contains(document.getElementById('collectorForm'))));
    await page.screenshot({path:'/tmp/paper-sidebar-groups-280.png'});
    const overflow = await page.locator('.leftBody').evaluate(el => [...el.querySelectorAll('*')].filter(child => child.getBoundingClientRect().right > el.getBoundingClientRect().right + 1).map(child => child.id || child.className));
    assert.deepEqual(overflow, [], '280px sidebar must not overflow horizontally');
    console.log('PASS 280px sidebar: separators, secondary history typography, keyboard disclosure, no overflow');
  } finally {await browser.close();}
})().catch(error => {console.error(error); process.exit(1);});
