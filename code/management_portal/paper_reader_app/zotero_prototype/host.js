// Isolated prototype: the iframe owns all upstream globals and listeners.
export async function mountReader(frame, {load, save, remove, savePosition, status = () => {}}) {
  const initial = await load();
  let closed = false;
  let pending = Promise.resolve();
  const enqueue = action => {
    const operation = pending.then(action);
    pending = operation.catch(error => {status(error.message);});
    return operation;
  };
  const reader = frame.contentWindow.createReader({
    type: 'pdf', data: {buf: initial.pdf}, annotations: initial.annotations,
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
      frame.remove();
    },
  };
}
