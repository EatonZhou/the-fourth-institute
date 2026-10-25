'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const {execFileSync} = require('node:child_process');
const codeRoot = process.env.TFI_TAU_CODE_ROOT || path.resolve(__dirname,'../../..');
const {chromium} = require(path.join(codeRoot, 'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const {readWallChatPanel} = require(path.join(codeRoot, 'management_portal/test_framework/tau_detail_prerequisites'));
const routeScript = execFileSync(process.env.TFI_TEST_PYTHON || 'python3', ['-c', 'from management_portal.resources.portal import portal_route_script; print(portal_route_script().decode())'], {cwd: codeRoot, encoding:'utf8'});
const scripts = ['portal_state.js','portal_page_state.js','portal_text.js','portal_ui_primitives.js','portal_ui.js',
  'portal_overlay.js','portal_resource_chips.js','portal_image_editor.js','portal_editor_open_service.js','portal_command_registry.js',
  'portal_workbench_context.js','portal_workbench.js','portal_resident_service.js','resident_protocol.js','portal_chat_workspace.js'];

(async () => {
  const requests = [], errors = [];
  const server = http.createServer((req,res) => { requests.push(req.url); res.setHeader('Content-Type','text/html'); res.end('<!doctype html><html><body></body></html>'); });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    browser = await chromium.launch({headless:true,args:['--no-sandbox']});
    const page = await browser.newPage();
    page.on('pageerror', e => errors.push(e.message));
    await page.goto(`http://127.0.0.1:${server.address().port}/fixture`);
    await page.evaluate(html => {
      const doc = new DOMParser().parseFromString(html,'text/html');
      document.body.append(doc.getElementById('wallNativeView').content.querySelector('#wallDetail'));
      const detail = document.getElementById('wallDetail'); detail.hidden=false; detail.setAttribute('aria-hidden','false');
      document.getElementById('wallChatTabs').hidden=false;
      const style = document.createElement('style'); style.textContent='[role=textbox]{min-height:30px;width:300px} [hidden]{display:none!important}'; document.head.append(style);
      window.testIO = [];
      window.PortalIdentity = {username:()=> 'fixture'};
      window.PortalEvents = {isConnected:()=>true,connect(){}};
      window.PortalAPI = {get:async url => {window.testIO.push(url); if(/\/session-restart-options(?:\?|$)/.test(url))return {restart:null}; throw new Error('Unexpected API '+url);}};
      window.PortalInstructionClient = {execute:async (name,args) => {window.testIO.push(name); if(name==='ListInputHistory')return {items:[]}; throw new Error('Unexpected instruction '+name);}};
      window.testPanels=[];
    },fs.readFileSync(path.join(codeRoot,'management_portal/wall.html'),'utf8'));
    await page.addScriptTag({content:routeScript});
    for(const name of scripts){
      const file = name==='portal_chat_workspace.js' && process.env.TFI_CHAT_SOURCE ? process.env.TFI_CHAT_SOURCE : path.join(codeRoot,'management_portal/static',name);
      await page.addScriptTag({content:fs.readFileSync(file,'utf8')});
    }
    const samples=[];
    for (const [kind,hostId,tabId] of [['work','wallWorkChat','wallWorkTab'],['discussion','wallChat','wallDiscussionTab']]) {
      const result=await page.evaluate(({kind,hostId,tabId})=>{
        for (const [host,tab] of [['wallWorkChat','wallWorkTab'],['wallChat','wallDiscussionTab']]){
          document.getElementById(host).hidden=host!==hostId;
          document.getElementById(tab).setAttribute('aria-selected',String(tab===tabId));
        }
        const root=document.getElementById(hostId);
        const before=root.getAttribute('aria-labelledby');
        for(let mount=0;mount<2;mount++){
          const panel=window.PortalChatWorkspace.panelPackage.create({root,id:`fixture-${kind}-${mount}`,autoLoad:false});
          window.testPanels.push(panel);
          if(mount===0)panel.destroy();
        }
        return {before,after:root.getAttribute('aria-labelledby'),role:root.getAttribute('role'),tab:tabId,tabName:document.getElementById(tabId).textContent.trim()};
      },{kind,hostId,tabId});
      samples.push(result);
      assert.equal(result.after,result.before,`Shared Chat must preserve the native ${kind} tabpanel owner on mount and remount`);
      assert.equal(result.role,'tabpanel');
      assert.equal(await page.getByRole('tabpanel',{name:result.tabName,exact:true}).count(),1);
      const sample=await readWallChatPanel(page.locator('#wallDetail'),{kind,...(kind==='work'?{messageId:'msg_fixture'}:{})});
      assert.equal(sample.selected_tab,kind); assert.equal(sample.panel_visible,true);
    }
    const fallback=await page.evaluate(()=>{
      const results=[];
      for(const spec of [{id:'plain'},{id:'unnamed',role:'tabpanel'},{id:'named',role:'tabpanel',label:'Host-owned label'}]){
        const root=document.createElement('section');root.id=spec.id;
        if(spec.role)root.setAttribute('role',spec.role);
        if(spec.label)root.setAttribute('aria-label',spec.label);
        document.body.append(root);
        for(let mount=0;mount<2;mount++){
          const panel=window.PortalChatWorkspace.panelPackage.create({root,id:`fixture-${spec.id}-${mount}`,autoLoad:false});window.testPanels.push(panel);
          if(mount===0)panel.destroy();
        }
        const labelledby=root.getAttribute('aria-labelledby');
        results.push({id:spec.id,label:root.getAttribute('aria-label'),labelledby,titleExists:!!document.getElementById(labelledby)});
      }
      return results;
    });
    for(const x of fallback.slice(0,2))assert.equal(x.titleExists,true,'Unnamed host still gets native Chat title');
    assert.equal(fallback[2].label,'Host-owned label');assert.equal(fallback[2].labelledby,null);
    await page.evaluate(()=>{for(const p of window.testPanels)p.destroy();window.dispatchEvent(new Event('pagehide'));});
    assert.deepEqual(errors,[]);
    const io=await page.evaluate(()=>window.testIO);
    assert.deepEqual(io,[],'Mounting unnamed local panels makes no API calls');
    assert.ok(requests.every(url=>url==='/fixture'||url==='/favicon.ico'));
    console.log(JSON.stringify({status:'passed',samples,fallback,production_requests:0,scope:'Isolated full native Chat mount/remount with native WALL template. Not formal TAU acceptance.'}));
  } finally {if(browser)await browser.close();await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
