'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const code=process.env.TAU_CODE_ROOT||path.resolve(__dirname,'../../..'),portal=path.join(code,'management_portal');
const {readHatgControlsSnapshot:read}=require(path.join(portal,'test_framework/tau_hatg_snapshot_evidence'));
const {chromium}=require(path.join(portal,'test_framework/tau_runtime/node_modules/playwright'));
const {portalWorkbenchScripts}=require('./portal_workbench_scripts.cjs');
(async()=>{const browser=await chromium.launch({headless:true,args:['--no-sandbox']});try{
 const p=await browser.newPage({viewport:{width:1200,height:900}}),errors=[];
 p.on('pageerror',e=>errors.push(e.message));
 const requests=[];
 await p.route('**/*',async route=>{
  const request=route.request(),u=new URL(request.url());
  requests.push({path:u.pathname,method:request.method(),body:request.postDataJSON()});
  const instruction=request.postDataJSON()?.instruction;
  if(instruction)assert.match(instruction,/^(Get|List|Search)/,'Fixture allows only reads');
  assert.equal(u.origin,'http://tau-fixture.local');
  if(u.pathname==='/fixture')return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(portal,'lineage.html'),'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'')});
  const file=path.join(portal,u.pathname);
  if(fs.existsSync(file)&&fs.statSync(file).isFile())return route.fulfill({path:file});
  return route.fulfill({contentType:'application/json',body:'{}'});
 });
 await p.goto('http://tau-fixture.local/fixture');
 const names=['portal_state','portal_text','portal_translations','portal_i18n','portal_page_state','portal_api','portal_instruction_client','portal_identity','portal_society_service','portal_resident_discovery_service','portal_overlay','portal_ui_primitives','portal_ui','portal_workbench_context',...portalWorkbenchScripts(path.join(portal,'static')).map(file=>file.replace(/\.js$/,'')),'lineage_workbench_controller','portal_resource_header'];
 for(const n of names)await p.addScriptTag({path:path.join(portal,'static',n+'.js')});
 const loader=fs.readFileSync(path.join(portal,'static/portal_hatg_loader.js'),'utf8');
 for(const match of loader.matchAll(/"(\/static\/[^" ]+\.js)"/g))await p.addScriptTag({path:path.join(portal,match[1])});
 await p.evaluate(async()=>{
  const root=document.createElement('section');root.id='lineage-hatg-panel';root.setAttribute('role','region');root.setAttribute('aria-label','Lineage editor');document.querySelector('#lineageWorkbenchRoot').append(root);
  window.panel=PortalHatgPanel.mountHatgPanel({root,pageState:{read:()=>({}),write(){},writeSoon(){}},waitForServiceReady:async()=>{},onLocationChange(){},onDeleteLineage(){}});
  await panel.ready;
 });
 assert.equal(await p.locator('#lineage-hatg-panel #graphNavigationBar').count(),1);
 let oldError=null;try{await p.locator('main').ariaSnapshot({timeout:300});}catch(e){oldError={name:e.name,message:e.message,stack:e.stack};}
 assert.match(oldError.message,/strict mode violation/);
 assert.deepEqual(await p.locator('main').evaluateAll(es=>es.map(e=>e.id)),['consoleShell','agentLineageShell']);
 assert.equal(await p.locator('#lineage-hatg-panel').getAttribute('data-hatg-error'),null);
 const root=p.locator('#lineage-hatg-panel'),expected={lineageId:'lineage_fixture',snapshotTimeoutMs:200};
 // Real mount, template, renderer and CSS; identity data is a local fixture,
 // not evidence of GetLineage, member creation or any live business action.
 await p.evaluate(()=>PortalResourceHeader.mount(document.querySelector('#activeLineageIdentity'),{
  title:'Local isolated lineage',type:'lineage',resource_id:'lineage_fixture',copy_label:'lineage_id',
 }));
 const first=await read(root,expected);
 assert.equal(first.captured,true,JSON.stringify(first));
 assert.equal(first.before.mains.length,2);assert.match(first.snapshot,/lineage_fixture/);
 assert.equal(first.same_root,true);assert.equal(first.error,null);
 assert.ok(first.before.controls.some(c=>c.id==='addNodeBtn'));
 const broad=await read(p.locator('main'),expected);
 assert.equal(broad.captured,false);assert.equal(broad.candidates.length,2);assert.equal(broad.snapshot,null);
 assert.equal((await read(root,{...expected,lineageId:'lineage_other'})).captured,false);
 assert.equal((await read(p.locator('#missing-root'),expected)).captured,false);
 await root.evaluate(n=>n.hidden=true);assert.equal((await read(root,expected)).captured,false);
 await root.evaluate(n=>n.hidden=false);
 await root.evaluate(n=>n.setAttribute('aria-busy','true'));assert.equal((await read(root,expected)).captured,false);
 await root.evaluate(n=>n.removeAttribute('aria-busy'));
 await root.locator('[data-portal-resource-id]').evaluate(n=>n.after(n.cloneNode(true)));
 assert.equal((await read(root,expected)).captured,false);
 await root.locator('[data-portal-resource-id]').last().evaluate(n=>n.remove());
 await root.evaluate(n=>n.after(n.cloneNode(true)));
 assert.equal((await read(root,expected)).captured,false);
 await root.last().evaluate(n=>n.remove());
 const html=await root.innerHTML();
 assert.equal((await read(root,expected)).captured,true);assert.equal(await root.innerHTML(),html);
 // Failure at the snapshot call must retain the earlier controls and stack.
 // These faults are injected at the read boundary, not into business handlers.
 const bind=snapshot=>({elementHandles:()=>root.elementHandles(),ariaSnapshot:snapshot,
  evaluateAll:(fn,arg)=>root.evaluateAll(fn,arg)});
 const injected=new Error('isolated snapshot failure');injected.name='TimeoutError';
 const failed=await read(bind(async()=>{throw injected;}),expected);
 assert.equal(failed.captured,false);assert.equal(failed.error.phase,'aria_snapshot');
 assert.equal(failed.error.name,'TimeoutError');assert.equal(failed.error.stack,injected.stack);
 assert.ok(failed.before.controls.length);assert.equal(failed.snapshot,null);assert.equal(failed.after,null);
 const changed=await read(bind(async options=>{
  const snapshot=await root.ariaSnapshot(options);
  await root.evaluate(n=>n.replaceWith(n.cloneNode(true)));
  return snapshot;
 }),expected);
 assert.equal(changed.captured,false);assert.equal(changed.same_root,false);
 assert.equal(changed.error.phase,'identity_after_snapshot');assert.ok(changed.snapshot);
 assert.equal(changed.after.connected,false);
 const timeout=await read(bind(async options=>{
  assert.equal(options.timeout,200);
  await root.evaluate(n=>n.remove());
  return root.ariaSnapshot(options);
 }),expected);
 assert.equal(timeout.captured,false);assert.equal(timeout.error.name,'TimeoutError');
 assert.equal(timeout.error.phase,'aria_snapshot');assert.ok(timeout.before.controls.length);
 assert.equal(timeout.snapshot,null);
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({status:'passed',baseline_error:oldError,main_ids:first.before.mains.map(x=>x.id),
  captured:first.captured,retained_failure:failed.error,detached_timeout:timeout.error,
  replacement_rejected:!changed.captured,fixture:'Native lineage.html outer main and public HATG mount/template/modules/CSS; local HTTP catalog and identity data fixtures',
  requests:requests.filter(r=>!r.path.startsWith('/static/')),formal_acceptance:false,production_requests:0}));
 await p.evaluate(()=>panel.destroy());
}finally{await browser.close();}})().catch(e=>{console.error(e.stack);process.exitCode=1;});
