'use strict';
// Production ResidentChatPanel, Markdown, Home host, Preview/DocumentSession and
// report adapter. Synthetic conversation and resource transport; no User session
// or provider, and no formal TAU browser is used.
const fs=require('node:fs'), path=require('node:path'), http=require('node:http'), assert=require('node:assert/strict');
const {createHash}=require('node:crypto');
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
  'portal_document_statistics.js','portal_preview_contract.js','portal_preview_runtime.js','portal_pdf_preview.js','portal_preview_renderers.js',
  ...groups['portal-workspace-surface'].map(s=>s.replace('/static/','')),'portal_agent_preview_source.js',
  'project_common.js','project_map_layout.js','project_map_drawer.js','project_map.js','home_map_view.js',
  'lineage_workbench_preview_chat_links.js','home_report.js','home_dialogue_state.js','portal_file_preview.js','home_dialogue.js'])];
const css=[...fs.readFileSync(path.join(root,'index.html'),'utf8').matchAll(/href="(\/static\/[^"?]+\.css)"/g)].map(m=>m[1]);
const out=process.env.HOME_DIALOGUE_EVIDENCE||'/tmp/home-dialogue-actions';fs.mkdirSync(out,{recursive:true});
const evidence={boundary:'real components; synthetic conversation/resource transport; no borrowed User or TAU session',checks:[],errors:[],requests:[]};
const explanationAddress='workspace_0123456789ab/file/shared-display/manifest.json';
const interactionAddress='workspace_0123456789ab/file/shared-display/body.html';
const interaction='<h2>共享图文解释</h2><button id="compare">比较两条路径</button><output id="answer"></output><script>document.getElementById("compare").onclick=()=>document.getElementById("answer").textContent="观察到不同结果";</script>';
const digest=value=>createHash('sha256').update(value).digest('hex');
const explanation=JSON.stringify({schema:'portal-explanation/v1',title:'共享解释页',revision:1,quality:'medium',interaction:{address:interactionAddress,sha256:digest(interaction)}});
const explanationReport={schema:'tfi.home-report.v1',content_kind:'explanation',report_id:'report_abcdefabcdef',revision:1,
  target:{kind:'report',id:'report_abcdefabcdef',revision:1},
  reference:{view:'report',title:'共享解释页',target:{kind:'report',id:'report_abcdefabcdef',revision:1}},
  explanation_target:{address:explanationAddress,sha256:digest(explanation)}};
let registeredExplanation=false;
const quotaOnly=process.argv.includes('--send-quota-only');
const contextOnly=quotaOnly||process.argv.includes('--submission-context-only');
evidence.scope=contextOnly?'submission context and Preview script boundary':'message actions, submission context and Preview script boundary';
function check(name,actual,expected){evidence.checks.push({name,actual,expected});assert.deepEqual(actual,expected,name);}
const server=http.createServer((req,res)=>{
  const url=new URL(req.url,'http://fixture');
  if(url.pathname==='/') {res.setHeader('Content-Type','text/html');return res.end('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'+css.map(s=>`<link rel="stylesheet" href="${s}">`).join('')+'<style>body{margin:0}#root{height:calc(100dvh - 16px);margin:8px}</style>'+modules.map(s=>`<script src="/static/${s}"></script>`).join('')+'<main id="root" data-portal-workbench-panel="dialogue-display"></main>');}
  if(url.pathname==='/static/portal_resource_routes.js'){res.setHeader('Content-Type','text/javascript');return res.end('window.PortalResourceRoutes={schema:"tfi.resource-portal-routes.v1",routes:[],text_resource_types:[],identity_parameters:[]};');}
  if(url.pathname==='/api/home-report/explanations') {
    evidence.requests.push(req.method+' '+url.pathname);
    res.setHeader('Content-Type','application/json');
    if(req.method==='POST') {let body='';req.on('data',data=>body+=data);req.on('end',()=>{
      assert.deepEqual(JSON.parse(body),explanationReport.explanation_target);
      registeredExplanation=true;res.end(JSON.stringify(explanationReport));});return;}
    if(url.searchParams.get('sha256')!==digest(explanation)) {res.statusCode=409;return res.end(JSON.stringify({error:'文件内容已改变'}));}
    if(!registeredExplanation) {res.statusCode=404;return res.end('{}');}
    return res.end(JSON.stringify(explanationReport));
  }
  if(url.pathname==='/api/home-report' && url.searchParams.get('id')===explanationReport.report_id) {
    res.setHeader('Content-Type','application/json');return res.end(JSON.stringify(explanationReport));
  }
  if(['/api/home-report','/api/home-report/save'].includes(url.pathname)) {evidence.requests.push(url.pathname+url.search);res.setHeader('Content-Type','application/json');return res.end(JSON.stringify({report_id:'report_0123456789ab',revision:1,latest_revision:1,reference:{view:'report',target:{kind:'report',id:'report_0123456789ab',revision:1}},title:'示例项目的真实组件汇报',target:{kind:'project',id:'demo'},version:'v1',checked_at:'2026-11-04T00:00:00Z',warnings:[],sources:[],counts:{},rows:[{id:'work',title:'已核对来源',body:'组件读取了指定项目',sources:[]}]}));}
  if(url.pathname==='/api/resources/content') {
    const address=url.searchParams.get('address');
    res.setHeader('Content-Type',address===explanationAddress?'application/json':'text/html');
    return res.end(address===explanationAddress?explanation:interaction);
  }
  if(url.pathname==='/api/home-explanation/media') {res.setHeader('Content-Type','application/json');return res.end('{"media":null}');}
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
      PortalI18n.apply('original');
      window.PortalIdentity={peek:()=>({username:'fixture'}),username:()=> 'fixture'};
      window.PortalAPI={get:async()=>({config:{},restart:null}),post:async()=>({})};
      window.previewCalls=[];window.deny=false;
      window.instructionCalls=[];
      window.PortalInstructionClient={execute:async(name,args)=>{
        instructionCalls.push({name,args});
        if(name==='GetResidentSettings') {
          if(window.reportingDenied)throw Object.assign(new Error('no_permit'),{status:403});
          if(window.reportingMissing)return {settings:{},settings_revision:'legacy-fixture'};
          return {settings:{reporting:window.reportingPolicy||{initiative:'off',quality:'medium'}},settings_revision:'policy-fixture'};
        }
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
      const create=PortalChatWorkspace.panelPackage.create; window.nativeCreate=create;
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
    check('valid directives are compact actions',await page.locator('.chatDisplayAction').count(),2);
    check('normal reply contains no internal JSON',await page.locator('.homeDialogueChat').innerText().then(t=>t.includes('portal-home-display/v1')),false);
    check('historical replies never auto-open',await page.locator('.homeDialogueEntry').count(),0);
    await action('预览幻灯片').click();await page.locator('.portalFileDocument:visible').waitFor();
    check('preview reads fresh source',await page.evaluate(()=>previewCalls[0].allowCached),false);
    await page.screenshot({path:path.join(out,'01-actions-preview-1440.png')});
    await page.getByRole('button',{name:'编辑当前文件的源码',exact:true}).click();await page.getByLabel('文件草稿').fill('保留未保存的手动草稿');
    await action('查看汇报').click();await page.locator('.home-report h2').waitFor();
    check('report action uses real adapter',await page.locator('.home-report h2').innerText(),'示例项目的真实组件汇报');
    await homeAction(page,'返回上一展示');
    check('manual history action retains document draft',await page.getByLabel('文件草稿').inputValue(),'保留未保存的手动草稿');
    // Unknown nonce click is explicit manual navigation, never an automatic ack.
    await page.evaluate(async()=>{window.submitted={resident_id:'resident_0123456789ab'};window.current=JSON.parse((await chatOptions.submissionContext(submitted)).context_text.split('示例结构：')[1].split('\n')[0]);chatOptions.onAcknowledgement({}, {target:submitted});});
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
    check('incomplete and unavailable blocks have no actions',await page.locator('.chatDisplayAction').count(),0);
    check('unknown and invalid explain unavailable',await page.locator('.chatDisplayNotice').filter({hasText:'暂不可用'}).count(),2);
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
    await action('预览幻灯片').press('Enter');await page.locator('.portalFileDocument:visible').waitFor();
    check('keyboard transfers focus to displayed target',await page.locator('.homeDialoguePreview .portalDocumentTab.is-active button').first().evaluate(e=>e===document.activeElement),true);
    await page.screenshot({path:path.join(out,'04-opened-390-large-font.png')});
    await page.locator('.homeDialogueTabs').getByRole('button',{name:'对话',exact:true}).click();
    await page.evaluate(()=>window.deny=true);await action('预览幻灯片').click();
    await page.getByText('暂时无法访问此文件',{exact:true}).first().waitFor();
    check('explicit click still observes source denial',await page.locator('.homeDialogueEntry:not([hidden]) .portalFileDocument:visible').count(),0);
    await page.locator('.homeDialogueTabs').getByRole('button',{name:'对话',exact:true}).click();
    await page.evaluate(()=>{snap.messages=[message('msg_map',displayFence(command('map-history',{view:'map',target:{kind:'project',id:'demo'}})))];render();});
    await action('打开研究地图').click();
    await page.locator('.homeMapSurface:not([hidden]) svg.mapEdges').waitFor();
    check('map action loads through the real native adapter',await page.evaluate(()=>instructionCalls.some(c=>c.name==='GetResearchMap'&&c.args.project_id==='demo')),true);
    await page.screenshot({path:path.join(out,'05-map-action-390.png')});
    // The general Markdown renderer still treats protocol examples as code.
    check('global Markdown is unchanged for ordinary callers',await page.evaluate(()=>MD.render(displayFence(command())).includes('<pre>')),true);
    }
    // Exercise the native Chat send -> Protocol prepare -> service boundary,
    // not a direct call to submissionContext. Only transport is substituted;
    // no model or private conversation is involved.
    if(process.argv.includes('--shared-display-only')) {
      await page.setViewportSize({width:1440,height:1000});
      await page.evaluate(({address,sha})=>{
        window.deny=false;
        PortalAPI.raw=(url,options)=>fetch(url,options);
        snap.messages=[message('msg_aaaaaaaaaaaaaaaa',displayFence(command('fixed',{view:'explanation',target:{address,sha256:sha}})))];render();
      },{address:explanationAddress,sha:digest(explanation)});
      await page.getByRole('button',{name:'打开解释页',exact:true}).click();
      await page.locator('.homeExplanationHeader h1').waitFor();
      check('Home uses Preview presentation for explanation',await page.locator('.homeExplanationHeader h1').innerText(),'共享解释页');
      check('Home uses the shared Preview resource identity',await page.locator('.homeDialoguePreview .portalPreviewDocumentPath .portalPreviewResourceId').innerText(),'report_abcdefabcdef');
      check('Home identity preserves the fixed revision',await page.locator('.homeDialoguePreview .portalPreviewDocumentPath .portalPreviewResourcePath').innerText(),'/revision/1');
      check('Home mounts the complete shared Preview package',await page.locator('.homeDialoguePreview').evaluate(el=>!!el.__portalWorkbenchPreviewPanel),true);
      check('Home has no parallel title toolbar',await page.locator('.homeDialogueDisplayBar,.homeDialogueMore,.homeDialogueIdentity').count(),0);
      check('Home exposes native document tabs',await page.locator('.homeDialoguePreview [role="tablist"] [role="tab"]').count()>0,true);
      check('native Visual mode is selected',await page.getByRole('button',{name:'以只读视觉模式查看当前文件',exact:true}).getAttribute('aria-pressed'),'true');
      const sharedChrome = await page.locator('.homeDialoguePreview .portalDocumentToolbar').evaluate(el=>[...el.querySelectorAll('button')].map(x=>x.getAttribute('aria-label')));
      await page.screenshot({path:path.join(out,'home-complete-preview.png')});
      await page.evaluate(()=>{
        const el=document.createElement('div');el.id='chrome-comparison';document.body.append(el);
        window.chromeComparison=PortalWorkspaceSurface.panelPackages.preview.create({root:el,explorer:false});
      });
      check('Home uses the identical native toolbar controls',sharedChrome,await page.locator('#chrome-comparison .portalDocumentToolbar').evaluate(el=>[...el.querySelectorAll('button')].map(x=>x.getAttribute('aria-label'))));
      await page.evaluate(()=>{chromeComparison.destroy();document.querySelector('#chrome-comparison').remove();});
      await page.evaluate(()=>{
        host.destroy();
        document.querySelector('#root').innerHTML='<div id="native-chat"></div><div id="native-preview"></div>';
        document.querySelector('#root').style.cssText='display:grid;grid-template-columns:1fr 1fr;height:950px';
        window.nativePanel=nativeCreate({root:document.querySelector('#native-chat'),target:{resident_id:'resident_0123456789ab'},autoLoad:false});
        nativePanel.protocol={snapshot:()=>snap,destroy(){}};
        nativePanel.residentProjection={resident:{resident_id:'resident_0123456789ab'},chat_workspace:{schema:'portal.chat_workspace.v3',target:{kind:'resident',id:'resident_0123456789ab'},header:{title:'Society Resident'},messages:[],composer:{can_send:true}}};
        const dependencies=Object.fromEntries(PortalLineageWorkbenchPreviewChatLinks.dependencies.map(key=>[key,()=>null]));
        Object.assign(dependencies,{currentLineageWorkbench:()=>({getPanel:()=>({registerPreviewHost:PortalPreview.registerHost})}),
          ensurePreviewVisible:()=>{window.previewVisible=true;},
          openAgentPreviewDocument:async request=>{
            window.nativeRequest=request;
            const document=await request.load();
            window.nativePreview=PortalPreview.render({root:window.document.querySelector('#native-preview'),document});
            await nativePreview.promise;
          }});
        PortalLineageWorkbenchPreviewChatLinks.createPreviewChatLinks(dependencies).bindWorkspacePreviewHost();
        nativePanel.applySnapshot(snap);
      });
      const native=page.locator('#native-chat');
      await native.getByRole('button',{name:'打开解释页',exact:true}).waitFor();
      check('Society shared Chat hides internal protocol',await native.innerText().then(t=>t.includes('portal-home-display/v1')),false);
      check('shared action has visible button affordance',await native.getByRole('button',{name:'打开解释页',exact:true}).evaluate(el=>{const s=getComputedStyle(el);return s.borderStyle==='solid'&&parseFloat(s.borderWidth)>=1;}),true);
      check('native historical message does not auto-open',await page.evaluate(()=>!!window.nativePreview),false);
      await native.getByRole('button',{name:'打开解释页',exact:true}).press('Enter');
      await page.locator('#native-preview .homeExplanationHeader h1').waitFor();
      check('Society native host uses shared Preview renderer',await page.evaluate(()=>[previewVisible,nativePreview.renderer]),[true,'display']);
      const frame=page.frameLocator('#native-preview iframe').frameLocator('iframe');
      await frame.getByRole('button',{name:'比较两条路径'}).click();
      check('opened page remains interactive',await frame.locator('#answer').innerText(),'观察到不同结果');
      await page.screenshot({path:path.join(out,'society-shared-display.png')});
      check('Society opens the actual report resource identity',await page.evaluate(()=>nativeRequest.path),'report_abcdefabcdef/revision/1');
      check('Home and Society reuse one report registration',evidence.requests.filter(v=>v==='POST /api/home-report/explanations').length,1);
      const wrong=await page.evaluate(async()=>{try {
        await PortalPreview.displayDocument({displayIntent:{view:'explanation',target:{address:'workspace_0123456789ab/file/shared-display/manifest.json',sha256:'0'.repeat(64)}}});
      } catch(error){return error.message;}});
      check('digest mismatch cannot resolve a report identity',wrong,'文件内容已改变');
      await page.evaluate(async()=>{
        const el=document.createElement('div'); el.id='identity-preview'; document.body.append(el);
        window.identityPanel=PortalWorkspaceSurface.panelPackages.preview.create({root:el});
        await identityPanel.setDocument({source:'resource',path:'workspace_0123456789ab/file/shared-display/manifest.json',title:'Resource',contentKind:'code',content:'{}'});
      });
      check('shared Preview separates actual resource ID from file path',await page.locator('#identity-preview .portalPreviewResourceId').innerText(),'workspace_0123456789ab');
      check('editable Preview address retains the entire resource file locator',await page.locator('#identity-preview [data-portal-preview-path]').getAttribute('data-portal-preview-path'),'workspace_0123456789ab/file/shared-display/manifest.json');
      await page.locator('#identity-preview [data-portal-preview-path]').click();
      check('resource address can still be copied and edited',await page.locator('#identity-preview .portalPreviewPathInput').inputValue(),'workspace_0123456789ab/file/shared-display/manifest.json');
      await page.keyboard.press('Escape');
      await page.evaluate(async()=>identityPanel.setDocument(await nativeRequest.load()));
      check('report toolbar shows its own ID',await page.locator('#identity-preview .portalPreviewResourceId').innerText(),'report_abcdefabcdef');
      check('shared presentation has no page errors',evidence.errors,[]);
      evidence.status='passed';
      console.log('PASS shared Resident actions, Society Preview host, Home presentation, fixed SHA and interactive content');
      return;
    }
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
      window.acceptedMessages=[]; window.sentMessages=[]; window.quotaMessages=[];
      const onAcknowledgement=panel.options.onAcknowledgement, onSent=panel.options.onSent;
      panel.options.onAcknowledgement=(result,context,...rest)=>{acceptedMessages.push(context.messageIdentity);return onAcknowledgement?.(result,context,...rest);};
      panel.options.onSent=(result,instance,context)=>{sentMessages.push(context.messageIdentity);return onSent?.(result,instance,context);};
      const quotaSubmitted=panel.workspace.controller.quotaSubmitted;
      panel.workspace.controller.quotaSubmitted=messageId=>{quotaMessages.push(messageId);return quotaSubmitted(messageId);};
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
    const messageIds=evidence.submissions.map(s=>s.message.message_id);
    check('successful sends reach runtime quota controls once',await page.evaluate(()=>quotaMessages),messageIds);
    check('quota notification does not interrupt acknowledgement callbacks',await page.evaluate(()=>acceptedMessages),messageIds);
    check('quota notification does not interrupt sent callbacks',await page.evaluate(()=>sentMessages),messageIds);
    if(quotaOnly) {
      check('native send has no page errors',evidence.errors,[]);
      console.log('PASS native send through ResidentChatWorkspace: quota notification, acknowledgement and sent callbacks each run once; no page error or resend.');
      return;
    }
    check('native send preserves user wording and presentation constraints',evidence.submissions.map(s=>s.message.content_parts.filter(p=>p.type==='text').map(p=>p.text).join('')),presentationRequests);
    const contexts=evidence.submissions.map(s=>s.message.content_parts.filter(p=>p.type==='context_reference'));
    check('native Protocol prepare attaches exactly one Home context',contexts.map(c=>c.length),presentationRequests.map(()=>1));
    const instructions=contexts.map(c=>c[0].context.context_text);
    const nonces=instructions.map(t=>JSON.parse(t.split('示例结构：')[1].split('\n')[0]).request_id);
    check('each native send has a fresh request nonce',new Set(nonces).size,presentationRequests.length);
    check('same verified policy applies across user wording',new Set(instructions.map(t=>t.split('示例结构：')[0])).size,1);
    check('Home carries verified reporting settings without a second policy prompt',contexts.every(c=>c[0].context.reporting.initiative==='off'),true);
    check('Home delegates rules to standard Resident injection',instructions.every(t=>t.includes('标准 Resident Prompt 注入')&&!t.includes('主动性 off：')&&!t.includes('仅high质量必须')),true);
    check('Home points to the formal authoring contract',instructions.every(t=>t.includes('/static/home_explanation_authoring.md')),true);
    check('Home preserves navigation and fixed resource references',instructions.every(t=>t.includes('固定汇报引用使用真实 report_ID 和 revision')&&t.includes('自动结果不会覆盖固定、草稿或用户手选展示')),true);
    evidence.policySubmissions=await page.evaluate(async()=>{
      const results=[];
      for(const initiative of ['off','low','medium','high'])for(const quality of ['low','medium','high']) {
        window.reportingPolicy={initiative,quality};
        await panel.send({text:'解释这个问题',plainText:'解释这个问题'},{clear:false,focus:false});
        results.push(sentBodies.at(-1).message.content_parts.find(p=>p.type==='context_reference').context);
      }
      window.reportingDenied=true;
      await panel.send({text:'普通问答',plainText:'普通问答'},{clear:false,focus:false});
      results.push(sentBodies.at(-1).message.content_parts.find(p=>p.type==='context_reference').context);
      window.reportingDenied=false;return results;
    });
    check('fresh effective policy reaches every native send',evidence.policySubmissions.slice(0,12).map(c=>c.reporting),['off','low','medium','high'].flatMap(initiative=>['low','medium','high'].map(quality=>({initiative,quality}))));
    check('Home does not duplicate high-initiative prompt',evidence.policySubmissions[11].context_text.includes('高主动性不等于每轮必生成'),false);
    check('permission failure preserves chat but forbids automatic generation',evidence.policySubmissions.at(-1).reporting_read==='unavailable'&&evidence.policySubmissions.at(-1).reporting===null&&evidence.policySubmissions.at(-1).reporting_error==='no_permit',true);
    check('explicit invalid policy is unavailable rather than configured default',await page.evaluate(async()=>{
      window.reportingPolicy={initiative:'always',quality:'high'};
      const context=await chatOptions.submissionContext({resident_id:'resident_0123456789ab'});
      window.reportingPolicy={initiative:'off',quality:'medium'};
      return context.reporting_read;
    }),'unavailable');
    check('missing effective policy is unavailable rather than a fabricated default',await page.evaluate(async()=>{
      window.reportingMissing=true;
      const context=await chatOptions.submissionContext({resident_id:'resident_0123456789ab'});
      window.reportingMissing=false;return [context.reporting_read,context.settings_revision,context.reporting];
    }),['unavailable','legacy-fixture',null]);
    check('quality changes retain the same message generation identity',await page.evaluate(async()=>{
      const origin={residentId:'resident_0123456789ab',messageId:'msg_0123456789abcdef',fenceIndex:0};
      const intent={view:'report',target:{kind:'project',id:'demo'}};
      const keys=await Promise.all([undefined,'low','medium','high'].map(quality=>PortalHomeDialogueState.messageOriginKey(origin,{...intent,quality})));
      return new Set(keys).size;
    }),1);
    check('out-of-order policy reads retain submission navigation order',await page.evaluate(async()=>{
      const execute=PortalInstructionClient.execute, issued=[], calls=[], originalOptions=chatOptions, originalPanel=panel;
      const create=PortalHomeDialogueState.create;
      PortalHomeDialogueState={...PortalHomeDialogueState,create(...args){const state=create(...args), issue=state.issue;state.issue=(...values)=>{const id=issue(...values);issued.push(id);return id;};return state;}};
      const testRoot=document.createElement('div');document.body.append(testRoot);
      const other=PortalHomeDialogue.connect({root:testRoot,context:{currentUserReady:Promise.resolve({username:'fixture'})}});
      await new Promise(resolve=>setTimeout(resolve,50));
      PortalInstructionClient.execute=async(name,args)=>name==='GetResidentSettings'?await new Promise(resolve=>calls.push(resolve)):execute(name,args);
      const options=chatOptions;
      const first=options.submissionContext({resident_id:'resident_0123456789ab'});
      const second=options.submissionContext({resident_id:'resident_0123456789ab'});
      const before=issued.slice();
      calls[1]({settings:{reporting:{initiative:'high',quality:'high'}},settings_revision:'new'});
      const secondContext=await second;
      calls[0]({settings:{reporting:{initiative:'off',quality:'medium'}},settings_revision:'old'});
      const firstContext=await first;
      const nonce=context=>JSON.parse(context.context_text.split('示例结构：')[1].split('\n')[0]).request_id;
      const result=before.length===2&&nonce(firstContext)===before[0]&&nonce(secondContext)===before[1];
      PortalInstructionClient.execute=execute;PortalHomeDialogueState={...PortalHomeDialogueState,create};
      other.destroy();testRoot.remove();window.chatOptions=originalOptions;window.panel=originalPanel;return result;
    }),true);
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
