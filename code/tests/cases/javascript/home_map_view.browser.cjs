// One browser, real native surfaces; only the instruction transport is an isolated fixture.
const { chromium } = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const { readFileSync, mkdirSync, writeFileSync } = require('node:fs');
const path = require('node:path'), http = require('node:http'), assert = require('node:assert/strict');
const staticRoot = path.resolve(__dirname, '../../../management_portal/static');
const out = process.env.HOME_MAP_EVIDENCE || '/tmp/home-map-evidence'; mkdirSync(out, { recursive: true });
const lineage = 'lineage_0123456789ab', direction = 'direction_0123456789ab';
let map = { revision: 1, goal: 'Testing native map', directions: [{ direction_id: direction, title: 'Test direction', stance: 'explore', teams: [lineage] }], relations: [], layout: {}, notes: [], outputs: {} };
let state = { schema: 'tfi.exploration-frontier.v1', items: [{ id: 'seed', title: 'Test frontier', line: 'Research', sources: ['article_0123456789ab'], attention: 'explore', next: 'Read evidence', reason: 'A question', progress: 'Initial' }] };
let denied = false, sourceDenied = false, conflict = false;
const calls = [];
const scripts = ['portal_state', 'portal_text', 'portal_translations', 'portal_i18n', 'portal_api', 'portal_instruction_client', 'portal_page_state', 'portal_ui_primitives', 'portal_ui', 'project_common', 'project_map_layout', 'project_map_drawer', 'project_map', 'home_map_view'];
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost');
  if (url.pathname === '/') {
    res.setHeader('Content-Type', 'text/html');
    return res.end(`<!doctype html><meta charset="utf-8">${['portal_components','portal_design_system','project','project_map'].map(n=>`<link rel="stylesheet" href="/static/${n}.css">`).join('')}<style>body{margin:0;font:14px system-ui;--panel:white;--text:#123;--border:#ccc;--accent:teal;--muted:#567}#root{height:760px}</style>${scripts.map(n=>`<script src="/static/${n}.js"></script>`).join('')}<div id="root"></div><script>window.events=[];window.opens=[];window.view=PortalHomeMapView.create({root:document.querySelector('#root'),onSelection:x=>events.push(x),onOpen:x=>opens.push(x)});</script>`);
  }
  if (url.pathname === '/api/instructions/execute') {
    let raw = ''; for await (const chunk of req) raw += chunk;
    const { instruction: name, arguments: args } = JSON.parse(raw); calls.push({ name, args });
    let result = {};
    if ((denied && ['GetResearchMap','GetLineage','GetSharedState'].includes(name)) || (sourceDenied && name === 'ResolveResource')) {
      res.writeHead(403, { 'Content-Type': 'application/json' }); return res.end(JSON.stringify({ error: { code:'instruction.permission_denied',message: 'No access' } }));
    }
    if (args.project_id === 'slow') await new Promise(r=>setTimeout(r,180));
    if (name === 'GetResearchMap') result = map;
    if (name === 'GetProject') result = { project: { id:args.project_id }, team: { lineages: [{ lineage_id:lineage,title:'Test team' }] } };
    if (name === 'GetLineage') result = { lineage: { title: 'Test team', nodes: [] } };
    if (name === 'GetSharedState') result = { value: state };
    if (name === 'ResolveResource') result = { resource:{title:'Test source',version:1} };
    if (name === 'ApplyResearchMapChanges') {
      if (conflict || args.expected_revision !== map.revision) { res.writeHead(409,{'Content-Type':'application/json'}); return res.end(JSON.stringify({error:{code:'instruction.version_conflict',message:'Version conflict'}})); }
      for (const c of args.changes) if (c.op === 'update_direction') Object.assign(map.directions.find(d=>d.direction_id===c.direction_id),c.fields);
      map.revision++; result={revision:map.revision};
    }
    if (name === 'UpdateSharedState') {
      const matched=JSON.stringify(args.expected_value)===JSON.stringify(state);
      if(matched)state=args.value;result={matched,value:state};
    }
    res.writeHead(200,{'Content-Type':'application/json'});return res.end(JSON.stringify({status:'committed',result}));
  }
  if(url.pathname.startsWith('/api/')) {res.writeHead(200,{'Content-Type':'application/json'});return res.end(JSON.stringify({username:'fixture'}));}
  if(url.pathname.startsWith('/static/')) {
    try {const file=path.join(staticRoot,url.pathname.slice(8));res.writeHead(200,{'Content-Type':file.endsWith('.css')?'text/css':'text/javascript'});return res.end(readFileSync(file));}catch{}
  }
  res.writeHead(404);res.end();
});
(async()=>{
  await new Promise(r=>server.listen(0,'127.0.0.1',r));
  const browser=await chromium.launch({headless:true});const page=await browser.newPage({viewport:{width:1100,height:800}});
  const errors=[],checks=[];page.on('pageerror',e=>errors.push(e.message));
  try {
    await page.goto(`http://127.0.0.1:${server.address().port}`);
    await page.evaluate(id=>view.show({kind:'project',id:'test',focus:id}),direction);
    await page.locator('.mapNode.is-selected').waitFor();
    assert.equal(await page.evaluate(()=>events.at(-1).address),direction);
    await page.locator('[data-edit]').click();await page.locator('[name="question"]').fill('Draft survives hidden surface');
    assert.equal(await page.evaluate(()=>view.snapshot().dirty),true);
    await page.locator('[data-map-viewport]').hover();await page.mouse.wheel(0,130);
    const transform=await page.locator('[data-map-world]').evaluate(el=>getComputedStyle(el).transform);
    await page.evaluate(async()=>{await view.setPresented(false);await view.setPresented(true);});
    assert.equal(await page.locator('[name="question"]').inputValue(),'Draft survives hidden surface');
    assert.equal(await page.locator('[data-map-world]').evaluate(el=>getComputedStyle(el).transform),transform);
    conflict=true;await page.locator('[data-edit-form] button[type="submit"]').click();
    await page.waitForFunction(()=>document.body.innerText.includes('Version conflict'));
    assert.equal(await page.locator('[name="question"]').inputValue(),'Draft survives hidden surface');
    conflict=false;await page.locator('[data-edit-form] button[type="submit"]').click();
    await page.locator('[data-edit-form]').waitFor({state:'detached'});
    assert.equal(map.directions[0].question,'Draft survives hidden surface');
    assert.ok(calls.some((c,i)=>c.name==='ApplyResearchMapChanges' && calls.slice(i+1).some(n=>n.name==='GetResearchMap')));
    checks.push('native project focus, selection, camera/draft restoration, conflict and save/readback');
    await page.locator('[data-map-frontier]').click();await page.locator('.graphNode[data-kind="seed"]').waitFor();
    await page.getByRole('button',{name:'适应',exact:true}).click();
    await page.locator('.graphNode[data-kind="seed"]').click();
    assert.equal(await page.evaluate(()=>events.at(-1).item_id),'seed');
    checks.push('project native exploration layer selection');
    await page.evaluate(id=>view.show({kind:'lineage',id,focus:'seed'}),lineage);
    const surface=page.locator('.homeMapSurface:not([hidden])');
    await surface.locator('#detail').waitFor();
    await surface.getByRole('button',{name:'更新线头',exact:true}).click();
    await surface.locator('[name="reason"]').fill('Native readback');
    await page.evaluate(()=>view.setPresented(false));
    state.items[0].progress='Concurrent change';
    await page.evaluate(()=>view.setPresented(true));
    assert.equal(await surface.locator('[name="reason"]').inputValue(),'Native readback');
    await surface.getByRole('button',{name:'保存线头',exact:true}).click();
    await surface.getByText('其他人已更新这条工作线。',{exact:false}).waitFor();
    assert.equal(state.items[0].reason,'A question');
    await surface.getByRole('button',{name:'保存线头',exact:true}).click();
    await page.waitForFunction(()=>!document.querySelector('.homeMapSurface:not([hidden])').shadowRoot.querySelector('#editor').open);
    assert.equal(state.items[0].reason,'Native readback');
    await page.evaluate(id=>view.show({kind:'lineage',id,focus:'seed'}),lineage);
    await surface.getByRole('button',{name:'放大',exact:true}).click();
    const zoom=await surface.locator('#zoom-level').textContent();
    await page.evaluate(async()=>{await view.setPresented(false);await view.setPresented(true);});
    assert.equal(await surface.locator('#zoom-level').textContent(),zoom);
    assert.equal(await surface.locator('.graphNode.is-selected').getAttribute('data-id'),lineage+':seed');
    await surface.getByRole('button',{name:'在此阅读',exact:true}).click();
    assert.equal(await page.evaluate(()=>opens.at(-1).view),'preview');
    checks.push('native lineage focus, source callback, mutation/readback and camera restoration');
    denied=true;await page.evaluate(async()=>{await view.setPresented(false);await view.setPresented(true).catch(()=>{});});
    assert.equal(await page.locator('.homeMapSurface:not([hidden])').count(),0);
    await page.getByRole('status').filter({hasText:'No access'}).waitFor();
    denied=false;await page.getByRole('button',{name:'重试',exact:true}).click();await surface.locator('.graphNode[data-kind="seed"]').waitFor();
    sourceDenied=true;await page.evaluate(async()=>{await view.setPresented(false);await view.setPresented(true);});
    assert.equal(await surface.locator('.graphNode[data-kind="seed"]').count(),0);
    sourceDenied=false;
    checks.push('scope revocation hidden, retry and per-source revocation');
    await page.evaluate(async()=>{await view.setPresented(false);await view.show({kind:'project',id:'test',focus:'direction_0123456789ab'},{view:'frontier'});await view.setPresented(true);});
    await page.locator('.homeMapSurface:not([hidden]) .graphNode[data-kind="direction"].is-selected').waitFor();
    const beforeRepeat=calls.length;await page.evaluate(()=>view.setPresented(true));assert.equal(calls.length,beforeRepeat);
    checks.push('host frontier intent, initially hidden focus, idempotent visibility and dirty snapshot');
    const late=await page.evaluate(async()=>{const old=view.show({kind:'project',id:'slow'});const next=view.show({kind:'project',id:'test'});return [await old,await next];});
    assert.deepEqual(late,[false,true]);
    await page.evaluate(async()=>{const c=new AbortController();const p=view.show({kind:'project',id:'slow'},{signal:c.signal,requestId:'cancel'});c.abort();await p;});
    assert.equal(await page.locator('.homeMapSurface:not([hidden])').count(),0);
    await page.evaluate(()=>view.show({kind:'project',id:'test'}));
    await page.setViewportSize({width:390,height:800});
    await page.screenshot({path:path.join(out,'narrow.png')});
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    await page.evaluate(()=>view.destroy());assert.equal(await page.locator('.homeMapView').count(),0);
    assert.deepEqual(errors,[]);checks.push('late request, cancellation, narrow container and teardown');
    writeFileSync(path.join(out,'report.json'),JSON.stringify({checks,errors,calls},null,2));console.log(JSON.stringify({checks,errors}));
  }catch(error){writeFileSync(path.join(out,'failure.json'),JSON.stringify({message:error.message,checks,errors,calls},null,2));await page.screenshot({path:path.join(out,'failure.png')});throw error;}
  finally {await browser.close();server.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
