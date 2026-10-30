'use strict';
// The chat workspace loads the image viewer on demand through
// PortalUIPrimitives.loadAsset: one tag per file with the page's asset
// version; a failed load says so, removes its tag and the next request tries
// again; once the script runs every waiting preview gets the same viewer.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const { chatWorkspaceScripts } = require('./chat_workspace_modules.cjs');
const { portalWorkbenchScripts } = require('./portal_workbench_scripts.cjs');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const STATIC = path.resolve(__dirname, '../../../management_portal/static');
const dom = new JSDOM('<script src="/static/portal_state.js?portal_v=r42"></script><section id="fixture"></section>', {
  url: 'https://chat.fixture.invalid/lineage', pretendToBeVisual: true, runScripts: 'outside-only',
});
const w = dom.window;
const observers = new Set();
const NativeMutationObserver = w.MutationObserver;
w.MutationObserver = class extends NativeMutationObserver {
  constructor(callback) { super(callback); observers.add(this); }
};
w.CSS ={ escape: value => String(value).replace(/[^a-zA-Z0-9_-]/g, char => `\\${char}`) };
w.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
w.matchMedia = query => Object.assign(new w.EventTarget(), { media: query, matches: false });
w.PortalIdentity = { username: () => 'sample_admin' };
w.eval(input.routeScript);
for (const name of ['portal_state.js', 'portal_page_state.js', 'portal_text.js', 'portal_ui_primitives.js', 'portal_ui.js',
  'portal_overlay.js', 'portal_resource_chips.js', 'portal_editor_open_service.js', 'portal_command_registry.js',
  'portal_workbench_context.js', ...portalWorkbenchScripts(), 'resident_protocol.js', ...chatWorkspaceScripts()]) {
  w.eval(fs.readFileSync(path.join(STATIC, name), 'utf8'));
}
const settle = async () => { for (let turn = 0; turn < 5; turn += 1) await new Promise(resolve => setTimeout(resolve, 0)); };
const toasts = [];
w.PortalUI.toast = (text, tone) => toasts.push([text, tone]);
const tags = file => [...w.document.head.querySelectorAll(`script[src*="${file}"], link[href*="${file}"]`)];

(async () => {
  await settle();
  // Loading the chat workspace already asked for the viewer once.
  assert.deepEqual(tags('portal_image_editor').map(tag => tag.getAttribute('src') || tag.getAttribute('href')),
    ['/static/portal_image_editor.css?portal_v=r42', '/static/portal_image_editor.js?portal_v=r42'],
    'one stylesheet and one script, both with the page\'s asset version');

  const chat = w.PortalChatWorkspace;
  chat.openImagePreview('/files/a.png', 'a.png');
  chat.openImagePreview('/files/b.png', 'b.png');
  assert.equal(tags('portal_image_editor.js').length, 1, 'waiting previews share the pending load');

  tags('portal_image_editor.js')[0].dispatchEvent(new w.Event('error'));
  await settle();
  assert.equal(tags('portal_image_editor.js').length, 0, 'a failed load removes its tag');
  assert.ok(toasts.some(([text, tone]) => text === '图片查看器加载失败，请重试' && tone === 'error'), 'and says so');

  const opened = [];
  chat.openImagePreview('/files/c.png', 'c.png');
  assert.equal(tags('portal_image_editor.js').length, 1, 'the next request tries again');
  w.PortalImageEditor = { open: (src, title) => opened.push([src, title]), bind() {} };
  tags('portal_image_editor.js')[0].dispatchEvent(new w.Event('load'));
  await settle();
  assert.deepEqual(opened, [['/files/c.png', 'c.png']], 'the loaded viewer opens the image');
  chat.openImagePreview('/files/d.png', 'd.png');
  assert.deepEqual(opened.at(-1), ['/files/d.png', 'd.png'], 'later previews use it without loading again');
  assert.equal(tags('portal_image_editor.js').length, 1);
  console.log('PASS the image viewer loads once through loadAsset, reports a failure and retries');
  await new Promise(resolve => setImmediate(resolve));
  for (const observer of observers) observer.disconnect();
  w.close();
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
