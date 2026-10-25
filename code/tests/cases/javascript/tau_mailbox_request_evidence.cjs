'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const code = process.env.TFI_TAU_CODE_ROOT || path.resolve(__dirname, '../../..');
const {chromium} = require(path.join(code, 'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const {observeBrowser} = require(path.join(code, 'management_portal/test_framework/tau_browser_observer'));
const {observeMailboxRequests, readMailboxSocietyPrerequisites} = require(process.env.TFI_MAILBOX_REQUEST_MODULE
  || path.join(code, 'management_portal/test_framework/tau_mailbox_request_evidence'));

(async () => {
  const target = 'resident_123456789abc', other = 'resident_abcdef123456';
  const evidence = fs.mkdtempSync(path.join(os.tmpdir(), 'tau-mailbox-request-'));
  let browser, observer, captureA, captureB, apiCount = 0;
  const source = `window.fixtureRead = function fixtureRead(instruction, resident, token) {
    return fetch('/api/instructions/execute', {method:'POST', headers:{'content-type':'application/json','x-portal-request-id':token},
      body:JSON.stringify({instruction,arguments:{resident_id:resident,private_argument:'MUST_NOT_RETAIN'}})}).then(r=>r.text());
  };
  window.fixtureProjection = function fixtureProjection(resident) {
    return fetch('/api/agent-workbench/residents/projection?id='+resident).then(r=>r.text());
  };`;
  const server = http.createServer((req, res) => {
    if (req.url.startsWith('/static/fixture.js')) { res.writeHead(200, {'content-type':'text/javascript'}); res.end(source); return; }
    if (!req.url.startsWith('/api/')) {
      res.writeHead(200, {'content-type':'text/html'});
      res.end('<nav data-portal-nav aria-label="Primary"><a href="/lineage">Society /</a></nav>'
        + '<button id="show">Show HATG</button><section hidden role="region" aria-label="HATG">Graph</section>'
        + '<script src="/static/fixture.js"></script>'); return;
    }
    apiCount++;
    let raw = '';
    req.on('data', chunk => { raw += chunk; });
    req.on('end', () => {
      const body = raw ? JSON.parse(raw) : {}, selected = body.arguments?.resident_id === target || req.url.includes(target);
      const status = body.instruction === 'GetResidentSummaries' ? 200 : 404;
      res.writeHead(status, {'content-type':'application/json'});
      res.end(JSON.stringify({error:{code:selected?'resident.not_found':'foreign.secret_code',message:'MUST_NOT_RETAIN'},private_result:'MUST_NOT_RETAIN'}));
    });
  });
  try {
    await new Promise(resolve => server.listen(0,'127.0.0.1',resolve));
    const origin = `http://127.0.0.1:${server.address().port}`;
    browser = await chromium.launch({headless:true,args:['--no-sandbox']});
    const context = await browser.newContext();
    observer = await observeBrowser(context,{evidenceDir:evidence});
    const pageA = await context.newPage(), pageB = await context.newPage();
    await pageA.goto(origin+'/lineage'); await pageB.goto(origin+'/lineage');
    const prerequisites = await readMailboxSocietyPrerequisites(pageA);
    assert.equal(prerequisites.navigation_ready,true);
    assert.equal(prerequisites.link.text,'Society /');
    assert.equal(prerequisites.society_document,true);
    assert.equal(prerequisites.hatg_visible,false);
    assert.equal(prerequisites.show_hatg_ready,true);
    // Read-only helper must not click the trigger or change collapsed state.
    assert.equal(await pageA.locator('section').isVisible(),false);
    await pageA.locator('#show').evaluate(n=>n.disabled=true);
    assert.equal((await readMailboxSocietyPrerequisites(pageA)).show_hatg_ready,false);
    await pageA.locator('nav').evaluate(n=>n.insertAdjacentHTML('beforeend','<a href="/lineage">Duplicate</a>'));
    assert.equal((await readMailboxSocietyPrerequisites(pageA)).navigation_ready,false);

    // Earlier original requests cannot be retroactively recovered.
    await pageA.evaluate(id=>window.fixtureRead('ListInputHistory',id,'original-before-registration'),target);
    const before = apiCount;
    const options = {origin,residentId:target,requestId:observer.requestId};
    captureA = await observeMailboxRequests({...options,page:pageA});
    captureB = await observeMailboxRequests({...options,page:pageB});
    assert.equal(apiCount,before); assert.deepEqual(captureA.snapshot().records,[]);
    await pageA.evaluate(async ({target,other})=>{
      await window.fixtureRead('GetResidentSummaries',other,'original-unlisted-operation');
      await window.fixtureRead('ListInputHistory',target,'original-own-history');
      await window.fixtureRead('ListInputHistory',other,'original-other-history');
      await window.fixtureProjection(target);
    },{target,other});
    await pageB.evaluate(id=>window.fixtureRead('ListInputHistory',id,'original-b-history'),target);
    const original = await captureA.stop(), originalB = await captureB.stop();
    assert.equal(original.records.length,4); assert.equal(originalB.records.length,1);
    assert.equal(original.records[0].submitted_instruction,'GetResidentSummaries');
    assert.equal(original.records[0].selected_resident,false);
    assert.equal(original.records[1].error_code,'resident.not_found');
    assert.equal(original.records[2].error_code,null);
    assert.equal(original.records[3].error_code,'resident.not_found');
    assert.ok(original.records.every(r=>r.request_id && r.initiator_association==='unique_original_request_signature'));
    assert.ok(original.records.every(r=>r.caller_stack.some(f=>f.source==='/static/fixture.js')));
    assert.ok(original.records.every(r=>r.trigger_reason==='not_inferred_from_stack_or_time'));
    assert.notEqual(original.records[1].frame_id,originalB.records[0].frame_id);
    assert.notEqual(original.records[1].loader_id,originalB.records[0].loader_id);
    assert.ok(!JSON.stringify(original).includes('MUST_NOT_RETAIN'));
    assert.ok(!JSON.stringify(original).includes(other));
    assert.ok(!JSON.stringify(original).includes('foreign.secret_code'));
    const stable = JSON.stringify(original);
    await pageA.evaluate(id=>window.fixtureRead('ListInputHistory',id,'after-stop'),target);
    assert.equal(JSON.stringify(captureA.snapshot()),stable);

    // Duplicate IDs and identical GETs cannot acquire invented exact callers.
    captureA = await observeMailboxRequests({...options,page:pageA});
    await pageA.evaluate(async id=>{
      await window.fixtureRead('ListInputHistory',id,'same-token');
      await window.fixtureRead('ListInputHistory',id,'same-token');
      await window.fixtureProjection(id); await window.fixtureProjection(id);
    },target);
    const duplicates = await captureA.stop();
    assert.equal(duplicates.records.length,4);
    assert.ok(duplicates.records.every(r=>r.initiator_association==='unavailable_or_ambiguous' && !r.cdp_request_id));
    assert.equal(apiCount,before+5+1+4);
  } finally {
    try { await captureA?.stop(); await captureB?.stop(); observer?.stop(); }
    finally {
      await browser?.close();
      await new Promise(resolve=>server.close(resolve)); fs.rmSync(evidence,{recursive:true,force:true});
    }
  }
  console.log(JSON.stringify({status:'passed',browser:'Chromium',isolated:true,business_acceptance:false,
    checks:['decorated navigation name and collapsed HATG','original instruction and owned error metadata','actual CDP caller stack','separate Page/frame/loader','pre-registration exclusion','duplicate identity rejection','private payload omission','no helper business requests','stopped and immutable captures','fixture cleanup']}));
})().catch(error=>{console.error(error);process.exitCode=1;});
