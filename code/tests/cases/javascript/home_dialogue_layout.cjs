// Native Home/Workbench geometry with isolated API transport. No user session.
const {chromium}=require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const homeAction=require('./home_dialogue_controls.cjs');
const fs=require('node:fs'),path=require('node:path'),http=require('node:http'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'../../../management_portal');
const groups=JSON.parse(fs.readFileSync(path.join(root,'script_groups.json')));
const html=fs.readFileSync(path.join(root,'index.html'),'utf8').replace(/<script data-portal-script-group="([^"]+)"><\/script>/g,(_,id)=>groups[id].map(src=>`<script src="${src}"></script>`).join(''));
const out=process.env.HOME_DIALOGUE_LAYOUT_EVIDENCE||'/tmp/home-dialogue-layout'; fs.mkdirSync(out,{recursive:true});
const lineage='lineage_0123456789ab', calls=[];
const frontier={schema:'tfi.exploration-frontier.v1',items:[{id:'seed',title:'Home 展开面板边界与编辑入口',line:'Home连续使用',
  sources:['article_0123456789ab'],attention:'explore',
  progress:'已完成手动三类汇报、文件草稿返回和保存；组件展示状态曾重建，原因待核对。',
  reason:'需要确认展示切换及组件重挂载是否保护阅读位置和草稿；本条仅登记问题，不自动执行。',
  next:'本地夹具核对宿主布局与原生编辑入口；不启动任何工作。'}]};
const server=http.createServer(async(req,res)=>{
  const url=new URL(req.url,'http://localhost');
  if(url.pathname==='/') {res.setHeader('Content-Type','text/html');return res.end(html);}
  if(url.pathname==='/static/portal_resource_routes.js') {res.setHeader('Content-Type','text/javascript');return res.end('window.PortalResourceRoutes={schema:"tfi.resource-portal-routes.v1",routes:[],text_resource_types:[],identity_parameters:[]};');}
  if(url.pathname==='/api/instructions/execute') {
    let raw='';for await(const chunk of req)raw+=chunk;
    const {instruction,arguments:args}=JSON.parse(raw);calls.push({instruction,args});
    let result={};
    if(instruction==='GetLineage')result={lineage:{title:'Home 宿主布局夹具',nodes:[]}};
    if(instruction==='GetSharedState')result={value:frontier};
    if(instruction==='ResolveResource')result={resource:{title:'布局验证来源',version:1}};
    res.setHeader('Content-Type','application/json');return res.end(JSON.stringify({schema:'tfi.hatg-instruction-result.v2',status:'committed',result}));
  }
  if(url.pathname.startsWith('/api/')) {res.setHeader('Content-Type','application/json');return res.end(JSON.stringify(url.pathname==='/api/me'?{username:'layout-fixture',principal:{kind:'user',id:'layout-fixture'},access:{allows:{}}}:{items:[],accounts:[],usage:[],result:{},schema:'tfi.hatg-instruction-result.v2',status:'committed'}));}
  const file=path.resolve(root,'.'+url.pathname); if(!file.startsWith(root+'/static/')||!fs.existsSync(file)){res.statusCode=404;return res.end();}
  res.setHeader('Content-Type',file.endsWith('.css')?'text/css':'text/javascript');res.end(fs.readFileSync(file));
});
(async()=>{
  await new Promise(r=>server.listen(0,'127.0.0.1',r));const browser=await chromium.launch({headless:true});const evidence=[];
  try {
    for(const viewport of [{width:1440,height:1000},{width:390,height:844},{width:720,height:500}]) {
      const page=await browser.newPage({viewport,locale:'zh-CN',hasTouch:true});page.setDefaultTimeout(5000);const errors=[];page.on('pageerror',e=>errors.push(e.message));
      await page.goto(`http://127.0.0.1:${server.address().port}/?home_component=dialogue-display`);
      await page.locator('.homeDialoguePreview .portalDocumentToolbar').waitFor({state:'attached'});
      await page.emulateMedia({reducedMotion:'reduce'});
      await page.evaluate(()=>document.documentElement.style.fontSize='20px');
      // The native toolbar opens an active document; an empty preview has no document to expand.
      await page.evaluate(id=>PortalPreview.request(PortalDisplayProtocol.previewRequest({view:'frontier',target:{kind:'lineage',id}})),lineage);
      await page.locator('.homeMapSurface:not([hidden]) .graphNode[data-kind="seed"]').waitFor();
      if(process.env.HOME_TAB_FOLD_ONLY==='1') {
        evidence.push(await require('./home_tab_autohide.cjs')(page,out,viewport));
        assert.deepEqual(errors,[]);await page.close();continue;
      }
      assert.equal(await page.locator('.homeDialogue > header').count(),0,'component header is removed, not visually hidden');
      const layout=await page.evaluate(()=>JSON.stringify(PortalWorkbench.get('home').getState().layout));
      const close=page.locator('.portalWorkbenchTabClose[aria-label$="对话与展示"]');
      const geometry=()=>close.evaluate(el=>{
        const rect=el.getBoundingClientRect(),hit=document.elementFromPoint(rect.x+rect.width/2,rect.y+rect.height/2),header=document.querySelector('.portalSiteHeader').getBoundingClientRect();
        return {rect:rect.toJSON(),hit:el===hit||el.contains(hit),hitClass:hit?.className,headerBottom:header.bottom,scrollY,ancestors:Array.from((function*(n){while(n){yield n;n=n.parentElement;}})(el)).filter(n=>n.scrollTop).map(n=>({class:n.className,top:n.scrollTop}))};
      });
      const before=await geometry();await homeAction(page,'展开');
      const expanded=await page.locator('.homeDialogueExpanded').evaluate(el=>({rect:el.getBoundingClientRect().toJSON(),
        mounted:el.matches('.portalWorkbenchPanelFrame > [data-portal-workbench-panel="dialogue-display"]'),
        viewportHeight:innerHeight,rootFont:getComputedStyle(document.documentElement).fontSize}));
      assert.equal(expanded.mounted,true,'exercise the native mounting selector');
      assert.ok(expanded.rect.bottom<=expanded.viewportHeight,'entire expanded panel stays inside the viewport');
      assert.equal(expanded.rootFont,'20px');
      const entry={viewport,before,expanded,errors};evidence.push(entry);
      if(viewport.width!==720) {
        const tabs=page.locator('.homeDialogueTabs');
        if(await tabs.isVisible())await tabs.getByRole('button',{name:'展示',exact:true}).click();
        await page.evaluate(id=>PortalPreview.request(PortalDisplayProtocol.previewRequest({view:'frontier',target:{kind:'lineage',id}})),lineage);
        const surface=page.locator('.homeMapSurface:not([hidden])');
        const seed=surface.locator('.graphNode[data-kind="seed"]');
        await seed.waitFor();await surface.getByRole('button',{name:'适应',exact:true}).click();await seed.click();
        const detail=surface.getByRole('complementary',{name:'节点详情',exact:true});
        const action=detail.getByRole('button',{name:'更新节点',exact:true});
        await action.evaluate(button=>{
          window.homeHeightPointerEvents=[];
          button.addEventListener('pointerdown',event=>{
            let hit=document.elementFromPoint(event.clientX,event.clientY);
            while(hit?.shadowRoot){const next=hit.shadowRoot.elementFromPoint(event.clientX,event.clientY);if(!next||next===hit)break;hit=next;}
            const detail=button.closest('#detail');
            window.homeHeightPointerEvents.push({type:event.pointerType,x:event.clientX,y:event.clientY,
              owned:hit===button||button.contains(hit),scrollTop:detail.scrollTop,scrollHeight:detail.scrollHeight,clientHeight:detail.clientHeight,
              panelBottom:document.querySelector('.homeDialogueExpanded').getBoundingClientRect().bottom,viewportHeight:innerHeight});
          });
        });
        // Each action starts at the top. Playwright's normal action scrolls the
        // real detail; a pointer event and visible native editor prove reachability.
        for(const mode of ['mouse','touch']) {
          await detail.evaluate(el=>el.scrollTop=0);
          if(mode==='mouse')await action.click();else await action.tap();
          await surface.locator('#editor').waitFor({state:'visible'});
          await page.screenshot({path:path.join(out,`editor-${mode}-${viewport.width}.png`)});
          const dismiss=surface.locator('#editor').getByRole('button',{name:'关闭',exact:true});
          if(mode==='mouse')await dismiss.click();else await dismiss.tap();
          await surface.locator('#editor').waitFor({state:'hidden'});
        }
        entry.pointerEvents=await page.evaluate(()=>window.homeHeightPointerEvents);
        assert.deepEqual(entry.pointerEvents.map(e=>e.type),['mouse','touch']);
        for(const e of entry.pointerEvents){assert.equal(e.owned,true);assert.ok(e.y<e.viewportHeight);assert.ok(e.panelBottom<=e.viewportHeight);}
        if(viewport.width===390)for(const e of entry.pointerEvents){assert.ok(e.scrollHeight>e.clientHeight);assert.ok(e.scrollTop>0,'detail scrolled to reach edit');}
      }
      await page.screenshot({path:path.join(out,`expanded-${viewport.width}.png`)});
      await homeAction(page,'返回原布局');
      // Desktop headers retract inside the stack. Native keyboard focus must
      // reveal the close control below navigation, without forced clicks.
      await close.focus();
      const after=await geometry();entry.after=after;
      await page.screenshot({path:path.join(out,`returned-${viewport.width}.png`)});
      if(process.env.HOME_DIALOGUE_EXPECT_OBSTRUCTION==='1'){assert.equal(after.hit,false);break;}
      assert.equal(after.hit,true,'revealed close center must be unobstructed without a forced click');
      assert.ok(after.rect.y>=after.headerBottom,'tab has space below navigation');
      assert.equal(await page.evaluate(()=>JSON.stringify(PortalWorkbench.get('home').getState().layout)),layout);
      await close.click();
      assert.ok(await page.evaluate(()=>PortalWorkbench.get('home').getState().hidden.includes('dialogue-display')));
      evidence.at(-1).mouseClose=true;assert.deepEqual(errors,[]);await page.close();
    }
    assert.ok(!calls.some(c=>['DeliverMessage','UpdateSharedState','ApplyResearchMapChanges'].includes(c.instruction)),'no business writes');
    console.log(JSON.stringify(evidence));
  } finally {fs.writeFileSync(path.join(out,'result.json'),JSON.stringify(evidence,null,2));await browser.close();await new Promise(r=>server.close(r));}
})().catch(e=>{console.error(e);process.exitCode=1;});
