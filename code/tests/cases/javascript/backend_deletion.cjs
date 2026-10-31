const assert = require('node:assert/strict');
const fs = require('node:fs');
const {createHarness} = require('./workbench_visibility_state.cjs');
const flush = async () => { for (let i = 0; i < 5; i++) await new Promise(resolve => setImmediate(resolve)); };

(async () => {
  const h = createHarness(), w = h.window, d = w.document;
  let panel;
  try {
    if (!w.PortalOverlay) w.eval(fs.readFileSync('management_portal/static/portal_overlay.js', 'utf8'));
    const calls = [];
    let accounts = [{account_id: 'my-ai', provider: 'provider_api', kind: 'api_profile', enabled: true,
      registration_version: 1, config: {api_id: 'my-api'}, registration: {version: 1, enabled: true, config: {api_id: 'my-api'}}}];
    let apis = [{api_id: 'my-api', provider: 'deepseek', default_model: 'test-model', enabled: true}];
    let rejection = null, finishDelete;
    w.PortalInstructionClient = {execute: async (name, args) => {
      calls.push({name, args});
      if (name === 'ListProviders') return {providers: [{provider: 'provider_api', kinds: ['api_profile']}]};
      if (name === 'ListProviderAccounts') return {accounts};
      if (name === 'ListModelApis') return {apis};
      if (name === 'ListBackendRoles') return {roles: [{role: 'default', account_id: 'my-ai', version: 1}]};
      if (name === 'DeleteProviderAccount' || name === 'DeleteModelApi') {
        if (rejection) throw Object.assign(new Error('conflict'), {code: 'instruction.conflict', details: rejection});
        await new Promise(resolve => { finishDelete = resolve; });
        if (name === 'DeleteProviderAccount') accounts = [];
        else apis = [];
        return {};
      }
      throw Error(name);
    }};
    w.eval(fs.readFileSync('management_portal/static/portal_management_backend.js', 'utf8')
      .replaceAll('export ', '') + '\nwindow.mountBackend = mount;');
    panel = await w.mountBackend(h.root, {me: {access: {permission_system: 'open'}}});
    const section = key => d.querySelector(`[data-backend-section="${key}"]`);
    const deleteButton = key => [...section(key).querySelectorAll('button')].find(b => b.textContent === '删除');
    const choose = async accept => {
      d.querySelector(`[data-portal-confirm-${accept ? 'accept' : 'cancel'}]`).click(); await flush();
    };
    deleteButton('accounts').click(); await flush();
    assert.equal(d.querySelector('[data-portal-confirmation]').hidden, false);
    await choose(false);
    assert(!calls.some(c => c.name === 'DeleteProviderAccount'), 'cancelling never deletes');
    assert(!/已保存|已删除/.test(d.querySelector('[data-backend-notice]').textContent), 'cancel must not claim success');
    assert(!deleteButton('accounts').disabled);

    rejection = {reason: 'account.role_in_use', roles: ['default', 'router_fast']};
    deleteButton('accounts').click(); await flush(); await choose(true);
    let alert = section('accounts').querySelector('[role=alert]');
    assert(alert, 'deletion errors stay beside the account, not only above the scrolled table');
    assert.match(alert.textContent, /default/); assert.match(alert.textContent, /router_fast/);
    assert.equal(d.activeElement, alert, 'a failed deletion focuses its visible explanation');
    assert.equal(accounts.length, 1);
    const rolesLink = [...section('accounts').querySelectorAll('button')].find(b => b.textContent === '查看 Backend 角色');
    assert(rolesLink); rolesLink.click(); await flush();
    assert(section('roles').closest('details').open, 'referencing roles can be reached from the refusal');

    rejection = {reason: 'account.busy', session_count: 2, execution_count: 1};
    deleteButton('accounts').click(); await flush(); await choose(true);
    alert = section('accounts').querySelector('[role=alert]');
    assert.match(alert.textContent, /2/); assert.match(alert.textContent, /1/);
    assert.equal(accounts.length, 1);
    assert(!calls.some(c => /RestartSession|CancelInput|UpdateBackendRole/.test(c.name)), 'no implicit migration or cascading');

    rejection = {reason: 'account.api_in_use', account_ids: ['my-ai']};
    deleteButton('apis').click(); await flush(); await choose(true);
    alert = section('apis').querySelector('[role=alert]');
    assert.match(alert.textContent, /my-ai/);
    assert.equal(apis.length, 1);
    assert([...section('apis').querySelectorAll('button')].some(b => b.textContent === '查看已接入的 AI'));

    rejection = null;
    deleteButton('accounts').click(); await flush(); await choose(true);
    const pending = calls.filter(c => c.name === 'DeleteProviderAccount').length;
    deleteButton('accounts').click(); await flush();
    assert.equal(calls.filter(c => c.name === 'DeleteProviderAccount').length, pending, 'pending delete cannot be duplicated');
    finishDelete(); await flush();
    assert.equal(section('accounts').querySelector('[data-backend-account]'), null);
    assert.match(d.querySelector('[data-backend-notice]').textContent, /已删除/);
    assert.equal(apis.length, 1, 'removing an account does not remove its API');
    deleteButton('apis').click(); await flush(); await choose(true);
    finishDelete(); await flush();
    assert.equal(apis.length, 0);
    panel.destroy();
    accounts = [{account_id: 'service', provider: 'kimi', kind: 'service', enabled: true},
      {account_id: 'registered-service', provider: 'kimi', kind: 'service', enabled: true, registration_version: 1},
      {account_id: 'ordinary', provider: 'provider_api', kind: 'api_profile', enabled: true, registration_version: 1}];
    apis = [{api_id: 'my-api'}];
    panel = await w.mountBackend(h.root, {me: {access: {allows: {UpdateProviderAccount: true}}}});
    assert([...section('accounts').querySelectorAll('.backendDelete button')].filter(b => !b.hidden).every(b => b.disabled));
    assert(deleteButton('apis').disabled, 'update permission alone does not grant delete permission');
    assert.match(deleteButton('apis').title, /权限/);
    panel.destroy();
    panel = await w.mountBackend(h.root, {me: {access: {allows: {DeleteProviderAccount: true, DeleteModelApi: true}}}});
    assert(deleteButton('accounts').disabled, 'service identity stays read-only');
    assert.match(deleteButton('accounts').title, /Provider/);
    assert(!section('accounts').querySelector('[data-backend-account="registered-service"] .backendDelete button').disabled,
      'registered service accounts use the same server-side reference checks as other accounts');
    assert(!section('accounts').querySelector('[data-backend-account="ordinary"] .backendDelete button').disabled);
    assert(!deleteButton('apis').disabled, 'specific delete permission is sufficient');
    console.log('PASS backend deletion confirmation, refusal, recovery, retry and non-cascading success');
  } finally {
    panel?.destroy(); await h.cleanup();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
