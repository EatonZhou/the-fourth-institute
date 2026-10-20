'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');

async function main() {
  const ids = ['Content', 'Meta', 'Query', 'Refresh', 'Critical', 'Run', 'Verify',
    'RunDetail', 'RunTitle', 'RunBody', 'RunClose'];
  const dom = new JSDOM(ids.map(id => `<div id="resourceBackup${id}"></div>`).join(''), {
    url: 'https://backup.fixture.invalid/', runScripts: 'outside-only',
  });
  const { window } = dom;
  window.PortalCompatibility = { identifier: name => `portal-${name}` };
  const template = (parts, ...values) => parts.reduce((out, part, i) => out + part + (values[i] ?? ''), '');
  window.PortalI18n = { t: text => text, html: template, text: template };
  window.PortalUIPrimitives = {
    escapeHtml(value) {
      const element = window.document.createElement('div');
      element.textContent = String(value ?? '');
      return element.innerHTML.replace(/"/g, '&quot;');
    },
    renderAsyncState() {},
    asyncStateHtml: () => '<div class="fixtureEmptyState"></div>',
    statusHtml: () => '<span class="fixtureStatus"></span>',
  };
  window.PortalUI = {
    createDialogController: () => ({ close() {} }),
    loadCurrentUser: () => Promise.resolve({ id: 'fixture' }),
    formatBytes: value => String(value ?? '—'),
    formatDateTime: value => String(value ?? ''),
  };
  let refresh;
  window.PortalRefreshCoordinator = {
    createSharedFetcher: () => ({}),
    create(options) {
      refresh = options.apply;
      return { start() {}, stop() {}, destroy() {}, refresh: () => Promise.resolve() };
    },
  };
  const source = path.resolve(__dirname, '../../../management_portal/static/resource_backup.js');
  try {
    window.eval(fs.readFileSync(source, 'utf8'));
    await Promise.resolve();
    const overview = {
      health: { status: 'healthy' }, target: {},
      policy: { profiles: [], retention: {}, verification: {} },
      coverage: [], warnings: [], recent_runs: [],
      permissions: { can_run: false, can_verify: false },
    };
    const link = () => window.document.querySelector('.resourceBackupRestore a');
    refresh({ ...overview, restore_guide: { resource_id: '', url: '' } });
    assert.equal(link(), null, 'unconfigured guide must not navigate to the current page');
    assert.equal(window.document.getElementById('resourceBackupCritical').disabled, true);
    assert.equal(window.document.getElementById('resourceBackupVerify').disabled, true);

    const url = '/resource/knowledge?item=info_777777777777';
    refresh({ ...overview, restore_guide: { resource_id: 'info_777777777777', url } });
    assert.equal(link().getAttribute('href'), url);
    assert.equal(link().textContent.trim(), '打开恢复指南');

    refresh({ ...overview, restore_guide: { resource_id: '', url: '' } });
    assert.equal(link(), null, 'refresh removes a previously configured link');
    assert.match(window.document.querySelector('.resourceBackupRestore').textContent, /Backup Center/);
    window.__PORTAL_LANGUAGE__ = 'en';
    for (const file of ['portal_text.js', 'portal_translations.js', 'portal_i18n.js']) {
      window.eval(fs.readFileSync(path.resolve(__dirname, '../../../management_portal/static', file), 'utf8'));
    }
    refresh({ ...overview,
      restore_guide: { resource_id: '', url: '' },
      health: { status: 'healthy', label: '备份健康', summary: '关键状态和 Resource 内容都有可用恢复点。' },
      target: { label: '加密 Restic 仓库', name: 'Internal repository name' },
      policy: { ...overview.policy, profiles: [{ id: 'custom', label: '用户配置的策略', description: '用户说明', status: 'healthy' }] },
      recent_runs: [{ run_id: 'fixture-run', profile_label: '仓库校验', summary: '校验已完成', status: 'completed' }],
    });
    const rendered = window.document.getElementById('resourceBackupContent').textContent;
    assert.match(rendered, /Backup health/);
    assert.match(rendered, /Recovery points are available/);
    assert.match(rendered, /Encrypted Restic repository/);
    assert.match(rendered, /Repository verification/);
    assert.match(rendered, /Verification complete/);
    assert.match(rendered, /用户配置的策略/);
    assert.match(rendered, /用户说明/);
    console.log('actual backup renderer: absent/configured/removed guide and permission state passed');
  } finally {
    window.close();
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
