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
      window.gitStatusFixture = { available: true, pull: { available: true, remote: 'origin', branch: 'main' }, remote: { available: true, remote: 'origin', branch: 'main' }, changes: [], branch: 'main', scope: '.', branches: [{ name: 'main', current: true }] };
      window.fixtureHistory = []; window.opened = []; window.diffs = []; window.gitCalls = [];
      window.posts = []; window.notices = []; window.refreshes = []; window.dirty = false; window.mode = 'delay';
      window.owner = PortalArticleWorkspaceGit.createGit({ ...depsFor(PortalArticleWorkspaceGit), $, state,
        ARTICLE_GIT_REFRESH_MS: 60000, esc: PortalUIPrimitives.escapeHtml, hasDirtyDocuments: () => dirty,
        openArticleFile: async path => opened.push(path),
        showToast: (text, tone) => notices.push({ text, tone }), refreshAfterPull: async id => refreshes.push(id),
        request: async (url, options = {}) => {
          if (options.method !== 'POST') return structuredClone(url.includes('/history') ? { commits: fixtureHistory } : gitStatusFixture);
          const payload = JSON.parse(options.body); posts.push({ url, payload });
          const action = url.split('/').at(-1);
          gitCalls.push(action);
          if (mode === 'scm') {
            if (action === 'stage') {
              gitStatusFixture.changes.forEach(item => {
                if (payload.paths.includes(item.path)) { item.staged = payload.staged; item.unstaged = !payload.staged; item.code = payload.staged ? 'M ' : ' M'; }
              });
              gitStatusFixture.staged_count = gitStatusFixture.changes.filter(item => item.staged).length;
            }
            if (action === 'commit') {
              gitStatusFixture.changes = []; gitStatusFixture.staged_count = 0;
              return { status: structuredClone(gitStatusFixture), commit: { short_sha: 'aaaaaaa' } };
            }
            if (action === 'discard') gitStatusFixture.changes = [];
            if (action === 'branch') gitStatusFixture.branch = payload.name;
            return { status: structuredClone(gitStatusFixture) };
          }
          if (mode === 'auth' && !payload.credential) throw Object.assign(new Error('auth'), { data: { auth_challenge: {
            host: 'example.invalid', username: 'git', secret_label: '访问令牌', help_text: 'Fixture credentials only',
          } } });
          if (mode === 'error') throw new Error('cannot fast-forward');
          if (mode === 'delay') await new Promise(resolve => { window.finishPull = resolve; });
          return { status: structuredClone(gitStatusFixture) };
        },
      });
      PortalArticleWorkspaceEvents.createEvents({ ...depsFor(PortalArticleWorkspaceEvents), $, state,
        pullArticleGit: owner.pullArticleGit, loadArticleGit: owner.loadArticleGit,
        handleArticleGitAction: owner.handleArticleGitAction, updateArticleGitStage: owner.updateArticleGitStage,
        commitArticleGit: owner.commitArticleGit, openArticleGitDiff: async (path, staged) => diffs.push({ path, staged }),
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

    await page.evaluate(async () => {
      mode = 'scm'; dirty = false;
      gitStatusFixture = { ...gitStatusFixture, pull: gitStatusFixture.remote, staged_count: 1,
        changes: [{ path: 'sections/main.tex', code: 'MM', staged: true, unstaged: true, untracked: false }],
      };
      fixtureHistory = [
        { sha: 'a', short_sha: 'aaaaaaa', subject: 'Merge paper', author: 'Author', parents: ['b', 'c'], refs: ['HEAD -> main'] },
        { sha: 'b', short_sha: 'bbbbbbb', subject: 'Local edit', author: 'Author', parents: ['d'], refs: [] },
        { sha: 'c', short_sha: 'ccccccc', subject: 'Remote edit', author: 'Resident', parents: ['d'], refs: ['origin/main'] },
        { sha: 'd', short_sha: 'ddddddd', subject: 'Initial', author: 'Author', parents: [], refs: [] },
      ];
      await owner.loadArticleGit({ includeHistory: true });
    });
    assert.equal(await page.locator('[data-git-diff]').count(), 2);
    await page.click('[data-git-diff-staged="true"]');
    await page.click('[data-git-diff-staged="false"]');
    assert.deepEqual(await page.evaluate(() => diffs), [{ path: 'sections/main.tex', staged: true }, { path: 'sections/main.tex', staged: false }]);
    assert.equal(await page.locator('#articleGitHistory circle').count(), 4);
    assert.match(await page.locator('#articleGitHistory').textContent(), /origin\/main/);
    assert.equal(await page.evaluate(() => PortalArticleWorkspaceGit.graphRows(fixtureHistory)[0].edges.length), 2);
    await page.click('[data-git-action="toggle-changes"]');
    assert.equal(await page.locator('[data-git-diff-staged="false"]').isVisible(), false);
    await page.click('[data-git-action="toggle-changes"]');
    await page.click('[data-git-action="unstage-all"]');
    await page.waitForFunction(() => gitStatusFixture.staged_count === 0);
    await page.click('[data-git-action="stage-all"]');
    await page.waitForFunction(() => gitStatusFixture.staged_count === 1);
    await page.fill('#articleGitMessage', 'Commit and push');
    await page.click('.articleGitCommitMenu summary');
    await page.click('[data-git-action="commit-push"]');
    await page.waitForFunction(() => gitCalls.at(-1) === 'push');
    assert.deepEqual(await page.evaluate(() => gitCalls.slice(-2)), ['commit', 'push']);
    assert.equal(await page.inputValue('#articleGitMessage'), '');
    for (const action of ['fetch', 'sync']) {
      await page.click(`[data-git-action="${action}"]`);
      await page.waitForFunction(action => gitCalls.at(-1) === action, action);
    }
    await page.click('[data-git-action="branch"]');
    const branchDialog = page.getByRole('dialog', { name: '切换或创建分支' });
    await branchDialog.locator('[name="newBranch"]').fill('paper/revision');
    await branchDialog.getByRole('button', { name: '切换', exact: true }).click();
    await page.waitForFunction(() => gitStatusFixture.branch === 'paper/revision');
    await page.evaluate(async () => {
      gitStatusFixture.changes = [{ path: 'new.tex', untracked: true, code: '??' }];
      await owner.loadArticleGit();
    });
    await page.click('[data-git-action="discard"]');
    const confirm = page.getByRole('alertdialog', { name: '放弃文件更改' });
    await confirm.getByRole('button', { name: '取消', exact: true }).click();
    assert.equal(await page.evaluate(() => gitCalls.at(-1)), 'branch');
    await page.click('[data-git-action="discard"]');
    await confirm.getByRole('button', { name: '放弃更改', exact: true }).click();
    await page.waitForFunction(() => gitCalls.at(-1) === 'discard');
    await page.evaluate(async () => {
      gitStatusFixture.changes = Array.from({ length: 15 }, (_, i) => ({ path: `sections/${['introduction', 'method', 'evaluation'][i % 3]}-${i}.tex`, staged: i < 3, unstaged: i >= 3, code: i < 3 ? 'M ' : ' M' }));
      gitStatusFixture.staged_count = 3;
      await owner.loadArticleGit({ includeHistory: true });
    });
    await page.waitForFunction(() => document.querySelectorAll('[data-git-diff]').length === 15);
    await page.evaluate(() => {
      owner.articleGitRefresh.stop();
      const panel = document.getElementById('articleGitPanel');
      document.body.replaceChildren(panel);
    });
    for (const name of ['portal_components.css', 'article_workspace.css', 'portal_design_system.css']) {
      await page.addStyleTag({ content: fs.readFileSync(path.join(staticRoot, name), 'utf8') });
    }
    await page.addStyleTag({ content: ':root { --panel: #fff; --text: #26313c; --muted: #687785; --border: #dce2e8; --accent: #3568b2; } #articleGitPanel { display: flex !important; flex-direction: column; position: fixed; inset: 12px auto 12px 12px; width: 360px; background: var(--panel); border: 1px solid var(--border); }' });
    for (const width of [260, 360]) {
      await page.locator('#articleGitPanel').evaluate((panel, width) => { panel.style.width = width + 'px'; }, width);
      assert.equal(await page.locator('#articleGitPanel').evaluate(panel => panel.scrollWidth <= panel.clientWidth), true, `panel fits ${width}px`);
    }
    await page.locator('#articleGitPanel').screenshot({ path: '/tmp/article-scm-panel.png' });
    assert.deepEqual(errors, []);
    console.log('PASS: SCM groups, diff sides, staging, commit/push, remote actions, graph, branches, discard, drafts, auth and stale results');
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
