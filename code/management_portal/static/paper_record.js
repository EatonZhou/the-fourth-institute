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

    async function deleteCurrentPaper() {
      const paper = state.detail?.paper || null;
      const paperId = String(paper?.id || state.selectedId || "").trim();
      if (!paperId || state.deletingPaperId) return;
      const title = String(paper?.title || paperId);
      state.deletingPaperId = paperId;
      let closedChat = false;
      let deleted = false;
      try {
        if (!await window.PortalUI.confirmAction("delete", title, {
          consequence: "这篇论文及其 /share/Papers 文件记录会一并删除。",
        })) return;
        // The cascade removes the Chat before the HTTP response returns.
        // Release its readers before deletion events can refresh that Resident.
        if (activeChatPaperId() === paperId || currentPaperChatPanel()?.surface?.()?.paper_id === paperId) {
          await currentPaperChatPanel()?.closeSurface?.();
          state.chatHistoryLoadedFor = "";
          closedChat = true;
          currentPaperChatPanel()?.setBusy?.(true);
        }
        await paperApi.request("/paper/api/papers/delete", {
          method: "POST",
          body: JSON.stringify({ paper_id: paperId }),
        });
        deleted = true;
        if (state.selectedId === paperId) {
          state.selectedId = "";
          state.detail = null;
          state.deepReadStatus = null;
          setMode("library");
        }
        await loadLibrary({ keepDetail: Boolean(state.selectedId) });
        renderWorkspace();
      } finally {
        state.deletingPaperId = "";
        if (closedChat) {
          currentPaperChatPanel()?.setBusy?.(false);
          if (!deleted && activeChatPaperId() === paperId) {
            await loadChatHistory().catch(alertError);
          }
        }
      }
    }

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
        mergeDetailPayload(await paperApi.postForm("/paper/api/papers/upload-pdf", form), { replace: true });
        state.selectedId = state.detail.paper.id;
        await loadDeepReadStatus(state.selectedId, { render: false });
        await loadLibrary({ keepDetail: true });
        renderTree();
        renderAssistantContext();
        renderWorkspace();
      } finally {
        state.pdfUploadInFlight = false;
        renderWorkspace();
      }
    }

    async function deleteCurrentPaperPdf() {
      const paper = state.detail?.paper || null;
      const paperId = String(paper?.id || state.selectedId || "").trim();
      if (!paperId || state.pdfDeleteInFlight) return;
      const title = String(paper?.title || paperId);
      if (!await window.PortalUI.confirmAction("delete", `${title} 的 PDF 文件`)) return;
      state.pdfDeleteInFlight = true;
      renderWorkspace();
      try {
        mergeDetailPayload(await paperApi.request("/paper/api/papers/delete-pdf", {
          method: "POST",
          body: JSON.stringify({ paper_id: paperId }),
        }), { replace: true });
        state.selectedId = state.detail.paper.id;
        state.deepReadStatus = null;
        await loadLibrary({ keepDetail: true });
        renderTree();
        renderAssistantContext();
        renderWorkspace();
      } finally {
        state.pdfDeleteInFlight = false;
        renderWorkspace();
      }
    }

    return Object.freeze({
      deleteCurrentPaper, deleteCurrentPaperPdf, openPdfUploadPicker, uploadCurrentPaperPdf,
    });
  }

  window.PortalPaperRecord = Object.freeze({ createRecord, dependencies: DEPENDENCIES });
})();
