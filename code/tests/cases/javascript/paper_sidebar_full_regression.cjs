const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path');
const {chromium} = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const root = path.resolve(__dirname, '../../../management_portal');
(async () => {
  const browser = await chromium.launch({headless:true});
  try {
    const page = await browser.newPage({viewport:{width:1000,height:1100}});
    await page.route('http://sidebar.test/**',route=>route.fulfill({body:'',contentType:'text/html'}));
    await page.goto('http://sidebar.test/');
    // Keep the full authored sidebar and its real styles; only network/bootstrap is simulated.
    await page.setContent(fs.readFileSync(path.join(root,'paper.html'),'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/g,''));
    const styles = await page.locator('link[rel=stylesheet]').evaluateAll(nodes => nodes.map(n=>n.getAttribute('href')));
    for (const href of styles.filter(x=>x.startsWith('/static/'))) await page.addStyleTag({path:path.join(root,href)});
    await page.addStyleTag({content:'#paperLibraryPane {width:358px;height:1050px;display:flex;flex-direction:column} #paperWorkbench {display:block} #paperWorkspacePane,#paperChatPane {display:none}'});
    await page.evaluate(() => {
      window.PortalI18n={t:x=>x};
      window.state={principalId:'fixture-user',favoriteIds:new Set(),favoritePending:new Set()};
      window.pending=[];
      window.paperApi={request:()=>new Promise((resolve,reject)=>pending.push({resolve,reject}))};
    });
    await page.evaluate(catalog=>{
      window.__PORTAL_TRANSLATIONS__=catalog;window.__PORTAL_LANGUAGE__='zh-CN';
      window.PortalCompatibility={identifier:x=>'fixture-'+x};
    },JSON.parse(fs.readFileSync(path.join(root,'translations/paper.json'),'utf8')));
    for (const file of ['portal_text.js','portal_i18n.js','portal_ui_primitives.js','portal_form_controls.js','portal_board_toolbar.js','paper_personal.js']) await page.addScriptTag({path:path.join(root,'static',file)});
    await page.evaluate(() => {
      PortalBoardToolbar.mountAll();
      document.querySelector('#folderTree').innerHTML='<div>⌄ CoRL</div><div style="padding-left:12px">⌄ 2026</div><div style="padding-left:24px">⌄ General</div>';
      PortalPaperPersonal.mount({state,paperApi,renderTree:()=>{},syncFavoriteButtons:()=>{},loadDetail:async()=>{},setMode:()=>{}});
      pending.shift().resolve({papers:[],next_cursor:null});
    });
    await page.addScriptTag({path:path.join(root,'static/paper_events.js')});
    await page.evaluate(()=>{
      window.__queryEvents=[];state.filters={};
      const deps=Object.fromEntries(PortalPaperEvents.dependencies.map(k=>[k,()=>{}]));
      Object.assign(deps,{$:id=>document.getElementById(id)||document.createElement('div'),state,
        loadLibrary:async()=>__queryEvents.push('load'),writeUiState:()=>__queryEvents.push('save')});
      PortalPaperEvents.createEvents(deps).bindEvents();
    });
    const status=page.locator('.paperPersonal [data-status]');
    await page.waitForFunction(()=>document.querySelector('[data-status]').textContent==='暂无记录');
    await page.locator('[data-scope=repository]').click();
    assert.equal(await status.textContent(),'');
    for (const language of ['en','zh-CN']) {
      await page.evaluate(lang=>PortalI18n.apply(lang),language);
      assert.equal(await page.locator('.paperSearchHistory > summary').first().textContent(),language==='en'?'History':'历史');
      assert(await page.locator('#collectorForm').evaluate(el=>!!(el.compareDocumentPosition(document.querySelector('.paperLibraryQueryGroup')) & Node.DOCUMENT_POSITION_FOLLOWING)));
      assert.equal(await page.locator('#clearLibraryFiltersBtn').textContent(),language==='en'?'Clear filters':'清除筛选');
      assert(await page.locator('.paperLibraryQueryGroup').evaluate(el=>el.contains(document.getElementById('searchInput')) && el.contains(document.getElementById('semanticSearchList')) && ['yearFilter','venueFilter','tagFilter','projectFilter'].every(id=>el.contains(document.getElementById(id))) && !el.contains(document.getElementById('collectorForm'))));
      await page.locator('#searchInput').fill('fixture existing-library query');
      await page.waitForFunction(()=>__queryEvents.includes('load'));
      for (const id of ['yearFilter','venueFilter','tagFilter','projectFilter']) {
        await page.locator('#'+id).evaluate(el=>{el.add(new Option('fixture','fixture'));el.value='fixture';el.dispatchEvent(new Event('change',{bubbles:true}));});
      }
      assert(await page.evaluate(()=>Object.values(state.filters).every(v=>v==='fixture') && state.query==='fixture existing-library query'));
      assert(await page.locator('#semanticSearchBtn').isVisible());
      await page.locator('#clearLibraryFiltersBtn').click();
      assert(await page.evaluate(()=>Object.keys(state.filters).length===0 && state.query==='' && ['yearFilter','venueFilter','tagFilter','projectFilter'].every(id=>document.getElementById(id).value==='')));
      assert.equal(await page.locator('.collectorBox [data-portal-board-slot] > label').evaluate(el=>getComputedStyle(el).fontSize),'12px');
      assert.equal(await page.locator('.paperLibraryQueryGroup label').first().evaluate(el=>getComputedStyle(el).fontSize),await page.locator('.paperPersonalFilters label').first().evaluate(el=>getComputedStyle(el).fontSize));

      await page.locator('[data-view=recent]').evaluate(el=>el.click());
      assert.equal(await status.textContent(),language==='en'?'No records yet':'暂无记录');
      assert.equal(await page.locator('[data-personal-clear]').textContent(),language==='en'?'Clear filters':'清除筛选');
      await page.locator('[data-scope=repository]').click();
      await page.locator('#paperLibraryPane').evaluate(el=>el.style.width='280px');
      assert.deepEqual(await page.locator('.leftBody').evaluate(el=>[...el.querySelectorAll('*')].filter(child=>child.getBoundingClientRect().width && child.getBoundingClientRect().right>el.getBoundingClientRect().right+1).map(child=>child.id||child.className)),[]);
      await page.screenshot({path:`/tmp/paper-collect-first-${language}.png`,fullPage:true});
    }
    await page.locator('[data-scope=personal]').click();
    assert.equal(await status.textContent(),'暂无记录');
    await page.locator('[data-scope=repository]').click();
    assert.equal(await status.textContent(),'');
    // A personal request finishing after switching away must not alter repository status.
    await page.locator('[data-view=favorites]').evaluate(el=>el.click());
    await page.locator('[data-scope=repository]').click();
    await page.evaluate(()=>pending.shift().resolve({papers:[],next_cursor:null}));
    await page.waitForTimeout(0);
    assert.equal(await status.textContent(),'');
    await page.locator('[data-scope=personal]').click();
    await page.locator('[data-scope=repository]').click();
    await page.evaluate(()=>pending.shift().reject(Error('late personal failure')));
    await page.waitForTimeout(0);
    assert.equal(await status.textContent(),'');
    for (const width of [358,280]) {
      await page.locator('#paperLibraryPane').evaluate((el,w)=>el.style.width=w+'px',width);
      const result=await page.evaluate(()=>{
        const rect=s=>{const r=document.querySelector(s).getBoundingClientRect();return [r.left,r.right];};
        const style=s=>getComputedStyle(document.querySelector(s));
        return {scope:rect('[data-scopes]'),tabs:rect('#leftFaceTabs'),search:rect('.searchWrap'),
          top:style('.searchWrap').borderTopWidth,bottom:style('.searchWrap').borderBottomWidth,
          history:style('.paperSearchHistory > summary').fontWeight,
          tree:style('#folderTree').borderTopWidth,
          branch:[...document.querySelectorAll('#folderTree div')].map(el=>getComputedStyle(el).borderTopWidth)};
      });
      assert.deepEqual(result.scope,result.tabs);assert.deepEqual(result.tabs,result.search);
      assert.equal(result.top,'1px');assert.equal(result.bottom,'0px');assert.equal(result.history,'400');assert.equal(result.tree,'0px');
      assert(result.branch.every(x=>x==='0px'));
      assert.deepEqual(await page.locator('.leftBody').evaluate(el=>[...el.querySelectorAll('*')].filter(child=>child.getBoundingClientRect().width && child.getBoundingClientRect().right>el.getBoundingClientRect().right+1).map(child=>child.id||child.className)),[]);
      await page.screenshot({path:`/tmp/paper-sidebar-full-${width}.png`,fullPage:true});
    }
    console.log('PASS full authored sidebar + production CSS/components: bidirectional cached empty/repository switching, late success/error, aligned 358/280px bounds, functional separators, unruled tree, normal-weight history');
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
