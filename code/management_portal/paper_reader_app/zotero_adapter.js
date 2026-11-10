import {openHttpDocument} from './zotero_prototype/http_transport.js';
import {mountReader} from './zotero_prototype/host.js';
export const ZOTERO_URL = '/paper/reader-assets/reader.html';

export function connectZotero({frame, paper, principal, status, ready, translateSelection, askSelection}) {
  let disposed = false, host, transport, failed = false, tick;
  let fullscreen = true, fullscreenSidebar = true;
  const applyLayout = () => {
    if (!host) return;
    frame.contentDocument.body.classList.toggle('portal-embedded', !fullscreen);
    host.reader.toggleSidebar(fullscreen ? fullscreenSidebar : false);
  };
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
    const style = frame.contentDocument.createElement('style');
    style.textContent = 'body.portal-embedded .portalTranslateSelection, body.portal-embedded .portalAISelection, body.portal-embedded #reader-ui .toolbar {display:none!important} body.portal-embedded #split-view, body.portal-embedded .split-view {top:0!important}';
    frame.contentDocument.head.append(style);
    applyLayout();
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
    button.onclick = () => {if (!disposed && fullscreen) translateSelection({text, document: {...transport.document}});};
    detail.append(button);
    if (askSelection) {
      const ai = detail.doc.createElement('button');
      ai.className = 'toolbar-button wide-button portalAISelection';
      ai.textContent = 'AI 解释 / 提问';
      ai.onclick = () => {if (!disposed && fullscreen) askSelection({text, document:{...transport.document}, position:structuredClone(detail.params.annotation.position)});};
      detail.append(ai);
    }
  }
  return {async aiDocument() {await start; if (disposed) throw Error('Reader 已关闭'); return {...transport.document};},
    async locateSelection(selection) {await start; if (!disposed) await host.reader.navigate({position:selection.position});},
    async saveAnswer(selection, comment, identity) {
      await start;
      if (disposed || !selection?.position?.rects?.length) throw Error('请先选择可定位的原文');
      const [x,y] = selection.position.rects[0];
      const note = {id:identity||crypto.randomUUID().replaceAll('-','').slice(0,8).toUpperCase(),type:'note',
        color:'#ffd400',sortIndex:String(selection.position.pageIndex).padStart(5,'0')+'|000000',
        pageLabel:String(selection.position.pageIndex+1),text:'',comment,tags:[],
        position:{pageIndex:selection.position.pageIndex,rects:[[x,y,x+22,y+22]]}};
      await host.reader.flushAnnotations();
      await transport.persistence.save([note]);
      if (!disposed) host.reader.setAnnotations([note]);
      return note;
    }, needsLeaveConfirmation() {
    if (!host) return false;
    const state = host.getSaveState();
    const persistence = transport.persistence.snapshot();
    return state.pending || state.saving || state.failed || !!persistence.pending || persistence.conflict;
  }, setFullscreen(value) {
    if (fullscreen && !value && host) fullscreenSidebar = frame.contentDocument.body.classList.contains('sidebar-open');
    fullscreen = !!value; applyLayout();
  }, async flush() {
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
