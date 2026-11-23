'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const cases = JSON.parse(fs.readFileSync(0, 'utf8'));
const root = path.resolve(__dirname, '../../..');
const staticRoot = path.join(root, 'management_portal/static');
const { chromium } = require(path.join(root, 'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const markup = fs.readFileSync(path.join(root, 'management_portal/article_workspace.html'), 'utf8');
const modules = ['portal_state', 'portal_text', 'portal_i18n', 'portal_ui_primitives',
  'portal_refresh_coordinator', 'article_workspace_git'];

(async () => {
  const server = http.createServer((req, res) => {
    const pathname = new URL(req.url, 'http://fixture').pathname;
    if (pathname === '/') {
      res.setHeader('Content-Type', 'text/html; charset=utf-8');
      return res.end('<!doctype html><body>' + modules.map(name => `<script src="/static/${name}.js"></script>`).join(''));
    }
    const target = path.resolve(staticRoot, '.' + pathname.slice('/static'.length));
    if (!pathname.startsWith('/static/') || !target.startsWith(staticRoot + path.sep) || !fs.existsSync(target)) {
      res.writeHead(404); return res.end();
    }
    res.setHeader('Content-Type', target.endsWith('.css') ? 'text/css; charset=utf-8' : 'text/javascript; charset=utf-8');
    res.end(fs.readFileSync(target));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    browser = await chromium.launch({ headless: true });
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    page.setDefaultTimeout(8000);
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(`http://127.0.0.1:${server.address().port}`);
    await page.addScriptTag({ url: '/static/vendor/monaco/vs/loader.js' });
    await page.evaluate(async ({ markup, cases }) => {
      const template = new DOMParser().parseFromString(markup, 'text/html').getElementById('articleEditorTemplate');
      document.body.append(template.content.cloneNode(true));
      const $ = id => document.getElementById(id);
      $('articleEditorPane').style.cssText = 'display:block';
      $('articleGitDiffEditor').style.cssText = 'width:1100px;height:600px';
      require.config({ paths: { vs: '/static/vendor/monaco/vs' } });
      await new Promise(resolve => require(['vs/editor/editor.main'], resolve));
      window.state = { selectedId: 'fixture', gitDiffModels: [], gitDiffSideBySide: true };
      window.cases = cases;
      window.caseIndex = 0;
      const deps = Object.fromEntries(PortalArticleWorkspaceGit.dependencies.map(key => [key, () => {}]));
      window.owner = PortalArticleWorkspaceGit.createGit({ ...deps, $, state,
        ARTICLE_GIT_REFRESH_MS: 60000, esc: PortalUIPrimitives.escapeHtml,
        monacoApi: () => monaco, languageForPath: () => 'plaintext',
        request: async () => cases[caseIndex].payload });
      $('articleGitDiffLayout').onclick = owner.toggleArticleGitDiffLayout;
    }, { markup, cases });
    for (let index = 0; index < cases.length; index++) {
      await page.evaluate(async index => {
        caseIndex = index;
        if (cases[index].kind === 'commit') await owner.openArticleGitCommit(cases[index].payload.commit.sha);
        else await owner.openArticleGitDiff('main.tex', cases[index].kind === 'staged');
        await state.gitDiffEditor.waitForDiff();
      }, index);
      assert.deepEqual(await page.evaluate(() => {
        const models = state.gitDiffEditor.getModel();
        return [models.original.getLinesContent(), models.modified.getLinesContent()];
      }), [cases[index].original, cases[index].modified], 'both versions preserve every line');
      for (const sideBySide of [true, false]) {
        if (await page.evaluate(() => state.gitDiffSideBySide) !== sideBySide) await page.click('#articleGitDiffLayout');
        // Measure actual rendered positions deep inside unchanged ranges; a folded
        // region maps these lines onto the same position instead of consecutive rows.
        for (const line of [1, 120, 240]) {
          await page.evaluate(line => state.gitDiffEditor.getModifiedEditor().revealLineInCenter(line), line);
          await page.waitForFunction(line => {
            const editor = state.gitDiffEditor.getModifiedEditor();
            const next = Math.min(240, line + 1), previous = next - 1;
            return editor.getTopForLineNumber(next) - editor.getTopForLineNumber(previous) > 10
              && editor.getDomNode().innerText.replace(/\u00a0/g, ' ').includes(`Unchanged manuscript line ${line}`);
          }, line).catch(async error => {
            const rendered = await page.evaluate(line => {
              const editor = state.gitDiffEditor.getModifiedEditor();
              return { line, top: editor.getTopForLineNumber(line),
                height: editor.getLayoutInfo().height, visible: editor.getVisibleRanges(),
                text: editor.getDomNode().innerText.slice(0, 250) };
            }, line);
            throw new Error(`${cases[index].kind}/${sideBySide}: ${JSON.stringify(rendered)}\n${error.message}`);
          });
        }
      }
    }
    await page.evaluate(async () => {
      cases[caseIndex].payload.truncated = true;
      await owner.openArticleGitCommit(cases[caseIndex].payload.commit.sha);
    });
    assert.match(await page.locator('#articleGitDiffTitle').textContent(), /仅显示部分内容/);
    assert.deepEqual(errors, []);
    console.log('Article Git: complete working/index/commit versions remain visible in side-by-side and inline views.');
  } finally {
    await browser?.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
