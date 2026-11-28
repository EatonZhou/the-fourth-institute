'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');

const dev = process.env.DUTY_LANES_DEV_ORIGIN;
const output = process.env.DUTY_LANES_EVIDENCE;

async function checkGeometry(root, expected, open) {
  await root.locator('[data-duty-lane]').first().waitFor();
  const actual = await root.evaluate(host => {
    const canvas = host.querySelector('.hatgCanvas');
    const frames = [...canvas.querySelectorAll('[data-duty-lane]')];
    const nodes = [...canvas.querySelectorAll('.hatgNode[data-node-id]')];
    const rect = new Map([...frames, ...nodes].map(el => [el, el.getBoundingClientRect()]));
    const intersect = (a, b) => Math.min(a.right, b.right) - Math.max(a.left, b.left) > 1 &&
      Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 1;
    const inside = (x, y, r) => x > r.left + 2 && x < r.right - 2 && y > r.top + 2 && y < r.bottom - 2;
    const owners = new Map(nodes.map(node => {
      const box = rect.get(node);
      const region = frames.find(frame => inside((box.left+box.right)/2, (box.top+box.bottom)/2, rect.get(frame)));
      return [node.dataset.nodeId, region?.dataset.dutyLane];
    }));
    const issues = [];
    const laneRight = Math.max(...frames.map(frame=>rect.get(frame).right));
    for (const [i, node] of nodes.entries()) {
      const id = node.dataset.nodeId;
      for (const other of nodes.slice(i+1)) {
        if (intersect(rect.get(node),rect.get(other))) issues.push(`overlapping nodes: ${id} / ${other.dataset.nodeId}`);
      }
      for (const frame of frames) {
        if (frame.dataset.dutyLane !== owners.get(id) && intersect(rect.get(node),rect.get(frame))) {
          issues.push(`node crosses lane: ${id} / ${frame.dataset.dutyLane}`);
        }
      }
      if (node.querySelector('[data-binding-key]') && rect.get(node).left < laneRight) issues.push(`interface inside lane band: ${id}`);
    }
    const edges = [...canvas.querySelectorAll('path.hatgEdge[d]')];
    for (const edge of edges) {
      const {sourceId,targetId,edgeId} = edge.dataset;
      const unrelated = frames.filter(frame => ![owners.get(sourceId),owners.get(targetId)].includes(frame.dataset.dutyLane));
      const matrix = edge.getScreenCTM();
      const length = edge.getTotalLength();
      for (let i=0; i<length; i+=.25) {
        const p = edge.getPointAtLength(i).matrixTransform(matrix);
        const hit = unrelated.find(frame=>inside(p.x,p.y,rect.get(frame)));
        if (hit) { issues.push(`edge crosses lane: ${edgeId} / ${hit.dataset.dutyLane}`); break; }
      }
    }
    return {
      issues,
      order:frames.map(frame=>frame.dataset.dutyLane),
      open:frames.filter(frame=>frame.firstElementChild.getAttribute('aria-expanded')==='true').map(frame=>frame.dataset.dutyLane),
      owners:Object.fromEntries(owners),
      nodes:nodes.map(node=>node.dataset.nodeId),
      edges:edges.map(edge=>edge.dataset.edgeId),
      zoom:Number(canvas.dataset.zoom),
    };
  });
  assert.deepEqual(actual.issues, []);
  assert.deepEqual(actual.order, expected);
  assert.deepEqual(actual.open, open);
  assert.ok(actual.zoom>=.6);
  return actual;
}

(async()=>{
  const browser = await chromium.launch({headless:true});
  const page = await browser.newPage({
    viewport:{width:1440,height:960},
    ...(dev ? {extraHTTPHeaders:{
      Authorization:`Bearer ${process.env.DUTY_LANES_RESIDENT_TOKEN}`,
      'X-Portal-Lineage-ID':'lineage_1e7e0f7269ae',
    }} : {}),
  });
  page.setDefaultTimeout(10000);
  const errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  try {
    await page.goto(dev ? new URL('/template-workshop',dev).href : process.env.WORKSHOP_TEST_ORIGIN);
    await page.evaluate(async ({dev,lineageId})=>{
      PortalI18n.apply('zh-CN');
      if(dev) {
        await PortalTemplateWorkshopLoader.load();
        const host=document.createElement('section');
        host.id='dutyLanesTest';
        host.style='position:fixed;inset:0;z-index:600;background:var(--panel)';
        document.body.append(host);
        const mount=LineageTemplateWorkshop.mount;
        window.LineageTemplateWorkshop={...LineageTemplateWorkshop,mount(options){return window.workshop=mount(options);}};
        window.execute=(name,args={})=>PortalInstructionClient.execute(name,args);
        window.panel=PortalTemplateWorkshopLoader.create({root:host,id:'duty-lanes-evidence'});
        await panel.show();
      } else {
        window.PortalIdentity={username:()=>'sample_admin', userId:async()=>'sample_admin',listUIAssociations:async()=>({items:[]})};
        window.PortalAPI={get:url=>fetch(url).then(response=>response.json())};
        window.execute=async (instruction,args={},options={})=>{
          const data=await (await fetch('/rpc',{method:'POST',headers:{'Content-Type':'application/json'},
            body:JSON.stringify({instruction,arguments:args,options})})).json();
          if (data.error) throw Error(data.error.message);
          return data.result;
        };
        await PortalTemplateWorkshopLoader.load();
        window.workshop=LineageTemplateWorkshop.mount({root:document.getElementById('templateWorkshop'),lineageId,execute});
        await workshop.load();
      }
    },{dev,lineageId:process.env.WORKSHOP_TEST_LINEAGE});
    const catalog=await page.evaluate(async()=>(await execute('ListTemplates')).templates);
    const root=page.locator(dev?'#dutyLanesTest':'#templateWorkshop');
    const expand=root.locator('[data-recipe-expand]');
    const checkClean=async()=>assert.equal(await page.evaluate(()=>workshop.dirty()),false);
    const report={};

    async function settle() {
      await page.mouse.move(1,1);
      await page.waitForTimeout(350);
    }
    async function capture(name, state, expected, open) {
      if(!output) return;
      fs.mkdirSync(output,{recursive:true});
      for(const theme of ['auto','dark']) for(const width of [1440,600]) {
        await page.setViewportSize({width,height:state==='all' && name!=='router'?2220:960});
        await page.evaluate(value=>PortalTheme.setMode(value),theme);
        await root.locator('[data-graph-fit]').click();
        await settle();
        report[`${name}-${state}-${theme}-${width}`]=await checkGeometry(root,expected,open);
        await root.screenshot({path:path.join(output,`${name}-${state}-${theme}-${width}.png`)});
        if(width===600) {
          const jump=root.locator('[data-graph-interfaces]');
          await jump.click();
          assert.equal(await jump.getAttribute('aria-pressed'),'true');
          await settle();
          await root.screenshot({path:path.join(output,`${name}-${state}-${theme}-${width}-interfaces.png`)});
          await jump.click();
          assert.equal(await jump.getAttribute('aria-pressed'),'false');
        }
      }
      await page.setViewportSize({width:1440,height:960});
      await page.evaluate(()=>PortalTheme.setMode('auto'));
      await root.locator('[data-graph-fit]').click();
    }

    for(const [name,builtin] of [['steward','family_role_steward'],['family','family'],['router','family_role_router']]) {
      await page.evaluate(id=>workshop.openTemplate(id),catalog.find(t=>t.builtin_key===builtin).template_id);
      await expand.waitFor();
      const source=await page.evaluate(()=>{
        const draft=workshop.template(),body=draft.editor.graph.body;
        return {
          payload:LineageTemplateWorkshopDraft.payload(draft),
          duties:LineageTemplateGraph.dutyGroups(body.presentation,[],{},draft).map(duty=>[duty.id,duty.members,duty.title]),
        };
      });
      const expected=source.duties.map(([id])=>id);
      if(name!=='router') {
        const single=JSON.stringify(['steward_resident','queue']);
        await root.locator('[data-template-resident-menu="steward_resident"]').click();
        const menu=root.locator('.templateResidentOverlay');
        const titles=await menu.locator('.residentMenuDutyText').allTextContents();
        assert.deepEqual(titles,source.duties.filter(([id])=>JSON.parse(id)[0]==='steward_resident').map(([, ,title])=>title));
        const action=menu.locator('[data-template-duty-toggle="queue"]');
        await action.locator('xpath=ancestor::details').locator('summary').click();
        await action.click();
        await settle();
        const singleGraph=await checkGeometry(root,expected,[single]);
        assert.equal(singleGraph.owners.member_queue,single);
        assert.equal(singleGraph.owners.notify_member_queue_task,single);
        assert.ok(!singleGraph.nodes.includes('recovery_check'));
        await capture(name,'single',expected,[single]);
      }
      await expand.click();
      await settle();
      const all=await checkGeometry(root,expected,expected);
      for(const [id,members] of source.duties) {
        for(const key of members) if(all.nodes.includes(key) && key!=='member_done') {
          assert.equal(all.owners[key],id,`ownership of ${key}`);
        }
      }
      if(name==='router') {
        assert.ok(all.edges.includes('interaction:facade_resident:facade_inbox'));
        assert.ok(all.edges.includes('relay'));
      }
      const positions=()=>root.locator('.hatgNode[data-node-id]').evaluateAll(nodes=>nodes.map(node=>[node.dataset.nodeId,node.style.left,node.style.top]));
      const defaults=await positions();
      if(name==='steward') {
        const node=root.locator('.hatgNode[data-node-id="member_queue"]');
        const box=await node.boundingBox();
        await page.mouse.move(box.x+box.width/2,box.y+box.height/2);
        await page.mouse.down();
        await page.mouse.move(box.x+box.width/2+30,box.y+box.height/2+15,{steps:8});
        await page.mouse.up();
        assert.notDeepEqual(await positions(),defaults);
        await checkGeometry(root,expected,expected);
      }
      await root.locator('[data-graph-arrange]').click();
      assert.deepEqual(await positions(),defaults);
      await capture(name,'all',expected,expected);
      await checkClean();
      assert.deepEqual(await page.evaluate(()=>LineageTemplateWorkshopDraft.payload(workshop.template())),source.payload);
      await expand.click();
      assert.equal(await root.locator('[data-duty-lane]').count(),0);
    }
    assert.deepEqual(errors,[]);
    if(output)fs.writeFileSync(path.join(output,'geometry.json'),JSON.stringify(report,null,2));
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
