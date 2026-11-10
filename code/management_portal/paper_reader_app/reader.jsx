import React, {useEffect, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {connectViewer, VIEWER_URL} from './viewer_adapter.js';
import {observeReadingReady} from './reading_ready.js';

function Reader({paper, anchor, close, control, readingReady, frameReady}) {
  const frame = useRef(null), bridge = useRef(null);
  const [status, setStatus] = useState('正在打开论文…');
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    frameReady(frame.current.contentWindow);
    const stopReadingReady = observeReadingReady(frame.current, paper, readingReady);
    const connection = connectViewer({frame: frame.current, paper, anchor,
      status: (message, error = false) => { setStatus(message); setFailed(error); }});
    bridge.current = connection;
    control(connection);
    frame.current.src = `${VIEWER_URL}?file=${encodeURIComponent(paper.pdf_url)}`;
    return () => { stopReadingReady(); connection.dispose(); };
  }, [paper.id]);
  return <div className="paperReadingApp">
    <header className="paperReadingToolbar">
      <strong>{paper.title}</strong>
      <a href={paper.pdf_url} target="_blank" rel="noopener noreferrer">原始 PDF</a>
      <button onClick={() => close()}>退出全屏</button>
    </header>
    <div className="paperReadingStatus" role="status">
      {status} {failed && <button onClick={() => bridge.current?.retry()}>重试保存</button>}
    </div>
    <iframe ref={frame} className="paperReadingViewer" title="论文全屏 PDF 阅读器" />
  </div>;
}

export function openReader({paper, anchor, onClose, onReady}) {
  const previousFocus = document.activeElement;
  const dialog = document.createElement('dialog');
  dialog.className = 'paperReadingDialog'; dialog.setAttribute('aria-label', '全屏论文阅读');
  document.body.append(dialog);
  const root = createRoot(dialog);
  let closed = false, closing = false, bridge;
  const instanceId = crypto.randomUUID();
  let sourceWindow;
  const close = async position => {
    if (closed || closing) return;
    closing = true;
    dialog.inert = true;
    if (bridge && !await bridge.flush()) { closing = false; dialog.inert = false; return; }
    closed = true;
    position ||= bridge?.captureAnchor() || anchor;
    root.unmount(); dialog.close(); dialog.remove(); previousFocus?.focus(); onClose?.(position);
  };
  dialog.addEventListener('cancel', event => { event.preventDefault(); close(); });
  dialog.showModal();
  root.render(<Reader paper={paper} anchor={anchor} close={close} control={value => { bridge = value; }}
    frameReady={value => { sourceWindow = value; }}
    readingReady={value => { if (!closed && !closing) onReady?.({...value, instanceId}); }} />);
  return {close, instanceId, get sourceWindow() { return sourceWindow; }};
}
