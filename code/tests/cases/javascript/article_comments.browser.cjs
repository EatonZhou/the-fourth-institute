'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), http = require('node:http');
const { STATIC } = require('./portal_workspace_surface_scripts.cjs');
const { chromium } = require(path.join(STATIC, '../test_framework/tau_runtime/node_modules/playwright'));
const html = fs.readFileSync(path.join(STATIC, '../article_workspace.html'), 'utf8')
  .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '').replace(/<link\b[^>]*>/gi, '')
  .replace('</head>', '<link rel="stylesheet" href="/static/article_workspace.css"><style>body{margin:0} .articleEditorPane{display:flex;flex-direction:column;width:900px;height:700px}.articleCodeEditor{min-height:180px}#articleSourceEditor{height:100%}</style></head>')
  .replace('</body>', '<script src="/static/article_workspace_comments.js"></script><script src="/static/vendor/monaco/vs/loader.js"></script></body>');
(async () => {
  let browser;
  const server = http.createServer((req, res) => {
    const rel = decodeURIComponent(req.url.split('?')[0]).replace('/static/', '');
    if (req.url === '/') { res.setHeader('Content-Type','text/html'); return res.end(html); }
    if (rel.includes('..') || !req.url.startsWith('/static/')) { res.writeHead(404); return res.end(); }
    try { res.setHeader('Content-Type', rel.endsWith('.css') ? 'text/css' : 'text/javascript'); res.end(fs.readFileSync(path.join(STATIC, rel))); }
    catch { res.writeHead(404); res.end(); }
  });
  await new Promise(r => server.listen(0, '127.0.0.1', r));
  try {
    browser = await chromium.launch({ headless: true });
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    const origin = `http://127.0.0.1:${server.address().port}`;
    await page.route('**/*', r => r.request().url().startsWith(origin + '/') ? r.continue() : r.abort());
    const errors = []; page.on('pageerror', e => errors.push(e.message));
    await page.goto(origin);
    await page.evaluate(() => {
      // Use actual page markup; remove unrelated workspace regions for this owner test.
      const pane = document.getElementById('articleEditorPane'); document.body.replaceChildren(pane);
      const $ = id => document.getElementById(id);
      const text = 'before selected text after';
      const doc = { source: text, editable: true };
      window.state = { selectedId: 'A', openFile: 'main.tex', documents: new Map([['main.tex', doc]]), monacoDisposables: [] };
      window.saved = 0; window.requests = []; window.threads = []; window.fail = false;
      const editor = $('articleSourceEditor'); editor.disabled = false; editor.value = text;
      window.comments = PortalArticleWorkspaceComments.createComments({ state,
        activeDocument: () => doc, editorValue: () => state.editor?.getValue() || editor.value,
        openArticleFile: async p => { state.openFile = p; }, saveAllDirtyDocuments: async () => { saved++; return true; },
        showToast: text => { window.toast = text; },
        request: async (url, options) => {
          requests.push(url);
          if (!options) return { threads: structuredClone(threads), actor: 'alice', can_write: true };
          if (fail) throw new Error('offline');
          const p = JSON.parse(options.body);
          let t = threads.find(t => t.id === p.thread_id);
          if (p.action === 'create') {
            t = { id: 't1', anchor: { path: p.path, quote: p.quote, start: p.start, end: p.end, prefix: 'before ', suffix: ' after', line: 1 },
              version: 1, resolved: false, created_at: new Date().toISOString(), messages: [] }; threads.push(t);
          }
          if (p.action === 'create' || p.action === 'reply') t.messages.push({ id: String(t.messages.length), text: p.text, author: 'alice', created_at: new Date().toISOString() });
          if (p.action === 'resolve' || p.action === 'reopen') t.resolved = p.action === 'resolve';
          return { thread: structuredClone(t) };
        } });
      comments.selectArticle();
    });
    await page.click('#articleCommentsToggle');
    await page.waitForFunction(() => !document.getElementById('articleCommentNew').disabled);
    await page.evaluate(() => document.getElementById('articleSourceEditor').setSelectionRange(7, 20));
    await page.click('#articleCommentNew');
    await page.fill('#articleCommentText', '<img src=x onerror=alert(1)> Clarify this');
    await page.click('#articleCommentSubmit');
    await page.waitForFunction(() => document.querySelectorAll('.articleCommentThread').length === 1);
    assert.equal(await page.evaluate(() => saved), 1);
    assert.equal(await page.locator('.articleCommentMessage img').count(), 0);
    await page.getByRole('button', { name: '回复', exact: true }).click();
    await page.fill('#articleCommentText', 'reply draft');
    await page.evaluate(() => { fail = true; });
    await page.click('#articleCommentSubmit');
    await page.waitForFunction(() => document.getElementById('articleCommentStatus').textContent.includes('offline'));
    assert.equal(await page.inputValue('#articleCommentText'), 'reply draft');
    await page.evaluate(() => { fail = false; });
    await page.click('#articleCommentSubmit');
    await page.waitForFunction(() => document.querySelectorAll('.articleCommentMessage').length === 2);
    await page.getByRole('button', { name: '解决', exact: true }).click();
    await page.waitForFunction(() => !document.querySelector('.articleCommentThread'));
    await page.selectOption('#articleCommentFilter', 'resolved');
    await page.getByRole('button', { name: '重新打开', exact: true }).click();
    await page.selectOption('#articleCommentFilter', 'open');
    // Monaco highlight and navigation use the production editor API.
    await page.evaluate(async () => {
      await new Promise((resolve, reject) => {
        require.config({ paths: { vs: '/static/vendor/monaco/vs' } }); require(['vs/editor/editor.main'], resolve, reject);
      });
      document.getElementById('articleCodeEditor').classList.add('monacoReady');
      state.editor = monaco.editor.create(document.getElementById('articleMonacoEditor'), { value: 'new prefix\nbefore selected text after', language: 'plaintext', automaticLayout: true });
      state.documents.get('main.tex').source = state.editor.getValue(); comments.bindEditor();
    });
    await page.click('.articleCommentQuote');
    assert.equal(await page.evaluate(() => state.editor.getModel().getValueInRange(state.editor.getSelection())), 'selected text');
    assert.ok(await page.evaluate(() => state.editor.getModel().getAllDecorations().some(d => d.options.inlineClassName === 'articleCommentAnchor')));
    await page.evaluate(() => { state.editor.setValue('source removed'); state.documents.get('main.tex').source = 'source removed'; });
    await page.click('.articleCommentQuote');
    await page.waitForFunction(() => window.toast?.includes('无法可靠定位'));
    assert.ok(await page.locator('.articleCommentMeta').innerText().then(t => t.includes('原文已变化')));
    await page.evaluate(() => document.getElementById('articleEditorPane').style.width = '420px');
    assert.ok(await page.evaluate(() => { const p=document.getElementById('articleComments'), e=document.getElementById('articleEditorPane'); return p.getBoundingClientRect().right <= e.getBoundingClientRect().right + 1; }));
    await page.evaluate(() => { state.selectedId = 'B'; window.threads = []; comments.selectArticle(); });
    await page.waitForFunction(() => !document.querySelector('.articleCommentThread'));
    assert.ok(await page.evaluate(() => requests.every(u => u.endsWith('/comments'))));
    assert.deepEqual(errors, []);
    console.log('Article comments: selection, persistence transport, reply, error draft, resolve/reopen, Monaco navigation, stale anchor, narrow layout, isolation passed');
  } finally { await browser?.close(); await new Promise(r => server.close(r)); }
})().catch(e => { console.error(e); process.exitCode = 1; });
