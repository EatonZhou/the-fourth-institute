// Reader and scoped personal-tree assertions use the current source together.
const assert=require('node:assert/strict'), fs=require('node:fs'),path=require('node:path');
const {chromium,firefox}=require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
(async()=>{for(const [name,type] of [[process.argv[3],{chromium,firefox}[process.argv[3]]]]){
 const browser=await type.launch({headless:true});try{

 const [url,,cookie,token]=process.argv.slice(2);
 const context=await browser.newContext({viewport:{width:900,height:720}});
 await context.addCookies([{name:cookie,value:token,url}]);
 const readerPage=await context.newPage();await readerPage.goto(url);await readerPage.addStyleTag({url:url+'/static/paper.css'});
 await readerPage.waitForFunction(()=>window.ready);await readerPage.evaluate(()=>openDocument(true));
 await readerPage.waitForFunction(()=>signals.length===1);
 await readerPage.evaluate(()=>{window.savedFrame=document.querySelector('iframe');window.savedWindow=savedFrame.contentWindow;document.querySelector('.paperReadingToolbar strong').textContent='A very long synthetic paper title '.repeat(12);});
 for(const full of [false,true,false,true,false]){
  await readerPage.evaluate(full=>host.setFullscreen(full),full);
  await readerPage.waitForFunction(full=>document.querySelector('dialog').classList.contains('is-embedded')===!full,full);
  const geometry=await readerPage.evaluate(()=>{const header=document.querySelector('.paperReadingToolbar'),title=header.querySelector('strong'),rect=header.getBoundingClientRect();return {titleWidth:title.getBoundingClientRect().width,truncated:title.scrollWidth>title.clientWidth,controls:[...header.querySelectorAll('button,a')].filter(e=>e.getBoundingClientRect().width>0).every(e=>{const r=e.getBoundingClientRect();return r.left>=rect.left-1&&r.right<=rect.right+1;}),viewer:document.querySelector('iframe').getBoundingClientRect().height,same:savedFrame===document.querySelector('iframe')&&savedWindow===savedFrame.contentWindow,ready:signals.length,sidebarHidden:document.querySelector('.paperReaderSidePanel').hidden};});
  assert((full?(geometry.titleWidth>30&&geometry.truncated):geometry.titleWidth===0)&&geometry.controls&&geometry.viewer>200&&geometry.same&&geometry.ready===1&&geometry.sidebarHidden,JSON.stringify({full,...geometry}));
 }
 assert.equal(await readerPage.evaluate(()=>host.close()),true);
 console.log(name+': PASS current Reader/CSS, 5 layout transitions, title ellipsis/controls/frame/ready; no Chat allocation');
 await context.close();
 const page=await browser.newPage({viewport:{width:900,height:720}});
 await page.setContent('<meta charset="utf-8"><section class="leftBody" style="width:380px;height:650px;overflow:auto"><div id="folderTree">UNCHANGED GLOBAL TREE</div></section><button id="paperFavoriteBtn"></button><iframe id="reader"></iframe><div id="chat">UNCHANGED CHAT</div>');
 for(const file of ['paper_library_view.js','paper_personal.js'])await page.addScriptTag({path:path.resolve(__dirname,'../../../management_portal/static',file)});
 await page.addStyleTag({path:path.resolve(__dirname,'../../../management_portal/static/paper.css')});
 await page.evaluate(()=>{
 window.PortalI18n={t:x=>x,html:(s,...v)=>s.reduce((a,x,i)=>a+x+(v[i]??''),'')};window.calls=[];window.opens=[];window.reader=document.querySelector('#reader');
 window.state={principalId:'A',selectedId:'p0',favoriteIds:new Set(),favoritePending:new Set(),paperTreeExpansion:{},paperTreeRenderLimit:200};
 const papers=Array.from({length:51},(_,i)=>({id:'p'+i,title:'Synthetic recent '+i,venue:'CoRL',year:2026,session:i<50?'General':'RoboLab',has_pdf:true}));
 const esc=x=>String(x).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');
 const api={request:async(url,opts)=>{calls.push(url);if(opts)return {favorite:false};if(state.principalId==='B')throw Error('synthetic denied');return url.includes('recent-reading')?(url.includes('cursor=next')?{papers:papers.slice(50),next_cursor:''}:{papers:papers.slice(0,50),next_cursor:'next'}):{papers:papers.slice(0,2)};}};
 const deps=Object.fromEntries(PortalPaperLibraryView.dependencies.map(k=>[k,()=>{}]));Object.assign(deps,{$:id=>document.getElementById(id),state,paperApi:api,esc,disclosureIcon:e=>e?'▾':'▸',paperStateHtml:x=>x,isCollectionMode:()=>false});
 const view=PortalPaperLibraryView.createLibraryView(deps);
 PortalPaperPersonal.mount({state,paperApi:api,renderTree:view.renderTree,syncFavoriteButtons:view.syncFavoriteButtons,loadDetail:async id=>opens.push(id),setMode:()=>{}});
 });
 await page.waitForFunction(()=>document.querySelectorAll('[data-row]').length===51);
 assert.equal(await page.locator('[data-export]').count(),0);
 assert.equal(await page.locator('.folderCount').first().textContent(),'51');
 assert.deepEqual(await page.locator('[data-row]').evaluateAll(es=>es.slice(0,3).map(e=>e.dataset.row)),['p0','p1','p2']);
 await page.locator('[data-paper-tree-toggle]').first().click();
 await page.locator('[data-view=favorites]').click();await page.waitForFunction(()=>document.querySelectorAll('[data-row]').length===2);
 assert.equal(await page.locator('.folderCount').first().textContent(),'2');
 await page.locator('[data-rows] [data-paper-favorite]').first().click();await page.waitForFunction(()=>document.querySelectorAll('[data-row]').length===1);
 assert.equal(await page.locator('.folderCount').first().textContent(),'1');
 await page.locator('[data-view=recent]').click();await page.locator('[data-view=favorites]').click();assert.equal(await page.locator('[data-row]').count(),1,'cached favorites do not resurrect removed rows');await page.locator('[data-view=recent]').click();assert.equal(await page.locator('[data-paper-tree-toggle]').first().getAttribute('aria-expanded'),'false');
 await page.locator('[data-paper-tree-toggle]').first().click();
 await page.locator('[data-scope=repository]').click();assert.equal(await page.locator('#folderTree').textContent(),'UNCHANGED GLOBAL TREE');
 await page.locator('[data-scope=personal]').click();
 assert(await page.evaluate(()=>reader===document.querySelector('#reader')&&opens.length===0&&document.querySelector('#chat').textContent==='UNCHANGED CHAT'));

 await page.screenshot({path:path.join(process.env.TMPDIR,'paper-static-candidate-'+name+'.png')});
 await page.evaluate(()=>{state.principalId='B';dispatchEvent(new CustomEvent('portal:paper-principal-changed'));});
 await page.waitForFunction(()=>document.querySelector('[data-status]').textContent==='synthetic denied');assert.equal(await page.locator('[data-row]').count(),0);
 console.log(name+': PASS exact scoped multipage counts/order, scope expansion, favorites removal, unchanged Reader/Chat, account error, no Bib downloads');
 }finally{await browser.close();}}
})().catch(e=>{console.error(e);process.exit(1)});
