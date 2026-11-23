(() => {
  "use strict";

  if (window.PortalArticleWorkspaceMonaco) return;

  // The Monaco editor of the open document: the LaTeX and BibTeX languages,
  // the themes, editor options and models, and reading the value, caret and
  // selection whether Monaco or the plain textarea is in use.
  const DEPENDENCIES = Object.freeze([
    "activateDocument", "activeDocument", "captureActiveDocument", "registerTexlabProviders", "onSelectionCommitted",
    "renderEditorLayers", "saveOpenFile", "scheduleAutoCompile", "scheduleAutoSave", "scheduleSemanticReload",
    "scheduleTexlabSync", "getSession", "updateCursorStatus", "updateDirtyState",
  ]);

  function createMonaco(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalArticleWorkspaceMonaco is missing: ${missing.join(", ")}`);
    const {
      activateDocument, activeDocument, captureActiveDocument, registerTexlabProviders, renderEditorLayers, onSelectionCommitted,
      saveOpenFile, scheduleAutoCompile, scheduleAutoSave, scheduleSemanticReload, scheduleTexlabSync, getSession,
      updateCursorStatus, updateDirtyState,
    } = dependencies;

    const root = document.getElementById("articleEditorTemplate").content.firstElementChild.cloneNode(true);
    const $ = id => root.id === id ? root : root.querySelector("#" + id);
    let editor = null, ready = false, suppressChange = false, destroyed = false;
    let layoutFrame = 0, observer = null, pendingView = null, resizing = false;
    const disposables = [];
    const events = new AbortController();
    const listen = (node, type, handler, options = {}) => node.addEventListener(type, handler, { ...options, signal: events.signal });
    function layout() {
      window.cancelAnimationFrame(layoutFrame);
      layoutFrame = window.requestAnimationFrame(() => { layoutFrame = 0; if (!destroyed) editor?.layout?.(); });
    }
    function connect() {
      if (destroyed || observer) return;
      observer = new ResizeObserver(() => { if (!resizing) layout(); });
      observer.observe(root);
      root.addEventListener("portal:workbench-resize", onResize);
    }
    function onResize(event) { resizing = event.detail?.reason === "resize-live"; if (!resizing) layout(); }
    function disconnect() {
      observer?.disconnect(); observer = null;
      root.removeEventListener("portal:workbench-resize", onResize);
      window.cancelAnimationFrame(layoutFrame);
    }
    function restoreViewFor(path) {
      if (pendingView?.articleId !== getSession().articleId || pendingView.path !== path) return;
      if (pendingView.viewState && editor) editor.restoreViewState(pendingView.viewState);
      pendingView = null;
    }

    let dismissConflict = null;
    async function confirmConflict({ path, local, remote }) {
      dismissConflict?.();
      if (destroyed) return false;
      const overlay = document.createElement("div");
      overlay.className = "portalDetailOverlay articleConflictDialog";
      overlay.hidden = true;
      overlay.innerHTML = `<section class="portalDetailSurface" role="dialog" aria-modal="true" aria-label="文件保存冲突">
        <div class="portalDetailHeader"><h2></h2></div>
        <div class="articleConflictLabels"><span>磁盘最新内容</span><span>本地草稿</span></div>
        <div class="articleConflictComparison"></div>
        <div class="portalDetailFooter"><button type="button" class="portalButton" data-conflict-cancel>取消，保留草稿</button>
        <button type="button" class="portalButton is-primary" data-conflict-save>以此草稿保存</button></div>
      </section>`;
      overlay.querySelector("h2").textContent = `${path} · 文件已在其他地方更新`;
      document.body.append(overlay);
      const host = overlay.querySelector(".articleConflictComparison");
      let diff = null, models = [], settled = false, resolve;
      const decision = new Promise(done => { resolve = done; });
      const finish = accepted => {
        if (settled) return;
        settled = true;
        dialog.close({ force: true }); dialog.destroy();
        diff?.dispose(); models.forEach(model => model.dispose());
        overlay.remove(); dismissConflict = null; resolve(accepted);
      };
      const dialog = window.PortalUI.createDialogController({
        modal: overlay, openClass: "portalDialogOpen", bodyClass: null,
        backdropClose: true, escapeClose: true, onClose: () => finish(false),
      });
      dismissConflict = () => finish(false);
      overlay.querySelector("[data-conflict-cancel]").onclick = () => finish(false);
      overlay.querySelector("[data-conflict-save]").onclick = () => finish(true);
      dialog.open({ focusTarget: overlay.querySelector("[data-conflict-cancel]") });
      // The comparison uses the same Monaco diff engine as Article Git.
      // Basic-editor mode still presents both complete values for review.
      if (window.monaco) {
        diff = window.monaco.editor.createDiffEditor(host, { readOnly: true, automaticLayout: true,
          renderSideBySide: true, originalEditable: false, minimap: { enabled: false } });
        models = [remote, local].map(value => window.monaco.editor.createModel(value, languageForPath(path)));
        diff.setModel({ original: models[0], modified: models[1] });
      } else {
        host.classList.add("articleConflictPlain");
        for (const content of [remote, local]) { const pre = document.createElement("pre"); pre.textContent = content; host.append(pre); }
      }
      return decision;
    }

    function monacoApi() {
      return window.monaco || null;
    }

    function documentForModel(model) {
      if (!model) return null;
      for (const documentState of getSession().documents.values()) {
        if (documentState.model === model) return documentState;
      }
      return null;
    }

    function activeEditorModel() {
      return editor?.getModel?.() || null;
    }

    function editorValue() {
      if (ready && editor) return editor.getValue();
      return $("articleSourceEditor").value;
    }

    function editorPosition() {
      if (ready && editor) {
        const position = editor.getPosition() || { lineNumber: 1, column: 1 };
        return { line: position.lineNumber, column: position.column };
      }
      const textarea = $("articleSourceEditor");
      const before = textarea.value.slice(0, textarea.selectionStart);
      const lines = before.split("\n");
      return { line: lines.length, column: lines.at(-1).length + 1 };
    }

    function editorSelectionContext(limit = 16000) {
      if (ready && editor) {
        const model = editor.getModel();
        const selection = editor.getSelection();
        if (!model || !selection) return null;
        const value = model.getValueInRange(selection);
        return {
          start_line: selection.startLineNumber,
          start_column: selection.startColumn,
          end_line: selection.endLineNumber,
          end_column: selection.endColumn,
          text: value.slice(0, limit),
          truncated: value.length > limit,
        };
      }
      const textarea = $("articleSourceEditor");
      const startText = textarea.value.slice(0, textarea.selectionStart);
      const endText = textarea.value.slice(0, textarea.selectionEnd);
      const startLines = startText.split("\n");
      const endLines = endText.split("\n");
      const value = textarea.value.slice(textarea.selectionStart, textarea.selectionEnd);
      return {
        start_line: startLines.length,
        start_column: startLines.at(-1).length + 1,
        end_line: endLines.length,
        end_column: endLines.at(-1).length + 1,
        text: value.slice(0, limit),
        truncated: value.length > limit,
      };
    }

    function articleModelUri(path) {
      const monaco = monacoApi();
      const segments = String(path || "").split("/").map(encodeURIComponent).join("/");
      return monaco.Uri.parse(`article://${encodeURIComponent(getSession().articleId || "none")}/${segments}`);
    }

    function languageForPath(path) {
      const suffix = String(path || "").toLowerCase().split(".").at(-1);
      if (suffix === "tex" || suffix === "sty" || suffix === "cls") return "latex";
      if (suffix === "bib") return "bibtex";
      if (suffix === "md" || suffix === "markdown") return "markdown";
      if (suffix === "json") return "json";
      if (suffix === "yaml" || suffix === "yml") return "yaml";
      if (suffix === "py") return "python";
      return "plaintext";
    }

    function isMarkdownPath(path) {
      return /\.(?:md|markdown)$/i.test(String(path || ""));
    }

    function defineMonacoLanguages(monaco) {
      const registered = new Set(monaco.languages.getLanguages().map(item => item.id));
      if (!registered.has("latex")) {
        monaco.languages.register({
          id: "latex",
          aliases: ["LaTeX", "TeX"],
          extensions: [".tex", ".sty", ".cls"],
          mimetypes: ["text/x-latex", "text/x-tex"],
        });
      }
      if (!registered.has("bibtex")) {
        monaco.languages.register({
          id: "bibtex",
          aliases: ["BibTeX"],
          extensions: [".bib"],
          mimetypes: ["text/x-bibtex"],
        });
      }
      const latexTokenizer = {
        defaultToken: "",
        tokenPostfix: ".latex",
        tokenizer: {
          root: [
            [/%.*$/, "comment"],
            [/\\(?:begin|end)(?=\s*\{)/, "keyword.control"],
            [/\\[A-Za-z@]+\*?/, "keyword"],
            [/\\./, "keyword"],
            [/\$+/, { token: "string.math", next: "@math" }],
            [/[{}[\]()]/, "@brackets"],
            [/[&_^]/, "operator"],
            [/\d+(?:\.\d+)?/, "number"],
          ],
          math: [
            [/\\[A-Za-z@]+\*?/, "keyword"],
            [/[{}[\]()]/, "@brackets"],
            [/\$+/, { token: "string.math", next: "@pop" }],
            [/[^$\\{}[\]()]+/, "string.math"],
          ],
        },
      };
      monaco.languages.setMonarchTokensProvider("latex", latexTokenizer);
      monaco.languages.setMonarchTokensProvider("bibtex", {
        defaultToken: "",
        tokenPostfix: ".bibtex",
        tokenizer: {
          root: [
            [/%.*$/, "comment"],
            [/@[A-Za-z]+(?=\s*\{)/, "keyword"],
            [/[A-Za-z][\w-]*(?=\s*=)/, "attribute.name"],
            [/"(?:[^"\\]|\\.)*"/, "string"],
            [/[{}(),=]/, "delimiter"],
            [/\d+/, "number"],
          ],
        },
      });
      monaco.languages.setLanguageConfiguration("latex", {
        comments: { lineComment: "%" },
        brackets: [["{", "}"], ["[", "]"], ["(", ")"]],
        autoClosingPairs: [
          { open: "{", close: "}" },
          { open: "[", close: "]" },
          { open: "(", close: ")" },
          { open: "$", close: "$", notIn: ["comment"] },
        ],
        surroundingPairs: [
          { open: "{", close: "}" },
          { open: "[", close: "]" },
          { open: "(", close: ")" },
          { open: "$", close: "$" },
        ],
        indentationRules: {
          increaseIndentPattern: /\\begin\s*\{[^}]+\}|(?:\{|\[|\()\s*$/,
          decreaseIndentPattern: /^\s*(?:\\end\s*\{[^}]+\}|\}|\]|\))/,
        },
        folding: {
          markers: {
            start: /^\s*%+\s*#?region\b/,
            end: /^\s*%+\s*#?endregion\b/,
          },
        },
      });
    }

    function defineMonacoThemes(monaco) {
      monaco.editor.defineTheme("article-light", {
        base: "vs",
        inherit: true,
        rules: [
          { token: "comment", foreground: "4B956F", fontStyle: "italic" },
          { token: "keyword", foreground: "245FC7" },
          { token: "keyword.control", foreground: "7D3FB2", fontStyle: "bold" },
          { token: "string.math", foreground: "A45121" },
          { token: "attribute.name", foreground: "7D3FB2" },
        ],
        colors: {
          "editor.background": "#FFFFFF",
          "editor.foreground": "#19202B",
          "editorLineNumber.foreground": "#98A1AE",
          "editorLineNumber.activeForeground": "#445164",
          "editorGutter.background": "#F8F9FB",
          "editor.lineHighlightBackground": "#F5F8FD",
          "editor.selectionBackground": "#B9D5FF80",
          "editor.inactiveSelectionBackground": "#D5E4FA72",
          "editorIndentGuide.background1": "#E8EBF0",
          "editorIndentGuide.activeBackground1": "#BEC7D4",
          "editorOverviewRuler.border": "#E5E8ED",
        },
      });
      monaco.editor.defineTheme("article-dark", {
        base: "vs-dark",
        inherit: true,
        rules: [
          { token: "comment", foreground: "72C596", fontStyle: "italic" },
          { token: "keyword", foreground: "82ADFF" },
          { token: "keyword.control", foreground: "C69AF0", fontStyle: "bold" },
          { token: "string.math", foreground: "F2A66F" },
          { token: "attribute.name", foreground: "C69AF0" },
        ],
        colors: {
          "editor.background": "#161A21",
          "editor.foreground": "#E7EBF0",
          "editorLineNumber.foreground": "#687386",
          "editorLineNumber.activeForeground": "#C1CAD6",
          "editorGutter.background": "#13171D",
          "editor.lineHighlightBackground": "#202630",
          "editor.selectionBackground": "#335E9B92",
          "editor.inactiveSelectionBackground": "#2E466B70",
          "editorIndentGuide.background1": "#2B323C",
          "editorIndentGuide.activeBackground1": "#566275",
          "editorOverviewRuler.border": "#252B35",
        },
      });
    }

    function applyMonacoTheme() {
      const monaco = monacoApi();
      if (!monaco || !ready) return;
      const selected = document.documentElement.dataset.portalTheme;
      const dark = selected === "dark" || selected === "scifi";
      monaco.editor.setTheme(dark ? "article-dark" : "article-light");
    }

    function monacoEditorOptions() {
      return {
        automaticLayout: true,
        model: null,
        theme: "article-light",
        fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
        fontSize: getSession().preferences.fontSize,
        lineHeight: Math.round(getSession().preferences.fontSize * 1.68),
        tabSize: getSession().preferences.tabSize,
        insertSpaces: true,
        detectIndentation: false,
        wordWrap: getSession().wordWrap ? "on" : "off",
        wrappingStrategy: "advanced",
        wrappingIndent: "same",
        minimap: { enabled: false },
        glyphMargin: true,
        folding: true,
        lineNumbersMinChars: 2,
        lineDecorationsWidth: 0,
        scrollBeyondLastLine: false,
        smoothScrolling: true,
        cursorBlinking: getSession().preferences.solidCursor ? "solid" : "blink",
        cursorSmoothCaretAnimation: "on",
        bracketPairColorization: { enabled: true },
        guides: { bracketPairs: true, indentation: true },
        renderWhitespace: "selection",
        renderControlCharacters: false,
        padding: { top: 12, bottom: 42 },
        fixedOverflowWidgets: true,
        contextmenu: true,
        quickSuggestions: { other: true, comments: false, strings: false },
        suggestOnTriggerCharacters: true,
        autoClosingBrackets: getSession().preferences.autoCloseBrackets ? "languageDefined" : "never",
        autoClosingQuotes: getSession().preferences.autoCloseBrackets ? "languageDefined" : "never",
        ariaLabel: window.PortalI18n.t("LaTeX source editor"),
      };
    }

    function initializeMonacoEditor() {
      return new Promise(resolve => {
        const loader = window.PortalCodeEditor?.loadMonaco;
        let settled = false;
        const finish = value => {
          if (settled) return;
          settled = true;
          window.clearTimeout(timeout);
          resolve(value);
        };
        const timeout = window.setTimeout(() => {
          $("articleLanguageStatus").textContent = window.PortalI18n.t("基础编辑器 · Monaco 加载超时");
          $("articleLanguageStatus").classList.add("warning");
          finish(false);
        }, 20_000);
        if (typeof loader !== "function") {
          $("articleLanguageStatus").textContent = window.PortalI18n.t("基础编辑器 · Monaco 未加载");
          $("articleLanguageStatus").classList.add("warning");
          finish(false);
          return;
        }
        loader().then(() => {
          try {
            if (destroyed) { finish(false); return; }
            const monaco = monacoApi();
            if (!monaco) {
              finish(false);
              return;
            }
            defineMonacoLanguages(monaco);
            defineMonacoThemes(monaco);
            captureActiveDocument();
            editor = monaco.editor.create($("articleMonacoEditor"), monacoEditorOptions());
            disposables.push(window.PortalCodeEditor.bindSelectionCommit($("articleMonacoEditor"), {
              editor: editor,
              readSelection: () => {
                const selection = editorSelectionContext(Infinity);
                return selection?.text ? selection : null;
              },
              onCommit: onSelectionCommitted,
            }));
            ready = true;
            $("articleCodeEditor").classList.add("monacoReady");
            applyMonacoTheme();
            registerTexlabProviders(monaco);
            disposables.push(editor.onDidChangeModelContent(() => {
              if (suppressChange || !getSession().path) return;
              if (!activeDocument()?.editable) return;
              const documentState = activeDocument();
              if (documentState) {
                documentState.source = editor.getValue();
                documentState.version = editor.getModel()?.getVersionId?.() || (documentState.version + 1);
              }
              $("articleSourceEditor").value = documentState.source;
              renderEditorLayers();
              updateDirtyState();
              updateCursorStatus();
              scheduleTexlabSync();
              scheduleAutoSave();
              scheduleAutoCompile();
              scheduleSemanticReload();

            }));
            disposables.push(editor.onDidChangeCursorPosition(() => {
              updateCursorStatus();
              captureActiveDocument();

            }));
            disposables.push(editor.onDidChangeCursorSelection(() => {
              updateCursorStatus();
              captureActiveDocument();

            }));
            disposables.push(editor.onDidScrollChange(() => captureActiveDocument()));
            disposables.push(editor.addAction({ id: "article.save", label: "保存", keybindings: [monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS], run: () => saveOpenFile() }));
            const themeObserver = new MutationObserver(applyMonacoTheme);
            themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ["data-portal-theme"] });
            disposables.push({ dispose: () => themeObserver.disconnect() });
            // Files may finish loading while Monaco is still loading. Preserve
            // edits made in the basic editor and attach that document now.
            if (getSession().path) activateDocument(getSession().path, { captureCurrent: false });
            $("articleLanguageStatus").textContent = window.PortalI18n.t("Monaco · TexLab 连接中");
            finish(true);
          } catch (error) {
            console.error(window.PortalI18n.t("Article Monaco initialization failed"), error);
            $("articleLanguageStatus").textContent = window.PortalI18n.t("基础编辑器 · Monaco 初始化失败");
            $("articleLanguageStatus").classList.add("warning");
            finish(false);
          }
        }, () => {
          $("articleLanguageStatus").textContent = window.PortalI18n.t("基础编辑器 · Monaco 加载失败");
          $("articleLanguageStatus").classList.add("warning");
          finish(false);
        });
      });
    }

    const { handleEditorPairing, syncEditorScroll, setEditorMode, toggleWordWrap, syncSourceToPdf,
      bringCurrentFileToChat, showToast } = dependencies;
    listen($("articleSourceEditor"), "input", event => {
      if (!activeDocument()?.editable) return;
      activeDocument().source = event.currentTarget.value;
      renderEditorLayers();
      updateDirtyState();
      updateCursorStatus();
      scheduleAutoSave();
      scheduleAutoCompile();
    });
    listen($("articleSourceEditor"), "scroll", () => {
      syncEditorScroll();
      captureActiveDocument();
    });
    ["click", "keyup", "select"].forEach(name => listen($("articleSourceEditor"), name, () => {
      updateCursorStatus();
      captureActiveDocument();
    }));
    listen($("articleSourceEditor"), "keydown", event => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
        event.preventDefault();
        saveOpenFile();
        return;
      }
      if (handleEditorPairing(event)) return;
      if (event.key === "Tab") {
        event.preventDefault();
        const editor = event.currentTarget;
        const start = editor.selectionStart;
        const end = editor.selectionEnd;
        editor.setRangeText(" ".repeat(getSession().preferences.tabSize), start, end, "end");
        editor.dispatchEvent(new Event("input", { bubbles: true }));
      }
    });
    listen($("articleSaveFile"), "click", () => saveOpenFile());
    listen($("articleCodeMode"), "click", () => setEditorMode("code"));
    listen($("articleVisualMode"), "click", () => {
      captureActiveDocument();
      setEditorMode("visual");
    });
    listen($("articleUndo"), "click", () => {
      if (ready) editor?.trigger?.("article-toolbar", "undo", null);
      else {
        $("articleSourceEditor").focus();
        document.execCommand("undo");
      }
    });
    listen($("articleRedo"), "click", () => {
      if (ready) editor?.trigger?.("article-toolbar", "redo", null);
      else {
        $("articleSourceEditor").focus();
        document.execCommand("redo");
      }
    });
    listen($("articleWordWrap"), "click", toggleWordWrap);
    listen($("articleSyncToPdf"), "click", syncSourceToPdf);
    const bringFileToChat = () => bringCurrentFileToChat()
      .catch(error => showToast(error.message || error, "error"));
    listen($("articleSendContext"), "click", bringFileToChat);
    disposables.push(window.PortalCodeEditor.bindSelectionCommit($("articleSourceEditor"), {
      readSelection: () => {
        const source = $("articleSourceEditor");
        return !ready && source.value.slice(source.selectionStart, source.selectionEnd);
      },
      onCommit: onSelectionCommitted,
    }));

    disposables.push(window.PortalCommandRegistry.root.register({
      id: "article.fileToChat",
      title: "当前文件加入聊天",
      keybindings: ["Ctrl+Alt+Enter", "Meta+Alt+Enter"],
      allowInInput: true,
      enabled: () => Boolean(getSession().articleId && !$("articleSendContext").disabled
        && (document.activeElement === $("articleSourceEditor") || editor?.hasTextFocus?.())),
      execute: ({ event }) => event?.repeat ? false : bringFileToChat(),
    }));

    return Object.freeze({
      root, connect, disconnect, save: saveOpenFile, confirmConflict,
      getEditor: () => editor, isReady: () => ready,
      setChangeSuppressed: value => { suppressChange = Boolean(value); },
      addDisposables: (...items) => disposables.push(...items),
      restoreViewFor,
      show: layout,
      hide() { captureActiveDocument(); window.cancelAnimationFrame(layoutFrame); },
      focus() { if (editor) editor.focus(); else $("articleSourceEditor").focus(); },
      snapshot() { captureActiveDocument(); return { articleId: getSession().articleId, path: getSession().path, viewState: editor?.saveViewState?.() || null }; },
      restore(value) { pendingView = value; },
      destroy() {
        if (destroyed) return;
        captureActiveDocument(); destroyed = true; dismissConflict?.(); disconnect(); events.abort();
        disposables.splice(0).forEach(item => typeof item === "function" ? item() : item?.dispose?.());
        editor?.dispose(); editor = null; ready = false;
        dependencies.destroyDocuments?.(); root.remove();
      },
      activeEditorModel, articleModelUri, documentForModel, editorPosition, editorSelectionContext,
      editorValue, initializeMonacoEditor, isMarkdownPath, languageForPath, monacoApi,
    });
  }

  function register() {
    if (window.PortalWorkbench.registeredPanelPackages().some(pkg => pkg.type === "article-editor")) return;
    window.PortalWorkbench.registerPanelPackage({
      type: "article-editor", version: 1, component: "PortalArticleWorkspaceMonaco",
      presentation: { title: "源码", icon: "code" }, sizing: { minWidth: 300, minHeight: 180 },
      instances: "single", capabilities: ["editing"],
      behavior: { canHide: true, canCollapse: true, canMove: true, closable: false },
      context: { channels: [] }, persistence: { schemaVersion: 1 },
      commands: [{ id: "save", title: "保存", execute: ({ panel }) => panel.save() }],
      create: createMonaco,
    });
  }
  window.PortalArticleWorkspaceMonaco = Object.freeze({ createMonaco, register, dependencies: DEPENDENCIES });
})();
