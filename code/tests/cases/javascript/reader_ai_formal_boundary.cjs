const fs=require('node:fs'),path=require('node:path'),cp=require('node:child_process'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'../../..');
const {chatWorkspaceScripts}=require('./chat_workspace_modules.cjs');
const {portalWorkbenchScripts}=require('./portal_workbench_scripts.cjs');
const scripts=['portal_state.js','portal_page_state.js','portal_text.js','portal_ui_primitives.js','portal_ui.js','portal_overlay.js','portal_resource_chips.js','portal_image_editor.js','portal_editor_open_service.js','portal_command_registry.js','portal_workbench_context.js',...portalWorkbenchScripts(), 'portal_resident_service.js','resident_protocol.js',...chatWorkspaceScripts()];
(async()=>{const browser=await require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright').chromium.launch({headless:true});try{
const [url,name,value,raw]=process.argv.slice(2);const context=await browser.newContext();await context.addCookies([{name,value,url}]);const page=await context.newPage();
await page.goto(url);await page.evaluate(()=>{window.PortalIdentity={username:()=> 'reader_one'};window.PortalEvents={isConnected:()=>true,connect(){}};window.PortalAPI={get:async url=>{throw Error('Unavailable isolated ancillary API: '+url);}};window.PortalInstructionClient={execute:async name=>{throw Error('No isolated instruction transport: '+name);}};window.ownerResponses=[];});
const route=cp.execFileSync(process.env.TFI_TEST_PYTHON,['-c','from management_portal.resources.portal import portal_route_script;print(portal_route_script().decode())'],{cwd:root,encoding:'utf8'});await page.addScriptTag({content:route});
for(const file of scripts)await page.addScriptTag({content:fs.readFileSync(path.join(root,'management_portal/static',file),'utf8')});
const outcome=await page.evaluate(async document=>{
const prepare=async read_only=>{const response=await fetch('/chat-workspace/api/prepare',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({surface:'paper',workspace_key:'paper-reader:'+document.paper_id+':00000000-0000-4000-8000-000000000001',message:'synthetic question',read_only,context:{document,expected_principal:'reader_one',selection:'synthetic selection'}})});const value=await response.json();ownerResponses.push({read_only,status:response.status,...value});if(!response.ok)throw Error(value.error);return value;};
const panel=PortalChatWorkspace.panelPackage.create({root:window.document.querySelector('#chat'),id:'formal-reader-probe',autoLoad:false,draftOwner:()=> 'reader_one',resolveResident:()=>prepare(true),resolveBeforeSend:()=>prepare(false),onError:()=>{}});
let openError='',sendError='';try{await panel.openSurface({workspace_key:'reader-probe'});}catch(e){openError=e.message;}
try{await panel.send({text:'synthetic question',plainText:'synthetic question'});}catch(e){sendError=e.message;}
const result={realComponent:!!panel.workspace,openError,sendError,responses:ownerResponses,composer:!!window.document.querySelector('[role="textbox"]')};panel.destroy();return result;
},JSON.parse(raw));
console.log(JSON.stringify(outcome,null,2));assert(outcome.realComponent);assert(outcome.composer);assert(outcome.responses.some(r=>r.read_only&&r.status===400&&r.error.includes('权威业务入口尚未启用')));assert(outcome.responses.some(r=>!r.read_only&&r.status===400&&r.error.includes('权威业务入口尚未启用')));
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
