'use strict';
const assert = require('node:assert/strict');
const path = require('node:path');
const repo = path.resolve(__dirname,'../../..');
const {chromium} = require(path.join(repo,'management_portal/test_framework/tau_runtime/node_modules/playwright'));
(async () => {
  const browser = await chromium.launch({headless:true});
  const page = await browser.newPage({viewport:{width:1440,height:960}});
  page.setDefaultTimeout(12000);
  const errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  const shot = name => page.screenshot({path:path.join(process.env.STORE_SCREENSHOTS,name+'.png')});
  try {
    await page.goto(process.env.STORE_TEST_ORIGIN);
    await page.evaluate(async lineageId => {
      PortalI18n.apply('zh-CN');
      window.actor='sample_admin';
      window.PortalIdentity={username:()=>actor};
      window.execute=async(instruction,args={},as=actor)=>{
        const response=await(await fetch('/rpc',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({instruction,arguments:args,actor:as})})).json();
        if(response.error)throw new Error(response.error.message);
        return response.result;
      };
      window.PortalInstructionClient={execute:(name,args)=>execute(name,args)};
      await PortalHatgLoader.load();
      await PortalUIPrimitives.loadAsset('/static/template_workshop.css');
      await PortalUIPrimitives.loadAsset('/static/lineage_templates.css');
      window.recipe=(await execute('CreateTemplate',{template:{kind:'structure',title:'轮次复核',description:'每轮结束后检查交付内容',body:{roles:[{key:'target',from:'target'}],components:[{key:'done',kind:'event_source',config:{event_type:'manual.triggered'}},{key:'review',kind:'effect',config:{script:'ListTemplates',endpoints:[]}}],wires:[]}}})).template;
      window.storeItem=(await execute('PublishTemplate',{template_id:recipe.template_id,release_note:'首版'})).item;
      await execute('InstallStoreTemplate',{store_id:storeItem.store_id},'store-reader');
      await execute('UpdateTemplate',{template_id:recipe.template_id,title:'轮次复核 · 新版'});
      await execute('PublishTemplate',{template_id:recipe.template_id,release_note:'增加复核节点'});
      window.opened=[];
      window.workshop=LineageTemplateWorkshop.mount({root:document.querySelector('#testWorkshop .templateWorkshop'),lineageId,templateId:recipe.template_id,execute:(name,args)=>execute(name,args),onOpenWorkshop:resource=>opened.push(resource)});
      await workshop.load();
      const host=document.querySelector('#testHatg');
      const actualId=id=>host.querySelector('#'+id);
      const fake=()=>document.createElement('div');
      const deps=Object.fromEntries(PortalHatgPanelBindings.dependencies.map(key=>[key,()=>{}]));
      const listen=(_scope,node,event,handler)=>node?.addEventListener(event,handler);
      Object.assign(deps,{$id:id=>actualId(id)||fake(),$queryAll:query=>host.querySelectorAll(query),$eventRoot:()=>host,
        state:{lineage:{lineage_id:lineageId}},panelHost:{pageStateController:{listen},hatgComponentOptions:{emit:(_name,detail)=>opened.push(detail.resource)}},hatgModule:()=>({observeNavigationHeight:()=>()=>{}}),clone:value=>structuredClone(value)});
      PortalHatgPanelBindings.createBindings(deps).bindActions();
      window.shelf=LineageTemplates.mount({shelf:actualId('templateShelf'),canvas:actualId('hatgCanvas'),getContext:()=>({lineage_id:lineageId}),request:(name,args)=>execute(name,args),resolveTarget:()=>({kind:'canvas'}),onOpenWorkshop:resource=>opened.push(resource)});
      await shelf.load();
      actualId('hatgViewport').style.height='800px';
      actualId('hatgCanvas').style.height='800px';
      document.body.style.display='flex';
    },process.env.STORE_TEST_LINEAGE);
    await page.locator('[data-workshop-publish]').click();
    await page.getByRole('button',{name:'取消',exact:true}).last().waitFor();
    await shot('workshop-publish');
    await page.getByRole('button',{name:'取消',exact:true}).last().click();
    await page.locator('#hatgToolboxToggle').hover();
    assert.equal(await page.locator('#hatgToolbox').isVisible(),true);
    await page.locator('#hatgToolboxTitle').hover();
    assert.equal(await page.locator('#hatgToolbox').isVisible(),true);
    await page.mouse.move(900,20);
    await page.waitForFunction(()=>document.querySelector('#hatgToolbox').hidden);
    // The actual launcher CSS must remain above the canvas when its legend opens.
    await page.evaluate(()=>{
      const mailbox=document.createElement('aside');
      mailbox.className='portalAgentInbox';mailbox.id='testMailbox';
      mailbox.style.cssText='left:360px;right:auto;top:300px';
      mailbox.innerHTML='<button class="portalAgentInboxTrigger">Mailbox</button>';
      document.body.append(mailbox);
      document.querySelector('#testHatg').style.zIndex='1';
    });
    await page.locator('#hatgLegendToggle').hover();
    await page.locator('#hatgLegendPopover').waitFor();
    assert.equal(await page.evaluate(()=>{
      const button=document.querySelector('#testMailbox button'),r=button.getBoundingClientRect();
      return button.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2));
    }),true,'legend must not lift the entire canvas over Mailbox');
    assert.equal(await page.evaluate(()=>document.querySelector('#hatgLegendPopover').parentElement===document.body),true);
    await page.locator('#hatgLegendPopover').hover();
    await page.mouse.move(900,20);
    await page.waitForFunction(()=>document.querySelector('#hatgLegendPopover').hidden);
    await page.evaluate(()=>document.querySelector('#testMailbox').remove());
    await page.locator('#hatgToolboxToggle').hover();
    const geometry=await page.evaluate(()=>{
      const drawer=document.querySelector('#hatgToolbox').getBoundingClientRect();
      const canvas=document.querySelector('#hatgCanvas').getBoundingClientRect();
      return {right:drawer.right,limit:canvas.right-44,left:drawer.left,width:drawer.width,overflow:document.querySelector('#hatgToolbox').scrollWidth>drawer.width+1};
    });
    assert.ok(geometry.right<=geometry.limit,JSON.stringify(geometry));
    assert.equal(geometry.overflow,false);
    await shot('hatg-narrow-toolbox');
    await page.locator('#hatgToolboxStore button').click();
    await page.locator('.templateStoreCard').first().waitFor();
    assert.equal(await page.locator('#hatgToolbox').isVisible(),false);
    await page.locator('[data-store-select]').first().click();
    await page.getByRole('heading',{name:'施加时调用的指令'}).waitFor();
    await shot('store-details');
    await page.locator('[data-store-search]').fill('unmatched');
    assert.equal(await page.locator('.templateStoreCard').count(),0);
    await page.locator('[data-store-search]').fill('轮次');
    await page.locator('[data-store-group=task]').click();
    assert.equal(await page.locator('.templateStoreCard').count(),0);
    await page.locator('[data-store-group=duty]').click();
    assert.equal(await page.locator('.templateStoreCard').count(),1);
    await page.keyboard.press('Escape');
    await page.locator('.templateStore').waitFor({state:'detached'});
    await page.evaluate(async()=>{
      actor='store-reader';
      window.local=(await execute('ListTemplates',{})).templates.find(row=>row.installed_from_store_id===storeItem.store_id);
      await execute('UpdateTemplate',{template_id:local.template_id,title:'我的复核修改'});
      await shelf.load();
    });
    await page.locator('#hatgToolboxToggle').click();
    await page.locator('[data-store-update]').waitFor();
    await shot('toolbox-update');
    await page.locator('[data-store-update]').click();
    await page.getByRole('button',{name:'另存新版副本（保留我的修改）',exact:true}).waitFor();
    await shot('local-conflict');
    await page.getByRole('button',{name:'另存新版副本（保留我的修改）',exact:true}).click();
    await page.waitForFunction(async()=>!(await execute('GetTemplate',{template_id:local.template_id})).template.store_status);
    await page.evaluate(()=>{window.PortalTemplateStore.open({request:(name,args)=>execute(name,args),onOpenWorkshop:resource=>opened.push(resource)});});
    await page.locator('[data-store-select]').click();
    await page.locator('[data-store-workshop]').click();
    assert.ok(await page.evaluate(()=>opened.at(-1).template_id));
    await page.evaluate(async()=>{
      actor='new-reader';
      window.PortalTemplateStore.open({request:(name,args)=>execute(name,args),onOpenWorkshop:resource=>opened.push(resource)});
    });
    await page.locator('[data-store-select]').click();
    await page.locator('[data-store-workshop]').click();
    assert.ok(await page.evaluate(()=>opened.at(-1).store_id));
    await page.evaluate(async lineageId=>{
      workshop.destroy();
      workshop=LineageTemplateWorkshop.mount({root:document.querySelector('#testWorkshop .templateWorkshop'),lineageId,storeId:storeItem.store_id,execute:(name,args)=>execute(name,args)});
      await workshop.load();
    },process.env.STORE_TEST_LINEAGE);
    assert.equal(await page.locator('[data-workshop-save]').isDisabled(),true);
    assert.equal(await page.locator('[data-action=copy]').isEnabled(),true);
    assert.equal(await page.locator('[data-workshop-install]').isVisible(),true);
    await page.locator('.workshopToolboxButton').hover();
    assert.equal(await page.locator('[data-workshop-id=workshopToolbox]').isVisible(),true);
    await page.mouse.move(900,20);
    await page.waitForFunction(()=>document.querySelector('[data-workshop-id=workshopToolbox]').hidden);
    // UI geometry uses short and long content; publishing/installing is covered above.
    await page.evaluate(()=>{
      const base={...storeItem,instructions_used:['CreateComponent'],application_parameters:[],is_publisher:false};
      window.geometryItems=[{...base,store_id:'short',title:'短标题',description:'短说明'},
        {...base,store_id:'long',title:'很长的模板名称'.repeat(15),description:'完整说明\n'.repeat(100)}];
      window.PortalTemplateStore.open({request:async()=>({items:geometryItems})});
    });
    await page.locator('[data-store-select=short]').waitFor();
    const dimensions=()=>page.evaluate(()=>{
      const size=node=>{const r=node.getBoundingClientRect();return [r.width,r.height];};
      return {dialog:size(document.querySelector('.templateStore')),cards:[...document.querySelectorAll('.templateStoreCard')].map(size)};
    });
    const initial=await dimensions();
    assert.deepEqual(initial.cards[0],initial.cards[1]);
    await page.locator('[data-store-select=short]').click();
    assert.deepEqual(await dimensions(),initial);
    await page.locator('[data-store-select=long]').click();
    assert.deepEqual(await dimensions(),initial);
    assert.equal(await page.locator('.templateStoreDetails').evaluate(node=>node.scrollHeight>node.clientHeight),true);
    await shot('store-fixed-size');
    await page.locator('[data-store-back]').click();
    assert.deepEqual(await dimensions(),initial);
    await page.setViewportSize({width:390,height:740});
    const narrow=(await dimensions()).dialog;
    await page.locator('[data-store-select=long]').click();
    assert.deepEqual((await dimensions()).dialog,narrow);
    assert.ok(narrow[0]<=390-32);
    await shot('store-fixed-size-mobile');
    assert.deepEqual(errors,[]);
    console.log('Chromium store interactions and screenshots verified');
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exit(1);});
