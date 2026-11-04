'use strict';
// Production ResidentChatPanel, Markdown, Home host, Preview/DocumentSession and
// report adapter. Synthetic conversation and resource transport; no User session
// or provider, and no formal TAU browser is used.
const fs=require('node:fs'), path=require('node:path'), http=require('node:http'), assert=require('node:assert/strict');
const {chromium}=require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const {chatWorkspaceScripts}=require('./chat_workspace_modules.cjs');
const {portalWorkbenchScripts}=require('./portal_workbench_scripts.cjs');
const homeAction=require('./home_dialogue_controls.cjs');
const root=path.resolve(__dirname,'../../../management_portal');
const groups=JSON.parse(fs.readFileSync(path.join(root,'script_groups.json')));
const modules=[...new Set(['portal_state.js','portal_page_state.js','portal_text.js','portal_translations.js','portal_i18n.js',
  'portal_ui_primitives.js','portal_ui.js','portal_overlay.js','portal_resource_routes.js','portal_resource_chips.js','portal_image_editor.js',
  'portal_editor_open_service.js','portal_command_registry.js','portal_workbench_context.js',...portalWorkbenchScripts(),
  'portal_resident_service.js','resident_protocol.js','markdown.js',...chatWorkspaceScripts(),
  'portal_preview_contract.js','portal_preview_runtime.js','portal_pdf_preview.js','portal_preview_renderers.js',
  ...groups['portal-workspace-surface'].map(s=>s.replace('/static/','')),'portal_agent_preview_source.js',
  'project_common.js','project_map_layout.js','project_map_drawer.js','project_map.js','home_map_view.js',
  'home_report.js','home_dialogue_state.js','home_dialogue_preview.js','home_dialogue.js'])];
const css=[...fs.readFileSync(path.join(root,'index.html'),'utf8').matchAll(/href="(\/static\/[^"?]+\.css)"/g)].map(m=>m[1]);
const out=process.env.HOME_DIALOGUE_EVIDENCE||'/tmp/home-dialogue-actions';fs.mkdirSync(out,{recursive:true});
const evidence={boundary:'real components; synthetic conversation/resource transport; no borrowed User or TAU session',checks:[],errors:[],requests:[]};
const contextOnly=process.argv.includes('--submission-context-only');
evidence.scope=contextOnly?'submission context and Preview script boundary':'message actions, submission context and Preview script boundary';
function check(name,actual,expected){evidence.checks.push({name,actual,expected});assert.deepEqual(actual,expected,name);}
const server=http.createServer((req,res)=>{
  const url=new URL(req.url,'http://fixture');
  if(url.pathname==='/') {res.setHeader('Content-Type','text/html');return res.end('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'+css.map(s=>`<link rel="stylesheet" href="${s}">`).join('')+'<style>body{margin:0}#root{height:calc(100dvh - 16px);margin:8px}</style>'+modules.map(s=>`<script src="/static/${s}"></script>`).join('')+'<main id="root" data-portal-workbench-panel="dialogue-display"></main>');}
  if(url.pathname==='/static/portal_resource_routes.js'){res.setHeader('Content-Type','text/javascript');return res.end('window.PortalResourceRoutes={schema:"tfi.resource-portal-routes.v1",routes:[],text_resource_types:[],identity_parameters:[]};');}
  if(['/api/home-report','/api/home-report/save'].includes(url.pathname)) {evidence.requests.push(url.pathname+url.search);res.setHeader('Content-Type','application/json');return res.end(JSON.stringify({report_id:'report_0123456789ab',revision:1,latest_revision:1,reference:{view:'report',target:{kind:'report',id:'report_0123456789ab',revision:1}},title:'示例项目的真实组件汇报',target:{kind:'project',id:'demo'},version:'v1',checked_at:'2026-11-04T00:00:00Z',warnings:[],sources:[],counts:{},rows:[{id:'work',title:'已核对来源',body:'组件读取了指定项目',sources:[]}]}));}
  if(url.pathname==='/api/home-report/validate'){res.setHeader('Content-Type','application/json');return res.end('{"readable":true}');}
  const file=path.resolve(root,'.'+url.pathname);
  if(file.startsWith(root+'/static/')&&fs.existsSync(file)&&fs.statSync(file).isFile()){res.setHeader('Content-Type',file.endsWith('.css')?'text/css':file.endsWith('.js')?'text/javascript':'application/octet-stream');return res.end(fs.readFileSync(file));}
  res.statusCode=404;res.end();
});
(async()=>{await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const browser=await chromium.launch({headless:true});
  let page;
  try {
    page=await browser.newPage({viewport:{width:1440,height:1000}});page.setDefaultTimeout(8000);
    page.on('pageerror',error=>{evidence.errors.push(error.message);console.error(error.message);});
    await page.goto('http://127.0.0.1:'+server.address().port);
    await page.evaluate(()=>{
      window.PortalIdentity={peek:()=>({username:'fixture'}),username:()=> 'fixture'};
      window.PortalAPI={get:async()=>({config:{},restart:null}),post:async()=>({})};
      window.previewCalls=[];window.deny=false;
      window.instructionCalls=[];
      window.PortalInstructionClient={execute:async(name,args)=>{
        instructionCalls.push({name,args});
        if(name==='ListInputHistory')return {entries:[]};
        if(name==='CheckPermission')return {allowed:true};
        if(name==='GetProject')return {project:{id:args.project_id},team:{lineages:[]}};
        if(name==='GetResearchMap')return {revision:1,goal:'示例研究地图',directions:[{direction_id:'direction_0123456789ab',title:'核对来源',stance:'explore',teams:[]}],relations:[],layout:{},notes:[],outputs:{}};
        return {};
      }};
      window.PortalResourceChips={...PortalResourceChips,loadReference:async address=>({title:address}),
      resourceAddressFromPreviewRequest:r=>r.resourceAddress||'',
      loadPreview:async(address,options)=>{
        previewCalls.push({address,allowCached:options.allowCached});
        if(deny)throw Object.assign(new Error('当前来源不可读'),{status:403});
        return {source:'resource',resource_address:address,resource_id:address.split('/')[0],title:address.includes('slides')?'研究汇报幻灯片':'来源笔记',kind:'text',content:'# 研究进展\n\n来自经过授权的内容读取入口。',content_url:'/content',version:1,capabilities:{edit:true,save:true}};
      }};
      window.PortalNav={personalAssistantTarget:async()=>({resident_id:'resident_0123456789ab',title:'个人门面'})};
      const create=PortalChatWorkspace.panelPackage.create;
      window.PortalChatWorkspace={...PortalChatWorkspace,panelPackage:{...PortalChatWorkspace.panelPackage,create(options){
        window.chatOptions=options;window.panel=create({...options,autoLoad:false});return panel;
      }}};
      window.host=PortalHomeDialogue.connect({root:document.querySelector('#root'),context:{currentUserReady:Promise.resolve()}});
    });
    await page.waitForFunction(()=>!!window.panel);
    await page.evaluate(()=>{
      window.snap={messages:[],controls:{can_send:true},queue:{items:[]}};
      panel.protocol={snapshot:()=>snap,destroy(){}};
      panel.residentProjection={resident:{resident_id:'resident_0123456789ab'},chat_workspace:{schema:'portal.chat_workspace.v3',target:{kind:'resident',id:'resident_0123456789ab'},header:{title:'个人门面'},messages:[],composer:{can_send:true}}};
      window.command=(request_id='historical',extras={})=>({protocol:'portal-home-display/v1',request_id,version:1,action:'open',view:'preview',target:{address:'article_0123456789ab/file/slides.md'},...extras});
      window.displayFence=value=>'```portal-home-display\n'+JSON.stringify(value)+'\n```';
      window.message=(id,text,role='agent')=>({message_id:id,role,created_at:'2026-11-04T00:00:00Z',content_parts:[{type:'text',text}]});
      window.render=()=>panel.applySnapshot(snap);
      snap.messages=[message('msg_0123456789abcdef','可以先预览材料，再查看汇报。\n\n'+displayFence(command())+'\n\n'+displayFence(command('older',{view:'report',target:{kind:'project',id:'demo'}})))];render();
    });
    if(!contextOnly) {
    const action=name=>page.locator('.homeDialogueChat').getByRole('button',{name,exact:true});
    await action('预览幻灯片').waitFor();
    check('valid directives are compact actions',await page.locator('.homeDialogueMessageAction').count(),2);
    check('normal reply contains no internal JSON',await page.locator('.homeDialogueChat').innerText().then(t=>t.includes('portal-home-display/v1')),false);
    check('historical replies never auto-open',await page.locator('.homeDialogueEntry').count(),0);
    await action('预览幻灯片').click();await page.locator('.homeDialogueDocument:visible').waitFor();
    check('preview reads fresh source',await page.evaluate(()=>previewCalls[0].allowCached),false);
    await page.screenshot({path:path.join(out,'01-actions-preview-1440.png')});
    await page.getByRole('button',{name:'编辑',exact:true}).click();await page.getByLabel('文件草稿').fill('保留未保存的手动草稿');
    await action('查看汇报').click();await page.locator('.home-report h2').waitFor();
    check('report action uses real adapter',await page.locator('.home-report h2').innerText(),'示例项目的真实组件汇报');
    await homeAction(page,'返回上一展示');
    check('manual history action retains document draft',await page.getByLabel('文件草稿').inputValue(),'保留未保存的手动草稿');
    // Unknown nonce click is explicit manual navigation, never an automatic ack.
    await page.evaluate(()=>{window.submitted={resident_id:'resident_0123456789ab'};window.current=JSON.parse(chatOptions.submissionContext(submitted).context_text.split('示例结构：')[1].split('\n')[0]);chatOptions.onAcknowledgement({}, {target:submitted});});
    await action('查看汇报').click();await page.locator('.home-report:visible h2').waitFor();
    await homeAction(page,'固定展示');
    const before=await page.locator('.homeDialogueEntry').count();
    await page.evaluate(()=>{snap.messages.push(message('msg_late',displayFence(command(current.request_id,{target:{address:'article_abcdefabcdef'}}))));render();});
    await page.waitForFunction(n=>document.querySelectorAll('.homeDialogueEntry').length===n+1,before);
    check('late result preserves pinned/manual display',await page.locator('.home-report:visible h2').innerText(),'示例项目的真实组件汇报');
    check('hidden late preview has no read',await page.evaluate(()=>previewCalls.some(c=>c.address==='article_abcdefabcdef')),false);
    await page.evaluate(()=>render());
    check('old nonce not auto-consumed again after click',await page.locator('.homeDialogueEntry').count(),before+1);
    // Render stream preparation, invalid commands and ordinary/user examples.
    await page.evaluate(()=>{
      snap.messages=[message('msg_stream','正在整理材料。\n\n```portal-home-display\n{"protocol":"portal-home-display/v1","request_id":"stream"'),
        message('msg_unknown',displayFence(command('unknown',{protocol:'portal-home-display/v99'}))),
        message('msg_invalid','```portal-home-display\n{broken\n```'),
        message('msg_user',displayFence(command()),'user'),
        message('msg_code','```json\n{"request_id":"ordinary"}\n```'),
        message('msg_outer','````markdown\n'+displayFence(command())+'\n````'),
        message('msg_indent',displayFence(command()).split('\n').map(l=>'    '+l).join('\n')),
        message('msg_quote',displayFence(command()).split('\n').map(l=>'> '+l).join('\n'))];render();
    });
    await page.getByText('正在准备展示…',{exact:true}).waitFor();
    check('incomplete and unavailable blocks have no actions',await page.locator('.homeDialogueMessageAction').count(),0);
    check('unknown and invalid explain unavailable',await page.locator('.homeDialogueMessageNotice').filter({hasText:'暂不可用'}).count(),2);
    const code=await page.locator('.homeDialogueChat pre code').allTextContents();
    check('user and ordinary code stay literal',code.some(t=>t.includes('"request_id":"ordinary"'))&&code.some(t=>t.includes('"request_id":"historical"')),true);
    check('outer fenced example stays one literal block',code.some(t=>t.startsWith('```portal-home-display')),true);
    check('stream partial JSON is never shown',await page.locator('.homeDialogueChat').innerText().then(t=>t.includes('"request_id":"stream"')),false);
    await page.screenshot({path:path.join(out,'02-stream-and-code.png')});
    // Complete the stream using the same message id: render transitions once,
    // with an explicit button while its unknown nonce remains non-executable.
    await page.evaluate(()=>{snap.messages=[message('msg_stream','材料已就绪。\n\n'+displayFence(command('stream')))];render();});
    await action('预览幻灯片').waitFor();
    check('stream close still cannot auto-admit unknown nonce',await page.locator('.homeDialogueEntry').count(),before+1);
    await page.setViewportSize({width:390,height:844});await page.evaluate(()=>document.documentElement.style.fontSize='20px');
    await page.locator('.homeDialogueTabs').getByRole('button',{name:'对话',exact:true}).click();
    await action('预览幻灯片').focus();await page.screenshot({path:path.join(out,'03-action-390-large-font.png')});
    const box=await action('预览幻灯片').boundingBox();check('small screen action fits width',box.x>=0&&box.x+box.width<=390,true);
    await action('预览幻灯片').press('Enter');await page.locator('.homeDialogueDocument:visible').waitFor();
    check('keyboard transfers focus to displayed target',await page.locator('.homeDialogueCurrent').evaluate(e=>e===document.activeElement),true);
    await page.screenshot({path:path.join(out,'04-opened-390-large-font.png')});
    await page.locator('.homeDialogueTabs').getByRole('button',{name:'对话',exact:true}).click();
    await page.evaluate(()=>window.deny=true);await action('预览幻灯片').click();
    await page.getByText('当前来源不可读',{exact:true}).first().waitFor();
    check('explicit click still observes source denial',await page.locator('.homeDialogueEntry:not([hidden]) .homeDialogueDocument:visible').count(),0);
    await page.locator('.homeDialogueTabs').getByRole('button',{name:'对话',exact:true}).click();
    await page.evaluate(()=>{snap.messages=[message('msg_map',displayFence(command('map-history',{view:'map',target:{kind:'project',id:'demo'}})))];render();});
    await action('打开研究地图').click();
    await page.locator('.homeMapSurface:not([hidden]) svg.mapEdges').waitFor();
    check('map action loads through the real native adapter',await page.evaluate(()=>instructionCalls.some(c=>c.name==='GetResearchMap'&&c.args.project_id==='demo')),true);
    await page.screenshot({path:path.join(out,'05-map-action-390.png')});
    // Without Home's conversationView the exact same assistant output is code.
    check('global Markdown is unchanged for ordinary callers',await page.evaluate(()=>MD.render(displayFence(command())).includes('<pre>')),true);
    }
    // Exercise the native Chat send -> Protocol prepare -> service boundary,
    // not a direct call to submissionContext. Only transport is substituted;
    // no model or private conversation is involved.
    const presentationRequests=[
      '注意力机制里的查询、键和值怎样共同工作？',
      '帮我理解这两种方案的差别。',
      '直观解释注意力机制，Transformer的核心',
      '这个词的英文是什么？',
      '只用文字回答：注意力机制是什么？不要创建文件，不切换右侧。',
      '查看已有汇报 report_0123456789ab 的第 3 版。',
    ];
    evidence.submissions=await page.evaluate(async requests=>{
      window.sentBodies=[];
      window.PortalResidentService={...PortalResidentService,sendInput:async body=>{
        sentBodies.push(structuredClone(body));
        return {resident_id:body.resident_id,message_id:body.message.message_id};
      }};
      for(const text of requests) {
        await panel.send({text,plainText:text},{clear:false,focus:false});
      }
      return sentBodies;
    },presentationRequests);
    check('native Chat sends each request to the current facade',evidence.submissions.map(s=>s.resident_id),presentationRequests.map(()=>'resident_0123456789ab'));
    check('native send preserves user wording and presentation constraints',evidence.submissions.map(s=>s.message.content_parts.filter(p=>p.type==='text').map(p=>p.text).join('')),presentationRequests);
    const contexts=evidence.submissions.map(s=>s.message.content_parts.filter(p=>p.type==='context_reference'));
    check('native Protocol prepare attaches exactly one Home context',contexts.map(c=>c.length),presentationRequests.map(()=>1));
    const instructions=contexts.map(c=>c[0].context.context_text);
    const nonces=instructions.map(t=>JSON.parse(t.split('示例结构：')[1].split('\n')[0]).request_id);
    check('each native send has a fresh request nonce',new Set(nonces).size,presentationRequests.length);
    check('presentation guidance is unconditional across user wording',new Set(instructions.map(t=>t.split('示例结构：')[0])).size,1);
    check('Resident owns presentation choice without keyword gates',instructions.every(t=>t.includes('由 Resident 根据')&&t.includes('等关键词为生成条件')&&t.includes('就主动生成并在右侧打开')&&t.includes('用户只在聊天里提问也如此')),true);
    check('simple answers and explicit opt-outs remain available',instructions.every(t=>t.includes('简短文字已足以回答时直接回答')&&t.includes('不为每次问答创建文件')&&t.includes('不创建文件或不切换右侧时遵从其限制')),true);
    check('generation does not override user navigation',instructions.every(t=>t.includes('生成与自动切换是两件事')&&t.includes('固定、草稿、手选及迟到保护始终有效')),true);
    check('persist read back then preview guidance reaches transport',instructions.every(t=>t.includes('UpdateWorkspaceFile({address:')&&t.includes('ReadResource 回读')&&t.includes('成功才')&&t.includes('view:preview')&&t.includes('本次 request_id')),true);
    check('generation failure and fixed-report guidance reach transport',instructions.every(t=>t.includes('不声称已经生成')&&t.includes('report_ID+revision')&&t.includes('不为它伪造')),true);
    check('unsupported scripts and media fallback are explicit',instructions.every(t=>t.includes('HTML/JS 文件只按文本/代码')&&t.includes('静态分步图解')&&t.includes('字幕')&&t.includes('不授予写权限')),true);
    check('wrong facade receives no Home submission context',await page.evaluate(()=>chatOptions.submissionContext({resident_id:'resident_abcdefabcdef'})),null);
    check('sending visual request itself does not create any file',await page.evaluate(()=>instructionCalls.some(c=>c.name==='UpdateWorkspaceFile')),false);
    // Existing Preview semantics substantiate the capability guidance. Raw HTML
    // is escaped in both .md and .html files; the host adds no script renderer.
    check('Preview renders Markdown and HTML without executing scripts',await page.evaluate(async()=>{
      const testRoot=document.createElement('div');document.body.append(testRoot);
      const markup='<script>window.previewScriptExecuted=true</script><img src=x onerror="window.previewScriptExecuted=true">';
      const results=[];
      for(const extension of ['md','html']) {
        const handle=PortalPreview.render({root:testRoot,document:{path:'explanation.'+extension,content:markup}});
        await handle.promise;
        results.push(testRoot.querySelector('script,img,iframe')===null&&!window.previewScriptExecuted&&testRoot.textContent.includes('<script>'));
        PortalPreview.dispose(testRoot);
      }
      testRoot.remove();return results;
    }),[true,true]);
    await page.evaluate(()=>host.destroy());check('no browser errors',evidence.errors,[]);
    evidence.status='passed';
  }catch(error){evidence.status='failed';evidence.error=error.stack;if(page){evidence.page=await page.evaluate(()=>({text:document.body.innerText,previews:window.previewCalls,entries:document.querySelectorAll('.homeDialogueEntry').length}));await page.screenshot({path:path.join(out,'failure.png')});}throw error;}
  finally{fs.writeFileSync(path.join(out,'result.json'),JSON.stringify(evidence,null,2));await browser.close();await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error);process.exitCode=1;});
