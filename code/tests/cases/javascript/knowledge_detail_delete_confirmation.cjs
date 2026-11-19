const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const repo = path.resolve(__dirname, '../../..');
const staticRoot = path.join(repo, 'management_portal/static');
const detailSource = fs.readFileSync(path.join(staticRoot, 'knowledge_board_detail.js'), 'utf8');
const original = 'thinking_0123456789ab';

function harness() {
  const deleted = [], confirmations = [];
  let confirm;
  const item = {kb_id: original, title: 'Original', type: 'thinking'};
  const snapshot = {requestId: 1, payload: item};
  const state = {detailFileId: original, detailSelectedFileId: original,
    selected: item, raw: {items: [item]}, deletedKnowledgeIds: new Set(), indexPaging: {requestSeq: 0},
    exactQuery: {seq: 0}, knowledgeMap: {}};
  const context = vm.createContext({
    state, detailDocumentSession: {snapshot: () => snapshot},
    window: {PortalI18n: {t: s => s}, PortalUI: {confirmAction: async (_action, title) => {
      confirmations.push(title);
      return new Promise(resolve => { confirm = resolve; });
    }}},
    knowledgeApi: {request: async (_url, options) => {
      const {kb_id} = JSON.parse(options.body);
      deleted.push(kb_id);
      return {deleted: true, kb_id};
    }},
    detailResourceGuest: null, loadIndex: async () => {},
    closeModal: () => { state.detailFileId = ''; }, renderAll() {},
    alertError: error => { throw error; },
  });
  vm.runInContext(detailSource, context);
  context.syncDetailButtons = () => {};
  context.closeModal = () => { state.detailFileId = ''; };
  return {context, state, snapshot, deleted, confirmations, confirm: value => confirm(value)};
}

test('a delete confirmation cannot follow navigation to another resource or file', async () => {
  for (const change of [h => { h.state.detailFileId = h.state.detailSelectedFileId = 'thinking_fedcba987654'; },
    h => { h.state.detailSelectedFileId = original + '/notes.md'; },
    h => { h.snapshot.requestId++; }]) {
    const h = harness();
    const pending = h.context.deleteDetail();
    assert.equal(h.confirmations[0], 'Original');
    change(h);
    h.confirm(true);
    await pending;
    assert.deepEqual(h.deleted, []);
    assert.equal(h.state.deletedKnowledgeIds.size, 0);
  }
});

test('cancelled confirmation does not mutate; a fresh confirmed retry deletes the original once', async () => {
  const h = harness();
  const cancelled = h.context.deleteDetail();
  h.confirm(false);
  await cancelled;
  assert.deepEqual(h.deleted, []);
  const retry = h.context.deleteDetail();
  h.confirm(true);
  await retry;
  assert.deepEqual(h.deleted, [original]);
  assert(h.state.deletedKnowledgeIds.has(original));
  assert.equal(h.state.detailFileId, '');
});

test('Knowledge header delete uses the shared browser confirmation and refreshes only after success', async () => {
  const {chromium} = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({viewport: {width: 390, height: 844}});
    page.setDefaultTimeout(5000);
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const board = fs.readFileSync(path.join(repo, 'knowledge_board_lan/knowledge_board.html'), 'utf8');
    const actions = board.match(/<div class="knowledgeDetailActions">[\s\S]*?<\/div>/)[0];
    await page.setContent('<main id="detail">' + actions + '</main><p id="error"></p>');
    for (const file of ['portal_themes.css', 'portal_design_system.css', 'portal_components.css', 'knowledge_board.css']) {
      await page.addStyleTag({path: path.join(staticRoot, file)});
    }
    await page.evaluate(id => {
      const text = (strings, ...values) => strings.reduce((out, str, index) => out + str + (values[index] ?? ''), '');
      window.PortalI18n = {t: value => value, text, html: text};
      window.calls = [];
      window.state = {
        detailFileId: id, detailSelectedFileId: id, selected: {kb_id: id, title: 'Original', type: 'thinking'},
        raw: {items: [{kb_id: id}]}, indexPaging: {requestSeq: 0, total: 1}, exactQuery: {seq: 0},
        deletedKnowledgeIds: new Set(), knowledgeMap: {open: true},
      };
      const snapshot = {requestId: 1, payload: {}};
      window.detailDocumentSession = {snapshot: () => snapshot};
      window.detailResourceGuest = null;
      window.el = Object.fromEntries([...document.querySelectorAll('[id]')].map(node => [node.id, node]));
      window.knowledgeApi = {request: async (url, options) => {
        calls.push([url, options.method, JSON.parse(options.body)]);
        if (window.rejectDelete) throw Error('HTTP 403');
        return {deleted: true, kb_id: id, resource_id: id};
      }};
      window.renderAll = () => calls.push(['render']);
      window.loadIndex = async ({refresh}) => calls.push(['index', refresh]);
      window.loadKnowledgeGraph = async ({refresh}) => calls.push(['graph', refresh]);
      window.alertError = error => { el.error.textContent = error.message; };
    }, original);
    for (const file of ['portal_state.js', 'portal_ui_primitives.js', 'portal_overlay.js', 'portal_ui.js']) {
      await page.addScriptTag({path: path.join(staticRoot, file)});
    }
    await page.evaluate(() => {
      window.PortalPreview = {configureDownloadLink() {}};
    });
    await page.addScriptTag({content: detailSource});
    const binding = fs.readFileSync(path.join(staticRoot, 'knowledge_board.js'), 'utf8')
      .match(/el\.mDelete\.addEventListener\("click", deleteDetail\);/)[0];
    await page.addScriptTag({content: `
      window.detailFiles = () => [];
      window.detailSelectedFileEditable = () => true;
      window.closeModal = () => {
        state.detailFileId = '';
        el.detail.hidden = true;
      };
      ${binding}
      syncDetailButtons();
    `});

    const remove = page.getByRole('button', {name: '删除知识条目'});
    const confirm = page.locator('[data-portal-confirm-accept]');
    assert.equal(await remove.isVisible(), true);
    assert.equal(await remove.locator('svg').count(), 1);
    const box = await remove.boundingBox();
    assert.ok(box.width > 0 && box.x >= 0 && box.x + box.width <= 390);
    const screenshots = process.env.KNOWLEDGE_DELETE_SCREENSHOT_DIR;
    if (screenshots) await page.screenshot({path: path.join(screenshots, 'toolbar-390.png')});
    for (const type of ['article', 'experiment']) {
      await page.evaluate(type => { state.selected.type = type; syncDetailButtons(); }, type);
      assert.equal(await remove.count(), 0);
    }
    await page.evaluate(() => { state.selected.type = 'thinking'; syncDetailButtons(); });
    await remove.click();
    await page.waitForFunction(() => el.error.textContent || document.querySelector('[data-portal-confirmation].open'));
    assert.equal(await page.locator('#error').textContent(), '', errors.join('\n'));
    assert.equal(await confirm.isVisible(), true);
    assert.match(await page.locator('#portalConfirmMessage').textContent(), /Original/);
    if (screenshots) await page.screenshot({path: path.join(screenshots, 'confirmation-390.png')});
    await page.locator('.portalDetailFooter [data-portal-confirm-cancel]').click();
    await page.waitForFunction(() => !el.mDelete.disabled);
    assert.deepEqual(await page.evaluate(() => calls), []);

    await page.evaluate(() => { window.rejectDelete = true; });
    await remove.click();
    await confirm.click();
    await page.waitForFunction(() => el.error.textContent === 'HTTP 403' && !el.mDelete.disabled);
    assert.equal(await page.evaluate(() => state.raw.items.length), 1);
    await page.evaluate(() => { window.rejectDelete = false; });
    await remove.click();
    await confirm.click();
    await page.waitForFunction(() => el.detail.hidden && calls.some(call => call[0] === 'graph'));
    const post = ['/knowledge/api/detail/delete', 'POST', {kb_id: original}];
    assert.deepEqual(await page.evaluate(() => calls), [post, post, ['render'], ['index', true], ['graph', true]]);
    assert.deepEqual(await page.evaluate(() => state.raw.items), []);
    assert.deepEqual(errors, []);
  } finally {
    await browser.close();
  }
});
