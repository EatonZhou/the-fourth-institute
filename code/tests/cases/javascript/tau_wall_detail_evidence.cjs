'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const source = process.env.TAU_WALL_SOURCE_ROOT || path.resolve(__dirname, '../../..');
const {chromium} = require(path.join(source, 'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const {observeBrowser} = require(path.join(source, 'management_portal/test_framework/tau_browser_observer'));
const {observeWallDetail, checkWallDetailReady} = require(process.env.TAU_WALL_DETAIL_HELPER
  || path.join(source, 'management_portal/test_framework/tau_wall_detail_evidence'));

(async () => {
  const evidenceDir = fs.mkdtempSync(path.join(os.tmpdir(), 'tau-wall-detail-'));
  const projectId = 'project-fixture', blockId = '.wall/dev.md#fixture';
  const discussionId = 'resident_111111111111', workerId = 'resident_222222222222';
  const messageId = 'msg_123456789abc';
  let mode = 'bound', release, browser, observer, capture, bodyReads = 0;
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://fixture.invalid');
    if (url.pathname !== '/api/wall/block') {
      res.setHeader('Content-Type', 'text/html');
      res.end(`<button id="open">Open detail</button><section role="dialog" id="detail" hidden>
        <textarea id="body">complete fixture body</textarea><span id="identity" data-workspace-resident-id=""></span>
        <div id="composer" contenteditable="true"></div></section><script>
        open = document.getElementById('open');
        open.onclick = async () => {
          const detail = document.getElementById('detail'); detail.hidden = false;
          const payload = await fetch('/api/wall/block?project=project-fixture&block=' + encodeURIComponent('.wall/dev.md#fixture')).then(r => r.json());
          // The initial board-derived empty composer precedes authoritative identity.
          setTimeout(() => {
            document.getElementById('body').value = payload.block.content;
            document.getElementById('identity').dataset.workspaceResidentId = payload.block.discussion?.resident_id || '';
            detail.dataset.rendered = 'yes';
          }, 100);
        };
        </script>`);
      return;
    }
    const send = () => {
      if (res.writableEnded) return;
      const block = {id: blockId, content: 'complete fixture body', title: 'private-title', resident_id: workerId,
        discussion: {resident_id: mode === 'empty' ? '' : discussionId},
        job: {resident_id: workerId, message_id: messageId, status: 'completed'}};
      if (mode === 'wrong-block') block.id = 'private-other-block';
      if (mode === 'missing-discussion') delete block.discussion;
      res.writeHead(mode === 'error' ? 503 : 200, {'Content-Type': 'application/json'});
      res.end(JSON.stringify({block, unused: 'private-extra-payload'}));
    };
    if (mode === 'held') release = send; else send();
  });
  try {
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const origin = `http://127.0.0.1:${server.address().port}`;
    browser = await chromium.launch({headless: true});
    const context = await browser.newContext();
    observer = await observeBrowser(context, {evidenceDir});
    const page = await context.newPage();
    page.on('response', response => {
      const read = response.text.bind(response);
      response.text = () => { bodyReads++; return read(); };
    });
    const start = (requestId = observer.requestId) => {
      capture = observeWallDetail({page, origin, projectId, blockId, evidenceDir, requestId}); return capture;
    };
    const sample = () => page.evaluate(({blockId}) => ({
      block_id: blockId, visible: !document.getElementById('detail').hidden, loading: false,
      selected_tab: 'discussion', panel_visible: !document.getElementById('detail').hidden,
      composer_editable: document.getElementById('composer').isContentEditable,
      body_value: document.getElementById('body').value,
      resident_id: document.getElementById('identity').dataset.workspaceResidentId,
      explicit_empty: document.getElementById('composer').textContent === '',
    }), {blockId});
    mode = 'held'; await page.goto(origin); start(); await page.click('#open');
    assert.equal(checkWallDetailReady(capture.snapshot(), await sample()).ready, false,
      'the board-derived empty composer cannot finish the original action');
    while (!release) await new Promise(resolve => setTimeout(resolve, 5));
    release(); release = null;
    const receipt = await capture.waitForReceipt();
    const empty = {...await sample(), resident_id: ''};
    assert.equal(checkWallDetailReady(receipt, empty).ready, false, 'authoritative bound discussion must not accept empty identity');
    await page.waitForSelector('#detail[data-rendered="yes"]');
    const ui = await sample();
    assert.equal(checkWallDetailReady(receipt, ui).ready, true);
    assert.equal(checkWallDetailReady(receipt, {...ui, loading: true}).ready, false);
    assert.equal(checkWallDetailReady(receipt, {...ui, resident_id: workerId}).ready, false, 'implementation is not discussion');
    assert.equal(checkWallDetailReady(receipt, {...ui, body_value: 'truncated body'}).ready, false);
    assert.equal(checkWallDetailReady(receipt, {...ui, selected_tab: 'work', resident_id: workerId,
      message_id: messageId}, {kind: 'work'}).ready, true);
    assert.equal(checkWallDetailReady(receipt, {...ui, selected_tab: 'work', resident_id: workerId,
      message_id: 'msg_abcdef'}, {kind: 'work'}).ready, false);
    await capture.stop();

    mode = 'empty'; await page.goto(origin); start(); await page.click('#open');
    const explicitEmpty = await capture.waitForReceipt();
    await page.waitForSelector('#detail[data-rendered="yes"]');
    assert.equal(checkWallDetailReady(explicitEmpty, await sample()).ready, true,
      'the exact response explicitly confirms the first-send discussion has no Resident');
    await capture.stop();

    for (mode of ['wrong-block', 'missing-discussion', 'error']) {
      await page.goto(origin); start(); await page.click('#open');
      await assert.rejects(capture.waitForReceipt(), /incomplete/);
      assert.equal(checkWallDetailReady(capture.snapshot(), await sample()).ready, false);
      await capture.stop();
    }
    mode = 'bound'; await page.goto(origin); start(() => null); const beforeMissing = bodyReads;
    await page.click('#open'); await assert.rejects(capture.waitForReceipt(), /incomplete/);
    assert.equal(bodyReads, beforeMissing, 'do not read a response without original public identity'); await capture.stop();

    mode = 'held'; await page.goto(origin); await page.click('#open');
    while (!release) await new Promise(resolve => setTimeout(resolve, 5));
    start(); const beforeLate = bodyReads; release(); release = null;
    await page.waitForSelector('#detail[data-rendered="yes"]');
    await assert.rejects(capture.waitForReceipt({timeoutMs: 50}), /timeout/);
    assert.equal(capture.snapshot().matching_requests, 0); assert.equal(bodyReads, beforeLate);
    await capture.stop();

    mode = 'bound'; await page.goto(origin); start();
    const beforeOther = bodyReads;
    await page.evaluate(() => fetch('/api/wall/block?project=other&block=private-unselected-block'));
    assert.equal(capture.snapshot().matching_requests, 0); assert.equal(bodyReads, beforeOther);
    await page.click('#open'); await capture.waitForReceipt();
    await page.click('#open'); await assert.rejects(capture.waitForReceipt(), /Ambiguous/);
    await capture.stop(); await observer.flush();
    const output = fs.readFileSync(capture.file, 'utf8');
    for (const privateValue of ['private-title', 'private-extra-payload', 'private-other-block',
      'private-unselected-block', 'complete fixture body']) assert.ok(!output.includes(privateValue));
    const rows = output.trim().split('\n').map(JSON.parse);
    const native = fs.readFileSync(observer.file, 'utf8').trim().split('\n').map(JSON.parse);
    for (const row of rows.filter(row => row.kind === 'detail_request' && row.request_id)) {
      const observed = native.find(item => item.kind === 'request_started' && item.request_id === row.request_id);
      assert.ok(observed?.page_id && observed.frame_id && observed.document_id && observed.document_confirmed);
    }
    assert.equal(page.listenerCount('request'), 0);
    assert.equal(page.listenerCount('requestfailed'), 0);
    console.log('WALL original detail evidence: browser race, explicit empty, identity, missing/late/ambiguous and privacy checks passed');
  } finally {
    release?.(); await capture?.stop(); await observer?.flush(); observer?.stop();
    await browser?.close(); await new Promise(resolve => server.close(resolve));
    fs.rmSync(evidenceDir, {recursive: true, force: true});
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
