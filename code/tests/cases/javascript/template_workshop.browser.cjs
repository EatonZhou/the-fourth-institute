'use strict';
const assert = require('node:assert/strict');
const path = require('node:path');
const repo = path.resolve(__dirname, '../../..');
const {chromium} = require(path.join(repo, 'management_portal/test_framework/tau_runtime/node_modules/playwright'));
(async () => {
  const browser = await chromium.launch({headless:true});
  const page = await browser.newPage({viewport:{width:1440,height:1000}});
  page.setDefaultTimeout(10000);
  const errors=[];
  page.on('pageerror', error => errors.push(error.message));
  try {
    await page.goto(process.env.WORKSHOP_TEST_ORIGIN);
    assert.deepEqual(errors,[], 'page scripts must load before mounting');
    await page.evaluate(async lineageId => {
      PortalI18n.apply('zh-CN');
      window.PortalIdentity={username:()=>'sample_admin'};
      window.PortalAPI={get:path=>fetch(path).then(response=>response.json())};
      window.opened=[]; window.PortalNav={openPage:url=>{opened.push(url);return Promise.resolve();}};
      window.calls=[];
      window.execute=async(instruction,args={},options={})=>{
        calls.push({instruction,args,options});
        const data=await (await fetch('/rpc',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({instruction,arguments:args,options})})).json();
        if(data.error)throw Object.assign(new Error(data.error.message),data.error);
        return data.result;
      };
      await PortalHatgLoader.load();
      window.parameterRecipe=(await execute('CreateTemplate',{template:{kind:'structure',title:'Parameterized loop',parameters:[{name:'rounds',type:'integer',default:4}],body:{roles:[],components:[{key:'rounds_loop',kind:'loop',config:{max_rounds:'{{param.rounds}}'}}],wires:[]}}})).template.template_id;
      const createPanel=PortalWorkbench.createPanel;
      window.PortalWorkbench=Object.freeze({...PortalWorkbench,createPanel(type,options){window.chatOptions=options;return createPanel(type,options);}});
      window.workshop=LineageTemplateWorkshop.mount({root:document.querySelector('#templateWorkshop'),lineageId,execute});
      await workshop.load();
      window.chat=LineageTemplateWorkshopChat.mount({root:document.querySelector('#templateWorkshop'),workshop,execute});
      await chat.load();
    }, process.env.WORKSHOP_TEST_LINEAGE);
    assert.equal(await page.locator('#workshopError').isVisible(),false);
    assert.equal(await page.locator('.workshopInfo').getAttribute('open'),null);
    assert.equal(await page.locator('[data-graph-inspector]').isVisible(),false);
    const layout=await page.evaluate(()=>({canvas:document.querySelector('#workshopCanvas').getBoundingClientRect().width,
      chat:document.querySelector('.workshopChatColumn').getBoundingClientRect().width,
      height:document.querySelector('.templateGraphCanvas').getBoundingClientRect().height}));
    assert.ok(layout.canvas>900 && layout.chat<=350 && layout.height>750,JSON.stringify(layout));
    await page.locator('.hatgNode[data-node-id="target"]').click();
    assert.equal(await page.locator('[data-graph-inspector]').isVisible(),true);
    assert.equal(await page.locator('.templateGraphPalette').isVisible(),false);
    await page.locator('[data-workshop-toolbox]').first().click();
    assert.equal(await page.locator('[data-toolbox-group="errands"]').count(),0);
    assert.equal(await page.locator('[data-toolbox-group="organizations"] .templateTile').count(),0);
    assert.equal(await page.locator('[data-template-id]').filter({hasText:'工坊助手'}).count(),0);
    await page.locator('[data-template-id="workshop_loop"]').click();
    await page.waitForTimeout(280);
    await page.locator('.templateGraphCanvas').click({position:{x:650,y:500}});
    assert.equal(await page.evaluate(()=>workshop.template().body.components.filter(row=>row.kind==='loop').length),1);
    await page.keyboard.press('Escape');
    await page.locator('[data-workshop-toolbox]').first().click();
    await page.locator('[data-template-id="workshop_seed"]').dragTo(page.locator('.templateGraphCanvas'),{targetPosition:{x:700,y:600}});
    assert.equal(await page.evaluate(()=>workshop.template().body.components.some(row=>row.kind==='seed')),true);
    await page.locator('[data-workshop-toolbox]').first().click();
    await page.locator('[data-template-id="'+await page.evaluate(()=>parameterRecipe)+'"]').click();
    await page.waitForTimeout(280);
    await page.locator('.templateGraphCanvas').click({position:{x:850,y:700}});
    assert.equal(await page.evaluate(()=>workshop.template().parameters.find(parameter=>parameter.name==='rounds').default),4);
    // Save / Save / Save As call the real stores and keep personal toolbox ownership.
    await page.locator('[data-meta="title"]').fill('Workshop saved');
    await page.locator('[data-workshop-save]').click();
    await page.waitForFunction(()=>workshop.template().template_id);
    const first=await page.evaluate(()=>workshop.template().template_id);
    await page.locator('[data-meta="title"]').fill('Workshop updated');
    await page.locator('[data-workshop-save]').click();
    await page.waitForFunction(()=>!workshop.dirty());
    assert.equal(await page.evaluate(()=>workshop.template().template_id),first);
    await page.locator('[data-action="copy"]').click();
    await page.waitForFunction(old=>workshop.template().template_id!==old,first);
    assert.equal(await page.evaluate(()=>workshop.template().created_by),'sample_admin');
    const validations=await page.evaluate(()=>calls.filter(call=>['CreateTemplate','UpdateTemplate'].includes(call.instruction)).map(call=>call.args.dry_run===true));
    assert.ok(validations.includes(true)&&validations.includes(false));
    const resolved=await page.evaluate(()=>execute('ReadResource',{address:workshop.template().template_id}));
    assert.equal(JSON.parse(resolved.text).title,'Workshop updated');
    // Opening stays read-only; first conversation creates in the user's My Lineage.
    assert.equal(await page.evaluate(()=>calls.filter(call=>call.instruction==='ApplyTemplate').length),0);
    const assistant=await page.evaluate(()=>chat.prepare());
    assert.equal(assistant.lineage_id,process.env.WORKSHOP_PERSONAL_LINEAGE);
    assert.notEqual(assistant.lineage_id,process.env.WORKSHOP_TEST_LINEAGE);
    const graph=await page.evaluate(lineage_id=>execute('GetLineage',{lineage_id}),assistant.lineage_id);
    const builtin=await page.evaluate(async()=> (await execute('ListTemplates')).templates.find(t=>t.builtin_key==='workshop_assistant').template_id);
    assert.equal(await page.evaluate(({lineage,builtin})=>LineageTemplateWorkshopChat.findAssistant(lineage,builtin),{lineage:graph.lineage,builtin}),assistant.resident_id);
    assert.ok(graph.lineage.function_blocks.some(block=>block.duty?.holder_id===assistant.resident_id&&block.duty?.category==='fixed'));
    await page.evaluate(()=>chat.prepare());
    assert.equal(await page.evaluate(()=>calls.filter(call=>call.instruction==='ApplyTemplate'&&!call.args.dry_run).length),1);
    assert.equal(await page.evaluate(()=>calls.filter(call=>call.instruction==='CreateDuty').length),1);
    assert.equal(await page.evaluate(()=>calls.filter(call=>call.instruction==='UpdateDuty').length),0);
    assert.equal(await page.evaluate(()=>chat.context().resources[0].address),await page.evaluate(()=>workshop.template().template_id));
    await page.evaluate(()=>chatOptions.submissionContext());
    // Read a real workspace file, and preserve local changes when the user chooses so.
    await page.locator('[data-meta="title"]').fill('My local work');
    await page.evaluate(async()=>{
      const payload={...workshop.template(),title:'Assistant candidate'};
      await execute('CreateTemplate',{template:LineageTemplateWorkshopDraft.payload(payload),dry_run:true});
      await execute('UpdateWorkspaceFile',{address:chat.context().context_text.split('工作区文件地址：')[1],content:JSON.stringify(payload)});
    });
    const decline=page.evaluate(()=>chat.readDraft()); decline.catch(()=>{});
    await page.getByRole('button',{name:'保留我的版本',exact:true}).click();
    await decline;
    assert.equal(await page.locator('[data-meta="title"]').inputValue(),'My local work');
    await page.evaluate(async()=>{
      const address=chat.context().context_text.split('工作区文件地址：')[1], previous=await execute('ReadResource',{address});
      await execute('UpdateWorkspaceFile',{address,content:JSON.stringify({...workshop.template(),title:'Assistant replacement'})},{expectedVersion:previous.version});
    });
    const accept=page.evaluate(()=>chatOptions.onDetail({snapshot:{input_states:[{message_id:'assistant-turn',state:'completed'}]}})); accept.catch(()=>{});
    await page.getByRole('button',{name:'使用助手版本',exact:true}).click();
    await accept;
    await page.waitForFunction(()=>document.querySelector('[data-meta="title"]').value==='Assistant replacement');
    assert.equal(await page.locator('[data-meta="title"]').inputValue(),'Assistant replacement');
    assert.equal(await page.evaluate(()=>workshop.dirty()),true);
    assert.ok(await page.evaluate(()=>document.querySelector('#workshopUnsaved').compareDocumentPosition(document.querySelector('#workshopChat .composer')) & Node.DOCUMENT_POSITION_FOLLOWING));
    assert.deepEqual(await page.evaluate(()=>opened),[]);
    // Non-structure modules keep their own editor and accept assistant drafts too.
    await page.evaluate(async()=>{
      chat.destroy();workshop.destroy();
      const prompt=(await execute('CreateTemplate',{template:{kind:'prompt',title:'Prompt recipe',body:{message:'Original'}}})).template;
      window.workshop=LineageTemplateWorkshop.mount({root:document.querySelector('#templateWorkshop'),lineageId:prompt.lineage_id||' ',templateId:prompt.template_id,execute});
      await workshop.load();
      await workshop.acceptDraft({...prompt,title:'Prompt draft',body:{message:'From assistant'}});
    });
    assert.equal(await page.locator('[data-workshop-toolbox]').first().isVisible(),false);
    assert.equal(await page.locator('[data-body-field="message"]').inputValue(),'From assistant');
    assert.equal(await page.evaluate(()=>workshop.dirty()),true);
    for(const kind of ['script','explore','familize']) {
      await page.evaluate(async kind=>{
        workshop.destroy();
        const prototype=(await execute('ListTemplates')).templates.find(template=>template.kind===kind);
        const payload=prototype?LineageTemplateWorkshopDraft.payload(prototype):{kind:'script',title:'Script recipe',body:{script:'ListTemplates',endpoints:[]}};
        const source=(await execute('CreateTemplate',{template:{...payload,title:'Module '+kind}})).template;
        window.workshop=LineageTemplateWorkshop.mount({root:document.querySelector('#templateWorkshop'),lineageId:'',templateId:source.template_id,execute});
        await workshop.load();
        await workshop.acceptDraft({...source,title:'Assistant '+kind});
      },kind);
      assert.equal(await page.locator('[data-workshop-toolbox]').first().isVisible(),false);
      assert.equal(await page.locator('[data-meta="title"]').inputValue(),'Assistant '+kind);
      if(kind==='script') await page.waitForSelector('.monaco-editor');
    }
    const builtinOriginal=await page.evaluate(async()=>{
      workshop.destroy();
      const template=(await execute('ListTemplates')).templates.find(item=>item.builtin_key==='fixed_reminder');
      window.workshop=LineageTemplateWorkshop.mount({root:document.querySelector('#templateWorkshop'),lineageId:'',templateId:template.template_id,execute});
      await workshop.load();return {id:template.template_id,title:template.title};
    });
    assert.equal(await page.locator('[data-workshop-save]').isDisabled(),true);
    assert.equal(await page.locator('[data-target]').count(),0);
    assert.equal(await page.locator('[data-meta="category"]').count(),0);
    await page.locator('[data-meta="title"]').fill('My builtin copy');
    await page.locator('[data-action="copy"]').click();
    await page.waitForFunction(()=>workshop.template().created_by==='sample_admin');
    assert.equal(await page.locator('[data-workshop-save]').isDisabled(),false);
    assert.equal((await page.evaluate(id=>execute('GetTemplate',{template_id:id}),builtinOriginal.id)).template.title,builtinOriginal.title);
    // The real lineage listing captured before My Lineage exists disables Chat
    // and prepare cannot create in the source graph as a fallback.
    await page.evaluate(async()=>{
      window.noPersonalBefore=calls.filter(call=>['ApplyTemplate','CreateDuty'].includes(call.instruction)).length;
      window.chat=LineageTemplateWorkshopChat.mount({root:document.querySelector('#templateWorkshop'),workshop,execute,
        listLineages:()=>fetch('/without-personal').then(response=>response.json())});
      await chat.load();
    });
    assert.ok(await page.locator('#workshopChat').innerText().then(text=>text.includes('当前用户没有 My Lineage')));
    assert.equal(await page.evaluate(async()=>(await chatOptions.resolveResident()).chat_workspace.composer.can_send),false);
    await assert.rejects(page.evaluate(()=>chat.prepare()),/当前用户没有 My Lineage/);
    assert.equal(await page.evaluate(()=>calls.filter(call=>['ApplyTemplate','CreateDuty'].includes(call.instruction)).length),await page.evaluate(()=>noPersonalBefore));
    // Real package and Workbench lifecycle, with the production Lineage editor
    // descriptor: split, tab, focus, close guard and persisted binding restore.
    await page.evaluate(async templateId=>{
      chat.destroy();workshop.destroy();
      document.body.innerHTML='<main id="cardWorkbench" style="height:100vh"></main>';
      PortalIdentity.peek=()=>({});
      window.PortalInstructionClient={execute};
      const deps=Object.fromEntries(PortalLineageWorkbenchEditors.dependencies.map(key=>[key,()=>{}]));
      const editors=PortalLineageWorkbenchEditors.createEditors({...deps,state:{},lineageSidebarRoots:new Map()});
      PortalWorkbench.registerPanelPackage({type:'workshop-source-fixture',version:1,
        presentation:{title:'HATG source',icon:'network'},sizing:{minWidth:200,minHeight:100},instances:'single',
        capabilities:[],behavior:{canHide:true,canCollapse:true,canMove:true,closable:false},context:{channels:[]},persistence:{schemaVersion:1},
        create:({root})=>{root.textContent='HATG source fixture';return root;}});
      const store={value:null,read(){return this.value||{};},replace(value){this.value=structuredClone(value);}};
      window.makeCards=()=>PortalWorkbench.create({id:'workshop-cards',root:'#cardWorkbench',store,
        initialEditors:[{id:'source',type:'workshop-source-fixture'}],defaultLayout:PortalWorkbench.model.stack(['source']),
        editorTypes:editors.lineageEditorTypes,createEditorDescriptor:editors.lineageEditorDescriptor,
        restorePanel:spec=>editors.lineageEditorDescriptor({instanceId:spec.id,type:spec.type,resource:spec.binding}),
        onPanelEvent:({event,sourcePanelId,detail},controller)=>{
          if(['workshop-saved','workshop-title'].includes(event))controller.setPanelTitle(sourcePanelId,detail.title);
          if(event==='workshop-deleted')void controller.closePanel(sourcePanelId);
        }});
      window.cards=makeCards();
      await PortalTemplateWorkshopLoader.openInWorkbench(cards,{template_id:templateId},'source');
      await cards.getPanelsByType('workshop')[0].ready;
    },first);
    assert.equal(await page.evaluate(()=>cards.getLayout().type),'split');
    const active=page.locator('.workshopPanelHost:visible');
    await active.locator('[data-meta="title"]').fill('Card persisted title');
    await active.locator('[data-workshop-save]').click();
    await page.waitForFunction(()=>cards.getPanelsByType('workshop')[0].target().title==='Card persisted title');
    await page.evaluate(async templateId=>{
      await PortalTemplateWorkshopLoader.openInWorkbench(cards,{template_id:templateId},'source');
      await cards.getPanelsByType('workshop')[1].ready;
    },builtinOriginal.id);
    assert.equal(await page.evaluate(()=>cards.getPanelsByType('workshop').length),2);
    await page.evaluate(async templateId=>PortalTemplateWorkshopLoader.openInWorkbench(cards,{template_id:templateId},'source'),first);
    assert.equal(await page.evaluate(()=>cards.getPanelsByType('workshop').length),2);
    assert.equal(await active.locator('[data-meta="title"]').inputValue(),'Card persisted title');
    await active.locator('[data-meta="title"]').fill('Unsaved card edit');
    const closing=page.evaluate(()=>cards.closePanel(cards.getPanelsByType('workshop')[0].id));closing.catch(()=>{});
    await page.getByRole('button',{name:'取消',exact:true}).click();
    assert.equal(await closing,false);
    await active.locator('[data-meta="title"]').fill('Card persisted title');
    await page.evaluate(async()=>{cards.destroy();window.cards=makeCards();await Promise.all(cards.getPanelsByType('workshop').map(panel=>panel.ready));});
    assert.deepEqual(await page.evaluate(()=>cards.getPanelsByType('workshop').map(panel=>panel.target().template_id).sort()),[first,builtinOriginal.id].sort());
    await page.evaluate(templateId=>cards.focusPanel(cards.getPanelsByType('workshop').find(panel=>panel.target().template_id===templateId).id),first);
    await active.locator('.workshopMore summary').click();
    await active.locator('[data-action="delete"]').click();
    await page.getByRole('alertdialog').getByRole('button',{name:'删除',exact:true}).click();
    await page.waitForFunction(()=>cards.getPanelsByType('workshop').length===1);
    assert.deepEqual(errors,[]);
    console.log('Workshop Chromium layout, inspector, pickup/drag, real saves, assistant discovery/duty and file draft conflicts passed.');
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
