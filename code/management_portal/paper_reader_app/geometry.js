// PDF-page-relative coordinates survive zoom, resize and lazy redraws.
export function selectionHighlight(selection, viewer) {
  if (!selection?.rangeCount || !String(selection).trim()) return null;
  const range = selection.getRangeAt(0);
  if (!viewer.contains(range.startContainer) || !viewer.contains(range.endContainer)) return null;
  const inText = node => (node.nodeType === 1 ? node : node.parentElement)?.closest('.textLayer');
  if (!inText(range.startContainer) || !inText(range.endContainer)) return null;
  const rects = [];
  const seen = new Set();
  for (const page of viewer.querySelectorAll('[data-pdf-page]')) {
    if (!range.intersectsNode(page)) continue;
    const bounds = page.getBoundingClientRect();
    if (!bounds.width || !bounds.height) continue;
    for (const rect of range.getClientRects()) {
      const left = Math.max(bounds.left, rect.left), top = Math.max(bounds.top, rect.top);
      const right = Math.min(bounds.right, rect.right), bottom = Math.min(bounds.bottom, rect.bottom);
      if (right - left < 1 || bottom - top < 1) continue;
      const value = {page: Number(page.dataset.pdfPage), x: (left - bounds.left) / bounds.width,
        y: (top - bounds.top) / bounds.height, width: (right - left) / bounds.width, height: (bottom - top) / bounds.height};
      const key = JSON.stringify(value);
      if (!seen.has(key)) { seen.add(key); rects.push(value); }
    }
  }
  return rects.length ? {id: crypto.randomUUID(), text: String(selection).trim(), rects} : null;
}

export function paintHighlights(viewer, highlights) {
  viewer.querySelectorAll('.paperHighlightLayer').forEach(node => node.remove());
  for (const page of viewer.querySelectorAll('[data-pdf-page]')) {
    const layer = document.createElement('div');
    layer.className = 'paperHighlightLayer';
    layer.setAttribute('aria-hidden', 'true');
    for (const item of highlights) for (const rect of item.rects) {
      if (rect.page !== Number(page.dataset.pdfPage)) continue;
      const mark = document.createElement('span');
      Object.assign(mark.style, {left: `${rect.x * 100}%`, top: `${rect.y * 100}%`,
        width: `${rect.width * 100}%`, height: `${rect.height * 100}%`});
      layer.append(mark);
    }
    page.append(layer);
  }
}
