// Isolated prototype: the iframe owns all upstream globals and listeners.
// Zotero's PDF URL entry passes only url/password to the embedded PDF.js viewer.
// Supply the versioned transport at that viewer's open boundary, for each split view.
function bindPdfSource(frame, source, status) {
  const active = new Set();
  function viewerLoaded(event) {
    const win = event.detail?.source;
    if (win?.parent !== frame.contentWindow) return;
    const app = win.PDFViewerApplication;
    const open = app.open.bind(app);
    app.open = args => {
      if (args.url !== source.url) return open(args);
      const controller = new AbortController();
      active.add(controller);
      // PDF.js may transfer the initial buffer to its worker. Each view needs its own copy.
      const range = new win.pdfjsLib.PDFDataRangeTransport(source.length, new win.Uint8Array(source.initialBytes), true);
      range.abort = () => { controller.abort(); active.delete(controller); };
      range.requestDataRange = (begin, end) => {
        source.read(begin, end, controller.signal).then(bytes => {
          if (!controller.signal.aborted) range.onDataRange(begin, new win.Uint8Array(bytes));
        }).catch(error => {
          if (controller.signal.aborted) return;
          status(error.message);
          range.abort();
          void app.pdfLoadingTask?.destroy();
        });
      };
      return open({...args, range, rangeChunkSize: source.chunkSize, disableStream: true, disableAutoFetch: true});
    };
  }
  const doc = frame.contentDocument;
  doc.addEventListener('webviewerloaded', viewerLoaded);
  return () => {
    doc.removeEventListener('webviewerloaded', viewerLoaded);
    for (const controller of active) controller.abort();
    active.clear();
  };
}

export async function mountReader(frame, {load, save, remove, savePosition, status = () => {}}) {
  const initial = await load();
  let closed = false;
  let pending = Promise.resolve();
  const enqueue = action => {
    const operation = pending.then(action);
    pending = operation.catch(error => {status(error.message);});
    return operation;
  };
  const unbind = initial.pdfSource && bindPdfSource(frame, initial.pdfSource, status);
  const reader = frame.contentWindow.createReader({
    type: 'pdf', data: initial.pdfSource ? {url: initial.pdfSource.url} : {buf: initial.pdf}, annotations: initial.annotations,
    primaryViewState: initial.position, authorName: initial.author,
    readOnly: false, loggedIn: true, showAnnotations: true,
    sidebarOpen: true, sidebarWidth: 260, sidebarView: 'annotations',
    onSaveAnnotations: annotations => enqueue(() => save(structuredClone(annotations))),
    onDeleteAnnotations: ids => enqueue(() => remove([...ids])),
    onAnnotationSaveError: error => status(error.message),
    onChangeViewState: (state, primary) => {
      if (primary && !closed) void enqueue(() => savePosition(structuredClone(state))).catch(() => {});
    },
    onOpenLink: url => {
      if (/^https?:\/\//i.test(url)) frame.ownerDocument.defaultView.open(url, '_blank', 'noopener');
    },
    onAddToNote: () => status('Note Editor is outside this prototype'),
  });
  return {
    reader,
    getSaveState: () => reader.getAnnotationSaveState(),
    locate: id => reader.navigate({annotationID: id}),
    async close() {
      if (closed) return;
      // Blur commits the current comment before the save barrier is requested.
      const win = frame.contentWindow;
      const blur = document => {
        const active = document.activeElement;
        if (active?.tagName === 'IFRAME') blur(active.contentDocument);
        active?.blur();
      };
      blur(win.document);
      frame.inert = true;
      try {await reader.flushAnnotations();}
      catch (error) {frame.inert = false; status(error.message); throw error;}
      await pending;
      closed = true;
      unbind?.();
      frame.remove();
    },
  };
}
