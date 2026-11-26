'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs');
const {chromium}=require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
(async()=>{
  const browser=await chromium.launch({headless:true}),page=await browser.newPage({viewport:{width:1440,height:1000}});
  const errors=[];page.on('pageerror',error=>errors.push(error.message));page.setDefaultTimeout(15000);
  try{
    await page.goto(process.env.WORKSHOP_TEST_ORIGIN);
    await page.evaluate(async lineageId=>{
      PortalI18n.apply('zh-CN');window.PortalIdentity={username:()=>'sample_admin',userId:async()=>'sample_admin',listUIAssociations:async()=>({items:[]})};
      window.execute=async(instruction,args={},options={})=>{
        const data=await(await fetch('/rpc',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({instruction,arguments:args,options})})).json();
        if(data.error)throw new Error(data.error.message);return data.result;
      };
      await PortalTemplateWorkshopLoader.load();
      window.workshop=LineageTemplateWorkshop.mount({root:document.querySelector('#templateWorkshop'),lineageId,execute});await workshop.load();
      const catalog=await execute('ListTemplates');window.roles=catalog.templates.filter(t=>t.kind==='role');
      window.family=catalog.templates.find(t=>t.builtin_key==='family');
    },process.env.WORKSHOP_TEST_LINEAGE);
    const ids=await page.evaluate(()=>roles.map(t=>t.template_id).sort());
    assert.equal(ids.length,2);
    assert.deepEqual((await page.locator('[data-toolbox-group="roles"] [data-template-id]').evaluateAll(n=>n.map(n=>n.dataset.templateId))).sort(),ids);
    const router=await page.evaluate(()=>roles.find(t=>t.body.role_key==='router').template_id);
    await page.locator(`[data-template-id="${router}"]`).click();
    await page.waitForFunction(()=>workshop.template().kind==='role');
    assert.equal(await page.evaluate(()=>workshop.dirty()),false);
    await page.locator('[data-recipe-parameters]').click();
    assert.equal(await page.locator('[data-body-field="memories"]').count(),1);
    assert.ok(await page.locator('.hatgNode').count()>1,'actual Router trigger and Effect are visible');
    await page.locator('[data-body-field="memories"]').fill('{');
    assert.equal(await page.evaluate(()=>workshop.dirty()),true,'invalid role JSON stays protected');
    await page.locator('[data-workshop-history="undo"]').click();
    assert.equal(await page.evaluate(()=>workshop.dirty()),false);
    await page.locator('[data-recipe-parameters]').click();
    const memory=await page.locator('[data-body-field="memories"]').inputValue();
    const changed=JSON.parse(memory);changed[0].prompt+=' Browser role edit';
    await page.locator('[data-body-field="memories"]').fill(JSON.stringify(changed));
    await page.waitForFunction(()=>document.querySelector('[data-preview-status]')?.dataset.state==='valid');
    assert.equal(await page.evaluate(()=>workshop.dirty()),true);
    // Builtin roles preserve Save As, then use the ordinary shared catalog.
    assert.equal(await page.locator('[data-workshop-save]').isDisabled(),true);
    await page.locator('[data-action="copy"]').click();
    await page.waitForFunction(()=>workshop.template().created_by==='sample_admin'&&!workshop.template().builtin_key);
    assert.equal(await page.evaluate(()=>workshop.dirty()),false);
    const own=await page.evaluate(()=>workshop.template());assert.match(own.body.memories[0].prompt,/Browser role edit/);
    await page.waitForFunction(()=>document.querySelector('[data-preview-status]')?.dataset.state==='valid');
    const fid=await page.evaluate(()=>family.template_id);await page.locator(`[data-template-id="${fid}"]`).click();
    try {await page.waitForFunction(()=>workshop.template().kind==='familize');}
    catch(error){throw new Error(JSON.stringify(await page.evaluate(()=>({kind:workshop.template().kind,dirty:workshop.dirty(),error:document.querySelector('#workshopError').textContent})))+' '+errors.join(';'));}
    assert.equal(await page.locator('[data-recipe-group]').count(),2);
    const folded=await page.locator('.hatgNode').count();
    await page.locator('[data-recipe-group]').first().click();
    assert.ok(await page.locator('.hatgNode').count()>folded);
    assert.equal(await page.evaluate(()=>workshop.dirty()),false,'role expansion only changes presentation');
    await page.locator('[data-recipe-group]').first().click();
    await page.waitForFunction(count=>document.querySelectorAll('.hatgNode').length===count,folded);
    const folder='/tmp/family-role-evidence';fs.mkdirSync(folder,{recursive:true});
    for(const theme of ['auto','liquid-glass','dark'])for(const width of [1440,600]){
      await page.setViewportSize({width,height:1000});await page.evaluate(theme=>PortalTheme.setMode(theme),theme);
      await page.waitForTimeout(350);await page.locator('[data-graph-fit]').click();await page.waitForTimeout(350);
      await page.screenshot({path:`${folder}/family-folded-${theme}-${width}.png`});
    }
    const cdp=await page.context().newCDPSession(page);
    await cdp.send('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-transparency',value:'reduce'}]});
    await page.evaluate(()=>PortalTheme.setMode('liquid-glass'));
    assert.equal(await page.evaluate(()=>matchMedia('(prefers-reduced-transparency: reduce)').matches),true);
    assert.equal(await page.locator('.workshopLibrary').evaluate(node=>getComputedStyle(node).backdropFilter),'none');
    await page.screenshot({path:`${folder}/family-reduced-transparency.png`});
    await cdp.send('Emulation.setEmulatedMedia',{features:[]});
    await page.setViewportSize({width:1440,height:1000});
    await page.locator('[data-recipe-group]').first().click();await page.locator('[data-recipe-group]').last().click();
    await page.locator('[data-graph-fit]').click();await page.waitForTimeout(400);await page.screenshot({path:`${folder}/family-expanded.png`});
    const steward=await page.evaluate(()=>roles.find(t=>t.body.role_key==='steward').template_id);await page.locator(`[data-template-id="${steward}"]`).click();
    await page.waitForFunction(()=>workshop.template().body.role_key==='steward');
    await page.locator('[data-graph-fit]').click();await page.waitForTimeout(400);await page.screenshot({path:`${folder}/steward-duties.png`});
    assert.deepEqual(errors,[]);
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
