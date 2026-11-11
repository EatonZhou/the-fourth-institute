'use strict';
// Actual Article event wiring and selection readers, including Monaco.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const root = path.resolve(__dirname, '../../..');
const staticRoot = path.join(root, 'management_portal/static');
const { chromium } = require(path.join(root, 'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const modules = ['portal_command_registry.js', 'article_workspace_chat.js', 'article_workspace_monaco.js', 'article_workspace_events.js'];
const html = fs.readFileSync(path.join(root, 'management_portal/article_workspace.html'), 'utf8')
  .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '')
  .replace(/<link\b[^>]*>/gi, '')
  .replace('</body>', modules.map(name => `<script src="/static/${name}"></script>`).join('') + '</body>');

(async () => {
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://fixture');
    if (url.pathname === '/') { res.setHeader('Content-Type', 'text/html; charset=utf-8'); return res.end(html); }
    const rel = url.pathname.slice('/static/'.length);
    if (!url.pathname.startsWith('/static/') || (!modules.includes(rel) && !rel.startsWith('vendor/monaco/')) || rel.includes('..')) {
      res.writeHead(404); return res.end();
    }
    try {
      res.setHeader('Content-Type', rel.endsWith('.css') ? 'text/css' : 'text/javascript');
      res.end(fs.readFileSync(path.join(staticRoot, rel)));
    } catch { res.writeHead(404); res.end(); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    browser = await chromium.launch({ headless: true });
    const page = await browser.newPage();
    page.setDefaultTimeout(8000);
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const origin = `http://127.0.0.1:${server.address().port}`;
    await page.route('**/*', route => route.request().url().startsWith(origin + '/') ? route.continue() : route.abort());
    await page.goto(origin);
    await page.evaluate(() => {
      window.PortalI18n = { t: text => text, text: parts => parts.join('') };
      window.references = []; window.notices = []; window.openings = 0; window.draft = 'Keep my question';
      window.holdChat = false;
      window.state = { selectedId: 'article_fixture', openFile: 'main.tex', source: '', editorPreferences: { tabSize: 2 } };
      const $ = id => document.getElementById(id);
      const controller = { draftText: () => draft, setDraftText: value => { draft = value; }, addReference: ref => references.push(ref) };
      state.chatController = controller;
      const depsFor = module => Object.fromEntries(module.dependencies.map(key => [key, () => {}]));
      const monaco = PortalArticleWorkspaceMonaco.createMonaco({ ...depsFor(PortalArticleWorkspaceMonaco), $, state });
      const host = PortalArticleWorkspaceChat.createChat({ ...depsFor(PortalArticleWorkspaceChat), $, state,
        selectedArticle: () => ({ title: 'Fixture', canonical_root: '/fixture' }), articleProjects: () => ['fixture'],
        activeDocument: () => ({ revision: 'revision-1' }), documentIsDirty: () => true,
        editorPosition: monaco.editorPosition, editorSelectionContext: monaco.editorSelectionContext, languageForPath: () => 'latex',
        setSideView: async view => {
          if (view !== 'chat') throw new Error('Unexpected view');
          openings++;
          if (holdChat) await new Promise(resolve => { window.releaseChat = resolve; });
          return controller;
        },
      });
      PortalArticleWorkspaceEvents.createEvents({ ...depsFor(PortalArticleWorkspaceEvents), $, state,
        bringCurrentContextToChat: host.bringCurrentContextToChat, showToast: error => notices.push(String(error)),
      });
      $('articleSendContext').disabled = false;
      $('articleSourceEditor').disabled = false;
      $('articleSourceEditor').style.display = 'block';
      $('articleSourceEditor').value = 'first line\nselected words\nlast line';
      $('articleSourceEditor').setSelectionRange(11, 25);
      $('articleSourceEditor').focus();
    });
    await page.keyboard.press('Control+Alt+Enter');
    await page.waitForFunction(() => references.length === 1);
    let context = await page.evaluate(() => JSON.parse(references[0].text));
    assert.equal(context.file.selection.text, 'selected words');
    assert.equal(context.file.selection.start_line, 2);
    assert.equal(context.file.path, 'main.tex');
    assert.equal(context.file.revision, 'revision-1');
    assert.equal(context.article_id, 'article_fixture');
    assert.equal(await page.evaluate(() => draft), 'Keep my question');
    assert.match(await page.locator('#articleSendContext').getAttribute('title'), /Ctrl\+Alt\+Enter/);
    assert.match(await page.locator('#articleSendContext').getAttribute('aria-keyshortcuts'), /Meta\+Alt\+Enter/);

    // Capture at invocation, before an asynchronous side-panel load changes focus/file.
    await page.evaluate(() => { holdChat = true; });
    await page.keyboard.press('Meta+Alt+Enter');
    await page.waitForFunction(() => Boolean(window.releaseChat));
    await page.keyboard.press('Meta+Alt+Enter');
    await page.evaluate(() => {
      state.openFile = 'other.tex'; document.getElementById('articleSourceEditor').setSelectionRange(0, 5);
      holdChat = false; releaseChat();
    });
    await page.waitForFunction(() => references.length === 2);
    context = await page.evaluate(() => JSON.parse(references[1].text));
    assert.equal(context.file.path, 'main.tex');
    assert.equal(context.file.selection.text, 'selected words');
    assert.equal(await page.evaluate(() => openings), 2, 'pending command cannot add twice');

    await page.evaluate(() => {
      const source = document.getElementById('articleSourceEditor');
      for (const extra of [{ repeat: true }, { isComposing: true }]) {
        source.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', ctrlKey: true, altKey: true, bubbles: true, cancelable: true, ...extra }));
      }
    });
    await page.locator('#articleResourceQuery').focus();
    await page.keyboard.press('Control+Alt+Enter');
    assert.equal(await page.evaluate(() => references.length), 2, 'search/IME/key repeat do not add context');

    // Monaco receives its own keyboard events; the Portal command must run first.
    await page.addScriptTag({ url: origin + '/static/vendor/monaco/vs/loader.js' });
    await page.evaluate(() => new Promise(resolve => {
      require.config({ paths: { vs: '/static/vendor/monaco/vs' } });
      require(['vs/editor/editor.main'], () => {
        const root = document.getElementById('articleMonacoEditor');
        root.style.cssText = 'display:block;width:600px;height:200px';
        state.editor = monaco.editor.create(root, { value: 'alpha\nMonaco selection\nomega', language: 'plaintext', automaticLayout: true });
        state.monacoReady = true; state.openFile = 'main.tex'; draft = '';
        state.editor.setSelection(new monaco.Selection(2, 1, 2, 17)); state.editor.focus();
        resolve();
      });
    }));
    await page.keyboard.press('Control+Alt+Enter');
    await page.waitForFunction(() => references.length === 3);
    assert.equal(await page.evaluate(() => JSON.parse(references.at(-1).text).file.selection.text), 'Monaco selection');
    assert.equal(await page.evaluate(() => draft), '', 'selected context adds a reference without a generated prompt');
    assert.equal(await page.evaluate(() => state.editor.getValue()), 'alpha\nMonaco selection\nomega', 'shortcut never edits source');
    await page.evaluate(() => { state.editor.setPosition({ lineNumber: 3, column: 1 }); state.editor.focus(); draft = ''; });
    await page.keyboard.press('Meta+Alt+Enter');
    await page.waitForFunction(() => references.length === 4);
    assert.equal(await page.evaluate(() => JSON.parse(references.at(-1).text).file.cursor.line), 3);
    assert.equal(await page.evaluate(() => draft), '', 'cursor context also leaves an empty draft empty');

    await page.evaluate(() => { holdChat = true; state.editor.focus(); });
    await page.keyboard.press('Control+Alt+Enter');
    await page.waitForFunction(() => openings === 5);
    await page.evaluate(() => { state.selectedId = 'article_other'; holdChat = false; releaseChat(); });
    await page.waitForFunction(() => !PortalCommandRegistry.root.get('article.contextToChat') || !PortalCommandRegistry.root.list().find(c => c.id === 'article.contextToChat').busy);
    assert.equal(await page.evaluate(() => references.length), 4, 'old selection cannot enter another article');
    await page.evaluate(() => state.editor.setSelection(new monaco.Selection(2, 1, 2, 17)));
    await page.locator('#articleSendContext').click();
    await page.waitForFunction(() => references.length === 5);
    assert.equal(await page.evaluate(() => JSON.parse(references.at(-1).text).file.selection.text), 'Monaco selection');
    assert.equal(await page.evaluate(() => draft), '', 'toolbar adds only the reference as well');
    assert.deepEqual(await page.evaluate(() => notices), []);
    assert.deepEqual(errors, []);
    console.log('PASS: Article context shortcut in Monaco/textarea, modifier variants, scope, frozen selection, draft preservation and button');
  } finally {
    await browser?.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
