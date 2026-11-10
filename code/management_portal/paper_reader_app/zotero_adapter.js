import {openHttpDocument} from './zotero_prototype/http_transport.js';
import {mountReader} from './zotero_prototype/host.js';
export const ZOTERO_URL = '/paper/reader-assets/reader.html';

export function connectZotero({frame, paper, principal, status, ready, translateSelection}) {
  let disposed = false, host, transport, failed = false, tick;
  let resolveLoaded;
  const loaded = new Promise(resolve => { resolveLoaded = resolve; });
  frame.addEventListener('load', resolveLoaded, {once: true});
  const start = (async () => {
    transport = await openHttpDocument({paperId: paper.id, principal});
    await loaded;
    if (disposed) return;
    // Upstream's public TextSelectionPopup extension supplies only the selected
    // annotation text. No PDF text extraction or DOM tree walk is involved.
    frame.contentWindow.addEventListener('customEvent', selectionPopup);
    host = await mountReader(frame, {...transport.options, status: message => {failed = true; status(message, true);}});
    if (disposed) {await host.close(); return;}
    const rendered = () => {
      if (disposed) return;
      const inner = frame.contentDocument.querySelector('iframe');
      const canvas = inner?.contentDocument?.querySelector('.page canvas');
      if (canvas?.width && canvas?.height) {
        status('');
        ready({paperId: paper.id, pdfUrl: new URL(paper.pdf_url, location.href).href,
          fingerprint: transport.document.attachment_version, source: frame.contentWindow});
      } else tick = requestAnimationFrame(rendered);
    };
    tick = requestAnimationFrame(rendered);
  })().catch(error => {failed = true; status(error.message, true); throw error;});
  // A close during load waits for initialization before removing the document.
  start.catch(() => {});
  function selectionPopup(event) {
    const detail = event.detail;
    if (disposed || !translateSelection || detail?.type !== 'renderTextSelectionPopup') return;
    const text = detail.params?.annotation?.text;
    if (typeof text !== 'string' || !text.trim()) return;
    const button = detail.doc.createElement('button');
    button.className = 'toolbar-button wide-button portalTranslateSelection';
    button.textContent = '翻译选区';
    button.onclick = () => {if (!disposed) translateSelection({text, document: {...transport.document}});};
    detail.append(button);
  }
  return {async flush() {
    try {await start; if (!host) return false; await host.close(); failed = false; return true;}
    catch (error) {status(error.message, true); return !host;}
  }, async retry() {
    if (!host) {status('载入失败，请关闭后重新打开', true); return false;}
    try {await host.reader.flushAnnotations(); failed = false; status(''); return true;}
    catch (error) {status(error.message, true); return false;}
  }, captureAnchor: () => undefined,
  dispose() {disposed = true; cancelAnimationFrame(tick); frame.contentWindow?.removeEventListener('customEvent', selectionPopup);},
  get failed() {return failed;}};
}
