(() => {
  "use strict";

  if (window.PortalLineageWorkbenchPreviewDocuments) return;

  // The Preview panel's documents: the active document session and its
  // snapshot, the document tabs, creating, activating and emptying sessions,
  // restoring a saved document, connecting the panel and opening a document.

  const DEPENDENCIES = Object.freeze([
    "$id", "apiGet", "apiPost", "applyPreviewWordWrap", "currentAgentChatWorkspace", "currentLineageWorkbench",
    "emptyPreviewSnapshot", "entryMtimeToken", "fileBaseName", "initialPreviewLine", "loadResidentFileDocument",
    "paintPreview", "previewPersistenceLocator", "previewState", "previewTitleForPayload", "renderPreviewActions",
    "renderPreviewEditor", "residentFileParts", "residentFilePartsForService", "residentService",
    "selectedResidentId", "setPreviewOpenInput", "setPreviewTitle", "state", "stopPreviewRefresh",
  ]);

  function createPreviewDocuments(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalLineageWorkbenchPreviewDocuments is missing: ${missing.join(", ")}`);
    const {
      $id, apiGet, apiPost, applyPreviewWordWrap, currentAgentChatWorkspace, currentLineageWorkbench,
      emptyPreviewSnapshot, entryMtimeToken, fileBaseName, initialPreviewLine, loadResidentFileDocument, paintPreview,
      previewPersistenceLocator, previewState, previewTitleForPayload, renderPreviewActions, renderPreviewEditor,
      residentFileParts, residentFilePartsForService, residentService, selectedResidentId, setPreviewOpenInput,
      setPreviewTitle, state, stopPreviewRefresh,
    } = dependencies;

    let previewAuxiliaryController = null;

    function activePreviewSnapshot() {
      return previewState.previewController?.snapshot?.() || emptyPreviewSnapshot;
    }

    function activePreviewPayload() {
      return activePreviewSnapshot().payload;
    }

    function previewIsEditable(payload = activePreviewPayload()) {
      return Boolean(payload && previewState.agentPreviewSource?.canEdit?.(payload));
    }

    function activeAgentPreviewDocument() {
      return previewState.previewTabsController?.active?.() || null;
    }

    function agentPreviewSessionIsVisible(documentState) {
      if (documentState) return previewState.previewTabsController?.isActive?.(documentState.key) === true;
      return !activeAgentPreviewDocument();
    }

    function focusInitialPreviewLine(request = {}) {
      const lineNumber = initialPreviewLine(request);
      if (!lineNumber) return false;
      const target = $id("previewBox")?.querySelector(`[data-preview-line="${lineNumber}"]`);
      if (!target) return false;
      target.scrollIntoView({ block: "center", inline: "nearest" });
      target.focus({ preventScroll: true });
      return true;
    }

    function createAgentPreviewSession(documentState = null) {
      return currentLineageWorkbench().getPanel("preview").createDocumentSession({
        canEdit: payload => previewIsEditable(payload),
        onStateChange: snapshot => {
          if (agentPreviewSessionIsVisible(documentState)) {
            renderPreviewActions();
          }
          previewState.previewTabsController?.render?.();
        },
        onPresent: (payload, _snapshot, meta) => {
          if (payload && agentPreviewSessionIsVisible(documentState)) paintPreview(payload, meta);
        },
        onEditStart: snapshot => {
          if (!agentPreviewSessionIsVisible(documentState)) return;
          renderPreviewEditor();
        },
      });
    }

    function activateAgentPreviewDocument(documentState, snapshot = documentState?.session?.snapshot?.()) {
      if (!documentState?.session) return false;
      previewState.previewController = documentState.session;
      stopPreviewRefresh();
      const sourceType = String(documentState.sourceType || "");
      if (
        sourceType === "resident"
        && documentState.residentId === selectedResidentId()
      ) {
        state.selectedFile = documentState.path;
      }
      if (snapshot?.payload) {
        paintPreview(snapshot.payload);
        if (snapshot.editing) renderPreviewEditor();
      } else {
        const path = documentState.title || documentState.path || documentState.key;
        setPreviewTitle(documentState.label || fileBaseName(path));
        $id("previewPath").textContent = path;
        setPreviewOpenInput(path);
        $id("previewBox").className = "previewBox";
        $id("previewBox").textContent = snapshot?.status === "error"
          ? window.PortalI18n.text`预览失败：${snapshot.error || window.PortalI18n.t("无法读取文件")}`
          : window.PortalI18n.t("加载中…");
      }
      previewState.previewTabsController?.render?.();
      renderPreviewActions();
      return true;
    }

    function resetAgentPreviewToEmpty() {
      previewAuxiliaryController?.destroy?.();
      previewAuxiliaryController = createAgentPreviewSession();
      previewState.previewController = previewAuxiliaryController;
      stopPreviewRefresh();
      setPreviewTitle(window.PortalI18n.t("预览"));
      $id("previewPath").textContent = "";
      setPreviewOpenInput("");
      previewState.previewSelectionController?.hide?.();
      $id("previewBox").className = "previewBox";
      $id("previewBox").textContent = window.PortalI18n.t("选择文件后显示内容。");
      previewState.previewTabsController?.render?.();
      renderPreviewActions();
    }

    function connectPreviewSurface() {
      previewState.agentPreviewPanelComponent = currentLineageWorkbench().getPanel("preview")?.surface || null;
      return previewState.agentPreviewPanelComponent;
    }

    function restorePreviewDocumentRequest(locator = {}) {
      const restored = previewPersistenceLocator(locator);
      if (!restored) return null;
      const path = restored.path;
      if (restored.sourceType === "resident") {
        if (!restored.residentId) return null;
        return {
          ...restored,
          load: () => loadResidentFileDocument(path, restored.residentId),
        };
      }
      if (restored.sourceType === "link") {
        return {
          ...restored,
          load: async () => {
            const payload = await window.PortalFileLinks.load(path);
            return previewState.agentPreviewSource.normalize(payload, {
              path: String(payload?.path || path),
              source: "workspace_link",
            });
          },
        };
      }
      if (restored.sourceType === "resource") {
        const address = restored.resourceAddress || path;
        return {
          ...restored,
          load: async () => {
            const payload = await window.PortalResourceChips.loadPreview(address);
            return payload == null ? null : window.PortalPreviewContract.normalizeDocument({
              ...payload,
              source: "resource",
              path: String(payload.path || address),
            });
          },
        };
      }
      if (restored.sourceType === "link-card") {
        const href = restored.href || path;
        return {
          ...restored,
          href,
          load: async () => window.PortalPreviewContract.normalizeDocument({
            source: "remote_link_card",
            path,
            href,
            title: restored.title || window.PortalI18n.t("链接"),
            kind: "remote-link",
            binary: true,
          }),
        };
      }
      if (!restored.lineageId || !restored.residentId) return null;
      return {
        ...restored,
        load: async () => previewState.agentPreviewSource.normalize(
          await residentService.file({ residentId: restored.residentId, ...residentFilePartsForService(path) }),
          {
            path,
            lineage_id: restored.lineageId,
            residentId: restored.residentId,
            source: "lineage-workspace",
          },
        ),
      };
    }

    function initPreviewController() {
      const previewRuntime = currentLineageWorkbench().getPanel("preview");
      previewState.agentPreviewSource = window.PortalAgentPreviewSource.create({
        apiGet,
        apiPost,
        residentService,
        entryVersion: entryMtimeToken,
        linkFileUrl: window.PortalFileLinks.apiUrl,
        resourceLoader: (address, options) => (
          window.PortalResourceChips.loadPreview(address, options)
        ),
        residentFileParts,
        selectedResidentId,
      });
      previewRuntime.setNavigationAdapter({
        id: "lineage-preview",
        serialize: resource => previewPersistenceLocator(resource),
        restore: locator => restorePreviewDocumentRequest(locator),
      });
      previewState.previewEditorTools = previewRuntime.createDocumentEditorTools({
        storageKey: window.PortalCompatibility.identifier("preview_word_wrap"),
        defaultWordWrap: true,
      });
      previewAuxiliaryController = createAgentPreviewSession();
      previewState.previewController = previewAuxiliaryController;
      previewState.previewTabsController = previewRuntime.createDocumentTabsController({
        shell: "#agentPreviewTabsShell",
        root: "#agentPreviewTabs",
        pinButton: "#pinAgentPreviewBtn",
        createDocument: request => ({
          label: String(request.title || request.label || request.path || request.key || "").split("/").pop() || window.PortalI18n.t("预览"),
          title: String(request.path || request.title || request.key || ""),
          path: String(request.path || ""),
          sourceType: String(request.sourceType || ""),
          residentId: String(request.residentId || ""),
          directoryId: String(request.directoryId || ""),
          relPath: String(request.relPath || ""),
          restoreLocator: previewPersistenceLocator(request),
        }),
        createSession: documentState => createAgentPreviewSession(documentState),
        serializeDocument: documentState => documentState.restoreLocator,
        restoreDocument: locator => restorePreviewDocumentRequest(locator),
        label: documentState => documentState.label,
        title: (documentState, snapshot) => (
          snapshot?.payload?.path
          || documentState.title
          || documentState.path
        ),
        onActivate: (documentState, snapshot) => activateAgentPreviewDocument(documentState, snapshot),
        onEmpty: () => resetAgentPreviewToEmpty(),
        onRender: snapshot => {
          const shell = $id("agentPreviewTabsShell");
          if (!snapshot.documents.length && shell) shell.hidden = true;
        },
        onError: error => window.PortalUI.toast(
          window.PortalI18n.text`Preview 操作失败：${error.message || error}`,
          "error",
        ),
      });
      previewState.previewTabsController.render();
      previewState.previewSelectionController?.destroy?.();
      previewState.previewSelectionController = previewRuntime.createPreviewSelectionController({
        root: "#previewBox",
        toolbar: "#previewSelectionToolbar",
        payload: () => activePreviewPayload(),
        path: payload => String(payload?.path || $id("previewPath")?.textContent || "").trim(),
        title: payload => previewTitleForPayload(payload || {}) || window.PortalI18n.t("预览"),
        chatWorkspace: () => currentAgentChatWorkspace(),
        selection: () => previewState.previewCodeEditor?.selection?.() || null,
        clearSelection: () => previewState.previewCodeEditor?.clearSelection?.(),
        disabled: payload => !payload,
        label: window.PortalI18n.t("添加到当前聊天"),
        ariaLabel: window.PortalI18n.t("把选中的文件内容添加到当前 Chat Workspace"),
        onAdd: (_selection, attachment) => window.PortalUI?.toast?.(
          `已添加 ${attachment?.name || "文件选段"} 到当前消息上下文`,
          "success",
        ),
      });
      applyPreviewWordWrap();
    }

    async function openAgentPreviewDocument(request = {}) {
      return await (previewState.previewTabsController
        ? previewState.previewTabsController.open(request)
        : previewState.previewController.open(request));
    }

    function previewDownloadUrl(payload = activePreviewPayload()) {
      return payload ? previewState.agentPreviewSource?.downloadUrl?.(payload) || "" : "";
    }

    return {
      activeAgentPreviewDocument, activePreviewPayload, activePreviewSnapshot, connectPreviewSurface,
      focusInitialPreviewLine, initPreviewController, openAgentPreviewDocument, previewDownloadUrl, previewIsEditable,
      resetAgentPreviewToEmpty,
    };
  }

  window.PortalLineageWorkbenchPreviewDocuments = Object.freeze({ createPreviewDocuments, dependencies: DEPENDENCIES });
})();
