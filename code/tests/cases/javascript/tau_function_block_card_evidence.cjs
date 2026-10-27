'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const portal=path.resolve(__dirname,'../../../management_portal');
const {chromium}=require(path.join(portal,'test_framework/tau_runtime/node_modules/playwright'));
const L='lineage_111111111111',B='fbi_222222222222',TITLE='Local Problems Block';
const report={scope:'Native graph and HATG graphNodeContent with templates/i18n/CSS. Graph/header/template lookup and failure list are local fixtures; no business requests.',formal_acceptance:false};
(async()=>{
  const browser=await chromium.launch({headless:true});
  try {
    const page=await browser.newPage({viewport:{width:1200,height:900}}),errors=[],requests=[];
    page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>requests.push(r.url()));
    await page.setContent(`<!doctype html><section id="panel" class="portalHatgPanelComponent" role="region"><div id="activeLineageIdentity"><code data-portal-resource-id="${L}">${L}</code></div><div id="hatgCanvas" class="hatgCanvas" style="position:relative;width:1000px;height:680px"><div id="world" class="hatgWorld"><svg id="edges"></svg><svg id="effects"></svg><div id="labels"></div><div id="nodes"></div></div></div></section>`);
    for(const n of ['portal_design_system','portal_themes','hatg_panel','lineage_function_blocks'])await page.addStyleTag({content:fs.readFileSync(path.join(portal,'static',n+'.css'),'utf8')});
    for(const n of ['portal_text','portal_state','portal_translations','portal_i18n','portal_ui_primitives','projection_libraries/portal-graph/1.0.0/geometry','vendor/d3/d3.v7.9.0.min','lineage_program','lineage_templates','lineage_graph','hatg_graph_view'])await page.addScriptTag({content:fs.readFileSync(path.join(portal,'static',n+'.js'),'utf8')});
    await page.evaluate(({B,TITLE})=>{
      const module=PortalHatgGraphView;
      const deps=Object.fromEntries(module.dependencies.map(n=>[n,()=>{throw new Error('Unexpected dependency '+n);} ]));
      Object.assign(deps,{esc:PortalUIPrimitives.escapeHtml,isFunctionBlockInstance:n=>n.kind==='function_block',
        functionBlockInstanceUnacknowledgedFailures:()=>[],currentTemplateController:()=>null,functionBlockSourceTitle:()=> 'Local template'});
      const view=module.createGraphView(deps);
      window.localBlocks=[{id:B,kind:'function_block',primitive_kind:'function_block',title:TITLE,x:50,y:40,elements:[]}];
      window.graph=PortalHatgGraph.createGraph({root:'#hatgCanvas',world:'#world',edgeLayer:'#edges',effectLayer:'#effects',labelLayer:'#labels',nodeLayer:'#nodes',nodeContent:view.graphNodeContent});
      window.renderLocal=()=>graph.render({nodes:localBlocks,edges:[]});renderLocal();graph.fit({duration:0});
    },{B,TITLE});
    const root=page.locator('#panel'),card=root.locator(`button.hatgNode[data-node-id="${B}"]`);
    await card.waitFor({state:'visible',timeout:1000});
    report.native={name:await card.getAttribute('aria-label'),text:await card.textContent(),snapshot:await card.ariaSnapshot()};
    assert.equal(report.native.name,TITLE);assert.equal(report.native.text.includes(TITLE),false);
    assert.equal(await root.getByRole('button',{name:TITLE,exact:true}).count(),1);
    if(process.argv.includes('--probe')){
      let failed;try{await root.getByText(TITLE,{exact:true}).waitFor({state:'visible',timeout:150});}catch(e){failed={name:e.name,message:e.message};}
      assert.equal(failed?.name,'TimeoutError');report.original_text_wait=failed;
    }else{
      const {readFunctionBlockCardReady}=require(path.join(portal,'test_framework/tau_function_block_card_evidence.js'));
      const opts={lineageId:L,blockId:B,title:TITLE},read=(more={})=>readFunctionBlockCardReady(root,{...opts,...more});
      report.ready=await read();assert.equal(report.ready.ready,true,JSON.stringify(report.ready));
      await page.evaluate(()=>{localBlocks.push({...localBlocks[0],id:'fbi_333333333333',x:80});renderLocal();});
      assert.equal(await root.getByRole('button',{name:TITLE,exact:true}).count(),2);
      assert.equal((await read()).ready,true);
      assert.equal((await read({blockId:'fbi_444444444444'})).ready,false);
      assert.equal((await read({lineageId:'lineage_555555555555'})).ready,false);
      await card.evaluate(n=>n.removeAttribute('aria-label'));
      const missing=await read();assert.equal(missing.ready,false);assert.equal(missing.candidates[0].cards[0].name,null);
      await page.evaluate(()=>renderLocal());
      await card.evaluate(n=>n.setAttribute('aria-label','Another block'));
      await root.evaluate((n,title)=>{const fake=document.createElement('span');fake.id='fake-title';fake.textContent=title;n.append(fake);},TITLE);
      assert.equal((await read()).ready,false,'Unrelated body text cannot supply the target card name');
      await page.locator('#fake-title').evaluate(n=>n.remove());
      await page.evaluate(()=>renderLocal());
      await card.evaluate(n=>{n.disabled=true;});assert.equal((await read()).ready,false);
      await card.evaluate(n=>{n.disabled=false;n.hidden=true;});assert.equal((await read()).ready,false);
      await card.evaluate(n=>{n.hidden=false;n.setAttribute('aria-labelledby','unrelated');});assert.equal((await read()).ready,false);
      await card.evaluate(n=>{n.removeAttribute('aria-labelledby');n.setAttribute('data-function-block-family','true');});assert.equal((await read()).ready,false);
      await page.evaluate(()=>renderLocal());
      await card.evaluate(n=>n.after(n.cloneNode(true)));const duplicate=await read();assert.equal(duplicate.ready,false);assert.equal(duplicate.candidates[0].cards.length,2);
      await card.last().evaluate(n=>n.remove());
      await root.evaluate(n=>{const clone=n.cloneNode(true);clone.id='other';n.after(clone);});
      const roots=await readFunctionBlockCardReady(page.locator('.portalHatgPanelComponent'),opts);assert.equal(roots.ready,false);assert.equal(roots.candidates.length,2);
      await page.locator('#other').evaluate(n=>n.remove());
      const handle=await root.elementHandle();
      const failed=await readFunctionBlockCardReady({elementHandles:async()=>[handle,{evaluate:async()=>{throw new Error('local card observation lost');},dispose:async()=>{}}]},opts);
      assert.equal(failed.ready,false);assert.equal(failed.candidates.length,1);assert.equal(failed.error.phase,'sample');assert.equal(failed.error.name,'Error');assert.ok(failed.error.stack.includes('local card observation lost'));
      report.error=failed.error;report.status='passed';
    }
    assert.deepEqual(errors,[]);assert.deepEqual(requests,[]);
    if(process.env.TAU_BLOCK_CARD_REPORT)fs.writeFileSync(process.env.TAU_BLOCK_CARD_REPORT,JSON.stringify(report,null,2)+'\n');
    console.log('Native collapsed Function Block has its title as accessible name, not visible title text.');
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
