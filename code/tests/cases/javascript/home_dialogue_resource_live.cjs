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
      const frame=document.createElement('div'); frame.className='portalWorkbenchPanelFrame';
      const root=document.createElement('section'); root.dataset.portalWorkbenchPanel='dialogue-display'; frame.append(root); document.querySelector('#homeWorkbenchRoot').append(frame);
      window.host=PortalHomeDialogue.connect({root,context:{currentUserReady:Promise.reject(new Error('当前Resident验证；不借个人门面会话'))}});
    });
    // Read the actual long-title resource from the feedback, without editing it.
    await page.evaluate(()=>PortalPreview.request({resourceAddress:'decision_b12782e7f490'}));
    await page.locator('.portalFileDocument:visible').waitFor();
    await page.waitForFunction(()=>document.querySelector('.homeDialogueCurrent').textContent.includes('对话'));
    await homeAction(page,'展开');
    evidence.density=[];
    async function measurePreview(label) {
      const measurement=await page.evaluate(()=>{
        const rect=selector=>document.querySelector(selector).getBoundingClientRect().toJSON();
        const bar=document.querySelector('.homeDialogueDisplayBar'), body=document.querySelector('.homeDialogueEntry:not([hidden]) .portalFileDocument');
        const edit=[...bar.querySelectorAll('button')].find(b=>b.textContent==='编辑'&&!b.hidden);
        const r=edit?.getBoundingClientRect(),hit=r&&document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
        return {viewport:{width:innerWidth,height:innerHeight},font:getComputedStyle(document.documentElement).fontSize,
          bar:rect('.homeDialogueDisplayBar'),display:rect('.homeDialogueDisplay'),panel:rect('.homeDialogueExpanded'),
          body:body.getBoundingClientRect().toJSON(),title:document.querySelector('.homeDialogueCurrent').textContent,
          visibleSelects:[...bar.querySelectorAll('select')].filter(e=>e.checkVisibility()).length,
          primaryEdit:!!edit&&edit.checkVisibility(),editHit:!!edit&&(hit===edit||edit.contains(hit)),horizontalOverflow:document.documentElement.scrollWidth>innerWidth};
      });
      assert.ok(measurement.bar.height<=49,'single compact row even with 20px root font');
      assert.ok(Math.abs(measurement.body.top-measurement.bar.bottom)<1,'no second toolbar or empty status row before content');
      assert.equal(measurement.visibleSelects,0);assert.equal(measurement.primaryEdit,true);assert.equal(measurement.editHit,true);
      assert.equal(measurement.horizontalOverflow,false);assert.ok(measurement.panel.bottom<=measurement.viewport.height);
      evidence.density.push({label,...measurement});
      await page.screenshot({path:path.join(out,`${label}-${measurement.viewport.width}.png`)});
    }
    for(const width of [1440,390]) {
      await page.setViewportSize({width,height:width===390?844:1000});
      await page.evaluate(()=>document.documentElement.style.fontSize='20px');
      if(width===390)await page.locator('.homeDialogueTabs').getByRole('button',{name:'展示',exact:true}).click();
      await measurePreview('resource-preview');
      await page.locator('.homeDialogueCurrent').click();
      assert.ok((await page.locator('.homeDialogueIdentityBody').innerText()).includes('对话驱动的 Portal 主页'));
      await page.locator('.homeDialogueCurrent').press('Escape');
      await page.getByRole('button',{name:'编辑',exact:true}).click();
      await page.getByLabel('文件草稿').waitFor({state:'visible'});
      assert.equal(await page.getByRole('button',{name:'保存',exact:true}).isEnabled(),false,'unchanged actual Design is never saved');
      await homeAction(page,'返回预览');
      await page.locator('.portalFileDocument:visible').waitFor();
      await page.locator('.homeDialogueMore > summary').click();
      const original=page.getByRole('link',{name:'打开原文件 ↗',exact:true});
      await original.waitFor();assert.ok(await original.getAttribute('href'));
      await page.screenshot({path:path.join(out,`resource-more-${width}.png`)});
      await page.locator('.homeDialogueMore > summary').press('Escape');
    }
    await page.evaluate(address=>PortalPreview.request({resourceAddress:address}),address);
    await page.getByRole('button',{name:'编辑',exact:true}).waitFor();
    await page.getByRole('button',{name:'编辑',exact:true}).click();
    const activeEditor=page.locator('.homeDialogueEntry:not([hidden])').getByLabel('文件草稿');
    const before=await activeEditor.inputValue();
    const saved=before+`\n本身份资源地址保存回读：${new Date().toISOString()}\n`;
    await activeEditor.fill(saved); await page.getByRole('button',{name:'保存',exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('.homeDialogueEntryNotice')?.textContent==='');
    await page.waitForFunction(()=>{const save=[...document.querySelectorAll('button')].find(b=>b.textContent==='保存'&&b.checkVisibility());return save?.disabled&&!document.querySelector('.homeDialogueEntry:not([hidden]) .portalFileEditor')?.disabled;});
    const readback=await page.evaluate(async address=>{
      const response=await PortalAPI.raw('/api/resources/content?address='+encodeURIComponent(address),{cache:'no-store'});
      if(!response.ok)throw new Error('readback '+response.status);return response.text();
    },address);
    assert.equal(readback,saved); evidence.savedAndReadback=true; evidence.bytes=new TextEncoder().encode(saved).length;
    await page.screenshot({path:path.join(out,'resource-file-edit-saved.png')});
    await homeAction(page,'返回预览');
    for(const width of [1440,390]) {
      await page.setViewportSize({width,height:1000});
      if(width===390)await page.locator('.homeDialogueTabs').getByRole('button',{name:'展示',exact:true}).click();
      await page.locator('.homeDialogueOpener > summary').click(); await page.locator('.homeDialogueMore > summary').click();
      assert.equal(await page.locator('.homeDialogueOpener').evaluate(el=>el.open),false);
      assert.equal(await page.locator('.homeDialogueMore').evaluate(el=>el.open),true);
      await page.screenshot({path:path.join(out,`menus-${width}.png`)});
      await page.locator('.homeDialogueMore > summary').press('Escape');
    }
    // Host controls must not replace/reset the independently owned report view.
    await page.locator('.homeDialogueOpener > summary').click();
    await page.getByLabel('展示类型',{exact:true}).selectOption('report');
    await page.getByLabel('目标类型',{exact:true}).selectOption('lineage');
    await page.getByLabel('目标 ID 或文件资源地址').fill('lineage_8794980b1bb7');
    await page.locator('.homeDialogueChooser').getByRole('button',{name:'展示',exact:true}).click();
    const generate=page.getByRole('button',{name:'生成讲解视频',exact:true});await generate.waitFor();assert.equal(await generate.isEnabled(),true);
    await generate.evaluate(el=>window.originalReportGenerate=el);
    const search=page.locator('.home-report input');await search.fill('研究');
    const historyValue=await page.getByLabel('展示历史',{exact:true}).locator('option').last().getAttribute('value');
    await homeAction(page,'返回上一展示');await page.getByRole('button',{name:'编辑',exact:true}).waitFor();
    await page.locator('.homeDialogueMore > summary').click();await page.getByLabel('展示历史',{exact:true}).selectOption(historyValue);
    await generate.waitFor();assert.equal(await generate.evaluate(el=>el===window.originalReportGenerate),true);
    assert.equal(await search.inputValue(),'研究');evidence.reportControlsRetained=true;
    assert.deepEqual(errors,[]); assert.equal(writes.length,1); assert.equal(writes[0].address,address); assert.equal(writes[0].status,'committed');
    Object.assign(evidence,{errors,writes,menus:[1440,390],passed:true});
    console.log(JSON.stringify(evidence));
  } catch(error) {
    evidence.error=error.message; evidence.status=await page.locator('[role="status"]').allTextContents();
    console.error(JSON.stringify({status:evidence.status,instructions,errors})); throw error;
  }
  finally {evidence.instructions=instructions;fs.writeFileSync(path.join(out,'result.json'),JSON.stringify(evidence,null,2));await client.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
