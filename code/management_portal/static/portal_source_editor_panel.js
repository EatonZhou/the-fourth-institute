(() => {
  "use strict";
  if (window.PortalSourceEditorPanel) return;

  const text = value => window.PortalI18n?.t(value) || value;
  const previewable = path => /\.(md|markdown|mdown|mdx|tex|ltx|latex)$/i.test(path);
  const element = (tag, className, content) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (content !== undefined) node.textContent = text(content);
    return node;
  };

  // The package owns document buffers, versions, view state and the editor DOM.
  // Hosts supply file I/O and consume document/selection events; they never
  // reach into Monaco or store a second editable copy of a document.
  function create(options = {}) {
    if (typeof options.readFile !== "function") throw new TypeError("source-editor requires readFile(path)");
    const root = options.root || document.createElement("section");
    root.classList.add("portalSourceEditor");
    const tabs = element("div", "portalSourceEditorTabs");
    tabs.setAttribute("role", "tablist");
    tabs.setAttribute("aria-label", text("已打开的文件"));
    const toolbar = element("div", "portalSourceEditorToolbar");
    toolbar.setAttribute("role", "toolbar");
    toolbar.setAttribute("aria-label", text("源码操作"));
    const name = element("span", "portalSourceEditorPath");
    const status = element("span", "portalSourceEditorStatus");
    status.setAttribute("role", "status");
    const button = (label, action, parent = toolbar) => {
      const node = element("button", "", label);
      node.type = "button";
      node.dataset.sourceAction = action;
      parent.append(node);
      return node;
    };
    toolbar.append(name);
    const saveButton = button("保存", "save");
    saveButton.title = text("保存（Ctrl+S / ⌘S）");
    const previewButton = button("预览", "preview");
    const chatButton = button("选区加入聊天", "chat");
    chatButton.title = text("选区加入聊天（鼠标选中后自动加入）");
    chatButton.setAttribute("aria-keyshortcuts", "Control+Alt+Enter Meta+Alt+Enter");
    const fileChatButton = button("文件加入聊天", "chat-file");
    fileChatButton.title = text("当前文件加入聊天（Ctrl+Alt+Enter / ⌘⌥Enter）");
    fileChatButton.setAttribute("aria-keyshortcuts", "Control+Alt+Enter Meta+Alt+Enter");
    fileChatButton.setAttribute("aria-keyshortcuts", "Control+Alt+Shift+Enter Meta+Alt+Shift+Enter");
    toolbar.append(status);
    const banner = element("div", "portalSourceEditorBanner");
    banner.setAttribute("role", "status");
    banner.hidden = true;
    const body = element("div", "portalSourceEditorBody");
    const empty = element("div", "portalSourceEditorEmpty", "从文件树打开文件，开始编辑。");
    body.append(empty);
    root.replaceChildren(tabs, toolbar, banner, body);

    const documents = new Map(), opening = new Map();
    let activePath = "", wantedPath = "", destroyed = false, refreshTask = null;
    let persistTimer = 0, closePath = "", comparison = null, comparisonId = 0, restoring = 0, panelError = "";
    const dirty = doc => !!doc && doc.content !== doc.baseContent;
    const active = () => documents.get(activePath);
    const selected = doc => {
      const selection = doc?.editor?.selection();
      return selection ? {
        start_line: selection.lineStart, start_column: selection.columnStart,
        end_line: selection.lineEnd, end_column: selection.columnEnd,
        text: selection.rawText ?? selection.text,
      } : null;
    };
    const describe = doc => doc ? {
      path: doc.path, content: doc.content, hash: doc.hash, dirty: dirty(doc),
      editable: doc.editable, language: window.PortalCodeEditor.languageForPath(doc.path),
      selection: selected(doc), conflict: !!doc.external, saving: !!doc.saving,
    } : null;
    const persist = () => {
      clearTimeout(persistTimer);
      if (restoring) return;
      persistTimer = setTimeout(() => { if (!destroyed) options.emit?.("persistence-change"); }, 200);
    };
    const changed = doc => {
      if (destroyed) return;
      render();
      options.onChange?.(describe(doc));
      options.emit?.("document-change", describe(doc));
      persist();
    };
    const fail = (doc, error) => {
      if (destroyed) return;
      if (doc) doc.error = String(error?.message || error);
      else panelError = String(error?.message || error);
      render();
      options.onError?.(error);
    };
    const run = task => { Promise.resolve().then(task).catch(error => fail(active(), error)); };

    function renderBanner() {
      const doc = active();
      banner.replaceChildren();
      banner.hidden = true;
      if (!doc) return;
      if (closePath === doc.path) {
        banner.hidden = false;
        banner.append(element("span", "", "文件尚未保存。"));
        button("保存并关闭", "save-close", banner).disabled = !doc.editable || !!doc.external || !!doc.saving;
        button("放弃改动并关闭", "discard-close", banner).disabled = !!doc.saving;
        button("取消", "cancel-close", banner);
      } else if (doc.external) {
        banner.hidden = false;
        banner.append(element("span", "", "文件已在外部更新；你的未保存内容已保留。"));
        button("比较改动", "compare", banner);
      } else if (doc.error) {
        banner.hidden = false;
        banner.append(element("span", "", doc.error));
      }
    }

    function render() {
      if (destroyed) return;
      const doc = active();
      for (const current of documents.values()) {
        current.tab.setAttribute("aria-selected", String(current.path === activePath));
        current.tab.tabIndex = current.path === activePath ? 0 : -1;
        current.tab.textContent = `${current.path.split("/").pop()}${dirty(current) ? " ●" : ""}${current.external ? " !" : ""}`;
        current.tab.title = current.path;
        current.pane.hidden = current.path !== activePath || !!comparison;
      }
      empty.hidden = !!doc || !!comparison;
      name.textContent = comparison?.path ? `${text("Git 改动")} · ${comparison.path}` : doc?.path || text("源码编辑器");
      name.title = comparison?.path || doc?.path || "";
      status.textContent = doc ? text(doc.saving ? "正在保存…" : doc.error ? "保存或加载失败" : !doc.editable ? "只读" : dirty(doc) ? "未保存" : "已保存") : panelError || text("未打开文件");
      saveButton.disabled = !!comparison || !doc?.editable || !dirty(doc) || !!doc?.saving || !!doc?.external;
      previewButton.hidden = !!comparison || !previewable(doc?.path || "");
      previewButton.disabled = !options.onPreview;
      chatButton.hidden = !options.onSelection;
      chatButton.disabled = !!comparison || !selected(doc);
      fileChatButton.hidden = !options.onFileReference;
      fileChatButton.disabled = !!comparison || !doc;
      renderBanner();
    }

    function closeComparison() {
      ++comparisonId;
      if (comparison) {
        comparison.editor?.dispose();
        comparison.codeEditor?.destroy();
        comparison.models?.forEach(model => model.dispose());
        comparison.root.remove();
        comparison = null;
      }
      render();
      active()?.editor?.layout();
    }

    function activate(path, focus = true) {
      const doc = documents.get(path);
      if (!doc || destroyed) return;
      closeComparison();
      closePath = "";
      activePath = path;
      wantedPath = path;
      panelError = "";
      render();
      doc.editor?.layout();
      if (focus) doc.editor?.focus();
      doc.tab.scrollIntoView({ block: "nearest", inline: "nearest" });
      changed(doc);
    }

    async function openFile(path, navigation = {}) {
      if (destroyed) return null;
      path = String(path || "").trim();
      if (!path) return null;
      if (navigation.background !== true) wantedPath = path;
      if (!documents.has(path) && !opening.has(path)) {
        const task = (async () => {
          const file = await options.readFile(path);
          if (destroyed) return null;
          if (!file || typeof file.content !== "string") throw new Error(text("该文件没有可编辑的文本内容。"));
          const doc = {
            path, content: file.content, baseContent: file.content, hash: file.hash,
            editable: file.editable === true && typeof options.saveFile === "function",
            external: null, error: "", saving: null, editor: null,
            pane: element("div", "portalSourceEditorDocument"),
            tabWrap: element("div", "portalSourceEditorTab"), tab: element("button"),
          };
          doc.pane.setAttribute("role", "tabpanel");
          doc.pane.setAttribute("aria-label", path);
          doc.tab.type = "button";
          doc.tab.setAttribute("role", "tab");
          doc.tab.dataset.sourcePath = path;
          doc.tabWrap.append(doc.tab);
          const closer = button("×", "close-tab", doc.tabWrap);
          closer.dataset.sourcePath = path;
          closer.setAttribute("aria-label", `${text("关闭")} ${path}`);
          documents.set(path, doc);
          tabs.append(doc.tabWrap);
          body.append(doc.pane);
          render();
          try {
            const monaco = await window.PortalCodeEditor.loadMonaco();
            doc.editor = await window.PortalCodeEditor.mount({
              root: doc.pane, value: doc.content, path, readOnly: !doc.editable, statusBar: true,
              ariaLabel: `${text("编辑")} ${path}`,
              onChange(value) {
                if (doc.updating || destroyed) return;
                doc.content = value;
                doc.error = "";
                changed(doc);
              },
              onSelectionChange() {
                if (doc.path === activePath) chatButton.disabled = !selected(doc);
                persist();
              },
              onSelectionCommit: options.onSelectionCommitted ? () => {
                if (!destroyed && doc.path === activePath && !comparison) {
                  run(() => addSelection(doc, options.onSelectionCommitted));
                }
              } : undefined,
              onSave: () => run(() => saveFile(doc.path)),
              actions: [
                ...(options.onSelection ? [{ id: "portal.source.addToChat", label: text("加入聊天"),
                  precondition: "editorHasSelection", contextMenuGroupId: "navigation", contextMenuOrder: 1,
                  run: () => addSelection(doc),
                }] : []),
                ...(options.onFileReference ? [{ id: "portal.source.addFileToChat", label: text("当前文件加入聊天"),
                  keybindings: [monaco.KeyMod.CtrlCmd | monaco.KeyMod.Alt | monaco.KeyCode.Enter],
                  precondition: "editorTextFocus", contextMenuGroupId: "navigation", contextMenuOrder: 1.1,
                  run: () => options.onFileReference({ ...describe(doc), selection: null }),
                }] : []),
                ...(options.onPreview && previewable(path) ? [{ id: "portal.source.preview", label: text("预览"),
                  contextMenuGroupId: "navigation", contextMenuOrder: 2, run: () => options.onPreview(describe(doc)),
                }] : []),
              ],
            });
            if (destroyed || !documents.has(path)) { doc.editor.destroy(); return null; }
            return doc;
          } catch (error) {
            documents.delete(path);
            doc.tabWrap.remove(); doc.pane.remove();
            throw error;
          }
        })().finally(() => { opening.delete(path); });
        opening.set(path, task);
      }
      const doc = opening.has(path) ? await opening.get(path) : documents.get(path);
      if (doc && navigation.background !== true && wantedPath === path && !destroyed) {
        activate(path, navigation.focus !== false);
        const line = Number(navigation.line || 0);
        const range = navigation.selection || (line > 0 ? { startLineNumber: line, endLineNumber: line,
          startColumn: Number(navigation.column) || 1, endColumn: Number(navigation.column) || 1 } : null);
        if (range) doc.editor.revealRange(range);
      }
      return describe(doc);
    }

    function addSelection(doc = active(), callback = options.onSelection) {
      const reference = describe(doc);
      if (!reference?.selection) return;
      // A reference carries the exact selected text, including whitespace, and
      // its base revision; the host attaches its resource identity.
      const { content, ...selectionReference } = reference;
      return callback?.(selectionReference);
    }

    function replaceContent(doc, value) {
      doc.content = value;
      doc.updating = true;
      try { doc.editor?.updateValue(value); } finally { doc.updating = false; }
    }

    async function refreshDocument(doc) {
      if (destroyed || doc.saving) return;
      const startingHash = doc.hash;
      const current = await options.readFile(doc.path);
      if (destroyed || documents.get(doc.path) !== doc || doc.saving || doc.hash !== startingHash) return;
      if (!current || typeof current.content !== "string") throw new Error(text("文件已不存在或无法作为文本读取。"));
      doc.editable = current.editable === true && typeof options.saveFile === "function";
      doc.editor?.setReadOnly(!doc.editable);
      doc.error = "";
      if (current.hash === doc.hash) {
        doc.external = null;
      } else if (dirty(doc) && current.content !== doc.content) {
        doc.external = current;
      } else {
        doc.baseContent = current.content;
        doc.hash = current.hash;
        doc.external = null;
        if (doc.content !== current.content) replaceContent(doc, current.content);
      }
      changed(doc);
    }

    async function refresh() {
      if (destroyed) return;
      if (refreshTask) return refreshTask;
      refreshTask = (async () => {
        for (const doc of documents.values()) {
          try { await refreshDocument(doc); } catch (error) { fail(doc, error); }
        }
      })().finally(() => { refreshTask = null; });
      return refreshTask;
    }

    async function saveFile(path = activePath) {
      if (typeof path !== "string") path = activePath;
      const doc = documents.get(path);
      if (destroyed || !doc) return null;
      if (doc.saving) {
        await doc.saving;
        return dirty(doc) ? saveFile(path) : describe(doc);
      }
      if (!dirty(doc)) return describe(doc);
      if (!doc.editable) throw new Error(text("该文件为只读，无法保存。"));
      if (doc.external) throw new Error(text("文件已在外部修改，请先比较改动。"));
      if (typeof doc.hash !== "string" || !doc.hash) throw new Error(text("无法确认文件版本，请重新打开文件后保存。"));
      const content = doc.content, expectedHash = doc.hash;
      doc.saving = Promise.resolve().then(async () => {
        try {
          const saved = await options.saveFile(path, content, expectedHash);
          if (destroyed || documents.get(path) !== doc) return;
          if (!saved?.hash) throw new Error(text("保存响应缺少文件版本，请刷新后检查。"));
          doc.hash = saved.hash;
          doc.baseContent = typeof saved.content === "string" ? saved.content : content;
          // Edits typed while the request is pending remain dirty against the
          // version that was actually saved.
          if (doc.content === content && doc.baseContent !== content) replaceContent(doc, doc.baseContent);
          doc.error = "";
          doc.external = null;
        } catch (error) {
          doc.error = String(error?.message || error);
          throw error;
        } finally {
          doc.saving = null;
          changed(doc);
        }
      });
      render();
      try { await doc.saving; }
      catch (error) {
        try { await refreshDocument(doc); } catch { /* Keep the failed save and local draft visible. */ }
        doc.error = String(error?.message || error);
        render();
        throw error;
      }
      return describe(doc);
    }

    async function saveAll() {
      for (const doc of documents.values()) {
        if (dirty(doc) || doc.saving) await saveFile(doc.path);
      }
      return !Array.from(documents.values()).some(dirty);
    }

    async function showDiff(path = activePath) {
      if (typeof path !== "string") path = activePath;
      const doc = documents.get(path);
      if (!doc?.external || destroyed) return false;
      activate(path, false);
      const id = ++comparisonId;
      const monaco = await window.PortalCodeEditor.loadMonaco();
      if (id !== comparisonId || !doc.external || destroyed) return false;
      const area = element("div", "portalSourceEditorComparison");
      const actions = element("div", "portalSourceEditorCompareActions");
      actions.append(element("span", "", "磁盘版本 ↔ 你的未保存版本"));
      button("使用磁盘版本", "use-disk", actions);
      button("保留我的版本并保存", "keep-local", actions).disabled = !doc.editable;
      button("返回编辑", "close-diff", actions);
      const viewport = element("div", "portalSourceEditorDiff");
      area.append(actions, viewport);
      body.append(area);
      const language = window.PortalCodeEditor.languageForPath(path);
      const models = [monaco.editor.createModel(doc.external.content, language), monaco.editor.createModel(doc.content, language)];
      const editor = monaco.editor.createDiffEditor(viewport, { automaticLayout: true, readOnly: true,
        originalEditable: false, renderSideBySide: true, minimap: { enabled: false }, scrollBeyondLastLine: false });
      editor.setModel({ original: models[0], modified: models[1] });
      comparison = { root: area, editor, models, doc, external: doc.external };
      render();
      return true;
    }

    async function resolveComparison(keepLocal) {
      const current = comparison;
      if (!current) return;
      const { doc, external } = current;
      // Compare-and-swap still applies if a further external edit arrives while
      // the user is reviewing the comparison.
      doc.hash = external.hash;
      doc.baseContent = external.content;
      doc.external = null;
      doc.error = "";
      if (!keepLocal) replaceContent(doc, external.content);
      closeComparison();
      changed(doc);
      if (keepLocal) await saveFile(doc.path);
      else await refreshDocument(doc);
    }

    async function showGitDiff(path, payload) {
      if (destroyed) return false;
      closeComparison();
      const id = ++comparisonId;
      const area = element("div", "portalSourceEditorComparison");
      const actions = element("div", "portalSourceEditorCompareActions");
      actions.append(element("span", "", `${text("Git 改动")} · ${path}`));
      button("返回编辑", "close-diff", actions);
      const viewport = element("div", "portalSourceEditorDocument");
      area.append(actions, viewport);
      body.append(area);
      const view = { root: area, path };
      comparison = view;
      render();
      try {
        const diff = typeof payload === "string" ? payload : String(payload?.diff || "");
        const codeEditor = await window.PortalCodeEditor.mount({ root: viewport, path,
          value: diff || text("该文件没有文本差异。"), language: "diff", readOnly: true, statusBar: true,
          ariaLabel: `${text("Git 改动")} ${path}` });
        if (destroyed || id !== comparisonId) { codeEditor.destroy(); return false; }
        view.codeEditor = codeEditor;
        codeEditor.layout();
        return true;
      } catch (error) {
        if (id === comparisonId) closeComparison();
        throw error;
      }
    }

    function removeDocument(path) {
      const doc = documents.get(path);
      if (!doc || doc.saving) return false;
      closeComparison();
      documents.delete(path);
      doc.editor?.destroy();
      doc.tabWrap.remove(); doc.pane.remove();
      closePath = "";
      if (activePath === path) {
        activePath = Array.from(documents.keys()).at(-1) || "";
        if (activePath) activate(activePath);
      }
      render(); persist();
      if (!activePath) options.onChange?.(null);
      return true;
    }

    function requestClose(path) {
      const doc = documents.get(path);
      if (!doc || doc.saving) return;
      if (!dirty(doc)) return removeDocument(path);
      activate(path);
      closePath = path;
      renderBanner();
    }

    const clicked = event => {
      const target = event.target.closest("button");
      if (!target || !root.contains(target)) return;
      const action = target.dataset.sourceAction;
      const doc = active();
      if (!action && target.dataset.sourcePath) activate(target.dataset.sourcePath);
      else if (action === "close-tab") requestClose(target.dataset.sourcePath);
      else if (action === "save") run(() => saveFile());
      else if (action === "preview") run(() => options.onPreview?.(describe(doc)));
      else if (action === "chat") run(() => addSelection());
      else if (action === "chat-file") run(() => options.onFileReference?.({ ...describe(active()), selection: null }));
      else if (action === "compare") run(() => showDiff());
      else if (action === "close-diff") closeComparison();
      else if (action === "use-disk") run(() => resolveComparison(false));
      else if (action === "keep-local") run(() => resolveComparison(true));
      else if (action === "cancel-close") { closePath = ""; render(); }
      else if (action === "discard-close") removeDocument(closePath);
      else if (action === "save-close") run(async () => {
        const path = closePath;
        await saveFile(path);
        if (!dirty(documents.get(path))) removeDocument(path);
      });
    };
    const keydown = event => {
      if (event.target.getAttribute("role") !== "tab") return;
      const paths = Array.from(documents.keys()), index = paths.indexOf(activePath);
      if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
        event.preventDefault();
        activate(paths[(index + (event.key === "ArrowRight" ? 1 : paths.length - 1)) % paths.length], false);
        active()?.tab.focus();
      } else if (event.key === "Delete") { event.preventDefault(); requestClose(activePath); }
    };
    root.addEventListener("click", clicked);
    tabs.addEventListener("keydown", keydown);
    render();

    function snapshot() {
      return {
        schemaVersion: 1, scopeKey: String(options.scopeKey || ""), activePath,
        documents: Array.from(documents.values(), doc => ({ path: doc.path,
          viewState: doc.editor?.viewState() || null,
          ...(dirty(doc) ? { draft: doc.content, baseContent: doc.baseContent, hash: doc.hash } : {}),
        })),
      };
    }

    async function restore(value) {
      if (!value || value.schemaVersion !== 1 || value.scopeKey !== String(options.scopeKey || "") || destroyed) return;
      restoring += 1;
      for (const saved of value.documents || []) {
        if (typeof saved?.path !== "string" || documents.has(saved.path) || destroyed) continue;
        try {
          await openFile(saved.path, { focus: false, background: true });
          const doc = documents.get(saved.path);
          if (!doc) continue;
          if (typeof saved.draft === "string") {
            const remote = { path: doc.path, content: doc.content, hash: doc.hash, editable: doc.editable };
            doc.baseContent = typeof saved.baseContent === "string" ? saved.baseContent : doc.baseContent;
            doc.hash = saved.hash;
            replaceContent(doc, saved.draft);
            if (remote.content === doc.content) { doc.baseContent = remote.content; doc.hash = remote.hash; }
            else if (remote.hash !== doc.hash) doc.external = remote;
          }
          doc.editor.restoreViewState(saved.viewState);
          changed(doc);
        } catch (error) { fail(null, error); }
      }
      restoring -= 1;
      if (!destroyed) {
        const path = documents.has(wantedPath) ? wantedPath : value.activePath;
        if (documents.has(path)) activate(path, false);
        persist();
      }
    }

    return Object.freeze({
      root, openFile, saveFile, saveAll, refresh, showDiff, showGitDiff, snapshot, restore,
      activeDocument: () => describe(active()),
      hasDirty: () => Array.from(documents.values()).some(dirty),
      canClose: () => !Array.from(documents.values()).some(doc => dirty(doc) || doc.saving),
      focus: () => active()?.editor?.focus(),
      show() { active()?.editor?.layout(); comparison?.editor?.layout(); comparison?.codeEditor?.layout(); },
      hide() {},
      destroy() {
        if (destroyed) return;
        destroyed = true;
        clearTimeout(persistTimer);
        closeComparison();
        for (const doc of documents.values()) doc.editor?.destroy();
        documents.clear();
        root.removeEventListener("click", clicked);
        tabs.removeEventListener("keydown", keydown);
        root.replaceChildren();
        root.classList.remove("portalSourceEditor");
      },
    });
  }

  function register() {
    if (window.PortalWorkbench.registeredPanelPackages().some(pkg => pkg.type === "source-editor")) return;
    window.PortalWorkbench.registerPanelPackage({
      type: "source-editor", version: 1, component: "PortalSourceEditorPanel",
      presentation: { title: text("源码编辑器"), icon: "code" },
      sizing: { minWidth: 260, minHeight: 200 }, instances: "multiple",
      capabilities: ["editing", "snapshot", "restore"],
      behavior: { canHide: true, canCollapse: true, canMove: true, closable: false },
      context: { channels: [] }, persistence: { schemaVersion: 1 }, create,
    });
  }
  window.PortalSourceEditorPanel = Object.freeze({ create, register });
  if (window.PortalWorkbench) register();
})();
