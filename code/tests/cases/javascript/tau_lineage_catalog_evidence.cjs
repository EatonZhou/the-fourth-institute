'use strict';
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict'),{execFileSync}=require('node:child_process');
const code=process.env.TAU_CODE_ROOT||path.resolve(__dirname,'../../..'),portal=path.join(code,'management_portal');
const {readLineageCatalogSurface:read}=require(path.join(portal,'test_framework/tau_lineage_catalog_evidence'));
const {chromium}=require(path.join(portal,'test_framework/tau_runtime/node_modules/playwright'));
const python=s=>execFileSync('python3',['-c',s],{cwd:code,encoding:'utf8'});
const html=python('from management_portal import script_groups; print(script_groups.expand_document(open("management_portal/lineage.html").read()))');
const routes=python('from management_portal.resources.portal import portal_route_script; print(portal_route_script().decode())');
(async()=>{const b=await chromium.launch({headless:true,args:['--no-sandbox']});try{
 const p=await b.newPage({viewport:{width:1440,height:1000}}),errors=[],requests=[];
 p.on('pageerror',e=>errors.push(e.message));
 await p.addInitScript(()=>{window.PortalSite={apply(){}};});
 await p.route('**/*',async route=>{
  const req=route.request(),u=new URL(req.url());assert.equal(u.origin,'http://tau-fixture.local');requests.push({path:u.pathname,method:req.method(),body:req.postDataJSON()});
  if(u.pathname==='/lineage')return route.fulfill({contentType:'text/html',body:html});
  if(u.pathname==='/static/portal_resource_routes.js')return route.fulfill({contentType:'text/javascript',body:routes});
  const f=path.join(portal,u.pathname);if(fs.existsSync(f)&&fs.statSync(f).isFile())return route.fulfill({path:f});
  if(u.pathname==='/api/events')return route.fulfill({contentType:'text/event-stream',body:''});
  let payload={};
  if(u.pathname==='/api/me')payload={username:'fixture',display_name:'Fixture'};
  if(u.pathname==='/api/agent-workbench/residents')payload={ready:true,residents:[],lineages:[],recommendation_resident:{resident_id:'resident_cccccccccccc',title:'Recommender',lineage_id:'lineage_dddddddddddd'},recommendation_status:'available',principal_hierarchy:{projects:[{project_id:'project_aaaaaaaaaaaa',lineage_id:'lineage_bbbbbbbbbbbb',title:'Loading records',accessible:true,resident_id:'resident_aaaaaaaaaaaa',lineages:[]}],user_residents:[]}};
  if(u.pathname==='/api/instructions/execute')payload={status:'committed',result:{}};
  return route.fulfill({contentType:'application/json',body:JSON.stringify(payload)});
 });
 await p.goto('http://tau-fixture.local/lineage');
 await p.locator('#agentList > .agentList').waitFor({timeout:12000});
 await p.locator('#agentList [data-resident-project-group]').waitFor({timeout:10000});
 const root=p.locator('#lineage-navigation-panel');
 const originalPredicate=await p.getByRole('button',{name:'New node',exact:true}).count()===1;
 assert.equal(originalPredicate,false);assert.equal(await p.locator('.portalHatgPanelComponent').count(),0);
 const first=await read(root);
 assert.equal(first.surface_ready,true,JSON.stringify(first));assert.equal(Object.hasOwn(first,'ready'),false);
 assert.match(await p.locator('#agentList').innerText(),/Loading records/);
 // Catalog/target identity remains an original-response responsibility. A
 // project title containing Loading is not a loading row or an editor gate.
 await p.evaluate(()=>{
  const other=document.createElement('aside');other.id='unrelated-loading';other.setAttribute('aria-busy','true');
  other.textContent='Loading unrelated Preview';document.body.append(other);
 });
 assert.equal((await read(root)).surface_ready,true);
 const savedList=await p.locator('#agentList').innerHTML();
 for(const kind of ['loading','error','empty']){
  await p.locator('#agentList').evaluate((n,kind)=>n.innerHTML=PortalUIPrimitives.asyncStateHtml({state:kind,message:'Localized catalog status'}),kind);
  const observed=await read(root);assert.equal(observed.surface_ready,false);
  assert.equal(observed.candidates[0].lists[0].states[0].state,kind);
 }
 await p.locator('#agentList').evaluate((n,html)=>n.innerHTML=html,savedList);
 assert.equal((await read(root)).surface_ready,true);
 await root.evaluate(n=>n.hidden=true);assert.equal((await read(root)).surface_ready,false);await root.evaluate(n=>n.hidden=false);
 await root.evaluate(n=>n.inert=true);assert.equal((await read(root)).surface_ready,false);await root.evaluate(n=>n.inert=false);
 await root.evaluate(n=>n.setAttribute('aria-busy','true'));assert.equal((await read(root)).surface_ready,false);await root.evaluate(n=>n.removeAttribute('aria-busy'));
 assert.equal((await read(root,{panelId:'other'})).surface_ready,false);
 assert.equal((await read(p.locator('#missing'))).surface_ready,false);
 await root.evaluate(n=>n.after(n.cloneNode(true)));const duplicated=await read(root);
 assert.equal(duplicated.surface_ready,false);assert.equal(duplicated.candidates.length,2);await root.last().evaluate(n=>n.remove());
 await root.locator('#refreshBtn').evaluate(n=>n.disabled=true);assert.equal((await read(root)).surface_ready,false);await root.locator('#refreshBtn').evaluate(n=>n.disabled=false);
 await root.locator('[data-side-panel="chatrooms"]').evaluate(n=>n.classList.add('active'));
 assert.equal((await read(root)).surface_ready,false);await root.locator('[data-side-panel="chatrooms"]').evaluate(n=>n.classList.remove('active'));
 await root.locator('#agentList > .agentList').evaluate(n=>n.remove());assert.equal((await read(root)).surface_ready,false);
 await root.locator('#agentList').evaluate((n,html)=>n.innerHTML=html,savedList);
 const htmlBefore=await root.innerHTML(),beforeRequests=requests.length;
 assert.equal((await read(root)).surface_ready,true);assert.equal(await root.innerHTML(),htmlBefore);
 assert.equal(requests.length,beforeRequests);assert.deepEqual(errors,[]);
 assert.ok(requests.filter(r=>r.path==='/api/instructions/execute').every(r=>/^(List|Get|Search)/.test(r.body.instruction)));
 console.log(JSON.stringify({status:'passed',original_new_node_predicate:originalPredicate,hatg_mounted:false,
  native_surface:first,requests:requests.filter(r=>!r.path.startsWith('/static/')),errors,
  fixture:'Full native lineage.html and script groups/PortalWorkbench/PortalNavigation/sidebar/CSS; local read-response fixtures, no live mutation',
  formal_acceptance:false,production_requests:0}));

}finally{await b.close();}})().catch(e=>{console.error(e.stack);process.exitCode=1;});
