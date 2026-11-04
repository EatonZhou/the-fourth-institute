const {chromium}=require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const {readFileSync,mkdirSync}=require('node:fs');
const path=require('node:path');
const http=require('node:http');
const assert=require('node:assert/strict');
const root=path.resolve(__dirname,'../../../management_portal');
const out=process.env.FRONTIER_EVIDENCE || '/tmp/frontier-evidence';mkdirSync(out,{recursive:true});
const lineage='lineage_0123456789ab',owner='resident_0123456789ab';
const seeds=[
  {id:'one',title:'让研究判断随着工作积累',line:'长期研究能力',attention:'optional',progress:'已经区分技术、方法和认识框架三个贡献层次。',reason:'保留选择与放弃方向的理由，让下一次判断有所依据。',next:'选择一项实际研究，记录框架如何改变了方法选择。',sources:['thinking_ecb9709daa08'],owner},
  {id:'two',title:'什么证据会改变我们的判断？',line:'长期研究能力',attention:'explore',progress:'长期偏好能够带来连续性，也可能放大盲点。',reason:'反例可以让研究品味持续修订，而不是不断自我确认。',next:'为一个已有判断寻找有理由的不同解释。',sources:['thinking_ecb9709daa08'],owner,parent:'one',relation:'builds_on'},
  {id:'three',title:'把值得介入的地方呈现出来',line:'人和 Agent 的协作',attention:'optional',progress:'将介入要求和实际执行状态分开表达。',reason:'人可以选择关注位置，自主推进的工作保持原有节奏。',next:'用同一批真实工作对比阅读与接续过程。',sources:['plan_9c8df049cf09'],owner}
];
let state={schema:'tfi.exploration-frontier.v1',items:seeds}, denied=false, conflict=false, delivered=0, input='running';
const server=http.createServer(async(req,res)=>{
  let name=new URL(req.url,'http://localhost').pathname;
  if(name==='/fixture') {res.writeHead(200,{'Content-Type':'text/html'});return res.end(`<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>body{margin:0}</style><script src="/static/portal_state.js"></script><script src="/static/portal_api.js"></script><script src="/static/portal_instruction_client.js"></script><script src="/static/portal_page_state.js"></script><div id="surface" style="height:100vh"></div><script type="module">import {mountFrontier} from '/static/frontier.js';window.frontier=mountFrontier(document.querySelector('#surface'),{lineage:'${lineage}'});</script>`);}

  if(name==='/map-fixture') {res.writeHead(200,{'Content-Type':'text/html'});return res.end(`<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>body{margin:0;--border:#dde4df;--accent:#286050;--muted:#718078;--panel:#fff;--text:#253e36;--warn:#a57b30;--ok:#25835a;font:14px system-ui}button{font:inherit}</style><link rel="stylesheet" href="/static/portal_components.css"><link rel="stylesheet" href="/static/portal_design_system.css"><link rel="stylesheet" href="/static/project.css"><link rel="stylesheet" href="/static/project_map.css">${['portal_state','portal_text','portal_translations','portal_i18n','portal_api','portal_instruction_client','portal_page_state','portal_ui_primitives','portal_ui','project_common','project_map_layout','project_map_drawer','project_map'].map(n=>'<script src="/static/'+n+'.js"></script>').join('')}<main id="map"></main><script>window.map=PortalProjectMap.create({root:document.querySelector('#map')});map.load('example');</script>`);}
  if(name==='/api/instructions/execute'){
    let text='';for await(const chunk of req)text+=chunk;const body=JSON.parse(text),a=body.arguments;let result={};
    if(body.instruction==='GetLineage')result={lineage:{title:'研究协作 · 实际内容预览',nodes:[{id:owner,title:'研究伙伴'}],project_id:'example'}};
    if(body.instruction==='GetSharedState')result={value:a.lineage_id===lineage ? state : {schema:'tfi.exploration-frontier.v1',items:[{...seeds[0],title:'Project team seed'}]}};
    if(body.instruction==='GetProject')result={project:{resource_id:'example'},team:{project_lineage_id:'lineage_abcdefabcdef',lineages:[{lineage_id:lineage,title:'Research team'}]}};
    if(body.instruction==='GetResearchMap')result={project_id:'example',revision:1,goal:'Explore with context',relations:[],notes:[],outputs:{},layout:{},directions:[{direction_id:'direction_aaaaaaaaaaaa',title:'Research judgement',stance:'focus',teams:[lineage]},{direction_id:'direction_bbbbbbbbbbbb',title:'Unassigned question',stance:'explore',teams:[]}]};
    if(body.instruction==='ResolveResource'){
      if(denied){res.writeHead(403,{'Content-Type':'application/json'});return res.end(JSON.stringify({error:{message:'来源不可读'}}));}
      result={resource:{title:a.address.startsWith('thinking')?'科研选题的思想框架与研究品味':'地图视图：持续呈现探索前沿',version:{number:1}}};
    }
    if(body.instruction==='UpdateSharedState'){
      const matched=!conflict && JSON.stringify(a.expected_value)===JSON.stringify(state);
      if(matched)state=a.value;result={matched,value:state};
    }
    if(body.instruction==='DeliverMessage'){delivered++;result={accepted:true,message_id:a.message.message_id};}
    if(body.instruction==='GetInputState')result={input_state:{state:input,reply_message_ids:input==='completed'?['msg_result']:[]}};
    res.writeHead(200,{'Content-Type':'application/json'});return res.end(JSON.stringify({status:'committed',result}));
  }
  if(name.startsWith('/api/')){res.writeHead(200,{'Content-Type':'application/json'});return res.end(JSON.stringify(name==='/api/me'?{username:'fixture',principal:{kind:'user',id:'fixture'}}:{}));}
  try{const file=name==='/frontier'?path.join(root,'frontier.html'):path.join(root,name);res.writeHead(200,{'Content-Type':name.endsWith('.css')?'text/css':name.endsWith('.js')?'text/javascript':'text/html'});res.end(readFileSync(file));}catch{res.writeHead(404);res.end();}
});
(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const browser=await chromium.launch({headless:true});const page=await browser.newPage({viewport:{width:1440,height:1050}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
  try{
    await page.goto(`http://127.0.0.1:${server.address().port}/fixture?lineage=${lineage}`);await page.waitForSelector('[data-kind="seed"]');
    assert.equal(await page.locator('[data-kind="seed"]').count(),3);
    await page.screenshot({path:path.join(out,'desktop.png'),fullPage:true});
    assert.equal(await page.locator('.graphEdge.builds_on').count(),1);
    const oldZoom=await page.locator('#zoom-level').textContent();await page.getByRole('button',{name:'放大',exact:true}).click();assert.notEqual(await page.locator('#zoom-level').textContent(),oldZoom);
    await page.locator('#fit').click();
    const node=page.locator('[data-kind="seed"]').first();const oldPos=await node.getAttribute('transform');const bounds=await node.boundingBox();await page.mouse.move(bounds.x+60,bounds.y+30);await page.mouse.down();await page.mouse.move(bounds.x+130,bounds.y+85,{steps:8});await page.mouse.up();assert.notEqual(await node.getAttribute('transform'),oldPos);await page.locator('#undo').click();assert.equal(await node.getAttribute('transform'),oldPos);
    await page.locator('#search').fill('判断');assert.ok(await page.locator('.graphNode.is-dim').count()>0);await page.locator('#search').fill('');await page.locator('#fit').click();
    await page.getByRole('button',{name:'让研究判断随着工作积累',exact:true}).last().click();
    await page.getByRole('button',{name:'稍后关注',exact:true}).click();await page.locator('#detail [data-close]').click();assert.equal(await page.locator('[data-kind="seed"]').count(),2);
    await page.locator('[data-filter="later"]').click();assert.equal(await page.locator('[data-kind="seed"]').count(),1);
    await page.locator('[data-kind="seed"]').click();await page.getByRole('button',{name:'恢复关注'}).click();await page.locator('#detail [data-close]').click();await page.locator('[data-filter="all"]').click();
    await page.locator('[data-kind="seed"]').first().click();await page.getByRole('button',{name:'更新节点'}).click();
    await page.locator('[name="reason"]').fill('寻找可以实际反驳的方法。');conflict=true;
    await page.getByRole('button',{name:'保存节点'}).click();await page.getByText('其他人已更新这条工作线。',{exact:false}).waitFor();assert.equal(await page.locator('[name="reason"]').inputValue(),'寻找可以实际反驳的方法。');
    conflict=false;await page.getByRole('button',{name:'保存节点'}).click();await page.waitForFunction(()=>!document.querySelector('#surface').shadowRoot.querySelector('#editor').open);
    await page.locator('[data-kind="seed"]').first().click();await page.getByRole('button',{name:'交给 Agent',exact:true}).click();await page.getByRole('button',{name:'确认交给 Agent',exact:true}).click();await page.waitForFunction(()=>!document.querySelector('#surface').shadowRoot.querySelector('#dispatch').open);assert.equal(delivered,1);
    await page.getByText('推进中',{exact:true}).first().waitFor();input='completed';await page.getByRole('button',{name:'刷新',exact:true}).click();await page.waitForFunction(()=>document.querySelector('#surface').shadowRoot.querySelectorAll('[data-kind="seed"]').length===2);await page.locator('[data-filter="history"]').click();await page.locator('.graphNode .nodeSub').filter({hasText:'已有回复'}).waitFor();
    denied=true;await page.getByRole('button',{name:'刷新',exact:true}).click();await page.getByText('内容已隐藏',{exact:false}).waitFor();assert.equal(await page.locator('[data-kind="seed"]').count(),0);
    denied=false;await page.getByRole('button',{name:'刷新',exact:true}).click();await page.waitForSelector('[data-kind="seed"]');await page.locator('[data-filter="all"]').click();
    await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(out,'mobile.png'),fullPage:true});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));assert.deepEqual(errors,[]);
    // Native Research Map: direction context, project aggregate, duplicate local IDs,
    // and live map rerenders preserve the open reading surface.
    state={schema:'tfi.exploration-frontier.v1',items:seeds};
    await page.setViewportSize({width:1400,height:950});
    await page.goto(`http://127.0.0.1:${server.address().port}/map-fixture`);
    await page.locator('[data-map-frontier]').click();
    await page.locator('.mapFrontier [data-kind="seed"]').first().waitFor();
    assert.equal(await page.locator('.mapFrontier [data-kind="seed"]').count(),4);
    assert.equal(await page.locator('.mapFrontier [data-kind="direction"]').count(),2);
    assert.ok(await page.locator('.mapFrontier .graphEdge.team').count()>0);
    await page.getByRole('button',{name:'Project team seed',exact:true}).click();
    assert.match(await page.locator('.mapFrontier #detail a').last().getAttribute('href'),/lineage_abcdefabcdef/);
    await page.evaluate(()=>map.load('example'));
    assert.ok(await page.locator('.mapFrontier #detail').isVisible());
    await page.locator('.mapFrontier #detail [data-close]').click();
    await page.screenshot({path:path.join(out,'map-desktop.png'),fullPage:true});
    await page.getByRole('button',{name:'Research judgement',exact:true}).click();
    await page.getByRole('button',{name:'管理研究方向',exact:true}).click();
    assert.ok(await page.locator('[data-map-viewport]').isVisible());
    await page.locator('[data-map-frontier]').click();
    await page.setViewportSize({width:390,height:844});
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    await page.screenshot({path:path.join(out,'map-mobile.png'),fullPage:true});
    await page.locator('[data-map-frontier]').click();
    assert.equal(await page.locator('.mapFrontier').count(),0);
    assert.ok(await page.locator('[data-map-viewport]').isVisible());
    state={schema:'tfi.exploration-frontier.v1',items:[...seeds,
      {...seeds[0],id:'four',title:'如何区分方法收益与框架收益？',parent:'two',relation:'depends_on',attention:'decision'},
      {...seeds[2],id:'five',title:'让反例成为下一轮选题的依据',parent:'two',relation:'converges'},
      {...seeds[2],id:'six',title:'哪些探索适合保持自主推进？',parent:'three',relation:'builds_on',attention:'explore'}]};
    await page.setViewportSize({width:1500,height:960});
    await page.goto(`http://127.0.0.1:${server.address().port}/fixture`);
    await page.locator('.graphEdge.converges').waitFor();
    assert.equal(await page.locator('.graphEdge.depends_on').count(),1);
    await page.locator('[data-kind="line"]').first().click();
    assert.equal(await page.locator('[data-kind="seed"]').count(),3);
    await page.locator('[data-kind="line"]').first().click();
    assert.equal(await page.locator('[data-kind="seed"]').count(),6);
    await page.locator('[data-kind="seed"]').first().click();await page.locator('#focus').click();
    assert.ok(await page.locator('.graphNode.is-dim').count()>0);
    await page.locator('#focus').click();await page.locator('#detail [data-close]').click();await page.locator('#fit').click();
    await page.screenshot({path:path.join(out,'branches.png'),fullPage:true});
    const beforePan=await page.locator('.frontierCanvas>g').getAttribute('transform');
    await page.locator('.frontierMinimap').click({position:{x:20,y:20}});
    assert.notEqual(await page.locator('.frontierCanvas>g').getAttribute('transform'),beforePan);
    state={schema:'tfi.exploration-frontier.v1',items:[]};
    await page.goto(`http://127.0.0.1:${server.address().port}/fixture`);
    await page.locator('[data-kind="new"]').waitFor();
    assert.equal(await page.locator('[data-kind="seed"]').count(),0);
    await page.screenshot({path:path.join(out,'empty.png'),fullPage:true});
    await page.getByRole('button',{name:'添加第一个地图节点',exact:true}).click();
    assert.ok(await page.locator('#editor').isVisible());
    assert.deepEqual(errors,[]);
    console.log('PASS: render, relationships, personal defer/restore, conflict preserves draft, dispatch receipt, result convergence, permission loss, mobile overflow. Fixture execution only; no live messages.');
  }finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
})().catch(e=>{console.error(e);process.exitCode=1;server.close();});
