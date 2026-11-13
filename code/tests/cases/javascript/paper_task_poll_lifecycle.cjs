const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const tick = () => new Promise(resolve => setImmediate(resolve));
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function fixture() {
  const timers = new Map(), delays = new Map(), calls = [];
  let sequence = 0;
  const state = { selectedId: 'paper_owned', mode: 'review', detail: { reviews: [] },
    registrationEditedFields: new Set(), leaving: false };
  const fields = new Map();
  const elements = new Map(['paperModal', 'paperForm', 'paperRegisterUploadStatus',
    'paperRegisterUploadBtn', 'submitPaperBtn'].map(id => [id, {
    hidden: false, classList: { toggle() {} }, reset() {},
    elements: { namedItem(name) { if (!fields.has(name)) fields.set(name, { value: '' }); return fields.get(name); } },
  }]));
  const document = { hidden: false };
  const handlers = new Map();
  const window = { PortalI18n: { t: String, message: String },
    setTimeout(callback, delay) { timers.set(++sequence, callback); delays.set(sequence, delay); return sequence; },
    clearTimeout(id) { timers.delete(id); },
    addEventListener(name, callback) { handlers.set(name, callback); } };
  const context = { window, document, setTimeout: window.setTimeout, clearTimeout: window.clearTimeout,
    FormData: class { append() {} } };
  for (const name of ['paper_registration', 'paper_reviews', 'paper_projection']) {
    const sourceRoot = process.env.PORTAL_POLL_SOURCE_ROOT || path.resolve(__dirname, '../../..');
    vm.runInNewContext(fs.readFileSync(path.join(sourceRoot, 'management_portal/static', name + '.js'), 'utf8'), context);
  }
  const api = { get: async () => ({}), postForm: async () => ({ upload_id: 'upload_owned' }) };
  const deps = Object.fromEntries([...window.PortalPaperRegistration.dependencies,
    ...window.PortalPaperReviews.dependencies, ...window.PortalPaperProjection.dependencies].map(key => [key, () => {}]));
  Object.assign(deps, { state, paperApi: api, $: id => elements.get(id),
    cancelPdfRendering: async () => true,
    paperDialog: { open() { elements.get('paperModal').hidden = false; }, close() { elements.get('paperModal').hidden = true; } },
    alertError(error) { throw error; } });
  for (const name of ['loadCollectorJobs', 'loadDetailSections', 'refreshAssetsProjection',
    'refreshDeepReadProjection', 'refreshExpProjection', 'refreshFigureProjection']) {
    deps[name] = async (...args) => { calls.push({ name, args }); };
  }
  const registration = window.PortalPaperRegistration.createRegistration(deps);
  const reviews = window.PortalPaperReviews.createReviews(deps);
  deps.resumePaperRegistrationPolling = () => registration.resumePaperRegistrationPolling?.();
  deps.resumeReviewPolling = () => reviews.resumeReviewPolling?.();
  return { state, api, timers, fields, calls, document, deps, elements, registration, reviews,
    projection: () => window.PortalPaperProjection.createProjection(deps),
    async fireDelay(delay) { const id = [...timers.keys()].find(key => delays.get(key) === delay);
      assert.ok(id, 'expected timer at ' + delay); const callback = timers.get(id); timers.delete(id); callback(); await tick(); },
    async fire() { assert.equal(timers.size, 1); const [id, callback] = timers.entries().next().value; timers.delete(id); callback(); await tick(); },
  };
}

test('registration permanent denial stops polling; transient failure recovers without resubmission', async () => {
  for (const status of [401, 403, 404, 503]) {
    const f = fixture(); let reads = 0, uploads = 0;
    f.api.postForm = async () => { uploads++; return { upload_id: 'upload_owned' }; };
    f.api.get = async url => {
      assert.match(url, /registration-draft\?upload_id=upload_owned$/);
      if (++reads === 1) throw Object.assign(new Error('read unavailable'), { status });
      return { status: 'done', metadata: { title: 'Recovered' } };
    };
    f.registration.openPaperModal();
    await f.registration.uploadPaperRegistrationPdf({ name: 'fixture.pdf' });
    await f.fire();
    if (status === 503) { assert.equal(f.timers.size, 1); await f.fire(); assert.equal(f.fields.get('title').value, 'Recovered'); }
    assert.equal(f.timers.size, 0, String(status));
    assert.equal(f.state.registrationDraftInFlight, false);
    assert.equal(f.elements.get('submitPaperBtn').disabled, false);
    assert.equal(uploads, 1);
    if (status !== 503) assert.match(f.elements.get('paperRegisterUploadStatus').textContent, /status unavailable/);
  }
});

test('registration terminal metadata stops after one status read and preserves manual fields', async () => {
  for (const status of ['done', 'partial', 'error']) {
    const f = fixture(); let reads = 0;
    f.registration.openPaperModal(); f.state.registrationEditedFields.add('title');
    f.elements.get('paperForm').elements.namedItem('title').value = 'Manual';
    f.api.get = async () => { reads++; return { status, metadata: { title: 'Extracted' } }; };
    await f.registration.uploadPaperRegistrationPdf({ name: 'fixture.pdf' }); await f.fire();
    assert.equal(reads, 1); assert.equal(f.timers.size, 0); assert.equal(f.fields.get('title').value, 'Manual');
    assert.equal(f.state.registrationDraftInFlight, false);
  }
});

test('closing or replacing the registration dialog invalidates a pending upload response', async () => {
  for (const action of ['close', 'reopen', 'leave', 'backdrop']) {
    const f = fixture(), response = deferred();
    f.registration.openPaperModal(); f.api.postForm = () => response.promise;
    const pending = f.registration.uploadPaperRegistrationPdf({ name: 'old.pdf' });
    if (action === 'leave') f.state.leaving = true;
    else if (action === 'backdrop') f.elements.get('paperModal').hidden = true;
    else { f.registration.closePaperModal(); if (action === 'reopen') f.registration.openPaperModal(); }
    response.resolve({ upload_id: 'upload_old' }); await pending;
    assert.equal(f.state.registrationUploadId, '', action);
    assert.equal(f.timers.size, 0, action);
  }
});

test('registration callbacks already queued when the page leaves issue no status read', async () => {
  const f = fixture(); let reads = 0;
  f.api.get = async () => { reads++; return { status: 'running' }; };
  f.registration.openPaperModal(); await f.registration.uploadPaperRegistrationPdf({ name: 'fixture.pdf' });
  const callback = f.timers.values().next().value;
  f.state.leaving = true; await f.projection().suspendPaperUpdates();
  assert.equal(f.timers.size, 0);
  callback(); await tick(); assert.equal(reads, 0); assert.equal(f.timers.size, 0);
});

test('review status completion or rejection after leaving does not restart polling or read artifacts', async () => {
  for (const outcome of ['running', 'done', 'error', 'reject']) {
    const f = fixture(), response = deferred(); let reads = 0;
    f.api.get = () => { reads++; return response.promise; };
    const pending = f.reviews.loadReviewStatus('paper_owned');
    f.state.leaving = true; f.projection().suspendPaperUpdates();
    if (outcome === 'reject') response.reject(new Error('temporary failure'));
    else response.resolve({ job: { status: outcome, job_id: 'job_owned', review_id: 'review_owned' } });
    await pending;
    assert.equal(f.timers.size, 0, outcome); assert.equal(f.calls.length, 0, outcome);
    await f.reviews.loadReviewStatus('paper_owned'); assert.equal(reads, 1, outcome);
  }
});

test('review active status schedules one read and terminal status removes the poll', async () => {
  for (const status of ['done', 'error']) {
    const f = fixture(); let reads = 0;
    f.api.get = async () => ({ job: { status: ++reads === 1 ? 'running' : status, job_id: 'job_owned', review_id: 'review_owned' } });
    await f.reviews.loadReviewStatus('paper_owned'); assert.equal(f.timers.size, 1);
    await f.fire(); assert.equal(f.timers.size, 0); assert.equal(reads, 2);
    assert.equal(f.calls.length, status === 'done' ? 1 : 0);
  }
});

test('Paper snapshot refresh stops between requests when suspended and resumes a fresh snapshot', async () => {
  for (const reason of ['leave', 'hidden']) {
    const f = fixture(), response = deferred(); let collectors = 0;
    f.deps.loadCollectorJobs = async () => { collectors++; await response.promise; };
    const projection = f.projection(); projection.resumePaperUpdates(); await f.fire();
    if (reason === 'leave') f.state.leaving = true; else f.document.hidden = true;
    projection.suspendPaperUpdates(); response.resolve(); await tick();
    assert.equal(f.calls.length, 0, reason); assert.equal(f.timers.size, 0, reason);
    if (reason === 'hidden') {
      f.document.hidden = false; projection.resumePaperUpdates(); await f.fire();
      assert.equal(collectors, 2); assert.equal(f.calls.length, 4);
    }
  }
});

test('Paper hidden registration resumes its existing task and ignores the old query', async () => {
  const f = fixture(), old = deferred(), projection = f.projection(); let reads = 0, uploads = 0;
  f.api.postForm = async () => { uploads++; return { upload_id: 'upload_owned' }; };
  f.api.get = () => ++reads === 1 ? old.promise : Promise.resolve({ status:'done', metadata:{ title:'Current' } });
  f.registration.openPaperModal(); await f.registration.uploadPaperRegistrationPdf({name:'fixture.pdf'});
  await f.fire();
  f.document.hidden = true; projection.suspendPaperUpdates({includeDeferred:false, includePdf:false});
  f.registration.resumePaperRegistrationPolling?.(); assert.equal(f.timers.size, 0);
  f.document.hidden = false; projection.resumePaperUpdates();
  await f.fireDelay(1500); assert.equal(f.fields.get('title').value, 'Current');
  old.resolve({ status:'running', metadata:{ title:'Obsolete' } }); await tick();
  await f.fireDelay(100);
  assert.equal(f.fields.get('title').value, 'Current'); assert.equal(f.timers.size, 0);
  assert.equal(reads, 2); assert.equal(uploads, 1);
});

test('Paper hidden upload acknowledgement is retained for resume without another upload', async () => {
  const f = fixture(), reply = deferred(), projection = f.projection(); let uploads = 0, reads = 0;
  f.api.postForm = () => { uploads++; return reply.promise; };
  f.api.get = async () => { reads++; return {status:'done', metadata:{title:'Recovered'}}; };
  f.registration.openPaperModal(); const pending = f.registration.uploadPaperRegistrationPdf({name:'fixture.pdf'});
  f.document.hidden = true; projection.suspendPaperUpdates({includeDeferred:false, includePdf:false});
  reply.resolve({upload_id:'upload_owned'}); await pending;
  assert.equal(f.state.registrationUploadId, 'upload_owned'); assert.equal(f.timers.size, 0);
  f.document.hidden = false; projection.resumePaperUpdates(); await f.fireDelay(1500); await f.fireDelay(100);
  assert.equal(reads, 1); assert.equal(uploads, 1); assert.equal(f.timers.size, 0);
});

test('Paper hidden review pauses queued and in-flight reads and resumes without resubmission', async () => {
  const f = fixture(), old = deferred(), projection = f.projection(); let reads = 0;
  f.api.get = () => ++reads === 1 ? Promise.resolve({job:{status:'running',job_id:'job_owned'}})
    : reads === 2 ? old.promise : Promise.resolve({job:{status:'done',job_id:'job_owned',review_id:'review_owned'}});
  await f.reviews.loadReviewStatus('paper_owned');
  const queued = f.timers.values().next().value;
  f.document.hidden = true; projection.suspendPaperUpdates({includeDeferred:false, includePdf:false});
  assert.equal(f.timers.size, 0); queued(); await tick(); assert.equal(reads, 1);
  f.document.hidden = false; projection.resumePaperUpdates(); await f.fireDelay(100); await f.fireDelay(3000);
  f.document.hidden = true; projection.suspendPaperUpdates({includeDeferred:false, includePdf:false});
  f.document.hidden = false; projection.resumePaperUpdates(); await f.fireDelay(100); await f.fireDelay(3000);
  old.resolve({job:{status:'running',job_id:'job_owned'}}); await tick();
  assert.equal(f.state.reviewJob.status, 'done'); assert.equal(f.timers.size, 0);
  assert.equal(reads, 3); assert.equal(f.calls.filter(c=>c.name==='loadDetailSections').length, 1);
});
