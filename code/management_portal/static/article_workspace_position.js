(() => {
  "use strict";
  if (window.PortalArticleWorkspacePosition) return;

  // Reading location only: never persist source, drafts or document payloads.
  const DOCUMENT_FIELDS = ["viewState", "selection", "selectionStart", "selectionEnd", "scrollTop", "scrollLeft", "viewMode"];
  function createPosition({ state, captureActiveDocument, captureArticlePdfPosition, openArticleFile, activateDocument }) {
    const store = window.PortalPageState.create("article-reading-position", {
      getUser: () => window.PortalIdentity?.username?.() || "anonymous",
    });
    let readyId = "", record = null, timer = 0, capturing = false;

    function persist() {
      window.clearTimeout(timer);
      timer = 0;
      if (!readyId || readyId !== state.selectedId || capturing) return;
      capturing = true;
      try {
        captureActiveDocument();
        const pdf = state.pdfRestorePosition || (state.outputView === "pdf" ? captureArticlePdfPosition() : null) || record?.pdf;
        record = {
          activeFile: state.openFile,
          tabs: state.openTabs.slice(0, 50).flatMap(path => {
            const doc = state.documents.get(path);
            if (!doc || doc.linked) return [];
            return [{ path, ...Object.fromEntries(DOCUMENT_FIELDS.map(key => [key, doc[key]])) }];
          }),
          pdf: pdf ? { page: pdf.page, xRatio: pdf.xRatio, yRatio: pdf.yRatio } : null,
          pdfZoom: state.pdfZoom,
          updatedAt: Date.now(),
        };
        const articles = { ...(store.read().articles || {}), [readyId]: record };
        const recent = Object.entries(articles).sort((a, b) => (b[1]?.updatedAt || 0) - (a[1]?.updatedAt || 0)).slice(0, 30);
        store.write({ articles: Object.fromEntries(recent) });
      } catch (_error) {
        // Storage can be unavailable; editing must remain usable.
      } finally { capturing = false; }
    }

    function schedule() {
      if (capturing || !readyId || readyId !== state.selectedId) return;
      window.clearTimeout(timer);
      timer = window.setTimeout(persist, 250);
    }

    function prepare(articleId) {
      window.clearTimeout(timer);
      timer = 0;
      readyId = "";
      try { record = store.read().articles?.[articleId] || null; } catch (_error) { record = null; }
      state.pdfZoom = Math.max(0.6, Math.min(2.4, Number(record?.pdfZoom) || 1));
      state.pdfRestorePosition = Number.isFinite(record?.pdf?.page) && record.pdf.page > 0 ? { ...record.pdf } : null;
    }

    async function restoreDocuments(articleId, primary) {
      const saved = record;
      const current = () => state.selectedId === articleId;
      try {
        for (const location of (Array.isArray(saved?.tabs) ? saved.tabs.slice(0, 50) : [])) {
          if (!current()) return;
          if (!state.filesByPath.has(location.path)) continue;
          const doc = await openArticleFile(location.path, { activate: false });
          if (!current()) return;
          if (doc) for (const key of DOCUMENT_FIELDS) if (location[key] !== undefined) doc[key] = location[key];
        }
        if (!current()) return;
        const active = state.documents.has(saved?.activeFile) ? saved.activeFile : state.openTabs[0];
        if (active) activateDocument(active, { captureCurrent: false });
        else if (primary) await openArticleFile(primary);
      } finally {
        if (current()) readyId = articleId;
      }
    }
    return Object.freeze({ persist, schedule, prepare, restoreDocuments });
  }
  window.PortalArticleWorkspacePosition = Object.freeze({ createPosition });
})();
