'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const root=path.resolve(__dirname,'../../../management_portal');
const out=process.env.REPO_HEADER_EVIDENCE;
(async()=>{
 const browser=await chromium.launch({headless:true});
 try {
  const page=await browser.newPage({viewport:{width:1440,height:900}});page.setDefaultTimeout(5000);
  const errors=[],observations=[];page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/*',r=>r.abort());
  await page.setContent('<!doctype html><html lang="zh-CN" data-portal-theme="light"><body data-resource-category="repo"><main style="padding:24px;max-width:1440px;margin:auto"><section id="repoWorkspace"></section></main></body></html>');
  const html=fs.readFileSync(path.join(root,'repo_workspace.html'),'utf8');
  for(const [,name] of html.matchAll(/<link[^>]+href="\/static\/([^"?]+\.css)"/g))await page.addStyleTag({path:path.join(root,'static',name)});
  for(const [,css] of html.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g))await page.addStyleTag({content:css});
  for(const name of ['portal_text','portal_state','portal_translations','portal_i18n','portal_ui_primitives','portal_overlay','portal_ui','portal_resource_header','repo_workspace_code_view','repo_workspace_shell','repo_workspace_events'])await page.addScriptTag({path:path.join(root,'static',name+'.js')});
  await page.evaluate(()=>{
   PortalI18n.apply('zh-CN');const noop=()=>{}, asyncNoop=async()=>{};
   window.calls=[];
   window.state={selectedId:'repo_dd656e09c207',records:[],activeTab:'code',overview:{name:'portal-multiagent-trace',ownership:'external',project_ids:['Harness芯片'],capabilities:{git:true,write_files:true},status:{dirty:false,changes:[]},branches:[{name:'main',current:true},{name:'feature/design'}],current_branch:'main',default_branch:'main',branch_count:2,tag_count:0,remotes:[{name:'origin',url:'https://example.com/repo.git',web_url:'https://example.com/repo'}],checkout_writable:true,checkout_path:'/fixture/repo'},tree:{path:'',entries:[]}};
   const registerForm=document.createElement('form');registerForm.innerHTML='<input name="placement">';
   const elements=new Proxy({workspace:document.getElementById('repoWorkspace'),modeTabs:[],editorTabs:[],registerForm},{get:(o,k)=>o[k]||(o[k]=document.createElement('input'))});
   window.PortalWorkbench={model:{split:noop,stack:noop},create:()=>({destroy:noop})};
   const deps=Object.fromEntries(PortalRepoWorkspaceCodeView.dependencies.map(k=>[k,noop]));
   Object.assign(deps,{state,elements,esc:PortalUIPrimitives.escapeHtml,uiIcon:PortalUIPrimitives.iconHtml,workspaceCapability:n=>state.overview.capabilities[n]===true});
   const code=PortalRepoWorkspaceCodeView.createCodeView(deps);
   const sd=Object.fromEntries(PortalRepoWorkspaceShell.dependencies.map(k=>[k,noop]));
   Object.assign(sd,deps,code,{projectIds:()=>[],objectOf:v=>v||{},renderChangesView:()=>'<p>Changes</p>',renderBranchesView:()=>'<p>Branches</p>'});
   window.shell=PortalRepoWorkspaceShell.createShell(sd);shell.renderWorkspace();
   const ed=Object.fromEntries(PortalRepoWorkspaceEvents.dependencies.map(k=>[k,(...args)=>{calls.push({name:k,value:args[0] instanceof Element?'element':args[0]});return Promise.resolve();}]));
   Object.assign(ed,{state,elements,renderWorkspace:shell.renderWorkspace,workspaceCapability:deps.workspaceCapability,
    dialogs:new Proxy({},{get:()=>({open:()=>calls.push({name:'shortcuts'}),close:noop})}),
    selectTab:async tab=>{calls.push({name:'selectTab',value:tab});state.activeTab=tab;shell.renderWorkspace();}});
   PortalRepoWorkspaceEvents.createEvents(ed);
  });
  for(const [width,theme] of JSON.parse(process.env.REPO_HEADER_VIEWS || '[[1440,"light"],[768,"light"],[390,"light"],[1440,"dark"]]')){
   await page.setViewportSize({width,height:900});await page.evaluate(theme=>document.documentElement.dataset.portalTheme=theme,theme);
   await page.evaluate(()=>document.getAnimations().forEach(a=>{if(Number.isFinite(a.effect.getComputedTiming().endTime))a.finish();}));
   const observation=await page.evaluate(()=>({overflow:document.documentElement.scrollWidth>innerWidth,
    tabs:[...document.querySelectorAll('.repoTab')].map(e=>({name:e.textContent.trim(),size:parseFloat(getComputedStyle(e).fontSize)})),
    branch:getComputedStyle(document.querySelector('.repoBranchCurrent')).fontSize,
    branchStyle:{bg:getComputedStyle(document.querySelector('.repoBranchControl')).backgroundColor,panel:getComputedStyle(document.querySelector('.repoBranchControl')).getPropertyValue('--panel'),text:getComputedStyle(document.querySelector('.repoBranchControl')).color,root:getComputedStyle(document.documentElement).getPropertyValue('--panel')},
    toolbarBorder:getComputedStyle(document.querySelector('.repoCodeToolbar')).borderTopWidth,
    visibleActions:[...document.querySelectorAll('.repoIdentityActions button')].filter(e=>e.checkVisibility()).length}));
   assert.equal(observation.overflow,false,JSON.stringify({width,...observation}));assert.equal(observation.toolbarBorder,'0px');assert.equal(observation.visibleActions,1);
   assert(observation.tabs.every(t=>t.size>=14));
   if(out){fs.mkdirSync(out,{recursive:true});await page.screenshot({path:path.join(out,`${width}-${theme}.png`),fullPage:true});}
   observations.push({width,theme,...observation});
  }
  if(!process.env.REPO_HEADER_VISUAL_ONLY){
  await page.setViewportSize({width:390,height:900});
  await page.locator('[data-repo-more]').focus();await page.keyboard.press('Enter');await page.locator('#repoMoreActions').waitFor({state:'visible'});
  assert(await page.locator('[data-repo-discard]').isDisabled());
  await page.keyboard.press('Escape');await page.locator('#repoMoreActions').waitFor({state:'hidden'});
  assert(await page.locator('[data-repo-more]').evaluate(e=>e===document.activeElement));
  for(const name of ['copy-id','register','shortcuts']){
   await page.locator('[data-repo-more]').click();await page.locator(`[data-repo-action="${name}"]`).click();
   await page.locator('#repoMoreActions').waitFor({state:'hidden'});
  }
  await page.locator('#repoBranchPicker').selectOption('feature/design');
  await page.locator('[data-repo-sync="pull"]').click();await page.locator('[data-repo-sync="push"]').click();
  await page.locator('.repoFileTools [data-repo-action="new-file"]').click();
  await page.evaluate(()=>{state.overview.status.dirty=true;shell.renderWorkspace();});
  assert(await page.locator('#repoBranchPicker').isDisabled());assert(await page.locator('[data-repo-sync="pull"]').isDisabled());
  await page.locator('[data-repo-more]').click();await page.locator('[data-repo-discard]').click();
  await page.locator('.repoTab[data-repo-tab="branches"]').click();
  assert.equal(await page.locator('.repoTab[aria-selected="true"]').getAttribute('data-repo-tab'),'branches');
  const calls=await page.evaluate(()=>window.calls);
  for(const name of ['copyRepoText','openRegisterModal','shortcuts','switchBranch','syncRepo','openEditor','discardRepoChanges','selectTab']) assert(calls.some(c=>c.name===name),name);
  await page.evaluate(()=>{state.activeTab='code';state.overview.capabilities={git:false};shell.renderWorkspace();});
  assert.equal(await page.locator('[data-repo-sync], #repoBranchPicker, [data-repo-discard], [data-repo-action="new-file"]').count(),0);
  }
  assert.deepEqual(errors,[]);
  const report={status:'passed',observations,errors,boundary:'Real Repo shell/code/event owners with isolated data and business callbacks; no Git mutation.'};
  if(out)fs.writeFileSync(path.join(out,'report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify({status:'passed',views:observations.length,errors}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
