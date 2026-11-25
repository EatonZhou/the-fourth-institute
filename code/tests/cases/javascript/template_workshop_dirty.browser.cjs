'use strict';
const assert = require('node:assert/strict');

module.exports = async function checkWorkshopDirtyState(page) {
  const root = page.locator('#dirtyWorkshopCase');
  const assertClean = async () => {
    assert.equal(await page.evaluate(()=>dirtyWorkshop.dirty()),false);
    assert.equal(await page.evaluate(()=>dirtyPanel.snapshot().dirty),false);
    assert.equal(await page.evaluate(()=>dirtyPanel.canClose()),true);
  };
  const waitForPreview = () => root.locator('[data-preview-status][data-state="valid"]').waitFor({state:'attached'});
  const open = async target => {
    await page.evaluate(async target=>{
      dirtyPanel?.destroy();
      const previous=document.querySelector('#dirtyWorkshopCase'); previous?.remove();
      const host=document.createElement('div'); host.id='dirtyWorkshopCase';
      host.style.cssText='position:fixed;inset:0;z-index:100;background:var(--panel)'; document.body.append(host);
      window.dirtyPanel=PortalTemplateWorkshopLoader.create({root:host,id:'dirty-case',target,
        execute:async(name,args,options)=>{
          if(window.rejectWorkshopSave && ['CreateTemplate','UpdateTemplate'].includes(name) && !args.dry_run) throw new Error('Save failed for dirty-state regression');
          return execute(name,args,options);
        },listLineages:async()=>({lineages:[]})});
      const pending=dirtyPanel.show();
      window.assertBeforeReady=dirtyPanel.snapshot().dirty;
      const mayClose=dirtyPanel.canClose();
      await pending;
      window.mayCloseOnOpening=await mayClose;
    },target || {});
    assert.equal(await page.evaluate(()=>assertBeforeReady),false);
    assert.equal(await page.evaluate(()=>mayCloseOnOpening),true);
    await waitForPreview();
    await assertClean();
  };
  await page.evaluate(()=>{
    window.dirtyPanel=null; window.rejectWorkshopSave=false;
    window.originalDirtyCaseChatOptions=window.chatOptions;
    window.realWorkshopFactory=LineageTemplateWorkshop;
    window.LineageTemplateWorkshop={...realWorkshopFactory,mount(options){window.dirtyWorkshop=realWorkshopFactory.mount(options);return dirtyWorkshop;}};
    window.unloadPrevented=()=>!window.dispatchEvent(new Event('beforeunload',{cancelable:true}));
  });
  try {
    await open();
    assert.equal(await page.evaluate(()=>unloadPrevented()),false);
    // Inspecting, fitting, zooming and panning affect only the current view.
    await root.locator('.hatgNode[data-node-id="target"]').click();
    const roleTitle=root.locator('[data-row-field="title"]');
    await roleTitle.fill('Changed role');
    assert.equal(await page.evaluate(()=>dirtyWorkshop.dirty()),true);
    await roleTitle.fill(''); await assertClean();
    await root.locator('[data-graph-fit]').click();
    const canvas=root.locator('.templateGraphCanvas');
    const box=await canvas.boundingBox();
    await page.mouse.move(box.x+box.width*.7,box.y+box.height*.6);
    await page.mouse.wheel(0,-200);
    await page.mouse.down(); await page.mouse.move(box.x+box.width*.75,box.y+box.height*.65,{steps:5}); await page.mouse.up();
    await assertClean();
    const title=root.locator('[data-meta="title"]');
    const originalTitle=await title.inputValue();
    await title.fill('Unsaved draft');
    assert.equal(await page.evaluate(()=>dirtyWorkshop.dirty()),true);
    assert.equal(await page.evaluate(()=>unloadPrevented()),true);
    const cancelled=page.evaluate(()=>dirtyPanel.canClose());
    await page.getByRole('button',{name:'取消',exact:true}).click();
    assert.equal(await cancelled,false);
    assert.equal(await title.inputValue(),'Unsaved draft');
    await title.fill(originalTitle);
    await assertClean();
    // A real node move persists body.layout, while view navigation did not.
    const node=await root.locator('.hatgNode[data-node-id="target"]').boundingBox();
    await page.mouse.move(node.x+node.width/2,node.y+node.height/2);
    await page.mouse.down(); await page.mouse.move(node.x+node.width/2+35,node.y+node.height/2+25,{steps:5}); await page.mouse.up();
    assert.equal(await page.evaluate(()=>dirtyWorkshop.dirty()),true);
    assert.ok(await page.evaluate(()=>dirtyWorkshop.template().body.layout.target));
    await title.fill('Saved dirty-state recipe');
    await page.evaluate(()=>{window.rejectWorkshopSave=true;});
    await root.locator('[data-workshop-save]').click();
    await root.locator('[data-workshop-id="workshopError"]').filter({hasText:'Save failed'}).waitFor();
    assert.equal(await page.evaluate(()=>dirtyWorkshop.dirty()),true);
    assert.equal(await page.evaluate(()=>dirtyWorkshop.template().template_id),undefined);
    await page.evaluate(()=>{window.rejectWorkshopSave=false;});
    await root.locator('[data-workshop-save]').click();
    await page.waitForFunction(()=>Boolean(dirtyWorkshop.template().template_id)&&!dirtyWorkshop.dirty());
    await assertClean();
    const firstId=await page.evaluate(()=>dirtyWorkshop.template().template_id);
    await title.fill('Saved As dirty-state recipe');
    await root.locator('[data-action="copy"]').click();
    await page.waitForFunction(id=>dirtyWorkshop.template().template_id!==id&&!dirtyWorkshop.dirty(),firstId);
    await assertClean();
    // Identical assistant content stays clean; new content retains the baseline.
    await page.evaluate(()=>dirtyWorkshop.acceptDraft(structuredClone(dirtyWorkshop.template())));
    await assertClean();
    await page.evaluate(()=>dirtyWorkshop.acceptDraft({...dirtyWorkshop.template(),title:'Assistant change'}));
    assert.equal(await page.evaluate(()=>dirtyWorkshop.dirty()),true);
    const switchBack=page.evaluate(()=>dirtyWorkshop.openTemplate(parameterRecipe));
    await page.getByRole('button',{name:'继续编辑',exact:true}).click(); await switchBack;
    assert.equal(await title.inputValue(),'Assistant change');
    await title.fill('Saved As dirty-state recipe'); await assertClean();
    await page.evaluate(()=>dirtyWorkshop.openTemplate(parameterRecipe));
    await waitForPreview(); await assertClean();
    await root.locator('.workshopInfo summary').click();
    const parameter=root.locator('[data-parameter-field="default"]');
    await parameter.fill('5'); assert.equal(await page.evaluate(()=>dirtyWorkshop.dirty()),true);
    await parameter.fill('4'); await assertClean();
    // An invalid JSON editor must protect its text even when readDraft throws.
    await root.locator('.workshopMore summary').click();
    await root.locator('[data-structure-view="json"]').click();
    await root.locator('.monaco-editor textarea').waitFor();
    const setCode = value => page.evaluate(value=>{
      const editor=monaco.editor.getEditors().find(editor=>document.querySelector('#dirtyWorkshopCase').contains(editor.getDomNode()));
      editor.setValue(value);
    },value);
    const body=await page.evaluate(()=>JSON.stringify(dirtyWorkshop.template().body,null,2));
    await setCode('{');
    await page.waitForFunction(()=>dirtyWorkshop.dirty());
    await root.locator('[data-preview-status][data-state="error"]').waitFor({state:'attached'});
    assert.equal(await page.evaluate(()=>dirtyWorkshop.dirty()),true);
    await root.locator('[data-workshop-save]').click();
    assert.equal(await page.evaluate(()=>dirtyWorkshop.dirty()),true);
    await setCode(body);
    await page.waitForFunction(()=>!dirtyWorkshop.dirty());
    await assertClean();
    const ownId=await page.evaluate(()=>dirtyWorkshop.template().template_id);
    const builtin=await page.evaluate(async()=>(await execute('ListTemplates')).templates.find(t=>t.builtin_key==='fixed_reminder').template_id);
    await open({template_id:builtin});
    assert.equal(await root.locator('[data-workshop-save]').isDisabled(),true);
    const item=await page.evaluate(async id=>(await execute('PublishTemplate',{template_id:id})).item,ownId);
    await open({store_id:item.store_id});
    assert.equal(await root.locator('[data-workshop-save]').isDisabled(),true);
  } finally {
    await page.evaluate(()=>{
      dirtyPanel?.destroy(); document.querySelector('#dirtyWorkshopCase')?.remove();
      window.LineageTemplateWorkshop=realWorkshopFactory;
      window.chatOptions=originalDirtyCaseChatOptions;
    });
  }
};
