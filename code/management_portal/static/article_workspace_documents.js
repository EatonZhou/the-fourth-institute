(() => {
  "use strict";

  if (window.PortalArticleWorkspaceDocuments) return;

  // The open documents: their tabs and sessions, activating one in the
  // editor, code and visual modes, linked files and resource previews, going
  // to a line, and saving one or every changed document.
  const DEPENDENCIES = Object.freeze([
    "$", "articleAbsolutePath", "articleModelUri", "closeArticleGitDiff", "detailResourceGuest",
    "editorValue", "isMarkdownPath", "languageForPath", "loadSemanticIndex", "monacoApi",
    "openArticleLinkedReference", "pdfUrl", "registerArticleLinkedFiles", "renderEditorLayers",
    "renderFileTree", "renderSemanticNavigation", "request", "scheduleArticleGitRefresh",
    "scheduleTexlabSync", "showToast", "state", "syncEditorScroll", "updateCursorStatus", "updateDirtyState",
  ]);

  function createDocuments(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalArticleWorkspaceDocuments is missing: ${missing.join(", ")}`);
    const {
      $, articleAbsolutePath, articleModelUri, closeArticleGitDiff, detailResourceGuest, editorValue,
      isMarkdownPath, languageForPath, loadSemanticIndex, monacoApi, openArticleLinkedReference, pdfUrl,
      registerArticleLinkedFiles, renderEditorLayers, renderFileTree, renderSemanticNavigation, request,
      scheduleArticleGitRefresh, scheduleTexlabSync, showToast, state, syncEditorScroll, updateCursorStatus,
      updateDirtyState,
    } = dependencies;

    let articleDocumentPreviewKey = "";

    function createArticleDocumentSession(payload) {
      const session = window.PortalDocumentSession.create({
        continuousEdit: true,
        canEdit: () => true,
      });
      session.present(payload);
      return session;
    }

    function activeDocument() {
      return state.openFile ? state.documents.get(state.openFile) || null : null;
    }

    function documentIsDirty(documentState) {
      if (!documentState?.editable) return false;
      return Boolean(documentState?.session?.snapshot?.().dirty
        ?? (documentState && documentState.source !== documentState.savedSource));
    }

    function hasDirtyDocuments() {
      return state.openTabs.some(path => documentIsDirty(state.documents.get(path)));
    }

    function captureActiveDocument() {
      const documentState = activeDocument();
      if (!documentState) return;
      if (!documentState.textDocument) return;
      const editor = $("articleSourceEditor");
      documentState.source = editorValue();
      if (documentState.editable) documentState.session?.updateDraft?.(documentState.source);
      documentState.savedSource = state.savedSource;
      if (state.monacoReady && state.editor) {
        documentState.viewState = state.editor.saveViewState();
        const selection = state.editor.getSelection();
        if (selection) {
          documentState.selection = {
            startLineNumber: selection.startLineNumber,
            startColumn: selection.startColumn,
            endLineNumber: selection.endLineNumber,
            endColumn: selection.endColumn,
          };
        }
        return;
      }
      documentState.scrollTop = editor.scrollTop;
      documentState.scrollLeft = editor.scrollLeft;
      documentState.selectionStart = editor.selectionStart;
      documentState.selectionEnd = editor.selectionEnd;
    }

    function renderDocumentTabs() {
      const host = $("articleDocumentTabs");
      const diffKey = "__article_git_diff__";
      const items = state.openTabs.map(path => {
        const documentState = state.documents.get(path);
        return {
          key: path,
          label: String(documentState?.name || path.split("/").at(-1) || path),
          title: String(documentState?.resourceAddress || path),
          active: !state.gitDiffPath && path === state.openFile,
          dirty: documentIsDirty(documentState),
        };
      });
      if (state.gitDiffPath) {
        const currentFile = state.gitDiffFiles[state.gitDiffActiveFile]?.path || state.gitDiffPath.replace(/^commit:/, "");
        items.push({
          key: diffKey,
          label: state.gitDiffPath.startsWith("commit:") ? state.gitDiffTitle : window.PortalI18n.text`${currentFile} · 工作树变更`,
          title: state.gitDiffTitle || currentFile,
          active: true,
          dirty: false,
        });
      }
      window.PortalWorkspaceSurface.renderDocumentTabs({
        root: host,
        emptyText: window.PortalI18n.t("Open a file from File tree"),
        items,
        onSelect: path => {
          if (path !== diffKey) activateDocument(path);
        },
        onClose: path => {
          if (path === diffKey) closeArticleGitDiff();
          else closeDocument(path);
        },
      });
      host.querySelector(`[data-portal-document-tab="${diffKey}"]`)?.classList.add("articleGitDocumentTab");
    }

    function disposeArticleDocumentPreview() {
      const root = $("articleDocumentPreview");
      window.PortalPreview.dispose(root);
      root.replaceChildren();
      articleDocumentPreviewKey = "";
    }

    function renderVisualDocument(documentState) {
      if (!documentState) return disposeArticleDocumentPreview();
      const key = JSON.stringify([documentState.path, documentState.previewDocument.version, documentState.source]);
      if (key === articleDocumentPreviewKey) return;
      const handle = window.PortalPreview.render({
        root: $("articleDocumentPreview"),
        document: { ...documentState.previewDocument, content: documentState.source },
        onPreviewRequest: openArticleLinkedReference,
        context: {
          renderMarkdown: (source, options) => window.MD.render(source, {
            ...options,
            articleMarkdown: !documentState.linked,
            basePath: documentState.resourceAddress || documentState.path,
          }),
          bindLinks: root => {
            if (documentState.resourceAddress) return;
            window.PortalFileLinks?.enhance?.(root, {
              basePath: articleAbsolutePath(documentState.path),
              source: "article-resource-detail",
              onOpen: openArticleLinkedReference,
              onDiscover: registerArticleLinkedFiles,
            });
          },
          onResourcePreview: openArticleResourcePreview,
        },
      });
      articleDocumentPreviewKey = key;
      handle.promise.catch(error => {
        if (articleDocumentPreviewKey === key) showToast(error.message || error, "error");
      });
    }

    function setEditorMode(mode) {
      const documentState = activeDocument();
      const textDocument = Boolean(documentState?.textDocument);
      const visualAvailable = Boolean(documentState);
      const visual = visualAvailable && (!textDocument || mode === "visual");
      if (documentState) documentState.viewMode = visual ? "visual" : "code";
      $("articleCodeMode").disabled = !textDocument;
      $("articleVisualMode").disabled = !visualAvailable;
      $("articleCodeMode").classList.toggle("active", Boolean(documentState) && !visual);
      $("articleVisualMode").classList.toggle("active", visual);
      $("articleCodeMode").setAttribute("aria-pressed", documentState && !visual ? "true" : "false");
      $("articleVisualMode").setAttribute("aria-pressed", visual ? "true" : "false");
      $("articleCodeEditor").classList.toggle("visualMode", visual);
      $("articleVisualEditor").hidden = !visual;
      if (visual) renderVisualDocument(documentState);
      else disposeArticleDocumentPreview();
      $("articleUndo").disabled = !documentState?.editable || visual;
      $("articleRedo").disabled = !documentState?.editable || visual;
      $("articleWordWrap").disabled = !textDocument || visual;
      if (!visual && state.monacoReady) window.requestAnimationFrame(() => state.editor?.layout?.());
    }

    function showEmptyEditor() {
      state.openFile = "";
      state.source = "";
      state.savedSource = "";
      const editor = $("articleSourceEditor");
      editor.value = "";
      editor.disabled = true;
      editor.scrollTop = 0;
      editor.scrollLeft = 0;
      $("articleSyntaxLayer").textContent = "";
      $("articleLineNumbers").textContent = "";
      if (state.monacoReady && state.editor) {
        state.editorSuppressChange = true;
        state.editor.setModel(null);
        state.editor.updateOptions({ readOnly: false });
        state.editorSuppressChange = false;
      }
      state.syntaxDiagnostics = [];
      $("articleSyntaxStatus").hidden = true;
      renderSemanticNavigation();
      $("articleEncodingStatus").textContent = window.PortalI18n.t("UTF-8 · LaTeX");
      $("articleSyncToPdf").disabled = true;
      $("articleSendContext").disabled = true;
      setEditorMode("code");
      renderDocumentTabs();
      renderFileTree();
      updateDirtyState();
      updateCursorStatus();
    }

    function activateDocument(path, options = {}) {
      const documentState = state.documents.get(path);
      if (!documentState) return;
      closeArticleGitDiff();
      if (options.captureCurrent !== false) captureActiveDocument();
      state.openFile = path;
      state.source = documentState.source;
      state.savedSource = documentState.savedSource;
      const editor = $("articleSourceEditor");
      const textDocument = documentState.textDocument;
      const readOnly = !documentState.editable;
      editor.disabled = readOnly;
      editor.value = state.source;
      if (state.monacoReady && state.editor) {
        if (textDocument && !documentState.model) {
          documentState.model = monacoApi().editor.createModel(
            documentState.source,
            languageForPath(documentState.languagePath || path),
            articleModelUri(path),
          );
        }
        state.editorSuppressChange = true;
        state.editor.setModel(textDocument ? documentState.model : null);
        state.editor.updateOptions({ readOnly });
        documentState.model?.updateOptions({
          tabSize: state.editorPreferences.tabSize,
          insertSpaces: true,
        });
        if (textDocument && documentState.viewState) state.editor.restoreViewState(documentState.viewState);
        state.editorSuppressChange = false;
        state.source = documentState.model?.getValue() || "";
        editor.value = state.source;
      } else if (textDocument) {
        const selectionStart = Math.max(0, Math.min(Number(documentState.selectionStart || 0), editor.value.length));
        const selectionEnd = Math.max(selectionStart, Math.min(Number(documentState.selectionEnd ?? selectionStart), editor.value.length));
        editor.setSelectionRange(selectionStart, selectionEnd);
        editor.scrollTop = Number(documentState.scrollTop || 0);
        editor.scrollLeft = state.wordWrap ? 0 : Number(documentState.scrollLeft || 0);
      }
      $("articleEncodingStatus").textContent = !textDocument
        ? window.PortalI18n.text`${documentState.kind.toUpperCase()} · 只读预览`
        : documentState.resourceAddress
          ? window.PortalI18n.text`Resource · ${documentState.kind === "markdown" ? "Markdown" : "Text"}`
          : window.PortalI18n.text`UTF-8 · ${path.toLowerCase().endsWith(".tex") ? "LaTeX" : (isMarkdownPath(path) ? "Markdown" : "Text")}`;
      $("articleSyncToPdf").disabled = !state.settings?.synctex_available || !path.toLowerCase().endsWith(".tex") || !pdfUrl();
      $("articleSendContext").disabled = false;
      renderDocumentTabs();
      renderFileTree();
      setEditorMode(documentState.viewMode);
      renderEditorLayers();
      renderSemanticNavigation();
      updateDirtyState();
      updateCursorStatus();
      if (options.line) goToLine(options.line, options.column || 1);
      else if (state.monacoReady) window.requestAnimationFrame(() => state.editor?.layout?.());
      else window.requestAnimationFrame(syncEditorScroll);
      scheduleTexlabSync(80);

    }

    async function closeDocument(path) {
      const documentState = state.documents.get(path);
      if (!documentState) return;
      captureActiveDocument();
      if (documentState.session && !await documentState.session.guardDiscard({
        confirmDiscard: () => window.PortalUI.confirm({
          title: window.PortalI18n.t("放弃未保存的修改？"),
          message: window.PortalI18n.text`“${path}”有未保存修改。关闭后将放弃这些修改。`,
          confirmLabel: window.PortalI18n.t("放弃修改"),
          tone: "danger",
        }),
      })) return;
      const index = state.openTabs.indexOf(path);
      state.openTabs = state.openTabs.filter(item => item !== path);
      documentState.model?.dispose?.();
      documentState.session?.destroy?.();
      state.documents.delete(path);
      if (state.openFile === path) {
        const next = state.openTabs[index] || state.openTabs[index - 1] || "";
        if (next) activateDocument(next);
        else showEmptyEditor();
      } else {
        renderDocumentTabs();
      }
    }

    function presentArticleDocument(target, payload = {}, options = {}) {
      if (state.documents.has(target)) {
        if (options.activate !== false) activateDocument(target, options);
        return state.documents.get(target);
      }
      const previewDocument = window.PortalPreview.normalizeDocument({ ...payload, path: target });
      const textDocument = window.PortalPreviewContract.isTextDocument(previewDocument);
      const kind = previewDocument.contentKind;
      const source = textDocument ? previewDocument.content : "";
      const linked = Boolean(options.linked);
      const editable = textDocument && !linked;
      const documentState = {
        path: target,
        name: String(options.name || payload.preview_title || payload.title || "").trim(),
        kind,
        linked,
        editable,
        textDocument,
        previewDocument,
        resourceAddress: String(options.resourceAddress || "").trim(),
        languagePath: String(options.languagePath || target),
        source,
        savedSource: source,
        revision: String(payload.revision || ""),
        modifiedAt: String(payload.modified_at || ""),
        version: 1,
        model: null,
        viewState: null,
        markerDecorations: [],
        scrollTop: 0,
        scrollLeft: 0,
        selectionStart: 0,
        selectionEnd: 0,
        viewMode: !textDocument || (linked && kind === "markdown") ? "visual" : "code",
      };
      if (editable) documentState.session = createArticleDocumentSession({ ...payload, content: source });
      state.documents.set(target, documentState);
      state.openTabs.push(target);
      if (options.activate !== false) activateDocument(target, options);
      else renderDocumentTabs();
      return documentState;
    }

    async function openArticleLinkedFile(file) {
      const target = String(file?.path || "").trim();
      if (!target || !window.PortalFileLinks) return null;
      if (target === state.openFile) return activeDocument();
      if (state.documents.has(target)) {
        activateDocument(target);
        return state.documents.get(target);
      }
      const articleId = state.selectedId;
      $("articleEditorState").textContent = window.PortalI18n.t("Opening linked file…");
      try {
        const payload = await window.PortalFileLinks.load(target);
        if (articleId !== state.selectedId) return null;
        return presentArticleDocument(target, payload, {
          linked: true,
          name: file.name,
        });
      } catch (error) {
        $("articleEditorState").textContent = window.PortalI18n.t("Unable to open linked file");
        showToast(error.message || error, "error");
        return null;
      }
    }

    async function openArticleResourcePreview(context = {}) {
      const address = String(
        window.PortalResourceChips?.resourceAddressFromPreviewRequest?.(context)
        || "",
      ).trim();
      if (!address) return false;
      const target = `resource:${address}`;
      if (state.documents.has(target)) {
        activateDocument(target);
        return true;
      }
      const articleId = state.selectedId;
      $("articleEditorState").textContent = window.PortalI18n.t("Opening Resource…");
      try {
        const payload = await (
          typeof context.load === "function"
            ? context.load()
            : window.PortalResourceChips.loadPreview(address)
        );
        if (articleId !== state.selectedId) return false;
        if (!payload) {
          if (typeof context.navigate === "function") context.navigate();
          else window.location.assign(
            context.href || `/r/${encodeURIComponent(address)}`,
          );
          return true;
        }
        if (!window.PortalPreviewContract.isTextDocument(payload)) {
          if (typeof context.navigate === "function") context.navigate();
          else window.location.assign(
            context.href || `/r/${encodeURIComponent(address)}`,
          );
          return true;
        }
        presentArticleDocument(target, payload, {
          linked: true,
          name: payload.preview_title || payload.title || address,
          resourceAddress: address,
          languagePath: String(payload.kind || "") === "markdown" ? `${address}.md` : address,
        });
        return true;
      } catch (error) {
        $("articleEditorState").textContent = window.PortalI18n.t("Unable to open Resource");
        showToast(window.PortalI18n.text`Resource 预览失败：${error.message || error}`, "error");
        return true;
      }
    }

    async function openArticleFile(path, options = {}) {
      const target = String(path || "");
      if (!target) return;
      if (target === state.openFile) {
        if (options.line) goToLine(options.line, options.column || 1);
        return activeDocument();
      }
      if (state.documents.has(target)) {
        if (options.activate !== false) activateDocument(target, options);
        return state.documents.get(target);
      }
      $("articleEditorState").textContent = window.PortalI18n.t("Opening…");
      const articleId = state.selectedId;
      try {
        const url = `/api/articles/${encodeURIComponent(articleId)}/file?path=${encodeURIComponent(target)}`;
        const metadata = await request(`${url}&meta=1`);
        if (articleId !== state.selectedId) return;
        let payload = metadata;
        if (window.PortalPreviewContract.isTextDocument(metadata)) {
          payload = { ...metadata, ...await request(url) };
        }
        if (articleId !== state.selectedId) return;
        if (state.documents.has(target)) {
          if (options.activate !== false) activateDocument(target, options);
          return state.documents.get(target);
        }
        return presentArticleDocument(target, payload, options);
      } catch (error) {
        $("articleEditorState").textContent = window.PortalI18n.t("Unable to open file");
        showToast(error.message || error, "error");
      }
    }

    function clearEditor() {
      for (const documentState of state.documents.values()) {
        documentState.model?.dispose?.();
        documentState.session?.destroy?.();
      }
      state.openTabs = [];
      state.documents.clear();
      showEmptyEditor();
    }

    function goToLine(line, column = 1) {
      if (state.monacoReady && state.editor) {
        const model = state.editor.getModel();
        if (!state.openFile || !model) return;
        const lineNumber = Math.max(1, Math.min(Number(line || 1), model.getLineCount()));
        const lineColumn = Math.max(1, Math.min(Number(column || 1), model.getLineMaxColumn(lineNumber)));
        state.editor.setPosition({ lineNumber, column: lineColumn });
        state.editor.setSelection({
          startLineNumber: lineNumber,
          startColumn: lineColumn,
          endLineNumber: lineNumber,
          endColumn: lineColumn,
        });
        state.editor.revealPositionInCenterIfOutsideViewport({ lineNumber, column: lineColumn });
        state.editor.focus();
        captureActiveDocument();
        updateCursorStatus();

        return;
      }
      const editor = $("articleSourceEditor");
      if (!state.openFile || editor.disabled) return;
      const lines = editor.value.split("\n");
      const lineNumber = Math.max(1, Math.min(Number(line || 1), lines.length));
      const start = lines.slice(0, lineNumber - 1).reduce((total, item) => total + item.length + 1, 0);
      const offset = Math.max(0, Math.min(Number(column || 1) - 1, lines[lineNumber - 1].length));
      editor.focus();
      editor.setSelectionRange(start + offset, start + offset);
      const lineHeight = parseFloat(getComputedStyle(editor).lineHeight) || 20;
      const scrollToTarget = () => {
        const syntaxLine = $("articleSyntaxLayer").querySelector(`[data-syntax-line="${lineNumber}"]`);
        const lineTop = syntaxLine ? syntaxLine.offsetTop : (lineNumber - 1) * lineHeight;
        editor.scrollTop = Math.max(0, lineTop - Math.min(3 * lineHeight, editor.clientHeight * .22));
        if (state.wordWrap) editor.scrollLeft = 0;
        syncEditorScroll();
        captureActiveDocument();
      };
      scrollToTarget();
      window.requestAnimationFrame(scrollToTarget);
      updateCursorStatus();
    }

    async function saveOpenFile({ silent = false } = {}) {
      if (!state.selectedId || !state.openFile) return false;
      if (!updateDirtyState()) return true;
      const documentState = activeDocument();
      if (!documentState?.session) return false;
      $("articleEditorState").textContent = window.PortalI18n.t("Saving…");
      const outcome = await documentState.session.save({
        save: async ({ draft, payload }) => {
          const result = await request(`/api/articles/${encodeURIComponent(state.selectedId)}/file`, {
            method: "POST",
            body: JSON.stringify({ path: state.openFile, content: draft }),
          });
          return { ...payload, ...result, content: draft };
        },
        onSaved: result => {
          const saved = documentState.session.snapshot().payload;
          state.source = saved.content;
          state.savedSource = saved.content;
          documentState.source = saved.content;
          documentState.savedSource = saved.content;
          documentState.revision = String(result.revision || documentState.revision || "");
          documentState.modifiedAt = String(result.modified_at || documentState.modifiedAt || "");
        updateDirtyState();
        scheduleTexlabSync(60);
        loadSemanticIndex().catch(() => {});
        scheduleArticleGitRefresh(0);
        detailResourceGuest.changed({ resourceId: state.selectedId, path: result.path || state.openFile });
        if (!silent) showToast(window.PortalI18n.text`${result.path} 已保存`, "success");
        },
        onError: error => window.PortalUI.toast(error.message || error, "error", {
          retry: () => saveOpenFile({ silent }),
        }),
      });
      updateDirtyState();
      return !!outcome?.payload;
    }

    async function saveAllDirtyDocuments({ silent = true } = {}) {
      captureActiveDocument();
      const dirtyPaths = state.openTabs.filter(path => documentIsDirty(state.documents.get(path)));
      if (!dirtyPaths.length) return true;
      $("articleSaveFile").disabled = true;
      $("articleEditorState").textContent = window.PortalI18n.t("Saving…");
      try {
        for (const path of dirtyPaths) {
          const documentState = state.documents.get(path);
          const outcome = await documentState.session.save({
            save: async ({ draft, payload }) => {
              const result = await request(`/api/articles/${encodeURIComponent(state.selectedId)}/file`, {
                method: "POST",
                body: JSON.stringify({ path, content: draft }),
              });
              return { ...payload, ...result, content: draft };
            },
          });
          if (!outcome?.payload) throw outcome?.error || new Error(`${path} 保存失败`);
          const result = outcome.payload;
          documentState.source = String(result.content || "");
          documentState.savedSource = documentState.source;
          documentState.revision = String(result.revision || documentState.revision || "");
          documentState.modifiedAt = String(result.modified_at || documentState.modifiedAt || "");
          if (path === state.openFile) state.savedSource = documentState.source;
        }
        updateDirtyState();
        scheduleTexlabSync(60);
        loadSemanticIndex().catch(() => {});
        scheduleArticleGitRefresh(0);
        detailResourceGuest.changed({ resourceId: state.selectedId, paths: dirtyPaths });
        if (!silent) showToast(window.PortalI18n.text`${dirtyPaths.length} 个文件已保存`, "success");
        return true;
      } catch (error) {
        updateDirtyState();
        showToast(error.message || error, "error");
        return false;
      }
    }

    return Object.freeze({
      activateDocument, activeDocument, captureActiveDocument, clearEditor, documentIsDirty, goToLine,
      hasDirtyDocuments, openArticleFile, openArticleLinkedFile, openArticleResourcePreview,
      renderDocumentTabs, saveAllDirtyDocuments, saveOpenFile, setEditorMode,
    });
  }

  window.PortalArticleWorkspaceDocuments = Object.freeze({ createDocuments, dependencies: DEPENDENCIES });
})();
