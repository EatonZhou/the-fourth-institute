'use strict';
// Actual Article markup, event wiring, Git owner and credential dialog.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const root = path.resolve(__dirname, '../../..');
const staticRoot = path.join(root, 'management_portal/static');
const { chromium } = require(path.join(root, 'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const modules = ['portal_state', 'portal_text', 'portal_i18n', 'portal_ui_primitives', 'portal_overlay', 'portal_ui',
  'portal_refresh_coordinator', 'portal_command_registry', 'article_workspace_git', 'article_workspace_events'];
const html = fs.readFileSync(path.join(root, 'management_portal/article_workspace.html'), 'utf8')
  .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '')
  .replace(/<link\b[^>]*>/gi, '')
  .replace('</body>', modules.map(name => `<script src="/static/${name}.js"></script>`).join('') + '</body>');

(async () => {
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://fixture');
    if (url.pathname === '/') { res.setHeader('Content-Type', 'text/html; charset=utf-8'); return res.end(html); }
    const name = path.basename(url.pathname, '.js');
    if (!modules.includes(name)) { res.writeHead(404); return res.end(); }
    res.setHeader('Content-Type', 'text/javascript'); res.end(fs.readFileSync(path.join(staticRoot, name + '.js')));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    browser = await chromium.launch({ headless: true });
    const page = await browser.newPage();
    page.setDefaultTimeout(8000);
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(`http://127.0.0.1:${server.address().port}`);
    await page.evaluate(async () => {
      const $ = id => document.getElementById(id);
      const depsFor = module => Object.fromEntries(module.dependencies.map(key => [key, () => {}]));
      window.state = { selectedId: 'article_fixture', sideView: 'git', gitHistory: [] };
      window.gitStatusFixture = { available: true, pull: { available: true, remote: 'origin', branch: 'main' }, changes: [], branch: 'main' };
      window.posts = []; window.notices = []; window.refreshes = []; window.dirty = false; window.mode = 'delay';
      window.owner = PortalArticleWorkspaceGit.createGit({ ...depsFor(PortalArticleWorkspaceGit), $, state,
        ARTICLE_GIT_REFRESH_MS: 60000, esc: PortalUIPrimitives.escapeHtml, hasDirtyDocuments: () => dirty,
        showToast: (text, tone) => notices.push({ text, tone }), refreshAfterPull: async id => refreshes.push(id),
        request: async (url, options = {}) => {
          if (!url.endsWith('/pull')) return url.includes('/history') ? { commits: [] } : gitStatusFixture;
          const payload = JSON.parse(options.body); posts.push({ url, payload });
          if (mode === 'auth' && !payload.credential) throw Object.assign(new Error('auth'), { data: { auth_challenge: {
            host: 'example.invalid', username: 'git', secret_label: '访问令牌', help_text: 'Fixture credentials only',
          } } });
          if (mode === 'error') throw new Error('cannot fast-forward');
          if (mode === 'delay') await new Promise(resolve => { window.finishPull = resolve; });
          return { status: gitStatusFixture };
        },
      });
      PortalArticleWorkspaceEvents.createEvents({ ...depsFor(PortalArticleWorkspaceEvents), $, state,
        pullArticleGit: owner.pullArticleGit, loadArticleGit: owner.loadArticleGit,
      });
      await owner.loadArticleGit({ includeHistory: true });
    });
    await page.click('#articleGitPull');
    assert.equal(await page.locator('#articleGitPull').isDisabled(), true);
    assert.equal(await page.locator('#articleGitPullLabel').textContent(), '正在拉取…');
    await page.evaluate(() => owner.pullArticleGit());
    assert.equal(await page.evaluate(() => posts.length), 1, 'duplicate pull suppressed');
    await page.evaluate(() => { dirty = true; finishPull(); });
    await page.waitForFunction(() => notices.length === 1);
    assert.match(await page.evaluate(() => notices[0].text), /草稿/);
    assert.deepEqual(await page.evaluate(() => refreshes), ['article_fixture']);
    await page.click('#articleGitPull');
    assert.equal(await page.evaluate(() => posts.length), 1, 'unsaved draft blocks network');

    await page.evaluate(() => { dirty = false; mode = 'error'; });
    await page.click('#articleGitPull');
    await page.waitForFunction(() => notices.at(-1).text.includes('fast-forward'));
    assert.equal(await page.locator('#articleGitPull').isDisabled(), false);

    await page.evaluate(() => { mode = 'auth'; });
    await page.click('#articleGitPull');
    const dialog = page.getByRole('dialog', { name: 'Git 认证' });
    await dialog.waitFor();
    assert.equal(await dialog.locator('[name="secret"]').getAttribute('type'), 'password');
    await dialog.locator('[name="secret"]').fill('fixture-only');
    await dialog.locator('[name="remember"]').check();
    await dialog.getByRole('button', { name: '拉取', exact: true }).click();
    await page.waitForFunction(() => refreshes.length === 2);
    assert.equal(await page.locator('[name="secret"]').count(), 0, 'credential removed from DOM');
    assert.equal(await page.evaluate(() => posts.at(-1).payload.remember), true);

    await page.evaluate(() => { mode = 'delay'; });
    await page.click('#articleGitPull');
    await page.waitForFunction(() => document.getElementById('articleGitPull').disabled);
    await page.evaluate(() => { state.selectedId = 'article_other'; window.noticesBefore = notices.length; finishPull(); });
    await page.waitForTimeout(50);
    assert.equal(await page.evaluate(() => notices.length === noticesBefore), true);
    assert.equal(await page.evaluate(() => refreshes.length), 2, 'old completion cannot reload another Article');
    await page.evaluate(async () => {
      gitStatusFixture = { ...gitStatusFixture, pull: { available: false, reason: '尚未配置远端仓库。' } };
      await owner.loadArticleGit();
    });
    assert.equal(await page.locator('#articleGitPull').isDisabled(), true);
    assert.match(await page.locator('#articleGitPullHint').textContent(), /远端/);
    assert.deepEqual(errors, []);
    console.log('PASS: pull click, busy state, drafts, errors, credentials, stale completion and unavailable reason');
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
