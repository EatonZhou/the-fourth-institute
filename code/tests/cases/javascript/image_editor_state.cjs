// Real ImageEditor, Dialog, Overlay and text modules in a DOM. Image decode
// and unavailable Canvas are environment boundaries only.
// Pixel drawing, undo snapshots and native pointer routing stay browser tests.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');

const staticRoot = path.resolve(__dirname, '../../../management_portal/static');
const modules = ['portal_state.js', 'portal_text.js', 'portal_ui_primitives.js', 'portal_overlay.js', 'portal_ui.js', 'portal_image_editor.js'];

async function withEditor(inspect, transform = source => source) {
  const dom = new JSDOM('<!doctype html><html lang="zh-CN"><body><main></main></body></html>', {
    url: 'http://image-editor.invalid', runScripts: 'outside-only', pretendToBeVisual: true,
  });
  const { window } = dom;
  const observers = [];
  const NativeMutationObserver = window.MutationObserver;
  window.MutationObserver = class extends NativeMutationObserver {
    constructor(callback) {
      super(callback);
      observers.push(this);
    }
  };
  window.matchMedia = query => ({
    media: query, matches: false, addEventListener() {}, removeEventListener() {},
  });
  // Neither contract draws or reads pixels. Returning unavailable is the
  // native Canvas boundary; it cannot manufacture annotation state or pixels.
  window.HTMLCanvasElement.prototype.getContext = () => null;
  try {
    for (const filename of modules) {
      const source = fs.readFileSync(path.join(staticRoot, filename), 'utf8');
      window.eval(filename === 'portal_image_editor.js' ? transform(source) : source);
    }
    await inspect(window);
  } finally {
    window.document.getElementById('portalImageEditorLightbox')?._portalImageDialog?.destroy();
    // Drain the real enhancer microtasks before jsdom removes its document.
    // Native browser disposal owns this step outside the controller contract.
    await new Promise(resolve => setImmediate(resolve));
    for (const observer of observers) observer.disconnect();
    await new Promise(resolve => setImmediate(resolve));
    window.close();
  }
}

async function failedLoad(transform) {
  await withEditor(async window => {
    window.PortalImageEditor.open('data:image/png;base64,broken', 'broken.png', { editable: true });
    const overlay = window.document.getElementById('portalImageEditorLightbox');
    const image = overlay.querySelector('[data-lightbox-image]');
    const status = overlay.querySelector('[data-image-load-status]');
    const canvas = overlay.querySelector('[data-lightbox-canvas]');
    assert.equal(status.textContent, '图片加载中…');
    assert.equal(overlay.hidden, false);
    // The environment supplies a decode failure, the installed production
    // handler must produce every asserted state and DOM change.
    image.dispatchEvent(new window.Event('error'));
    assert.equal(status.textContent, '图片加载失败', 'decode failure must render an actionable error');
    assert.equal(status.hidden, false);
    assert.equal(status.classList.contains('is-error'), true);
    assert.equal(overlay.querySelector('[data-image-draw-save]').disabled, true, 'failed image cannot be saved');
    assert.equal(overlay.querySelector('[data-image-draw-mode="pen"]').disabled, true, 'failed image cannot be annotated');
    assert.deepEqual([canvas.width, canvas.height], [0, 0], 'failed image leaves no canvas extent');
    assert.equal(image.hidden, true);
    assert.equal(overlay._portalImageDraw.ready, false);
    assert.equal(overlay._portalImageDraw.loadError, true);
    assert.equal(overlay.querySelector('[data-image-draw-cancel]').disabled, false, 'failure keeps cancellation available');
    assert.equal(window.PortalImageEditor.close(overlay), true);
    assert.equal(overlay.hidden, true);
  }, transform);
}

async function closeConfirmation(transform) {
  await withEditor(async window => {
    window.PortalImageEditor.open('/fixture-image.png', 'diagram.png', { editable: true });
    const overlay = window.document.getElementById('portalImageEditorLightbox');
    // The input is an existing unsaved editing session. Native drawing and
    // actual pixels producing dirty=true remain in the browser close cases.
    overlay._portalImageDraw = { ...overlay._portalImageDraw, ready: true, dirty: true };
    assert.equal(window.PortalImageEditor.close(overlay), false, 'dirty close must defer its result until approval');
    const confirmation = window.document.querySelector('[data-portal-confirmation]');
    assert.ok(confirmation && !confirmation.hidden, 'image close must use the shared dirty confirmation');
    assert.equal(overlay.hidden, false, 'dirty editor stays open until approval');
    confirmation.querySelector('[data-portal-confirm-accept]').click();
    await Promise.resolve();
    await Promise.resolve();
    assert.equal(overlay.hidden, true, 'approved image close must complete');
    assert.equal(confirmation.hidden, true);
  }, transform);
}

const contracts = { failed_load: failedLoad, close_confirmation: closeConfirmation };
module.exports = { contracts };
if (require.main === module) {
  const name = process.argv[2];
  assert.ok(Object.hasOwn(contracts, name), 'choose an explicit image editor contract');
  contracts[name]().then(() => console.log(`contract passed: ${name}`)).catch(error => {
    console.error(error);
    process.exitCode = 1;
  });
}
