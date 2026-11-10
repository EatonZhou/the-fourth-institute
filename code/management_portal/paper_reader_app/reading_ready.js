// Reading lifecycle only; annotation persistence is owned by viewer_adapter.
export function observeReadingReady(frame, paper, notify) {
  let disposed = false, app, listener, sent = false;
  const expected = new URL(paper.pdf_url, location.href).href;
  const loaded = async event => {
    if (disposed || event.detail?.source !== frame.contentWindow) return;
    const source = frame.contentWindow;
    if (source.location.origin !== location.origin) return;
    app = source.PDFViewerApplication;
    await app.initializedPromise;
    if (disposed) return;
    listener = event => {
      const document = app.pdfDocument;
      const view = app.pdfViewer.getPageView(event.pageNumber - 1);
      if (disposed || sent || event.error || event.source !== view || !document ||
          new URL(app.url, location.href).href !== expected || !view?.div.querySelector('canvas')) return;
      const fingerprint = document.fingerprints?.[0];
      if (!fingerprint || frame.contentWindow !== source) return;
      sent = true;
      notify({paperId: paper.id, pdfUrl: expected, fingerprint, source});
    };
    app.eventBus.on('pagerendered', listener);
  };
  document.addEventListener('webviewerloaded', loaded);
  return () => { disposed = true; document.removeEventListener('webviewerloaded', loaded);
    if (listener) app?.eventBus.off('pagerendered', listener); };
}
