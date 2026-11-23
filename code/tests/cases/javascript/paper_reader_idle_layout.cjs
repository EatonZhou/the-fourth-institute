// Real built Reader shell; isolate document I/O so this measures host layout,
// not PDF parsing, a model request, or a user's annotations.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const {chromium} = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const root = path.resolve(__dirname, '../../../management_portal');
const html = `<!doctype html><link rel="stylesheet" href="/static/paper.css">
<style>#host{position:absolute;left:40px;top:80px;width:700px;height:450px}</style>
<div id="host"></div><script>window.PortalI18n={t:s=>s};</script>
<script type="module">
import {openReader} from '/static/paper_fullscreen_reader.js';
window.current=true;
window.reader=openReader({paper:{id:'paper_0123456789ab',title:'Layout fixture',pdf_url:'/fixture.pdf'},
 principal:'fixture',chat:{width:()=>320},container:()=>document.querySelector('#host'),isCurrent:()=>current});
window.ready=true;
</script>`;
const server = http.createServer((req, res) => {
  const pathname = new URL(req.url, 'http://fixture').pathname;
  if (pathname === '/') {res.setHeader('Content-Type', 'text/html'); return res.end(html);}
  if (pathname === '/paper/reader-assets/reader.html') {
    // The transport never receives a native ready event. No read/write API runs.
    res.setHeader('Content-Type', 'text/html'); return res.end('<!doctype html><p>Document I/O isolated</p>');
  }
  const file = path.resolve(root, '.' + pathname);
  if (!file.startsWith(root + path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) {res.statusCode=404;return res.end();}
  res.setHeader('Content-Type', file.endsWith('.css') ? 'text/css' : 'text/javascript');res.end(fs.readFileSync(file));
});
(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({headless:true});
  try {
    const page = await browser.newPage();
    await page.goto(`http://127.0.0.1:${server.address().port}`);
    await page.waitForFunction(() => window.ready && document.querySelector('iframe'));
    await page.waitForTimeout(200);
    await page.evaluate(() => {window.originalFrame=document.querySelector('iframe');});
    const cdp = await page.context().newCDPSession(page);
    await cdp.send('Performance.enable');
    async function idle(label) {
      await page.evaluate(() => {
        window.mutationCount=0;
        window.observer=new MutationObserver(records=>mutationCount+=records.length);
        observer.observe(document.querySelector('dialog'), {attributes:true});
      });
      const before = await cdp.send('Performance.getMetrics');
      await page.waitForTimeout(600);
      const after = await cdp.send('Performance.getMetrics');
      const mutations=await page.evaluate(()=>{observer.disconnect();return mutationCount;});
      const delta=name=>after.metrics.find(m=>m.name===name).value-before.metrics.find(m=>m.name===name).value;
      const sample={label,mutations,layouts:delta('LayoutCount'),styleRecalculations:delta('RecalcStyleCount')};
      console.log(JSON.stringify(sample));
      assert.equal(mutations,0,`${label}: a stationary Reader must not mutate its DOM every animation frame`);
      assert.equal(sample.layouts,0,`${label}: a stationary Reader must not force layout`);
    }
    await idle('embedded');
    await page.evaluate(()=>{Object.assign(document.querySelector('#host').style,{left:'120px',width:'560px'});});
    await page.waitForFunction(()=>document.querySelector('dialog').style.left==='120px' && document.querySelector('dialog').style.width==='560px');
    await page.evaluate(()=>reader.setFullscreen(true));
    await page.waitForFunction(()=>document.querySelector('dialog').style.width==='100vw');
    await idle('fullscreen');
    await page.evaluate(()=>current=false);
    await page.waitForFunction(()=>document.querySelector('dialog').hidden);
    await idle('hidden');
    await page.evaluate(()=>{current=true;reader.setFullscreen(false);});
    await page.waitForFunction(()=>!document.querySelector('dialog').hidden && document.querySelector('dialog').style.width==='560px');
    assert(await page.evaluate(()=>originalFrame===document.querySelector('iframe')), 'layout changes must preserve the Reader instance');
    await page.evaluate(()=>document.querySelector('#host').remove());
    await page.waitForFunction(()=>document.querySelector('dialog').hidden);
    console.log('PASS Reader idle layout, resize, fullscreen, hidden mode, same iframe and detached host');
  } finally {await browser.close();server.closeAllConnections();server.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
