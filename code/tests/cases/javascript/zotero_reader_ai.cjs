const assert=require('node:assert/strict');
const engine=require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright')[process.argv[3]];
(async()=>{const browser=await engine.launch({headless:true});try{
 const [url,,name,value]=process.argv.slice(2);const ctx=await browser.newContext();await ctx.addCookies([{name,value,url}]);
 await ctx.addInitScript(()=>{
  window.aiPrepares=[];window.aiSends=[];
  window.PortalChatWorkspace={messageRole:m=>m.role==='assistant'?'agent':m.role,panelPackage:{create(options){
   const field=document.createElement('textarea'),button=document.createElement('button');field.setAttribute('aria-label','AI测试提问');button.textContent='发送AI测试';options.root.append(field,button);
   const panel={workspace:{setDraftText:t=>field.value=t},openSurface:async target=>{await options.resolveResident(target);},
    send:async()=>{if(!aiSends.length)await options.resolveBeforeSend({}, {text:field.value});aiSends.push(field.value);options.onDetail({messages:[{role:'assistant',content_parts:[{type:'text',text:'合成回答：仅当前论文选区。'}]}]});},destroy:()=>{options.root.replaceChildren();}};
   button.onclick=()=>panel.send();return panel;
  }}};
 });
 const p=await ctx.newPage();await p.route('**/chat-workspace/api/prepare',route=>{const body=route.request().postDataJSON();return p.evaluate(b=>aiPrepares.push(b),body).then(()=>route.fulfill({json:{initial_context:{context_text:'trusted synthetic single-paper context'}}}));});
 await p.goto(url+'/integration');await p.waitForFunction(()=>ready);await p.evaluate(()=>openPaper('paper_0123456789ab'));await p.waitForFunction(()=>posts===1);await p.locator('#paperFullscreenBtn').click();
 const outer=p.frames()[1],inner=outer.frameLocator('iframe').first();await inner.locator('.textLayer span').first().dblclick();await outer.locator('.portalAISelection').click();
 await p.getByLabel('AI测试提问').waitFor();await p.waitForFunction(()=>aiPrepares.length===1);
 const first=await p.evaluate(()=>aiPrepares[0]);assert.equal(first.context.expected_principal,'reader_one');assert(first.context.selection);assert(first.context.document.attachment_version);assert(first.workspace_key.startsWith('paper-reader:paper_0123456789ab:'));
 assert.equal(first.read_only,true);await p.getByRole('button',{name:'发送AI测试'}).click();await p.waitForFunction(()=>aiSends.length===1);assert.equal(await p.evaluate(()=>aiPrepares[1].read_only),false);await p.getByLabel('AI测试提问').fill('仅追问当前讨论');await p.getByRole('button',{name:'发送AI测试'}).click();assert.equal(await p.evaluate(()=>aiPrepares.length),2);
 await p.getByRole('button',{name:/引用选区/}).click();await outer.waitForFunction(()=>document.querySelector('#pageNumber').value==='1');
 await p.getByText('确认保存回答为便签',{exact:true}).click();await p.getByLabel('选择 AI 回答').selectOption({index:1});
 assert.equal(await outer.locator('[data-sidebar-annotation-id]').count(),0,'answer not automatically saved');
 await p.getByRole('button',{name:'确认保存便签',exact:true}).click();await p.getByText('便签已保存',{exact:true}).waitFor();await outer.locator('[data-sidebar-annotation-id]').first().waitFor();
 await p.getByRole('button',{name:'新 AI 讨论'}).click();await p.waitForFunction(()=>aiPrepares.length===3);const next=await p.evaluate(()=>aiPrepares[2]);assert.notEqual(next.workspace_key,first.workspace_key);assert.equal(next.context.selection,'');
 await p.getByRole('button',{name:'收起 AI'}).click();console.log('PASS actual selection/document, isolated discussion identity, followup same panel, citation navigation, explicit note persisted; Research Chat panel contract synthetic');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
