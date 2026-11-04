// Opt-in real API check. Only HOME_DIALOGUE_FILE_ADDRESS is written; callers
// must supply their own disposable Workspace file. Never reuse a TAU session.
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const {chromium} = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const {openResidentPortal} = require('../../../scripts/resident_browser.cjs');
const homeAction = require('./home_dialogue_controls.cjs');
const portal = path.resolve(__dirname,'../../../management_portal');
const groups = JSON.parse(fs.readFileSync(path.join(portal,'script_groups.json')));
const html = fs.readFileSync(path.join(portal,'index.html'),'utf8')
  .replace(/<script data-portal-script-group="([^"]+)"><\/script>/g,(_,id)=>groups[id].map(src=>`<script src="${src}"></script>`).join(''))
  .replace('<script src="/static/home.js"></script>','');
(async()=>{
  const address=process.env.HOME_DIALOGUE_FILE_ADDRESS;
  assert.match(address || '',/^workspace_[a-f0-9]{12}\/file\/.+/, 'explicit disposable Workspace file required');
  const origin=process.env.HOME_DIALOGUE_ORIGIN || 'http://127.0.0.1:28401';
  const out=process.env.HOME_DIALOGUE_EVIDENCE || '/tmp/home-dialogue-resource-live'; fs.mkdirSync(out,{recursive:true});
  const client=await openResidentPortal({chromium,origin,residentId:'resident_6b7d6cdae31c',lineageId:'lineage_8794980b1bb7'});
  const page=client.page, errors=[], writes=[], instructions=[]; page.setDefaultTimeout(15000);
  const evidence={scope:'Dev static source and real API with own Resident; writes only explicit own fixture',address,principal:client.identity.principal,origin};
  page.on('pageerror',error=>errors.push(error.message));
  page.on('response',async response=>{
    if(new URL(response.url()).pathname!=='/api/instructions/execute')return;
    const request=response.request().postDataJSON();
    const receipt=await response.json(); instructions.push({instruction:request?.instruction,status:receipt.status,error:receipt.error?.message,allowed:receipt.result?.allowed});
    if(request?.instruction==='UpdateWorkspaceFile') writes.push({address:request.arguments.address,status:receipt.status,instruction_id:receipt.instruction_id});
  });
  try {
    await page.route('**/*',async route=>{
      const url=new URL(route.request().url()); if(url.origin!==origin)return route.fallback();
      if(url.pathname==='/')return route.fulfill({contentType:'text/html',body:html});
      if(url.pathname.startsWith('/static/')) {
        const file=path.resolve(portal,'.'+url.pathname);
        if(file.startsWith(portal+'/static/')&&fs.existsSync(file))return route.fulfill({path:file});
      }
      return route.fallback();
    });
    await page.setViewportSize({width:1440,height:1000}); await page.goto(origin);
    await page.waitForFunction(()=>!!window.PortalHomeDialogue);
    const identity=await page.evaluate(()=>PortalIdentity.load());
    assert.equal(identity.principal.id,client.identity.principal.id,'wait for current Resident identity before capturing preview ownership');
    await page.evaluate(()=>{
      const root=document.createElement('section'); root.style.height='100%'; document.querySelector('#homeWorkbenchRoot').append(root);
      window.host=PortalHomeDialogue.connect({root,context:{currentUserReady:Promise.reject(new Error('当前Resident验证；不借个人门面会话'))}});
    });
    await page.evaluate(address=>PortalPreview.request({resourceAddress:address}),address);
    await page.getByRole('button',{name:'编辑',exact:true}).waitFor();
    await homeAction(page,'展开');
    await page.getByRole('button',{name:'编辑',exact:true}).click();
    const before=await page.getByLabel('文件草稿').inputValue();
    const saved=before+`\n本身份资源地址保存回读：${new Date().toISOString()}\n`;
    await page.getByLabel('文件草稿').fill(saved); await page.getByRole('button',{name:'保存',exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('.homeDialogueEntryNotice')?.textContent==='');
    await page.waitForFunction(()=>{const save=[...document.querySelectorAll('button')].find(b=>b.textContent==='保存');return save?.disabled&&!document.querySelector('.homeDialogueEditor')?.disabled;});
    const readback=await page.evaluate(async address=>{
      const response=await PortalAPI.raw('/api/resources/content?address='+encodeURIComponent(address),{cache:'no-store'});
      if(!response.ok)throw new Error('readback '+response.status);return response.text();
    },address);
    assert.equal(readback,saved); evidence.savedAndReadback=true; evidence.bytes=new TextEncoder().encode(saved).length;
    await page.screenshot({path:path.join(out,'resource-file-edit-saved.png')});
    await page.getByRole('button',{name:'返回预览',exact:true}).click();
    for(const width of [1440,390]) {
      await page.setViewportSize({width,height:1000});
      if(width===390)await page.locator('.homeDialogueTabs').getByRole('button',{name:'展示',exact:true}).click();
      await page.locator('.homeDialogueOpener > summary').click(); await page.locator('.homeDialogueMore > summary').click();
      assert.equal(await page.locator('.homeDialogueOpener').evaluate(el=>el.open),false);
      assert.equal(await page.locator('.homeDialogueMore').evaluate(el=>el.open),true);
      await page.screenshot({path:path.join(out,`menus-${width}.png`)});
      await page.locator('.homeDialogueMore > summary').press('Escape');
    }
    assert.deepEqual(errors,[]); assert.equal(writes.length,1); assert.equal(writes[0].address,address); assert.equal(writes[0].status,'committed');
    Object.assign(evidence,{errors,writes,menus:[1440,390],passed:true});
    console.log(JSON.stringify(evidence));
  } catch(error) {
    evidence.error=error.message; evidence.status=await page.locator('[role="status"]').allTextContents();
    console.error(JSON.stringify({status:evidence.status,instructions,errors})); throw error;
  }
  finally {evidence.instructions=instructions;fs.writeFileSync(path.join(out,'result.json'),JSON.stringify(evidence,null,2));await client.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
