'use strict';
// Real Markdown, link binding, browser clicks and navigation; only HTTP data is a fixture.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const { chromium } = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const routeScript = fs.readFileSync(0, 'utf8');
const base = path.resolve(__dirname, '../../../management_portal/static');
const id = 'article_0123456789ab';
const nested = id + '/file/main.tex';
const scripts = ['portal_state', 'portal_text', 'portal_i18n', 'portal_ui_primitives',
  'portal_preview_contract', 'portal_resource_chips', 'markdown'];
const html = '<!doctype html><meta charset="utf-8"><main id="chat"></main><output id="notice"></output>' +
  '<script src="/routes.js"></script>' + scripts.map(name => `<script src="/static/${name}.js"></script>`).join('') +
  `<script>
window.previews=[];
window.PortalUI={toast:message=>document.getElementById('notice').textContent=message};
window.PortalAPI={raw:(url,options)=>fetch(url,options),get:async url=>{
  const response=await fetch(url), value=await response.json();
  if(!response.ok) throw Object.assign(new Error(value.error),{status:response.status,data:value});
  return value;
}};
window.renderLinks=markdown=>{
  const chat=document.getElementById('chat');
  chat.innerHTML=MD.render(markdown);
  PortalResourceChips.enhance(chat,{onResourcePreview:async request=>{
    previews.push({address:request.address,document:await request.load()});return true;
  }});
};
</script>`;

(async () => {
  let denied = false, browser;
  const requests = [], errors = [];
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://fixture');
    requests.push({path:url.pathname,address:url.searchParams.get('address')});
    if (url.pathname === '/routes.js') {
      res.setHeader('Content-Type', 'text/javascript'); return res.end(routeScript);
    }
    if (url.pathname.startsWith('/static/')) {
      res.setHeader('Content-Type', 'text/javascript');
      return res.end(fs.readFileSync(path.join(base, path.basename(url.pathname))));
    }
    if (url.pathname === '/api/resources/reference') {
      const address = url.searchParams.get('address');
      res.writeHead(denied ? 403 : 200, {'Content-Type':'application/json'});
      return res.end(JSON.stringify(denied ? {error:'无权读取'} : {
        resource_type:'article',title:'Fixture paper',content_kind:'text',media_type:'text/plain',filename:'main.tex',
        url: address === id ? '/resource/article?item=' + id : '/api/resources/content?address=' + encodeURIComponent(address),
      }));
    }
    if (url.pathname === '/api/resources/content') {
      res.setHeader('Content-Type','text/plain'); return res.end('\\documentclass{article}\n');
    }
    if (url.pathname.startsWith('/r/')) {
      res.writeHead(302, {Location:'/resource/article?item=' + id}); return res.end();
    }
    res.setHeader('Content-Type','text/html; charset=utf-8');
    res.end(url.pathname === '/resource/article' ? '<h1>Article editor</h1>' : html);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  try {
    browser = await chromium.launch({headless:true});
    const page = await browser.newPage();
    page.setDefaultTimeout(6000);
    page.on('pageerror', error => errors.push(error.message));
    const origin = 'http://127.0.0.1:' + server.address().port;
    const reset = async markdown => {
      requests.length = 0;
      await page.goto(origin + '/lineage');
      await page.evaluate(markdown => renderLinks(markdown), markdown);
    };
    for (const [markdown, selector, suffix] of [
      ['[打开论文继续编辑](' + id + ')', '#chat a', ''],
      ['[论文](/resource/article?item=' + id + '&tab=source#editor)', '#chat a', '&tab=source#editor'],
      [id, '#chat .portalResourceChip', ''],
    ]) {
      await reset(markdown);
      if (markdown.startsWith('[打开')) {
        assert.equal(await page.locator(selector).getAttribute('href'), '/r/' + id);
        assert.equal(await page.locator(selector).textContent(), '打开论文继续编辑');
      }
      await page.locator(selector).click();
      await page.waitForURL(origin + '/resource/article?item=' + id + suffix);
      assert.equal(requests.filter(r => r.path === '/api/resources/reference' && r.address === id).length, 1);
      assert.equal(requests.some(r => r.path === '/api/resources/content'), false, 'root Article never opens the raw TeX Preview');
    }
    await reset('[新标签打开](' + id + ')');
    const popupEvent = page.context().waitForEvent('page');
    await page.locator('#chat a').click({modifiers:['Control']});
    const popup = await popupEvent;
    await popup.waitForURL(origin + '/resource/article?item=' + id);
    await popup.close();
    assert.equal(new URL(page.url()).pathname, '/lineage');

    await reset('[查看源文件](' + nested + ')');
    await page.locator('#chat a').click();
    await page.waitForFunction(() => previews.length === 1);
    assert.equal(await page.evaluate(() => previews[0].address), nested);
    assert.match(await page.evaluate(() => previews[0].document.content), /documentclass/);
    assert.equal(new URL(page.url()).pathname, '/lineage');

    await reset('[打开论文](' + id + ')');
    // Even a successful hover cache cannot authorize a later click.
    await page.evaluate(id => PortalResourceChips.loadReference(id), id);
    denied = true;
    await page.locator('#chat a').click();
    await page.waitForFunction(() => document.getElementById('notice').textContent.includes('不可用'));
    assert.equal(new URL(page.url()).pathname, '/lineage');
    assert.equal(requests.filter(r => r.path === '/api/resources/reference' && r.address === id).length, 2);
    assert.equal(await page.evaluate(() => previews.length), 0);
    denied = false;

    await reset('[普通文件](main.tex) [外部地址](https://example.test/' + id + ')');
    assert.equal(await page.locator('[data-portal-resource-link-bound]').count(), 0);
    assert.deepEqual(errors, [], 'no unhandled browser errors');
    console.log('Article links: editing navigation, native modified click, nested preview and denied access passed.');
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => {console.error(error);process.exitCode=1;});
