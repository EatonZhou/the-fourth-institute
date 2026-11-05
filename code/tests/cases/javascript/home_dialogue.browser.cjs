// Exercise host integration with real DocumentSession and Preview; only transport
// and independently owned adapters are fixtures. Live auth is a separate check.
const {chromium} = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const homeAction = require('./home_dialogue_controls.cjs');
const fs = require('node:fs'), path = require('node:path'), http = require('node:http'), assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../../../management_portal');
const groups = JSON.parse(fs.readFileSync(path.join(root,'script_groups.json')));
const homeHtml = fs.readFileSync(path.join(root,'index.html'),'utf8').replace(/<script data-portal-script-group="([^"]+)"><\/script>/g,(_,id)=>groups[id].map(src=>`<script src="${src}"></script>`).join(''));
const scripts = ['portal_state.js','portal_page_state.js','portal_text.js','portal_translations.js','portal_i18n.js','portal_ui_primitives.js',
  'portal_document_statistics.js','portal_preview_contract.js','portal_preview_runtime.js','portal_pdf_preview.js','portal_preview_renderers.js',
  ...groups['portal-workspace-surface'].map(x=>x.replace(/^\/static\//,'')),
  'portal_agent_preview_source.js','markdown.js','portal_display_protocol.js','home_dialogue_state.js','portal_file_preview.js','home_dialogue.js'];
const server = http.createServer((req,res) => {
  const pathname = new URL(req.url,'http://localhost').pathname;
  if(pathname === '/static/portal_resource_routes.js') {res.setHeader('Content-Type','text/javascript');return res.end('window.PortalResourceRoutes={schema:"tfi.resource-portal-routes.v1",routes:[],text_resource_types:[],identity_parameters:[]};');}
  if(pathname === '/home') {res.setHeader('Content-Type','text/html');return res.end(homeHtml);}
  if(pathname.startsWith('/api/')) {res.setHeader('Content-Type','application/json');return res.end(JSON.stringify(pathname==='/api/me'?{username:'home-fixture',principal:{kind:'user',id:'home-fixture'},access:{allows:{}}}:{items:[],accounts:[],usage:[],result:{},schema:'tfi.hatg-instruction-result.v2',status:'committed'}));}
  if(pathname === '/') {res.setHeader('Content-Type','text/html'); return res.end('<!doctype html><meta charset="utf-8"><link rel="stylesheet" href="/static/portal_design_system.css"><link rel="stylesheet" href="/static/home_dialogue.css"><style>body{margin:0}#root{height:800px}</style>' + scripts.map(s=>`<script src="/static/${s}"></script>`).join('')+'<div id="root"></div>');}
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
      window.workspaceContent='workspace original'; window.failPermission=false;
      window.PortalInstructionClient={execute:async(name,args,options)=>{
        calls.push({name,args,options});
        if(name==='ResolveResource') return {target:{target_kind:'file',exists:true,address:args.address,file_reference:{workspace_id:'workspace_0123456789ab',relative_path:'notes 100%.md'}}};
        if(name==='CheckPermission') {if(failPermission)throw new Error('权限服务暂不可用'); return {allowed:canEditPreview};}
        if(rejectSave)throw new Error('保存冲突');
        if(name==='UpdateWorkspaceFile') {window.workspaceContent=args.content; return {version:'updated'};}
        return {resource:{content:args.changes.text,version:2}};
      }};
      window.PortalResourceChips={resourceAddressFromPreviewRequest:r=>/^(article|workspace)_/.test(r.resourceAddress)?r.resourceAddress:'',
        loadReference:async address=>({title:'真实目标 '+address.slice(-12)}),
        loadPreview:async (address,options)=>{previewReads++; calls.push({previewOptions:options}); if(window.holdPreview) await new Promise(resolve=>window.finishPreview=resolve); if(denyPreview)throw Object.assign(new Error('当前来源不可读'),{status:403}); const workspace=address.startsWith('workspace_'); return {source:'resource',resource_id:address.split('/')[0],resource_address:address,title:'真实预览契约',kind:'text',content:workspace?workspaceContent:'original',content_url:'/api/resources/content?address='+encodeURIComponent(address),version:1,capabilities:{edit:!workspace&&canEditPreview,save:!workspace&&canEditPreview}};}};
      window.PortalFileLinks={describe:()=>null};
      window.PortalNav={personalAssistantTarget:async()=>({resident_id:'resident_0123456789ab',lineage_id:'lineage_0123456789ab',title:'current facade'})};
      window.PortalChatWorkspace={panelPackage:{create:options=>{window.chatOptions=options; options.root.textContent='Chat transport fixture'; return {workspace:{addReference:value=>calls.push({reference:value})},setPresented:value=>calls.push({chatPresented:value}),destroy:()=>calls.push({chatDestroyed:true})};}}};
      window.PortalHomeReportView={create:({root,onSelection})=>{window.reportSelection=onSelection; let presented=true; return {show(target){calls.push({reportShowPresented:presented}); root.textContent=target.id; return new Promise(resolve=>pending.push({resolve,target}));},setPresented(value){presented=value; calls.push({reportPresented:value}); if(window.rejectPresentation&&value) return Promise.resolve({error:new Error('地图恢复失败')});},destroy(){calls.push({adapterDestroyed:true});}};}};
      window.controller=PortalHomeDialogue.connect({root:document.querySelector('#root'),context:{currentUserReady:Promise.resolve()}});
    });
    await page.waitForFunction(()=>!!window.chatOptions);
    if (process.argv.includes('--tabs-only')) {
      await require('./home_preview_tabs.cjs')(page, assert, homeAction);
      assert.deepEqual(errors, []);
      console.log('Home shared Preview replacement/pinning/dedup/dirty/error/race/history passed');
      return;
    }
    // Manual navigation and pinned late result.
    await page.evaluate(()=>PortalPreview.request(PortalDisplayProtocol.previewRequest({view:'report',target:{kind:'lineage',id:'lineage_0123456789ab'}})));
    await page.waitForFunction(()=>pending.length > 0).catch(async error=>{console.error(await page.locator('#root').innerText());console.error(errors);throw error;});
    await page.evaluate(async()=>{
      pending.shift().resolve();
      const failedTarget={resident_id:'resident_0123456789ab'}, sentTarget={resident_id:'resident_0123456789ab'};
      window.failedProtocol=JSON.parse((await chatOptions.submissionContext(failedTarget)).context_text.split('示例结构：')[1].split('\n')[0]);
      window.protocol=JSON.parse((await chatOptions.submissionContext(sentTarget)).context_text.split('示例结构：')[1].split('\n')[0]);
      chatOptions.onAcknowledgement({}, {target:sentTarget});
      chatOptions.onDetail({snapshot:{messages:[{role:'assistant',message_id:'failed-result',content_parts:[{type:'text',text:'```portal-home-display\n'+JSON.stringify({...failedProtocol,target:{kind:'lineage',id:'lineage_abcdefabcdef'}})+'\n```'}]}]}});
    });
    assert.equal(await page.locator('.homeDialogueEntry').count(),1,'failed send is not acknowledged by the next success');
    // Native Preview owns the complete chrome at both desktop and narrow sizes.
    for (const width of [1200,390]) {
      await page.setViewportSize({width,height:860});
      if(width===390) await page.locator('.homeDialogueTabs').getByRole('button',{name:'展示',exact:true}).click();
      assert.equal(await page.locator('.homeDialoguePreview').evaluate(el=>!!el.__portalWorkbenchPreviewPanel),true);
      assert.equal(await page.locator('.homeDialogueDisplayBar').count(),0);
      await page.locator('.homeDialoguePreview [data-portal-preview-path]').click();
      const pathInput=page.locator('.homeDialoguePreview .portalPreviewPathInput');
      await pathInput.press('Escape');
    }
    await page.setViewportSize({width:1200,height:860});
    assert.equal(await page.evaluate(async()=>{
      const before=calls.filter(c=>'reportPresented' in c).length;
      document.querySelector('#root').style.height='780px'; await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
      return calls.filter(c=>'reportPresented' in c).length-before;
    }),0,'resizing an active adapter does not repeat setPresented or reload');
    // Domain reports have no filesystem address. Their reference retains the actual request, focus and sources.
    await page.evaluate(()=>reportSelection({kind:'report',target:{kind:'task',id:'resident_0123456789ab/msg_abcdefabcdef'},focus:'direction-x',label:'任务进展',text:'实际任务状态',sources:[{address:'article_0123456789ab',version:3}]}));
    await page.locator('.homeDialoguePreview .portalPreviewSelectionToolbar').click();
    const reference=await page.evaluate(()=>calls.find(c=>c.reference).reference);
    assert.equal(reference.path,undefined); assert.ok(reference.text.includes('resident_0123456789ab/msg_abcdefabcdef')); assert.ok(reference.text.includes('article_0123456789ab')); assert.deepEqual(reference.source_message_ids,['msg_abcdefabcdef']);
    await homeAction(page,'固定展示');
    await page.evaluate(()=>{chatOptions.onDetail({snapshot:{messages:[{role:'assistant',message_id:'msg_123456789abc',content_parts:[{type:'text',text:'```portal-home-display\n'+JSON.stringify({...protocol,target:{kind:'lineage',id:'lineage_abcdefabcdef'}})+'\n```'}]}]}});});
    await page.waitForFunction(()=>calls.filter(c=>'reportShowPresented' in c).length===2).catch(async error=>{
      throw new Error(`${error.message}; entry failures: ${await page.locator('.homeDialogueEntryNotice').allTextContents()}`);
    });
    assert.equal(await page.evaluate(()=>calls.filter(c=>'reportShowPresented' in c).at(-1).reportShowPresented),false,'queued report receives hidden state before show');
    assert.ok((await page.locator('.homeDialoguePreview [data-portal-preview-path]').textContent()).includes('0123456789ab'));
    await page.waitForFunction(()=>pending.length > 0).catch(async error=>{console.error(await page.locator('#root').innerText());console.error(errors);throw error;});
    await page.evaluate(()=>pending.shift().resolve());
    assert.ok((await page.locator('.homeDialoguePreview [data-portal-preview-path]').textContent()).includes('0123456789ab'));
    // Existing preview/session editing, failed save, history preservation, then actual save contract.
    await page.evaluate(()=>PortalPreview.request({resourceAddress:'article_0123456789ab'}));
    await page.getByRole('button',{name:'编辑当前文件的源码',exact:true}).click(); await page.getByRole('textbox',{name:'文件草稿',exact:true}).fill('draft kept');
    await page.evaluate(()=>{window.rejectSave=true;}); await page.getByRole('button',{name:'保存当前文件',exact:true}).click(); await page.getByText('保存冲突',{exact:true}).waitFor();
    await homeAction(page,'返回上一展示');
    const values=await page.locator('.homeDialoguePreview [data-portal-document-tab]').evaluateAll(options=>options.map(o=>o.dataset.portalDocumentTab));
    await page.locator(`[data-portal-document-tab=${JSON.stringify(values.at(-1))}]`).click(); assert.equal(await page.getByRole('textbox',{name:'文件草稿',exact:true}).inputValue(),'draft kept');
    await page.evaluate(()=>{window.rejectSave=false;}); await page.getByRole('button',{name:'保存当前文件',exact:true}).click();
    await page.waitForFunction(()=>calls.filter(c=>c.name==='UpdateResource').length===2);
    assert.equal(await page.evaluate(()=>calls.filter(c=>c.name==='UpdateResource').at(-1).args.changes.text),'draft kept');
    await page.evaluate(()=>{window.rejectPresentation=true;}); await page.locator(`[data-portal-document-tab=${JSON.stringify(values[1])}]`).click();
    await page.getByText('地图恢复失败',{exact:true}).waitFor();
    await page.evaluate(()=>{window.rejectPresentation=false;}); await page.locator(`[data-portal-document-tab=${JSON.stringify(values.at(-1))}]`).click(); await page.getByRole('textbox',{name:'文件草稿',exact:true}).waitFor({state:'visible'});
    // Keyboard and pointer resizing; narrow component in a wide desktop viewport.
    const divider=page.getByRole('separator',{name:'调整对话与展示宽度',includeHidden:true});
    await divider.focus(); await divider.press('ArrowRight'); assert.equal(await divider.getAttribute('aria-valuenow'),'45');
    const dividerBox=await divider.boundingBox(); await page.mouse.move(dividerBox.x+4,dividerBox.y+40); await page.mouse.down(); await page.mouse.move(600,dividerBox.y+40); await page.mouse.up();
    const ratio=await divider.getAttribute('aria-valuenow'); assert.equal(ratio,'50');
    const beforeResize=await page.evaluate(()=>calls.filter(c=>'reportPresented' in c).length);
    await page.evaluate(()=>{document.querySelector('#root').style.maxWidth='520px';}); await page.waitForFunction(()=>document.querySelector('#root').dataset.narrow==='true');
    await homeAction(page,'展开'); await page.waitForFunction(()=>document.querySelector('#root').dataset.narrow==='false');
    await homeAction(page,'返回原布局'); await page.waitForFunction(()=>document.querySelector('#root').dataset.narrow==='true');
    assert.equal(await divider.getAttribute('aria-valuenow'),ratio);
    assert.equal(await page.evaluate(()=>calls.filter(c=>'reportPresented' in c).length),beforeResize,'resize does not reload hidden history adapters');
    await page.setViewportSize({width:480,height:800}); await page.locator('.homeDialogueTabs').getByRole('button',{name:'对话',exact:true}).click();
    assert.equal(await page.locator('.homeDialogueDisplay').isVisible(),false);
    await page.locator('.homeDialogueTabs').getByRole('button',{name:'展示',exact:true}).click(); assert.equal(await page.getByRole('textbox',{name:'文件草稿',exact:true}).inputValue(),'draft kept');
    await page.evaluate(()=>controller.destroy()); assert.equal(await page.locator('#root').textContent(),'');
    // The real host and preview own a single recoverable failure, including restoration.
    await page.setViewportSize({width:945,height:860});
    await page.evaluate(()=>{
      localStorage.clear(); document.querySelector('#root').style.maxWidth=''; window.denyPreview=true;
      window.readsBeforeRestore=previewReads;
      PortalPageState.create('home-dialogue-display',{user:()=> 'home-fixture'}).write({schemaVersion:2,
        entries:[{intent:{view:'preview',target:{address:'article_0123456789ab'}}}],active:0,pane:'display'});
      window.controller=PortalHomeDialogue.connect({root:document.querySelector('#root'),context:{currentUserReady:Promise.resolve({username:'home-fixture'})}});
    });
    await page.waitForFunction(()=>document.querySelectorAll('.homeDialogueEntry').length===1);
    assert.equal(await page.evaluate(()=>previewReads),await page.evaluate(()=>readsBeforeRestore),'saved targets do not trigger a default read');
    assert.equal(await page.locator('.homeDialoguePreview .portalDocumentTab.is-active').count(),0);
    assert.equal(await page.locator('.portalFilePreviewFailure').count(),0,'no default failure or preview adapter');
    if(process.env.SCREENSHOT_DIR){fs.mkdirSync(process.env.SCREENSHOT_DIR,{recursive:true});await page.screenshot({path:path.join(process.env.SCREENSHOT_DIR,'preview-idle-945.png')});}
    await page.locator('.homeDialoguePreview [data-portal-document-tab]').first().click();
    const failure=page.locator('.portalFilePreviewFailure');
    await failure.getByRole('heading',{name:'暂时无法访问此文件'}).waitFor();
    assert.equal(await failure.locator('code').textContent(),'article_0123456789ab');
    assert.equal(await page.getByRole('button',{name:'重试访问',exact:true}).count(),1);
    assert.equal(await page.getByRole('button',{name:'重试读取',exact:true}).count(),0);
    assert.ok((await page.locator('.homeDialoguePreview .portalDocumentTab.is-active').getAttribute('title')).includes('读取失败'));
    await failure.getByText('查看错误详情',{exact:true}).click();
    assert.equal(await page.getByText('当前来源不可读',{exact:true}).count(),1);
    await failure.getByText('查看错误详情',{exact:true}).click();
    if(process.env.SCREENSHOT_DIR){fs.mkdirSync(process.env.SCREENSHOT_DIR,{recursive:true});await page.screenshot({path:path.join(process.env.SCREENSHOT_DIR,'preview-denied-945.png')});}
    await page.setViewportSize({width:390,height:844});
    await page.waitForFunction(()=>document.querySelector('#root').dataset.narrow==='true');
    await page.locator('.homeDialogueTabs').getByRole('button',{name:'展示',exact:true}).click();
    await failure.waitFor({state:'visible'});
    assert.ok(await failure.evaluate(el=>el.scrollWidth<=el.clientWidth),'failure card fits narrow display');
    if(process.env.SCREENSHOT_DIR)await page.screenshot({path:path.join(process.env.SCREENSHOT_DIR,'preview-denied-390.png')});
    await page.locator('.homeDialogueTabs').getByRole('button',{name:'对话',exact:true}).click();
    await page.locator('.homeDialogueTabs').getByRole('button',{name:'展示',exact:true}).click();
    await failure.waitFor({state:'visible'});
    assert.equal(await page.getByRole('button',{name:'重试读取',exact:true}).count(),0,'restoring a denied preview does not duplicate the failure');
    await page.evaluate(()=>{window.denyPreview=false;});
    await failure.getByRole('button',{name:'重试访问',exact:true}).click();
    await failure.waitFor({state:'hidden'});
    await page.waitForFunction(()=>document.querySelector('.portalFileDocument').textContent.includes('original'));
    assert.ok(!(await page.locator('.homeDialoguePreview .portalDocumentTab.is-active').getAttribute('title')).includes('读取失败'),'successful internal retry updates the host status');
    await page.evaluate(()=>{window.denyPreview=true;window.dispatchEvent(new Event('portal:permissions-changed'));});
    await failure.waitFor({state:'visible'});
    assert.equal(await page.locator('.portalFileDocument').textContent(),'','permission loss clears the previously rendered content');
    await failure.getByRole('button',{name:'打开其他内容',exact:true}).click();
    assert.equal(await page.locator('.homeDialoguePreview .portalPreviewPathInput').inputValue(),'article_0123456789ab');
    await page.locator('.homeDialoguePreview .portalPreviewPathInput').press('Escape');
    await failure.getByRole('button',{name:'关闭此展示',exact:true}).click();
    await failure.waitFor({state:'detached'});
    assert.equal(await page.getByRole('button',{name:'重试访问',exact:true}).count(),0);
    await page.evaluate(()=>{controller.destroy();window.denyPreview=false;});
    await page.evaluate(()=>{
      window.mountFilePreview=()=>{
        const root=document.querySelector('#root');root.replaceChildren();
        const pane=document.createElement('div');root.append(pane);
        const preview=PortalWorkspaceSurface.panelPackages.preview.create({root:pane,explorer:false});
        const body=preview.surface.element('preview');body.replaceChildren();
        let alive=true,file;
        const update=()=>{if(!alive||!file)return;const toolbar=file.toolbar();preview.setPresentation({tabs:[],path:file.snapshot().payload?.resource_address || '',document:toolbar.document,download:toolbar.download,actions:toolbar.actions});};
        file=PortalFilePreview.create({root:body,onChange:()=>queueMicrotask(update)});
        return {...file,destroy(){alive=false;file.destroy();preview.destroy();root.replaceChildren();}};
      };
    });
    // Real DocumentSession + Preview access lifecycle. No cached content is revealed before the new read.
    await page.evaluate(()=>{window.access=mountFilePreview(); access.setPresented(false); window.readsBefore=previewReads; return access.show({address:'article_0123456789ab'});});
    assert.equal(await page.evaluate(()=>previewReads),await page.evaluate(()=>readsBefore),'hidden preview does not read');
    await page.evaluate(()=>access.setPresented(true)); await page.getByRole('button',{name:'编辑当前文件的源码',exact:true}).click(); await page.getByRole('textbox',{name:'文件草稿',exact:true}).fill('recoverable draft');
    assert.ok(await page.evaluate(()=>calls.filter(c=>c.previewOptions).every(c=>c.previewOptions.allowCached===false && c.previewOptions.readOnly===false)),'fresh server capabilities determine editing');
    await page.evaluate(()=>{access.setPresented(false); window.denyPreview=true;}); await page.evaluate(()=>access.setPresented(true));
    assert.equal(await page.getByRole('textbox',{name:'文件草稿',exact:true}).isVisible(),false); assert.equal(await page.locator('.portalFileDocument').textContent(),'');
    assert.equal(await page.evaluate(()=>access.snapshot().draft),'recoverable draft');
    await page.evaluate(()=>{window.denyPreview=false;}); await page.getByRole('button',{name:'重试访问',exact:true}).click(); await page.getByRole('textbox',{name:'文件草稿',exact:true}).waitFor({state:'visible'});
    assert.equal(await page.getByRole('textbox',{name:'文件草稿',exact:true}).inputValue(),'recoverable draft');
    await page.evaluate(()=>{window.canEditPreview=false; window.dispatchEvent(new CustomEvent('portal:permissions-changed'));});
    await page.waitForFunction(()=>document.querySelector('.portalFileEditor').disabled);
    assert.equal(await page.getByRole('button',{name:'保存当前文件',exact:true}).isEnabled(),false);
    await page.evaluate(()=>{window.fixtureIdentity='other-user'; window.dispatchEvent(new CustomEvent('portal:identity-changed'));});
    assert.equal(await page.getByRole('textbox',{name:'文件草稿',exact:true}).isVisible(),false); assert.equal(await page.getByRole('textbox',{name:'文件草稿',exact:true,includeHidden:true}).inputValue(),'');
    await page.evaluate(()=>{window.fixtureIdentity='home-fixture'; window.canEditPreview=true; window.dispatchEvent(new CustomEvent('portal:identity-changed'));}); await page.getByRole('textbox',{name:'文件草稿',exact:true}).waitFor({state:'visible'});
    assert.equal(await page.getByRole('textbox',{name:'文件草稿',exact:true}).inputValue(),'recoverable draft');
    await page.evaluate(()=>{access.setPresented(false); window.holdPreview=true; window.restoring=access.setPresented(true);});
    assert.equal(await page.getByRole('textbox',{name:'文件草稿',exact:true}).isVisible(),false);
    await page.evaluate(async()=>{access.destroy(); finishPreview(); await restoring;});
    assert.equal(await page.locator('#root').textContent(),'','late authorization completion never recreates a destroyed view');
    // Workspace resources expose no UpdateResource text capability. Use the
    // server-resolved Workspace address and current-actor file permission.
    await page.evaluate(async()=>{
      window.holdPreview=false; window.workspaceAddress='workspace_0123456789ab/file/notes 100%25.md';
      window.filePreview=mountFilePreview();
      await filePreview.show({address:workspaceAddress});
    });
    await page.getByRole('button',{name:'编辑当前文件的源码',exact:true}).click(); await page.getByRole('textbox',{name:'文件草稿',exact:true}).fill('workspace saved');
    await page.getByRole('button',{name:'保存当前文件',exact:true}).click();
    await page.waitForFunction(()=>calls.some(c=>c.name==='UpdateWorkspaceFile'));
    const fileSave=await page.evaluate(()=>calls.find(c=>c.name==='UpdateWorkspaceFile'));
    assert.equal(fileSave.args.address,'workspace_0123456789ab/file/notes 100%25.md'); assert.equal(fileSave.args.content,'workspace saved');
    assert.equal(fileSave.options.expectedVersion[fileSave.args.address],require('node:crypto').createHash('sha256').update('workspace original').digest('hex'));
    assert.deepEqual(await page.evaluate(()=>calls.filter(c=>c.name==='CheckPermission').at(-1).args),{principal_id:'home-fixture',a:'UpdateWorkspaceFile',resource_id:'workspace_0123456789ab'});
    await page.evaluate(async()=>{filePreview.destroy(); window.filePreview=mountFilePreview(); await filePreview.show({address:workspaceAddress});});
    assert.equal(await page.evaluate(()=>filePreview.snapshot().payload.content),'workspace saved','fresh read returns saved file content');
    assert.ok((await page.locator('.portalFileDocument').innerText()).includes('workspace saved'));
    await page.evaluate(async()=>{window.canEditPreview=false; filePreview.setPresented(false); await filePreview.setPresented(true);});
    assert.equal(await page.getByRole('button',{name:'编辑当前文件的源码',exact:true}).isEnabled(),false); assert.equal(await page.locator('.portalFileDocument').isVisible(),true,'readable but not writable stays read-only');
    await page.evaluate(async()=>{window.canEditPreview=true; window.failPermission=true; filePreview.setPresented(false); await filePreview.setPresented(true);});
    assert.equal(await page.getByRole('button',{name:'编辑当前文件的源码',exact:true}).isEnabled(),false); await page.getByText('文件可读，但无法核对编辑权限：权限服务暂不可用',{exact:true}).waitFor();
    await page.evaluate(()=>{window.failPermission=false;}); await page.getByRole('button',{name:'重试访问',exact:true}).click(); await page.getByRole('button',{name:'编辑当前文件的源码',exact:true}).waitFor();
    await page.evaluate(()=>filePreview.destroy());
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
    await page.waitForSelector('.homeDialoguePreview',{state:'attached'});
    await page.evaluate(()=>PortalPreview.request({resourceAddress:'article_0123456789ab'}));
    const beforeExpand=await page.evaluate(()=>JSON.stringify(PortalWorkbench.get('home').getState().layout));
    await homeAction(page,'展开'); await homeAction(page,'返回原布局');
    assert.equal(await page.evaluate(()=>JSON.stringify(PortalWorkbench.get('home').getState().layout)),beforeExpand);
    assert.deepEqual(errors,[]);
    console.log('Home dialogue browser: late results, pin, draft/save conflict, history, narrow layout, dispose, defaults, old layout and deep link passed');
  } finally {await browser.close(); await new Promise(r=>server.close(r));}
})().catch(e=>{console.error(e);process.exitCode=1;});
