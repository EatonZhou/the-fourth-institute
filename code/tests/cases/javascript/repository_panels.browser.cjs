'use strict';
const assert = require('node:assert/strict');
const path = require('node:path');
const { chromium } = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const { portalWorkbenchScripts, STATIC } = require('./portal_workbench_scripts.cjs');

(async () => {
  const browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1600, height: 900}});
    page.setDefaultTimeout(5000);
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => route.request().url() === 'http://repository.fixture/'
      ? route.fulfill({contentType: 'text/html', body: '<main style="height:800px;display:grid;grid-template-columns:250px 250px 300px 1fr"><section id="files"></section><section id="search"></section><section id="git"></section><section id="preview"></section></main>'})
      : route.fulfill({status: 404, body: ''}));
    await page.goto('http://repository.fixture/');
    await page.addStyleTag({path: path.join(STATIC, 'portal_repository_panels.css')});
    await page.evaluate(() => {
      const tag = (parts, ...values) => parts.reduce((text, part, i) => text + part + (values[i] ?? ''), '');
      window.PortalI18n = {t: x => x, text: tag, html: tag};
    });
    for (const file of ['portal_state.js', 'portal_text.js', 'portal_ui.js', 'portal_ui_primitives.js', 'portal_editor_open_service.js',
      'portal_command_registry.js', 'portal_workbench_context.js', ...portalWorkbenchScripts(), 'markdown.js', 'portal_repository_panels.js']) {
      await page.addScriptTag({path: path.join(STATIC, file)});
    }
    await page.evaluate(() => {
      window.reads = []; window.opened = []; window.diffs = []; window.failures = []; window.pdfDocuments = []; window.compiles = []; window.cacheReads = []; window.saveOrder = []; window.waitingSearch = {};
      const api = {get: async url => {
        reads.push(url); const parsed = new URL(url, location.href), suffix = parsed.pathname.split('/workspace')[1];
        if (suffix === '/tree') {
          const directory = parsed.searchParams.get('path');
          return {entries: directory === 'docs' ? [{name: 'guide.md', path: 'docs/guide.md', kind: 'file'}]
            : [{name: 'docs', path: 'docs', kind: 'directory'}, {name: '<script>.py', path: '<script>.py', kind: 'file'}]};
        }
        if (suffix === '/search') {
          const query = parsed.searchParams.get('q');
          return new Promise(resolve => { waitingSearch[query] = resolve; });
        }
        if (suffix === '/diff') return {path: 'src/main.py', diff: '- old\n+ new'};
        if (suffix === '') return {capabilities: {git: true}, current_branch: 'main', status: {changes: [{path: 'src/main.py', code: ' M'}]}};
        throw new Error('Unexpected request ' + url);
      }};
      PortalWorkbench.registerPanelPackage({type: 'preview', version: 1, presentation: {title: 'PDF', icon: 'window'},
        sizing: {minWidth: 200, minHeight: 180}, instances: 'multiple', capabilities: ['preview'],
        behavior: {canMove: true}, context: {channels: []}, persistence: {schemaVersion: 1},
        create: ({root}) => ({root, setDocument: async value => { pdfDocuments.push(value); root.textContent = 'PDF:' + value.rawUrl; }, destroy() { root.replaceChildren(); }, show() {}, hide() {}})});
      PortalRepositoryPanels.register(); PortalRepositoryPanels.register();
      const shared = {repoId: 'repo_123456789abc', api, onOpenFile: path => opened.push(path), onDiff: (path, diff) => diffs.push({path, diff}), onError: error => failures.push(error.message)};
      window.files = PortalWorkbench.createPanel('repository-files', {...shared, root: document.getElementById('files')});
      window.search = PortalWorkbench.createPanel('repository-search', {...shared, root: document.getElementById('search')});
      window.git = PortalWorkbench.createPanel('repository-git', {...shared, root: document.getElementById('git')});
      window.preview = PortalWorkbench.createPanel('repository-preview', {...shared, root: document.getElementById('preview'),
        loadLatex: async (path, compiler) => { cacheReads.push({path, compiler});
          if (window.holdCache) return new Promise(resolve => { window.resolveCache = resolve; });
          return window.cachedReport || null;
        },
        beforeCompile: async () => { saveOrder.push('saved'); }, compileLatex: async (path, compiler) => { saveOrder.push('compile'); compiles.push({path, compiler});
          if (window.holdCompile) return new Promise(resolve => { window.resolveCompile = resolve; });
          if (window.throwCompile) throw Object.assign(new Error('HTTP 422'), {payload: {report: {status: 'error', log: 'Compiler report from 422'}}});
          if (window.failCompile) return {status: 'error', log: 'main.tex:1: Error', error: 'TeX failed', pdf_available: false};
          return {status: 'success', primary_file: 'main.tex', pdf_available: true, pdf_url: '/preview/compiled.pdf?path=main.tex', compiled_at: 'revision-' + compiles.length, log: 'Build complete'};
        }});
      files.show(); git.show();
    });
    await page.locator('#files [data-repository-path="docs"]').waitFor();
    assert.equal(await page.evaluate(() => reads.filter(x => x.includes('/tree')).length), 1, 'folder contents are lazy');
    await page.locator('#files [data-repository-path="docs"]').click();
    await page.locator('#files [data-repository-path="docs/guide.md"]').click();
    assert.deepEqual(await page.evaluate(() => opened), ['docs/guide.md']);
    await page.evaluate(() => { files.hide(); const root = files.root; document.querySelector('main').append(root); files.show(); });
    assert.equal(await page.evaluate(() => reads.filter(x => x.includes('/tree')).length), 2, 'moving or showing retains loaded tree');
    assert.equal(await page.locator('#files script').count(), 0, 'file names are text');
    await page.evaluate(() => files.refresh());
    assert.equal(await page.locator('#files [data-repository-path="docs"]').getAttribute('aria-expanded'), 'true');
    assert.equal(await page.evaluate(() => reads.filter(x => x.includes('/tree')).length), 4, 'refresh only reads root and expanded folders');

    await page.locator('#search input').fill('old');
    await page.locator('#search form').evaluate(form => form.requestSubmit());
    await page.waitForFunction(() => waitingSearch.old);
    await page.locator('#search input').fill('new');
    await page.locator('#search form').evaluate(form => form.requestSubmit());
    await page.waitForFunction(() => waitingSearch.new);
    await page.evaluate(() => { waitingSearch.new({results: [{path: 'new.py', name: 'new.py', directory: ''}]}); waitingSearch.old({results: [{path: 'old.py', name: 'old.py', directory: ''}]}); });
    await page.locator('#search [data-repository-path="new.py"]').click();
    assert.equal(await page.locator('#search [data-repository-path="old.py"]').count(), 0, 'stale search is ignored');
    await page.locator('#git [data-repository-diff]').click();
    await page.waitForFunction(() => diffs.length === 1);
    assert.equal(await page.evaluate(() => diffs[0].diff.diff), '- old\n+ new');

    await page.evaluate(() => preview.setDocument({path: 'docs/guide.md', content: '# Draft\n\n![pic](../images/chart.png)\n\n[Source](../src/main.py)', dirty: true}));
    assert.equal(await page.locator('#preview h1').textContent(), 'Draft');
    assert.match(await page.locator('#preview img').getAttribute('src'), /repo_123456789abc%2Ffile%2Fimages%2Fchart.png/);
    await page.locator('#preview a').click();
    assert.equal(await page.evaluate(() => opened.at(-1)), 'src/main.py');
    await page.evaluate(() => preview.setDocument({path: 'docs/guide.md', content: '# New draft', dirty: true}));
    assert.equal(await page.locator('#preview h1').textContent(), 'New draft');
    assert.equal(await page.evaluate(() => compiles.length), 0, 'Markdown updates never compile');
    await page.evaluate(() => preview.setDocument({path: 'chapters/introduction.tex', content: 'text', dirty: true}));
    assert.equal(await page.evaluate(() => compiles.length), 0, 'opening tex does not compile');
    assert.deepEqual(await page.evaluate(() => cacheReads[0]), {path: 'chapters/introduction.tex', compiler: ''}, 'cache lookup uses automatic compiler');
    await page.locator('#preview .repositoryLatexToolbar input').fill('main.tex');
    await page.locator('#preview .repositoryLatexToolbar input').dispatchEvent('change');
    assert.equal(await page.locator('#preview select').inputValue(), '', 'default compiler follows source magic');
    await page.locator('#preview form button').click();
    await page.waitForFunction(() => pdfDocuments.length === 1);
    assert.deepEqual(await page.evaluate(() => saveOrder), ['saved', 'compile']);
    assert.deepEqual(await page.evaluate(() => compiles[0]), {path: 'main.tex', compiler: ''});
    assert.equal(await page.evaluate(() => pdfDocuments[0].kind), 'pdf');
    assert.equal(await page.evaluate(() => new URL(pdfDocuments[0].rawUrl, location.href).searchParams.get('v')), 'revision-1', 'PDF URL is versioned by compilation');
    assert.equal(await page.locator('#preview .repositoryCompileLog pre').textContent(), 'Build complete');
    await page.locator('#preview select').selectOption('xelatex');
    await page.evaluate(() => preview.setDocument({path: 'chapters/conclusion.tex', content: 'next'}));
    assert.equal(await page.locator('#preview .repositoryLatexToolbar input').inputValue(), 'main.tex', 'explicit main persists across included files');
    const snapshot = await page.evaluate(() => preview.snapshot());
    assert.equal(snapshot.compiler, 'xelatex'); assert.ok(!('content' in snapshot), 'snapshot is UI state, not file contents');
    await page.evaluate(async () => { window.failCompile = true; await preview.compile(); });
    assert.equal(await page.locator('#preview .repositoryCompileLog pre').textContent(), 'main.tex:1: Error');
    assert.equal(await page.locator('#preview .repositoryPdfHost').isVisible(), true, 'failed build retains prior successful preview');
    assert.equal(await page.locator('#preview form button').isEnabled(), true);
    await page.evaluate(async () => { window.failCompile = false; window.throwCompile = true; await preview.compile(); });
    assert.equal(await page.locator('#preview .repositoryCompileLog pre').textContent(), 'Compiler report from 422', 'HTTP error reports remain reviewable');

    await page.evaluate(() => { window.throwCompile = false; window.holdCompile = true; void preview.compile(); });
    await page.waitForFunction(() => window.resolveCompile);
    await page.evaluate(async () => {
      await preview.setDocument({path: 'README.md', content: '# Another document'});
      resolveCompile({status: 'succeeded', pdf_url: '/stale.pdf'});
    });
    await page.waitForFunction(() => document.querySelector('#preview h1')?.textContent === 'Another document');
    assert.equal(await page.evaluate(() => pdfDocuments.length), 1, 'late compile cannot replace a different document');
    assert.equal(await page.locator('#preview .repositoryPdfHost').isVisible(), false);
    await page.evaluate(() => preview.setDocument(null));
    assert.match(await page.locator('#preview .repositoryPanelHint').textContent(), /打开 Markdown/);
    const buildsBeforeCache = await page.evaluate(() => compiles.length);
    await page.evaluate(async () => {
      window.cachedReport = {status: 'warning', primary_file: 'main.tex', pdf_available: true, pdf_url: '/preview/compiled.pdf?path=main.tex', compiled_at: 'cached-revision', log: 'Cached result'};
      await preview.setDocument({path: 'main.tex', content: 'tex'});
    });
    assert.equal(await page.evaluate(() => compiles.length), buildsBeforeCache, 'cache read never starts a build');
    assert.equal(await page.evaluate(() => pdfDocuments.length), 2);
    assert.equal(await page.evaluate(() => new URL(pdfDocuments[1].rawUrl, location.href).searchParams.get('v')), 'cached-revision');
    await page.evaluate(async () => { window.holdCompile = false; window.cachedReport = null; await preview.compile(); });
    assert.equal(await page.evaluate(() => pdfDocuments.length), 3, 'recompile of same main updates the PDF');
    assert.notEqual(await page.evaluate(() => pdfDocuments[2].rawUrl), await page.evaluate(() => pdfDocuments[1].rawUrl));
    await page.evaluate(() => { window.holdCache = true; void preview.loadCached(); });
    await page.waitForFunction(() => window.resolveCache);
    await page.evaluate(async () => { await preview.setDocument({path: 'OTHER.md', content: '# Current'}); resolveCache({pdf_url: '/stale-cache.pdf', status: 'success', pdf_available: true}); });
    assert.equal(await page.locator('#preview h1').textContent(), 'Current');
    assert.equal(await page.evaluate(() => pdfDocuments.length), 3, 'stale cache result cannot replace the new document');
    assert.deepEqual(await page.evaluate(() => failures), ['TeX failed', 'HTTP 422']);
    await page.evaluate(() => { files.destroy(); search.destroy(); git.destroy(); preview.destroy(); });
    assert.deepEqual(errors, []);
    console.log('Repository packages: lazy tree, move persistence, search races, Git diff, Markdown draft/links, explicit LaTeX compile, failure retention, stale-result guard passed.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
