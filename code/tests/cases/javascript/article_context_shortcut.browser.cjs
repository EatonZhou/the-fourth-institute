'use strict';
// Actual Article event wiring and selection readers, including Monaco.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const root = path.resolve(__dirname, '../../..');
const staticRoot = path.join(root, 'management_portal/static');
const { chromium } = require(path.join(root, 'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const modules = ['portal_code_editor.js', 'portal_command_registry.js', 'article_workspace_chat.js', 'article_workspace_monaco.js', 'article_workspace_events.js'];
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
    page.on('console', message => { if(message.type()==='error') console.error(message.text()); });
    page.on('pageerror', error => errors.push(error.message));
    const origin = `http://127.0.0.1:${server.address().port}`;
    await page.route('**/*', route => route.request().url().startsWith(origin + '/') ? route.continue() : route.abort());
    await page.goto(origin);
    await page.evaluate(() => {
      window.PortalI18n = { t: text => text, text: parts => parts.join('') };
      window.references = []; window.notices = []; window.openings = 0; window.draft = 'Keep my question';
      window.holdChat = false;
      window.state = { selectedId: 'article_fixture', openFile: 'main.tex', source: '', editorPreferences: { tabSize: 2, fontSize:14 } };
      const $ = id => document.getElementById(id);
      const controller = { focusComposer() {}, draftText: () => draft, setDraftText: value => { draft = value; },
        addReference(ref, options = {}) {
          const index = references.findIndex(r => r.id === options.replaceId);
          const item = { ...ref, id: index < 0 ? `ref-${references.length}` : options.replaceId };
          if (index < 0) references.push(item); else references[index] = item;
          return item;
        },
      };
      state.chatController = controller;
      state.chatPanel = { async openSurface() {
        if (holdChat) await new Promise(resolve => { window.releaseChat = resolve; });
        return {};
      } };
      const depsFor = module => Object.fromEntries(module.dependencies.map(key => [key, () => {}]));
      let host;
      const doc = {revision:'revision-1',editable:true};
      state.documents = new Map([['main.tex',doc]]);
      let monaco;
      const options = { ...depsFor(PortalArticleWorkspaceMonaco),
        getSession: () => ({articleId:state.selectedId,path:state.openFile,documents:state.documents,preferences:state.editorPreferences}),
        activeDocument: () => doc, handleEditorPairing: () => false, syncEditorScroll() {},
        bringCurrentFileToChat: () => host.bringCurrentFileToChat(), onSelectionCommitted: () => host.bringSelectionToChat(),
        showToast: error => notices.push(String(error)),
        activateDocument: () => monaco.getEditor().setModel(window.monaco.editor.createModel($('articleSourceEditor').value,'latex')),
      };
      window.resetEditor = () => {
        monaco?.destroy();
        monaco = window.editorOwner = PortalArticleWorkspaceMonaco.createMonaco(options);
        document.body.append(monaco.root); monaco.connect();
      };
      resetEditor();
      document.body.append(monaco.root); monaco.connect();
      host = PortalArticleWorkspaceChat.createChat({ ...depsFor(PortalArticleWorkspaceChat), $, state,
        outputView: () => ({getSyncLocation:()=>null}), selectedArticle: () => ({ title: 'Fixture', canonical_root: '/fixture' }), articleProjects: () => ['fixture'],
        activeDocument: () => ({ revision: 'revision-1' }), documentIsDirty: () => true,
        editorPosition: () => monaco.editorPosition(), editorSelectionContext: (...args) => monaco.editorSelectionContext(...args), editorValue: () => monaco.editorValue(), languageForPath: () => 'latex',
        setSideView: async view => {
          if (view !== 'chat') throw new Error('Unexpected view');
          openings++;
          if (holdChat) await new Promise(resolve => { window.releaseChat = resolve; });
          return controller;
        },
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
    assert.equal(context.file.content, 'first line\nselected words\nlast line');
    assert.equal(context.file.selection, null);
    assert.equal(await page.evaluate(() => references[0].line_start), undefined);
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
    assert.equal(context.file.content, 'first line\nselected words\nlast line');
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
    await page.evaluate(async () => {
      require.config({paths:{vs:'/static/vendor/monaco/vs'}});
      window.PortalCodeEditor = {...window.PortalCodeEditor,loadMonaco:()=>new Promise(resolve=>require(['vs/editor/editor.main'],resolve))};
      document.getElementById('articleMonacoEditor').style.cssText = 'display:block;width:600px;height:200px';
      document.getElementById('articleSourceEditor').value = 'alpha\nMonaco selection\nomega';
      state.openFile = 'main.tex'; draft = '';
      if(!await editorOwner.initializeMonacoEditor()) throw Error('Monaco failed');
      state.editor = editorOwner.getEditor();
      state.editor.setSelection(new monaco.Selection(2,1,2,17)); state.editor.focus();
    });
    await page.keyboard.press('Control+Alt+Enter');
    await page.waitForFunction(() => references.length === 3);
    assert.equal(await page.evaluate(() => JSON.parse(references.at(-1).text).file.content), 'alpha\nMonaco selection\nomega');
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
    await page.waitForFunction(() => !PortalCommandRegistry.root.list().find(c => c.id === 'article.fileToChat').busy);
    assert.equal(await page.evaluate(() => references.length), 4, 'old selection cannot enter another article');
    await page.evaluate(() => state.editor.setSelection(new monaco.Selection(2, 1, 2, 17)));
    await page.locator('#articleSendContext').click();
    await page.waitForFunction(() => references.length === 5);
    assert.equal(await page.evaluate(() => JSON.parse(references.at(-1).text).file.content), 'alpha\nMonaco selection\nomega');
    assert.equal(await page.evaluate(() => draft), '', 'toolbar adds only the reference as well');

    // Real mouse selection in the fallback editor; initialization is shared and
    // only the latest selection is added when the conversation arrives late.
    const beforeOpenings = await page.evaluate(() => openings);
    await page.evaluate(() => {
      resetEditor(); document.getElementById('articleSourceEditor').disabled = false; holdChat = true; window.releaseChat = null;
      const source = document.getElementById('articleSourceEditor');
      source.value = 'alpha bravo charlie';
      source.style.cssText = 'position:fixed;left:80px;top:80px;width:600px;height:120px;font:20px monospace;line-height:24px;padding:0;border:0;z-index:99999;display:block';
      source.setSelectionRange(0, 0); source.focus();
    });
    const selectFallback = async (start, end) => {
      const points = await page.locator('#articleSourceEditor').evaluate((source, [start, end]) => {
        const c = document.createElement('canvas').getContext('2d'); c.font = getComputedStyle(source).font;
        const box = source.getBoundingClientRect();
        return [start, end].map(offset => ({x: box.left + c.measureText(source.value.slice(0, offset)).width + 1, y: box.top + 12}));
      }, [start, end]);
      await page.mouse.move(points[0].x, points[0].y); await page.mouse.down();
      await page.mouse.move(points[1].x, points[1].y, {steps: 5}); await page.mouse.up();
      await page.evaluate(() => new Promise(requestAnimationFrame));
    };
    await selectFallback(0, 5);
    await page.waitForFunction(() => Boolean(releaseChat)).catch(async error => {
      console.error(await page.evaluate(() => ({notices, selection: [document.getElementById('articleSourceEditor').selectionStart,
        document.getElementById('articleSourceEditor').selectionEnd], focus: document.activeElement.id})));
      throw error;
    });
    await selectFallback(6, 11);
    assert.equal(await page.evaluate(() => references.length), 5, 'waiting selection has not been attached yet');
    await page.evaluate(() => { holdChat = false; releaseChat(); });
    await page.waitForFunction(() => references.length === 6);
    assert.equal(await page.evaluate(() => JSON.parse(references.at(-1).text).file.selection.text), 'bravo');
    assert.equal(await page.evaluate(() => openings), beforeOpenings, 'automatic selection does not open or focus a side panel');
    assert.equal(await page.evaluate(() => document.activeElement.id), 'articleSourceEditor');
    await selectFallback(12, 19);
    await page.waitForFunction(() => JSON.parse(references.at(-1).text).file.selection.text === 'charlie');
    assert.equal(await page.evaluate(() => references.length), 6, 'replace the automatic reference, retain full files');
    assert.deepEqual(await page.evaluate(() => notices), []);
    assert.deepEqual(errors, []);
    console.log('PASS: Article context shortcut in Monaco/textarea, modifier variants, scope, frozen selection, draft preservation and button');
  } finally {
    await browser?.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
