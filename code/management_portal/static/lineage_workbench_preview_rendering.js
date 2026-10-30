(() => {
  "use strict";

  if (window.PortalLineageWorkbenchPreviewRendering) return;

  // Showing a document in the Preview panel: its title, path and open input,
  // word wrap, opening it in a new tab, the state of the panel's actions and
  // painting the document.

  const DEPENDENCIES = Object.freeze([
    "$id", "activePreviewPayload", "activePreviewSnapshot", "currentLineageWorkbench", "destroyPreviewCodeEditor",
    "focusInitialPreviewLine", "previewDownloadUrl", "previewIsEditable", "previewRefreshKey", "previewState",
    "previewTitleForPayload",
  ]);

  function createPreviewRendering(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalLineageWorkbenchPreviewRendering is missing: ${missing.join(", ")}`);
    const {
      $id, activePreviewPayload, activePreviewSnapshot, currentLineageWorkbench, destroyPreviewCodeEditor,
      focusInitialPreviewLine, previewDownloadUrl, previewIsEditable, previewRefreshKey, previewState,
      previewTitleForPayload,
    } = dependencies;

    function setPreviewTitle(title) {
      const text = String(title || window.PortalI18n.t("预览")).trim() || window.PortalI18n.t("预览");
      const el = $id("previewTitle");
      el.textContent = text;
      el.title = text;
    }

    function setPreviewOpenInput(value) {
      const input = $id("previewOpenInput");
      if (input) input.value = String(value || "");
    }

    function previewUsesTextLayout(payload = activePreviewPayload()) {
      if (!payload || payload.binary) return false;
      return ["markdown", "json", "jsonl", "text", "code"].includes(String(payload.kind || ""));
    }

    function applyPreviewWordWrap() {
      previewState.previewEditorTools?.applyWordWrap?.({
        root: "#agentPreviewPanel",
        button: "#wrapPreviewBtn",
      });
      previewState.previewCodeEditor?.setWordWrap?.(previewState.previewEditorTools?.wordWrap?.() !== false);
    }

    function togglePreviewWordWrap() {
      previewState.previewEditorTools?.toggleWordWrap?.();
      applyPreviewWordWrap();
    }

    function previewExternalUrl(payload = activePreviewPayload()) {
      if (!payload) return "";
      if (String(payload.source || "") === "remote_link_card") return String(payload.href || payload.path || "");
      return window.PortalPreviewRenderers.previewUrl(payload, {
        rawUrl: current => previewState.agentPreviewSource?.rawUrl?.(current) || "",
      });
    }

    function openPreviewInNewTab() {
      const payload = activePreviewPayload();
      if (previewUsesTextLayout(payload)) {
        return window.PortalPreviewRenderers.openRenderedDocument($id("previewBox"), {
          title: previewTitleForPayload(payload),
        });
      }
      const url = previewExternalUrl();
      if (!url) return false;
      window.open(url, "_blank", "noopener,noreferrer");
      return true;
    }

    function renderPreviewActions() {
      previewState.agentPreviewPanelComponent?.setReadError(activePreviewSnapshot().readError);
      const editBtn = $id("editPreviewBtn");
      const visualBtn = $id("visualPreviewBtn");
      const saveBtn = $id("savePreviewBtn");
      const undoBtn = $id("undoPreviewBtn");
      const redoBtn = $id("redoPreviewBtn");
      const wrapBtn = $id("wrapPreviewBtn");
      const externalBtn = $id("openPreviewExternalBtn");
      const refreshBtn = $id("refreshPreviewBtn");
      const panelActive = false;
      const action = previewState.previewController?.actionState?.() || {
        hasPayload: !!activePreviewPayload(),
        loading: false,
        editable: previewIsEditable(),
        editing: activePreviewSnapshot().editing,
        dirty: activePreviewSnapshot().dirty,
        saving: activePreviewSnapshot().saving,
        canEdit: previewIsEditable() && !activePreviewSnapshot().saving,
        canSave: activePreviewSnapshot().dirty && !activePreviewSnapshot().saving,
      };
      const downloadUrl = previewDownloadUrl();
      previewState.agentPreviewPanelComponent?.setDownload?.(panelActive ? "" : downloadUrl);
      const hasPayload = action.hasPayload ?? !!activePreviewPayload();
      const externalUrl = previewExternalUrl();
      if (refreshBtn) {
        refreshBtn.disabled = (
          panelActive
          || !hasPayload
          || !previewRefreshKey()
          || action.loading
          || action.dirty
          || action.saving
        );
      }
      editBtn.disabled = panelActive || !action.canEdit;
      editBtn.classList.toggle("active", action.editing);
      editBtn.setAttribute("aria-pressed", action.editing ? "true" : "false");
      visualBtn.disabled = panelActive || !hasPayload || action.saving;
      visualBtn.classList.toggle("active", !action.editing);
      visualBtn.setAttribute("aria-pressed", action.editing ? "false" : "true");
      saveBtn.disabled = panelActive || !action.canSave;
      undoBtn.disabled = panelActive || !action.editing || action.saving || !previewState.previewCodeEditor;
      redoBtn.disabled = panelActive || !action.editing || action.saving || !previewState.previewCodeEditor;
      wrapBtn.disabled = panelActive || !previewUsesTextLayout();
      externalBtn.disabled = panelActive || action.saving || !(previewUsesTextLayout() || externalUrl);
      const previewPath = hasPayload
        ? String(activePreviewPayload()?.path || activePreviewPayload()?.resourceAddress || "")
        : "";
      previewState.agentPreviewPanelComponent?.setPath?.(previewPath);
      applyPreviewWordWrap();
      previewState.previewTabsController?.render?.();
    }

    function renderPreview(payload, options = {}) {
      if (previewState.previewController) return previewState.previewController.present(payload, options);
      return paintPreview(payload, options);
    }

    function paintPreview(payload, options = {}) {
      destroyPreviewCodeEditor();
      const box = $id("previewBox");
      const preserveScroll = options.preserveScroll === true;
      const previousScrollTop = box ? box.scrollTop : 0;
      const restorePreviewScroll = () => {
        if (!preserveScroll || !box) return;
        window.requestAnimationFrame(() => {
          box.scrollTop = previousScrollTop;
        });
      };
      previewState.previewSelectionController?.hide?.();
      setPreviewTitle(previewTitleForPayload(payload));
      $id("previewPath").textContent = payload.path || window.PortalI18n.t("预览");
      setPreviewOpenInput(payload.path || "");
      const renderPromise = currentLineageWorkbench()?.getPanel("preview")?.setDocument(payload);
      previewState.previewTabsController?.render?.();
      applyPreviewWordWrap();
      renderPreviewActions();
      Promise.resolve(renderPromise)
        .then(() => {
          restorePreviewScroll();
          if (!preserveScroll) focusInitialPreviewLine(payload);
          options.afterRender?.();
        })
        .catch(error => window.PortalUI.toast(window.PortalUI.errorMessage(window.PortalI18n.t("预览"), error), "error"));
      return renderPromise;
    }

    return {
      applyPreviewWordWrap, openPreviewInNewTab, paintPreview, renderPreview, renderPreviewActions,
      setPreviewOpenInput, setPreviewTitle, togglePreviewWordWrap,
    };
  }

  window.PortalLineageWorkbenchPreviewRendering = Object.freeze({ createPreviewRendering, dependencies: DEPENDENCIES });
})();
