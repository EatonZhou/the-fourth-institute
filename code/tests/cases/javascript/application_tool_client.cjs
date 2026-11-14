const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const calls = [];
let responses = [];
const window = {
  PortalI18n: {message: value => value, requestLanguage: () => 'zh'},
  PortalAPI: {requestResult: async (endpoint, options) => {
    calls.push({endpoint, options});
    const next = responses.shift();
    if (next instanceof Error) throw next;
    return next;
  }},
  dispatchEvent() {}, location: {assign() {}},
};
const context = vm.createContext({window, console, AbortController,
  crypto: require('node:crypto').webcrypto, CustomEvent: class {},
  setTimeout: callback => setTimeout(callback, 0), clearTimeout});
vm.runInContext(fs.readFileSync(path.join(__dirname,
  '../../../management_portal/static/portal_instruction_client.js'), 'utf8'), context);
const ok = result => ({response: {ok: true, status: 200}, payload: {status: 'committed', result}});

(async () => {
  responses = [ok({users: []})];
  await window.PortalInstructionClient.execute('ListUsers');
  assert.equal(calls[0].endpoint, '/api/instructions/execute');
  assert.equal(calls[0].options.body.instruction, 'ListUsers');
  responses = [{response: {ok: false, status: 503}, payload: {
    status: 'failed', error: {code: 'instruction.projection_pending'}}}, ok({pulse: {}})];
  await window.PortalApplicationToolClient.execute('application:pulse.publish',
    {source_period: 'daily', source_key: 'day', item_id: 'item', published: true},
    {idempotencyKey: 'one-publish', expectedVersion: 7});
  assert.equal(calls.length, 3);
  for (const call of calls.slice(1)) {
    assert.equal(call.endpoint, '/api/application-tools/execute');
    assert.equal(call.options.body.schema, 'tfi.application-tool-call.v1');
    assert.equal(call.options.body.tool, 'application:pulse.publish');
    assert.equal(call.options.body.idempotency_key, 'one-publish');
    assert.equal(call.options.body.expected_version, 7);
    assert(!('instruction' in call.options.body));
  }
  responses = [{response: {ok: false, status: 403}, payload: {status: 'failed',
    call_id: 'operation-denied', error: {code: 'instruction.forbidden', message: 'revoked'}}}];
  await assert.rejects(window.PortalApplicationToolClient.execute('application:pulse.read',
    {user_id: 'alice'}), error => error.code === 'instruction.forbidden'
      && error.instructionId === 'operation-denied');
  assert.equal(calls.length, 4);
  console.log('application tool page transport: passed');
})().catch(error => {console.error(error); process.exitCode = 1;});
