'use strict';
// One browser/page, sequential cases, fixture-only HTTP. No live Portal access.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const root = path.resolve(__dirname, '../../..');
const { chromium } = require(path.join(root, 'management_portal/test_framework/tau_runtime/node_modules/playwright'));
assert.ok(process.argv.includes('--workers=1'), 'This browser case must run serially');
const id = 'concept_123456789abc', other = 'concept_abcdef123456';
const source = name => fs.readFileSync(path.join(root, 'management_portal/static', name), 'utf8');
const scripts = ['portal_ui_primitives.js', 'portal_overlay.js', 'portal_ui.js', 'portal_api.js',
  'portal_instruction_client.js', 'portal_identity.js', 'portal_workspace_surface_session_transitions.js',
  'portal_workspace_surface_session_loading.js', 'portal_workspace_surface_session_editing.js',
  'portal_workspace_surface.js', 'knowledge_board_detail_metadata.js', 'knowledge_board_detail_files.js',
  'knowledge_board_detail.js'];
const htmlPath = 'knowledge_board_lan/knowledge_board.html';
const html = fs.readFileSync(path.join(root, htmlPath), 'utf8')
  .replace(/<script\b[^>]*>[\s\S]*?<\/script>/g, '').replace(/<link\b[^>]*>/g, '');

(async () => {
  const browser = await chromium.launch({ headless: true });
  const posts = [], unexpected = [], errors = [];
  let mode = 'completed', filename = 'old.md', version = 3, releasePost;
  let held = false, reads = 0;
  let username = 'rename-fixture-alice';
  const detail = resource => ({ kb_id: resource, title: 'Unchanged title', type: 'concept', project: 'fixture',
    can_edit: true, filename: resource === id ? filename : 'other.md', version: { number: version },
    path: `/fixture/${filename}`, kind: 'markdown', text: '# Unchanged title\nOriginal body',
    files: [{ address: resource, relative_path: resource === id ? filename : 'other.md', primary: true, kind: 'markdown' },
      { address: `${resource}/file/attachment.md`, relative_path: 'attachment.md', kind: 'markdown' }],
  });
  try {
    const page = await browser.newPage({ viewport: { width: 1400, height: 1000 } });
    page.on('pageerror', error => { errors.push(error.message); console.error('Browser error:', error.message); });
    page.setDefaultTimeout(10000);
    await page.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (url.origin !== 'http://knowledge.test') { unexpected.push(url.href); return route.abort(); }
      if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: html });
      if (url.pathname === '/api/me') return route.fulfill({ json: { username } });
      if (url.pathname === '/knowledge/api/detail') {
        reads++; return route.fulfill({ json: detail(url.searchParams.get('kb_id')) });
      }
      if (url.pathname !== '/api/instructions/execute') { unexpected.push(url.href); return route.abort(); }
      const body = route.request().postDataJSON();
      posts.push(body);
      assert.equal(body.instruction, 'UpdateKnowledgeFileName');
      assert.equal(body.arguments.resource_id, id);
      const args = body.arguments;
      assert.equal(Number.isSafeInteger(args.expected_version), true);
      assert.ok(args.operation_id);
      if (mode === 'held') { held = true; await new Promise(resolve => { releasePost = resolve; }); }
      if (mode === '400') return route.fulfill({ status: 400, json: {
        status: 'failed', error: { code: 'instruction.invalid_arguments', message: 'Target file exists' },
      } });
      if (mode === '409') return route.fulfill({ status: 409, json: {
        status: 'failed', error: { code: 'knowledge.version_conflict', message: 'Version conflict' },
      } });
      if (mode === 'network') return route.abort('failed');
      if (!['partial', 'aborted'].includes(mode)) { if (filename !== args.filename) version++; filename = args.filename; }
      return route.fulfill({ json: { status: 'committed', result: {
        resource_id: id, address: id, filename, path: `/fixture/${filename}`, version: { number: version },
        operation_id: args.operation_id, status: ['partial', 'aborted'].includes(mode) ? 'partial' : 'completed',
        ...(mode === 'aborted' ? { retryable: false } : {}),
        ...(mode === 'partial' ? { error: 'Index update pending' } : {}),
      } } });
    });
    await page.goto('http://knowledge.test/');
    const mount = async () => {
    for (const name of ['portal_design_system.css', 'portal_components.css', 'knowledge_board.css']) {
      await page.addStyleTag({ content: source(name) });
    }
    await page.evaluate(() => {
      window.PortalCompatibility = { identifier: x => `fixture-${x}` };
      window.PortalState = { storage: {
        readText: (key, fallback) => sessionStorage.getItem(key) || fallback,
        writeText: (key, value) => sessionStorage.setItem(key, value),
        remove: key => sessionStorage.removeItem(key),
      } };
      window.PortalI18n = { t: x => x, message: x => x, requestLanguage: () => 'en',
        html: (parts, ...values) => String.raw({ raw: parts }, ...values),
        text: (parts, ...values) => String.raw({ raw: parts }, ...values) };
      window.el = Object.fromEntries(['modal', 'mFavorite', 'mResourceIdentity', 'mDownload', 'mEdit', 'mDelete',
        'mSave', 'mCancelEdit', 'mClose', 'mText', 'mSub', 'mFiles'].map(id => [id, document.getElementById(id)]));
      window.state = { detailFileId: '', detailSelectedFileId: '', detailSelectedFileKind: 'markdown',
        detailLinkedFiles: new Map(), detailExpandedFolders: new Set(), deletedKnowledgeIds: new Set(),
        detailLinkDiscoverySequence: 0, detailChat: { draft: 'private chat draft', messages: ['keep'] } };
      window.detailDocumentSession = window.detailPreviewRoot = null;
      window.changes = []; window.indexLoads = 0; window.toasts = [];
      window.detailResourceGuest = { embedded: () => false, changed: event => changes.push(event) };
      window.detailGraphEnabled = () => false;
      window.detailChatPayload = () => null;
      window.loadIndex = async () => { indexLoads++; };
      window.alertError = error => { throw error; };
      window.PortalPreview = { configureDownloadLink() {}, dispose() {},
        render: ({root, document}) => { root.textContent = document.content; return { promise: Promise.resolve() }; } };
      window.PortalResourceHeader = { mount: (root, item) => { root.textContent = item.title; } };
      el.modal.hidden = false; el.modal.style.display = 'flex';
      window.resetDetailChat = () => { throw Error('Rename must not reset private chat'); };
    });
    for (const name of scripts) await page.addScriptTag({ content: source(name) });
    await page.evaluate(() => {
      // Identity can announce itself before knowledge_board.js initializes state.
      window.dispatchEvent(new CustomEvent('portal:identity-changed', { detail: { identity: { username: 'early' } } }));
      window.escapeHtml = PortalUIPrimitives.escapeHtml;
      window.knowledgeApi = PortalAPI;
      const toast = PortalUI.toast;
      PortalUI.toast = (message, tone, options) => { toasts.push({ message, tone }); return toast(message, tone, options); };
      initDetailDocumentSession();
      el.mEdit.addEventListener('click', () => setDetailEditing(true));
      el.mCancelEdit.addEventListener('click', () => setDetailEditing(false));
      window.selectFixture = async resource => {
        state.detailFileId = state.detailSelectedFileId = resource;
        state.detailLinkDiscoverySequence++;
        state.selected = { kb_id: resource, type: 'concept' };
        await openDetailFile({ address: resource, kind: 'markdown' }, { skipDiscard: true });
      };
    });
    await page.evaluate(id => selectFixture(id), id);
    await page.waitForFunction(() => !!detailRenameActor);
    };
    await mount();
    const settle = () => page.waitForFunction(() => !detailRenaming);
    const rename = async name => {
      await page.locator('#mRename').click();
      await page.locator('#portalPromptInput').fill(name);
      await page.locator('[data-portal-prompt-accept]').click();
    };
    const report = scope => console.log(JSON.stringify({ status: 'passed', scope, transport: 'fixture-only', workers: 1,
      browser: browser.version(), playwright: require(path.join(root, 'management_portal/test_framework/tau_runtime/node_modules/playwright/package.json')).version,
      node: process.version, requests: posts.length, detailReads: reads,
      pins: Object.fromEntries([...scripts.map(x => `management_portal/static/${x}`), htmlPath].map(file =>
        [file, crypto.createHash('sha256').update(fs.readFileSync(path.join(root, file))).digest('hex')])) }, null, 2));
    if (process.argv.includes('--recovery-only')) {
      const key = `portal:knowledge.file.rename:v1:${JSON.stringify([username, id])}`;
      const persisted = () => page.evaluate(key => sessionStorage.getItem(key), key);
      const reload = async () => { await page.reload(); await mount(); };
      mode = 'partial';
      await rename('recovered.md'); await settle();
      const original = posts.at(-1).arguments;
      const { resource_id, ...operation } = original;
      assert.equal(resource_id, id);
      assert.deepEqual(JSON.parse(await persisted()), operation);
      await reload();
      assert.match(await page.locator('#mRename').textContent(), /继续/);
      assert.equal(posts.length, 1, 'Reload never automatically replays a mutation');
      mode = 'network';
      await page.locator('#mRename').click(); await settle();
      assert.deepEqual(posts.at(-1).arguments, original);
      assert.deepEqual(JSON.parse(await persisted()), operation);
      username = 'rename-fixture-bob';
      await reload();
      assert.equal(await page.locator('#mRename').textContent(), '重命名主文件');
      assert.equal(posts.length, 2, 'Another account cannot replay the pending operation');
      username = 'rename-fixture-alice';
      await reload();
      mode = 'completed';
      await page.locator('#mRename').click(); await settle();
      assert.deepEqual(posts.at(-1).arguments, original);
      assert.equal(await persisted(), null);
      await reload();
      assert.equal(await page.locator('#mRename').textContent(), '重命名主文件');

      mode = 'aborted';
      await rename('source-changed.md'); await settle();
      assert.equal(await persisted(), null, 'retryable=false clears aborted operation');
      assert.match(await page.evaluate(() => toasts.at(-1).message), /请刷新详情/);
      assert.equal(await page.locator('#mRename').textContent(), '重命名主文件');
      await reload();
      mode = '409';
      await rename('conflict-after-refresh.md'); await settle();
      assert.equal(await persisted(), null, 'Definitive rejection clears persisted operation');
      await page.evaluate(key => sessionStorage.setItem(key, JSON.stringify({
        filename: 'bad.index.md', expected_version: '3', operation_id: 'bad', extra: 'invalid',
      })), key);
      await reload();
      assert.equal(await page.locator('#mRename').textContent(), '重命名主文件');
      assert.equal(posts.length, 5, 'Malformed persisted data is never executed');
      // Storage denial must retain same-page continuation and warn before reload.
      await page.evaluate(key => {
        sessionStorage.removeItem(key);
        for (const method of ['getItem', 'setItem', 'removeItem']) {
          const original = Storage.prototype[method];
          Storage.prototype[method] = function (key, ...args) {
            if (key.startsWith('portal:knowledge.file.rename:')) throw new DOMException('Denied', 'SecurityError');
            return original.call(this, key, ...args);
          };
        }
      }, key);
      mode = 'partial';
      await rename('memory-only.md'); await settle();
      assert.equal(await page.evaluate(() => toasts.some(item => /请勿刷新或关闭/.test(item.message))), true);
      const memoryOnly = posts.at(-1).arguments;
      mode = 'completed';
      await page.locator('#mRename').click(); await settle();
      assert.deepEqual(posts.at(-1).arguments, memoryOnly);
      assert.deepEqual(unexpected, []); assert.deepEqual(errors, []);
      report('reload-recovery');
      return;
    }
    for (const name of ['../bad.md', '.hidden.md', 'foo.index.md', 'foo.InDeX.MD', 'bad.txt']) {
      await rename(name); await settle(); assert.equal(posts.length, 0);
      assert.match(await page.evaluate(() => toasts.at(-1).message), /\.index\.md/);
    }
    if (process.argv.includes('--validation-only')) {
      await rename('index.md'); await settle();
      assert.equal(posts.length, 1, 'index.md is a valid main filename');
      assert.equal(posts[0].arguments.filename, 'index.md');
      assert.equal(await page.locator('.knowledgeFileLeaf.active .knowledgeFileName').textContent(), 'index.md');
      assert.deepEqual(unexpected, []); assert.deepEqual(errors, []);
      report('filename-validation');
      return;
    }
    assert.equal(await page.locator('#mRename').isVisible(), true);
    // Gate from authoritative payload; attachments and specialized resource types stay hidden.
    for (const type of ['skill', 'article', 'experiment']) {
      await page.evaluate(type => { state.selected.type = type; syncDetailButtons(); }, type);
      assert.equal(await page.locator('#mRename').isVisible(), false);
    }
    await page.evaluate(() => {
      state.selected.type = 'concept'; detailDocumentSession.snapshot().payload.can_edit = false; syncDetailButtons();
    });
    assert.equal(await page.locator('#mRename').isVisible(), false);
    await page.evaluate(() => { detailDocumentSession.snapshot().payload.can_edit = true;
      state.detailSelectedFileId += '/file/attachment.md'; syncDetailButtons(); });
    assert.equal(await page.locator('#mRename').isVisible(), false);
    await page.evaluate(() => { state.detailSelectedFileId = state.detailFileId; syncDetailButtons(); });
    // An actual edited textarea is neither discarded nor submitted to the rename endpoint.
    await page.locator('#mEdit').click();
    await page.locator('#detailEditor').fill('Unsaved body');
    assert.equal(await page.locator('#mRename').isDisabled(), true);
    await page.evaluate(() => renameDetailFile());
    assert.equal(await page.locator('#detailEditor').inputValue(), 'Unsaved body');
    assert.equal(posts.length, 0);
    await page.locator('#mCancelEdit').click();
    await page.locator('[data-portal-confirm-accept]').click();
    await page.waitForFunction(() => !state.detailEditing);
    mode = '409';
    const beforeFailure = await page.evaluate(() => ({ draft: detailDocumentSession.snapshot().draft, text: state.detailText }));
    await rename('conflict.md'); await settle();
    assert.match(await page.evaluate(() => toasts.at(-1).message), /Version conflict/);
    assert.equal(await page.evaluate(() => state.selected.filename), 'old.md');
    assert.deepEqual(await page.evaluate(() => ({ draft: detailDocumentSession.snapshot().draft, text: state.detailText })), beforeFailure);
    mode = '400';
    await rename('exists.md'); await settle();
    assert.match(await page.evaluate(() => toasts.at(-1).message), /Target file exists/);
    assert.equal(await page.locator('#mRename').textContent(), '重命名主文件', 'A rejected filename can be corrected');
    mode = 'partial';
    const beforeReads = reads;
    await rename('renamed.md'); await settle();
    assert.match(await page.locator('#mRename').textContent(), /继续/);
    assert.match(await page.evaluate(() => toasts.at(-1).message), /尚未完成/);
    assert.equal(reads, beforeReads, 'Partial must not refresh as success');
    mode = 'completed';
    await page.locator('#mRename').click(); await settle();
    assert.deepEqual(posts.at(-1).arguments, posts.at(-2).arguments, 'Continuation keeps original operation arguments');
    assert.notEqual(posts.at(-1).idempotency_key, posts.at(-2).idempotency_key, 'Continuation can advance a partial receipt');
    assert.equal(await page.locator('.knowledgeFileLeaf.active .knowledgeFileName').textContent(), 'renamed.md');
    assert.equal(await page.evaluate(() => state.selected.version.number), 4);
    assert.equal(await page.evaluate(() => state.selected.title), 'Unchanged title');
    assert.equal(await page.evaluate(() => state.detailText), '# Unchanged title\nOriginal body');
    assert.deepEqual(await page.evaluate(() => state.detailChat), { draft: 'private chat draft', messages: ['keep'] });
    assert.equal(await page.evaluate(() => indexLoads), 1);
    await rename('renamed.md'); await settle();
    assert.equal(version, 4, 'Same basename is a no-op');
    mode = 'network';
    await rename('after-network.md'); await settle();
    assert.equal(await page.evaluate(() => state.selected.filename), 'renamed.md');
    mode = 'completed';
    await page.locator('#mRename').click(); await settle();
    assert.deepEqual(posts.at(-1).arguments, posts.at(-2).arguments);
    // A response to a previous selection may not reopen it or change the current detail.
    mode = 'held';
    await rename('late.md');
    while (!held) await new Promise(resolve => setTimeout(resolve, 10));
    await page.evaluate(other => selectFixture(other), other);
    releasePost(); await settle();
    assert.equal(await page.evaluate(() => state.detailFileId), other);
    assert.equal(await page.locator('.knowledgeFileLeaf.active .knowledgeFileName').textContent(), 'other.md');
    assert.equal(await page.evaluate(() => state.detailChat.draft), 'private chat draft');
    assert.deepEqual(unexpected, []); assert.deepEqual(errors, []);
    report('all');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
