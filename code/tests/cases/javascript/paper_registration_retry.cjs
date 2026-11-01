const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { webcrypto } = require('node:crypto');

test('failed submission retains its key, edits and new forms get fresh keys', async () => {
  const window = { crypto: webcrypto };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,
    '../../../management_portal/static/paper_registration.js'), 'utf8'), {
    window, crypto: webcrypto, PortalUIPrimitives: { randomUUID: () => webcrypto.randomUUID() },
    FormData: class { constructor(form) { this.form = form; } entries() { return Object.entries(this.form.values); } },
    clearTimeout,
  });
  const form = { values: { title: 'Same title' }, reset() { this.values = { title: 'Same title' }; } };
  const requests = [];
  let fail = true;
  const state = { registrationEditedFields: new Set() };
  const registration = window.PortalPaperRegistration.createRegistration({
    $: name => name === 'paperForm' ? form : null, state,
    paperDialog: { open() {}, close() {} },
    paperApi: { async request(url, options) {
      requests.push(options);
      if (fail) throw new Error('response lost');
      return { paper: { id: 'paper_0123456789ab' } };
    } },
    loadLibrary: async () => {}, mergeDetailPayload() {}, setMode() {}, syncPaperLocation() {},
  });
  const submit = () => registration.submitPaper({ preventDefault() {}, currentTarget: form });
  const key = index => requests[index].headers?.['Idempotency-Key'];
  registration.openPaperModal();
  await assert.rejects(submit(), /response lost/);
  assert.ok(key(0));
  await assert.rejects(submit(), /response lost/);
  assert.equal(key(0), key(1));
  form.values.title = 'Changed title';
  await assert.rejects(submit(), /response lost/);
  assert.notEqual(key(1), key(2));
  fail = false;
  await submit();
  assert.equal(key(2), key(3));
  registration.openPaperModal();
  form.values.title = 'Changed title';
  await submit();
  assert.notEqual(key(3), key(4));
  assert.equal(requests[3].body, requests[4].body);
});
