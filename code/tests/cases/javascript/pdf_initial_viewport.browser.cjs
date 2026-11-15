'use strict';
const assert = require('node:assert/strict');
const path = require('node:path');
const STATIC = path.resolve(__dirname, '../../../management_portal/static');
const { chromium } = require(path.join(STATIC, '../test_framework/tau_runtime/node_modules/playwright'));

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    await page.setContent('<div id="viewport"><div id="pdf"></div></div>');
    await page.evaluate(() => {
      const template = (parts, ...args) => parts.reduce((s, part, i) => s + part + (args[i] ?? ''), '');
      window.PortalI18n = { t: text => text, html: template, text: template, markup: text => text };
      window.PortalUIPrimitives = { escapeHtml: String };
      HTMLCanvasElement.prototype.getContext = () => ({ setTransform() {} });
      window.observers = [];
      window.IntersectionObserver = class {
        constructor(callback, options) { this.callback = callback; this.options = options; window.observers.push(this); }
        observe() {}
        unobserve() {}
        disconnect() { this.disconnected = true; }
      };
      window.rendered = [];
      const pdf = { numPages: 8, getPage: async number => ({
        getViewport: ({ scale }) => ({ width: 500 * scale, height: 700 * scale }),
        render: () => { window.rendered.push(number); return { promise: Promise.resolve(), cancel() {} }; },
        getTextContent: async () => ({ items: [], styles: {} }),
      }) };
      window.pdfjsLib = { getDocument: () => ({ promise: Promise.resolve(pdf) }) };
    });
    await page.addScriptTag({ path: path.join(STATIC, 'portal_pdf_preview.js') });
    const result = await page.evaluate(async () => {
      const root = document.getElementById('pdf');
      const viewport = document.getElementById('viewport');
      let controller = window.PortalPdfPreview.render({ root, url: '/default.pdf' });
      await controller.promise;
      const defaultOrder = [...window.rendered];
      const status = root.dataset.pdfStatus;
      controller.cancel();
      window.rendered.length = 0;
      let geometry;
      controller = window.PortalPdfPreview.render({
        root, viewport, url: '/restored.pdf',
        initialPages: ({ root, pageCount }) => {
          geometry = { pageCount, placeholders: root.querySelectorAll('[data-pdf-page]').length };
          return [5, 6];
        },
      });
      await controller.promise;
      const initialOrder = [...window.rendered];
      const observer = window.observers.at(-1);
      const observesViewport = observer.options.root === viewport;
      observer.callback([{ target: root.querySelector('[data-pdf-page="7"]'), isIntersecting: true }]);
      await controller.ensurePage(7);
      controller.cancel();
      return { status, defaultOrder, geometry, initialOrder, observesViewport,
        finalOrder: [...window.rendered], disconnected: observer.disconnected };
    });
    assert.equal(result.status, 'done');
    assert.deepEqual(result.defaultOrder, [1], 'ordinary previews still start with page one');
    assert.deepEqual(result.geometry, { pageCount: 8, placeholders: 8 }, 'geometry precedes viewport selection');
    assert.deepEqual(result.initialOrder, [5, 6], 'only requested pages are painted before readiness');
    assert.equal(result.observesViewport, true);
    assert.deepEqual(result.finalOrder, [5, 6, 7], 'other pages remain lazy');
    assert.equal(result.disconnected, true);
    console.log('PASS: shared PDF default, viewport priority, lazy continuation and cancellation');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
