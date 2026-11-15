'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { STATIC, portalWorkspaceSurfaceScripts } = require('./portal_workspace_surface_scripts.cjs');
const { chromium } = require(path.join(STATIC, '../test_framework/tau_runtime/node_modules/playwright'));
const modules = ['portal_state.js', 'portal_page_state.js', 'portal_text.js', 'portal_i18n.js', 'portal_ui_primitives.js', 'portal_ui.js',
  'portal_pdf_preview.js', 'portal_command_registry.js', ...portalWorkspaceSurfaceScripts(), 'article_workspace_documents.js', 'article_workspace_position.js',
  'article_workspace_syntax.js', 'article_workspace_monaco.js', 'article_workspace_pdf.js', 'article_workspace_build.js', 'article_workspace_events.js'];
const html = fs.readFileSync(path.join(STATIC, '../article_workspace.html'), 'utf8')
  .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '').replace(/<link\b[^>]*>/gi, '')
  .replace('</body>', modules.map(n => `<script src="/static/${n}"></script>`).join('') + '</body>');
(async () => {
  const text = Array.from({ length: 200 }, (_, i) => `Line ${i + 1} of the source`).join('\n');
  const files = { 'main.tex': text, 'intro.tex': text };
  let browser;
  // Small valid multi-page PDF for the real PDF.js rendering/swap check.
  const objects = ['<< /Type /Catalog /Pages 2 0 R >>',
    '<< /Type /Pages /Kids [3 0 R 5 0 R 7 0 R 9 0 R 11 0 R] /Count 5 >>'];
  for (let n = 1; n <= 5; n++) {
    const stream = `0.8 g 40 40 420 620 re f 0 g BT /F1 24 Tf 60 600 Td (Page ${n}) Tj ET`;
    objects.push(`<< /Type /Page /Parent 2 0 R /MediaBox [0 0 500 700] /Resources << /Font << /F1 13 0 R >> >> /Contents ${n * 2 + 2} 0 R >>`,
      `<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`);
  }
  objects.push('<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>');
  let pdfBytes = '%PDF-1.4\n'; const offsets = [0];
  objects.forEach((object, index) => { offsets.push(pdfBytes.length); pdfBytes += `${index + 1} 0 obj\n${object}\nendobj\n`; });
  const xref = pdfBytes.length;
  pdfBytes += `xref\n0 ${offsets.length}\n0000000000 65535 f \n` + offsets.slice(1).map(n => `${String(n).padStart(10, '0')} 00000 n \n`).join('')
    + `trailer\n<< /Size ${offsets.length} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://fixture');
    if (url.pathname === '/') { res.setHeader('Content-Type', 'text/html'); return res.end(html); }
    if (url.pathname === '/away') return res.end('<p>Elsewhere</p>');
    if (url.pathname === '/fixture.pdf') {
      res.setHeader('Content-Type', 'application/pdf');
      return setTimeout(() => res.end(pdfBytes), url.searchParams.get('v') === 'real-rebuild' ? 250 : 0);
    }
    const rel = url.pathname.slice('/static/'.length);
    if (url.pathname.endsWith('/file')) {
      const name = url.searchParams.get('path');
      res.setHeader('Content-Type', 'application/json');
      return res.end(JSON.stringify({ path: name, kind: 'text', content: files[name] }));
    }
    if (!url.pathname.startsWith('/static/') || rel.includes('..') || (!modules.includes(rel)
      && rel !== 'article_workspace.css' && !rel.startsWith('vendor/monaco/') && !rel.startsWith('vendor/pdfjs/'))) {
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
      window.realPdfPreview = window.PortalPdfPreview;
      window.PortalPdfPreview = { render: ({ root, scaleMultiplier, version }) => {
        const pageCount = window.pdfPageCount;
        const failure = window.pdfFailure;
        root.dataset.fixtureVersion = version || 'initial';
        const promise = (async () => {
          root.style.position = 'relative'; root.innerHTML = 'Loading PDF…';
          // Match the asynchronous download: the shorter loading view resets
          // the scroll container before PDF page geometry becomes available.
          await new Promise(resolve => requestAnimationFrame(resolve));
          if (window.pdfLoadDelay) await new Promise(resolve => setTimeout(resolve, window.pdfLoadDelay));
          if (failure) throw new Error('PDF download failed');
          void root.parentElement.scrollTop;
          root.dataset.pdfPageCount = String(pageCount); root.innerHTML = '';
          for (let n = 1; n <= pageCount; n++) {
            const p = document.createElement('div'); p.dataset.pdfPage = n;
            p.dataset.pdfRenderState = n === 1 ? 'done' : 'pending';
            p.style.cssText = `width:${500 * scaleMultiplier}px;height:${700 * scaleMultiplier}px;margin:10px;background:#eee`;
            p.textContent = `Page ${n}`; root.append(p);
          }
        })();
        return { promise, cancel: noop, ensurePage: async n => {
          if (n < 1 || n > pageCount) throw new Error('page out of bounds');
          if (window.pdfPageDelay) await new Promise(resolve => setTimeout(resolve, window.pdfPageDelay));
          root.querySelector(`[data-pdf-page="${n}"]`).dataset.pdfRenderState = 'done';
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
        window.pdfLoadDelay = 100; window.pdfPageDelay = 60;
        window.swapFrames = [];
        const sample = () => {
          const host = document.getElementById('articlePdfView');
          const viewport = host.getBoundingClientRect();
          const visible = Array.from(host.querySelectorAll('[data-pdf-page]')).filter(p => {
            const bounds = p.getBoundingClientRect();
            return bounds.bottom > viewport.top && bounds.top < viewport.bottom;
          });
          window.swapFrames.push({ position: pdfOwner.captureArticlePdfPosition(),
            version: host.querySelector('.articlePdfDocument')?.dataset.fixtureVersion,
            ready: visible.length > 0 && visible.every(p => p.dataset.pdfRenderState === 'done') });
          if (window.completedPdfRenders === completed) requestAnimationFrame(sample);
        };
        requestAnimationFrame(sample);
        finishCompile({ id: state.selectedId, status: 'success', pdf_url: '/fixture.pdf', compiled_at: `build-${completed + 1}` });
        return { position, completed, zoom: state.pdfZoom };
      }, { scrollTop, pages, fromScratch });
      await page.waitForFunction(completed => window.completedPdfRenders > completed, anchor.completed);
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(resolve)));
      return { before: anchor, after: await page.evaluate(() => ({ position: pdfOwner.captureArticlePdfPosition(), zoom: state.pdfZoom })),
        frames: await page.evaluate(() => window.swapFrames) };
    };
    const rebuilt = await rebuildAt({ scrollTop: 2750 });
    assert.equal(rebuilt.after.position.page, rebuilt.before.position.page);
    assert.ok(Math.abs(rebuilt.after.position.yRatio - rebuilt.before.position.yRatio) < .01);
    assert.ok(Math.abs(rebuilt.after.position.xRatio - rebuilt.before.position.xRatio) < .01);
    assert.equal(rebuilt.after.zoom, rebuilt.before.zoom);
    assert.ok(rebuilt.frames.length > 2, 'observe intermediate animation frames during slow PDF rendering');
    assert.ok(rebuilt.frames.every(frame => frame.position?.page === rebuilt.before.position.page), 'never paint the loading view or PDF top');
    assert.ok(rebuilt.frames.filter(frame => frame.version === 'build-1').every(frame => frame.ready), 'new visible pages are rendered before replacement');
    const shortened = await rebuildAt({ scrollTop: 4100, pages: 3, fromScratch: true });
    assert.equal(shortened.before.position.page, 5);
    assert.equal(shortened.after.position.page, 3);
    assert.ok(Math.abs(shortened.after.position.yRatio - shortened.before.position.yRatio) < .01);
    const failedSwap = await page.evaluate(async () => {
      const old = document.getElementById('articlePdfDocument'), scroll = document.getElementById('articlePdfView').scrollTop;
      window.pdfFailure = true; state.build.compiled_at = 'failed-update';
      await pdfOwner.renderPdf(); window.pdfFailure = false;
      return old === document.getElementById('articlePdfDocument') && scroll === document.getElementById('articlePdfView').scrollTop
        && !document.querySelector('[data-article-pdf-staging]');
    });
    assert.ok(failedSwap, 'failed replacement keeps the visible PDF and cleans up staging');
    // A cancelled view belongs to the previous Article, even if its DOM remains.
    await page.evaluate(async () => {
      state.build.compiled_at = 'superseded'; const pending = pdfOwner.renderPdf();
      pdfOwner.cancelPdf(); state.selectedId = 'another-article'; state.build.compiled_at = 'another-article-build';
      await pdfOwner.renderPdf();
      await pending;
    });
    assert.equal(await page.evaluate(() => document.getElementById('articlePdfView').scrollTop), 0);
    assert.equal(await page.evaluate(() => document.getElementById('articlePdfDocument').dataset.fixtureVersion), 'another-article-build');
    await enter({ user: 'other-reader' }); assert.equal(await page.evaluate(() => state.openFile), 'main.tex');
    await enter({ articleId: 'B' }); assert.equal(await page.evaluate(() => state.openFile), 'main.tex');
    await enter({ available: ['main.tex'] }); assert.equal(await page.evaluate(() => state.openFile), 'main.tex');
    await page.evaluate(async () => { state.pdfRestorePosition = { page: 90, xRatio: .5, yRatio: .5 }; pdfOwner.cancelPdf(); await pdfOwner.renderPdf(); });
    assert.equal(await page.evaluate(() => pdfOwner.captureArticlePdfPosition().page), 5);
    // Exercise the same transition with production CSS, PDF.js, canvases and
    // lazy page rendering, including scrolling while the new PDF downloads.
    await page.addStyleTag({ url: origin + '/static/article_workspace.css' });
    const realSwap = await page.evaluate(async () => {
      pdfOwner.cancelPdf(); window.PortalPdfPreview = window.realPdfPreview;
      state.build.compiled_at = 'real-initial'; state.pdfZoom = 1.4;
      await pdfOwner.renderPdf();
      const host = document.getElementById('articlePdfView');
      host.scrollTop = 2800;
      const old = document.getElementById('articlePdfDocument');
      const frames = []; let finished = false;
      const sample = () => {
        const rect = host.getBoundingClientRect();
        const pages = Array.from(host.querySelectorAll('[data-pdf-page]')).filter(p => {
          const bounds = p.getBoundingClientRect(); return bounds.bottom > rect.top && bounds.top < rect.bottom;
        });
        frames.push({ page: pdfOwner.captureArticlePdfPosition()?.page,
          old: old === document.getElementById('articlePdfDocument'),
          ready: pages.length > 0 && pages.every(p => p.dataset.pdfRenderState === 'done' && p.querySelector('canvas')) });
        if (!finished) requestAnimationFrame(sample);
      };
      requestAnimationFrame(sample);
      state.build.compiled_at = 'real-rebuild'; const updating = pdfOwner.renderPdf();
      await pdfOwner.setArticlePdfZoom(1.6);
      host.scrollTop = 3200;
      const target = pdfOwner.captureArticlePdfPosition();
      const scale = Number(old.querySelector(`[data-pdf-page="${target.page}"]`).dataset.pdfScale);
      await updating; finished = true;
      await new Promise(resolve => requestAnimationFrame(resolve));
      return { frames, target, result: pdfOwner.captureArticlePdfPosition(), scale,
        newScale: Number(host.querySelector(`[data-pdf-page="${target.page}"]`).dataset.pdfScale),
        staging: Boolean(document.querySelector('[data-article-pdf-staging]')) };
    });
    assert.ok(realSwap.frames.some(frame => frame.old) && realSwap.frames.some(frame => !frame.old));
    assert.ok(realSwap.frames.every(frame => frame.page === realSwap.target.page), 'real renderer never exposes a different page');
    assert.ok(realSwap.frames.filter(frame => !frame.old).every(frame => frame.ready), 'new PDF has rendered canvases on its first visible frame');
    assert.ok(Math.abs(realSwap.target.yRatio - realSwap.result.yRatio) < .01);
    assert.equal(realSwap.newScale, realSwap.scale, 'zoom changes during loading apply to the replacement');
    assert.equal(realSwap.staging, false);
    assert.deepEqual(errors, []);
    console.log('PASS: return and recompile preserve PDF page/position/zoom; fresh source, user/Article isolation and removed targets');
  } finally { await browser?.close(); await new Promise(resolve => server.close(resolve)); }
})().catch(error => { console.error(error); process.exitCode = 1; });
