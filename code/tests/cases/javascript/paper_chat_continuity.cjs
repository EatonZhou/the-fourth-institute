const fs=require('fs'),path=require('path'),http=require('http'),assert=require('assert/strict');
const {chromium,firefox}=require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const {chatWorkspaceScripts}=require('./chat_workspace_modules.cjs');
const {portalWorkbenchScripts}=require('./portal_workbench_scripts.cjs');
const input=JSON.parse(fs.readFileSync(0,'utf8')),root=path.resolve(__dirname,'../../..');
const scripts=['portal_state.js','portal_page_state.js','portal_text.js','portal_ui_primitives.js','portal_ui.js','portal_overlay.js','portal_resource_chips.js','portal_image_editor.js','portal_editor_open_service.js','portal_command_registry.js','portal_workbench_context.js',...portalWorkbenchScripts(),'resident_protocol.js',...chatWorkspaceScripts(),'paper_chat.js'];
const styles=['portal_components.css','portal_components_markdown.css','portal_components_chat_frame.css','portal_components_messages.css','portal_components_composer.css','portal_workbench.css','paper.css'];
const html=`<!doctype html><html><head><meta charset="utf-8">${styles.map(x=>`<link rel="stylesheet" href="/static/${x}">`).join('')}<style>body{margin:0}#host{position:absolute;top:40px;right:0;width:358px;height:calc(100dvh - 40px)} </style></head><body><div class="portalWorkbenchPanelFrame" id="host"><section data-portal-workbench-panel id="chat"></section></div><script src="/route.js"></script>${scripts.map(x=>`<script src="/static/${x}"></script>`).join('')}<script>
window.state={principalId:'fixture',username:'fixture'};
const deps=Object.fromEntries(PortalPaperChat.dependencies.map(k=>[k,()=>{}]));
Object.assign(deps,{state,esc:String,md:String,paperApi:{request:async()=>({principal:'fixture',slots:[],legacy:[]})}});
window.paperChat=PortalPaperChat.createChat(deps);const descriptor=paperChat.paperChatDescriptor();
window.panel=PortalChatWorkspace.panelPackage.create({...descriptor.options,root:document.querySelector('#chat')});descriptor.onShow({panel:{runtime:panel}});
window.draw=long=>{const empty=descriptor.options.conversationView().project({rendering:{empty_text:'没有找到可展示的 Codex session 历史。'}}).rendering.empty_text;
 panel.workspace.applyContract({composer:{can_send:true,can_edit:true},messages:long?Array.from({length:30},(_,i)=>({id:'m'+i,role:'assistant',content:'Saved conversation '.repeat(30)})):[],rendering:{empty_text:empty}});};draw(false);window.ready=true;
</script></body></html>`;
const server=http.createServer((req,res)=>{if(req.url==='/'){res.setHeader('content-type','text/html');return res.end(html);}if(req.url==='/route.js'){res.setHeader('content-type','text/javascript');return res.end(input.routeScript);}
const file=path.join(root,'management_portal',new URL(req.url,'http://fixture').pathname);if(!fs.existsSync(file)||!fs.statSync(file).isFile()){res.statusCode=404;return res.end();}res.setHeader('content-type',path.extname(file)==='.css'?'text/css':'text/javascript');res.end(fs.readFileSync(file));});
(async()=>{await new Promise(r=>server.listen(0,'127.0.0.1',r));try{for(const [name,type] of [['chromium',chromium],['firefox',firefox]]){const browser=await type.launch({headless:true});try{const page=await browser.newPage();await page.goto('http://127.0.0.1:'+server.address().port);await page.waitForFunction(()=>window.ready);
const result=await page.evaluate(async()=>{
 const calls=[];let surface={};panel.openSurface=async target=>{calls.push(target);surface=target;panel.workspace.setDraftText('User draft');draw(true);return {resident_id:'resident_tmp'};};panel.surface=()=>surface;
 state.researchLegacyKey='paper:paper_111111111111';state.requestedChatWorkspaceId=state.researchLegacyKey;state.selectedId='paper_111111111111';
 await paperChat.loadChatHistory();const root=panel.workspace.root;const draft=panel.workspace.inputText();
 state.selectedId='paper_222222222222';state.requestedChatWorkspaceId='';await paperChat.loadChatHistory({paperId:state.selectedId});
 if(calls.length!==1||paperChat.activeChatWorkspaceId()!==state.researchLegacyKey||panel.workspace.root!==root||panel.workspace.inputText()!==draft)throw Error('legacy identity/draft changed across papers');
 state.researchSlot={slot_id:'slot_123',resident_id:'resident_tmp'};surface={workspace_key:'research:slot_123'};state.selectedId='';await paperChat.loadChatHistory();if(calls.length!==1)throw Error('research surface reopened');
 const host=document.createElement('div');host.className='leftBody';document.body.append(host);
 for(const kind of ['repository','personal']){const row=document.createElement('div');row.className='paperLeaf';row.innerHTML='<button class="folderLine">LIBERO-PRO</button><button class="portalButton is-icon is-quiet paperFavorite" aria-label="Favorite">☆</button>';host.append(row);const button=row.querySelector('.paperFavorite'),b=button.getBoundingClientRect(),r=row.getBoundingClientRect();if(b.width<28||b.height<28||r.height>32||getComputedStyle(button).borderTopWidth!=='0px')throw Error('noncompact '+kind);}
 return {calls:calls.length,key:paperChat.activeChatWorkspaceId()};
});assert.equal(result.calls,1);console.log(name+': paper switch retains legacy target/draft/root and research surface; shared Repository/Personal star 28px target and compact row');}finally{await browser.close();}}}finally{server.close();}})().catch(e=>{console.error(e);server.close();process.exitCode=1;});
