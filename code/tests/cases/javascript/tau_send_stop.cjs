'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { chromium } = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const { sendThenStop } = require('../../../management_portal/test_framework/tau_send_stop');

(async () => {
  const evidenceDir = fs.mkdtempSync(path.join(os.tmpdir(), 'tau-send-stop-'));
  let browser;
  try {
    browser = await chromium.launch({ headless: true });
    const page = await browser.newPage();
    async function prepare(delay) {
      await page.setContent(`<button id="send">Send</button><button id="stop" disabled>Stop</button>
        <script>window.submissions = 0; window.stops = 0; window.trusted = [];
        document.querySelector('#send').onclick = e => { submissions++; trusted.push(e.isTrusted);
          ${delay === null ? '' : `setTimeout(() => document.querySelector('#stop').disabled = false, ${delay});`} };
        document.querySelector('#stop').onclick = e => { stops++; trusted.push(e.isTrusted); document.querySelector('#stop').disabled = true; };</script>`);
      return { send: page.locator('#send'), stop: page.locator('#stop'), evidenceDir };
    }
    const quick = await sendThenStop(await prepare(0));
    assert.equal(quick.send_click_count, 1);
    assert.equal(quick.stop_click_count, 1);
    assert.ok(quick.stop_dom_ready_ms >= quick.send_click_ms);
    assert.ok(quick.stop_click_ms >= quick.stop_dom_ready_ms);
    assert.deepEqual(await page.evaluate(() => [submissions, stops, trusted]), [1, 1, [true, true]]);
    assert.deepEqual(quick.pointer_events.map(x => [x.type,x.control]), [
      ['pointerdown','send'],['pointerup','send'],['pointerdown','stop'],['pointerup','stop']]);
    assert.ok(quick.stop_observations.some(x => x.visible && x.enabled && x.center_hits_stop));

    const delayed = await sendThenStop(await prepare(650));
    assert.ok(delayed.send_to_stop_ms >= 650, 'disabled wait is retained, not subtracted to pass 500ms');
    assert.equal(delayed.click_error, null);
    assert.ok(delayed.stop_observations.some(x => !x.enabled));

    const coveredControls = await prepare(0);
    await page.evaluate(() => document.querySelector('#send').addEventListener('click', () => {
      const r = document.querySelector('#stop').getBoundingClientRect();
      const cover = document.createElement('div'); cover.textContent = 'private fixture content';
      Object.assign(cover.style, {position:'fixed',zIndex:100,left:r.left+'px',top:r.top+'px',width:r.width+'px',height:r.height+'px'});
      document.body.append(cover); setTimeout(() => cover.remove(), 650);
    }));
    const covered = await sendThenStop(coveredControls);
    assert.ok(covered.send_to_stop_ms >= 650, 'hit-target delay remains inside the original timing');
    assert.ok(covered.stop_observations.some(x => x.visible && x.enabled && !x.center_hits_stop && x.hit_tag === 'DIV'));
    assert.ok(covered.stop_observations.some(x => x.center_hits_stop));
    assert.deepEqual(await page.evaluate(() => [submissions, stops]), [1, 1]);

    let timeout;
    try { await sendThenStop({ ...await prepare(null), timeoutMs: 300 }); }
    catch (error) { timeout = error; }
    assert.equal(timeout?.name, 'TimeoutError');
    assert.equal(timeout.sendStopMeasurement.send_click_count, 1);
    assert.equal(timeout.sendStopMeasurement.stop_click_count, 0);
    assert.equal(timeout.sendStopMeasurement.send_to_stop_ms, null);
    assert.deepEqual(await page.evaluate(() => [submissions, stops]), [1, 0], 'no replay or forced Stop');
    await page.setContent(`<div id="chat"><button id="send">Send</button></div><button id="other">Stop</button>
      <script>window.submissions=0;window.stops=0;document.querySelector('#send').onclick=()=>{
        submissions++;const button=document.createElement('button');button.id='stop';button.textContent='Stop';
        button.onclick=()=>{stops++;button.remove();};document.querySelector('#chat').append(button);
      };</script>`);
    const mounted = await sendThenStop({send:page.locator('#send'),stop:page.locator('#chat #stop'),stopSelector:'#chat #stop',evidenceDir});
    assert.equal(mounted.send_click_count,1);assert.equal(mounted.stop_click_count,1);
    assert.ok(mounted.stop_dom_ready_ms >= mounted.send_click_ms);
    assert.ok(mounted.send_to_stop_ms >= 0 && mounted.send_to_stop_ms < 500);
    assert.deepEqual(await page.evaluate(()=>[submissions,stops]),[1,1]);
    let absent;
    try {await sendThenStop({send:page.locator('#absent'),stop:page.locator('#stop'),evidenceDir,timeoutMs:100});}
    catch(error) {absent=error;}
    assert.equal(absent?.sendStopMeasurement.observation,'not_started');
    const rows = fs.readFileSync(path.join(evidenceDir, 'tau-send-stop.jsonl'), 'utf8').trim().split('\n').map(JSON.parse);
    assert.equal(rows.length, 6, 'pre-click failure and dynamically mounted Stop retain original receipts');
    assert.equal(rows[3].click_error, 'TimeoutError');
    assert.ok(!JSON.stringify(rows).includes('Send</button>'), 'no control content is recorded');
    assert.ok(!JSON.stringify(rows).includes('private fixture content'), 'no hit-target content is recorded');
    console.log('send-stop native clicks passed');
  } finally {
    if (browser) await browser.close();
    fs.rmSync(evidenceDir, { recursive: true, force: true });
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
