import React, {useEffect, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {connectZotero, ZOTERO_URL} from './zotero_adapter.js';
import {TranslationMode, TranslationPanel} from './translation_panel.jsx';
import {translateSelection} from './translation_client.js';
import {AIPanel} from './ai_panel.jsx';

function Reader({paper, principal, anchor, close, control, readingReady, frameReady, isCurrent, isFullscreen}) {
  const frame = useRef(null), bridge = useRef(null);
  const [status, setStatus] = useState('正在打开论文…');
  const [failed, setFailed] = useState(false);
  const [selection, setSelection] = useState(null);
  const [mode, setMode] = useState('auto');
  const [discussion,setDiscussion] = useState(null);
  const openDiscussion=async selection=>{
    if(!isCurrent()||!isFullscreen())return;
    if(discussion&&!selection)return;
    const document=selection?.document||await bridge.current.aiDocument();
    if(isCurrent())setDiscussion({...selection,document,key:`paper:${paper.id}`});
  };
  useEffect(() => {
    frameReady(frame.current.contentWindow);
    const connection = connectZotero({frame: frame.current, paper, principal, ready: readingReady,
      translateSelection: value => {if (isCurrent() && isFullscreen()) setSelection(value);},
      askSelection: value=>void openDiscussion(value),
      status: (message, error = false) => { setStatus(message); setFailed(error); }});
    bridge.current = connection;
    control(connection);
    frame.current.src = ZOTERO_URL;
    return () => { connection.dispose(); };
  }, [paper.id]);
  return <div className="paperReadingApp">
    <header className="paperReadingToolbar">
      <strong title={paper.title}>{paper.title}</strong>
      <TranslationMode mode={mode} onChange={setMode} />
      <button aria-expanded={!!discussion} onClick={()=>discussion?setDiscussion(null):openDiscussion()}>{discussion?'收起 AI':'打开 AI'}</button>
      <a href={paper.pdf_url} target="_blank" rel="noopener noreferrer">原始 PDF</a>
      <button onClick={() => close()}><span className="paperLayoutEnter">全屏阅读</span><span className="paperLayoutExit">退出全屏</span></button>
    </header>
    <div className="paperReadingStatus" data-empty={!status} role="status">
      {status} {failed && <button onClick={() => bridge.current?.retry()}>重试保存</button>}
    </div>
    <TranslationPanel mode={mode} selection={selection} isCurrent={isCurrent} request={(input, mode, operationId, signal) => {
      if (!isFullscreen()) throw new Error('请进入全屏后翻译');
      return translateSelection({selection: input, mode, operationId, principal, signal});
    }} />
    <div className="paperReadingContent"><iframe ref={frame} className="paperReadingViewer" title="论文全屏 PDF 阅读器" />
      {discussion&&<AIPanel key={discussion.key} discussion={discussion} principal={principal} bridge={()=>bridge.current} isCurrent={isCurrent} close={()=>setDiscussion(null)} />}
    </div>
  </div>;
}

export function openReader({paper, principal, anchor, onClose, onReady, container, isCurrent = () => true}) {
  const previousFocus = document.activeElement;
  const dialog = document.createElement('dialog');
  dialog.className = 'paperReadingDialog'; dialog.setAttribute('aria-label', '全屏论文阅读');
  document.body.append(dialog);
  const root = createRoot(dialog);
  let closed = false, closing = false, bridge;
  const instanceId = crypto.randomUUID();
  let sourceWindow;
  let fullscreen = !container, layoutTick;
  const layout = () => {
    if (closed) return;
    dialog.classList.toggle('is-embedded', !fullscreen);
    if (!fullscreen) {
      const target = container?.();
      if (target) {
        const rect = target.getBoundingClientRect();
        Object.assign(dialog.style, {left: `${rect.left}px`, top: `${rect.top}px`, width: `${rect.width}px`, height: `${rect.height}px`});
      }
    } else Object.assign(dialog.style, {left: '0', top: '0', width: '100vw', height: '100dvh'});
    layoutTick = requestAnimationFrame(layout);
  };
  const setFullscreen = value => { fullscreen = !!value; bridge?.setFullscreen(fullscreen); dialog.classList.toggle('is-embedded', !fullscreen); dialog.setAttribute('aria-modal', String(fullscreen)); };
  const escape = event => {
    if (container && fullscreen && event.key === 'Escape') {event.preventDefault(); event.stopPropagation(); setFullscreen(false);}
  };
  document.addEventListener('keydown', escape, true);
  // A browser unload cannot await the native save barrier. Keep the document
  // interactive when navigation is cancelled; in-app exits use close() first.
  const beforeUnload = event => {
    if (!closed && bridge?.needsLeaveConfirmation()) {event.preventDefault(); event.returnValue = '';}
  };
  window.addEventListener('beforeunload', beforeUnload);
  const close = async position => {
    if (closed) return true;
    if (closing) return false;
    closing = true;
    dialog.inert = true;
    if (bridge && !await bridge.flush()) { closing = false; dialog.inert = false; setFullscreen(true); return false; }
    closed = true;
    cancelAnimationFrame(layoutTick);
    document.removeEventListener('keydown', escape, true);
    window.removeEventListener('beforeunload', beforeUnload);
    position ||= bridge?.captureAnchor() || anchor;
    root.unmount(); dialog.close(); dialog.remove(); previousFocus?.focus(); onClose?.(position);
    return true;
  };
  const toggle = () => container ? setFullscreen(!fullscreen) : close();
  dialog.addEventListener('cancel', event => { event.preventDefault(); toggle(); });
  if (container) {dialog.show(); layout();} else dialog.showModal();
  root.render(<Reader paper={paper} principal={principal} anchor={anchor} close={toggle} control={value => { bridge = value; bridge.setFullscreen(fullscreen); }}
    isFullscreen={() => fullscreen}
    isCurrent={() => !closed && isCurrent()}
    frameReady={value => { sourceWindow = value; }}
    readingReady={value => { if (!closed && !closing) onReady?.({...value, instanceId}); }} />);
  return {close, setFullscreen, paperId: paper.id, principal, pdfUrl: paper.pdf_url, instanceId, get sourceWindow() { return sourceWindow; }};
}
