// Production view, synthetic saved report, deterministic clock and HTTP replies.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');

const name = process.argv[2];
const tick = async () => { for (let i = 0; i < 4; i++) await new Promise(setImmediate); };
async function check() {
  const dom = new JSDOM('<main id="root"></main>', {url:'http://fixture/', runScripts:'outside-only', pretendToBeVisual:true});
  const w = dom.window, root = w.document.querySelector('#root');
  const observers = [], MutationObserver = w.MutationObserver;
  w.MutationObserver = class extends MutationObserver {
    constructor(callback) {super(callback); observers.push(this);}
  };
  let hidden = false, now = 0, sequence = 0;
  const timers = new Map(), requests = [];
  Object.defineProperty(w.document, 'hidden', {get:() => hidden});
  w.setTimeout = (fn, delay = 0) => {const id = ++sequence; timers.set(id, {fn, at:now + delay}); return id;};
  w.clearTimeout = id => timers.delete(id);
  w.HTMLMediaElement.prototype.pause = () => {};
  w.HTMLMediaElement.prototype.load = () => {};
  w.fetch = (url, options) => new Promise(resolve => requests.push({url, options, resolve, answered:false}));
  const reply = (request, value, status = 200) => {
    assert.ok(request && !request.answered, 'expected an unanswered request');
    request.answered = true;
    request.resolve({ok:status < 400, status, json:async () => value});
  };
  const take = suffix => requests.find(r => !r.answered && r.url.endsWith(suffix));
  const mediaReads = () => requests.filter(r => /\/media\/high$/.test(r.url));
  const advance = async ms => {
    const end = now + ms;
    for (let steps = 0; ; steps++) {
      assert.ok(steps < 50, 'unbounded timer loop');
      const next = [...timers].filter(([, t]) => t.at <= end).sort((a,b) => a[1].at-b[1].at)[0];
      if (!next) break;
      now = next[1].at; timers.delete(next[0]); next[1].fn(); await tick();
    }
    now = end; await tick();
  };
  const visibility = value => {hidden = value; w.document.dispatchEvent(new w.Event('visibilitychange'));};
  const click = label => {
    const b = [...root.querySelectorAll('button')].find(b => b.textContent === label);
    assert.ok(b, 'missing control: ' + label); b.click();
  };
  const report = () => ({title:'Synthetic saved report', report_id:'report_123456789abc', revision:1,
    latest_revision:1, target:{kind:'project',id:'fixture'}, quality:'high', version:'a'.repeat(64),
    goal:'fixture', warnings:[], rows:[], counts:{}, sources:[], reasoning:{available:false},
    review:{status:'submitted'}, media:{status:'pending',quality:'high'}});
  const codeRoot = path.resolve(__dirname, '../../..');
  // Localization is independent of the task clock; keep its document observer
  // out of this fixture while exercising the actual radio control and view.
  w.PortalI18n = {t:String};
  for (const file of ['portal_ui_primitives.js','home_report.js']) {
    const source = file === 'home_report.js' && process.env.PORTAL_POLL_REPORT_SOURCE
      || path.join(codeRoot, 'management_portal/static', file);
    w.eval(fs.readFileSync(source, 'utf8'));
  }
  const view = w.PortalHomeReportView.create({root});
  const aborter = new w.AbortController();
  try {
    const opening = view.show({kind:'report',id:'report_123456789abc',revision:1}, {signal:aborter.signal});
    reply(requests[0], report()); await opening;
    if (name === 'hidden-resume' || name === 'resume-denied') {
      visibility(true); await advance(40000);
      assert.equal(requests.length, 1, 'hidden tab must not read task or source state');
      visibility(false); await tick();
      assert.equal(mediaReads().length, 0, 'revalidation must precede resumed task reads');
      const denied = name === 'resume-denied';
      reply(take('/validate'), denied ? {error:'revoked'} : {readable:true}, denied ? 403 : 200);
      await tick(); await advance(1000);
      if (denied) {
        assert.equal(mediaReads().length, 0);
        assert.match(root.textContent, /已撤去/);
      } else {
        assert.equal(mediaReads().length, 1, 'pending task must resume after authorized reveal');
        reply(mediaReads()[0], {status:'failed',error:'fixture failure',quality:'high'});
      }
    } else {
      await advance(1000);
      const poll = mediaReads()[0]; assert.ok(poll, 'initial pending task poll');
      if (name === 'hidden-inflight') {
        visibility(true); reply(poll, {status:'pending',quality:'high'}); await tick(); await advance(40000);
        assert.equal(requests.length, 2, 'late hidden response must not resurrect timers');
      } else if (['destroy','unpresent','abort'].includes(name)) {
        if (name === 'destroy') view.destroy();
        else if (name === 'unpresent') await view.setPresented(false);
        else aborter.abort();
        reply(poll, {status:'pending',quality:'high'}); await tick(); await advance(40000);
        assert.equal(requests.length, 2, 'closed view must not resume reads');
      } else if (name === 'cancel-inflight' || name === 'cancel-inflight-error') {
        click('取消生成'); reply(take('/media/cancel'), {status:'cancelled',quality:'high'}); await tick();
        reply(poll, name === 'cancel-inflight-error' ? {error:'obsolete task unavailable'} : {status:'pending',quality:'high'},
          name === 'cancel-inflight-error' ? 404 : 200);
        await tick(); await advance(10000);
        assert.equal(mediaReads().length, 1, 'late pending read must not poll a cancelled task');
        assert.match(root.textContent, /生成已取消/);
      } else if (name === 'hidden-resume-inflight') {
        visibility(true); visibility(false); await tick();
        reply(take('/validate'), {readable:true}); await tick(); await advance(1000);
        assert.equal(mediaReads().length, 2, 'visible view resumes one pending task read');
        reply(poll, {status:'pending',quality:'high'}); await tick(); await advance(4000);
        assert.equal(mediaReads().length, 2, 'read from before hide must not start a second chain');
      } else if (name === 'reload-inflight') {
        click('读取审核结果');
        reply(requests.find(r => !r.answered && r !== poll), report()); await tick();
        await advance(1000); // Replacement task read is already in flight.
        reply(poll, {status:'pending',quality:'high'}); await tick(); await advance(4000);
        assert.equal(mediaReads().length, 2, 'accepted snapshot must own exactly one polling chain');
      } else if (name.startsWith('denied-')) {
        reply(poll, {error:'permanently unavailable'}, Number(name.split('-')[1]));
        await tick(); await advance(40000);
        assert.equal(requests.length, 2, 'withdrawn report must not keep reading state');
        assert.match(root.textContent, /已撤去/);
      } else if (name === 'terminal') {
        reply(poll, {status:'failed',quality:'high',error:'fixture failure'}); await tick(); await advance(40000);
        assert.equal(mediaReads().length, 1, 'terminal task must stop polling');
        assert.ok(take('/validate'), 'visible report source authorization checks remain necessary');
        assert.match(root.textContent, /重试视频/);
      } else throw new Error('unknown case: ' + name);
    }
    assert.equal(requests.filter(r => r.options.method === 'POST' && r.url.endsWith('/media')).length, 0,
      'read/reopen must not generate media');
    console.log(JSON.stringify({case:name, requests:requests.map(r => r.url), status:'passed'}));
  } finally {
    view.destroy();
    // Drain the real UI enhancer's queued mutation work before JSDOM disposal.
    await tick();
    observers.forEach(observer => observer.disconnect());
    dom.window.close();
  }
}
check().catch(error => {console.error(error); process.exitCode = 1;});
