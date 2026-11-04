// Exercise host integration with real DocumentSession and Preview; only transport
// and independently owned adapters are fixtures. Live auth is a separate check.
const {chromium} = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const fs = require('node:fs'), path = require('node:path'), http = require('node:http'), assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../../../management_portal');
const groups = JSON.parse(fs.readFileSync(path.join(root,'script_groups.json')));
const homeHtml = fs.readFileSync(path.join(root,'index.html'),'utf8').replace(/<script data-portal-script-group="([^"]+)"><\/script>/g,(_,id)=>groups[id].map(src=>`<script src="${src}"></script>`).join(''));
const scripts = ['portal_state.js','portal_text.js','portal_translations.js','portal_i18n.js','portal_ui_primitives.js',
  'portal_preview_contract.js','portal_preview_runtime.js','portal_pdf_preview.js','portal_preview_renderers.js',
  ...groups['portal-workspace-surface'].map(x=>x.replace(/^\/static\//,'')),
  'portal_agent_preview_source.js','home_dialogue_state.js','home_dialogue_preview.js','home_dialogue.js'];
const server = http.createServer((req,res) => {
  const pathname = new URL(req.url,'http://localhost').pathname;
  if(pathname === '/static/portal_resource_routes.js') {res.setHeader('Content-Type','text/javascript');return res.end('window.PortalResourceRoutes={schema:"tfi.resource-portal-routes.v1",routes:[],text_resource_types:[],identity_parameters:[]};');}
  if(pathname === '/home') {res.setHeader('Content-Type','text/html');return res.end(homeHtml);}
  if(pathname.startsWith('/api/')) {res.setHeader('Content-Type','application/json');return res.end(JSON.stringify(pathname==='/api/me'?{username:'home-fixture',principal:{kind:'user',id:'home-fixture'},access:{allows:{}}}:{items:[],accounts:[],usage:[],result:{},schema:'tfi.hatg-instruction-result.v2',status:'committed'}));}
  if(pathname === '/') {res.setHeader('Content-Type','text/html'); return res.end('<!doctype html><meta charset="utf-8"><link rel="stylesheet" href="/static/home_dialogue.css"><style>body{margin:0}#root{height:800px}</style>' + scripts.map(s=>`<script src="/static/${s}"></script>`).join('')+'<div id="root"></div>');}
  const file = path.join(root,pathname); if(!file.startsWith(root + '/static/') || !fs.existsSync(file)) {res.statusCode=404;return res.end();}
  res.setHeader('Content-Type', file.endsWith('.css')?'text/css':'text/javascript'); res.end(fs.readFileSync(file));
});
(async()=>{
  await new Promise(r=>server.listen(0,'127.0.0.1',r)); const browser = await chromium.launch({headless:true});
  try {
    const page = await browser.newPage({viewport:{width:1200,height:860}}); page.setDefaultTimeout(7000); const errors=[]; page.on('pageerror',e=>{errors.push(e.message);console.error('pageerror', e.message);});
    await page.goto(`http://127.0.0.1:${server.address().port}`);
    await page.evaluate(()=>{
      window.calls=[]; window.pending=[]; window.rejectSave=false; window.previewReads=0; window.denyPreview=false; window.canEditPreview=true;
      window.fixtureIdentity='home-fixture'; window.PortalIdentity={peek:()=>({username:fixtureIdentity})};
      window.PortalUI = {confirm:async()=>true};
      window.PortalAPI = {get:async()=>({}),post:async()=>({})}; window.PortalResidentService={};
      window.PortalInstructionClient={execute:async(name,args)=>{calls.push({name,args});if(rejectSave)throw new Error('保存冲突'); return {resource:{content:args.changes.text,version:2}};}};
      window.PortalResourceChips={resourceAddressFromPreviewRequest:r=>r.resourceAddress.startsWith('article_')?r.resourceAddress:'',
        loadReference:async address=>({title:'真实目标 '+address.slice(-12)}),
        loadPreview:async (address,options)=>{previewReads++; calls.push({previewOptions:options}); if(window.holdPreview) await new Promise(resolve=>window.finishPreview=resolve); if(denyPreview)throw Object.assign(new Error('当前来源不可读'),{status:403}); return {source:'resource',resource_id:address,resource_address:address,title:'真实预览契约',kind:'text',content:'original',version:1,capabilities:{edit:canEditPreview,save:canEditPreview}};}};
      window.PortalFileLinks={describe:()=>null};
      window.PortalNav={personalAssistantTarget:async()=>({resident_id:'resident_0123456789ab',lineage_id:'lineage_0123456789ab',title:'current facade'})};
      window.PortalChatWorkspace={panelPackage:{create:options=>{window.chatOptions=options; options.root.textContent='Chat transport fixture'; return {workspace:{addReference:value=>calls.push({reference:value})},setPresented:value=>calls.push({chatPresented:value}),destroy:()=>calls.push({chatDestroyed:true})};}}};
      window.PortalHomeReportView={create:({root,onSelection})=>{window.reportSelection=onSelection; let presented=true; return {show(target){calls.push({reportShowPresented:presented}); root.textContent=target.id; return new Promise(resolve=>pending.push({resolve,target}));},setPresented(value){presented=value; calls.push({reportPresented:value}); if(window.rejectPresentation&&value) return Promise.resolve({error:new Error('地图恢复失败')});},destroy(){calls.push({adapterDestroyed:true});}};}};
      window.controller=PortalHomeDialogue.connect({root:document.querySelector('#root'),context:{currentUserReady:Promise.resolve()}});
    });
    await page.waitForFunction(()=>!!window.chatOptions);
    // Manual navigation and pinned late result.
    await page.locator('.homeDialogueOpener > summary').click();
    await page.getByLabel('目标类型',{exact:true}).selectOption('lineage'); await page.getByLabel('目标 ID 或文件资源地址').fill('lineage_0123456789ab'); await page.getByRole('button',{name:'展示',exact:true}).last().click();
    await page.evaluate(()=>{
      pending.shift().resolve();
      const failedTarget={resident_id:'resident_0123456789ab'}, sentTarget={resident_id:'resident_0123456789ab'};
      window.failedProtocol=JSON.parse(chatOptions.submissionContext(failedTarget).context_text.split('示例结构：')[1].split('\n')[0]);
      window.protocol=JSON.parse(chatOptions.submissionContext(sentTarget).context_text.split('示例结构：')[1].split('\n')[0]);
      chatOptions.onAcknowledgement({}, {target:sentTarget});
      chatOptions.onDetail({snapshot:{messages:[{role:'assistant',message_id:'failed-result',content_parts:[{type:'text',text:'```portal-home-display\n'+JSON.stringify({...failedProtocol,target:{kind:'lineage',id:'lineage_abcdefabcdef'}})+'\n```'}]}]}});
    });
    assert.equal(await page.locator('.homeDialogueEntry').count(),1,'failed send is not acknowledged by the next success');
    assert.equal(await page.evaluate(async()=>{
      const before=calls.filter(c=>'reportPresented' in c).length;
      document.querySelector('#root').style.height='780px'; await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
      return calls.filter(c=>'reportPresented' in c).length-before;
    }),0,'resizing an active adapter does not repeat setPresented or reload');
    // Domain reports have no filesystem address. Their reference retains the actual request, focus and sources.
    await page.evaluate(()=>reportSelection({kind:'report',target:{kind:'task',id:'resident_0123456789ab/msg_abcdefabcdef'},focus:'direction-x',label:'任务进展',text:'实际任务状态',sources:[{address:'article_0123456789ab',version:3}]}));
    await page.locator('.homeDialogueMore > summary').click(); await page.getByRole('button',{name:'引用到对话',exact:true}).click();
    const reference=await page.evaluate(()=>calls.find(c=>c.reference).reference);
    assert.equal(reference.path,undefined); assert.ok(reference.text.includes('resident_0123456789ab/msg_abcdefabcdef')); assert.ok(reference.text.includes('article_0123456789ab')); assert.deepEqual(reference.source_message_ids,['msg_abcdefabcdef']);
    await page.getByRole('button',{name:'固定展示',exact:true}).click();
    await page.evaluate(()=>{chatOptions.onDetail({snapshot:{messages:[{role:'assistant',message_id:'new',content_parts:[{type:'text',text:'```portal-home-display\n'+JSON.stringify({...protocol,target:{kind:'lineage',id:'lineage_abcdefabcdef'}})+'\n```'}]}]}});});
    await page.waitForFunction(()=>document.querySelectorAll('.homeDialogueEntry').length===2);
    assert.equal(await page.evaluate(()=>calls.filter(c=>'reportShowPresented' in c).at(-1).reportShowPresented),false,'queued report receives hidden state before show');
    assert.ok((await page.locator('.homeDialogueCurrent').textContent()).includes('0123456789ab'));
    await page.evaluate(()=>pending.shift().resolve());
    assert.ok((await page.locator('.homeDialogueCurrent').textContent()).includes('0123456789ab'));
    // Existing preview/session editing, failed save, history preservation, then actual save contract.
    await page.locator('.homeDialogueOpener > summary').click();
    await page.getByLabel('展示类型',{exact:true}).selectOption('preview'); await page.getByLabel('目标 ID 或文件资源地址').fill('article_0123456789ab'); await page.getByRole('button',{name:'展示',exact:true}).last().click();
    await page.getByRole('button',{name:'编辑',exact:true}).click(); await page.getByLabel('文件草稿').fill('draft kept');
    await page.evaluate(()=>{window.rejectSave=true;}); await page.getByRole('button',{name:'保存',exact:true}).click(); await page.getByText('保存冲突',{exact:true}).waitFor();
    await page.getByRole('button',{name:'返回上一展示',exact:true}).click();
    const values=await page.getByLabel('展示历史').locator('option').evaluateAll(options=>options.map(o=>o.value));
    await page.getByLabel('展示历史').selectOption(values.at(-1)); assert.equal(await page.getByLabel('文件草稿').inputValue(),'draft kept');
    await page.evaluate(()=>{window.rejectSave=false;}); await page.getByRole('button',{name:'保存',exact:true}).click();
    await page.waitForFunction(()=>calls.filter(c=>c.name==='UpdateResource').length===2);
    assert.equal(await page.evaluate(()=>calls.filter(c=>c.name==='UpdateResource').at(-1).args.changes.text),'draft kept');
    await page.evaluate(()=>{window.rejectPresentation=true;}); await page.getByLabel('展示历史').selectOption(values[1]);
    await page.getByText('地图恢复失败',{exact:true}).waitFor();
    await page.evaluate(()=>{window.rejectPresentation=false;}); await page.getByLabel('展示历史').selectOption(values.at(-1)); await page.getByLabel('文件草稿').waitFor({state:'visible'});
    // Keyboard and pointer resizing; narrow component in a wide desktop viewport.
    const divider=page.getByRole('separator',{name:'调整对话与展示宽度',includeHidden:true});
    await divider.focus(); await divider.press('ArrowRight'); assert.equal(await divider.getAttribute('aria-valuenow'),'45');
    const dividerBox=await divider.boundingBox(); await page.mouse.move(dividerBox.x+4,dividerBox.y+40); await page.mouse.down(); await page.mouse.move(600,dividerBox.y+40); await page.mouse.up();
    const ratio=await divider.getAttribute('aria-valuenow'); assert.equal(ratio,'50');
    const beforeResize=await page.evaluate(()=>calls.filter(c=>'reportPresented' in c).length);
    await page.evaluate(()=>{document.querySelector('#root').style.maxWidth='520px';}); await page.waitForFunction(()=>document.querySelector('#root').dataset.narrow==='true');
    await page.getByRole('button',{name:'展开',exact:true}).click(); await page.waitForFunction(()=>document.querySelector('#root').dataset.narrow==='false');
    await page.getByRole('button',{name:'返回原布局',exact:true}).click(); await page.waitForFunction(()=>document.querySelector('#root').dataset.narrow==='true');
    assert.equal(await divider.getAttribute('aria-valuenow'),ratio);
    assert.equal(await page.evaluate(()=>calls.filter(c=>'reportPresented' in c).length),beforeResize,'resize does not reload hidden history adapters');
    await page.setViewportSize({width:480,height:800}); await page.locator('.homeDialogueTabs').getByRole('button',{name:'对话',exact:true}).click();
    assert.equal(await page.locator('.homeDialogueDisplay').isVisible(),false);
    await page.locator('.homeDialogueTabs').getByRole('button',{name:'展示',exact:true}).click(); assert.equal(await page.getByLabel('文件草稿').inputValue(),'draft kept');
    await page.evaluate(()=>controller.destroy()); assert.equal(await page.locator('#root').textContent(),'');
    // Real DocumentSession + Preview access lifecycle. No cached content is revealed before the new read.
    await page.evaluate(()=>{window.access=PortalHomeDialoguePreview.create({root:document.querySelector('#root')}); access.setPresented(false); window.readsBefore=previewReads; return access.show({address:'article_0123456789ab'});});
    assert.equal(await page.evaluate(()=>previewReads),await page.evaluate(()=>readsBefore),'hidden preview does not read');
    await page.evaluate(()=>access.setPresented(true)); await page.getByRole('button',{name:'编辑',exact:true}).click(); await page.getByLabel('文件草稿').fill('recoverable draft');
    assert.ok(await page.evaluate(()=>calls.filter(c=>c.previewOptions).every(c=>c.previewOptions.allowCached===false && c.previewOptions.readOnly===false)),'fresh server capabilities determine editing');
    await page.evaluate(()=>{access.setPresented(false); window.denyPreview=true;}); await page.evaluate(()=>access.setPresented(true));
    assert.equal(await page.getByLabel('文件草稿').isVisible(),false); assert.equal(await page.locator('.homeDialogueDocument').textContent(),'');
    assert.equal(await page.evaluate(()=>access.snapshot().draft),'recoverable draft');
    await page.evaluate(()=>{window.denyPreview=false;}); await page.getByRole('button',{name:'重试访问',exact:true}).click(); await page.getByLabel('文件草稿').waitFor({state:'visible'});
    assert.equal(await page.getByLabel('文件草稿').inputValue(),'recoverable draft');
    await page.evaluate(()=>{window.canEditPreview=false; window.dispatchEvent(new CustomEvent('portal:permissions-changed'));});
    await page.waitForFunction(()=>document.querySelector('.homeDialogueEditor').disabled);
    assert.equal(await page.getByRole('button',{name:'保存',exact:true}).isEnabled(),false);
    await page.evaluate(()=>{window.fixtureIdentity='other-user'; window.dispatchEvent(new CustomEvent('portal:identity-changed'));});
    assert.equal(await page.getByLabel('文件草稿').isVisible(),false); assert.equal(await page.getByLabel('文件草稿').inputValue(),'');
    await page.evaluate(()=>{window.fixtureIdentity='home-fixture'; window.canEditPreview=true; window.dispatchEvent(new CustomEvent('portal:identity-changed'));}); await page.getByLabel('文件草稿').waitFor({state:'visible'});
    assert.equal(await page.getByLabel('文件草稿').inputValue(),'recoverable draft');
    await page.evaluate(()=>{access.setPresented(false); window.holdPreview=true; window.restoring=access.setPresented(true);});
    assert.equal(await page.getByLabel('文件草稿').isVisible(),false);
    await page.evaluate(async()=>{access.destroy(); finishPreview(); await restoring;});
    assert.equal(await page.locator('#root').textContent(),'','late authorization completion never recreates a destroyed view');
    assert.deepEqual(errors,[]);
    await page.setViewportSize({width:1200,height:860});
    await page.goto(`http://127.0.0.1:${server.address().port}/home`);
    await page.waitForFunction(()=>!!window.PortalHome);
    const quoted=await page.evaluate(reference=>PortalChatWorkspaceComposer.createConversationSelectionReference(reference),reference);
    assert.equal(quoted.content_type,'text/x-portal-preview-reference'); assert.ok(quoted.reference_text.includes('resident_0123456789ab/msg_abcdefabcdef'));
    assert.equal(await page.locator('.homeDialogueChat').count(),0,'optional component stays lazy on first visit');
    const initial = await page.evaluate(()=>PortalWorkbench.get('home').getState());
    assert.ok(initial.hidden.includes('dialogue-display')); assert.ok(initial.hidden.includes('get-me-aligned')); assert.ok(initial.hidden.includes('my-pulse-publish'));
    await page.evaluate(()=>{
      for(const key of Object.keys(localStorage)) {
        try {const stored=JSON.parse(localStorage.getItem(key)); if(stored?.panelIds?.includes('get-me-aligned')) {
          stored.contentSchemaVersion=1; stored.panelIds=stored.panelIds.filter(id=>id!=='dialogue-display'); stored.hidden=stored.hidden.filter(id=>id!=='dialogue-display'); localStorage.setItem(key,JSON.stringify(stored));
        }}catch{}
      }
    });
    await page.reload(); await page.waitForFunction(()=>!!window.PortalHome);
    assert.equal(await page.locator('.homeDialogueChat').count(),0,'old layout does not spontaneously add the new panel');
    await page.goto(`http://127.0.0.1:${server.address().port}/home?home_component=dialogue-display`);
    await page.waitForSelector('.homeDialogueChooser',{state:'attached'});
    const beforeExpand=await page.evaluate(()=>JSON.stringify(PortalWorkbench.get('home').getState().layout));
    await page.getByRole('button',{name:'展开',exact:true}).click(); await page.getByRole('button',{name:'返回原布局',exact:true}).click();
    assert.equal(await page.evaluate(()=>JSON.stringify(PortalWorkbench.get('home').getState().layout)),beforeExpand);
    assert.deepEqual(errors,[]);
    console.log('Home dialogue browser: late results, pin, draft/save conflict, history, narrow layout, dispose, defaults, old layout and deep link passed');
  } finally {await browser.close(); await new Promise(r=>server.close(r));}
})().catch(e=>{console.error(e);process.exitCode=1;});
