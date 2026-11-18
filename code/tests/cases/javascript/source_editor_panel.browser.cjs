'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), http = require('node:http');
const { STATIC, portalWorkbenchScripts } = require('./portal_workbench_scripts.cjs');
const { babelParse } = require('./babel_parse.cjs');
const { chromium } = require(path.join(STATIC, '../test_framework/tau_runtime/node_modules/playwright'));

(async () => {
  let browser;
  const server = http.createServer((req, res) => {
    if (req.url === '/') {
      res.setHeader('Content-Type', 'text/html; charset=utf-8');
      return res.end('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"></head><body><main id="workbench" style="height:760px"></main></body></html>');
    }
    const rel = decodeURIComponent(req.url.split('?')[0]).replace(/^\/static\//, '');
    if (!req.url.startsWith('/static/') || rel.includes('..')) { res.writeHead(404); return res.end(); }
    try {
      res.setHeader('Content-Type', rel.endsWith('.css') ? 'text/css; charset=utf-8' : rel.endsWith('.ttf') ? 'font/ttf' : 'text/javascript; charset=utf-8');
      res.end(fs.readFileSync(path.join(STATIC, rel)));
    } catch { res.writeHead(404); res.end(); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  try {
    browser = await chromium.launch({ headless: true });
    const page = await browser.newPage({ viewport: { width: 1500, height: 900 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const origin = `http://127.0.0.1:${server.address().port}`;
    await page.route('**/*', route => route.request().url().startsWith(origin + '/') ? route.continue() : route.abort());
    await page.goto(origin);
    for (const file of ['portal_workbench.css', 'portal_source_editor_panel.css']) {
      await page.addStyleTag({ url: origin + '/static/' + file });
    }
    await page.addStyleTag({ content: ':root{--panel:#fff;--panel-2:#f5f6f8;--text:#172233;--muted:#667080;--border:#d3d9e4;--accent:#4566c5;--mono:monospace}body{margin:8px}' });
    const state = fs.readFileSync(path.join(STATIC, 'portal_state.js'), 'utf8');
    const first = babelParse(state, 'portal_state.js').program.body[0];
    await page.addScriptTag({ content: state.slice(first.start, first.end) });
    for (const file of ['portal_text.js', 'portal_ui.js', 'portal_ui_primitives.js', 'portal_editor_open_service.js',
      'portal_command_registry.js', 'portal_workbench_context.js', ...portalWorkbenchScripts(),
      'portal_document_statistics.js', 'portal_code_editor.js', 'portal_source_editor_panel.js', 'vendor/monaco/vs/loader.js']) {
      await page.addScriptTag({ url: origin + '/static/' + file });
    }
    await page.evaluate(async () => {
      const monaco = await PortalCodeEditor.loadMonaco();
      const createEditor = monaco.editor.create.bind(monaco.editor);
      window.editors = [];
      monaco.editor.create = (...args) => { const editor = createEditor(...args); editors.push(editor); return editor; };
      window.disk = {
        'src/main.js': { path: 'src/main.js', content: '  let answer = 41;\nconst other = 1;\n', hash: 'one', editable: true },
        'README.md': { path: 'README.md', content: '# Hello\n\nWorld\n', hash: 'two', editable: true },
        'main.tex': { path: 'main.tex', content: '\\documentclass{article}\n\\begin{document}Hi\\end{document}', hash: 'three', editable: true },
        'readonly.txt': { path: 'readonly.txt', content: 'Read me', hash: 'four', editable: false },
      };
      window.reads = []; window.writes = []; window.references = []; window.previews = []; window.failures = [];
      window.committed = []; window.fileReferences = [];
      window.holdSave = false; window.failSave = false;
      window.adapter = {
        scopeKey: 'repo-fixture',
        async readFile(path) { reads.push(path); if (!disk[path]) throw new Error('File removed'); return structuredClone(disk[path]); },
        async saveFile(path, content, hash) {
          if (failSave) throw new Error('offline');
          if (holdSave) await new Promise(resolve => { window.finishSave = resolve; });
          if (disk[path].hash !== hash) throw Object.assign(new Error('conflict'), { status: 409 });
          writes.push({ path, content, hash });
          disk[path] = { ...disk[path], content, hash: 'saved-' + writes.length };
          return structuredClone(disk[path]);
        },
        onSelection: reference => references.push(reference), onPreview: doc => previews.push(doc),
        onSelectionCommitted: reference => committed.push(reference),
        onFileReference: reference => fileReferences.push(reference),
        onError: error => failures.push(error.message),
      };
      PortalWorkbench.registerPanelPackage({ type: 'source-editor-fixture', version: 1,
        presentation: { title: 'Navigation', icon: 'files' }, sizing: { minWidth: 100, minHeight: 100 },
        instances: 'multiple', capabilities: [], behavior: { canMove: true }, context: { channels: [] },
        persistence: { schemaVersion: 1 }, create: options => { options.root.textContent = 'Files'; return options.root; } });
      const { stack, split } = PortalWorkbench.model;
      window.savedWorkbench = null;
      window.wb = PortalWorkbench.create({ id: 'source-panel-fixture', root: '#workbench',
        store: { read: () => ({}), replace: value => { savedWorkbench = value; return value; } },
        initialEditors: [{ id: 'navigation', type: 'source-editor-fixture' }, { id: 'code', type: 'source-editor', options: adapter }],
        defaultLayout: split('horizontal', [stack(['navigation']), stack(['code'])], [1, 4]),
      });
      window.invoke = (method, ...args) => wb.invokePanel('code', method, ...args);
      await invoke('openFile', 'src/main.js');
    });
    if (process.argv.includes('--git-deleted')) {
      await page.evaluate(async () => {
        window.beforeReads = reads.length;
        window.beforeModels = monaco.editor.getModels().length;
        await invoke('showGitDiff', 'deleted.txt', { diff: 'diff --git a/deleted.txt b/deleted.txt\ndeleted file mode 100644\n--- a/deleted.txt\n+++ /dev/null\n@@ -1 +0,0 @@\n-deleted content\n' });
      });
      assert.equal(await page.evaluate(() => reads.length === beforeReads), true, 'Git diffs never read a deleted file');
      assert.equal(await page.evaluate(() => editors.at(-1).getValue().includes('-deleted content')), true);
      assert.ok(await page.evaluate(() => editors.at(-1).getModel().getAllDecorations().some(d => d.options.className === 'portalDiffLineDelete')));
      assert.equal(await page.evaluate(() => invoke('activeDocument').path), 'src/main.js', 'diff leaves the active source buffer intact');
      await page.getByRole('button', { name: '返回编辑', exact: true }).click();
      assert.equal(await page.evaluate(() => monaco.editor.getModels().length === beforeModels), true);
      await page.evaluate(() => wb.destroy());
      assert.equal(await page.evaluate(() => monaco.editor.getModels().length), 0);
      assert.deepEqual(errors, []);
      console.log('source-editor deleted Git diff: supplied patch rendered without disk reads, source preserved, models disposed');
      return;
    }
    assert.equal(await page.evaluate(() => invoke('activeDocument').path), 'src/main.js');
    await page.evaluate(() => editors[0].setSelection({ startLineNumber: 1, startColumn: 1, endLineNumber: 1, endColumn: 19 }));
    // Actual Monaco context menu; references preserve whitespace and revision.
    const point = await page.evaluate(() => {
      const editor = editors[0], p = editor.getScrolledVisiblePosition({ lineNumber: 1, column: 5 }), box = editor.getDomNode().getBoundingClientRect();
      return { x: box.left + p.left + 2, y: box.top + p.top + p.height / 2 };
    });
    await page.mouse.click(point.x, point.y, { button: 'right' });
    const menu = page.locator('.monaco-menu .action-label').filter({ hasText: /^加入聊天$/ });
    await menu.waitFor({ timeout: 5000 }).catch(async error => {
      console.error('menu evidence', point, await page.evaluate(() => ({
        root: document.querySelector('.portalSourceEditor').getBoundingClientRect().toJSON(),
        editor: editors[0].getDomNode().getBoundingClientRect().toJSON(),
        menu: [...document.querySelectorAll('[role="menuitem"]')].map(node => node.textContent),
        supported: editors[0].getAction('portal.source.addToChat')?.isSupported(),
      })));
      await page.screenshot({ path: '/tmp/source-editor-menu.png' });
      throw error;
    });
    await page.waitForTimeout(150);
    await menu.click();
    await page.waitForFunction(() => references.length > 0, null, { timeout: 5000 });
    assert.deepEqual(await page.evaluate(() => references[0].selection), {
      start_line: 1, start_column: 1, end_line: 1, end_column: 19, text: '  let answer = 41;',
    });
    assert.equal(await page.evaluate(() => references[0].hash), 'one');
    assert.equal(await page.evaluate(() => 'content' in references[0]), false);
    // Persistent tabs preserve the model, selection and undo stack.
    await page.evaluate(async () => {
      editors[0].executeEdits('fixture', [{ range: new monaco.Range(1, 16, 1, 18), text: '42' }]);
      editors[0].setSelection(new monaco.Range(1, 3, 1, 6));
      window.beforeModel = editors[0].getModel();
      await invoke('openFile', 'README.md');
    });
    await page.getByRole('tab', { name: 'main.js ●', exact: true }).click();
    assert.equal(await page.evaluate(() => editors[0].getModel() === beforeModel), true);
    assert.equal(await page.evaluate(() => invoke('activeDocument').selection.text), 'let');
    // The shortcut routes to the focused tab even after another editor was mounted.
    await page.evaluate(() => editors[0].focus());
    await page.keyboard.press('Control+Alt+Enter');
    await page.waitForFunction(() => references.length === 2);
    assert.equal(await page.evaluate(() => references[1].path), 'src/main.js');
    assert.equal(await page.evaluate(() => references[1].selection.text), 'let');
    assert.equal(await page.evaluate(() => reads.filter(p => p === 'src/main.js').length), 1);
    assert.equal(await page.evaluate(() => committed.length), 0, 'restore, keyboard and right click do not auto-add');
    await page.evaluate(() => editors[0].setPosition({ lineNumber: 2, column: 1 }));
    const dragPoints = await page.evaluate(() => {
      const editor = editors[0], box = editor.getDomNode().getBoundingClientRect();
      return [3, 6].map(column => {
        const p = editor.getScrolledVisiblePosition({ lineNumber: 1, column });
        return { x: box.left + p.left, y: box.top + p.top + p.height / 2 };
      });
    });
    await page.mouse.move(dragPoints[0].x, dragPoints[0].y); await page.mouse.down();
    await page.mouse.move(dragPoints[1].x, dragPoints[1].y, { steps: 8 });
    assert.equal(await page.evaluate(() => committed.length), 0, 'dragging does not emit partial selections');
    await page.mouse.up();
    await page.waitForFunction(() => committed.length === 1, null, { timeout: 5000 }).catch(async error => {
      console.error('mouse selection', await page.evaluate(() => ({ selection: invoke('activeDocument').selection, committed, focus: editors[0].hasTextFocus() })));
      throw error;
    });
    assert.equal(await page.evaluate(() => committed[0].selection.text), 'let');
    assert.equal(await page.evaluate(() => editors[0].hasTextFocus()), true);
    await page.keyboard.press('Control+Alt+Shift+Enter');
    await page.waitForFunction(() => fileReferences.length === 1);
    assert.equal(await page.evaluate(() => fileReferences[0].content), '  let answer = 42;\nconst other = 1;\n');
    assert.equal(await page.evaluate(() => fileReferences[0].dirty), true);
    assert.equal(await page.evaluate(() => fileReferences[0].selection), null);
    assert.equal(await page.evaluate(() => fileReferences[0].hash), 'one');
    // Changing Workbench placement moves the existing root and editor instance.
    await page.evaluate(() => {
      window.originalRoot = document.querySelector('.portalSourceEditor');
      wb.applyWorkspace({ name: 'Vertical', layout: PortalWorkbench.model.split('vertical', [PortalWorkbench.model.stack(['code']), PortalWorkbench.model.stack(['navigation'])], [4, 1]), hidden: [] });
    });
    assert.equal(await page.evaluate(() => originalRoot === document.querySelector('.portalSourceEditor') && editors[0].getModel() === beforeModel), true);
    assert.equal(await page.evaluate(() => invoke('activeDocument').selection.text), 'let');
    // Ctrl+S saves the selected file; later typing stays dirty during slow saves.
    await page.evaluate(() => { editors[0].focus(); holdSave = true; });
    await page.waitForFunction(() => editors[0].hasTextFocus(), null, { timeout: 5000 });
    await page.keyboard.press('Control+s');
    await page.waitForFunction(() => typeof finishSave === 'function', null, { timeout: 5000 }).catch(async error => {
      console.error('save evidence', await page.evaluate(() => ({ active: document.activeElement.outerHTML.slice(0, 450), doc: invoke('activeDocument'), focus: editors[0].hasTextFocus(), failures })));
      throw error;
    });
    await page.evaluate(() => { editors[0].executeEdits('fixture', [{ range: new monaco.Range(2, 1, 2, 1), text: '// more\n' }]); finishSave(); holdSave = false; });
    await page.waitForFunction(() => writes.length === 1 && !invoke('activeDocument').saving);
    assert.equal(await page.evaluate(() => invoke('activeDocument').dirty), true);
    assert.equal(await page.evaluate(() => disk['src/main.js'].content.includes('// more')), false);
    await page.evaluate(() => invoke('saveAll'));
    assert.equal(await page.evaluate(() => invoke('hasDirty')), false);
    // Clean buffers follow external edits while retaining position.
    await page.evaluate(async () => {
      editors[0].setPosition({ lineNumber: 2, column: 3 });
      disk['src/main.js'] = { ...disk['src/main.js'], content: '  let answer = 99;\n// more\nconst other = 1;\n', hash: 'external-clean' };
      await invoke('refresh');
    });
    assert.equal(await page.evaluate(() => invoke('activeDocument').content.startsWith('  let answer = 99;')), true);
    assert.equal(await page.evaluate(() => editors[0].getPosition().lineNumber), 2);
    // Dirty buffers preserve drafts and require an explicit conflict decision.
    await page.evaluate(async () => {
      await invoke('openFile', 'README.md');
      editors[1].setValue('# Local draft\n');
      disk['README.md'] = { ...disk['README.md'], content: '# Remote draft\n', hash: 'external-dirty' };
      await invoke('refresh');
    });
    assert.equal(await page.evaluate(() => invoke('activeDocument').content), '# Local draft\n');
    await page.getByRole('button', { name: '比较改动', exact: true }).click();
    await page.waitForSelector('.portalSourceEditorDiff .monaco-diff-editor');
    await page.evaluate(() => { disk['README.md'] = { ...disk['README.md'], content: '# Newer remote\n', hash: 'external-again' }; });
    await page.getByRole('button', { name: '保留我的版本并保存', exact: true }).click();
    await page.waitForFunction(() => failures.includes('conflict'));
    assert.equal(await page.evaluate(() => disk['README.md'].content), '# Newer remote\n');
    assert.equal(await page.evaluate(() => invoke('activeDocument').content), '# Local draft\n');
    await page.getByRole('button', { name: '比较改动', exact: true }).click();
    await page.waitForSelector('.portalSourceEditorDiff .monaco-diff-editor');
    await page.getByRole('button', { name: '保留我的版本并保存', exact: true }).click();
    await page.waitForFunction(() => disk['README.md'].content === '# Local draft\n');
    await page.getByRole('button', { name: '预览', exact: true }).click();
    assert.equal(await page.evaluate(() => previews.at(-1).content), '# Local draft\n');
    await page.evaluate(() => invoke('openFile', 'main.tex'));
    await page.getByRole('button', { name: '预览', exact: true }).click();
    assert.equal(await page.evaluate(() => previews.at(-1).path), 'main.tex');
    // The Git view accepts the backend payload and raw patches; it cannot
    // modify a source buffer or leave its temporary model behind.
    await page.evaluate(async () => {
      window.beforeGitModels = monaco.editor.getModels().length;
      await invoke('showGitDiff', 'README.md', { diff: 'diff --git a/README.md b/README.md\n--- a/README.md\n+++ b/README.md\n@@ -1 +1 @@\n-old\n+new\n' });
    });
    assert.equal(await page.getByRole('button', { name: '保存', exact: true }).isDisabled(), true);
    assert.equal(await page.evaluate(() => editors.at(-1).getOption(monaco.editor.EditorOption.readOnly)), true);
    assert.ok(await page.evaluate(() => editors.at(-1).getModel().getAllDecorations().some(d => d.options.className === 'portalDiffLineAdd')));
    await page.getByRole('button', { name: '返回编辑', exact: true }).click();
    assert.equal(await page.evaluate(() => monaco.editor.getModels().length === beforeGitModels), true);
    assert.equal(await page.evaluate(() => invoke('activeDocument').path), 'main.tex');
    await page.evaluate(() => invoke('showGitDiff', 'README.md', 'plain patch'));
    assert.equal(await page.evaluate(() => editors.at(-1).getValue()), 'plain patch');
    await page.getByRole('button', { name: '返回编辑', exact: true }).click();
    // Read-only documents can be browsed and selected but never saved.
    await page.evaluate(() => invoke('openFile', 'readonly.txt'));
    assert.equal(await page.getByRole('button', { name: '保存', exact: true }).isDisabled(), true);
    assert.equal(await page.evaluate(() => editors.find(e => e.getValue() === 'Read me').getOption(monaco.editor.EditorOption.readOnly)), true);
    // Failed saves, closing a dirty tab, and reload preserve the draft.
    await page.evaluate(async () => { await invoke('openFile', 'README.md'); editors[1].setValue('# Keep this\n'); failSave = true; });
    await page.getByRole('button', { name: '保存', exact: true }).click();
    await page.waitForFunction(() => failures.includes('offline'));
    await page.getByRole('button', { name: '关闭 README.md', exact: true }).click();
    await page.getByRole('button', { name: '取消', exact: true }).click();
    assert.equal(await page.evaluate(() => invoke('activeDocument').content), '# Keep this\n');
    await page.evaluate(async () => {
      window.snapshot = invoke('snapshot');
      window.expectedModels = monaco.editor.getModels().length;
      wb.destroy();
      window.remainingModels = monaco.editor.getModels().length;
      disk['README.md'] = { ...disk['README.md'], content: '# Changed while away\n', hash: 'away' };
      const target = document.getElementById('workbench');
      window.restored = PortalWorkbench.createPanel('source-editor', { ...adapter, root: target });
      await restored.restore(snapshot);
    });
    assert.equal(await page.evaluate(() => remainingModels), 0, 'all editor and diff models disposed');
    assert.equal(await page.evaluate(() => restored.activeDocument().content), '# Keep this\n');
    assert.equal(await page.evaluate(() => restored.activeDocument().conflict), true);
    assert.equal(await page.evaluate(() => restored.hasDirty()), true);
    // Destroy while a file is in flight must not append late editors.
    await page.evaluate(async () => {
      restored.destroy();
      const root = document.getElementById('workbench');
      let deliver;
      const pending = PortalSourceEditorPanel.create({ root, readFile: () => new Promise(resolve => { deliver = resolve; }) });
      const opening = pending.openFile('pending.txt');
      pending.destroy();
      deliver({ content: 'late', hash: 'late', editable: true });
      await opening;
    });
    assert.equal(await page.evaluate(() => monaco.editor.getModels().length), 0);
    assert.deepEqual(errors, []);
    console.log('source-editor: real Monaco + Workbench tabs/layout, selection chat, preview, CAS conflicts, concurrent typing, read-only, draft restore, disposal passed');
  } finally {
    await browser?.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
