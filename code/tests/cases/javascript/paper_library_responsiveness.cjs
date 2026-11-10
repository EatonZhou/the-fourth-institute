// Whole Paper page and shared UI, with isolated deterministic API responses.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),http=require('node:http');
const engines=require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const root=path.resolve(__dirname,'../../../management_portal');
const groups=JSON.parse(fs.readFileSync(path.join(root,'script_groups.json')));
const html=fs.readFileSync(path.join(root,'paper.html'),'utf8').replace(/<script data-portal-script-group="([^"]+)"><\/script>/g,(_,key)=>groups[key].map(src=>`<script src="${src}"></script>`).join(''));
const live=process.env.PAPER_LIBRARY_SNAPSHOT?JSON.parse(fs.readFileSync(process.env.PAPER_LIBRARY_SNAPSHOT)):null;
const detail=process.env.PAPER_DETAIL_SNAPSHOT?JSON.parse(fs.readFileSync(process.env.PAPER_DETAIL_SNAPSHOT)):null;
const papers=live?.papers||Array.from({length:4502},(_,i)=>({id:`paper_${i.toString(16).padStart(12,'0')}`,title:`Fixture paper ${i}`,year:String(2020+i%7),venue:`Fixture ${i%180}`,session:'General',authors:[],tags:['机器学习','具身智能','架构','计算','控制'],has_pdf:false}));
function foldersFor(rows){const venues=new Map();for(const p of rows){if(!venues.has(p.venue))venues.set(p.venue,new Map());const years=venues.get(p.venue);years.set(p.year,(years.get(p.year)||0)+1);}return [...venues].map(([name,years])=>({name,count:[...years.values()].reduce((a,b)=>a+b,0),children:[...years].map(([name,count])=>({name,count,children:[{name:'General',count}]}))}));}
const requests=[];
let favoriteCount=2;
let pdfDelay=0;
const server=http.createServer((req,res)=>{
  const url=new URL(req.url,'http://fixture');
  if(url.pathname==='/paper'){res.setHeader('Content-Type','text/html');res.end(html.replace('<head>','<head><script>window.PortalSite={apply(){}};window.__PORTAL_IDENTITY_BOOTSTRAP__={username:"fixture",principal:{kind:"user",id:"fixture"},access:{allows:{}}};</script>'));return;}
  if(url.pathname==='/fixture.pdf'){res.setHeader('Content-Type','application/pdf');const bytes=fs.readFileSync(process.env.PAPER_PDF_FIXTURE||path.join(root,'../tests/cases/fixtures/paper-synthetic-means.pdf'));setTimeout(()=>res.end(bytes),pdfDelay);return;}
  if(url.pathname==='/static/portal_resource_routes.js'){res.setHeader('Content-Type','text/javascript');res.end('window.PortalResourceRoutes={schema:"tfi.resource-portal-routes.v1",routes:[],text_resource_types:[],identity_parameters:[]};');return;}
  if(url.pathname.includes('/events')){res.writeHead(200,{'Content-Type':'text/event-stream'});res.write(': fixture\n\n');req.on('close',()=>res.end());return;}
  if(url.pathname.startsWith('/static/')){
    const file=path.join(root,url.pathname);if(!fs.existsSync(file)){res.statusCode=404;res.end();return;}
    res.setHeader('Content-Type',file.endsWith('.css')?'text/css':file.endsWith('.json')?'application/json':'text/javascript');res.end(fs.readFileSync(file));return;
  }
  requests.push(url.pathname+url.search);
  let data={};
  if(url.pathname==='/paper/api/library'){
    const rows=url.searchParams.has('favorite')?papers.slice(0,favoriteCount):papers;
    data={papers:rows,favorite_ids:papers.slice(0,2).map(p=>p.id),filters:{years:['2026'],venues:['Fixture'],tags:[],projects:[]},summary:{paper_count:papers.length,visible_count:rows.length,analysis_count:0,review_count:0},folders:foldersFor(rows)};
    if(live){data.filters=live.filters;if(!url.searchParams.has('favorite'))data.folders=live.folders;}
  } else if(url.pathname==='/paper/api/papers/detail') data={...detail,paper:{...(detail?.paper||papers[0]),id:papers[0].id,has_pdf:true,pdf_url:'/fixture.pdf'},analysis:detail?.analysis||{body:'# Fixture analysis\n\nReading the **fixture** paper.'},summary:{content:'Fixture summary'},loaded_sections:['base','analysis','summary']};
  else if(url.pathname.endsWith('/me')) data={username:'fixture',user:{username:'fixture'},authenticated:true};
  else if(url.pathname.endsWith('/semantic-searches')) data={searches:Array.from({length:20},(_,i)=>({search_id:`search_${i.toString(16).padStart(12,'0')}`,query:'具身智能相关论文 '+i,query_interpretation:'查找相关论文 '+i,status:'done',result_count:i}))};
  else if(url.pathname.endsWith('/collector-jobs')) data={jobs:[]};
  else if(url.pathname.includes('/ccf')) data={venues:[],years:[],papers:[]};
  else if(url.pathname.includes('navigation')) data={modules:[]};
  res.setHeader('Content-Type','application/json');res.end(JSON.stringify(data));
});
(async()=>{
  await new Promise(r=>server.listen(0,'127.0.0.1',r));
  const browser=await engines[process.env.PAPER_TEST_BROWSER||'chromium'].launch({headless:true});
  const watchdog=setTimeout(()=>{console.error('Browser remained unresponsive',requests.slice(-12));browser.close();},20000);
  try{
    const page=await browser.newPage({viewport:{width:Number(process.env.PAPER_TEST_WIDTH||1440),height:960},locale:'zh-CN'});page.setDefaultTimeout(6000);
    const errors=[];
    page.on('pageerror',e=>errors.push(e.message));
    page.on('console',m=>{if(m.type()==='error') errors.push(m.text().slice(0,250));});
    await page.addInitScript(()=>{
      // Observe responsiveness without replacing or disabling production observers.
      window.__heartbeat=0;
      setInterval(()=>window.__heartbeat++,50);
    });
    await page.goto(`http://127.0.0.1:${server.address().port}/paper`,{waitUntil:'domcontentloaded'});
    await page.waitForFunction(()=>document.querySelector('#libraryMeta')?.textContent.includes('4502'));
    await page.locator('#favoriteFilter').check();
    await page.waitForFunction(()=>document.querySelector('#libraryMeta')?.textContent.startsWith('2/4502'));
    await page.locator('[data-left-face="library"]').click();
    await page.locator('#favoriteFilter').uncheck();
    await page.waitForFunction(()=>document.querySelector('#libraryMeta')?.textContent.startsWith('4502/4502'));
    await page.locator('.paperRow').first().click();
    await page.waitForSelector('#paperPdfViewer[data-pdf-status="done"]');
    await page.evaluate(()=>{
      window.__readerBeforeFilter=document.querySelector('#paperPdfViewer');
      window.__canvasBeforeFilter=window.__readerBeforeFilter.querySelector('canvas');
      window.__analysisBeforeFilter=document.querySelector('#analysisReaderContent');
    });
    for(let round=0;round<3;round++){
      await page.locator('#favoriteFilter').check();
      await page.waitForFunction(()=>document.querySelector('#libraryMeta')?.textContent.startsWith('2/4502'));
      await page.locator('[data-left-face="library"]').click();
      await page.locator('#favoriteFilter').uncheck();
      await page.waitForFunction(()=>document.querySelector('#libraryMeta')?.textContent.startsWith('4502/4502'));
      assert.equal(await page.evaluate(()=>window.__readerBeforeFilter===document.querySelector('#paperPdfViewer')
        &&window.__canvasBeforeFilter===document.querySelector('#paperPdfViewer canvas')
        &&window.__analysisBeforeFilter===document.querySelector('#analysisReaderContent')),true,'Sidebar filters and active Library tab must preserve the PDF, canvas and analysis');
    }
    await page.locator('[data-mode="library"]').click();
    await page.locator('#favoriteFilter').uncheck();
    await page.waitForFunction(()=>document.querySelectorAll('.paperRow').length===100);
    favoriteCount=0;
    await page.locator('#favoriteFilter').check();
    await page.waitForFunction(()=>document.querySelector('#libraryMeta')?.textContent.startsWith('0/4502'));
    await page.locator('[data-left-face="library"]').hover();
    await page.waitForTimeout(500);
    await page.locator('[data-left-face="library"]').click();
    const heartbeat=await page.evaluate(()=>window.__heartbeat);
    await page.waitForFunction(previous=>window.__heartbeat>previous+2,heartbeat);
    assert.equal(await page.locator('.paperRow').count(),0);
    await page.locator('#favoriteFilter').uncheck();
    await page.waitForFunction(()=>document.querySelectorAll('.paperRow').length===100);
    // Switching back from a different face must still refresh the workspace.
    await page.locator('[data-left-face="ccf"]').click();
    await page.waitForFunction(()=>document.querySelector('[data-left-face="ccf"]').classList.contains('active'));
    await page.locator('[data-left-face="library"]').click();
    await page.waitForFunction(()=>document.querySelectorAll('.paperRow').length===100);
    // Leave Reader while the PDF is still loading, then while its pages are
    // rendering. A fresh tab prevents the shared PDF cache masking the race.
    pdfDelay=500;
    await page.goto(`http://127.0.0.1:${server.address().port}/paper`,{waitUntil:'domcontentloaded'});
    await page.waitForSelector('.paperRow');
    await page.locator('.paperRow').first().click();
    await page.waitForSelector('#paperPdfViewer');
    await page.locator('[data-mode="library"]').click();
    await page.waitForFunction(()=>document.querySelectorAll('.paperRow').length===100);
    await page.waitForTimeout(700);
    assert.equal(await page.locator('#paperPdfViewer').count(),0,'Late PDF load must not reopen Reader');
    pdfDelay=0;
    for(let round=0;round<3;round++){
      await page.locator('.paperRow').first().click();
      await page.waitForSelector('#paperPdfViewer canvas');
      await page.locator('#paperPdfViewer').evaluate(el=>{el.scrollTop=el.scrollHeight;});
      await page.locator('[data-mode="library"]').hover();
      await page.waitForTimeout(150);
      await page.locator('[data-mode="library"]').click();
      await page.waitForFunction(()=>document.querySelectorAll('.paperRow').length===100);
    }
    assert.deepEqual(errors,[]);
    console.log('PASS Paper library/favorites/PDF lifecycle',process.env.PAPER_TEST_BROWSER||'chromium');
  }finally{clearTimeout(watchdog);await browser.close();server.close();}
})().catch(e=>{console.error(e);process.exitCode=1;server.close();});
