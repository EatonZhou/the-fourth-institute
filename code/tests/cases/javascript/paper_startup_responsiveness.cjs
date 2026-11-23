// Real Paper page with deliberately pending catalog/analysis reads. A direct
// paper link must start its Reader without waiting for unrelated responses.
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), http = require('node:http');
const {chromium} = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const root = path.resolve(__dirname, '../../../management_portal');
const baseline = new Map();
if(process.env.PAPER_STARTUP_BASELINE) {
  for(const name of ['paper.js','paper_library.js','paper_reader.js']) baseline.set('/static/'+name,
    require('node:child_process').execFileSync('git',['show',`${process.env.PAPER_STARTUP_BASELINE}:code/management_portal/static/${name}`],{cwd:root}));
}
const groups = JSON.parse(fs.readFileSync(path.join(root, 'script_groups.json')));
const html = fs.readFileSync(path.join(root, 'paper.html'), 'utf8')
  .replace(/<script data-portal-script-group="([^"]+)"><\/script>/g,
    (_, key) => groups[key].map(src => `<script src="${src}"></script>`).join(''))
  .replace('<head>', '<head><script>window.PortalSite={apply(){}};window.__PORTAL_IDENTITY_BOOTSTRAP__={username:"fixture",authenticated:true,principal_id:"fixture"};</script>');
const paper = {id:'paper_0123456789ab', title:'Independent Reader', has_pdf:true, pdf_url:'/fixture.pdf', tags:[], authors:[]};
const server = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://fixture');
  if (url.pathname === '/paper') {res.setHeader('Content-Type','text/html');return res.end(html);}
  if (url.pathname === '/paper/reader-assets/reader.html') {
    res.setHeader('Content-Type','text/html');return res.end('<!doctype html><p>Document transport isolated</p>');
  }
  if (url.pathname.startsWith('/static/')) {
    if(url.pathname==='/static/portal_resource_routes.js') {
      res.setHeader('Content-Type','text/javascript');return res.end(fs.readFileSync(process.env.PORTAL_TEST_RESOURCE_ROUTES));
    }
    if(baseline.has(url.pathname)) {res.setHeader('Content-Type','text/javascript');return res.end(baseline.get(url.pathname));}
    const file = path.resolve(root, '.' + url.pathname);
    if (file.startsWith(root + path.sep) && fs.existsSync(file) && fs.statSync(file).isFile()) {
      res.setHeader('Content-Type', file.endsWith('.css') ? 'text/css' : file.endsWith('.js') ? 'text/javascript' : 'application/octet-stream');
      return res.end(fs.readFileSync(file));
    }
    res.statusCode=404;return res.end();
  }
  let data = {};
  if (url.pathname.endsWith('/me')) data={username:'fixture',user:{username:'fixture'},authenticated:true,principal_id:'fixture'};
  if (url.pathname.endsWith('/papers/detail')) data={paper,loaded_sections:['base']};
  if (url.pathname.endsWith('/semantic-searches')) data={searches:[]};
  if (url.pathname.endsWith('/collector-jobs')) data={jobs:[]};
  if (url.pathname.includes('navigation')) data={modules:[]};
  res.setHeader('Content-Type','application/json');res.end(JSON.stringify(data));
});
(async () => {
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const browser=await chromium.launch({headless:true});
  try {
    for (const catalogFails of [false,true]) {
      const page=await browser.newPage();page.setDefaultTimeout(5000);
      const errors=[];page.on('pageerror',error=>errors.push(error.message));
      page.on('dialog',async dialog=>{errors.push(dialog.message());await dialog.dismiss();});
      const requests=[];page.on('request',request=>{if(!request.url().includes('/static/'))requests.push(new URL(request.url()).pathname+new URL(request.url()).search);});
      let releaseCatalog, releaseAnalysis, catalogRequested=false, analysisRequested=false;
      const catalogGate=new Promise(resolve=>releaseCatalog=resolve), analysisGate=new Promise(resolve=>releaseAnalysis=resolve);
      await page.route('**/paper/api/library?*',async route=>{
        catalogRequested=true;await catalogGate;
        await route.fulfill({status:catalogFails?503:200,contentType:'application/json',body:JSON.stringify(catalogFails
          ? {error:'Catalog temporarily unavailable'}
          : {papers:[paper],favorite_ids:[],filters:{years:[],venues:[],tags:[],projects:[]},folders:[],summary:{paper_count:1,visible_count:1,analysis_count:0,review_count:0}})});
      });
      await page.route('**/paper/api/papers/detail?*',async route=>{
        const url=new URL(route.request().url());
        if(url.searchParams.get('sections')==='base')return route.continue();
        analysisRequested=true;await analysisGate;
        await route.fulfill({contentType:'application/json',body:JSON.stringify({paper,analysis:{body:'Analysis arrived'},summary:{content:''},loaded_sections:['analysis','summary']})});
      });
      try {
        await page.goto(`http://127.0.0.1:${server.address().port}/paper?paper=${paper.id}`,{waitUntil:'domcontentloaded'});
        await page.waitForSelector('.paperReadingDialog iframe');
        assert(catalogRequested && analysisRequested,'background requests start while Reader becomes usable');
        await page.evaluate(()=>window.originalReader=document.querySelector('.paperReadingDialog iframe'));
        releaseAnalysis();
        await page.waitForFunction(()=>document.querySelector('#analysisReaderContent')?.textContent.includes('Analysis arrived'));
        await page.evaluate(()=>window.originalPanels=document.querySelector('.readerGrid'));
        await page.evaluate(()=>{window.reportedErrors=[];const original=PortalUI.alertError;PortalUI.alertError=(error,...args)=>{reportedErrors.push(error.stack||String(error));return original(error,...args);};});
        releaseCatalog();
        await page.waitForFunction(failed=>failed
          ? document.querySelector('#libraryMeta')?.textContent.includes('Catalog temporarily unavailable')
          : document.querySelector('#libraryMeta')?.textContent.includes('1'),catalogFails);
        await page.waitForTimeout(150);
        assert(await page.evaluate(()=>originalReader===document.querySelector('.paperReadingDialog iframe')), 'late data must retain the Reader document');
        assert(await page.evaluate(()=>originalPanels===document.querySelector('.readerGrid')), 'catalog settlement must not rebuild the open paper');
        assert.deepEqual(errors,[]);
        console.log(`PASS direct Reader starts before catalog/analysis; catalog ${catalogFails?'failure':'success'} retains document and panels`);
      } catch(error) {
        console.error(JSON.stringify({errors,reported:await page.evaluate(()=>window.reportedErrors),requests,header:await page.locator('#libraryMeta').innerText(),workspace:await page.locator('#workspaceMain').innerText(),dialogs:await page.locator('.paperReadingDialog').count()}));
        throw error;
      } finally {releaseCatalog();releaseAnalysis();await page.close();}
    }
  } finally {await browser.close();server.closeAllConnections();server.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
