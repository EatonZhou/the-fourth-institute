const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
}

function fixture() {
  const timers = new Map();
  let timerId = 0;
  const window = {
    PortalI18n: { t: text => text },
    PortalUI: { confirmAction: async () => true, toast() {} },
  };
  const context = {
    window,
    document: { hidden: false },
    FormData: class { append() {} },
    setTimeout: callback => { timers.set(++timerId, callback); return timerId; },
    clearTimeout: id => timers.delete(id),
  };
  for (const file of ['paper_record.js', 'paper_reviews.js']) {
    vm.runInNewContext(fs.readFileSync(path.join(__dirname,
      '../../../management_portal/static', file), 'utf8'), context);
  }
  const state = { selectedId: 'paper_a', detail: { paper: { id: 'paper_a' } }, mode: 'review' };
  const calls = { merged: [], sections: [], requests: [], modes: [] };
  const api = { request: async () => ({}), postForm: async () => ({}) };
  api.get = (...args) => api.request(...args);
  const deps = Object.fromEntries([...window.PortalPaperRecord.dependencies,
    ...window.PortalPaperReviews.dependencies].map(key => [key, () => {}]));
  Object.assign(deps, {
    state, paperApi: api, loadLibrary: async () => {},
    mergeDetailPayload: payload => { calls.merged.push(payload); state.detail = payload; },
    loadDeepReadStatus: async () => {},
    loadDetailSections: async (id, sections) => { calls.sections.push({ id, sections }); },
    setMode: mode => { state.mode = mode; calls.modes.push(mode); },
  });
  const switchPaper = () => {
    state.selectedId = 'paper_b';
    state.detail = { paper: { id: 'paper_b' } };
    state.deepReadStatus = { status: 'idle', paper_id: 'paper_b' };
    state.expStatus = { status: 'idle', paper_id: 'paper_b' };
    state.mode = 'reader';
  };
  return { state, api, calls, timers, switchPaper,
    record: window.PortalPaperRecord.createRecord(deps),
    reviews: window.PortalPaperReviews.createReviews(deps) };
}

test('PDF upload responses do not reopen a previously selected paper', async () => {
  for (const action of ['upload']) {
    const f = fixture();
    const response = deferred();
    const started = deferred();
    let requested;
    const send = (url, options) => {
      requested = { url, options };
      started.resolve();
      return response.promise;
    };
    f.api.postForm = send;
    f.api.request = send;
    const pending = f.record.uploadCurrentPaperPdf({ name: 'fixture.pdf' });
    await started.promise;
    assert.ok(requested.url.includes('upload-pdf'));
    f.switchPaper();
    response.resolve({ paper: { id: 'paper_a' } });
    await pending;
    assert.equal(f.state.selectedId, 'paper_b', action);
    assert.equal(f.state.detail.paper.id, 'paper_b', action);
    assert.equal(f.calls.merged.length, 0, action);
    assert.equal(f.state.pdfUploadInFlight, false);
  }
});

test('PDF upload still updates the same paper and release busy state on rejection', async () => {
  for (const action of ['upload']) {
    const f = fixture();
    const updated = { paper: { id: 'paper_a', title: 'Updated' } };
    f.api.postForm = f.api.request = async () => updated;
    const run = () => f.record.uploadCurrentPaperPdf({ name: 'fixture.pdf' });
    await run();
    assert.equal(f.state.detail, updated);
    f.api.postForm = f.api.request = async () => { throw new Error('Permission denied'); };
    await assert.rejects(run(), /Permission denied/);
    assert.equal(f.state.pdfUploadInFlight, false);
    assert.equal(f.state.detail, updated);
  }
});

test('late Deep Read and Exp starts cannot replace another paper status or mode', async () => {
  for (const action of ['startDeepRead', 'createExp']) {
    const f = fixture();
    const response = deferred();
    f.api.request = (url, options) => {
      assert.equal(JSON.parse(options.body).paper_id, 'paper_a');
      return response.promise;
    };
    const pending = f.reviews[action]();
    f.switchPaper();
    response.resolve({ paper_id: 'paper_a', status: 'queued' });
    await pending;
    assert.equal(f.state.deepReadStatus.paper_id, 'paper_b');
    assert.equal(f.state.expStatus.paper_id, 'paper_b');
    assert.equal(f.state.mode, 'reader');
  }
});

test('late completed status cannot refresh artifacts on another paper', async () => {
  for (const action of ['refreshDeepReadProjection', 'refreshExpProjection']) {
    const f = fixture();
    f.state.deepReadStatus = f.state.expStatus = { status: 'running' };
    const response = deferred();
    f.api.request = () => response.promise;
    const pending = f.reviews[action]();
    f.switchPaper();
    response.resolve({ paper_id: 'paper_a', status: 'done' });
    await pending;
    assert.equal(f.calls.sections.length, 0);
  }
});

test('review permission and missing-resource errors stop polling without claiming task failure', async () => {
  for (const status of [401, 403, 404]) {
    const f = fixture();
    const job = { job_id: 'review_a', status: 'running' };
    f.state.reviewJob = job;
    f.state.reviewInFlight = true;
    f.state.reviewPollTimer = 7;
    f.timers.set(7, () => {});
    const error = Object.assign(new Error(`HTTP ${status}`), { status });
    f.api.request = async () => { throw error; };
    await assert.rejects(f.reviews.loadReviewStatus('paper_a'), error);
    assert.equal(f.timers.size, 0, `HTTP ${status}`);
    assert.equal(f.state.reviewPollTimer, 0);
    assert.equal(f.state.reviewPollError, error.message);
    assert.equal(f.state.reviewJob, job);
  }
});

test('transient review errors retry status only and recover to the returned job', async () => {
  const f = fixture();
  let count = 0;
  f.api.request = async url => {
    assert.ok(url.includes('/reviews/status?'));
    if (++count === 1) throw Object.assign(new Error('Temporarily unavailable'), { status: 503 });
    return { job: { job_id: 'review_a', status: 'error', error: 'Model failed' } };
  };
  await assert.rejects(f.reviews.loadReviewStatus('paper_a'), /Temporarily/);
  assert.equal(f.timers.size, 1);
  await [...f.timers.values()][0]();
  assert.equal(count, 2);
  assert.equal(f.timers.size, 0);
  assert.equal(f.state.reviewJob.error, 'Model failed');
  assert.equal(f.state.reviewInFlight, false);
});
