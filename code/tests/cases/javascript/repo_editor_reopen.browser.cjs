"use strict";
// Two actual browser pages, the production editor controller, isolated HTTP.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const http = require("node:http");
const crypto = require("node:crypto");
const {chromium} = require("../../../management_portal/test_framework/tau_runtime/node_modules/playwright");
let content = "original Unicode 协作\n";
const hash = () => crypto.createHash("sha256").update(content).digest("hex");
const file = () => ({path:"协作.txt", name:"协作.txt", content, hash:hash(), editable:true});
let conflicts = 0;
const html = `<!doctype html><button id="edit">Edit</button><pre id="preview"></pre>
<section id="modal" hidden><h2 id="title"></h2><span id="eyebrow"></span><input id="path"><textarea id="source"></textarea><pre id="rendered"></pre><p id="status"></p><button id="save">Save</button><button id="close">Discard and close</button></section>
<script>
const template=(parts,...values)=>parts.reduce((out,part,i)=>out+part+(values[i]??''),'');
window.PortalI18n={t:value=>value,text:template,html:template};
</script><script src="/controller.js"></script><script>
const byId=id=>document.getElementById(id);
const state={selectedId:'repo_123456789abc',navigationSequence:1,tree:{path:''}};
const getJson=async(url,options={})=>{const response=await fetch(url,options);const data=await response.json();if(!response.ok)throw Error(data.error);return data;};
const elements={editorEyebrow:byId('eyebrow'),editorTitle:byId('title'),editorPath:byId('path'),editorSource:byId('source'),editorStatus:byId('status'),editorPreview:byId('rendered'),editorSave:byId('save'),editorTabs:[]};
const deps=Object.fromEntries(PortalRepoWorkspaceDialogs.dependencies.map(key=>[key,()=>{}]));
const renderWorkspace=()=>{byId('preview').textContent=state.file?.content||'';};
Object.assign(deps,{state,elements,getJson,queryState:()=>({}),workspaceCapability:()=>true,renderWorkspace,dirname:()=>'',
 dialogs:{editor:{open:()=>{byId('modal').hidden=false;},close:()=>{byId('modal').hidden=true;}}},
 postJson:(url,data)=>getJson(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}),
 loadWorkspace:async()=>{state.file=await getJson('/api/repos/repo_123456789abc/workspace/file?path='+encodeURIComponent('协作.txt'),{cache:'no-store'});renderWorkspace();},
 showToast:message=>{byId('status').textContent=message;}});
const owner=PortalRepoWorkspaceDialogs.createDialogs(deps);
byId('edit').onclick=()=>owner.openEditor(state.file);
byId('save').onclick=()=>owner.saveEditor();
byId('close').onclick=()=>owner.closeEditor();
window.editorState=state;
deps.loadWorkspace().then(()=>{document.body.dataset.ready='true';});
</script>`;
(async()=>{
  const server=http.createServer(async(req,res)=>{
    if(req.url==='/controller.js') {
      res.setHeader('Content-Type','text/javascript');
      return res.end(fs.readFileSync(path.join(__dirname,'../../../management_portal/static/repo_workspace_dialogs.js')));
    }
    if(req.url.startsWith('/api/repos/')) {
      res.setHeader('Content-Type','application/json');
      res.setHeader('Cache-Control','max-age=3600');
      if(req.method==='POST') {
        let raw='';for await(const chunk of req)raw+=chunk;
        const data=JSON.parse(raw);
        if(data.expected_hash!==hash()) {conflicts++;res.writeHead(409);return res.end(JSON.stringify({error:'version conflict'}));}
        content=data.content;
        return res.end('{}');
      }
      return res.end(JSON.stringify(file()));
    }
    res.setHeader('Content-Type','text/html');res.end(html);
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const browser=await chromium.launch({headless:true});
  try {
    const context=await browser.newContext();
    const a=await context.newPage(),b=await context.newPage();
    const errors=[];
    for(const page of [a,b]) {
      page.on('pageerror',error=>errors.push(error.message));
      await page.goto('http://127.0.0.1:'+server.address().port);
      await page.waitForFunction(()=>document.body.dataset.ready==='true');
      await page.getByRole('button',{name:'Edit',exact:true}).click();
      await page.locator('#modal').waitFor({state:'visible'});
    }
    await a.locator('#source').fill('saved by A 中文\n');
    await a.getByRole('button',{name:'Save',exact:true}).click();
    await a.locator('#modal').waitFor({state:'hidden'});
    await b.locator('#source').fill('B draft survives conflict');
    await b.getByRole('button',{name:'Save',exact:true}).click();
    await b.waitForFunction(()=>document.getElementById('status').textContent==='version conflict');
    assert.equal(await b.locator('#source').inputValue(),'B draft survives conflict');
    await b.getByRole('button',{name:'Discard and close',exact:true}).click();
    await b.getByRole('button',{name:'Edit',exact:true}).click();
    await b.locator('#modal').waitFor({state:'visible'});
    assert.equal(await b.locator('#source').inputValue(),'saved by A 中文\n');
    assert.equal(await b.evaluate(()=>editorState.editorExpectedHash),hash());
    assert.equal(await b.locator('#preview').innerText(),'saved by A 中文\n');
    await b.locator('#source').fill('explicit merge A+B\n');
    await b.getByRole('button',{name:'Save',exact:true}).click();
    await b.locator('#modal').waitFor({state:'hidden'});
    assert.equal(content,'explicit merge A+B\n');
    assert.equal(conflicts,1);
    assert.deepEqual(errors,[]);
    console.log('PASS real Chromium dual-page conflict, retained draft, discard/reopen fresh bytes/hash, explicit merge without page refresh');
  } finally {await browser.close();server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error);process.exitCode=1;});
