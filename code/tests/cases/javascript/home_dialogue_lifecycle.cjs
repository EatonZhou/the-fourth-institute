// Native Home/report/media lifecycle; isolated API fixtures, never a TAU browser.
const {chromium}=require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const homeAction=require('./home_dialogue_controls.cjs');
const fs=require('node:fs'),path=require('node:path'),http=require('node:http'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'../../../management_portal');
const groups=JSON.parse(fs.readFileSync(path.join(root,'script_groups.json')));
const html=fs.readFileSync(path.join(root,'index.html'),'utf8').replace(/<script data-portal-script-group="([^"]+)"><\/script>/g,(_,id)=>groups[id].map(src=>`<script src="${src}"></script>`).join(''));
const out=process.env.HOME_DIALOGUE_LIFECYCLE_EVIDENCE||'/tmp/home-dialogue-lifecycle';fs.mkdirSync(out,{recursive:true});
let videoBytes,username='lifecycle-fixture',denied=false;
const reads=[];
const reports=new Map(),origins=new Map();
const reportResponse=(id)=>{const data=reports.get(id);return {...data,report_id:id,revision:1,latest_revision:1,reference:{view:'report',target:{kind:'report',id,revision:1}}};};
const server=http.createServer(async(req,res)=>{
  const url=new URL(req.url,'http://localhost'),json=data=>{res.setHeader('Content-Type','application/json');res.end(JSON.stringify(data));};
  if(url.pathname==='/') {res.setHeader('Content-Type','text/html');return res.end(html.replace('<head>',`<head><script>window.__PORTAL_IDENTITY_BOOTSTRAP__=${JSON.stringify({username,principal:{kind:'user',id:username},access:{allows:{}}})}</script>`));}
  if(url.pathname==='/away') return res.end('<!doctype html><title>Away</title>Other document');
  if(url.pathname==='/static/portal_resource_routes.js'){res.setHeader('Content-Type','text/javascript');return res.end('window.PortalResourceRoutes={schema:"tfi.resource-portal-routes.v1",routes:[],text_resource_types:[],identity_parameters:[]};');}
  if(url.pathname==='/api/home-report/media/fixture/video.mp4') {res.setHeader('Content-Type','video/webm');res.setHeader('Content-Length',videoBytes.length);return res.end(videoBytes);}
  if(url.pathname.endsWith('/captions.vtt')) {res.setHeader('Content-Type','text/vtt');return res.end('WEBVTT\n\n00:00.000 --> 00:08.000\nLifecycle fixture\n');}
  if(url.pathname.endsWith('/poster.png')) {res.statusCode=204;return res.end();}
  if(url.pathname.startsWith('/api/home-report')) {
    let body={};if(req.method==='POST'){let raw='';for await(const chunk of req)raw+=chunk;body=JSON.parse(raw||'{}');}
    reads.push({path:url.pathname,id:url.searchParams.get('id'),username,denied});
    if(denied){res.statusCode=403;return json({error:'fixture permission revoked'});}
    if(url.pathname==='/api/home-report/validate') return json({readable:true});
    if(url.pathname==='/api/home-report/save'){
      let id=origins.get(body.origin_key);
      if(!id){id='report_'+(reports.size+1).toString(16).padStart(12,'0');origins.set(body.origin_key,id);reports.set(id,{title:body.target.id,target:body.target,goal:'Lifecycle transport fixture',version:'fixture',checked_at:'2026-11-04T00:00:00Z',rows:[],counts:{},sources:[],warnings:[],reasoning:{available:true,mode:'dependency',nodes:[{id:'seed',title:'来源',x:.5,y:.5}],edges:[],seed:'seed',before:{reached:[]},after:{reached:['seed'],trace:[]},unconnected:[],rule:'fixture',limit:'fixture'}});}
      return json(reportResponse(id));
    }
    if(url.pathname==='/api/home-report/media'){
      const report=reports.get(body.report_id);report.media||={status:'ready',job_id:'fixture',base_url:'/api/home-report/media/fixture',chapters:[{start:4,title:'定位'}],transcript:'Isolated fixture',duration:8,generation_seconds:0,version:'fixture'};return json(report.media);
    }
    if(url.searchParams.get('kind')==='report')return json(reportResponse(url.searchParams.get('id')));
    return json({title:url.searchParams.get('id'),goal:'Lifecycle transport fixture',version:'fixture',checked_at:'2026-11-04T00:00:00Z',rows:[],counts:{},sources:[],warnings:[],media_capability:{available:true}});
  }
  if(url.pathname.startsWith('/api/'))return json(url.pathname==='/api/me'?{username,principal:{kind:'user',id:username},access:{allows:{}}}:{items:[],accounts:[],usage:[],result:{},schema:'tfi.hatg-instruction-result.v2',status:'committed'});
  const file=path.resolve(root,'.'+url.pathname);if(!file.startsWith(root+'/static/')||!fs.existsSync(file)){res.statusCode=404;return res.end();}
  res.setHeader('Content-Type',file.endsWith('.css')?'text/css':'text/javascript');res.end(fs.readFileSync(file));
});
(async()=>{
  await new Promise(r=>server.listen(0,'127.0.0.1',r));const browser=await chromium.launch({headless:true,ignoreDefaultArgs:['--disable-back-forward-cache']});
  const evidence={scope:'native Home and report source, isolated transport; no shared TAU session',reads,events:[],errors:[]};
  try {
    const page=await browser.newPage({viewport:{width:1440,height:900},locale:'zh-CN'});page.setDefaultTimeout(7000);
    await page.goto(`http://127.0.0.1:${server.address().port}/away`);
    // Generate a local, seekable media fixture through the browser encoder.
    videoBytes=Buffer.from(await page.evaluate(async()=>{
      const canvas=document.createElement('canvas');canvas.width=320;canvas.height=180;
      const ctx=canvas.getContext('2d'),stream=canvas.captureStream(10),recorder=new MediaRecorder(stream,{mimeType:'video/webm'}),chunks=[];
      recorder.ondataavailable=e=>chunks.push(e.data);
      const done=new Promise(resolve=>recorder.onstop=resolve);recorder.start();
      const timer=setInterval(()=>{ctx.fillStyle='#223344';ctx.fillRect(0,0,320,180);ctx.fillStyle='white';ctx.fillText(String(Date.now()),10,90);},100);
      await new Promise(r=>setTimeout(r,8000));recorder.stop();await done;clearInterval(timer);stream.getTracks().forEach(t=>t.stop());
      return Array.from(new Uint8Array(await new Blob(chunks).arrayBuffer()));
    }));
    page.on('pageerror',e=>evidence.errors.push(e.message));
    await page.exposeFunction('recordLifecycle',record=>evidence.events.push(record));
    await page.addInitScript(()=>{
      for(const name of ['pagehide','pageshow','beforeunload'])window.addEventListener(name,e=>recordLifecycle({name,persisted:e.persisted,trusted:e.isTrusted,timeOrigin:performance.timeOrigin,at:Date.now()}));
    });
    await page.goto(`http://127.0.0.1:${server.address().port}/?home_component=dialogue-display`);
    await homeAction(page,'展开');
    async function open(id){
      await page.locator('.homeDialogueOpener > summary').click();await page.getByLabel('目标类型',{exact:true}).selectOption('project');
      await page.getByLabel('目标 ID 或文件资源地址').fill(id);await page.locator('.homeDialogueChooser button').click();
      await page.getByRole('heading',{name:id,exact:true}).waitFor();
    }
    const state=()=>page.evaluate(()=>({timeOrigin:performance.timeOrigin,expanded:!!document.querySelector('.homeDialogueExpanded'),pane:document.querySelector('.homeDialogue').dataset.pane,
      entries:document.querySelectorAll('.homeDialogueEntry').length,title:document.querySelector('.homeDialogueCurrent').textContent,
      pinned:document.querySelector('.homeDialogueToolbar button[aria-pressed]')?.getAttribute('aria-pressed'),videos:document.querySelectorAll('video').length}));
    await open('lifecycle-first');await open('lifecycle-second');await homeAction(page,'返回上一展示');
    await homeAction(page,'固定展示');
    await page.getByRole('button',{name:'生成讲解视频',exact:true}).click();
    const video=page.locator('video:visible');await video.waitFor();
    await page.waitForFunction(()=>document.querySelector('video')?.readyState>=2);
    await video.focus();await video.press('Space');await page.waitForFunction(()=>{const v=document.querySelector('video');return !v.paused&&v.currentTime>.3;});
    await video.press('Space');await page.waitForFunction(()=>document.querySelector('video').paused);
    await page.evaluate(()=>window.originalVideo=document.querySelector('video'));
    await video.press('ArrowRight');
    const afterKey=await state();assert.equal(afterKey.expanded,true);assert.equal(afterKey.entries,2);
    assert.equal(await page.evaluate(()=>originalVideo===document.querySelector('video')),true);
    evidence.afterKey=afterKey;
    // Stable or older Web generations cannot trigger navigation.
    await page.evaluate(()=>{for(const stamp of [100,100,99])window.dispatchEvent(new CustomEvent('portal:events-ready',{detail:{server_started_at:stamp}}));});
    assert.equal((await state()).timeOrigin,afterKey.timeOrigin);
    // A new Web process intentionally reloads the same URL via portal_theme.
    await Promise.all([page.waitForEvent('domcontentloaded'),page.evaluate(()=>window.dispatchEvent(new CustomEvent('portal:events-ready',{detail:{server_started_at:200}})))]);
    await page.waitForSelector('.homeDialogueChooser',{state:'attached'});
    evidence.afterRestart=await state();await page.screenshot({path:path.join(out,'after-restart.png')});
    assert.notEqual(evidence.afterRestart.timeOrigin,afterKey.timeOrigin);
    assert.equal(evidence.afterRestart.entries,2,'new document restores host history');
    assert.equal(evidence.afterRestart.expanded,true);assert.equal(evidence.afterRestart.pinned,'true');assert.equal(evidence.afterRestart.pane,'display');
    await page.getByRole('heading',{name:'lifecycle-first',exact:true}).waitFor();
    assert.equal(await page.locator('video').count(),1,'fixed report restores its persisted media');
    assert.equal(reads.filter(r=>r.path==='/api/home-report/save').length,2,'restoring target never replays materialization');
    assert.equal(reads.filter(r=>r.path==='/api/home-report/media').length,1,'restoring media never regenerates it');
    // Persisted pagehide is a suspension, not destruction of history instances.
    await video.waitFor();
    await page.evaluate(()=>{window.cachedVideo=document.querySelector('video');window.cachedEntry=document.querySelector('.homeDialogueEntry');window.dispatchEvent(new PageTransitionEvent('pagehide',{persisted:true}));});
    await page.evaluate(()=>window.dispatchEvent(new PageTransitionEvent('pageshow',{persisted:true})));
    await page.getByRole('heading',{name:'lifecycle-first',exact:true}).waitFor();
    assert.equal(await page.evaluate(()=>cachedVideo===document.querySelector('video')&&cachedEntry===document.querySelector('.homeDialogueEntry')),true);
    evidence.cacheRestore=await state();assert.equal(evidence.cacheRestore.expanded,true);
    await page.goto(`http://127.0.0.1:${server.address().port}/away`);await page.goBack();
    await page.getByRole('heading',{name:'lifecycle-first',exact:true}).waitFor();
    evidence.actualBack={...await state(),pageshow:evidence.events.filter(e=>e.name==='pageshow'&&e.trusted).at(-1)};
    assert.equal(evidence.actualBack.entries,2);assert.equal(evidence.actualBack.expanded,true);
    if(evidence.actualBack.pageshow.persisted)assert.equal(await page.evaluate(()=>cachedVideo===document.querySelector('video')),true);
    denied=true;await page.reload();await page.getByText('fixture permission revoked',{exact:true}).waitFor();
    evidence.deniedRestore=await state();assert.equal(await page.getByRole('heading',{name:'lifecycle-first',exact:true}).count(),0);
    denied=false;username='other-fixture';await page.reload();await page.waitForSelector('.homeDialogueChooser',{state:'attached'});
    assert.equal((await state()).entries,0,'different identity cannot adopt history');
    evidence.otherIdentity=await state();assert.deepEqual(evidence.errors,[]);evidence.passed=true;
    console.log(JSON.stringify({passed:true,afterRestart:evidence.afterRestart,cacheRestore:evidence.cacheRestore,reads:reads.length}));
  } finally {fs.writeFileSync(path.join(out,'result.json'),JSON.stringify(evidence,null,2));await browser.close();await new Promise(r=>server.close(r));}
})().catch(e=>{console.error(e);process.exitCode=1;});
