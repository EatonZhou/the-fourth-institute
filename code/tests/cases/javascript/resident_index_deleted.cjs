'use strict';
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path'), http = require('node:http');
const code = process.env.TAU_CODE_ROOT || path.resolve(__dirname,'../../..');
const draft = process.env.TAU_INDEX_DELETE_DRAFT || path.join(code,'management_portal/static');
const { chromium } = require(path.join(code, 'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const rid = 'resident_111111111111', other = 'resident_222222222222';
async function scenario(browser, patched) {
  let targetState = 'present', held = null, hold = false, eventCursor = 1;
  const requests = [], failures = [], pageErrors = [];
  const entry = id => ({ key: `resident:${id}`, resident_id: id, kind: 'resident-main', section: 'conversation', title: id, unread: false, pinned: true, updated_at: '2026-10-25T00:00:00Z', read_revision: 1 });
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://localhost');
    if (!url.pathname.startsWith('/api/')) { res.writeHead(200, {'content-type':'text/html'});res.end('<title>Isolated index tombstone</title>');return; }
    let raw = '';req.on('data', x => raw += x);req.on('end', () => {
      const body = raw ? JSON.parse(raw) : {}, id = url.searchParams.get('id') || url.searchParams.get('resident');
      requests.push({ path: url.pathname, id, instruction: body.instruction || null });
      const reply = (status, data) => {res.writeHead(status, {'content-type':'application/json'});res.end(JSON.stringify(data));};
      if (url.pathname === '/api/agent-conversations') {
        if (!id) return reply(200, { entries: [entry(rid), entry(other)], ready: true, read_revision: 1, content_signature: 'initial' });
        const present = id !== rid || targetState === 'present';
        return reply(200, { resident_id: id, target_state: id === rid ? targetState : 'present', entries: present ? [entry(id)] : [], target_present: present, resident_cursor: eventCursor, projection_cursor: eventCursor, ready: true, read_revision: 2 });
      }
      if (body.instruction === 'ListResidentStates') return reply(200, {status:'committed', result:{states:(body.arguments.resident_ids || []).map(id => ({resident_id:id, state:{status:'idle'},viewer_state:{unread:false},cursor:1}))}});
      if (url.pathname.endsWith('/projection')) {
        const finish = () => {
          if (id === rid && targetState === 'deleted') {failures.push(id);return reply(404, {code:'operation.not_found',error:'not found'});}
          return reply(200, {resident_id:id, resident:{resident_id:id}, provider_session:{id:'bound'}});
        };
        if (hold) {held = () => reply(200,{resident_id:id, resident:{resident_id:id},provider_session:{id:'bound'}});return;}
        finish();return;
      }
      reply(500, {error:'unexpected fixture route'});
    });
  });
  await new Promise(r => server.listen(0,'127.0.0.1',r));
  const context = await browser.newContext(), page = await context.newPage();
  page.setDefaultTimeout(8000);
  page.on('pageerror', e => pageErrors.push(e.message));
  try {
    await page.goto(`http://127.0.0.1:${server.address().port}`);
    await page.evaluate(() => {
      window.PortalI18n = {t:x=>x,message:x=>typeof x==='string'?x:x.message,requestLanguage:()=> 'en'};
      window.PortalIdentity = {username:()=> 'fixture',userId:async()=> 'fixture'};
      window.PortalCompatibility = {identifier:x=>`fixture:${x}`};
      window.PortalEvents = {isConnected:()=>true,connect(){},watchResident:()=>()=>{}};
      window.nativeReads=[];window.projectionUpdates=[];
      window.addEventListener('portal:resident-read', e => nativeReads.push(e.detail));
    });
    for (const name of ['portal_state.js','portal_text.js','portal_ui_primitives.js','portal_api.js','portal_instruction_client.js','portal_resident_service.js','resident_projection.js','resident_protocol.js','portal_refresh_coordinator.js','portal_conversation_store.js']) {
      const candidate = path.join(draft,name);
      await page.addScriptTag({path:patched && fs.existsSync(candidate) ? candidate : path.join(code,'management_portal/static',name)});
    }
    await page.evaluate(async rid => {
      await PortalConversationStore.refresh();await PortalResidentProjection.load(rid);
      window.stopProjection=PortalResidentProjection.subscribe(rid, v=>projectionUpdates.push(v.error ? {code:v.error.code} : {ok:true}));
    },rid);
    await page.waitForFunction(()=>projectionUpdates.length===1);
    assert.equal(await page.evaluate(rid=>!!PortalConversationStore.entry(`resident:${rid}`),rid),true);
    const notify = async cursor => {eventCursor=cursor;return page.evaluate(({rid,cursor})=>window.dispatchEvent(new CustomEvent('portal:change',{detail:{resident_events:[{resident_id:rid,cursor,event_type:'resident.input.state.changed',payload:{input_state:{state:'cancelled',message_id:'fixture_original'}}}]}})),{rid,cursor});};
    // A permission-filtered tombstone must not permanently retire the identity.
    targetState='forbidden';await notify(2);
    await page.waitForFunction(rid=>!PortalConversationStore.entry(`resident:${rid}`),rid);
    await page.waitForFunction(()=>projectionUpdates.length===2);
    assert.equal(await page.evaluate(()=>nativeReads.some(r=>r.phase==='index_deleted')),false);
    targetState='present';await notify(3);
    await page.waitForFunction(rid=>!!PortalConversationStore.entry(`resident:${rid}`),rid);
    // Preserve a real outstanding response so a tombstone also tests stale data.
    hold=true;
    await page.evaluate(rid=>{window.late=PortalResidentService.projection({residentId:rid}).then(()=>({ok:true}),e=>({code:e.code}));},rid);
    const limit=Date.now()+5000;while(!held && Date.now()<limit) await new Promise(r=>setTimeout(r,5));assert(held);
    hold=false;targetState='deleted';await notify(4);
    await page.waitForFunction(rid=>!PortalConversationStore.entry(`resident:${rid}`),rid);
    await page.waitForFunction(()=>projectionUpdates.length===3);
    const removedReadCount=requests.filter(r=>r.path.endsWith('/projection')&&r.id===rid).length;
    held();const late=await page.evaluate(()=>window.late);
    if(patched){
      assert.equal(failures.length,0);assert.equal(late.code,'resident.deleted');
      assert.equal(await page.evaluate(rid=>PortalResidentProjection.peek(rid),rid),null);
      const trace=await page.evaluate(()=>nativeReads.filter(r=>r.phase==='index_deleted'));
      assert.equal(trace.length,1);assert.equal(trace[0].resident_id,rid);assert.equal(trace[0].lifecycle.deleted,true);
      assert.equal(await page.evaluate(rid=>PortalResidentService.projection({residentId:rid}).then(()=>null,e=>e.code),rid),'resident.deleted');
      assert.equal(requests.filter(r=>r.path.endsWith('/projection')&&r.id===rid).length,removedReadCount);
    } else {assert.equal(failures.length,1);assert.equal(late.ok,true);}
    // The later notification must not cause a retry, and unrelated reads survive.
    await page.evaluate(rid=>window.dispatchEvent(new CustomEvent('portal:change',{detail:{resident_changes:{[rid]:['deleted','identity']}}})),rid);
    await page.waitForFunction(()=>projectionUpdates.length===4);
    await page.evaluate(other=>PortalResidentProjection.load(other),other);
    assert.equal(await page.evaluate(other=>!!PortalResidentProjection.peek(other),other),true);
    assert.deepEqual(pageErrors,[]);
    return {patched,status:'passed',post_tombstone_http_404s:failures.length,late_response:late,permission_recovery:true,unrelated_target_preserved:true,formal_acceptance:false,scope:'isolated native Store/Projection/Service with HTTP fixtures; not original BA replay or state-batch remediation'};
  } finally {await context.close();await new Promise(r=>server.close(r));}
}
(async()=>{const browser=await chromium.launch({headless:true,args:['--no-sandbox']});try{for(const patched of (process.env.TAU_COMPARE_BASELINE ? [false,true] : [true]))console.log(JSON.stringify(await scenario(browser,patched)));}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
