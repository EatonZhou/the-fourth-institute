'use strict';
// Native Chat, Home and report adapter with a persistent HTTP contract fixture.
// Each browser has independent storage. No User session or formal TAU is used.
const fs=require('node:fs'),path=require('node:path'),http=require('node:http'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const {chromium}=require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const {chatWorkspaceScripts}=require('./chat_workspace_modules.cjs');
const {portalWorkbenchScripts}=require('./portal_workbench_scripts.cjs');
const homeAction=require('./home_dialogue_controls.cjs');
const root=path.resolve(__dirname,'../../../management_portal');
const out=process.env.HOME_REPORT_BINDING_EVIDENCE||'/tmp/home-dialogue-report-bindings';fs.mkdirSync(out,{recursive:true});
const modules=[...new Set(['portal_state.js','portal_page_state.js','portal_text.js','portal_translations.js','portal_i18n.js',
  'portal_ui_primitives.js','portal_ui.js','portal_overlay.js','portal_resource_routes.js','portal_resource_chips.js',
  'portal_command_registry.js','portal_workbench_context.js',...portalWorkbenchScripts(),
  'portal_resident_service.js','resident_protocol.js','markdown.js',...chatWorkspaceScripts(),
  'portal_preview_contract.js','portal_preview_runtime.js','home_report.js','home_dialogue_state.js','home_dialogue.js'])];
const css=[...fs.readFileSync(path.join(root,'index.html'),'utf8').matchAll(/href="(\/static\/[^"?]+\.css)"/g)].map(m=>m[1]);
const records=new Map(),origins=new Map(),requests=[],checks=[],errors=[];
let sequence=0,live=1,denied=false,delay=0,videoBytes;
const reference=(id,revision)=>({view:'report',target:{kind:'report',id,revision},address:`${id}/revision/${revision}`,version:`source-${revision}`,checked_at:'2026-11-04T00:00:00Z'});
const snapshot=(target)=>({title:`固定汇报 · 来源 ${live}`,goal:'跨文档汇报契约夹具',target,version:`source-${live}`,checked_at:'2026-11-04T00:00:00Z',rows:[],counts:{},sources:[],warnings:[],
  reasoning:{available:true,mode:'dependency',nodes:[{id:'seed',title:'来源',x:.5,y:.5}],edges:[],seed:'seed',before:{reached:[]},after:{reached:['seed'],trace:[]},unconnected:[],rule:'fixture',limit:'fixture'}});
function response(id,revision){const record=records.get(id),data=record?.get(revision);if(!data)return null;return {...data,report_id:id,revision,latest_revision:Math.max(...record.keys()),reference:reference(id,revision)};}
const server=http.createServer(async(req,res)=>{
  const url=new URL(req.url,'http://fixture'),json=(value,status=200)=>{res.statusCode=status;res.setHeader('Content-Type','application/json');res.end(JSON.stringify(value));};
  if(url.pathname==='/') {res.setHeader('Content-Type','text/html');return res.end('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'+css.map(s=>`<link rel="stylesheet" href="${s}">`).join('')+'<style>body{margin:0}#root{height:calc(100dvh - 16px);margin:8px}</style>'+modules.map(s=>`<script src="/static/${s}"></script>`).join('')+'<main id="root" data-portal-workbench-panel="dialogue-display"></main>');}
  if(url.pathname==='/away')return res.end('<!doctype html><title>Media fixture</title>');
  if(url.pathname==='/static/portal_resource_routes.js'){res.setHeader('Content-Type','text/javascript');return res.end('window.PortalResourceRoutes={schema:"tfi.resource-portal-routes.v1",routes:[],text_resource_types:[],identity_parameters:[]};');}
  if(url.pathname.startsWith('/api/home-report')) {
    let body={};if(req.method==='POST'){let raw='';for await(const chunk of req)raw+=chunk;body=JSON.parse(raw||'{}');}
    requests.push({method:req.method,path:url.pathname,query:Object.fromEntries(url.searchParams),body});
    if(denied)return json({error:'来源权限已撤回'},403);
    const id=url.searchParams.get('id'),revision=Number(url.searchParams.get('revision')||1);
    if(url.pathname.endsWith('/video.mp4')){res.setHeader('Content-Type','video/webm');return res.end(videoBytes);}
    if(url.pathname.endsWith('/captions.vtt')){res.setHeader('Content-Type','text/vtt');return res.end('WEBVTT\n');}
    if(url.pathname.endsWith('/poster.png')){res.statusCode=204;return res.end();}
    if(url.pathname==='/api/home-report/save'){
      let binding=origins.get(body.origin_key);
      if(!binding){assert.ok(body.origin_key,'materialization must have a stable origin');const id='report_'+(++sequence).toString(16).padStart(12,'0');binding={id,revision:1};origins.set(body.origin_key,binding);records.set(id,new Map([[1,snapshot(body.target)]]));}
      if(delay)await new Promise(r=>setTimeout(r,delay));
      return json(response(binding.id,binding.revision));
    }
    if(url.pathname==='/api/home-report/refresh'){
      const record=records.get(body.report_id),revision=Math.max(...record.keys());
      if(body.expected_revision!==revision)return json({error:'版本冲突'},409);
      record.set(revision+1,snapshot(record.get(revision).target));return json(response(body.report_id,revision+1));
    }
    if(url.pathname==='/api/home-report/media'&&req.method==='POST'){
      const record=records.get(body.report_id)?.get(body.revision);assert.ok(record,'media binds a fixed revision');
      record.media={status:'ready',job_id:`${body.report_id}-${body.revision}`,base_url:`/api/home-report/${body.report_id}/revision/${body.revision}/file`,chapters:[],transcript:'固定文字稿',duration:1,generation_seconds:0,version:record.version};return json(record.media);
    }
    if(url.pathname==='/api/home-report/validate')return json({readable:true});
    if(url.searchParams.has('origin_key')){const binding=origins.get(url.searchParams.get('origin_key'));return binding?json(response(binding.id,binding.revision)):json({error:'尚未生成汇报'},404);}
    if(url.searchParams.get('kind')==='report'){const data=response(id,revision);return data?json(data):json({error:'汇报不存在'},404);}
    return json(snapshot({kind:url.searchParams.get('kind'),id}));
  }
  const file=path.resolve(root,'.'+url.pathname);if(file.startsWith(root+'/static/')&&fs.existsSync(file)&&fs.statSync(file).isFile()){res.setHeader('Content-Type',file.endsWith('.css')?'text/css':'text/javascript');return res.end(fs.readFileSync(file));}
  res.statusCode=404;res.end();
});
function check(name,actual,expected){checks.push({name,actual,expected});assert.deepEqual(actual,expected,name);}
(async()=>{
  await new Promise(r=>server.listen(0,'127.0.0.1',r));const base=`http://127.0.0.1:${server.address().port}`,browser=await chromium.launch({headless:true});let page;
  const posts=()=>requests.filter(r=>r.method==='POST'&&!r.path.endsWith('/validate')).length;
  async function mount(page){
    await page.goto(base);await page.evaluate(()=>{
      window.PortalIdentity={peek:()=>({username:'report-binding-fixture'}),username:()=> 'report-binding-fixture'};
      window.PortalAPI={get:async()=>({config:{}}),post:async()=>({})};
      window.PortalInstructionClient={execute:async()=>({entries:[]})};
      window.PortalNav={personalAssistantTarget:async()=>({resident_id:'resident_0123456789ab',title:'个人门面'})};
      const create=PortalChatWorkspace.panelPackage.create;
      window.PortalChatWorkspace={...PortalChatWorkspace,panelPackage:{...PortalChatWorkspace.panelPackage,create(options){window.chatOptions=options;return window.panel=create({...options,autoLoad:false});}}};
      window.host=PortalHomeDialogue.connect({root:document.querySelector('#root'),context:{currentUserReady:Promise.resolve({username:'report-binding-fixture'})}});
    });await page.waitForFunction(()=>!!window.panel);
    await page.evaluate(()=>{
      window.snap={messages:[],controls:{can_send:true},queue:{items:[]}};
      panel.protocol={snapshot:()=>snap,destroy(){}};
      panel.residentProjection={resident:{resident_id:'resident_0123456789ab'},chat_workspace:{schema:'portal.chat_workspace.v3',target:{kind:'resident',id:'resident_0123456789ab'},header:{title:'个人门面'},messages:[],composer:{can_send:true}}};
      window.command=(target={kind:'project',id:'demo'},request_id='historical')=>({protocol:'portal-home-display/v1',request_id,version:1,action:'open',view:'report',target});
      window.displayFence=value=>'```portal-home-display\n'+JSON.stringify(value)+'\n```';
      window.message=(id,text)=>({message_id:id,role:'agent',created_at:'2026-11-04T00:00:00Z',content_parts:[{type:'text',text}]});
      snap.messages=[message('msg_0123456789abcdef',displayFence(command())+'\n\n'+displayFence(command()))];panel.applySnapshot(snap);
    });
  }
  const action=p=>p.locator('.homeDialogueMessageAction');
  const fixed=()=>page.locator('.homeDialogueEntry:not([hidden]) .home-report h2');
  try{
    page=await browser.newPage({viewport:{width:1440,height:1000}});page.setDefaultTimeout(8000);page.on('pageerror',e=>errors.push(e.message));
    await page.goto(base+'/away');videoBytes=Buffer.from(await page.evaluate(async()=>{
      const c=document.createElement('canvas');c.width=160;c.height=90;const stream=c.captureStream(10),rec=new MediaRecorder(stream,{mimeType:'video/webm'}),chunks=[];
      rec.ondataavailable=e=>chunks.push(e.data);const stopped=new Promise(r=>rec.onstop=r);rec.start();const timer=setInterval(()=>{const ctx=c.getContext('2d');ctx.fillStyle='#246';ctx.fillRect(0,0,160,90);},50);
      await new Promise(r=>setTimeout(r,1000));rec.stop();await stopped;clearInterval(timer);stream.getTracks().forEach(t=>t.stop());return Array.from(new Uint8Array(await new Blob(chunks).arrayBuffer()));
    }));
    await mount(page);check('historical messages do not write on render',posts(),0);
    check('canonical key ignores nonce and JSON field ordering',await page.evaluate(async()=>{
      const provenance={residentId:'resident_0123456789ab',messageId:'msg_0123456789abcdef',fenceIndex:0};
      const a=await PortalHomeDialogueState.messageOriginKey(provenance,command());
      const b=await PortalHomeDialogueState.messageOriginKey(provenance,{target:{id:'demo',kind:'project'},view:'report',request_id:'different'});
      const c=await PortalHomeDialogueState.messageOriginKey({...provenance,fenceIndex:1},command());return a===b&&a!==c;
    }),true);
    await action(page).first().click();await fixed().waitFor();
    check('first click materializes once',records.size,1);
    const key=requests.find(r=>r.path.endsWith('/save')).body.origin_key;
    check('message key is stable digest',/^home-message\/v1:[a-f0-9]{64}$/.test(key),true);
    const first=origins.get(key),title=await fixed().innerText();
    await page.getByRole('button',{name:'生成讲解视频',exact:true}).click();await page.locator('video:visible').waitFor();await page.waitForFunction(()=>document.querySelector('video')?.readyState>=2);
    await page.locator('video:visible').focus();await page.locator('video:visible').press('Space');await page.waitForFunction(()=>!document.querySelector('video').paused);await page.locator('video:visible').press('Space');
    const videoPath=await page.locator('video:visible').getAttribute('src');await page.screenshot({path:path.join(out,'01-fixed-report-video-1440.png')});
    live=2;
    const other=await browser.newContext({viewport:{width:390,height:844}});page=await other.newPage();page.setDefaultTimeout(8000);page.on('pageerror',e=>errors.push(e.message));await mount(page);
    await page.evaluate(()=>document.documentElement.style.fontSize='20px');const beforeOpen=posts();
    check('fresh browser has no navigation history',await page.locator('.homeDialogueEntry').count(),0);
    await action(page).first().focus();await action(page).first().press('Enter');await fixed().waitFor();await page.locator('video:visible').waitFor();
    check('same message in another browser keeps source snapshot',await fixed().innerText(),title);
    check('same message keeps original resource',records.size,1);
    check('same message keeps video path',await page.locator('video:visible').getAttribute('src'),videoPath);
    check('reopen never regenerates media',requests.slice().filter(r=>r.method==='POST'&&r.path.endsWith('/media')).length,1);
    check('stable save lookup ignores changed live version',posts(),beforeOpen+1);
    await page.screenshot({path:path.join(out,'02-reopen-390-font20.png')});
    await page.getByRole('button',{name:'刷新为新版本',exact:true}).click();await page.waitForFunction(()=>document.querySelectorAll('.homeDialogueEntry').length===2);
    await page.getByRole('heading',{name:'固定汇报 · 来源 2',exact:true}).waitFor();
    check('refresh preserves original origin revision',origins.get(key),first);
    await homeAction(page,'返回上一展示');await page.locator('video:visible').waitFor();check('previous entry still displays old version',await fixed().innerText(),title);
    const beforeReload=posts();await mount(page);await fixed().waitFor();await page.locator('video:visible').waitFor();
    check('new document restores exact version without writes',posts(),beforeReload);
    check('navigation storage has no body, credentials or automatic-admission nonce',await page.evaluate(()=>{
      const saved=PortalPageState.create('home-dialogue-display',{user:()=> 'report-binding-fixture'}).read();
      return saved.entries.every(e=>Object.keys(e).every(k=>['intent','queued','originKey'].includes(k)))&&!JSON.stringify(saved).includes('request_id');
    }),true);
    check('navigation descriptors contain fixed targets',await page.evaluate(()=>{
      const saved=PortalPageState.create('home-dialogue-display',{user:()=> 'report-binding-fixture'}).read();return saved.entries.map(e=>[e.intent.target.kind,e.intent.target.id,e.intent.target.revision]);
    }),[['report',first.id,1],['report',first.id,2]]);
    await page.locator('.homeDialogueTabs').getByRole('button',{name:'对话',exact:true}).click();await action(page).nth(1).click();await fixed().waitFor();
    check('second fence is an independent semantic request',records.size,2);
    const directBefore=posts();await page.evaluate(id=>{snap.messages=[message('msg_abcdefabcdef',displayFence(command({kind:'report',id,revision:1})))];panel.applySnapshot(snap);},first.id);
    await page.locator('.homeDialogueTabs').getByRole('button',{name:'对话',exact:true}).click();await action(page).first().click();await page.locator('video:visible').waitFor();
    check('explicit report message uses GET only',posts(),directBefore);
    check('direct fixed target ignores newer revision',await fixed().innerText(),title);
    // Await a committed save with an interrupted response: restore must look up
    // the saved request identity, never replay the creation POST.
    delay=700;await page.locator('.homeDialogueOpener > summary').click();await page.getByLabel('目标类型',{exact:true}).selectOption('project');await page.getByLabel('目标 ID 或文件资源地址').fill('manual');await page.locator('.homeDialogueChooser button').click();
    await page.waitForFunction(()=>document.querySelector('.homeDialogueCurrent').textContent==='manual');
    await new Promise(r=>setTimeout(r,100));const beforeInterrupted=posts();await mount(page);delay=0;await fixed().waitFor();
    check('interrupted request restores by read only',posts(),beforeInterrupted);
    check('manual identity persisted before POST',requests.some(r=>r.path.endsWith('/save')&&/^home-request\/v1:/.test(r.body.origin_key)),true);
    await page.evaluate(()=>{host.destroy();PortalPageState.create('home-dialogue-display',{user:()=> 'report-binding-fixture'}).write({schemaVersion:2,entries:[{
      intent:{view:'report',target:{kind:'project',id:'demo'}},messageOrigin:{residentId:'resident_0123456789ab',messageId:'msg_0123456789abcdef',fenceIndex:0}}],active:0,pane:'display'});});
    const beforeHashRestore=posts();await mount(page);await page.locator('video:visible').waitFor();
    check('navigation saved before digest completion restores the same origin without writes',posts(),beforeHashRestore);
    check('pre-digest recovery keeps original snapshot',await fixed().innerText(),title);
    // Hidden old source-only layouts have no creation authority.
    await page.evaluate(()=>{host.destroy();PortalPageState.create('home-dialogue-display',{user:()=> 'report-binding-fixture'}).write({schemaVersion:1,entries:[{intent:{view:'report',target:{kind:'project',id:'legacy'}}}],active:0,pane:'display'});});
    const beforeLegacy=posts();await mount(page);await new Promise(r=>setTimeout(r,150));check('legacy restore never writes',posts(),beforeLegacy);
    await page.getByRole('button',{name:'生成汇报',exact:true}).waitFor();
    const pendingKey=await page.evaluate(()=>PortalPageState.create('home-dialogue-display',{user:()=> 'report-binding-fixture'}).read().entries[0].originKey);
    await page.getByRole('button',{name:'生成汇报',exact:true}).click();await fixed().waitFor();
    check('uncommitted restore requires explicit generation with its saved identity',requests.filter(r=>r.path.endsWith('/save')).at(-1).body.origin_key,pendingKey);
    // A current acknowledged assistant result and its explicit historical
    // button must resolve the same semantic request, without sharing admission.
    await page.evaluate(()=>{
      window.sent={resident_id:'resident_0123456789ab'};
      window.currentCommand=JSON.parse(chatOptions.submissionContext(sent).context_text.split('示例结构：')[1].split('\n')[0]);
      chatOptions.onAcknowledgement({}, {target:sent});
      snap.messages=[message('msg_123456789abc',displayFence(command({kind:'project',id:'automatic'},currentCommand.request_id)))];panel.applySnapshot(snap);
    });await fixed().waitFor();
    const beforeExplicit=records.size,automaticKey=requests.filter(r=>r.path.endsWith('/save')).at(-1).body.origin_key;
    await page.locator('.homeDialogueTabs').getByRole('button',{name:'对话',exact:true}).click();await action(page).first().click();await fixed().waitFor();
    check('automatic result and historical click use the same origin',requests.filter(r=>r.path.endsWith('/save')).at(-1).body.origin_key,automaticKey);
    check('explicit reopen does not mint another report',records.size,beforeExplicit);
    await homeAction(page,'固定展示');const beforeLate=posts();
    await page.evaluate(()=>{
      const sent={resident_id:'resident_0123456789ab'},p=JSON.parse(chatOptions.submissionContext(sent).context_text.split('示例结构：')[1].split('\n')[0]);
      chatOptions.onAcknowledgement({}, {target:sent});
      snap.messages.push(message('msg_23456789abcd',displayFence(command({kind:'project',id:'late'},p.request_id))));panel.applySnapshot(snap);
    });await new Promise(r=>setTimeout(r,150));
    check('pinned late report never writes in the background',posts(),beforeLate);
    check('pinned late report keeps current target',await page.locator('.homeDialogueEntry:not([hidden]) .home-report header').innerText().then(t=>t.includes('automatic')),true);
    denied=true;await page.evaluate(id=>{snap.messages=[message('msg_abcdefabcdef',displayFence(command({kind:'report',id,revision:1})))];panel.applySnapshot(snap);},first.id);
    await page.locator('.homeDialogueTabs').getByRole('button',{name:'对话',exact:true}).click();await action(page).first().click();await page.getByText(/来源权限已撤回/).first().waitFor();
    check('denied fixed report exposes no cached video',await page.locator('.homeDialogueEntry:not([hidden]) video').count(),0);
    check('no script errors',errors,[]);
    fs.writeFileSync(path.join(out,'result.json'),JSON.stringify({status:'passed',boundary:'native components with persistent HTTP fixture, not real User/backend authorization',checks,requests,videoSha256:crypto.createHash('sha256').update(videoBytes).digest('hex')},null,2));
  }catch(error){if(page)await page.screenshot({path:path.join(out,'failure.png')});fs.writeFileSync(path.join(out,'result.json'),JSON.stringify({status:'failed',error:error.stack,checks,requests,errors},null,2));throw error;}
  finally{await browser.close();await new Promise(r=>server.close(r));}
})().catch(error=>{console.error(error);process.exitCode=1;});
