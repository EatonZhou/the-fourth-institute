'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const codeRoot = process.env.TFI_TAU_CODE_ROOT || path.resolve(__dirname, '../../..');
const {chromium} = require(path.join(codeRoot,'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const {readSessionLineageIdentity,readSessionMessageCards,readSessionGraphSnapshot} = require(process.env.TFI_SESSION_PREREQUISITES_MODULE
  || path.join(codeRoot,'management_portal/test_framework/tau_session_prerequisites'));

(async () => {
  const browser = await chromium.launch({headless:true,args:['--no-sandbox']});
  try {
    const page = await browser.newPage({viewport:{width:1200,height:800}}); let requests = 0;
    page.on('request',()=>requests++);
    await page.setContent('<section role="region" id="chat"><span data-workspace-resident-id="resident_source">Source</span>'
      + '<article class="messageItem" data-message-key="msg_input" data-message-instance="msg_input">Original input</article>'
      + '<div data-message-key="msg_input">Anchor</div><span data-portal-message-key="msg_input">Actions</span>'
      + '<article class="messageItem" hidden data-message-key="msg_input">Hidden history</article></section>'
      + '<section role="region" id="other"><span data-workspace-resident-id="resident_child">Child</span>'
      + '<article class="messageItem" data-message-key="msg_input" data-message-instance="msg_input">Inherited</article></section>');
    const chat = page.locator('#chat'); const identity = {residentId:'resident_source',messageId:'msg_input'};
    const first = await readSessionMessageCards(chat,identity);
    assert.equal(first.visible_card_count,1); assert.equal(first.ready,true);
    assert.equal((await readSessionMessageCards(page.locator('#other'),identity)).ready,false);
    await chat.evaluate(n=>n.insertAdjacentHTML('beforeend','<article class="messageItem" data-message-key="msg_input">Duplicate</article>'));
    assert.equal((await readSessionMessageCards(chat,identity)).visible_card_count,2);
    assert.equal((await readSessionMessageCards(chat,identity)).ready,false);
    assert.equal(first.visible_card_count,1);
    await chat.evaluate(n=>n.lastElementChild.remove());
    await chat.evaluate(n=>n.hidden=true);
    assert.equal((await readSessionMessageCards(chat,identity)).ready,false);
    assert.equal((await readSessionMessageCards(page.locator('#missing'),identity)).region_present,false);
    await assert.rejects(readSessionMessageCards(chat.locator('article').first(),identity),/full original Chat/);

    // Actual native search markup, with the same BODY portal transition.
    await page.evaluate(html=>{
      const native = new DOMParser().parseFromString(html,'text/html');
      const root = document.createElement('section'); root.id='lineage-hatg-panel'; root.setAttribute('role','region');
      const input=native.querySelector('#graphNodeSearchInput'); const results=native.querySelector('#graphNodeSearchResults');
      if (!input || !results) throw new Error('Native search template missing');
      input.value='resident_source'; root.append(input); document.body.append(root);
      results.hidden=false; results.innerHTML='<div class="graphNodeSearchResultList"><button data-graph-search-node="resident_source">Source</button></div>';
      document.body.append(results);
      root.insertAdjacentHTML('beforeend','<div data-node-id="resident_source" style="position:absolute;left:100px;top:200px;width:280px;height:160px;transform-origin:top left">Source</div>');
    },fs.readFileSync(path.join(codeRoot,'management_portal/hatg-panel.html'),'utf8'));
    const graph=page.locator('#lineage-hatg-panel'), options={residentId:'resident_source'};
    // Exercise the real resource-header renderer used by renderLineageHeader.
    await page.evaluate(() => {
      window.PortalUIPrimitives={elementOf:x=>x,escapeHtml:x=>String(x)};
      window.PortalI18n={t:x=>x,html:(parts,...values)=>parts.reduce((s,p,i)=>s+p+(values[i]??''),'')};
      document.getElementById('lineage-hatg-panel').insertAdjacentHTML('afterbegin',
        '<div id="activeLineageIdentity"></div><button id="addNodeBtn">New</button>');
    });
    await page.addScriptTag({content:fs.readFileSync(path.join(codeRoot,'management_portal/static/portal_resource_header.js'),'utf8')});
    await page.evaluate(()=>window.PortalResourceHeader.mount(document.getElementById('activeLineageIdentity'),
      {title:'Fixed lineage',type:'lineage',resource_id:'lineage_fixed'},{idId:'activeLineageSubtitle'}));
    const fixed={lineageId:'lineage_fixed'};
    assert.equal((await readSessionLineageIdentity(graph,fixed)).identity_ready,true);
    assert.equal((await readSessionLineageIdentity(graph,{lineageId:'lineage_other'})).identity_ready,false);
    await graph.locator('#addNodeBtn').evaluate(n=>n.disabled=true);
    assert.equal((await readSessionLineageIdentity(graph,fixed)).identity_ready,false);
    await graph.locator('#addNodeBtn').evaluate(n=>n.disabled=false);
    const original=await readSessionGraphSnapshot(graph,options);
    assert.equal(original.search_ready,true); assert.equal(original.portalled_to_body,true); assert.equal(original.capture_candidate,true);
    const list=page.locator('#graphNodeSearchResults > .graphNodeSearchResultList');
    await list.evaluate(n=>n.insertAdjacentHTML('beforeend','<div class="graphSearchEmpty">Searching</div>'));
    assert.equal((await readSessionGraphSnapshot(graph,options)).search_ready,false);
    await list.locator('.graphSearchEmpty').evaluate(n=>n.remove());
    await graph.locator('[data-node-id]').evaluate(n=>n.style.transform='scale(0.02)');
    const tiny=await readSessionGraphSnapshot(graph,options);
    assert.equal(tiny.search_ready,true); assert.equal(tiny.capture_candidate,false);
    assert.ok(tiny.geometry.width < 6 && tiny.geometry.height < 4);
    assert.deepEqual(tiny.layout_position,original.layout_position);
    await graph.locator('[data-node-id]').evaluate(n=>n.style.transform='');
    await page.evaluate(()=>document.body.append(document.getElementById('graphNodeSearchResults').cloneNode(true)));
    await assert.rejects(readSessionGraphSnapshot(graph,options),/Ambiguous original search popover/);
    await page.locator('[id="graphNodeSearchResults"]').last().evaluate(n=>n.remove());
    await graph.locator('input').evaluate(n=>n.setAttribute('aria-controls','not-the-original'));
    assert.equal((await readSessionGraphSnapshot(graph,options)).search_ready,false);
    await graph.locator('input').evaluate(n=>n.setAttribute('aria-controls','graphNodeSearchResults'));
    await graph.evaluate(n=>n.hidden=true);
    assert.equal((await readSessionGraphSnapshot(graph,options)).search_ready,false);
    await graph.evaluate(n=>n.remove());
    assert.equal((await readSessionGraphSnapshot(graph,options)).panel_present,false);
    assert.equal((await readSessionLineageIdentity(graph,fixed)).panel_present,false);
    assert.equal(original.search_ready,true); assert.equal(requests,0);
    console.log(JSON.stringify({status:'passed',browser:'Chromium',scopes:['native Lineage resource header and enabled creation','physical message card identity','Resident isolation','hidden and duplicate rejection','native BODY search binding','asynchronous search status','tiny node capture rejection','ambiguous popover rejection','panel removal','immutable snapshots'],production_calls:0,business_acceptance:false}));
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
