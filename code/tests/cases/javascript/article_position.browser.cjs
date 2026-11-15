'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { STATIC, portalWorkspaceSurfaceScripts } = require('./portal_workspace_surface_scripts.cjs');
const { chromium } = require(path.join(STATIC, '../test_framework/tau_runtime/node_modules/playwright'));
const modules = ['portal_state.js', 'portal_page_state.js', 'portal_text.js', 'portal_i18n.js', 'portal_ui_primitives.js', 'portal_ui.js',
  'portal_command_registry.js', ...portalWorkspaceSurfaceScripts(), 'article_workspace_documents.js', 'article_workspace_position.js',
  'article_workspace_syntax.js', 'article_workspace_monaco.js', 'article_workspace_pdf.js', 'article_workspace_build.js', 'article_workspace_events.js'];
const html = fs.readFileSync(path.join(STATIC, '../article_workspace.html'), 'utf8')
  .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '').replace(/<link\b[^>]*>/gi, '')
  .replace('</body>', modules.map(n => `<script src="/static/${n}"></script>`).join('') + '</body>');
(async () => {
  const text = Array.from({ length: 200 }, (_, i) => `Line ${i + 1} of the source`).join('\n');
  const files = { 'main.tex': text, 'intro.tex': text };
  let browser;
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://fixture');
    if (url.pathname === '/') { res.setHeader('Content-Type', 'text/html'); return res.end(html); }
    if (url.pathname === '/away') return res.end('<p>Elsewhere</p>');
    const rel = url.pathname.slice('/static/'.length);
    if (url.pathname.endsWith('/file')) {
      const name = url.searchParams.get('path');
      res.setHeader('Content-Type', 'application/json');
      return res.end(JSON.stringify({ path: name, kind: 'text', content: files[name] }));
    }
    if (!url.pathname.startsWith('/static/') || rel.includes('..') || (!modules.includes(rel) && !rel.startsWith('vendor/monaco/'))) {
      res.writeHead(404); return res.end();
    }
    res.setHeader('Content-Type', rel.endsWith('.css') ? 'text/css' : 'text/javascript');
    try { res.end(fs.readFileSync(path.join(STATIC, rel))); } catch { res.writeHead(404); res.end(); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  try {
    browser = await chromium.launch({ headless: true });
    const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
    page.setDefaultTimeout(10000);
    const errors = []; page.on('pageerror', e => errors.push(e.message));
    const origin = `http://127.0.0.1:${server.address().port}`;
    await page.route('**/*', route => route.request().url().startsWith(origin + '/') ? route.continue() : route.abort());
    const setup = async ({ user = 'reader', articleId = 'A', available = ['main.tex', 'intro.tex'] } = {}) => {
      const $ = id => document.getElementById(id), noop = () => {};
      const deps = module => Object.fromEntries(module.dependencies.map(k => [k, noop]));
      window.PortalIdentity = { username: () => user };
      window.PortalPreviewContract = { isTextDocument: () => true };
      window.PortalPreview = { normalizeDocument: value => ({ ...value, contentKind: 'text', capabilities: {} }),
        configureDownloadLink: noop, dispose: noop, createFrameCoalescer: () => ({ cancel: noop }) };
      // Deterministic PDF page geometry; exercise the production Article PDF
      // capture/restore and render completion with no external PDF service.
      window.pdfPageCount = 5;
      window.PortalPdfPreview = { render: ({ root, scaleMultiplier }) => {
        const pageCount = window.pdfPageCount;
        const promise = (async () => {
          root.style.position = 'relative'; root.innerHTML = 'Loading PDF…';
          // Match the asynchronous download: the shorter loading view resets
          // the scroll container before PDF page geometry becomes available.
          await new Promise(resolve => requestAnimationFrame(resolve));
          void root.parentElement.scrollTop;
          root.dataset.pdfPageCount = String(pageCount); root.innerHTML = '';
          for (let n = 1; n <= pageCount; n++) {
            const p = document.createElement('div'); p.dataset.pdfPage = n;
            p.style.cssText = `width:${500 * scaleMultiplier}px;height:${700 * scaleMultiplier}px;margin:10px;background:#eee`;
            p.textContent = `Page ${n}`; root.append(p);
          }
        })();
        return { promise, cancel: noop, ensurePage: async n => {
          if (n < 1 || n > pageCount) throw new Error('page out of bounds');
        } };
      } };
      window.state = { selectedId: articleId, openFile: '', openTabs: [], documents: new Map(), source: '', savedSource: '',
        filesByPath: new Map(available.map(p => [p, {}])), editorPreferences: { tabSize: 2, fontSize: 14 },
        monacoDisposables: [], settings: {}, compilingArticles: new Set(), outputView: 'pdf', pdfZoom: 1, pdfScaleVersion: 0,
        build: { pdf_available: true, pdf_url: '/fixture.pdf' } };
      let syntax, editor, location, pdf;
      window.docs = PortalArticleWorkspaceDocuments.createDocuments({ ...deps(PortalArticleWorkspaceDocuments), $, state,
        editorValue: () => state.monacoReady ? state.editor.getValue() : $('articleSourceEditor').value,
        updateDirtyState: () => syntax.updateDirtyState(), monacoApi: () => window.monaco,
        articleModelUri: p => monaco.Uri.parse(`article://${articleId}/${p}`), languageForPath: () => 'latex',
        loadSemanticIndex: async () => {}, request: async url => (await fetch(url)).json(),
        scheduleReadingPosition: () => location.schedule(),
      });
      syntax = PortalArticleWorkspaceSyntax.createSyntax({ ...deps(PortalArticleWorkspaceSyntax), $, state,
        activeDocument: docs.activeDocument, renderDocumentTabs: docs.renderDocumentTabs });
      location = window.readingPosition = PortalArticleWorkspacePosition.createPosition({ state,
        captureActiveDocument: docs.captureActiveDocument, captureArticlePdfPosition: () => pdf.captureArticlePdfPosition(),
        openArticleFile: docs.openArticleFile, activateDocument: docs.activateDocument });
      pdf = window.pdfOwner = PortalArticleWorkspacePdf.createPdf({ ...deps(PortalArticleWorkspacePdf), $, state,
        selectedArticle: () => ({ title: 'Fixture' }) });
      window.completedPdfRenders = 0;
      window.buildOwner = PortalArticleWorkspaceBuild.createBuild({ ...deps(PortalArticleWorkspaceBuild), $, state,
        request: async () => new Promise(resolve => { window.finishCompile = resolve; }),
        renderPdf: async () => { await pdf.renderPdf(); window.completedPdfRenders += 1; },
        showPdfError: error => { throw error; },
      });
      const shared = { $, state, activeDocument: docs.activeDocument, captureActiveDocument: docs.captureActiveDocument,
        updateDirtyState: syntax.updateDirtyState, hasDirtyDocuments: docs.hasDirtyDocuments };
      editor = PortalArticleWorkspaceMonaco.createMonaco({ ...deps(PortalArticleWorkspaceMonaco), ...shared, activateDocument: docs.activateDocument });
      PortalArticleWorkspaceEvents.createEvents({ ...deps(PortalArticleWorkspaceEvents), ...shared,
        articleGitRefresh: { stop: noop }, articleContentRefresh: { stop: noop },
        persistReadingPosition: location.persist, scheduleReadingPosition: location.schedule });
      $('articleCodeEditor').style.cssText = 'position:fixed;left:0;top:0;width:600px;height:600px;background:white;z-index:100';
      $('articleMonacoEditor').style.cssText = 'display:block;width:600px;height:600px';
      $('articlePdfView').style.cssText = 'position:fixed;left:650px;top:0;width:700px;height:600px;overflow:auto;z-index:100;background:white';
      location.prepare(articleId);
      await location.restoreDocuments(articleId, 'main.tex');
      await pdf.renderPdf();
      require.config({ paths: { vs: '/static/vendor/monaco/vs' } });
      window.PortalCodeEditor = { loadMonaco: () => new Promise(resolve => require(['vs/editor/editor.main'], resolve)) };
      if (!await editor.initializeMonacoEditor()) throw new Error('Monaco failed');
    };
    const enter = async options => {
      await page.goto(origin);
      await page.addScriptTag({ url: origin + '/static/vendor/monaco/vs/loader.js' });
      await page.evaluate(setup, options);
    };
    await enter();
    await page.evaluate(async () => {
      await docs.openArticleFile('intro.tex');
      state.editor.setPosition({ lineNumber: 80, column: 4 }); state.editor.setScrollTop(1400);
      await pdfOwner.setArticlePdfZoom(1.4);
      document.getElementById('articlePdfView').scrollTop = 2150;
    });
    const before = await page.evaluate(() => ({ position: state.editor.getPosition(), scroll: state.editor.getScrollTop(), pdf: pdfOwner.captureArticlePdfPosition() }));
    await page.goto(origin + '/away');
    files['intro.tex'] = text.replace('Line 1 ', 'Fresh 1 ');
    await enter();
    const after = await page.evaluate(() => ({ active: state.openFile, tabs: state.openTabs, position: state.editor.getPosition(),
      scroll: state.editor.getScrollTop(), pdf: pdfOwner.captureArticlePdfPosition(), zoom: state.pdfZoom, source: state.editor.getValue() }));
    assert.equal(after.active, 'intro.tex'); assert.deepEqual(after.tabs, ['main.tex', 'intro.tex']);
    assert.deepEqual(after.position, before.position); assert.ok(Math.abs(after.scroll - before.scroll) < 2);
    assert.equal(after.zoom, 1.4); assert.equal(after.pdf.page, before.pdf.page);
    assert.ok(Math.abs(after.pdf.yRatio - before.pdf.yRatio) < .01); assert.match(after.source, /^Fresh 1/);
    const stored = await page.evaluate(() => JSON.stringify(PortalPageState.create('article-reading-position', { getUser: () => 'reader' }).read()));
    assert.ok(!stored.includes('Line 1 of the source')); assert.ok(!stored.includes('Fresh 1'));
    // A completed build replaces the PDF through the real build -> view -> PDF
    // path. Restore the latest reading position, even if scrolled while compiling.
    const rebuildAt = async ({ scrollTop, pages = 5, fromScratch = false }) => {
      const anchor = await page.evaluate(({ scrollTop, pages, fromScratch }) => {
        const completed = window.completedPdfRenders;
        void buildOwner.compileArticle({ fromScratch });
        document.getElementById('articlePdfView').scrollTop = scrollTop;
        const position = pdfOwner.captureArticlePdfPosition();
        window.pdfPageCount = pages;
        finishCompile({ id: state.selectedId, status: 'success', pdf_url: '/fixture.pdf', compiled_at: `build-${completed + 1}` });
        return { position, completed, zoom: state.pdfZoom };
      }, { scrollTop, pages, fromScratch });
      await page.waitForFunction(completed => window.completedPdfRenders > completed, anchor.completed);
      return { before: anchor, after: await page.evaluate(() => ({ position: pdfOwner.captureArticlePdfPosition(), zoom: state.pdfZoom })) };
    };
    const rebuilt = await rebuildAt({ scrollTop: 2750 });
    assert.equal(rebuilt.after.position.page, rebuilt.before.position.page);
    assert.ok(Math.abs(rebuilt.after.position.yRatio - rebuilt.before.position.yRatio) < .01);
    assert.ok(Math.abs(rebuilt.after.position.xRatio - rebuilt.before.position.xRatio) < .01);
    assert.equal(rebuilt.after.zoom, rebuilt.before.zoom);
    const shortened = await rebuildAt({ scrollTop: 4100, pages: 3, fromScratch: true });
    assert.equal(shortened.before.position.page, 5);
    assert.equal(shortened.after.position.page, 3);
    assert.ok(Math.abs(shortened.after.position.yRatio - shortened.before.position.yRatio) < .01);
    // A cancelled view belongs to the previous Article, even if its DOM remains.
    await page.evaluate(async () => {
      pdfOwner.cancelPdf(); state.selectedId = 'another-article';
      await pdfOwner.renderPdf();
    });
    assert.equal(await page.evaluate(() => document.getElementById('articlePdfView').scrollTop), 0);
    await enter({ user: 'other-reader' }); assert.equal(await page.evaluate(() => state.openFile), 'main.tex');
    await enter({ articleId: 'B' }); assert.equal(await page.evaluate(() => state.openFile), 'main.tex');
    await enter({ available: ['main.tex'] }); assert.equal(await page.evaluate(() => state.openFile), 'main.tex');
    await page.evaluate(async () => { state.pdfRestorePosition = { page: 90, xRatio: .5, yRatio: .5 }; pdfOwner.cancelPdf(); await pdfOwner.renderPdf(); });
    assert.equal(await page.evaluate(() => pdfOwner.captureArticlePdfPosition().page), 5);
    assert.deepEqual(errors, []);
    console.log('PASS: return and recompile preserve PDF page/position/zoom; fresh source, user/Article isolation and removed targets');
  } finally { await browser?.close(); await new Promise(resolve => server.close(resolve)); }
})().catch(error => { console.error(error); process.exitCode = 1; });
