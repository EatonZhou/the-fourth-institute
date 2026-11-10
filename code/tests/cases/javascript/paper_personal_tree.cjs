const assert=require('node:assert/strict'), fs=require('node:fs'),path=require('node:path');
const {chromium,firefox}=require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
(async()=>{for(const [name,type] of [['chromium',chromium],['firefox',firefox]]){
 const browser=await type.launch({headless:true});try{
 const page=await browser.newPage({viewport:{width:900,height:720}});
 await page.setContent('<meta charset="utf-8"><section class="leftBody" style="width:380px;height:650px;overflow:auto"><div id="folderTree">UNCHANGED GLOBAL TREE</div></section><button id="paperFavoriteBtn"></button><iframe id="reader"></iframe><div id="chat">UNCHANGED CHAT</div>');
 for(const file of ['paper_library_view.js','paper_personal.js'])await page.addScriptTag({path:path.resolve(__dirname,'../../../management_portal/static',file)});
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
 await page.addStyleTag({path:path.resolve(__dirname,'../../../management_portal/static/paper.css')});
 await page.screenshot({path:'/tmp/paper-personal-tree-'+name+'.png'});
 await page.evaluate(()=>{state.principalId='B';dispatchEvent(new CustomEvent('portal:paper-principal-changed'));});
 await page.waitForFunction(()=>document.querySelector('[data-status]').textContent==='synthetic denied');assert.equal(await page.locator('[data-row]').count(),0);
 console.log(name+': PASS exact scoped multipage counts/order, scope expansion, favorites removal, unchanged Reader/Chat, account error, no Bib downloads');
 }finally{await browser.close();}}
})().catch(e=>{console.error(e);process.exit(1)});
