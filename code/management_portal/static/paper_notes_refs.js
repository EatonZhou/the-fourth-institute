(() => {
  "use strict";

  if (window.PortalPaperNotesRefs) return;

  // Notes and references: their modes, saving and deleting notes, extracting
  // references, registering one as a Paper and opening it.
  const DEPENDENCIES = Object.freeze([
    "$", "alertError", "assetsJobRunning", "assetsJobStatusText", "chip", "currentAssetsJob",
    "detailSectionLoadingHtml", "detailSectionsLoaded", "esc", "loadDetail", "loadLibrary", "md",
    "needPaperHtml", "paperApi", "paperStateHtml", "renderWorkspace", "setMode", "state",
  ]);

  function createNotesRefs(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalPaperNotesRefs is missing: ${missing.join(", ")}`);
    const {
      $, alertError, assetsJobRunning, assetsJobStatusText, chip, currentAssetsJob, detailSectionLoadingHtml,
      detailSectionsLoaded, esc, loadDetail, loadLibrary, md, needPaperHtml, paperApi, paperStateHtml,
      renderWorkspace, setMode, state,
    } = dependencies;

    function renderNotesMode() {
      const detail = state.detail;
      if (!detail?.paper) {
        $("workspaceMain").innerHTML = needPaperHtml();
        return;
      }
      if (!detailSectionsLoaded(["notes"])) {
        $("workspaceMain").innerHTML = detailSectionLoadingHtml("Notes");
        return;
      }
      const notes = detail.notes || [];
      $("workspaceMain").innerHTML = window.PortalI18n.html`
          <form class="apiCard" id="noteForm" style="margin-bottom:10px;">
            <h3>Add note</h3>
            <textarea class="portalControl" id="noteInput" placeholder="记录问题、判断、摘录或后续 TODO"></textarea>
            <div class="portalToolbar paperToolbar" style="justify-content:flex-end;margin-top:8px;">
              <button class="portalButton is-compact is-primary" type="submit">保存笔记</button>
            </div>
          </form>
          <div class="reviewOutput">
            ${notes.length ? notes.map(renderNote).join("") : paperStateHtml(window.PortalI18n.t("No notes yet."))}
          </div>
        `;
      $("noteForm")?.addEventListener("submit", event => saveNote(event).catch(alertError));
    }

    function renderNote(note) {
      return window.PortalI18n.html`
          <article class="apiCard">
            <div class="portalToolbar paperToolbar" style="justify-content:space-between;">
              <h3>${esc(note.created_by || "note")}</h3>
              <div class="chips">
                ${chip(new Date((note.created_at || 0) * 1000).toLocaleString())}
                <button class="portalButton is-compact is-danger" type="button" data-delete-note="${esc(note.note_id || "")}">删除</button>
              </div>
            </div>
            <div class="markdownBody">${md(note.body || "")}</div>
          </article>
        `;
    }

    function renderRefsMode() {
      const detail = state.detail;
      if (!detail?.paper) {
        $("workspaceMain").innerHTML = needPaperHtml();
        return;
      }
      if (!detailSectionsLoaded(["refs"])) {
        $("workspaceMain").innerHTML = detailSectionLoadingHtml("References");
        return;
      }
      const paperId = String(detail.paper.id || state.selectedId || "");
      const refs = detail.refs || [];
      const assetsJob = currentAssetsJob();
      const assetsRunning = assetsJobRunning(assetsJob);
      if (!refs.length && paperId && !assetsRunning && !state.refExtractInFlight && !state.refsAutoExtracted[paperId]) {
        queueAutoExtractRefs(paperId);
      }
      const loading = state.refExtractInFlight === paperId || assetsRunning;
      const error = state.refExtractErrors[paperId] || "";
      const emptyText = loading
        ? (state.refExtractInFlight === paperId
          ? window.PortalI18n.t("Extracting refs from the current PDF...")
          : assetsJobStatusText(assetsJob))
        : (error ? window.PortalI18n.text`Refs extraction failed: ${error}` : window.PortalI18n.t("No refs found in this paper."));
      $("workspaceMain").innerHTML = window.PortalI18n.html`
          <div class="reviewOutput">
            ${refs.length ? refs.map(renderRef).join("") : paperStateHtml(emptyText, {
            state: error ? "error" : (loading ? "loading" : "empty"),
          })}
          </div>
        `;
    }

    function renderRef(ref) {
      const registeredId = String(ref.registered_paper_id || "");
      const refId = String(ref.ref_id || "");
      const registering = state.refRegisterInFlight && state.refRegisterInFlight === refId;
      const actions = registeredId
        ? chip("registered", "good")
        : window.PortalI18n.html`<button class="portalButton is-compact is-primary" type="button" data-register-ref="${esc(refId)}" ${registering ? "disabled" : ""}>${registering ? "Registering..." : "Register"}</button>`;
      return window.PortalI18n.html`
          <article class="apiCard refCard ${registeredId ? "clickable" : ""}" ${registeredId ? `data-open-ref-paper="${esc(registeredId)}"` : ""}>
            <div class="refHead">
              <h3 class="refTitle">${esc(ref.title || `Reference ${ref.index || ""}`)}</h3>
              <div class="refActions">
                ${actions}
              </div>
            </div>
            <div class="refRaw">${esc(ref.raw || "")}</div>
          </article>
        `;
    }

    async function saveNote(event) {
      event.preventDefault();
      if (!state.selectedId) return;
      const input = $("noteInput");
      const body = String(input?.value || "").trim();
      if (!body) return;
      const note = await paperApi.request("/paper/api/notes/create", {
        method: "POST",
        body: JSON.stringify({ paper_id: state.selectedId, body }),
      });
      state.detail.notes = [note, ...(state.detail.notes || [])];
      renderWorkspace();
    }

    async function deleteNote(noteId) {
      const id = String(noteId || "").trim();
      if (!id) return;
      if (!await window.PortalUI.confirmAction("delete", "这条笔记")) return;
      await paperApi.request("/paper/api/notes/delete", {
        method: "POST",
        body: JSON.stringify({ note_id: id }),
      });
      state.detail.notes = (state.detail.notes || []).filter(item => String(item.note_id || "") !== id);
      renderWorkspace();
    }

    function queueAutoExtractRefs(paperId) {
      const id = String(paperId || "").trim();
      if (!id || state.refExtractInFlight || state.refsAutoExtracted[id]) return;
      state.refsAutoExtracted[id] = true;
      state.refExtractInFlight = id;
      state.refExtractErrors[id] = "";
      queueMicrotask(() => {
        extractRefs({ paperId: id }).catch(err => {
          state.refExtractErrors[id] = err.message || String(err || "unknown error");
          if (state.mode === "refs") renderWorkspace();
          alertError(err);
        });
      });
    }

    async function extractRefs({ paperId = state.selectedId } = {}) {
      const id = String(paperId || "").trim();
      if (!id) return;
      try {
        const result = await paperApi.request("/paper/api/refs/extract", {
          method: "POST",
          body: JSON.stringify({ paper_id: id }),
        });
        if (String(state.selectedId || "") === id && state.detail?.paper) {
          state.detail.refs = result.refs || [];
          state.refExtractErrors[id] = "";
        }
      } finally {
        if (state.refExtractInFlight === id) state.refExtractInFlight = "";
        if (state.mode === "refs") renderWorkspace();
      }
    }

    async function registerRef(refId) {
      const id = String(refId || "").trim();
      if (!id || state.refRegisterInFlight) return;
      state.refRegisterInFlight = id;
      renderWorkspace();
      try {
        const result = await paperApi.request("/paper/api/refs/register", {
          method: "POST",
          body: JSON.stringify({ paper_id: state.selectedId || "", ref_id: id }),
        });
        if (result.ref && state.detail?.refs) {
          state.detail.refs = state.detail.refs.map(item => String(item.ref_id || "") === id ? result.ref : item);
        }
        await loadLibrary({ keepDetail: true });
        if (state.mode === "refs") renderWorkspace();
      } finally {
        state.refRegisterInFlight = "";
        if (state.mode === "refs") renderWorkspace();
      }
    }

    async function openRefPaper(paperId) {
      const id = String(paperId || "").trim();
      if (!id) return;
      await loadDetail(id);
      setMode("reader");
    }

    return Object.freeze({
      deleteNote, openRefPaper, registerRef, renderNotesMode, renderRefsMode,
    });
  }

  window.PortalPaperNotesRefs = Object.freeze({ createNotesRefs, dependencies: DEPENDENCIES });
})();
