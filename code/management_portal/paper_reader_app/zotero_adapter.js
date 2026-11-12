import {openHttpDocument} from './zotero_prototype/http_transport.js';
import {mountReader} from './zotero_prototype/host.js';
export const ZOTERO_URL = '/paper/reader-assets/reader.html';

export function connectZotero({frame, paper, principal, status, ready, translateSelection, askSelection, dismissTranslation}) {
  let disposed = false, host, transport, failed = false, tick;
  const popupObservers = new Set();
  const t=value=>window.PortalI18n?.t(value)||value;
  let localeObserver;
  let refreshLocale=()=>{};
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
    // Localize only the upstream empty annotation placeholder, never annotation
    // bodies or PDF text. Leave vendored assets and the save barrier untouched.
    const localizeEmptyAnnotations=()=>{
      for(const [selector,label] of [['.portalTranslateSelection','翻译选区'],['.portalAISelection','AI 解释 / 提问']]){
        for(const node of frame.contentDocument.querySelectorAll(selector))if(node.textContent!==t(label))node.textContent=t(label);
      }
      for(const node of frame.contentDocument.querySelectorAll('#annotations > div:not(.annotation)')){
        if(node.textContent==='Create an annotation to see it in the sidebar'||node.dataset.portalEmptyAnnotations){
          node.dataset.portalEmptyAnnotations='true';
          const label=t('创建批注后，可在侧栏查看');
          if(node.textContent!==label)node.textContent=label;
        }
      }
    };
    localeObserver=new MutationObserver(localizeEmptyAnnotations);
    refreshLocale=localizeEmptyAnnotations;
    window.addEventListener('portal:language-changed',refreshLocale);
    localeObserver.observe(frame.contentDocument.querySelector('#reader-ui'),{childList:true,subtree:true});
    localizeEmptyAnnotations();
    const style = frame.contentDocument.createElement('style');
    style.textContent = 'body.portal-embedded .portalTranslationPopup, body.portal-embedded .portalTranslateSelection, body.portal-embedded .portalAISelection, body.portal-embedded #reader-ui .toolbar {display:none!important} body.portal-embedded #split-view, body.portal-embedded .split-view {top:0!important}';
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
    button.textContent = t('翻译选区');
    const wrapper = detail.doc.createElement('div');
    const popup = detail.doc.createElement('div'); popup.className = 'portalTranslationPopup';
    popup.style.cssText = 'box-sizing:border-box; width:100%; max-width:100%; min-width:0; max-height:42vh; overflow:auto; text-align:start; white-space:normal;';
    const style = detail.doc.createElement('style');
    style.textContent = '.portalTranslationPopup,.portalTranslationPopup * {box-sizing:border-box;min-width:0;max-width:100%;overflow-wrap:anywhere;word-break:normal} .portalTranslationPopup .paperTranslation {font:13px/1.5 system-ui;padding:6px;white-space:normal} .portalTranslationPopup button {font:inherit;white-space:normal} .portalTranslationPopup .paperTranslationActions {display:flex;gap:4px;margin-top:6px} .portalTranslationPopup .paperTranslationActions button {flex:1;border:1px solid #c7c7c7;border-radius:5px;background:#fff;color:#222;box-shadow:0 1px 2px #0002;min-height:26px;padding:3px 6px;cursor:pointer} .portalTranslationPopup .paperTranslationActions button:hover {background:#eee} .portalTranslationPopup .paperTranslationActions button:focus-visible {outline:2px solid #4688c7} .portalTranslationPopup .paperTranslationText {white-space:pre-wrap;max-height:24vh;overflow:auto} .portalTranslationPopup small {display:block;font-size:11px}';
    wrapper.append(button, style, popup);
    // Keep native selection popup open when interacting with its appended controls.
    wrapper.addEventListener('mousedown', event => event.stopPropagation());
    button.onclick = () => {
      if (disposed || !fullscreen) return;
      const dismissPopup=()=>{
        const doc=detail.doc;
        doc.getSelection()?.removeAllRanges();
        for(const inner of doc.querySelectorAll('iframe')){
          const pdfDoc=inner.contentDocument;
          pdfDoc?.getSelection()?.removeAllRanges();
          pdfDoc?.body.dispatchEvent(new pdfDoc.defaultView.KeyboardEvent('keydown',{key:'Escape',code:'Escape',bubbles:true}));
        }
        doc.body.dispatchEvent(new doc.defaultView.KeyboardEvent('keydown',{key:'Escape',code:'Escape',bubbles:true}));
      };
      translateSelection({text, document: {...transport.document}, popup, dismissPopup});
      const observer = new MutationObserver(() => {
        if (!popup.isConnected) {observer.disconnect();popupObservers.delete(observer);dismissTranslation?.(popup);}
      });
      observer.observe(detail.doc.body, {childList:true,subtree:true});popupObservers.add(observer);
    };
    detail.append(wrapper);
    if (askSelection) {
      const ai = detail.doc.createElement('button');
      ai.className = 'toolbar-button wide-button portalAISelection';
      ai.textContent = t('AI 解释 / 提问');
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
  dispose() {disposed = true; localeObserver?.disconnect(); window.removeEventListener('portal:language-changed',refreshLocale); for (const observer of popupObservers) observer.disconnect(); popupObservers.clear(); cancelAnimationFrame(tick); frame.contentWindow?.removeEventListener('customEvent', selectionPopup);},
  get failed() {return failed;}};
}
