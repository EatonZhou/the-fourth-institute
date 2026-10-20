// Real Repo page/controller and HTTP backend; only the surrounding Portal shell
// is stubbed. Target LAN/authenticated full-page acceptance remains separate.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const root = path.resolve(__dirname, '../../..');

(async () => {
  const [base, repoId, gitId] = process.argv.slice(2);
  const browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage();
    page.setDefaultTimeout(8000);
    const errors = [], requests = [];
    page.on('pageerror', error => {errors.push(error.message); console.error('Page error:', error.message);});
    page.on('request', request => requests.push(new URL(request.url()).pathname));
    await page.addInitScript(() => {
      const tag = (parts, ...values) => parts.reduce((out, part, i) => out + part + (values[i] ?? ''), '');
      const noop = () => {};
      const history = method => patch => {
        const url = new URL(location.href);
        for (const [key, value] of Object.entries(patch)) value ? url.searchParams.set(key, value) : url.searchParams.delete(key);
        window.history[method]({}, '', url);
      };
      window.PortalI18n = {html: tag, text: tag, t: String, markup: String};
      window.PortalUIPrimitives = {
        escapeHtml: value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch])),
        objectOf: value => value && typeof value === 'object' ? value : {},
        iconHtml: () => '', asyncStateHtml: ({message}) => `<p>${message}</p>`,
      };
      window.PortalResourceDetail = {
        createDetailHistory: () => ({push: history('pushState'), replace: history('replaceState')}),
        createGuest: () => ({setResource: noop, ready: noop, failed: noop}),
      };
      window.PortalComponentRecovery = {create: ({onReady}) => ({run: onReady, guard: fn => fn(), isUnavailable: () => false})};
      window.PortalFormControls = {
        bindFilterDropdown: () => ({open: noop, close: noop}),
        bindFilterSummary: () => ({sync: noop}),
      };
      window.PortalAPI = {get: async url => {
        const response = await fetch(url); const data = await response.json();
        if (!response.ok) throw Error(data.error || `HTTP ${response.status}`);
        return data;
      }};
      window.PortalUI = {
        createDialogController: () => ({open: noop, close: noop}), toast: noop,
        formatDateTime: value => String(value || ''), formatBytes: value => String(value || 0),
      };
      window.PortalPreviewContract = {normalizeKind: ({path}) => /\.md$/.test(path) ? 'markdown' : 'text'};
      window.PortalWorkbench = {
        model: {split: noop, stack: noop}, create: () => ({destroy: noop}),
        createPanel: (_kind, {root}) => ({destroy: noop, setDocument: async doc => {root.textContent = doc.content;}}),
      };
      window.MD = {registerImagePathResolver: noop, render: String};
    });
    const html = fs.readFileSync(path.join(root, 'management_portal/repo_workspace.html'), 'utf8')
      .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '')
      .replace('</body>', '<script src="/static/repo_workspace.js"></script></body>');
    await page.route('**/resource/repo?*', route => route.fulfill({contentType: 'text/html', body: html}));
    const workspace = page.locator('#repoWorkspace');
    // A stale Git-only deep link must switch to source before any detail fetch.
    for (const tab of ['commits', 'branches', 'changes', 'settings']) {
      await page.goto(`${base}/resource/repo?item=${repoId}&tab=${tab}`);
      await workspace.locator('[data-open-directory="architecture"]').waitFor().catch(async error => {
        console.error('Workspace:', await workspace.innerHTML()); throw error;
      });
      assert.equal(await workspace.locator('[data-repo-tab]').count(), 1);
      assert.equal(await workspace.locator('[data-repo-sync], [data-repo-discard], #repoBranchPicker, [data-repo-action="new-file"]').count(), 0);
      assert.equal(await page.locator('#repoAutoCommitAll').isVisible(), false);
      assert(!await workspace.textContent().then(s => /detached HEAD|尚无提交|工作区干净/.test(s)));
    }
    await workspace.locator('[data-open-directory="architecture"]').click();
    await workspace.locator('[data-open-file="architecture/modules.yaml"]').click();
    await page.waitForFunction(() => document.querySelector('#repoFilePreviewHost')?.textContent === 'modules: []\n');
    assert.equal(await workspace.locator('[data-file-history], [data-file-blame], [data-edit-file], [data-delete-file]').count(), 0);
    await page.keyboard.press('b');
    await page.keyboard.press('t');
    await page.keyboard.press('w');
    await page.waitForTimeout(50);
    assert.equal(await workspace.locator('#repoFilePreviewHost').textContent(), 'modules: []\n');
    assert(!requests.some(url => new RegExp(`/repos/${repoId}/workspace/(commits|credentials|stats|search|blame|diff)$`).test(url)));
    // Browser back and direct file URL preserve source navigation.
    await page.goBack();
    await workspace.locator('[data-open-file="architecture/modules.yaml"]').waitFor();
    await page.goto(`${base}/resource/repo?item=${repoId}&file=architecture%2Fmodules.yaml`);
    await page.waitForFunction(() => document.querySelector('#repoFilePreviewHost')?.textContent === 'modules: []\n');
    // The ordinary Git page still exposes branch/history/edit capabilities.
    await page.goto(`${base}/resource/repo?item=${gitId}`);
    await workspace.locator('#repoBranchPicker').waitFor();
    assert.equal(await workspace.locator('[role="tab"]').count(), 5);
    await workspace.locator('.repoTreeRow[data-open-file="README.txt"]').click();
    await workspace.locator('[data-file-history]').waitFor();
    assert.equal(await workspace.locator('[data-edit-file]').count(), 1);
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({status:'passed',scope:'isolated Chromium + real Repo HTTP backend',source_navigation:true,git_controls_hidden:true,stale_git_tabs:4,plain_git:true,requests}));
  } finally { await browser.close(); }
})().catch(error => {console.error(error); process.exitCode = 1;});
