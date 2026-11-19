'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const bootFixture = JSON.parse(fs.readFileSync(0, 'utf8'));
const { STATIC, portalWorkspaceSurfaceScripts } = require('./portal_workspace_surface_scripts.cjs');
const browserType = require(path.join(STATIC, '../test_framework/tau_runtime/node_modules/playwright'))[process.env.PORTAL_ARTICLE_TEST_BROWSER || 'chromium'];
const workbenchScripts = JSON.parse(fs.readFileSync(path.join(STATIC, '../script_groups.json'), 'utf8'))['portal-workbench'].map(s => s.replace('/static/', ''));
const modules = ['portal_state.js', 'portal_page_state.js', 'portal_text.js', 'portal_i18n.js', 'portal_ui_primitives.js', 'portal_ui.js',
  'portal_pdf_preview.js', 'portal_command_registry.js', 'portal_workbench_context.js', 'portal_editor_open_service.js', ...workbenchScripts, ...portalWorkspaceSurfaceScripts(), 'article_workspace_documents.js', 'article_workspace_position.js', 'article_workspace_tools.js', 'article_workspace_layout.js',
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
    if (url.pathname === '/full') { res.setHeader('Content-Type', 'text/html'); return res.end(bootFixture.html); }
    if (url.pathname === '/static/portal_resource_routes.js') {
      res.setHeader('Content-Type', 'text/javascript'); return res.end(bootFixture.routes);
    }
    if (url.pathname === '/api/events') {
      res.setHeader('Content-Type', 'text/event-stream');
      res.write(': fixture\n\n'); return;
    }
    if (url.pathname.startsWith('/api/') && !url.pathname.endsWith('/file')) {
      const id = 'article_000000000001';
      const build = { id, pdf_available: true, pdf_url: '/fixture.pdf', status: 'success', compiled_at: 'full-initial', diagnostics: [] };
      let data = {};
      if (url.pathname === '/api/articles') data = { articles: [{ id, title: 'Recompile fixture', primary_file: 'main.tex', compiled_pdf: { available: true, url: '/fixture.pdf' } }] };
      else if (url.pathname.endsWith('/build')) data = build;
      else if (url.pathname.endsWith('/compile')) data = { ...build, compiled_at: 'full-rebuilt' };
      else if (url.pathname.endsWith('/settings')) data = { id, primary_file: 'main.tex', synctex_available: false };
      else if (url.pathname.endsWith('/files')) data = { id, entries: [{ path: 'main.tex', name: 'main.tex', type: 'file', kind: 'text' }] };
      else if (url.pathname.endsWith('/preflight')) data = { id, status: 'ready', issues: [] };
      else if (url.pathname.endsWith('/semantic')) data = { id, sections: [], labels: [], references: [] };
      else if (url.pathname.endsWith('/comments')) data = { id, threads: [] };
      else if (url.pathname.endsWith('/preferences')) data = { preferences: {} };
      else if (/identity|current-user|\/me$/.test(url.pathname)) data = { username: 'fixture', user: { username: 'fixture' }, authenticated: true };
      res.setHeader('Content-Type', 'application/json');
      if (url.pathname.endsWith('/compile')) return setTimeout(() => res.end(JSON.stringify(data)), 5500);
      return res.end(JSON.stringify(data));
    }
    if (url.pathname === '/') { res.setHeader('Content-Type', 'text/html'); return res.end(html); }
    if (url.pathname === '/away') return res.end('<p>Elsewhere</p>');
    if (url.pathname === '/fixture.pdf') {
      res.setHeader('Content-Type', 'application/pdf');
      return setTimeout(() => res.end(pdfBytes), /rebuild|rebuilt/.test(url.searchParams.get('v')) ? 250 : 0);
    }
    const rel = url.pathname.slice('/static/'.length);
    if (url.pathname.endsWith('/file')) {
      const name = url.searchParams.get('path');
      res.setHeader('Content-Type', 'application/json');
      return res.end(JSON.stringify({ path: name, kind: 'text', content: files[name] }));
    }
    if (!url.pathname.startsWith('/static/') || rel.includes('..')) {
      res.writeHead(404); return res.end();
    }
    res.setHeader('Content-Type', rel.endsWith('.css') ? 'text/css' : 'text/javascript');
    try { res.end(fs.readFileSync(path.join(STATIC, rel))); } catch { res.writeHead(404); res.end(); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  try {
    browser = await browserType.launch({ headless: true });
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
        request: async url => url.endsWith('/compile')
          ? new Promise((resolve, reject) => { window.finishCompile = resolve; window.failCompile = reject; })
          : { status: 'failed', diagnostics: [], issues: [] },
        renderPdf: async () => { await pdf.renderPdf(); window.completedPdfRenders += 1; },
        setPdfCompiling: pdf.setPdfCompiling,
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
    if (!process.env.PORTAL_ARTICLE_TEST_FULL_ONLY) {
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
    // path. Keep the reading position behind the compilation status until ready.
    const rebuildAt = async ({ scrollTop, pages = 5, fromScratch = false }) => {
      const anchor = await page.evaluate(({ scrollTop, pages, fromScratch }) => {
        const completed = window.completedPdfRenders;
        document.getElementById('articlePdfView').scrollTop = scrollTop;
        const position = pdfOwner.captureArticlePdfPosition();
        window.compiling = buildOwner.compileArticle({ fromScratch });
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
            busy: !document.getElementById('articlePdfCompileStatus').hidden,
            version: host.querySelector('.articlePdfDocument')?.dataset.fixtureVersion,
            ready: visible.length > 0 && visible.every(p => p.dataset.pdfRenderState === 'done') });
          if (window.completedPdfRenders === completed) requestAnimationFrame(sample);
        };
        requestAnimationFrame(sample);
        finishCompile({ id: state.selectedId, status: 'success', pdf_url: '/fixture.pdf', compiled_at: `build-${completed + 1}` });
        return { position, completed, zoom: state.pdfZoom };
      }, { scrollTop, pages, fromScratch });
      await page.waitForFunction(completed => window.completedPdfRenders > completed, anchor.completed);
      await page.evaluate(() => window.compiling);
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
    assert.ok(rebuilt.frames.some(frame => frame.busy), 'compilation status remains through PDF loading');
    assert.ok(rebuilt.frames.every(frame => frame.position?.page === rebuilt.before.position.page), 'never paint the loading view or PDF top');
    assert.ok(rebuilt.frames.filter(frame => frame.version === 'build-1').every(frame => frame.ready), 'new visible pages are rendered before replacement');
    const shortened = await rebuildAt({ scrollTop: 4100, pages: 3, fromScratch: true });
    assert.equal(shortened.before.position.page, 5);
    assert.equal(shortened.after.position.page, 3);
    assert.ok(Math.abs(shortened.after.position.yRatio - shortened.before.position.yRatio) < .01);
    const failedCompile = await page.evaluate(async () => {
      const old = document.getElementById('articlePdfDocument');
      const compiling = buildOwner.compileArticle();
      failCompile(new Error('Fixture compilation failed'));
      await compiling;
      return document.getElementById('articlePdfCompileStatus').hidden && state.outputView === 'log'
        && !document.getElementById('articlePdfView').inert && old === document.getElementById('articlePdfDocument');
    });
    assert.ok(failedCompile, 'compilation failure clears the waiting status and retains the previous PDF');
    await page.evaluate(() => buildOwner.setOutputView('pdf'));
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
      pdfOwner.setPdfCompiling(true); pdfOwner.cancelPdf();
      state.selectedId = 'another-article'; state.build.compiled_at = 'another-article-build';
      await pdfOwner.renderPdf();
      await pending;
    });
    assert.equal(await page.evaluate(() => document.getElementById('articlePdfView').scrollTop), 0);
    assert.ok(await page.evaluate(() => document.getElementById('articlePdfCompileStatus').hidden));
    assert.equal(await page.evaluate(() => document.getElementById('articlePdfDocument').dataset.fixtureVersion), 'another-article-build');
    await enter({ user: 'other-reader' }); assert.equal(await page.evaluate(() => state.openFile), 'main.tex');
    await enter({ articleId: 'B' }); assert.equal(await page.evaluate(() => state.openFile), 'main.tex');
    await enter({ available: ['main.tex'] }); assert.equal(await page.evaluate(() => state.openFile), 'main.tex');
    await page.evaluate(async () => { state.pdfRestorePosition = { page: 90, xRatio: .5, yRatio: .5 }; pdfOwner.cancelPdf(); await pdfOwner.renderPdf(); });
    assert.equal(await page.evaluate(() => pdfOwner.captureArticlePdfPosition().page), 5);
    // Exercise the same transition with production CSS, PDF.js, canvases and
    // lazy page rendering, with the compilation status replacing the old PDF.
    const styles = Array.from(fs.readFileSync(path.join(STATIC, '../article_workspace.html'), 'utf8')
      .matchAll(/href="(\/static\/[^\"]+\.css)"/g), m => m[1]);
    for (const style of styles) await page.addStyleTag({ url: origin + style });
    const realSwap = await page.evaluate(async () => {
      const $ = id => document.getElementById(id), noop = () => {};
      for (const id of ['articleCodeEditor', 'articleMonacoEditor', 'articlePdfView']) $(id).removeAttribute('style');
      PortalArticleWorkspaceLayout.createLayout({
        ...Object.fromEntries(PortalArticleWorkspaceLayout.dependencies.map(k => [k, noop])), $, state,
        articleLayoutStore: { read: () => ({}), write: noop }, scheduleArticlePdfLayout: pdfOwner.scheduleArticlePdfLayout,
      }).initArticleWorkbench();
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      pdfOwner.cancelPdf(); window.PortalPdfPreview = window.realPdfPreview;
      const originalRender = window.realPdfPreview.render;
      const rendered = [];
      window.PortalPdfPreview = { ...window.realPdfPreview, render: config => originalRender({
        ...config, onPage: data => {
          if (config.version === 'real-rebuild') rendered.push(data.pageNumber);
          config.onPage?.(data);
        },
      }) };
      state.build.compiled_at = 'real-initial'; state.pdfZoom = 1.4;
      await pdfOwner.renderPdf();
      const host = document.getElementById('articlePdfView');
      host.scrollTop = 3200;
      const target = pdfOwner.captureArticlePdfPosition();
      const old = document.getElementById('articlePdfDocument');
      const frames = []; let finished = false;
      const sample = () => {
        const rect = host.getBoundingClientRect();
        const pages = Array.from(host.querySelectorAll('[data-pdf-page]')).filter(p => {
          const bounds = p.getBoundingClientRect(); return bounds.bottom > rect.top && bounds.top < rect.bottom;
        });
        frames.push({ page: pdfOwner.captureArticlePdfPosition()?.page,
          busy: !document.getElementById('articlePdfCompileStatus').hidden,
          yRatio: pdfOwner.captureArticlePdfPosition()?.yRatio,
          hitPage: Number(document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2)?.closest('[data-pdf-page]')?.dataset.pdfPage || 0),
          old: old === document.getElementById('articlePdfDocument'),
          ready: pages.length > 0 && pages.every(p => p.dataset.pdfRenderState === 'done' && p.querySelector('canvas')) });
        if (!finished) requestAnimationFrame(sample);
      };
      requestAnimationFrame(sample);
      const completed = window.completedPdfRenders;
      const compiling = buildOwner.compileArticle();
      finishCompile({ id: state.selectedId, status: 'success', pdf_url: '/fixture.pdf', compiled_at: 'real-rebuild' });
      const scale = Number(old.querySelector(`[data-pdf-page="${target.page}"]`).dataset.pdfScale);
      await compiling;
      while (window.completedPdfRenders === completed) await new Promise(resolve => requestAnimationFrame(resolve));
      finished = true;
      await new Promise(resolve => requestAnimationFrame(resolve));
      return { frames, target, result: pdfOwner.captureArticlePdfPosition(), scale, rendered,
        newScale: Number(host.querySelector(`[data-pdf-page="${target.page}"]`).dataset.pdfScale),
        staging: Boolean(document.querySelector('[data-article-pdf-staging]')) };
    });
    assert.ok(realSwap.frames.some(frame => frame.old) && realSwap.frames.some(frame => !frame.old));
    assert.ok(realSwap.rendered[0] > 1, 'recompile starts painting the restored viewport, not page one');
    assert.ok(Math.abs(realSwap.rendered[0] - realSwap.target.page) <= 1, 'first painted page belongs to the reading viewport');
    assert.ok(realSwap.frames.every(frame => frame.page === realSwap.target.page), 'real renderer never exposes a different page');
    assert.ok(realSwap.frames.every(frame => Math.abs(frame.yRatio - realSwap.target.yRatio) < .01),
      `the visible page position must remain stable: ${JSON.stringify(realSwap)}`);
    assert.ok(realSwap.frames.some(frame => frame.busy), 'real PDF loading shows compilation status');
    assert.ok(realSwap.frames.every(frame => frame.busy ? frame.hitPage === 0 : frame.hitPage === realSwap.target.page),
      'the viewport shows compilation status, then the reading page without exposing the PDF top');
    assert.ok(realSwap.frames.filter(frame => !frame.old).every(frame => frame.ready), 'new PDF has rendered canvases on its first visible frame');
    assert.ok(Math.abs(realSwap.target.yRatio - realSwap.result.yRatio) < .01);
    assert.equal(realSwap.newScale, realSwap.scale, 'the replacement keeps the same zoom');
    assert.equal(realSwap.staging, false);
    assert.deepEqual(errors, []);
    }
    // Full application boot and the actual compile button, without substituted
    // frontend owners: catalog refresh, shared controls and page events all run.
    const full = await browser.newPage({ viewport: { width: 1400, height: 900 } });
    const fullErrors = [];
    const fullRequests = [];
    full.on('request', request => { if (request.url().includes('/api/')) fullRequests.push(new URL(request.url()).pathname); });
    full.on('pageerror', error => fullErrors.push(error.message));
    await full.route('**/*', route => route.request().url().startsWith(origin + '/') ? route.continue() : route.abort());
    await full.goto(origin + '/full?item=article_000000000001');
    await full.waitForFunction(() => document.querySelectorAll('#articlePdfDocument [data-pdf-page]').length === 5, null, { timeout: 10000 }).catch(async error => {
      throw new Error(JSON.stringify({ error: error.message, fullErrors, fullRequests, state: await full.evaluate(() => ({
        ready: Boolean(window.ArticleWorkspaceReady), preview: document.getElementById('articlePdfView')?.textContent,
        notices: Array.from(document.querySelectorAll('[role="alert"],[data-portal-toast-stack]')).map(n => n.textContent),
      })) }));
    });
    await full.evaluate(async () => {
      await window.ArticleWorkspaceReady;
      const host = document.getElementById('articlePdfView');
      host.scrollTop = 1600;
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      window.pdfFrameSamples = [];
      window.pdfOldDocument = document.getElementById('articlePdfDocument');
      window.pdfTargetTop = host.scrollTop;
      window.pdfSampling = true;
      const sample = () => {
        const status = document.getElementById('articlePdfCompileStatus');
        const busy = !status.hidden;
        const rect = host.getBoundingClientRect();
        const pages = Array.from(host.querySelectorAll('[data-pdf-page]')).filter(p => {
          const bounds = p.getBoundingClientRect(); return bounds.bottom > rect.top && bounds.top < rect.bottom;
        });
        pdfFrameSamples.push({ top: host.scrollTop, old: pdfOldDocument === document.getElementById('articlePdfDocument'),
          busy, hidden: getComputedStyle(host).visibility === 'hidden',
          statusVisible: busy && status.getBoundingClientRect().width > 0,
          ready: pages.length > 0 && pages.every(p => p.dataset.pdfRenderState === 'done' && p.querySelector('canvas')) });
        if (pdfSampling) requestAnimationFrame(sample);
      };
      requestAnimationFrame(sample);
    });
    await full.click('#articleCompile');
    await full.waitForSelector('#articlePdfCompileStatus', { state: 'visible' });
    assert.match(await full.locator('#articlePdfCompileStatus').textContent(), /正在编译/);
    await full.waitForFunction(() => pdfOldDocument !== document.getElementById('articlePdfDocument') && !document.querySelector('[data-article-pdf-staging]'));
    await full.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    const fullResult = await full.evaluate(() => { pdfSampling = false; return { frames: pdfFrameSamples, top: pdfTargetTop }; });
    assert.ok(fullResult.frames.every(frame => Math.abs(frame.top - fullResult.top) < 2), JSON.stringify(fullResult));
    assert.ok(fullResult.frames.some(frame => frame.busy && frame.old), 'the actual compile button immediately shows status');
    assert.ok(fullResult.frames.filter(frame => frame.busy).every(frame => frame.hidden && frame.statusVisible),
      'the old PDF stays hidden throughout the slow build and PDF rendering');
    assert.ok(fullResult.frames.filter(frame => !frame.old).every(frame => frame.ready), 'replacement is painted before reveal');
    assert.ok(!fullResult.frames.at(-1).busy && !fullResult.frames.at(-1).hidden, 'the new PDF becomes visible after compiling');
    // Splitter motion must not repeatedly rescale/repaint the PDF or duplicate
    // Monaco's own automatic layout. Check the real application and PDF.js.
    await full.waitForTimeout(400);
    await full.evaluate(() => {
      const editor = monaco.editor.getEditors().find(e => document.getElementById('articleEditorPane').contains(e.getDomNode()));
      const layout = editor.layout.bind(editor);
      window.manualLayouts = 0;
      editor.layout = (...args) => { manualLayouts++; return layout(...args); };
      window.resizeEditor = editor;
      window.resizeSnapshot = () => {
        const host = document.getElementById('articlePdfView'), root = document.getElementById('articlePdfDocument');
        const bounds = host.getBoundingClientRect(), y = bounds.top + bounds.height / 2;
        const page = [...root.querySelectorAll('[data-pdf-page]')].find(p => {
          const rect = p.getBoundingClientRect(); return rect.top <= y && rect.bottom >= y;
        });
        const rect = page?.getBoundingClientRect();
        return { scales: Number(root.dataset.pdfPreviewScaleCount || 0), redraws: Number(root.dataset.pdfRedrawCount || 0),
          page: page?.dataset.pdfPage, ratio: rect ? (y - rect.top) / rect.height : null,
          editorWidth: editor.getLayoutInfo().width, manualLayouts, position: editor.getPosition() };
      };
    });
    const resizeBefore = await full.evaluate(() => resizeSnapshot());
    const handle = await full.evaluate(() => {
      const edge = document.getElementById('articleEditorPane').getBoundingClientRect().right;
      const handles = [...document.querySelectorAll('#articleWorkspaceGrid .portalWorkbenchSplitHandle')]
        .map(el => el.getBoundingClientRect()).filter(r => r.height > r.width);
      const rect = handles.sort((a,b) => Math.abs(a.left-edge)-Math.abs(b.left-edge))[0];
      return {x: rect.left+rect.width/2, y: rect.top+rect.height/2};
    });
    await full.mouse.move(handle.x,handle.y); await full.mouse.down();
    for (let i=1;i<=24;i++) {
      await full.mouse.move(handle.x-i*3,handle.y);
      await full.evaluate(() => new Promise(resolve => requestAnimationFrame(resolve)));
    }
    await full.waitForTimeout(350); // A pause while held must not start PDF raster work.
    const resizeDuring = await full.evaluate(() => resizeSnapshot());
    await full.mouse.up();
    await full.waitForFunction(count => Number(document.getElementById('articlePdfDocument').dataset.pdfRedrawCount || 0)>count,resizeBefore.redraws);
    const resizeAfter = await full.evaluate(() => resizeSnapshot());
    console.log('resize work', JSON.stringify({before:resizeBefore,during:resizeDuring,after:resizeAfter}));
    assert.ok(resizeDuring.editorWidth < resizeBefore.editorWidth-40, 'Monaco follows the live splitter');
    assert.equal(resizeDuring.manualLayouts,0,'live drag relies on Monaco automatic layout only');
    assert.equal(resizeDuring.scales,resizeBefore.scales,'PDF scale calculations wait for release');
    assert.equal(resizeDuring.redraws,resizeBefore.redraws,'PDF rasterization waits for release');
    assert.equal(resizeAfter.scales,resizeBefore.scales+1,'one final PDF scale update');
    assert.equal(resizeAfter.redraws,resizeBefore.redraws+1,'one final PDF redraw');
    assert.equal(resizeAfter.page,resizeBefore.page);
    assert.ok(Math.abs(resizeAfter.ratio-resizeBefore.ratio)<.015,'resize keeps the reading anchor');
    assert.deepEqual(resizeAfter.position,resizeBefore.position,'resize keeps the editor caret');
    assert.deepEqual(fullErrors, []);
    await full.close();
    console.log('PASS: compilation status until rendered PDF replacement; preserved reading position; failure and navigation cleanup');
  } finally { await browser?.close(); await new Promise(resolve => server.close(resolve)); }
})().catch(error => { console.error(error); process.exitCode = 1; });
