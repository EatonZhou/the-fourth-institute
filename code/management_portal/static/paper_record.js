(() => {
  "use strict";

  if (window.PortalPaperRecord) return;

  // Changes to the selected Paper's record: deleting it (its Chat is released
  // first) and uploading or deleting its PDF.
  const DEPENDENCIES = Object.freeze([
    "$", "activeChatPaperId", "alertError", "currentPaperChatPanel", "loadChatHistory", "loadDeepReadStatus",
    "loadLibrary", "mergeDetailPayload", "paperApi", "renderAssistantContext", "renderTree",
    "renderWorkspace", "setMode", "state",
  ]);

  function createRecord(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalPaperRecord is missing: ${missing.join(", ")}`);
    const {
      $, activeChatPaperId, alertError, currentPaperChatPanel, loadChatHistory, loadDeepReadStatus,
      loadLibrary, mergeDetailPayload, paperApi, renderAssistantContext, renderTree, renderWorkspace, setMode,
      state,
    } = dependencies;

    function openPdfUploadPicker() {
      const input = $("paperPdfUploadInput");
      if (!state.selectedId || !input || state.pdfUploadInFlight) return;
      input.value = "";
      input.click();
    }

    async function uploadCurrentPaperPdf(file) {
      const paperId = String(state.detail?.paper?.id || state.selectedId || "").trim();
      if (!paperId || !file) return;
      if (!String(file.name || "").toLowerCase().endsWith(".pdf")) {
        window.PortalUI.toast(window.PortalI18n.t("请选择 PDF 文件。"), "warning");
        return;
      }
      state.pdfUploadInFlight = true;
      renderWorkspace();
      try {
        const form = new FormData();
        form.append("paper_id", paperId);
        form.append("file", file, file.name || "paper.pdf");
        const payload = await paperApi.postForm("/paper/api/papers/upload-pdf", form);
        if (String(state.selectedId || "") !== paperId) return;
        mergeDetailPayload(payload, { replace: true });
        await loadDeepReadStatus(paperId, { render: false });
        await loadLibrary({ keepDetail: true });
        renderTree();
        renderAssistantContext();
        renderWorkspace();
      } finally {
        state.pdfUploadInFlight = false;
        renderWorkspace();
      }
    }

    return Object.freeze({
      openPdfUploadPicker, uploadCurrentPaperPdf,
    });
  }

  window.PortalPaperRecord = Object.freeze({ createRecord, dependencies: DEPENDENCIES });
})();
