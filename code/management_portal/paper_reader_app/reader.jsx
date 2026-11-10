import React, {useEffect, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {selectionHighlight, paintHighlights} from './geometry.js';

async function api(url, payload) {
  return window.PortalAPI.request(url, payload ? {method: 'POST', body: payload} : {});
}

function Reader({paper, anchor, close, capture}) {
  const viewer = useRef(null), controller = useRef(null), values = useRef([]), alive = useRef(true);
  const currentZoom = useRef(1);
  const [highlights, setHighlights] = useState([]), [documentId, setDocumentId] = useState('');
  const [selected, setSelected] = useState(null), [status, setStatus] = useState('正在加载 PDF…');
  const [busy, setBusy] = useState(false), [zoom, setZoom] = useState(1);
  const [showHighlights, setShowHighlights] = useState(false);
  const update = items => { values.current = items; setHighlights(items); paintHighlights(viewer.current, items); };
  useEffect(() => {
    const c = window.PortalPdfPreview.render({root: viewer.current, url: paper.pdf_url,
      title: paper.title, pageClass: 'pdfJsPage', textLayerClass: 'textLayer', statusClass: 'pdfJsStatus',
      minPageWidth: 280, maxPageWidth: 2400, minScale: 0.2, maxScale: 8, horizontalPadding: 40,
      onPage: () => paintHighlights(viewer.current, values.current)});
    controller.current = c;
    capture(() => c.captureAnchor());
    c.promise.then(async pdf => {
      if (!alive.current) return;
      if (!pdf) throw new Error('PDF 无法解析，请使用“打开原文件”；高亮暂不可用。');
      if (anchor) c.restoreAnchor(anchor);
      const fingerprint = pdf.fingerprints?.[0] || pdf.fingerprint;
      if (!fingerprint) throw new Error('PDF 缺少版本标识，高亮暂不可用。');
      const data = await api(`/paper/api/highlights?id=${encodeURIComponent(paper.id)}&document=${encodeURIComponent(fingerprint)}`);
      if (!alive.current) return;
      update(data.highlights); setDocumentId(fingerprint); setStatus('选中文字后点击“高亮”；自动保存到当前账号。');
    }).catch(error => { if (alive.current) setStatus(error.message); });
    const resize = new ResizeObserver(async () => {
      try {
        await c.promise;
        if (!alive.current) return;
        const position = c.captureAnchor();
        c.commitGestureScale({scaleMultiplier: currentZoom.current});
        if (position) c.restoreAnchor(position);
        await c.redraw();
      } catch (error) { if (alive.current) setStatus(error.message); }
    });
    resize.observe(viewer.current);
    return () => { alive.current = false; resize.disconnect(); c.cancel(); };
  }, []);
  async function save(operation, item) {
    setBusy(true);
    try {
      await api('/paper/api/highlights', {paper_id: paper.id, document: documentId, operation,
        ...(operation === 'add' ? {highlight: item} : {id: item.id})});
      if (!alive.current) return;
      update(operation === 'add' ? [...values.current, item] : values.current.filter(h => h.id !== item.id));
      setSelected(null); window.getSelection()?.removeAllRanges(); setStatus('已保存');
    } catch (error) { if (alive.current) setStatus(`保存失败：${error.message}`); }
    finally { if (alive.current) setBusy(false); }
  }
  async function changeZoom(value) {
    currentZoom.current = value;
    setZoom(value);
    const c = controller.current, position = c.captureAnchor();
    c.commitGestureScale({scaleMultiplier: value});
    if (position) c.restoreAnchor(position);
    try { await c.redraw(); } catch (error) { if (alive.current) setStatus(error.message); }
  }
  return <div className="paperReadingApp">
    <header className="paperReadingToolbar">
      <strong title={paper.title}>{paper.title || '论文阅读'}</strong>
      <button type="button" disabled={zoom <= 0.5} onClick={() => changeZoom(Math.max(0.5, zoom - 0.25))} aria-label="缩小">−</button>
      <button type="button" onClick={() => changeZoom(1)}>适应宽度</button>
      <button type="button" disabled={zoom >= 3} onClick={() => changeZoom(Math.min(3, zoom + 0.25))} aria-label="放大">＋</button>
      <span>{Math.round(zoom * 100)}%</span>
      <button type="button" disabled={!selected || !documentId || busy} onMouseDown={e => e.preventDefault()} onClick={() => save('add', selected)}>高亮</button>
      <button type="button" aria-pressed={showHighlights} onClick={() => setShowHighlights(!showHighlights)}>我的高亮</button>
      <a href={paper.pdf_url} target="_blank" rel="noopener noreferrer">打开原文件</a>
      <button type="button" onClick={() => close(controller.current?.captureAnchor())}>退出全屏</button>
    </header>
    <p className="paperReadingStatus" role="status">{status}</p>
    <main className="paperReadingBody">
      <div className="pdfJsViewer paperReadingPdf" ref={viewer} tabIndex="0" aria-label="论文全文"
        onMouseUp={() => setSelected(selectionHighlight(window.getSelection(), viewer.current))}
        onKeyUp={() => setSelected(selectionHighlight(window.getSelection(), viewer.current))} />
      <aside hidden={!showHighlights} aria-label="我的高亮" className="paperReadingHighlights">
        <h2>我的高亮（{highlights.length}）</h2>
        {!highlights.length && <p>选中文字即可添加高亮。</p>}
        {highlights.map(item => <article key={item.id}>
          <button className="paperHighlightJump" onClick={async () => {
            const c = controller.current, page = item.rects[0].page;
            await c.ensurePage(page); if (!alive.current) return;
            viewer.current.querySelector(`[data-pdf-page="${page}"]`)?.scrollIntoView({block: 'start'});
          }}>第 {item.rects[0].page} 页：{item.text}</button>
          <button disabled={busy} onClick={() => save('delete', item)}>删除高亮</button>
        </article>)}
      </aside>
    </main>
  </div>;
}

export function openReader({paper, anchor, onClose}) {
  const previousFocus = document.activeElement;
  const dialog = document.createElement('dialog');
  dialog.className = 'paperReadingDialog'; dialog.setAttribute('aria-label', '全屏论文阅读');
  document.body.append(dialog);
  const root = createRoot(dialog);
  let closed = false;
  let capturePosition = () => anchor;
  const close = position => {
    if (closed) return; closed = true;
    position ||= capturePosition();
    root.unmount(); dialog.close(); dialog.remove(); previousFocus?.focus(); onClose?.(position);
  };
  dialog.addEventListener('cancel', event => { event.preventDefault(); close(); });
  dialog.showModal();
  root.render(<Reader paper={paper} anchor={anchor} close={close} capture={fn => { capturePosition = fn; }} />);
  return {close};
}
