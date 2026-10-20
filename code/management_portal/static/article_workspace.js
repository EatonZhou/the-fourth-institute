(function () {
  "use strict";

  const $ = id => document.getElementById(id);
  const esc = value => window.PortalUIPrimitives.escapeHtml(value);
  const articleStateHtml = (message, options = {}) => window.PortalUIPrimitives.asyncStateHtml({
    state: options.state || "empty",
    title: options.title || "",
    message,
    density: options.density || "compact",
    icon: options.icon,
  });
  const queryState = () => new URLSearchParams(window.location.search);
  const articleRegisterDialog = window.PortalUI.createDialogController({
    modal: $("articleRegisterModal"),
    openClass: "portalDialogOpen",
    bodyClass: null,
    backdropClose: true,
    escapeClose: true,
    onOpen: () => { $("articleRegisterModal").hidden = false; },
    onClose: () => { $("articleRegisterModal").hidden = true; },
  });
  const articleEntryDialog = window.PortalUI.createDialogController({
    modal: $("articleEntryModal"),
    openClass: "portalDialogOpen",
    bodyClass: null,
    backdropClose: true,
    escapeClose: true,
    onOpen: () => { $("articleEntryModal").hidden = false; },
    onClose: () => { $("articleEntryModal").hidden = true; },
  });
  const embedded = queryState().get("embedded") === "1";
  const EDITOR_PREFERENCE_KEY = "portal.articleWorkspace.editorPreferences";
  const ARTICLE_GIT_REFRESH_MS = 5000;
  const articleLayoutStore = window.PortalWorkbench.layout.createStore({
    storageKey: "portal.articleWorkspace.layout",
  });
  let articleWorkbench = null;
  const WORD_WRAP_STORAGE_KEY = "portal.articleWorkspace.wordWrap";
  const EDITOR_PREFERENCE_DEFAULTS = Object.freeze({
    autoCloseBrackets: true,
    solidCursor: false,
    fontSize: 12,
    tabSize: 2,
  });
  const state = {
    articles: [],
    selectedId: "",
    sideView: "files",
    gitStatus: null,
    gitHistory: [],
    gitDiffPath: "",
    gitDiffTitle: "",
    gitDiffEditor: null,
    gitDiffModels: [],
    gitDiffFiles: [],
    gitDiffActiveFile: 0,
    gitDiffSideBySide: true,
    files: [],
    filesByPath: new Map(),
    linkedFiles: new Map(),
    linkedFileProbes: new Set(),
    collapsedDirectories: new Set(),
    selectedDirectory: "",
    openFile: "",
    openTabs: [],
    documents: new Map(),
    source: "",
    savedSource: "",
    editor: null,
    monacoReady: false,
    editorSuppressChange: false,
    monacoDisposables: [],
    renderFrame: 0,
    outlineTimer: 0,
    semanticTimer: 0,
    semanticIndex: null,
    semanticView: "outline",
    settings: null,
    build: null,
    buildLoading: false,
    logFilter: "all",
    outputView: "pdf",
    pdfSyncLocation: null,
    pdfSyncPending: false,
    pdfZoom: 1,
    pdfScaleVersion: 0,
    pdfLayoutFrame: 0,
    editorLayoutFrame: 0,
    pdfRedrawTimer: 0,
    pdfGestureCommitTimer: 0,
    pdfGestureAnchor: null,
    pdfTouchGesture: null,
    chatController: null,
    chatPanel: null,
    chatLoadRequest: null,
    activeDiagnostic: null,
    entryKind: "file",
    autoCompileTimer: 0,
    autoCompileQueued: false,
    compilingArticles: new Set(),
    stopRequested: false,
    wordWrap: true,
    settingsSection: "editor",
    editorPreferences: { ...EDITOR_PREFERENCE_DEFAULTS },
    syntaxDiagnostics: [],
    texlabDiagnostics: [],
    texlabTimer: 0,
    texlabVersion: 0,
    texlabStatus: "starting",
    collapsedSearchFiles: new Set(),
    semanticSearch: null,
    resourceSearchOpen: false,
    lastSelectedId: "",
    preferenceSaveTimer: 0,
  };
  const detailHistory = window.PortalResourceDetail.createDetailHistory({
    owner: "resource-workspace:article",
    parameter: "item",
  });
  let articleProjectPopover = null;
  let articleResourceSearchPopover = null;
  let articleCompilePopover = null;
  let articlePdfController = null;
  let articlePdfRenderKey = "";
  let articleDocumentPreviewKey = "";
  const articlePdfGestureFrames = window.PortalPreview.createFrameCoalescer(
    pending => previewArticlePdfGestureScale(pending.value, pending.point),
    error => showPdfError(error),
  );
  const detailResourceGuest = window.PortalResourceDetail.createGuest({
    resourceId: () => state.selectedId || queryState().get("item"),
    resourceType: "article",
    detailKind: "article",
    guardClose: () => {
      captureActiveDocument();
      if (!hasDirtyDocuments()) return true;
      return window.PortalUI.confirmAction("close", "当前 Article", {
        consequence: "关闭会放弃所有尚未保存的文件修改。",
      });
    },
  });


  async function request(url, options = {}) {
    return window.PortalAPI.request(url, options);
  }

  function monacoApi() {
    return window.monaco || null;
  }

  function documentForModel(model) {
    if (!model) return null;
    for (const documentState of state.documents.values()) {
      if (documentState.model === model) return documentState;
    }
    return null;
  }

  function activeEditorModel() {
    return state.editor?.getModel?.() || null;
  }

  function editorValue() {
    if (state.monacoReady && state.editor) return state.editor.getValue();
    return $("articleSourceEditor").value;
  }

  function editorPosition() {
    if (state.monacoReady && state.editor) {
      const position = state.editor.getPosition() || { lineNumber: 1, column: 1 };
      return { line: position.lineNumber, column: position.column };
    }
    const editor = $("articleSourceEditor");
    const before = editor.value.slice(0, editor.selectionStart);
    const lines = before.split("\n");
    return { line: lines.length, column: lines.at(-1).length + 1 };
  }

  function editorSelectionContext(limit = 16000) {
    if (state.monacoReady && state.editor) {
      const model = state.editor.getModel();
      const selection = state.editor.getSelection();
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
    const editor = $("articleSourceEditor");
    const startText = editor.value.slice(0, editor.selectionStart);
    const endText = editor.value.slice(0, editor.selectionEnd);
    const startLines = startText.split("\n");
    const endLines = endText.split("\n");
    const value = editor.value.slice(editor.selectionStart, editor.selectionEnd);
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
    return monaco.Uri.parse(`article://${encodeURIComponent(state.selectedId || "none")}/${segments}`);
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

  function articleRawUrl(path) {
    return `/api/articles/${encodeURIComponent(state.selectedId)}/raw?path=${encodeURIComponent(path)}`;
  }

  function articleRelativePath(basePath, relativePath) {
    const base = String(basePath || "").split("/");
    base.pop();
    for (const part of String(relativePath || "").split("/")) {
      if (!part || part === ".") continue;
      if (part === "..") base.pop();
      else base.push(part);
    }
    return base.join("/");
  }

  window.MD.registerImagePathResolver("article-workspace", function (rawPath, options = {}) {
    const path = String(rawPath || "").trim();
    if (!options.articleMarkdown || !path) return "";
    if (/^(?:data:image\/|blob:|https?:\/\/|\/)/i.test(path)) return "";
    return articleRawUrl(articleRelativePath(options.basePath, path));
  });

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
    if (!monaco || !state.monacoReady) return;
    const selected = document.documentElement.dataset.portalTheme;
    const dark = selected === "dark" || selected === "scifi";
    monaco.editor.setTheme(dark ? "article-dark" : "article-light");
  }

  function lspRange(monaco, value) {
    const range = value && typeof value === "object" ? value : {};
    const start = range.start && typeof range.start === "object" ? range.start : {};
    const end = range.end && typeof range.end === "object" ? range.end : start;
    return new monaco.Range(
      Number(start.line || 0) + 1,
      Number(start.character || 0) + 1,
      Number(end.line || 0) + 1,
      Number(end.character || 0) + 1,
    );
  }

  function lspMarkup(value) {
    if (!value) return "";
    if (typeof value === "string") return value;
    if (Array.isArray(value)) {
      return value.map(item => lspMarkup(item)).filter(Boolean).join("\n\n");
    }
    if (typeof value === "object") {
      if (typeof value.value === "string") return value.value;
      if (typeof value.language === "string" && typeof value.value === "string") {
        return `\`\`\`${value.language}\n${value.value}\n\`\`\``;
      }
    }
    return String(value || "");
  }

  function completionKind(monaco, value) {
    const kinds = monaco.languages.CompletionItemKind;
    const map = {
      1: kinds.Text, 2: kinds.Method, 3: kinds.Function, 4: kinds.Constructor,
      5: kinds.Field, 6: kinds.Variable, 7: kinds.Class, 8: kinds.Interface,
      9: kinds.Module, 10: kinds.Property, 11: kinds.Unit, 12: kinds.Value,
      13: kinds.Enum, 14: kinds.Keyword, 15: kinds.Snippet, 16: kinds.Color,
      17: kinds.File, 18: kinds.Reference, 19: kinds.Folder, 20: kinds.EnumMember,
      21: kinds.Constant, 22: kinds.Struct, 23: kinds.Event, 24: kinds.Operator,
      25: kinds.TypeParameter,
    };
    return map[Number(value)] ?? kinds.Text;
  }

  async function texlabProviderRequest(action, model, position, extra = {}) {
    const documentState = documentForModel(model);
    if (!state.selectedId || !documentState) return null;
    return request(`/api/articles/${encodeURIComponent(state.selectedId)}/texlab`, {
      method: "POST",
      body: JSON.stringify({
        action,
        path: documentState.path,
        content: model.getValue(),
        version: Math.max(1, Number(documentState.version || model.getVersionId())),
        line: Math.max(0, Number(position?.lineNumber || 1) - 1),
        character: Math.max(0, Number(position?.column || 1) - 1),
        ...extra,
      }),
    });
  }

  function registerTexlabProviders(monaco) {
    state.monacoDisposables.push(monaco.languages.registerCompletionItemProvider("latex", {
      triggerCharacters: ["\\", "{", ",", ":"],
      provideCompletionItems: async (model, position, context) => {
        try {
          const payload = await texlabProviderRequest("completion", model, position, {
            trigger_kind: Number(context?.triggerKind || 1) + 1,
            trigger_character: context?.triggerCharacter || "",
          });
          const suggestions = (payload?.items || []).map(item => {
            const textEdit = item.textEdit && typeof item.textEdit === "object" ? item.textEdit : null;
            const rawRange = textEdit?.range || textEdit?.replace || textEdit?.insert;
            const documentation = lspMarkup(item.documentation);
            return {
              label: typeof item.label === "string" ? item.label : (item.label?.label || ""),
              kind: completionKind(monaco, item.kind),
              detail: item.detail || "",
              documentation: documentation ? { value: documentation } : undefined,
              insertText: textEdit?.newText ?? item.insertText ?? item.label ?? "",
              insertTextRules: Number(item.insertTextFormat) === 2
                ? monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet
                : undefined,
              range: rawRange ? lspRange(monaco, rawRange) : undefined,
              sortText: item.sortText,
              filterText: item.filterText,
              preselect: Boolean(item.preselect),
            };
          }).filter(item => item.label);
          return { suggestions, incomplete: Boolean(payload?.is_incomplete) };
        } catch (_) {
          return { suggestions: [] };
        }
      },
    }));
    state.monacoDisposables.push(monaco.languages.registerHoverProvider("latex", {
      provideHover: async (model, position) => {
        try {
          const payload = await texlabProviderRequest("hover", model, position);
          const hover = payload?.hover;
          const value = lspMarkup(hover?.contents);
          if (!value) return null;
          return {
            range: hover?.range ? lspRange(monaco, hover.range) : undefined,
            contents: [{ value }],
          };
        } catch (_) {
          return null;
        }
      },
    }));
    state.monacoDisposables.push(monaco.languages.registerDefinitionProvider("latex", {
      provideDefinition: async (model, position) => {
        try {
          const payload = await texlabProviderRequest("definition", model, position);
          const definitions = [];
          for (const location of payload?.locations || []) {
            let documentState = state.documents.get(location.path);
            if (!documentState) {
              await openArticleFile(location.path, { activate: false });
              documentState = state.documents.get(location.path);
            }
            if (!documentState?.model) continue;
            definitions.push({
              uri: documentState.model.uri,
              range: new monaco.Range(
                Number(location.line || 1),
                Number(location.column || 1),
                Number(location.end_line || location.line || 1),
                Number(location.end_column || location.column || 1),
              ),
            });
          }
          return definitions;
        } catch (_) {
          return [];
        }
      },
    }));
  }

  function monacoEditorOptions() {
    return {
      automaticLayout: true,
      model: null,
      theme: "article-light",
      fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
      fontSize: state.editorPreferences.fontSize,
      lineHeight: Math.round(state.editorPreferences.fontSize * 1.68),
      tabSize: state.editorPreferences.tabSize,
      insertSpaces: true,
      detectIndentation: false,
      wordWrap: state.wordWrap ? "on" : "off",
      wrappingStrategy: "advanced",
      wrappingIndent: "same",
      minimap: { enabled: false },
      glyphMargin: true,
      folding: true,
      lineNumbersMinChars: 3,
      scrollBeyondLastLine: false,
      smoothScrolling: true,
      cursorBlinking: state.editorPreferences.solidCursor ? "solid" : "blink",
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
      autoClosingBrackets: state.editorPreferences.autoCloseBrackets ? "languageDefined" : "never",
      autoClosingQuotes: state.editorPreferences.autoCloseBrackets ? "languageDefined" : "never",
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
          const monaco = monacoApi();
          if (!monaco) {
            finish(false);
            return;
          }
          defineMonacoLanguages(monaco);
          defineMonacoThemes(monaco);
          captureActiveDocument();
          state.editor = monaco.editor.create($("articleMonacoEditor"), monacoEditorOptions());
          state.monacoReady = true;
          $("articleCodeEditor").classList.add("monacoReady");
          applyMonacoTheme();
          registerTexlabProviders(monaco);
          state.monacoDisposables.push(state.editor.onDidChangeModelContent(() => {
            if (state.editorSuppressChange || !state.openFile) return;
            if (!activeDocument()?.editable) return;
            state.source = state.editor.getValue();
            const documentState = activeDocument();
            if (documentState) {
              documentState.source = state.source;
              documentState.version = state.editor.getModel()?.getVersionId?.() || (documentState.version + 1);
            }
            $("articleSourceEditor").value = state.source;
            renderEditorLayers();
            updateDirtyState();
            updateCursorStatus();
            scheduleTexlabSync();
            scheduleAutoCompile();
            scheduleSemanticReload();

          }));
          state.monacoDisposables.push(state.editor.onDidChangeCursorPosition(() => {
            updateCursorStatus();
            captureActiveDocument();

          }));
          state.monacoDisposables.push(state.editor.onDidChangeCursorSelection(() => {
            updateCursorStatus();
            captureActiveDocument();

          }));
          state.monacoDisposables.push(state.editor.onDidScrollChange(() => captureActiveDocument()));
          state.editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => saveOpenFile());
          const themeObserver = new MutationObserver(applyMonacoTheme);
          themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ["data-portal-theme"] });
          state.monacoDisposables.push({ dispose: () => themeObserver.disconnect() });
          // Files may finish loading while Monaco is still loading. Preserve
          // edits made in the basic editor and attach that document now.
          if (state.openFile) activateDocument(state.openFile, { captureCurrent: false });
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

  function selectedArticle() {
    return state.articles.find(article => article.id === state.selectedId) || null;
  }

  function articleRoot(article = selectedArticle()) {
    return (article?.roots || []).find(root => root.path === article.canonical_root)
      || (article?.roots || [])[0]
      || {};
  }

  function articleCanonicalRootPath() {
    return String(articleRoot()?.path || selectedArticle()?.canonical_root || "")
      .replaceAll("\\", "/")
      .replace(/\/+$/, "");
  }

  function articleAbsolutePath(path) {
    const target = String(path || "").replaceAll("\\", "/");
    if (!target || target.startsWith("/") || target.startsWith("~/")) return target;
    const root = articleCanonicalRootPath();
    return root ? `${root}/${target.replace(/^\/+/, "")}` : target;
  }

  function articleInternalPath(path) {
    const target = String(path || "").replaceAll("\\", "/");
    const root = articleCanonicalRootPath();
    if (!root || !target.startsWith(`${root}/`)) return "";
    const relative = target.slice(root.length + 1);
    const entry = state.filesByPath.get(relative);
    return entry && entry.type !== "directory" ? relative : "";
  }

  function articleProjects(article) {
    return Array.isArray(article?.project_ids) ? article.project_ids.filter(Boolean) : [];
  }

  function updateUrl(id) {
    detailHistory.replace({ item: id });
  }

  async function loadArticleNavigationPreference() {
    state.lastSelectedId = "";
    try {
      const preferences = await window.PortalIdentity?.loadPreferences?.({ maxAgeMs: 0 });
      const serverValue = String(preferences?.resource_workspaces?.article?.last_item_id || "").trim();
      if (/^article_[0-9a-f]{12}$/.test(serverValue)) state.lastSelectedId = serverValue;
    } catch (_error) {}
    return state.lastSelectedId;
  }

  function flushArticleNavigationPreference({ keepalive = false } = {}) {
    window.clearTimeout(state.preferenceSaveTimer);
    state.preferenceSaveTimer = 0;
    window.PortalIdentity.savePreferences({
      resource_workspaces: {
        article: { last_item_id: state.lastSelectedId },
      },
    }, {
      immediate: true,
      keepalive,
    }).catch(() => {});
  }

  function persistArticleSelection(articleId) {
    const id = /^article_[0-9a-f]{12}$/.test(String(articleId || ""))
      ? String(articleId)
      : "";
    state.lastSelectedId = id;
    window.clearTimeout(state.preferenceSaveTimer);
    state.preferenceSaveTimer = window.setTimeout(flushArticleNavigationPreference, 120);
  }

  function updateSemanticUrl(searchId) {
    detailHistory.replace({ semantic: searchId });
  }

  function formatDate(value) {
    if (!value) return "";
    return window.PortalUI.formatDateTime(value, { style: "status" });
  }

  function showToast(message, kind = "") {
    return window.PortalUI.toast(message, kind || "success");
  }

  function createArticleDocumentSession(payload) {
    const session = window.PortalDocumentSession.create({
      continuousEdit: true,
      canEdit: () => true,
    });
    session.present(payload);
    return session;
  }

  function setProjectMenu(open, context) {
    if (open) articleProjectPopover?.open(context);
    else articleProjectPopover?.close(context);
  }

  function compileSettingValue(key) {
    const defaults = {
      auto_compile: false,
      compile_mode: "normal",
      syntax_check: true,
      error_handling: "continue",
    };
    return state.settings && Object.hasOwn(state.settings, key)
      ? state.settings[key]
      : defaults[key];
  }

  function renderCompileMenu() {
    document.querySelectorAll("[data-compile-setting]").forEach(button => {
      const key = button.dataset.compileSetting;
      const raw = button.dataset.compileValue;
      const value = raw === "true" ? true : raw === "false" ? false : raw;
      const selected = compileSettingValue(key) === value;
      button.classList.toggle("selected", selected);
      button.setAttribute("aria-checked", selected ? "true" : "false");
    });
    $("articleStopCompile").disabled = !state.compilingArticles.has(state.selectedId) || state.stopRequested;
    $("articleRecompileScratch").disabled = !state.selectedId || state.buildPreflight?.status === "blocked" || state.compilingArticles.has(state.selectedId);
    $("articleCompileSettings").disabled = !state.selectedId;
  }

  function normalizedEditorPreferences(value = {}) {
    const fontSize = [11, 12, 13, 14, 16].includes(Number(value.fontSize))
      ? Number(value.fontSize)
      : EDITOR_PREFERENCE_DEFAULTS.fontSize;
    const tabSize = [2, 4, 8].includes(Number(value.tabSize))
      ? Number(value.tabSize)
      : EDITOR_PREFERENCE_DEFAULTS.tabSize;
    return {
      autoCloseBrackets: typeof value.autoCloseBrackets === "boolean"
        ? value.autoCloseBrackets
        : EDITOR_PREFERENCE_DEFAULTS.autoCloseBrackets,
      solidCursor: typeof value.solidCursor === "boolean"
        ? value.solidCursor
        : EDITOR_PREFERENCE_DEFAULTS.solidCursor,
      fontSize,
      tabSize,
    };
  }

  function persistEditorPreferences() {
    window.PortalState.storage.writeJson(EDITOR_PREFERENCE_KEY, state.editorPreferences);
    window.PortalState.storage.writeText(WORD_WRAP_STORAGE_KEY, state.wordWrap ? "1" : "0");
  }

  function renderEditorPreferenceControls() {
    if (!$("articleEditorAutoClose")) return;
    $("articleEditorAutoClose").checked = state.editorPreferences.autoCloseBrackets;
    $("articleEditorSolidCursor").checked = state.editorPreferences.solidCursor;
    $("articleEditorCodeCheck").checked = Boolean(compileSettingValue("syntax_check"));
    $("articleEditorCodeCheck").disabled = !state.selectedId;
    $("articleEditorWordWrap").checked = state.wordWrap;
    $("articleEditorFontSize").value = String(state.editorPreferences.fontSize);
    $("articleEditorTabSize").value = String(state.editorPreferences.tabSize);
  }

  function applyEditorPreferences() {
    const editorPane = document.querySelector(".articleEditorPane");
    editorPane.style.setProperty("--article-editor-font-size", `${state.editorPreferences.fontSize}px`);
    editorPane.style.setProperty("--article-editor-tab-size", String(state.editorPreferences.tabSize));
    $("articleCodeEditor").classList.toggle("solidCursor", state.editorPreferences.solidCursor);
    state.editor?.updateOptions?.({
      fontSize: state.editorPreferences.fontSize,
      lineHeight: Math.round(state.editorPreferences.fontSize * 1.68),
      tabSize: state.editorPreferences.tabSize,
      cursorBlinking: state.editorPreferences.solidCursor ? "solid" : "blink",
      autoClosingBrackets: state.editorPreferences.autoCloseBrackets ? "languageDefined" : "never",
      autoClosingQuotes: state.editorPreferences.autoCloseBrackets ? "languageDefined" : "never",
    });
    activeEditorModel()?.updateOptions?.({
      tabSize: state.editorPreferences.tabSize,
      insertSpaces: true,
    });
    renderEditorPreferenceControls();
    if (state.openFile) renderEditorLayers();
  }

  function updateEditorPreference(key, value) {
    state.editorPreferences = normalizedEditorPreferences({
      ...state.editorPreferences,
      [key]: value,
    });
    persistEditorPreferences();
    applyEditorPreferences();
  }

  function setSettingsSection(section) {
    const target = section === "compiler" ? "compiler" : "editor";
    state.settingsSection = target;
    document.querySelectorAll("[data-settings-section]").forEach(button => {
      const active = button.dataset.settingsSection === target;
      button.classList.toggle("active", active);
      button.setAttribute("aria-selected", active ? "true" : "false");
      button.tabIndex = active ? 0 : -1;
    });
    document.querySelectorAll("[data-settings-panel]").forEach(panel => {
      const active = panel.dataset.settingsPanel === target;
      panel.classList.toggle("active", active);
      panel.hidden = !active;
    });
  }

  function setCompileMenu(open, context) {
    if (open && state.selectedId) articleCompilePopover?.open(context);
    else articleCompilePopover?.close(context);
  }

  function filteredArticles(input) {
    const needle = String(input?.value || "").trim().toLowerCase();
    if (!needle) return state.articles;
    return state.articles.filter(article => [
      article.title,
      article.id,
      article.canonical_root,
      ...articleProjects(article),
    ].join(" ").toLowerCase().includes(needle));
  }

  function setArticleResourceSearchOpen(open, context) {
    if (open) articleResourceSearchPopover?.open(context);
    else articleResourceSearchPopover?.close(context);
  }

  function initializeArticlePopovers() {
    articleProjectPopover = window.PortalOverlay.bindPopover({
      trigger: $("articleProjectButton"),
      panel: $("articleProjectMenu"),
      placement: "bottom-start",
      ariaHaspopup: "dialog",
      closeOnOutside: true,
      closeOnEscape: true,
      onOpen: () => {
        setCompileMenu(false);
        setArticleResourceSearchOpen(false);
        renderArticleSelectors();
        window.setTimeout(() => {
          if (articleProjectPopover?.isOpen()) $("articleMenuSearch").focus();
        }, 0);
      },
    });
    articleResourceSearchPopover = window.PortalOverlay.bindPopover({
      trigger: $("articleResourceQuery"),
      anchor: $("articleResourceSearch"),
      panel: $("articleResourceSearchPopover"),
      placement: "bottom-start",
      ariaHaspopup: "listbox",
      closeOnOutside: true,
      closeOnEscape: true,
      restoreFocusOnEscape: false,
      onOpen: () => {
        state.resourceSearchOpen = true;
        setProjectMenu(false);
        setCompileMenu(false);
        renderArticleResourceSearch();
      },
      onClose: () => {
        state.resourceSearchOpen = false;
      },
    });
    articleCompilePopover = window.PortalOverlay.bindPopover({
      trigger: $("articleCompileSettings"),
      panel: $("articleCompileMenu"),
      placement: "bottom-start",
      ariaHaspopup: "menu",
      closeOnOutside: true,
      closeOnEscape: true,
      onOpen: () => {
        setProjectMenu(false);
        setArticleResourceSearchOpen(false);
        renderCompileMenu();
      },
    });
  }

  function articleResourceResultHtml(article, semanticResult = null) {
    const projects = articleProjects(article).join(" · ") || "No Project";
    return window.PortalI18n.html`<button class="articleResourceResult ${article.id === state.selectedId ? "active" : ""}" type="button" data-article-header-select="${esc(article.id)}" data-portal-resource-id="${esc(article.id)}" aria-label="${esc(`${article.title || article.id}，Article ID ${article.id}`)}">
      <span>
        <strong title="${esc(article.title || article.id)}">${esc(article.title || article.id)}</strong>
        <small>${esc(projects)}</small>
        ${semanticResult?.reason ? window.PortalI18n.html`<em>${esc(semanticResult.reason)}</em>` : ""}
      </span>
      <code title="${esc(`Article ID：${article.id}`)}">${semanticResult ? `${Number(semanticResult.score || 0)}% · ${esc(article.id.replace("article_", ""))}` : esc(article.id.replace("article_", ""))}</code>
    </button>`;
  }

  function renderArticleResourceSearch() {
    const host = $("articleResourceSearchResults");
    const activeSemanticId = state.semanticSearch?.activeId() || "";
    if (activeSemanticId) {
      const articles = state.semanticSearch.filterAndSort(
        state.articles,
        article => article.id,
      );
      host.innerHTML = window.PortalI18n.html`
        <div class="articleResourceSearchHeading"><span>语义结果</span><span>${articles.length}</span></div>
        ${articles.length
          ? articles.map(article => articleResourceResultHtml(
              article,
              state.semanticSearch.resultFor(article.id),
            )).join("")
          : window.PortalI18n.t('<div class="articleResourceSearchEmpty">当前语义检索没有匹配的 Article。</div>')}`;
      return;
    }
    const articles = filteredArticles($("articleResourceQuery"));
    host.innerHTML = window.PortalI18n.html`
      <div class="articleResourceSearchHeading"><span>Article 项目</span><span>${articles.length}</span></div>
      ${articles.length
        ? articles.map(article => articleResourceResultHtml(article)).join("")
        : window.PortalI18n.t('<div class="articleResourceSearchEmpty">没有匹配的 Article。</div>')}`;
  }

  function initializeArticleSemanticSearch() {
    state.semanticSearch = window.PortalSemanticSearch?.create({
      input: $("articleResourceQuery"),
      button: $("articleSemanticButton"),
      panel: $("articleSemanticPanel"),
      identity: $("articleSemanticIdentity"),
      identityOptions: {
        titleId: "articleSemanticSearchTitle",
        idId: "articleSemanticSearchId",
      },
      projectionScope: "article",
      client: {
        get: url => request(url),
        post: (url, payload) => request(url, {
          method: "POST",
          body: JSON.stringify(payload),
        }),
      },
      initialActiveId: String(queryState().get("semantic") || ""),
      urls: {
        list: "/api/resource-semantic-searches?resource_scope=article",
        start: "/api/resource-semantic-search/start",
        refresh: "/api/resource-semantic-search/refresh",
        delete: "/api/resource-semantic-search/delete",
      },
      startPayload: () => ({ resource_scope: "article" }),
      actionPayload: () => ({ resource_scope: "article" }),
      onStart: () => {
        $("articleResourceQuery").value = "";
        updateSemanticUrl("");
        setArticleResourceSearchOpen(true);
      },
      onActiveChange: searchId => {
        if (searchId) $("articleResourceQuery").value = "";
        updateSemanticUrl(searchId || "");
        renderArticleResourceSearch();
        if (searchId) setArticleResourceSearchOpen(true);
      },
      onJobsChange: () => {
        if (state.resourceSearchOpen) renderArticleResourceSearch();
      },
      onError: (_error, message) => showToast(message, "error"),
    });
  }

  function articleSelectorHtml(article) {
    const projects = articleProjects(article);
    const projectLabel = projects.join(" · ") || window.PortalI18n.t("No Project");
    return window.PortalI18n.html`<button class="articleMenuItem ${article.id === state.selectedId ? "active" : ""}" type="button" data-article-select="${esc(article.id)}" data-portal-resource-id="${esc(article.id)}" aria-label="${esc(`${article.title || article.id}，Article ID ${article.id}`)}">
      <span><strong title="${esc(article.title || article.id)}">${esc(article.title || article.id)}</strong><small title="${esc(`Article ID：${article.id}`)}">${esc(article.id)}</small></span>
      <span class="articleMenuProjects" title="${esc(window.PortalI18n.text`所属项目：${projectLabel}`)}">${esc(projectLabel)}</span>
    </button>`;
  }

  function renderArticleSelectors() {
    const menuArticles = filteredArticles($("articleMenuSearch"));
    $("articleMenuList").innerHTML = menuArticles.length
      ? menuArticles.map(articleSelectorHtml).join("")
      : articleStateHtml("没有匹配的 Article。", { density: "inline", icon: false });

    const libraryArticles = filteredArticles($("articleLibrarySearch"));
    $("articleLibraryMeta").textContent = `${libraryArticles.length} / ${state.articles.length} registered`;
    $("articleLibraryList").innerHTML = libraryArticles.length
      ? libraryArticles.map(article => window.PortalI18n.html`
        <button class="articleLibraryCard ${article.id === state.selectedId ? "active" : ""}" type="button" data-article-select="${esc(article.id)}" data-portal-resource-id="${esc(article.id)}" aria-label="${esc(`${article.title || article.id}，Article ID ${article.id}`)}">
          <strong title="${esc(article.title || article.id)}">${esc(article.title || article.id)}</strong>
          <span>${esc(articleProjects(article).join(" · ") || window.PortalI18n.t("No Project"))}</span>
          <code title="${esc(article.canonical_root || "")}">${esc(article.id)}</code>
        </button>`).join("")
      : articleStateHtml("没有匹配的 Article。");
    $("articleUnregisterCurrent").disabled = !state.selectedId;
    if (state.resourceSearchOpen) renderArticleResourceSearch();
  }

  function renderProjectIdentity() {
    const article = selectedArticle();
    window.PortalResourceHeader?.mount?.("articleProjectIdentity", {
      title: article?.title || window.PortalI18n.t("Select an Article project"),
      type: article ? "article" : "",
      resource_id: article?.id || "",
      copy_label: "article_id",
    }, {
      compact: true,
      headingTag: "strong",
      titleId: "articleProjectTitle",
      idId: "articleProjectId",
    });
    const projectButton = $("articleProjectButton");
    projectButton.setAttribute("aria-label", article
      ? window.PortalI18n.text`切换 Article。当前：${article.title || article.id}，Article ID ${article.id}`
      : window.PortalI18n.t("选择 Article"));
    projectButton.title = article
      ? `${article.title || article.id} · ${article.id}`
      : window.PortalI18n.t("选择 Article");
    const pdfIdentity = $("articlePdfIdentity");
    pdfIdentity.textContent = article?.id || window.PortalI18n.t("未选择 Article");
    pdfIdentity.title = article ? window.PortalI18n.text`当前 Article Resource ID：${article.id}` : window.PortalI18n.t("当前未选择 Article");
    $("articleCompile").disabled = !article || state.compilingArticles.has(state.selectedId);
    $("articleSaveFile").disabled = !state.openFile || !documentIsDirty(activeDocument());
    window.PortalSite.setTitle(() => article
      ? `${article.title} — ${window.PortalI18n.t("Article")}`
      : window.PortalI18n.t("Article Workspace"));
    renderArticleSelectors();
    renderCompileMenu();
  }

  function setSideView(view, { forceOpen = true } = {}) {
    const target = ["files", "search", "git", "chat", "settings", "library"].includes(view) ? view : "files";
    state.sideView = target;
    if (forceOpen && !articleWorkbench?.isPanelVisible("navigation")) articleWorkbench?.showPanel("navigation");
    document.querySelectorAll("[data-article-view]").forEach(button => {
      button.classList.toggle("active", button.dataset.articleView === target);
    });
    document.querySelectorAll("[data-article-panel]").forEach(panel => {
      panel.classList.toggle("active", panel.dataset.articlePanel === target);
    });
    if (target === "chat") return loadArticleChat().catch(error => showToast(error.message || error, "error"));
    if (target === "git") loadArticleGit({ includeHistory: true }).catch(error => showToast(error.message || error, "error"));
    if (target === "search") window.setTimeout(() => $("articleGlobalSearchInput").focus(), 0);
    if (target === "settings") setSettingsSection(state.settingsSection);
  }

  function renderArticleGitBadge() {
    const button = $("articleGitView");
    const badge = $("articleGitBadge");
    if (!button || !badge) return;
    const available = Boolean(state.gitStatus?.available);
    const count = available && Array.isArray(state.gitStatus?.changes)
      ? state.gitStatus.changes.length
      : 0;
    badge.hidden = count === 0;
    badge.textContent = count > 99 ? "99+" : String(count);
    const label = count ? window.PortalI18n.text`源代码管理，${count} 个未提交变更` : window.PortalI18n.t("源代码管理");
    button.setAttribute("aria-label", label);
    button.title = label;
  }

  function renderArticleGit() {
    const status = state.gitStatus;
    const panel = $("articleGitPanel");
    const changesHost = $("articleGitChanges");
    const historyHost = $("articleGitHistory");
    const available = Boolean(status?.available);
    renderArticleGitBadge();
    panel.classList.toggle("is-unavailable", !available);
    $("articleGitIdentity").hidden = !available;
    $("articleGitCommitForm").hidden = !available;
    $("articleGitHistorySection").hidden = !available;
    $("articleGitCommit").disabled = !available || !Number(status?.staged_count || 0);
    if (!available) {
      $("articleGitSummary").textContent = window.PortalI18n.t("版本控制尚未启用");
      changesHost.innerHTML = window.PortalI18n.html`<div class="articleGitOnboarding">
        <div class="articleGitOnboardingMark" data-portal-icon="branch" aria-hidden="true"></div>
        <div class="articleGitOnboardingCopy">
          <strong>开始记录稿件版本</strong>
          <p>创建本地 Git 仓库，之后可以逐行审阅大模型修改、选择要提交的文件，并随时回看历史版本。</p>
        </div>
        <button class="portalButton is-primary" type="button" data-git-init data-portal-icon="plus"><span>启用 Git 版本管理</span></button>
        <small>${esc(status?.reason || window.PortalI18n.t("当前 Article 目录尚未纳入 Git 管理。"))}</small>
      </div>`;
      historyHost.replaceChildren();
      $("articleGitHistoryCount").textContent = "0";
      window.PortalIcons?.enhance?.(changesHost);
      return;
    }
    const head = status.head || {};
    const changes = Array.isArray(status.changes) ? status.changes : [];
    $("articleGitSummary").textContent = window.PortalI18n.text`${changes.length} 个变更 · ${Number(status.staged_count || 0)} 个已暂存`;
    $("articleGitIdentity").innerHTML = window.PortalI18n.html`<span data-portal-icon="branch" aria-hidden="true"></span><strong>${esc(status.branch || window.PortalI18n.t("detached HEAD"))}</strong><code>${esc(head.short_sha || window.PortalI18n.t("尚无提交"))} · ${esc(status.scope || ".")}</code>`;
    const sections = [
      ["已暂存的更改", changes.filter(item => item.staged), true],
      ["更改", changes.filter(item => item.unstaged || item.untracked), false],
    ];
    changesHost.innerHTML = sections.map(([title, items, staged]) => {
      if (!items.length) return "";
      return window.PortalI18n.html`<section class="articleGitChangeSection"><div class="articleGitSectionHead"><strong>${title}</strong><small>${items.length}</small></div>${items.map(item => window.PortalI18n.html`
        <div class="articleGitChangeRow" title="${esc(item.path)}">
          <button class="articleGitChangePath" type="button" data-git-diff="${esc(item.path)}">${esc(item.path.split("/").at(-1) || item.path)}<small>${esc(item.path.includes("/") ? item.path.slice(0, item.path.lastIndexOf("/")) : "")}</small></button>
          <span class="articleGitStatusCode" title="${esc(item.label || "")}">${esc(item.untracked ? "U" : item.conflicted ? "!" : String(item.code || "").trim() || "M")}</span>
          <button class="portalButton is-icon is-quiet iconBtn articleGitStageButton" type="button" data-git-stage="${esc(item.path)}" data-git-staged="${staged ? "false" : "true"}" aria-label="${staged ? window.PortalI18n.t("取消暂存") : window.PortalI18n.t("暂存")} ${esc(item.path)}" title="${staged ? window.PortalI18n.t("取消暂存") : window.PortalI18n.t("暂存")}" data-portal-icon="${staged ? "minus" : "plus"}"></button>
        </div>`).join("")}</section>`;
    }).join("") || window.PortalI18n.html`<div class="portalAsyncState is-compact" data-state="empty" data-density="compact" role="status"><span class="portalAsyncStateIcon" aria-hidden="true"></span><span class="portalAsyncStateMessage">工作树干净，没有未提交的稿件改动。</span></div>`;
    $("articleGitHistoryCount").textContent = String(state.gitHistory.length);
    historyHost.innerHTML = state.gitHistory.map(commit => window.PortalI18n.html`<button class="articleGitCommitRow" type="button" data-git-commit="${esc(commit.sha || "")}" data-git-commit-short="${esc(commit.short_sha || "")}" data-git-commit-subject="${esc(commit.subject || "")}" title="查看 ${esc(commit.short_sha || "commit")} 的更改"><code>${esc(commit.short_sha || "")}</code><span>${esc(commit.subject || window.PortalI18n.t("(无提交信息)"))}</span><span data-portal-icon="chevron-right" aria-hidden="true"></span></button>`).join("");
    window.PortalIcons?.enhance?.($("articleGitPanel"));
  }

  let articleGitHistoryRequested = false;
  const articleGitRefresh = window.PortalRefreshCoordinator.create({
    key: () => state.selectedId,
    interval: ARTICLE_GIT_REFRESH_MS,
    initialDelay: ARTICLE_GIT_REFRESH_MS,
    shouldRefresh: () => !!state.selectedId,
    fetch: async ({ key }) => {
      const id = encodeURIComponent(key);
      const includeHistory = articleGitHistoryRequested || state.sideView === "git";
      articleGitHistoryRequested = false;
      let historyError = null;
      const [status, history] = await Promise.all([
        request(`/api/articles/${id}/git`),
        includeHistory ? request(`/api/articles/${id}/git/history?limit=12`).catch(error => {
          historyError = error;
          return null;
        }) : Promise.resolve(null),
      ]);
      return { status, history, historyError };
    },
    apply: ({ status, history, historyError }) => {
      const commits = history ? (Array.isArray(history.commits) ? history.commits : []) : state.gitHistory;
      const changed = JSON.stringify([state.gitStatus, state.gitHistory]) !== JSON.stringify([status, commits]);
      state.gitStatus = status;
      state.gitHistory = commits;
      if (changed) renderArticleGit();
      if (historyError) throw historyError;
    },
  });

  async function loadArticleGit({ includeHistory = false, silent = false } = {}) {
    if (!state.selectedId) return;
    articleGitHistoryRequested ||= includeHistory;
    articleGitRefresh.start();
    try {
      await articleGitRefresh.refresh({ force: true });
    } catch (error) {
      if (!silent) throw error;
    }
  }

  function scheduleArticleGitRefresh(delay = ARTICLE_GIT_REFRESH_MS) {
    if (!state.selectedId) return;
    articleGitRefresh.start();
    articleGitRefresh.invalidate({ delay });
  }

  async function updateArticleGitStage(path, staged) {
    state.gitStatus = await request(`/api/articles/${encodeURIComponent(state.selectedId)}/git/stage`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paths: [path], staged }),
    });
    renderArticleGit();
  }

  async function initializeArticleGit() {
    state.gitStatus = await request(`/api/articles/${encodeURIComponent(state.selectedId)}/git/init`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    await loadArticleGit({ includeHistory: true });
    showToast(window.PortalI18n.t("已为当前 Article 初始化 Git 仓库"), "success");
  }

  async function commitArticleGit(event) {
    event.preventDefault();
    const message = $("articleGitMessage").value.trim();
    if (!message) return;
    $("articleGitCommit").disabled = true;
    try {
      const result = await request(`/api/articles/${encodeURIComponent(state.selectedId)}/git/commit`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message }),
      });
      $("articleGitMessage").value = "";
      state.gitStatus = result.status;
      await loadArticleGit({ includeHistory: true });
      showToast(window.PortalI18n.text`已提交 ${result.commit?.short_sha || window.PortalI18n.t("Article 更改")}`, "success");
    } finally {
      $("articleGitCommit").disabled = !Number(state.gitStatus?.staged_count || 0);
    }
  }

  function closeArticleGitDiff() {
    state.gitDiffPath = "";
    state.gitDiffTitle = "";
    state.gitDiffEditor?.dispose?.();
    state.gitDiffEditor = null;
    state.gitDiffModels.forEach(model => model?.dispose?.());
    state.gitDiffModels = [];
    state.gitDiffFiles = [];
    state.gitDiffActiveFile = 0;
    $("articleGitDiffEditor").replaceChildren();
    $("articleGitDiffFiles").replaceChildren();
    $("articleGitDiffView").hidden = true;
    $("articleEditorToolbar").hidden = false;
    renderDocumentTabs();
  }

  function cleanGitDiffPath(path, scope = "") {
    let value = String(path || "").replace(/^['\"]|['\"]$/g, "");
    value = value.replace(/^[ab]\//, "");
    const prefix = String(scope || "").replace(/^\.\/$|^\.$/, "").replace(/\/$/, "");
    if (prefix && value.startsWith(`${prefix}/`)) value = value.slice(prefix.length + 1);
    return value === "/dev/null" ? "" : value;
  }

  function gitPatchBlocks(diff) {
    const source = String(diff || "");
    const starts = [];
    const matcher = /^diff --git /gm;
    let match;
    while ((match = matcher.exec(source))) starts.push(match.index);
    if (!starts.length) return source ? [source] : [];
    return starts.map((start, index) => source.slice(start, starts[index + 1] ?? source.length));
  }

  function parseGitPatch(diff, { fallbackPath = "", scope = "" } = {}) {
    const blocks = gitPatchBlocks(diff);
    return blocks.map((block, blockIndex) => {
      const lines = block.split("\n");
      const header = lines.find(line => line.startsWith("diff --git ")) || "";
      const headerMatch = header.match(/^diff --git a\/(.+) b\/(.+)$/);
      const oldHeader = lines.find(line => line.startsWith("--- "))?.slice(4).split("\t")[0] || "";
      const newHeader = lines.find(line => line.startsWith("+++ "))?.slice(4).split("\t")[0] || "";
      const oldPath = cleanGitDiffPath(oldHeader || headerMatch?.[1] || "", scope);
      const newPath = cleanGitDiffPath(newHeader || headerMatch?.[2] || "", scope);
      const path = cleanGitDiffPath((blocks.length === 1 && fallbackPath) || newPath || oldPath || `变更 ${blockIndex + 1}`, scope);
      const original = [];
      const modified = [];
      let firstOriginalLine = 1;
      let firstModifiedLine = 1;
      let foundHunk = false;
      let inHunk = false;
      for (const line of lines) {
        const hunk = line.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/);
        if (hunk) {
          const oldStart = Math.max(1, Number(hunk[1]) || 1);
          const newStart = Math.max(1, Number(hunk[2]) || 1);
          if (!foundHunk) {
            firstOriginalLine = oldStart;
            firstModifiedLine = newStart;
          }
          while (original.length < oldStart - 1) original.push("");
          while (modified.length < newStart - 1) modified.push("");
          foundHunk = true;
          inHunk = true;
          continue;
        }
        if (!inHunk || line.startsWith("\\ No newline at end of file")) continue;
        if (line.startsWith("+")) modified.push(line.slice(1));
        else if (line.startsWith("-")) original.push(line.slice(1));
        else if (line.startsWith(" ")) {
          original.push(line.slice(1));
          modified.push(line.slice(1));
        } else if (line.startsWith("diff --git ")) inHunk = false;
      }
      return {
        path,
        originalPath: oldPath || path,
        modifiedPath: newPath || path,
        original: original.join("\n"),
        modified: modified.join("\n"),
        firstOriginalLine,
        firstModifiedLine,
        binary: /^(?:Binary files|GIT binary patch)/m.test(block),
        hasChanges: foundHunk,
      };
    });
  }

  function renderArticleGitDiffFile(index) {
    const file = state.gitDiffFiles[index];
    if (!file) return;
    state.gitDiffActiveFile = index;
    document.querySelectorAll("[data-git-diff-file]").forEach(button => {
      const active = Number(button.dataset.gitDiffFile) === index;
      button.classList.toggle("active", active);
      button.setAttribute("aria-selected", String(active));
    });
    state.gitDiffModels.forEach(model => model?.dispose?.());
    state.gitDiffModels = [];
    const monaco = monacoApi();
    const empty = $("articleGitDiffEmpty");
    if (!monaco || !file.hasChanges) {
      state.gitDiffEditor?.setModel?.(null);
      empty.textContent = file.binary ? window.PortalI18n.text`${file.path} 是二进制文件，无法显示文本差异。` : window.PortalI18n.text`${file.path} 没有可显示的文本差异。`;
      empty.hidden = false;
      $("articleGitDiffEditor").hidden = true;
      return;
    }
    empty.hidden = true;
    $("articleGitDiffEditor").hidden = false;
    if (!state.gitDiffEditor) {
      state.gitDiffEditor = monaco.editor.createDiffEditor($("articleGitDiffEditor"), {
        automaticLayout: true,
        readOnly: true,
        originalEditable: false,
        renderSideBySide: state.gitDiffSideBySide,
        useInlineViewWhenSpaceIsLimited: false,
        enableSplitViewResizing: true,
        renderIndicators: true,
        ignoreTrimWhitespace: false,
        diffWordWrap: "on",
        wordWrap: "on",
        minimap: { enabled: false },
        folding: false,
        glyphMargin: false,
        lineNumbersMinChars: 3,
        scrollBeyondLastLine: false,
        fontSize: 12,
        renderOverviewRuler: true,
        hideUnchangedRegions: { enabled: true, contextLineCount: 3, minimumLineCount: 4, revealLineCount: 8 },
      });
    }
    const revision = `${Date.now()}-${index}`;
    const language = languageForPath(file.modifiedPath || file.originalPath || file.path);
    const originalModel = monaco.editor.createModel(file.original, language, monaco.Uri.parse(`article-diff://old/${revision}/${file.originalPath}`));
    const modifiedModel = monaco.editor.createModel(file.modified, language, monaco.Uri.parse(`article-diff://new/${revision}/${file.modifiedPath}`));
    state.gitDiffModels = [originalModel, modifiedModel];
    state.gitDiffEditor.setModel({ original: originalModel, modified: modifiedModel });
    window.requestAnimationFrame(() => {
      state.gitDiffEditor?.layout?.();
      state.gitDiffEditor?.getModifiedEditor?.().revealLineInCenter(file.firstModifiedLine);
    });
  }

  function renderArticleGitDiff(title, diff, emptyMessage, options = {}) {
    state.gitDiffTitle = title;
    $("articleGitDiffTitle").textContent = title;
    $("articleGitDiff").textContent = diff;
    state.gitDiffFiles = parseGitPatch(diff, options);
    const filesHost = $("articleGitDiffFiles");
    filesHost.innerHTML = state.gitDiffFiles.map((file, index) => window.PortalI18n.html`<button class="articleGitDiffFile${index === 0 ? " active" : ""}" type="button" role="tab" aria-selected="${index === 0 ? "true" : "false"}" data-git-diff-file="${index}" title="${esc(file.path)}"><span>${esc(file.path)}</span><small>${file.binary ? "BIN" : "M"}</small></button>`).join("");
    filesHost.hidden = state.gitDiffFiles.length <= 1;
    $("articleGitDiffEmpty").textContent = emptyMessage;
    $("articleGitDiffEmpty").hidden = state.gitDiffFiles.length > 0;
    $("articleGitDiffEditor").hidden = state.gitDiffFiles.length === 0;
    $("articleGitDiffView").hidden = false;
    $("articleEditorToolbar").hidden = true;
    renderDocumentTabs();
    if (state.gitDiffFiles.length) renderArticleGitDiffFile(0);
  }

  function toggleArticleGitDiffLayout() {
    state.gitDiffSideBySide = !state.gitDiffSideBySide;
    state.gitDiffEditor?.updateOptions?.({
      renderSideBySide: state.gitDiffSideBySide,
      useInlineViewWhenSpaceIsLimited: false,
    });
    $("articleGitDiffLayout").textContent = state.gitDiffSideBySide ? window.PortalI18n.t("并排") : window.PortalI18n.t("内联");
    $("articleGitDiffLayout").setAttribute("aria-pressed", String(state.gitDiffSideBySide));
  }

  async function openArticleGitDiff(path) {
    const payload = await request(`/api/articles/${encodeURIComponent(state.selectedId)}/git/diff?path=${encodeURIComponent(path)}`);
    state.gitDiffPath = path;
    renderArticleGitDiff(`${path} · 工作树变更`, String(payload.diff || ""), "该文件当前没有可显示的工作树差异。", { fallbackPath: path, scope: payload.scope || "" });
  }

  async function openArticleGitCommit(commitSha, shortSha, subject) {
    const payload = await request(`/api/articles/${encodeURIComponent(state.selectedId)}/git/diff?commit=${encodeURIComponent(commitSha)}`);
    const commit = payload.commit || {};
    const label = commit.short_sha || shortSha || commitSha.slice(0, 7);
    const message = commit.subject || subject || window.PortalI18n.t("(无提交信息)");
    state.gitDiffPath = `commit:${commit.sha || commitSha}`;
    renderArticleGitDiff(`${label} · ${message}`, String(payload.diff || ""), "该提交在当前 Article 目录中没有可显示的更改。", { scope: payload.scope || "" });
  }

  function setNavigationPanelVisible(visible) {
    if (visible) articleWorkbench?.showPanel("navigation");
    else articleWorkbench?.hidePanel("navigation");
  }

  function treeFromEntries(entries) {
    const root = { name: "", path: "", type: "directory", children: new Map() };
    for (const entry of entries) {
      const parts = String(entry.path || "").split("/").filter(Boolean);
      let cursor = root;
      parts.forEach((part, index) => {
        const path = parts.slice(0, index + 1).join("/");
        if (!cursor.children.has(part)) {
          cursor.children.set(part, {
            name: part,
            path,
            type: index === parts.length - 1 ? entry.type : "directory",
            children: new Map(),
          });
        }
        cursor = cursor.children.get(part);
        if (index === parts.length - 1) Object.assign(cursor, entry);
      });
    }
    return root;
  }

  function visibleTreeEntries(node, depth = 0) {
    const children = [...node.children.values()].sort((left, right) => {
      if (left.type !== right.type) return left.type === "directory" ? -1 : 1;
      return left.name.localeCompare(right.name, undefined, { sensitivity: "base" });
    });
    return children.flatMap(child => [
      { ...child, depth },
      ...(child.type === "directory" && !state.collapsedDirectories.has(child.path)
        ? visibleTreeEntries(child, depth + 1) : []),
    ]);
  }

  function articleLinkedFile(descriptor, metadata = {}) {
    const path = String(descriptor?.path || "").trim();
    if (!path) return null;
    return {
      path,
      name: String(descriptor.name || window.PortalFileLinks?.fileName?.(path) || "file"),
      kind: String(metadata.kind || descriptor.kind || window.PortalFileLinks?.kindForPath?.(path) || "text"),
    };
  }

  async function registerArticleLinkedFiles(descriptors) {
    if (!window.PortalFileLinks || !Array.isArray(descriptors) || !descriptors.length) return;
    const articleId = state.selectedId;
    const pendingPaths = state.linkedFileProbes;
    const currentPath = articleAbsolutePath(activeDocument()?.path || "");
    const candidates = [...new Map(descriptors.map(descriptor => [
      String(descriptor?.path || ""),
      descriptor,
    ])).values()].filter(descriptor => {
      const path = String(descriptor?.path || "");
      return path
        && path !== currentPath
        && !articleInternalPath(path)
        && !state.linkedFiles.has(path)
        && !pendingPaths.has(path);
    });
    if (!candidates.length) return;
    candidates.forEach(descriptor => pendingPaths.add(descriptor.path));
    let inspected;
    try {
      inspected = await Promise.all(candidates.map(async descriptor => {
        try {
          const metadata = await window.PortalFileLinks.probe(descriptor.path);
          return metadata ? articleLinkedFile(descriptor, metadata) : null;
        } catch {
          return null;
        }
      }));
    } finally {
      candidates.forEach(descriptor => pendingPaths.delete(descriptor.path));
    }
    if (articleId !== state.selectedId || pendingPaths !== state.linkedFileProbes) return;
    let changed = false;
    inspected.filter(Boolean).forEach(file => {
      if (state.linkedFiles.has(file.path) || articleInternalPath(file.path)) return;
      state.linkedFiles.set(file.path, file);
      changed = true;
    });
    if (changed) renderFileTree();
  }

  async function openArticleLinkedReference(request) {
    const resourceAddress = String(
      window.PortalResourceChips?.resourceAddressFromPreviewRequest?.(
        request || {},
      )
      || "",
    ).trim();
    if (resourceAddress) {
      await openArticleResourcePreview({
        ...request,
        address: resourceAddress,
      });
      return;
    }
    const path = String(request?.path || request?.href || "").trim();
    if (!path) return;
    const internalPath = articleInternalPath(path);
    if (internalPath) {
      await openArticleFile(internalPath);
      return;
    }
    let file = state.linkedFiles.get(path);
    if (!file) {
      file = articleLinkedFile({
        ...request,
        path,
        name: request?.name || window.PortalFileLinks?.fileName?.(path),
      });
      if (!file) return;
      state.linkedFiles.set(path, file);
      renderFileTree();
    }
    await openArticleLinkedFile(file);
  }

  function renderFileTree() {
    const root = $("articleFileTree");
    window.PortalFileTree.dispose(root);
    if (!state.selectedId) {
      root.innerHTML = articleStateHtml("Select an Article project.");
      return;
    }
    const linkedFiles = [...state.linkedFiles.values()]
      .sort((left, right) => (
        String(left.name || "").localeCompare(String(right.name || ""))
        || String(left.path || "").localeCompare(String(right.path || ""))
      ))
      .map(file => ({ ...file, type: "file", linked: true, depth: 0 }));
    const entries = [...visibleTreeEntries(treeFromEntries(state.files)), ...linkedFiles];
    if (!entries.length) {
      root.innerHTML = articleStateHtml("This Article project has no visible files.");
      return;
    }
    window.PortalFileTree.render({
      root,
      entries,
      ariaLabel: window.PortalI18n.t("Article files"),
      membership: state.collapsedDirectories,
      isExpanded: entry => !state.collapsedDirectories.has(entry.path),
      isSelected: entry => entry.path === state.openFile,
      getMeta: entry => entry.primary ? "主文件" : (entry.linked ? "引用文件" : ""),
      beforeToggle: path => { state.selectedDirectory = path; },
      render: renderFileTree,
      open: path => {
        const linked = state.linkedFiles.get(path);
        if (linked) return openArticleLinkedReference(linked);
        state.selectedDirectory = path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "";
        return openArticleFile(path);
      },
    });
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

  function plainTexTitle(value) {
    return String(value || "")
      .replace(/\\(?:textbf|textit|emph|texorpdfstring)\*?(?:\[[^\]]*\])?\{([^{}]*)\}/g, "$1")
      .replace(/\\[A-Za-z@]+\*?/g, "")
      .replace(/[{}]/g, "")
      .replace(/~/g, " ")
      .replace(/\s+/g, " ")
      .trim();
  }

  function parseOutline(source) {
    const levels = { part: 0, chapter: 0, section: 0, subsection: 1, subsubsection: 2, paragraph: 3 };
    const output = [];
    String(source || "").split("\n").forEach((line, index) => {
      if (/^\s*%/.test(line)) return;
      const match = line.match(/^\s*\\(part|chapter|section|subsection|subsubsection|paragraph)\*?(?:\[[^\]]*\])?\{(.+?)\}/);
      if (!match) return;
      output.push({
        title: plainTexTitle(match[2]) || match[1],
        level: levels[match[1]] || 0,
        line: index + 1,
      });
    });
    return output;
  }

  function semanticDocumentOrder(index) {
    return new Map((index?.documents || []).map((item, position) => [item.path, position]));
  }

  function groupedSemanticItems(items, index, rowHtml) {
    const groups = new Map();
    for (const item of items) {
      const path = String(item.path || "Other");
      if (!groups.has(path)) groups.set(path, []);
      groups.get(path).push(item);
    }
    const order = semanticDocumentOrder(index);
    return [...groups.entries()]
      .sort(([left], [right]) => (
        (order.get(left) ?? 10000) - (order.get(right) ?? 10000)
        || left.localeCompare(right)
      ))
      .map(([path, values]) => window.PortalI18n.html`
        <section class="articleSemanticGroup">
          <div class="articleSemanticGroupTitle"><span title="${esc(path)}">${esc(path)}</span><small>${values.length}</small></div>
          ${values.map(rowHtml).join("")}
        </section>`).join("");
  }

  function semanticNavigationItems() {
    const index = state.semanticIndex || {};
    if (state.semanticView === "outline") {
      let headings = Array.isArray(index.headings) ? index.headings.slice() : [];
      if (state.openFile.toLowerCase().endsWith(".tex")) {
        headings = headings.filter(item => item.path !== state.openFile);
        headings.push(...parseOutline(state.source).map(item => ({
          ...item,
          path: state.openFile,
          column: 1,
        })));
      }
      return {
        items: headings,
        html: groupedSemanticItems(headings, index, item => window.PortalI18n.html`
          <button class="articleOutlineItem" style="--outline-depth:${Number(item.level || 0)}" type="button" data-editor-path="${esc(item.path)}" data-editor-line="${Number(item.line || 1)}" data-editor-column="${Number(item.column || 1)}">
            <span>${esc(item.title || item.command || "Untitled")}</span><small>L${Number(item.line || 1)}</small>
          </button>`),
        empty: state.selectedId ? "工程中还没有可导航的章节标题。" : "Select an Article project.",
      };
    }
    if (state.semanticView === "labels") {
      const items = Array.isArray(index.labels) ? index.labels : [];
      return {
        items,
        html: groupedSemanticItems(items, index, item => window.PortalI18n.html`
          <button class="articleOutlineItem" type="button" data-editor-path="${esc(item.path)}" data-editor-line="${Number(item.line || 1)}" data-editor-column="${Number(item.column || 1)}">
            <span>\\label{${esc(item.key)}}</span><small>L${Number(item.line || 1)}</small>
          </button>`),
        empty: "工程中还没有 LaTeX 标签。",
      };
    }
    if (state.semanticView === "citations") {
      const bibliography = Array.isArray(index.bibliography) ? index.bibliography : [];
      const citations = Array.isArray(index.citations) ? index.citations : [];
      const usageCounts = new Map();
      citations.forEach(item => usageCounts.set(item.key, (usageCounts.get(item.key) || 0) + 1));
      const bibliographyHtml = bibliography.length ? window.PortalI18n.html`
        <section class="articleSemanticGroup">
          <div class="articleSemanticGroupTitle"><span>文献库</span><small>${bibliography.length}</small></div>
          ${bibliography.map(item => window.PortalI18n.html`
            <button class="articleOutlineItem" type="button" data-editor-path="${esc(item.path)}" data-editor-line="${Number(item.line || 1)}" data-editor-column="${Number(item.column || 1)}" title="${esc(item.title || item.key)}">
              <span>${esc(item.key)}${item.title ? ` · ${esc(item.title)}` : ""}</span><small>${usageCounts.get(item.key) || 0} 次</small>
            </button>`).join("")}
        </section>` : "";
      const usageHtml = groupedSemanticItems(citations, index, item => window.PortalI18n.html`
        <button class="articleOutlineItem" type="button" data-editor-path="${esc(item.path)}" data-editor-line="${Number(item.line || 1)}" data-editor-column="${Number(item.column || 1)}">
          <span>\\${esc(item.command || "cite")}{${esc(item.key)}}</span><small>L${Number(item.line || 1)}</small>
        </button>`);
      return {
        items: [...bibliography, ...citations],
        html: bibliographyHtml + usageHtml,
        empty: "工程中还没有文献条目或引用。",
      };
    }
    if (state.semanticView === "figures") {
      const items = Array.isArray(index.figures) ? index.figures : [];
      return {
        items,
        html: groupedSemanticItems(items, index, item => window.PortalI18n.html`
          <button class="articleOutlineItem ${item.exists ? "" : window.PortalI18n.t("problem error")}" type="button" data-editor-path="${esc(item.path)}" data-editor-line="${Number(item.line || 1)}" data-editor-column="${Number(item.column || 1)}" title="${esc(item.resolved_path || item.target)}">
            <span>${item.exists ? "" : window.PortalI18n.t("缺失 · ")}${esc(item.target || "figure")}</span><small>L${Number(item.line || 1)}</small>
          </button>`),
        empty: "工程中还没有 includegraphics 图片引用。",
      };
    }
    const items = Array.isArray(index.problems) ? index.problems : [];
    return {
      items,
      html: groupedSemanticItems(items, index, item => window.PortalI18n.html`
        <button class="articleOutlineItem problem ${esc(item.severity || "warning")}" type="button" data-editor-path="${esc(item.path)}" data-editor-line="${Number(item.line || 1)}" data-editor-column="${Number(item.column || 1)}" title="${esc(item.message)}">
          <span>${esc(item.message || window.PortalI18n.t("LaTeX semantic problem"))}</span><small>L${Number(item.line || 1)}</small>
        </button>`),
      empty: state.semanticIndex ? "没有发现跨文件引用、标签、文献或图片问题。" : "正在建立工程语义索引…",
    };
  }

  function renderSemanticNavigation() {
    const navigation = semanticNavigationItems();
    $("articleOutlineCount").textContent = String(navigation.items.length);
    $("articleOutlineList").innerHTML = navigation.html
      || articleStateHtml(navigation.empty, { density: "inline", icon: false });
    document.querySelectorAll("[data-semantic-view]").forEach(button => {
      const active = button.dataset.semanticView === state.semanticView;
      button.classList.toggle("active", active);
      button.setAttribute("aria-selected", active ? "true" : "false");
    });
  }

  async function loadSemanticIndex() {
    if (!state.selectedId) {
      state.semanticIndex = null;
      renderSemanticNavigation();
      return;
    }
    const id = state.selectedId;
    try {
      const payload = await request(`/api/articles/${encodeURIComponent(id)}/semantic`);
      if (id !== state.selectedId) return;
      state.semanticIndex = payload;
      renderSemanticNavigation();
      updateAllEditorMarkers();
    } catch (error) {
      if (id !== state.selectedId) return;
      state.semanticIndex = null;
      renderSemanticNavigation();
      showToast(window.PortalI18n.text`工程语义索引：${error.message || error}`, "warning");
    }
  }

  function scheduleSemanticReload(delay = 150) {
    window.clearTimeout(state.semanticTimer);
    state.semanticTimer = window.setTimeout(() => {
      state.semanticTimer = 0;
      renderSemanticNavigation();
    }, Math.max(50, Number(delay) || 150));
  }

  function highlightTexLine(line) {
    let output = "";
    let plain = "";
    const flush = () => {
      if (!plain) return;
      output += esc(plain);
      plain = "";
    };
    for (let index = 0; index < line.length;) {
      const char = line[index];
      if (char === "%" && (index === 0 || line[index - 1] !== "\\")) {
        flush();
        output += window.PortalI18n.html`<span class="texComment">${esc(line.slice(index))}</span>`;
        break;
      }
      if (char === "\\") {
        const command = line.slice(index).match(/^\\[A-Za-z@]+\*?/);
        if (command) {
          flush();
          const environment = /^\\(?:begin|end)$/.test(command[0]);
          output += window.PortalI18n.html`<span class="${environment ? "texEnvironment" : "texCommand"}">${esc(command[0])}</span>`;
          index += command[0].length;
          continue;
        }
      }
      if (char === "$" && (index === 0 || line[index - 1] !== "\\")) {
        let end = index + 1;
        while (end < line.length) {
          if (line[end] === "$" && line[end - 1] !== "\\") {
            end += 1;
            break;
          }
          end += 1;
        }
        flush();
        output += window.PortalI18n.html`<span class="texMath">${esc(line.slice(index, end))}</span>`;
        index = end;
        continue;
      }
      if ("{}[]".includes(char)) {
        flush();
        output += window.PortalI18n.html`<span class="texBrace">${esc(char)}</span>`;
        index += 1;
        continue;
      }
      plain += char;
      index += 1;
    }
    flush();
    return output || " ";
  }

  function latexCharacterIsEscaped(text, index) {
    let slashes = 0;
    for (let cursor = index - 1; cursor >= 0 && text[cursor] === "\\"; cursor -= 1) slashes += 1;
    return slashes % 2 === 1;
  }

  function latexValidationLines(source) {
    const lines = String(source || "").split("\n");
    const globallyDisabled = lines.slice(0, 5).some(line => /^\s*%%novalidate(?:\s|$)/i.test(line));
    let enabled = !globallyDisabled;
    return lines.map(rawLine => {
      if (/^\s*%%begin\s+novalidate(?:\s|$)/i.test(rawLine)) {
        enabled = false;
        return "";
      }
      if (/^\s*%%end\s+novalidate(?:\s|$)/i.test(rawLine)) {
        enabled = !globallyDisabled;
        return "";
      }
      if (!enabled) return "";
      for (let index = 0; index < rawLine.length; index += 1) {
        if (rawLine[index] === "%" && !latexCharacterIsEscaped(rawLine, index)) {
          return rawLine.slice(0, index);
        }
      }
      return rawLine;
    });
  }

  function lintLatexSource(source) {
    if (!compileSettingValue("syntax_check") || !state.openFile.toLowerCase().endsWith(".tex")) return [];
    const lines = latexValidationLines(source);
    const diagnostics = [];
    const seen = new Set();
    const environments = [];
    const groups = [];
    const math = [];
    const leftRight = [];
    const verbatimNames = new Set(["verbatim", "verbatim*", "lstlisting", "minted"]);
    const mathEnvironmentNames = new Set([
      "math", "displaymath", "equation", "equation*", "align", "align*",
      "alignat", "alignat*", "gather", "gather*", "multline", "multline*",
      "flalign", "flalign*", "eqnarray", "eqnarray*", "split",
    ]);
    const mathOnlyCommands = new Set([
      "frac", "dfrac", "tfrac", "binom", "dbinom", "tbinom", "sqrt",
      "sum", "prod", "coprod", "int", "iint", "iiint", "iiiint", "oint",
      "lim", "sup", "inf", "min", "max", "sin", "cos", "tan", "cot",
      "sec", "csc", "log", "ln", "exp", "det", "gcd", "Pr",
      "alpha", "beta", "gamma", "delta", "epsilon", "varepsilon", "zeta",
      "eta", "theta", "vartheta", "iota", "kappa", "lambda", "mu", "nu",
      "xi", "pi", "varpi", "rho", "varrho", "sigma", "varsigma", "tau",
      "upsilon", "phi", "varphi", "chi", "psi", "omega", "Gamma", "Delta",
      "Theta", "Lambda", "Xi", "Pi", "Sigma", "Upsilon", "Phi", "Psi", "Omega",
      "partial", "nabla", "infty", "forall", "exists", "neg", "pm", "mp",
      "times", "div", "cdot", "ast", "star", "circ", "bullet", "oplus",
      "otimes", "leq", "geq", "neq", "approx", "sim", "equiv", "in", "notin",
      "subset", "subseteq", "supset", "supseteq", "cup", "cap", "bigcup",
      "bigcap", "to", "mapsto", "leftarrow", "rightarrow", "leftrightarrow",
      "Leftarrow", "Rightarrow", "Leftrightarrow", "vec", "hat", "widehat",
      "tilde", "widetilde", "bar", "dot", "ddot", "overline",
    ]);
    const textOnlyCommands = new Set([
      "textbf", "textmd", "textup", "textit", "textsl", "textsc",
      "emph", "chapter", "section", "subsection", "subsubsection",
      "paragraph", "subparagraph", "item", "caption", "footnote",
    ]);
    const textModeArgumentCommands = new Set([
      "text", "textrm", "textsf", "texttt", "textnormal", "mbox", "hbox", "vbox",
    ]);
    let verbatimEnvironment = "";
    const add = (line, message) => {
      const lineNumber = Math.max(1, Number(line || 1));
      const key = `${lineNumber}:${message}`;
      if (seen.has(key)) return;
      seen.add(key);
      diagnostics.push({ line: lineNumber, message });
    };
    const closeStack = (stack, expected, line, missingMessage, unexpectedMessage) => {
      const current = stack.at(-1);
      if (current?.token === expected) {
        stack.pop();
        return;
      }
      add(line, current ? missingMessage(current.token) : unexpectedMessage());
      const matching = stack.map(item => item.token).lastIndexOf(expected);
      if (matching >= 0) stack.splice(matching);
    };

    lines.forEach((line, index) => {
      const lineNumber = index + 1;
      if (!line) return;
      if (verbatimEnvironment) {
        const endPattern = new RegExp(`\\\\end\\s*\\{${verbatimEnvironment.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\}`);
        if (endPattern.test(line)) {
          const current = environments.at(-1);
          if (current?.token === verbatimEnvironment) environments.pop();
          verbatimEnvironment = "";
        }
        return;
      }

      const environmentPattern = /\\(begin|end)\s*\{([^{}]+)\}/g;
      for (const match of line.matchAll(environmentPattern)) {
        const action = match[1];
        const name = match[2].trim();
        if (action === "begin") {
          environments.push({ token: name, line: lineNumber });
          if (verbatimNames.has(name)) verbatimEnvironment = name;
        } else {
          closeStack(
            environments,
            name,
            lineNumber,
            open => `环境不匹配：应先结束 ${open}，却遇到 ${name}`,
            () => `多余的 \\end{${name}}`,
          );
        }
      }
      if (verbatimEnvironment) return;

      for (let cursor = 0; cursor < line.length; cursor += 1) {
        const char = line[cursor];
        const escaped = latexCharacterIsEscaped(line, cursor);
        if (char === "{" && !escaped) groups.push({ token: "{", line: lineNumber });
        if (char === "}" && !escaped) {
          if (groups.length) groups.pop();
          else add(lineNumber, "多余的右花括号 }");
        }

        if (char === "\\" && !escaped && ["(", ")", "[", "]"].includes(line[cursor + 1])) {
          const token = line.slice(cursor, cursor + 2);
          if (token === "\\(" || token === "\\[") {
            math.push({ token, line: lineNumber });
          } else {
            const expected = token === "\\)" ? "\\(" : "\\[";
            closeStack(
              math,
              expected,
              lineNumber,
              open => `数学定界符不匹配：${open} 未正确关闭`,
              () => `多余的数学定界符 ${token}`,
            );
          }
          cursor += 1;
          continue;
        }
        if (char === "$" && !escaped) {
          const token = line[cursor + 1] === "$" ? "$$" : "$";
          if (math.at(-1)?.token === token) math.pop();
          else math.push({ token, line: lineNumber });
          if (token === "$$") cursor += 1;
        }
      }

      for (const match of line.matchAll(/\\(left|right)\b/g)) {
        if (match[1] === "left") leftRight.push({ token: "\\left", line: lineNumber });
        else {
          closeStack(
            leftRight,
            "\\left",
            lineNumber,
            () => "\\left 与 \\right 不匹配",
            () => "多余的 \\right",
          );
        }
      }
    });

    environments.forEach(item => add(item.line, `缺少 \\end{${item.token}}`));
    groups.forEach(item => add(item.line, "缺少右花括号 }"));
    math.forEach(item => add(item.line, `数学定界符 ${item.token} 未关闭`));
    leftRight.forEach(item => add(item.line, "\\left 缺少对应的 \\right"));

    const mathContexts = [];
    const groupModeOverrides = [];
    let pendingGroupMode = null;
    let contextVerbatimEnvironment = "";
    const inMathMode = () => {
      for (let index = groupModeOverrides.length - 1; index >= 0; index -= 1) {
        if (typeof groupModeOverrides[index] === "boolean") return groupModeOverrides[index];
      }
      return mathContexts.length > 0;
    };
    const closeMathContext = (kind, token) => {
      const matching = mathContexts.map(item => `${item.kind}:${item.token}`).lastIndexOf(`${kind}:${token}`);
      if (matching >= 0) mathContexts.splice(matching, 1);
    };
    lines.forEach((line, index) => {
      const lineNumber = index + 1;
      if (!line) return;
      if (contextVerbatimEnvironment) {
        const escapedName = contextVerbatimEnvironment.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
        if (new RegExp(`\\\\end\\s*\\{${escapedName}\\}`).test(line)) contextVerbatimEnvironment = "";
        return;
      }
      const tokens = line.matchAll(/\\(?:begin|end)\s*\{[^{}]+\}|\\\(|\\\)|\\\[|\\\]|\$\$|\$|\\[A-Za-z@]+|[{}]/g);
      for (const match of tokens) {
        const token = match[0];
        if (token.startsWith("\\begin") || token.startsWith("\\end")) {
          const environmentMatch = token.match(/^\\(begin|end)\s*\{([^{}]+)\}$/);
          if (!environmentMatch) continue;
          const action = environmentMatch[1];
          const name = environmentMatch[2].trim();
          if (action === "begin" && verbatimNames.has(name)) {
            contextVerbatimEnvironment = name;
            break;
          }
          if (!mathEnvironmentNames.has(name)) continue;
          if (action === "begin") mathContexts.push({ kind: "environment", token: name });
          else closeMathContext("environment", name);
          continue;
        }
        if ((token === "$" || token === "$$") && latexCharacterIsEscaped(line, match.index)) continue;
        if (token === "$" || token === "$$" || token === "\\(" || token === "\\[") {
          const current = mathContexts.at(-1);
          if ((token === "$" || token === "$$") && current?.kind === "delimiter" && current.token === token) {
            mathContexts.pop();
          } else {
            mathContexts.push({ kind: "delimiter", token });
          }
          continue;
        }
        if (token === "\\)" || token === "\\]") {
          closeMathContext("delimiter", token === "\\)" ? "\\(" : "\\[");
          continue;
        }
        if (token === "{") {
          groupModeOverrides.push(pendingGroupMode);
          pendingGroupMode = null;
          continue;
        }
        if (token === "}") {
          groupModeOverrides.pop();
          pendingGroupMode = null;
          continue;
        }
        const command = token.slice(1);
        const mathMode = inMathMode();
        if (mathOnlyCommands.has(command) && !mathMode) {
          add(lineNumber, `数学命令 \\${command} 不能用在数学模式之外`);
        }
        if (textOnlyCommands.has(command) && mathMode) {
          add(lineNumber, `文本命令 \\${command} 不能直接用在数学模式内`);
        }
        if (textModeArgumentCommands.has(command)) pendingGroupMode = false;
        if (command === "ensuremath") pendingGroupMode = true;
      }
    });
    return diagnostics.slice(0, 100);
  }

  function normalizedDiagnosticPath(value) {
    let path = String(value || "").trim().replace(/\\/g, "/").replace(/^\.\//, "");
    const root = String(articleRoot()?.path || selectedArticle()?.canonical_root || "").replace(/\\/g, "/").replace(/\/+$/, "");
    if (root && path.startsWith(`${root}/`)) path = path.slice(root.length + 1);
    if (state.filesByPath.has(path)) return path;
    const matches = [...state.filesByPath.keys()].filter(candidate => candidate.endsWith(`/${path}`) || candidate === path);
    return matches.length === 1 ? matches[0] : path;
  }

  function monacoSeverity(value) {
    const monaco = monacoApi();
    if (!monaco) return 1;
    const severity = String(value || "").toLowerCase();
    if (severity === "error") return monaco.MarkerSeverity.Error;
    if (severity === "warning") return monaco.MarkerSeverity.Warning;
    if (severity === "hint") return monaco.MarkerSeverity.Hint;
    return monaco.MarkerSeverity.Info;
  }

  function markerRange(model, item) {
    const lineCount = Math.max(1, model.getLineCount());
    const startLineNumber = Math.max(1, Math.min(Number(item.line || 1), lineCount));
    const maxColumn = model.getLineMaxColumn(startLineNumber);
    const startColumn = Math.max(1, Math.min(Number(item.column || 1), maxColumn));
    const endLineNumber = Math.max(startLineNumber, Math.min(Number(item.end_line || startLineNumber), lineCount));
    const endMaxColumn = model.getLineMaxColumn(endLineNumber);
    const requestedEnd = Number(item.end_column || (endLineNumber === startLineNumber ? maxColumn : endMaxColumn));
    const minimumEnd = endLineNumber === startLineNumber
      ? Math.min(maxColumn, startColumn + 1)
      : 1;
    const endColumn = Math.max(minimumEnd, Math.min(requestedEnd, endMaxColumn));
    return { startLineNumber, startColumn, endLineNumber, endColumn };
  }

  function compileDiagnosticsForPath(path) {
    return (Array.isArray(state.build?.diagnostics) ? state.build.diagnostics : [])
      .filter(item => normalizedDiagnosticPath(item.file || state.settings?.primary_file) === path)
      .map(item => ({
        ...item,
        path,
        source: "LaTeX build",
        column: Number(item.column || 1),
      }));
  }

  function texlabDiagnosticsForPath(path) {
    return state.texlabDiagnostics.filter(item => item.path === path);
  }

  function semanticProblemsForPath(path) {
    return (Array.isArray(state.semanticIndex?.problems) ? state.semanticIndex.problems : [])
      .filter(item => item.path === path);
  }

  function updateEditorMarkers(path) {
    const monaco = monacoApi();
    const documentState = state.documents.get(path);
    const model = documentState?.model;
    if (!state.monacoReady || !monaco || !model || model.isDisposed?.()) return;
    const syntaxItems = path === state.openFile ? state.syntaxDiagnostics : [];
    monaco.editor.setModelMarkers(model, "article-syntax", syntaxItems.map(item => ({
      ...markerRange(model, item),
      severity: monaco.MarkerSeverity.Error,
      message: item.message,
      source: "Article syntax check",
    })));
    const compileItems = compileDiagnosticsForPath(path);
    monaco.editor.setModelMarkers(model, "article-build", compileItems.map(item => ({
      ...markerRange(model, item),
      severity: monacoSeverity(item.severity),
      message: item.message || window.PortalI18n.t("LaTeX diagnostic"),
      source: "LaTeX build",
    })));
    const texlabItems = texlabDiagnosticsForPath(path);
    monaco.editor.setModelMarkers(model, "texlab", texlabItems.map(item => ({
      ...markerRange(model, item),
      severity: monacoSeverity(item.severity),
      message: item.message || window.PortalI18n.t("TexLab diagnostic"),
      source: item.source || "TexLab",
      code: item.code || undefined,
    })));
    const semanticItems = semanticProblemsForPath(path);
    monaco.editor.setModelMarkers(model, "article-semantic", semanticItems.map(item => ({
      ...markerRange(model, item),
      severity: monacoSeverity(item.severity),
      message: item.message || window.PortalI18n.t("Project semantic problem"),
      source: "Article semantic index",
    })));
    const decorations = compileItems.map(item => {
      const range = markerRange(model, item);
      return {
        range: new monaco.Range(range.startLineNumber, 1, range.startLineNumber, 1),
        options: {
          isWholeLine: true,
          glyphMarginClassName: item.severity === "warning"
            ? "articleDiagnosticGlyph articleWarningGlyph"
            : "articleDiagnosticGlyph",
          glyphMarginHoverMessage: { value: item.message || "LaTeX diagnostic" },
        },
      };
    });
    documentState.markerDecorations = model.deltaDecorations(
      documentState.markerDecorations || [],
      decorations,
    );
  }

  function updateAllEditorMarkers() {
    for (const path of state.documents.keys()) updateEditorMarkers(path);
    renderSyntaxStatus();
  }

  function currentEditorDiagnosticCount() {
    if (!state.openFile) return 0;
    return state.syntaxDiagnostics.length
      + compileDiagnosticsForPath(state.openFile).length
      + texlabDiagnosticsForPath(state.openFile).length
      + semanticProblemsForPath(state.openFile).length;
  }

  function renderSyntaxStatus() {
    const status = $("articleSyntaxStatus");
    const count = currentEditorDiagnosticCount();
    if (!count) {
      status.hidden = true;
      status.textContent = "";
      status.title = "";
      return;
    }
    status.hidden = false;
    status.textContent = window.PortalI18n.text`${count} 个问题`;
    const details = [
      ...state.syntaxDiagnostics,
      ...compileDiagnosticsForPath(state.openFile),
      ...texlabDiagnosticsForPath(state.openFile),
      ...semanticProblemsForPath(state.openFile),
    ];
    status.title = details.slice(0, 8).map(item => window.PortalI18n.text`第 ${item.line} 行：${item.message}`).join("\n");
  }

  function renderEditorLayers() {
    window.cancelAnimationFrame(state.renderFrame);
    state.syntaxDiagnostics = lintLatexSource(state.source);
    renderSyntaxStatus();
    if (state.monacoReady) {
      updateEditorMarkers(state.openFile);
      window.clearTimeout(state.outlineTimer);
      state.outlineTimer = window.setTimeout(renderSemanticNavigation, 130);
      return;
    }
    state.renderFrame = window.requestAnimationFrame(() => {
      const lines = String(state.source || "").split("\n");
      const issuesByLine = new Map();
      state.syntaxDiagnostics.forEach(item => {
        if (!issuesByLine.has(item.line)) issuesByLine.set(item.line, []);
        issuesByLine.get(item.line).push(item.message);
      });
      $("articleSyntaxLayer").innerHTML = lines.map((line, index) => {
        const issues = issuesByLine.get(index + 1) || [];
        return window.PortalI18n.html`<span class="articleSyntaxLine ${issues.length ? "syntaxIssue" : ""}" data-syntax-line="${index + 1}" ${issues.length ? `title="${esc(issues.join("；"))}"` : ""}>${highlightTexLine(line)}</span>`;
      }).join("");
      $("articleLineNumbers").innerHTML = window.PortalI18n.html`<div class="articleLineNumbersInner">${lines.map((_line, index) => window.PortalI18n.html`<div class="articleLineNumberRow ${issuesByLine.has(index + 1) ? "syntaxIssue" : ""}">${index + 1}</div>`).join("")}</div>`;
      const syntaxLines = $("articleSyntaxLayer").querySelectorAll(".articleSyntaxLine");
      $("articleLineNumbers").querySelectorAll(".articleLineNumberRow").forEach((row, index) => {
        row.style.height = state.wordWrap ? `${Math.max(20, syntaxLines[index]?.getBoundingClientRect().height || 0)}px` : "";
      });
      syncEditorScroll();
    });
    window.clearTimeout(state.outlineTimer);
    state.outlineTimer = window.setTimeout(renderSemanticNavigation, 130);
  }

  function syncEditorScroll() {
    if (state.monacoReady) return;
    const editor = $("articleSourceEditor");
    $("articleSyntaxLayer").style.transform = `translate(${-editor.scrollLeft}px, ${-editor.scrollTop}px)`;
    const numberLayer = $("articleLineNumbers").firstElementChild;
    if (numberLayer) numberLayer.style.transform = `translateY(${-editor.scrollTop}px)`;
  }

  function updateCursorStatus() {
    if (activeDocument() && !activeDocument().textDocument) {
      $("articleCursorStatus").textContent = window.PortalI18n.t("只读预览");
      return;
    }
    const position = editorPosition();
    const selection = editorSelectionContext();
    const selected = selection?.text ? ` · 已选 ${selection.text.length} 字符` : "";
    $("articleCursorStatus").textContent = window.PortalI18n.text`Ln ${position.line}, Col ${position.column}${selected}`;
  }

  function updateDirtyState() {
    const dirty = state.openFile
      && activeDocument()?.editable
      && state.source !== state.savedSource;
    const documentState = activeDocument();
    if (documentState?.editable) {
      documentState.source = state.source;
      documentState.savedSource = state.savedSource;
      documentState.session?.updateDraft?.(state.source);
    }
    $("articleSaveFile").disabled = !state.openFile || !dirty;
    $("articleEditorState").textContent = state.openFile
      ? (
        documentState?.resourceAddress
          ? window.PortalI18n.t("Read-only Resource")
          : (documentState?.linked ? window.PortalI18n.t("Read-only linked file") : (!documentState?.editable ? window.PortalI18n.t("只读预览") : (dirty ? window.PortalI18n.t("Unsaved changes") : "Saved")))
      )
      : window.PortalI18n.t("Select a file from File tree");
    renderDocumentTabs();
    return Boolean(dirty);
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

  async function loadFiles() {
    if (!state.selectedId) return;
    const payload = await request(`/api/articles/${encodeURIComponent(state.selectedId)}/files`);
    if (payload.id !== state.selectedId) return;
    state.files = Array.isArray(payload.entries) ? payload.entries : [];
    state.filesByPath = new Map(state.files.map(entry => [String(entry.path || ""), entry]));
    renderFileTree();
    renderSettings();
  }

  async function loadSettings() {
    if (!state.selectedId) return;
    const payload = await request(`/api/articles/${encodeURIComponent(state.selectedId)}/settings`);
    if (payload.id !== state.selectedId) return;
    state.settings = payload;
    renderSettings();
    if (!payload.synctex_available) {
      $("articleSyncToPdf").disabled = true;
      $("articleSyncToPdf").title = window.PortalI18n.t("缺少 synctex；安装 Article 工具链后可在源码与 PDF 间定位");
      $("articleSyncHint").textContent = window.PortalI18n.t("缺少 synctex：无法从 PDF 定位源码");
    } else {
      $("articleSyncToPdf").title = window.PortalI18n.t("在 PDF 中定位当前源码");
      $("articleSyncHint").textContent = window.PortalI18n.t("双击 PDF 返回源码");
    }
    const id = state.selectedId;
    request(`/api/articles/${encodeURIComponent(id)}/preflight`).then(preflight => {
      if (id !== state.selectedId) return;
      state.buildPreflight = preflight;
      renderBuildPreflight();
    }).catch(error => {
      if (id !== state.selectedId) return;
      state.buildPreflight = { status: "blocked", issues: [{ severity: "error", message: error.message || String(error) }] };
      renderBuildPreflight();
    });
  }

  function renderBuildPreflight() {
    const host = $("articleBuildPreflight");
    const preflight = state.buildPreflight;
    if (!preflight) {
      host.dataset.status = "idle";
      host.textContent = window.PortalI18n.t("尚未检查构建条件");
      return;
    }
    host.dataset.status = preflight.status || "idle";
    const issues = Array.isArray(preflight.issues) ? preflight.issues : [];
    const blocked = preflight.status === "blocked";
    $("articleCompile").disabled = !state.selectedId || blocked || state.compilingArticles.has(state.selectedId);
    $("articleRecompileScratch").disabled = !state.selectedId || blocked || state.compilingArticles.has(state.selectedId);
    const reason = blocked ? issues.filter(issue => issue.severity === "error").map(issue => issue.message).join("；") : "";
    $("articleCompile").title = reason;
    $("articleRecompileScratch").title = reason;
    if (!issues.length) {
      host.innerHTML = window.PortalI18n.html`<strong>构建预检通过</strong><span>${(preflight.dependencies || []).length} 项依赖可解析</span>`;
      return;
    }
    host.innerHTML = window.PortalI18n.html`<strong>${preflight.status === "blocked" ? window.PortalI18n.t("构建条件不完整") : window.PortalI18n.t("构建前提醒")}</strong>${issues.map(issue => window.PortalI18n.html`<span>${esc(issue.message || "")}</span>`).join("")}`;
  }

  function setTexlabStatus(status, message = "") {
    state.texlabStatus = status;
    const host = $("articleLanguageStatus");
    host.classList.toggle("ready", status === "ready");
    host.classList.toggle("warning", status === "warning");
    host.textContent = status === "ready"
      ? window.PortalI18n.text`Monaco · TexLab${state.texlabDiagnostics.length ? window.PortalI18n.text` · ${state.texlabDiagnostics.length} 诊断` : ""}`
      : status === "warning"
        ? (message ? `Monaco · TexLab 不可用：${message}` : window.PortalI18n.t("Monaco · TexLab 不可用"))
        : window.PortalI18n.t("Monaco · TexLab 连接中");
    host.title = message || host.textContent;
  }

  function scheduleTexlabSync(delay = 420) {
    window.clearTimeout(state.texlabTimer);
    state.texlabTimer = 0;
    if (!state.selectedId || !state.openFile || !state.openFile.match(/\.(?:tex|bib|sty|cls)$/i)) return;
    state.texlabTimer = window.setTimeout(() => {
      state.texlabTimer = 0;
      syncTexlabDocument().catch(() => {});
    }, Math.max(40, Number(delay) || 420));
  }

  async function syncTexlabDocument({ followup = true } = {}) {
    const documentState = activeDocument();
    const model = activeEditorModel();
    if (!state.selectedId || !documentState || !documentState.path.match(/\.(?:tex|bib|sty|cls)$/i)) return;
    const id = state.selectedId;
    const path = documentState.path;
    const token = ++state.texlabVersion;
    setTexlabStatus("starting");
    try {
      const payload = await request(`/api/articles/${encodeURIComponent(id)}/texlab`, {
        method: "POST",
        body: JSON.stringify({
          action: followup ? "sync" : "diagnostics",
          path,
          content: model?.getValue?.() ?? documentState.source,
          version: Math.max(1, Number(documentState.version || model?.getVersionId?.() || 1)),
        }),
      });
      if (token !== state.texlabVersion || id !== state.selectedId) return;
      state.texlabDiagnostics = Array.isArray(payload.diagnostics) ? payload.diagnostics : [];
      setTexlabStatus(payload.available === false ? "warning" : "ready", payload.error || "");
      updateAllEditorMarkers();
      if (followup) {
        window.clearTimeout(state.texlabTimer);
        state.texlabTimer = window.setTimeout(() => {
          state.texlabTimer = 0;
          syncTexlabDocument({ followup: false }).catch(() => {});
        }, 650);
      }
    } catch (error) {
      if (token !== state.texlabVersion || id !== state.selectedId) return;
      setTexlabStatus("warning", error.message || String(error));
    }
  }

  function renderSettings() {
    const settings = state.settings;
    if (!settings) {
      $("articleCompiler").innerHTML = "";
      $("articlePrimaryFile").innerHTML = "";
      $("articleTexLive").textContent = "—";
      $("articleLatexmkPath").textContent = "—";
      $("articleEnvironmentProfile").textContent = "—";
      $("articleEnvironmentPolicy").textContent = "—";
      $("articleUseLatexmkrc").checked = false;
      $("articleShellEscape").checked = false;
      renderBuildPreflight();
      renderEditorPreferenceControls();
      renderCompileMenu();
      return;
    }
    $("articleCompiler").innerHTML = (settings.available_compilers || []).map(compiler => window.PortalI18n.html`
      <option value="${esc(compiler.id)}" ${compiler.id === settings.compiler ? "selected" : ""} ${compiler.available ? "" : "disabled"}>
        ${esc(compiler.label)}${compiler.available ? "" : " (unavailable)"}
      </option>`).join("");
    const configuredCandidates = Array.isArray(settings.primary_candidates)
      ? new Set(settings.primary_candidates.map(path => String(path || "")))
      : null;
    const texFiles = state.files.filter(entry => (
      entry.type === "file"
      && String(entry.path || "").toLowerCase().endsWith(".tex")
      && (!configuredCandidates || configuredCandidates.has(String(entry.path || "")))
    ));
    if (!texFiles.some(entry => entry.path === settings.primary_file)) {
      texFiles.unshift({ path: settings.primary_file, invalidPrimary: true });
    }
    $("articlePrimaryFile").innerHTML = texFiles.map(entry => window.PortalI18n.html`<option value="${esc(entry.path)}" ${entry.path === settings.primary_file ? "selected" : ""} ${entry.invalidPrimary ? "disabled" : ""}>${esc(entry.path)}${entry.invalidPrimary ? window.PortalI18n.t("（不是独立主文档）") : ""}</option>`).join("");
    $("articleTexLive").textContent = settings.tex_live_version || window.PortalI18n.t("TeX Live");
    $("articleLatexmkPath").textContent = settings.latexmk || "latexmk";
    $("articleEnvironmentProfile").textContent = settings.environment_profile || "—";
    $("articleEnvironmentPolicy").textContent = settings.environment_policy || "—";
    $("articleUseLatexmkrc").checked = Boolean(settings.use_project_latexmkrc);
    $("articleShellEscape").checked = Boolean(settings.shell_escape);
    renderBuildPreflight();
    renderEditorPreferenceControls();
    renderCompileMenu();
  }

  function buildCounts() {
    const fallback = { all: 0, error: 0, warning: 0, info: 0 };
    return { ...fallback, ...(state.build?.counts || {}) };
  }

  function renderBuildSummary() {
    const build = state.build || { status: "idle" };
    const counts = buildCounts();
    const status = build.status || "idle";
    const mode = build.compile_mode === "draft" ? window.PortalI18n.t("快速草稿 · 图片以空框显示") : "";
    const summary = status === "idle"
      ? window.PortalI18n.t("尚未编译")
      : status === "running"
        ? (build.compile_mode === "draft" ? window.PortalI18n.t("正在编译快速草稿…") : window.PortalI18n.t("编译中…"))
        : `${build.compiler_label || build.compiler || "LaTeX"}${mode ? ` · ${mode}` : ""} · ${formatDate(build.compiled_at)}${counts.all ? ` · ${counts.all} diagnostics` : ""}`;
    $("articleBuildSummary").textContent = summary;
    $("articleLogBadge").hidden = counts.all === 0;
    $("articleLogBadge").textContent = counts.all > 99 ? "99+" : String(counts.all);
    $("articlePdfDownload").hidden = !build.pdf_available;
    if (build.pdf_available) {
      $("articlePdfDownload").href = build.pdf_url || `/api/articles/${encodeURIComponent(state.selectedId)}/pdf`;
    }
    $("articleSyncHint").hidden = !build.pdf_available;
    $("articleSyncToPdf").disabled = !state.settings?.synctex_available || !build.pdf_available || !state.openFile.toLowerCase().endsWith(".tex");
    updateArticlePdfZoomControls();
    renderLog();
    updateAllEditorMarkers();
  }

  async function loadBuild() {
    if (!state.selectedId) return;
    const payload = await request(`/api/articles/${encodeURIComponent(state.selectedId)}/build`);
    if (payload.id !== state.selectedId) return;
    state.build = payload;
    renderBuildSummary();
  }

  function setOutputView(view) {
    state.outputView = view === "log" ? "log" : "pdf";
    $("articlePdfView").classList.toggle("active", state.outputView === "pdf");
    $("articleLogView").classList.toggle("active", state.outputView === "log");
    $("articleLogButton").classList.toggle("active", state.outputView === "log");
    $("articleCompileMenuWrap").hidden = state.outputView === "log";
    $("articleBackToPdf").hidden = state.outputView !== "log";
    updateArticlePdfZoomControls();
    if (state.outputView === "log") setCompileMenu(false);
    if (state.outputView === "pdf") renderPdf().catch(error => showPdfError(error));
    else renderLog();
  }

  function renderLogTabs() {
    const counts = buildCounts();
    const tabs = [
      ["all", "All logs"],
      ["error", "Errors"],
      ["warning", "Warnings"],
      ["info", "Info"],
    ];
    $("articleLogTabs").innerHTML = tabs.map(([id, label]) => window.PortalI18n.html`
      <button class="articleLogTab ${state.logFilter === id ? "active" : ""}" type="button" data-log-filter="${id}">
        ${label} <span>${counts[id] || 0}</span>
      </button>`).join("");
  }

  function renderLog() {
    renderLogTabs();
    const build = state.build || {};
    const diagnostics = (Array.isArray(build.diagnostics) ? build.diagnostics : [])
      .map((item, buildIndex) => ({ ...item, buildIndex }))
      .filter(item => state.logFilter === "all" || item.severity === state.logFilter);
    const body = diagnostics.length
      ? diagnostics.map(item => window.PortalI18n.html`
        <article class="articleDiagnostic ${esc(item.severity || "info")}">
          <button class="articleDiagnosticButton" type="button" data-build-diagnostic="${item.buildIndex}" ${item.file ? `data-diagnostic-file="${esc(item.file)}"` : ""} ${item.line ? `data-diagnostic-line="${Number(item.line)}"` : ""} data-portal-icon="chevron-right">
            <strong>${esc(item.message || window.PortalI18n.t("LaTeX diagnostic"))}</strong>
            <small>${esc(item.file || "")}${item.line ? `:${Number(item.line)}` : ""}</small>
          </button>
          ${item.file || item.line ? window.PortalI18n.html`<div class="articleDiagnosticMeta">${esc(item.file || state.settings?.primary_file || "")}${item.line ? ` · line ${Number(item.line)}` : ""}</div>` : ""}
          <div class="articleDiagnosticActions">
            <button type="button" data-open-build-diagnostic="${item.buildIndex}">在编辑器中打开</button>
            <button type="button" data-ask-build-diagnostic="${item.buildIndex}">询问 Agent</button>
          </div>
        </article>`).join("")
      : articleStateHtml(
        build.status === "success"
          ? "Compilation completed without diagnostics."
          : (build.status === "idle"
            ? "Compile the Article to view its LaTeX log."
            : `No ${state.logFilter === "all" ? "" : state.logFilter + " "}diagnostics.`),
      );
    const raw = build.log
      ? window.PortalI18n.html`<details class="articleRawLog"><summary>Raw LaTeX log</summary><pre>${esc(build.log)}</pre></details>`
      : "";
    $("articleDiagnostics").innerHTML = body + raw;
  }

  function buildDiagnostic(index) {
    const diagnostics = Array.isArray(state.build?.diagnostics) ? state.build.diagnostics : [];
    return diagnostics[Number(index)] || null;
  }

  async function openBuildDiagnostic(index) {
    const item = buildDiagnostic(index);
    if (!item) return;
    const path = normalizedDiagnosticPath(item.file || state.settings?.primary_file || "");
    state.activeDiagnostic = {
      severity: item.severity || "info",
      message: item.message || window.PortalI18n.t("LaTeX diagnostic"),
      path,
      line: Number(item.line || 1),
      source: "LaTeX build",
    };
    if (path && state.filesByPath.has(path)) {
      await openArticleFile(path, {
        line: Number(item.line || 1),
        column: Number(item.column || 1),
      });

    } else {
      showToast(window.PortalI18n.t("这个编译问题没有可定位的工程源文件。"), "warning");
    }
  }

  async function askBuildDiagnostic(index) {
    const item = buildDiagnostic(index);
    if (!item) return;
    const articleId = state.selectedId;
    await openBuildDiagnostic(index);
    if (articleId !== state.selectedId) return;
    const controller = await setSideView("chat");
    if (!controller || articleId !== state.selectedId) return;
    const path = normalizedDiagnosticPath(item.file || state.settings?.primary_file || "");

    const prompt = `请分析并修复这个 LaTeX 编译问题：\n${item.message || "LaTeX diagnostic"}\n位置：${path || "unknown"}:${Number(item.line || 1)}`;
    const existing = String(controller?.draftText?.() || "").trim();
    controller?.setDraftText?.(existing ? `${existing}\n\n${prompt}` : prompt);
    addArticleChatReference();

  }

  function cancelPdf() {
    window.cancelAnimationFrame(state.pdfLayoutFrame);
    articlePdfGestureFrames.cancel();
    window.clearTimeout(state.pdfRedrawTimer);
    window.clearTimeout(state.pdfGestureCommitTimer);
    state.pdfLayoutFrame = 0;
    state.pdfRedrawTimer = 0;
    state.pdfGestureCommitTimer = 0;
    state.pdfGestureAnchor = null;
    state.pdfScaleVersion += 1;
    articlePdfController?.cancelGestureScale?.();
    articlePdfController?.cancel?.();
    articlePdfController = null;
    articlePdfRenderKey = "";
  }

  function pdfUrl() {
    if (state.build?.pdf_available) {
      return state.build.pdf_url || `/api/articles/${encodeURIComponent(state.selectedId)}/pdf`;
    }
    return selectedArticle()?.compiled_pdf?.available
      ? (selectedArticle().compiled_pdf.url || `/api/articles/${encodeURIComponent(state.selectedId)}/pdf`)
      : "";
  }

  function showPdfError(error) {
    if (state.outputView !== "pdf") return;
    updateArticlePdfZoomControls({ available: false });
    $("articlePdfView").innerHTML = window.PortalI18n.html`<div class="articlePdfEmpty"><div class="articlePdfEmptyIcon">PDF</div><h2>无法渲染 PDF</h2><p>${esc(error?.message || error)}</p></div>`;
  }

  function updateArticlePdfZoomControls({ available = Boolean(pdfUrl()) } = {}) {
    const controls = $("articlePdfZoomControls");
    if (!controls) return;
    controls.hidden = state.outputView !== "pdf" || !available;
    $("articlePdfZoomValue").textContent = `${Math.round(state.pdfZoom * 100)}%`;
    $("articlePdfZoomOut").disabled = state.pdfZoom <= 0.6;
    $("articlePdfZoomIn").disabled = state.pdfZoom >= 2.4;
    $("articlePdfZoomFit").classList.toggle("active", Math.abs(state.pdfZoom - 1) < 0.001);
  }

  function captureArticlePdfPosition(point = {}) {
    const scroller = $("articlePdfView");
    if (!scroller) return null;
    const scrollerRect = scroller.getBoundingClientRect();
    const clientX = Number.isFinite(point.clientX) ? point.clientX : scrollerRect.left + (scrollerRect.width / 2);
    const clientY = Number.isFinite(point.clientY) ? point.clientY : scrollerRect.top + (scrollerRect.height / 2);
    let page = document.elementFromPoint(clientX, clientY)?.closest?.("[data-pdf-page]");
    if (!page || !scroller.contains(page)) {
      const pages = scroller.querySelectorAll("[data-pdf-page]");
      if (!pages.length) return null;
      const documentHost = $("articlePdfDocument");
      const contentY = scroller.scrollTop + clientY - scrollerRect.top - Number(documentHost?.offsetTop || 0);
      let low = 0;
      let high = pages.length - 1;
      while (low <= high) {
        const middle = Math.floor((low + high) / 2);
        const candidate = pages[middle];
        const top = candidate.offsetTop;
        const bottom = top + candidate.offsetHeight;
        if (contentY < top) high = middle - 1;
        else if (contentY > bottom) low = middle + 1;
        else {
          page = candidate;
          break;
        }
      }
      if (!page) {
        const candidates = [pages[Math.max(0, Math.min(pages.length - 1, low))], pages[Math.max(0, high)]]
          .filter(Boolean);
        page = candidates.reduce((closest, candidate) => {
          const top = candidate.offsetTop;
          const distance = contentY < top ? top - contentY : Math.max(0, contentY - top - candidate.offsetHeight);
          return !closest || distance < closest.distance ? { candidate, distance } : closest;
        }, null)?.candidate;
      }
    }
    if (!page) return null;
    const bounds = page.getBoundingClientRect();
    return {
      page: Number(page.dataset.pdfPage || 1),
      xRatio: Math.max(0, Math.min(1, (clientX - bounds.left) / Math.max(1, bounds.width))),
      yRatio: Math.max(0, Math.min(1, (clientY - bounds.top) / Math.max(1, bounds.height))),
      clientX,
      clientY,
    };
  }

  function restoreArticlePdfPosition(position) {
    if (!position) return;
    const scroller = $("articlePdfView");
    const page = scroller?.querySelector?.(`[data-pdf-page="${Number(position.page || 1)}"]`);
    if (!scroller || !page) return;
    const bounds = page.getBoundingClientRect();
    const targetX = bounds.left + (Math.max(0, Math.min(1, Number(position.xRatio ?? 0.5))) * bounds.width);
    const targetY = bounds.top + (Math.max(0, Math.min(1, Number(position.yRatio ?? 0.5))) * bounds.height);
    scroller.scrollLeft += targetX - Number(position.clientX || 0);
    scroller.scrollTop += targetY - Number(position.clientY || 0);
  }

  function scheduleArticlePdfRedraw(delay = 180) {
    window.clearTimeout(state.pdfRedrawTimer);
    const controller = articlePdfController;
    state.pdfRedrawTimer = window.setTimeout(() => {
      state.pdfRedrawTimer = 0;
      if (controller !== articlePdfController || state.outputView !== "pdf") return;
      controller?.redraw?.().catch(error => showToast(window.PortalI18n.text`PDF redraw: ${error.message || error}`, "warning"));
    }, delay);
  }

  async function previewArticlePdfScale(point = {}) {
    const controller = articlePdfController;
    if (!controller?.previewScale) return false;
    const scaleVersion = ++state.pdfScaleVersion;
    await controller.promise;
    if (controller !== articlePdfController || scaleVersion !== state.pdfScaleVersion) return false;
    const position = captureArticlePdfPosition(point);
    const changed = controller.previewScale({ scaleMultiplier: state.pdfZoom });
    if (changed) restoreArticlePdfPosition(position);
    return changed;
  }

  function scheduleArticlePdfLayout() {
    window.cancelAnimationFrame(state.pdfLayoutFrame);
    state.pdfLayoutFrame = window.requestAnimationFrame(() => {
      state.pdfLayoutFrame = 0;
      previewArticlePdfScale().then(changed => {
        if (changed) scheduleArticlePdfRedraw(220);
      }).catch(() => {});
    });
  }

  function scheduleArticleEditorLayout() {
    window.cancelAnimationFrame(state.editorLayoutFrame);
    state.editorLayoutFrame = window.requestAnimationFrame(() => {
      state.editorLayoutFrame = 0;
      state.editor?.layout?.();
    });
  }

  async function setArticlePdfZoom(value, point = {}) {
    window.clearTimeout(state.pdfGestureCommitTimer);
    state.pdfGestureCommitTimer = 0;
    state.pdfGestureAnchor = null;
    articlePdfGestureFrames.cancel();
    articlePdfController?.cancelGestureScale?.();
    const next = Math.round(Math.max(0.6, Math.min(2.4, Number(value) || 1)) * 100) / 100;
    if (Math.abs(next - state.pdfZoom) < 0.001) return;
    state.pdfZoom = next;
    updateArticlePdfZoomControls();
    if (await previewArticlePdfScale(point)) {
      const version = String(state.build?.compiled_at || selectedArticle()?.compiled_pdf?.mtime_ns || "");
      articlePdfRenderKey = `${state.selectedId}|${pdfUrl()}|${version}|${state.pdfZoom}`;
      scheduleArticlePdfRedraw();
      return;
    }
    await renderPdf({ preservePosition: true });
  }

  function previewArticlePdfGestureScale(value, point = {}) {
    const controller = articlePdfController;
    if (!controller?.previewGestureScale) return false;
    const next = Math.round(Math.max(0.6, Math.min(2.4, Number(value) || 1)) * 100) / 100;
    if (!state.pdfGestureAnchor) state.pdfGestureAnchor = captureArticlePdfPosition(point);
    state.pdfZoom = next;
    updateArticlePdfZoomControls();
    return controller.previewGestureScale({ scaleMultiplier: next, point });
  }

  async function commitArticlePdfGestureScale() {
    window.clearTimeout(state.pdfGestureCommitTimer);
    state.pdfGestureCommitTimer = 0;
    await Promise.resolve(articlePdfGestureFrames.flush());
    const controller = articlePdfController;
    const position = state.pdfGestureAnchor;
    state.pdfGestureAnchor = null;
    if (!controller?.commitGestureScale) return false;
    const changed = controller.commitGestureScale({ scaleMultiplier: state.pdfZoom });
    if (changed) restoreArticlePdfPosition(position);
    const version = String(state.build?.compiled_at || selectedArticle()?.compiled_pdf?.mtime_ns || "");
    articlePdfRenderKey = `${state.selectedId}|${pdfUrl()}|${version}|${state.pdfZoom}`;
    if (changed) scheduleArticlePdfRedraw(80);
    return changed;
  }

  function scheduleArticlePdfGestureCommit(delay = 110) {
    window.clearTimeout(state.pdfGestureCommitTimer);
    state.pdfGestureCommitTimer = window.setTimeout(() => {
      state.pdfGestureCommitTimer = 0;
      commitArticlePdfGestureScale().catch(error => showPdfError(error));
    }, delay);
  }

  function scheduleArticlePdfGestureZoom(value, point = {}) {
    articlePdfGestureFrames.schedule({ value, point });
  }

  function bindArticlePdfGestures() {
    const host = $("articlePdfView");
    const touchDistance = touches => Math.hypot(
      touches[0].clientX - touches[1].clientX,
      touches[0].clientY - touches[1].clientY,
    );
    const touchMidpoint = touches => ({
      clientX: (touches[0].clientX + touches[1].clientX) / 2,
      clientY: (touches[0].clientY + touches[1].clientY) / 2,
    });
    host.addEventListener("wheel", event => {
      if (!event.ctrlKey || state.outputView !== "pdf" || !pdfUrl()) return;
      event.preventDefault();
      const factor = Math.exp(-event.deltaY * 0.01);
      const queuedZoom = Number(articlePdfGestureFrames.pending()?.value || state.pdfZoom);
      scheduleArticlePdfGestureZoom(queuedZoom * factor, {
        clientX: event.clientX,
        clientY: event.clientY,
      });
      scheduleArticlePdfGestureCommit();
    }, { passive: false });
    host.addEventListener("touchstart", event => {
      if (event.touches.length !== 2 || state.outputView !== "pdf" || !pdfUrl()) return;
      event.preventDefault();
      state.pdfTouchGesture = {
        distance: Math.max(1, touchDistance(event.touches)),
        zoom: state.pdfZoom,
      };
    }, { passive: false });
    host.addEventListener("touchmove", event => {
      if (event.touches.length !== 2 || !state.pdfTouchGesture) return;
      event.preventDefault();
      const scale = touchDistance(event.touches) / state.pdfTouchGesture.distance;
      scheduleArticlePdfGestureZoom(state.pdfTouchGesture.zoom * scale, touchMidpoint(event.touches));
    }, { passive: false });
    const finishTouchGesture = () => {
      state.pdfTouchGesture = null;
      commitArticlePdfGestureScale().catch(error => showPdfError(error));
    };
    host.addEventListener("touchend", event => {
      if (event.touches.length < 2) finishTouchGesture();
    }, { passive: true });
    host.addEventListener("touchcancel", finishTouchGesture, { passive: true });

    host.addEventListener("gesturestart", event => {
      if (state.outputView !== "pdf" || !pdfUrl()) return;
      event.preventDefault();
      state.pdfTouchGesture = { zoom: state.pdfZoom, distance: 1 };
    }, { passive: false });
    host.addEventListener("gesturechange", event => {
      if (!state.pdfTouchGesture) return;
      event.preventDefault();
      scheduleArticlePdfGestureZoom(state.pdfTouchGesture.zoom * Number(event.scale || 1), {
        clientX: event.clientX,
        clientY: event.clientY,
      });
    }, { passive: false });
    host.addEventListener("gestureend", finishTouchGesture, { passive: true });
  }

  async function renderPdf({ preservePosition = false } = {}) {
    if (state.outputView !== "pdf") return;
    const host = $("articlePdfView");
    if (state.buildLoading) {
      host.innerHTML = window.PortalI18n.markup('<div class="articlePdfStatus">正在读取构建状态…</div>');
      return;
    }
    const url = pdfUrl();
    if (!url) {
      cancelPdf();
      updateArticlePdfZoomControls({ available: false });
      host.innerHTML = window.PortalI18n.html`<div class="articlePdfEmpty"><div class="articlePdfEmptyIcon">PDF</div><h2>尚无 PDF</h2><p>编译 Article 后将在此显示文档。</p></div>`;
      return;
    }
    updateArticlePdfZoomControls({ available: true });
    const version = String(state.build?.compiled_at || selectedArticle()?.compiled_pdf?.mtime_ns || "");
    const renderKey = `${state.selectedId}|${url}|${version}|${state.pdfZoom}`;
    if (articlePdfController && articlePdfRenderKey === renderKey && $("articlePdfDocument")) {
      await articlePdfController.promise;
      return;
    }
    const readingPosition = preservePosition ? captureArticlePdfPosition() : null;
    cancelPdf();
    host.innerHTML = window.PortalI18n.html`<div class="articlePdfDocument" id="articlePdfDocument"><div class="articlePdfStatus" id="articlePdfStatus">正在加载 PDF…</div></div>`;
    const documentHost = $("articlePdfDocument");
    articlePdfController = window.PortalPdfPreview.render({
      root: documentHost,
      url,
      title: selectedArticle()?.title || window.PortalI18n.t("Article"),
      version,
      statusClass: "articlePdfStatus",
      pageClass: "articlePdfPage",
      textLayerClass: "articlePdfText",
      horizontalPadding: 12,
      minScale: 0.45,
      maxScale: 4,
      scaleMultiplier: state.pdfZoom,
      outputScale: ratio => Math.min(2, ratio),
      onPage: ({ pageNumber, pageElement }) => {
        pageElement.dataset.pageLabel = `第 ${pageNumber} 页 · 双击返回源码`;
      },
      onError: error => showToast(window.PortalI18n.text`PDF.js fallback: ${error.message || error}`, "warning"),
    });
    const controller = articlePdfController;
    articlePdfRenderKey = renderKey;
    await controller.promise;
    if (articlePdfController === controller && readingPosition?.page) {
      await controller.ensurePage?.(readingPosition.page, { render: true });
    }
    if (articlePdfController === controller) restoreArticlePdfPosition(readingPosition);
  }

  function revealPdfSyncMatch(match) {
    const page = Math.max(1, Number(match?.page || 1));
    const pageElement = $("articlePdfDocument")?.querySelector?.(`[data-pdf-page="${page}"]`);
    if (!pageElement) return false;
    pageElement.querySelectorAll(".articlePdfSyncHighlight").forEach(item => item.remove());
    const scale = Math.max(.01, Number(pageElement.dataset.pdfScale || 1));
    const left = Math.max(0, Number(match.h ?? match.x ?? 0) * scale);
    const top = Math.max(0, Number(match.v ?? match.y ?? 0) * scale);
    const width = Math.max(18, Number(match.width || 0) * scale);
    const height = Math.max(10, Number(match.height || 0) * scale);
    const marker = document.createElement("div");
    marker.className = "articlePdfSyncHighlight";
    marker.style.left = `${Math.min(left, pageElement.clientWidth - 8)}px`;
    marker.style.top = `${Math.min(top, pageElement.clientHeight - 8)}px`;
    marker.style.width = `${Math.min(width, Math.max(18, pageElement.clientWidth - left))}px`;
    marker.style.height = `${Math.min(height, Math.max(10, pageElement.clientHeight - top))}px`;
    pageElement.appendChild(marker);
    pageElement.scrollIntoView({ behavior: "smooth", block: "center", inline: "nearest" });
    return true;
  }

  async function syncSourceToPdf() {
    if (state.pdfSyncPending || !state.settings?.synctex_available || !state.selectedId || !state.openFile) return;
    const position = editorPosition();
    state.pdfSyncPending = true;
    $("articleSyncToPdf").disabled = true;
    try {
      const payload = await request(`/api/articles/${encodeURIComponent(state.selectedId)}/synctex/source-to-pdf`, {
        method: "POST",
        body: JSON.stringify({
          path: state.openFile,
          line: position.line,
          column: position.column,
          page_hint: Number(state.pdfSyncLocation?.page || 0),
        }),
      });
      state.pdfSyncLocation = {
        page: Number(payload.match?.page || 0),
        x: Number(payload.match?.x || 0),
        y: Number(payload.match?.y || 0),
        source_path: state.openFile,
        source_line: position.line,
      };
      if (state.outputView !== "pdf") setOutputView("pdf");
      await renderPdf();
      await articlePdfController?.ensurePage?.(payload.match?.page, { render: true });
      if (!revealPdfSyncMatch(payload.match)) {
        throw new Error("PDF 页面尚未完成渲染");
      }

    } catch (error) {
      showToast(window.PortalI18n.text`源码 → PDF：${error.message || error}`, "warning");
    } finally {
      state.pdfSyncPending = false;
      $("articleSyncToPdf").disabled = !state.settings?.synctex_available || !pdfUrl() || !state.openFile.toLowerCase().endsWith(".tex");
    }
  }

  async function syncPdfToSource(pageElement, event) {
    if (state.pdfSyncPending || !state.settings?.synctex_available || !state.selectedId || !pageElement) return;
    const page = Number(pageElement.dataset.pdfPage || 1);
    const scale = Math.max(.01, Number(pageElement.dataset.pdfScale || 1));
    const rect = pageElement.getBoundingClientRect();
    const x = Math.max(0, (event.clientX - rect.left) / scale);
    const y = Math.max(0, (event.clientY - rect.top) / scale);
    state.pdfSyncPending = true;
    try {
      const payload = await request(`/api/articles/${encodeURIComponent(state.selectedId)}/synctex/pdf-to-source`, {
        method: "POST",
        body: JSON.stringify({ page, x, y }),
      });
      const match = payload.match || {};
      state.pdfSyncLocation = {
        page,
        x,
        y,
        source_path: match.path || "",
        source_line: Number(match.line || 1),
      };
      await openArticleFile(match.path, {
        line: Number(match.line || 1),
        column: Number(match.column || 1),
      });
      showToast(window.PortalI18n.text`已定位到 ${match.path}:${Number(match.line || 1)}`, "success");

    } catch (error) {
      showToast(window.PortalI18n.text`PDF → 源码：${error.message || error}`, "warning");
    } finally {
      state.pdfSyncPending = false;
    }
  }

  function scheduleAutoCompile(delay = 2500) {
    window.clearTimeout(state.autoCompileTimer);
    state.autoCompileTimer = 0;
    if (!compileSettingValue("auto_compile") || !state.selectedId || !state.openFile) return;
    state.autoCompileTimer = window.setTimeout(() => {
      state.autoCompileTimer = 0;
      if (state.compilingArticles.has(state.selectedId)) {
        state.autoCompileQueued = true;
        return;
      }
      compileArticle({ automatic: true });
    }, Math.max(150, Number(delay) || 2500));
  }

  async function updateCompilePreference(button) {
    if (!state.selectedId || button.disabled) return;
    const key = button.dataset.compileSetting;
    const raw = button.dataset.compileValue;
    const value = raw === "true" ? true : raw === "false" ? false : raw;
    if (compileSettingValue(key) === value) return;
    button.disabled = true;
    try {
      state.settings = await request(`/api/articles/${encodeURIComponent(state.selectedId)}/settings`, {
        method: "POST",
        body: JSON.stringify({ [key]: value }),
      });
      renderSettings();
      if (key === "syntax_check") renderEditorLayers();
      if (key === "auto_compile" && value) scheduleAutoCompile();
      if (key === "auto_compile" && !value) {
        window.clearTimeout(state.autoCompileTimer);
        state.autoCompileTimer = 0;
        state.autoCompileQueued = false;
      }
    } catch (error) {
      showToast(error.message || error, "error");
      renderCompileMenu();
    } finally {
      button.disabled = false;
    }
  }

  async function updateEditorCodeCheck(input) {
    const enabled = Boolean(input.checked);
    if (!state.selectedId) {
      renderEditorPreferenceControls();
      return;
    }
    input.disabled = true;
    try {
      state.settings = await request(`/api/articles/${encodeURIComponent(state.selectedId)}/settings`, {
        method: "POST",
        body: JSON.stringify({ syntax_check: enabled }),
      });
      renderSettings();
      renderEditorLayers();
    } catch (error) {
      showToast(error.message || error, "error");
      renderEditorPreferenceControls();
    } finally {
      input.disabled = false;
    }
  }

  async function stopCompilation() {
    if (!state.selectedId || !state.compilingArticles.has(state.selectedId) || state.stopRequested) return;
    const articleId = state.selectedId;
    state.stopRequested = true;
    renderCompileMenu();
    try {
      const result = await request(`/api/articles/${encodeURIComponent(articleId)}/stop`, {
        method: "POST",
        body: "{}",
      });
      if (articleId !== state.selectedId) return;
      if (result.stop_requested) showToast(window.PortalI18n.t("正在停止 LaTeX 编译…"), "warning");
      else {
        state.stopRequested = false;
        renderCompileMenu();
        showToast(window.PortalI18n.t("当前没有正在运行的编译。"), "warning");
      }
    } catch (error) {
      if (articleId !== state.selectedId) return;
      state.stopRequested = false;
      renderCompileMenu();
      showToast(error.message || error, "error");
    }
  }

  async function compileArticle({ fromScratch = false, automatic = false } = {}) {
    if (!state.selectedId) return;
    if (state.buildPreflight?.status === "blocked") {
      if (!automatic) showToast(window.PortalI18n.t("构建条件不完整，请查看编译设置中的预检提示。"), "warning");
      return;
    }
    const articleId = state.selectedId;
    if (state.compilingArticles.has(state.selectedId)) {
      if (automatic) state.autoCompileQueued = true;
      return;
    }
    setCompileMenu(false);
    if (hasDirtyDocuments() && !(await saveAllDirtyDocuments({ silent: true }))) return;
    if (articleId !== state.selectedId || state.compilingArticles.has(articleId)) return;
    state.compilingArticles.add(articleId);
    state.stopRequested = false;
    const compileButtons = [$("articleCompile")];
    compileButtons.forEach(button => {
      button.disabled = true;
      button.dataset.previousLabel = button.textContent;
      button.textContent = window.PortalI18n.t("编译中…");
    });
    state.build = {
      ...(state.build || {}),
      id: state.selectedId,
      status: "running",
      compile_mode: compileSettingValue("compile_mode"),
      summary: "正在编译 PDF…",
    };
    renderBuildSummary();
    renderCompileMenu();
    try {
      const result = await request(`/api/articles/${encodeURIComponent(articleId)}/compile`, {
        method: "POST",
        timeoutMs: 180000,
        body: JSON.stringify({ from_scratch: Boolean(fromScratch) }),
      });
      if (articleId !== state.selectedId) return;
      state.build = { ...result, pdf_available: true, pdf_url: result.pdf_url };
      renderBuildSummary();
      setOutputView("pdf");
      await loadArticles({ preserve: true, reloadSelected: false });
      if (articleId !== state.selectedId) return;
      if (!automatic) {
        const message = result.compile_mode === "draft"
          ? window.PortalI18n.t("快速草稿已生成；图片已替换为空框。")
          : (result.warnings ? window.PortalI18n.t("PDF 已生成；LaTeX 留下了需要检查的诊断。") : window.PortalI18n.t("PDF 编译完成。"));
        showToast(message, result.warnings ? "warning" : "success");
      }
    } catch (error) {
      if (articleId !== state.selectedId) return;
      try { await loadBuild(); } catch (_) {}
      if (articleId !== state.selectedId) return;
      setOutputView("log");
      showToast(state.stopRequested ? window.PortalI18n.t("LaTeX 编译已停止。") : (error.message || window.PortalI18n.t("LaTeX compilation failed")), state.stopRequested ? "warning" : "error");
    } finally {
      state.compilingArticles.delete(articleId);
      if (articleId !== state.selectedId) return;
      state.stopRequested = false;
      compileButtons.forEach(button => {
        button.disabled = !state.selectedId;
        button.textContent = button.dataset.previousLabel || window.PortalI18n.t("重新编译");
      });
      renderCompileMenu();
      if (state.autoCompileQueued) {
        state.autoCompileQueued = false;
        scheduleAutoCompile(250);
      }
    }
  }

  async function saveSettings(event) {
    event.preventDefault();
    if (!state.selectedId) return;
    const button = event.currentTarget.querySelector('button[type="submit"]');
    button.disabled = true;
    try {
      state.settings = await request(`/api/articles/${encodeURIComponent(state.selectedId)}/settings`, {
        method: "POST",
        body: JSON.stringify({
          compiler: $("articleCompiler").value,
          primary_file: $("articlePrimaryFile").value,
          use_project_latexmkrc: $("articleUseLatexmkrc").checked,
          shell_escape: $("articleShellEscape").checked,
        }),
      });
      renderSettings();
      await loadFiles();
      await loadSemanticIndex();
      showToast(window.PortalI18n.t("LaTeX 编译配置已保存。"), "success");
    } catch (error) {
      showToast(error.message || error, "error");
    } finally {
      button.disabled = false;
    }
  }

  function highlightSearchPreview(preview, needle) {
    const source = String(preview || "");
    const tokens = [...new Set(String(needle || "").trim().split(/\s+/).filter(Boolean))]
      .sort((left, right) => right.length - left.length);
    if (!tokens.length) return esc(source);
    const lowerSource = source.toLowerCase();
    const lowerTokens = tokens.map(token => token.toLowerCase());
    let cursor = 0;
    let output = "";
    while (cursor < source.length) {
      let matchIndex = -1;
      let matchLength = 0;
      lowerTokens.forEach(token => {
        const index = lowerSource.indexOf(token, cursor);
        if (index < 0) return;
        if (matchIndex < 0 || index < matchIndex || (index === matchIndex && token.length > matchLength)) {
          matchIndex = index;
          matchLength = token.length;
        }
      });
      if (matchIndex < 0) {
        output += esc(source.slice(cursor));
        break;
      }
      output += window.PortalI18n.html`${esc(source.slice(cursor, matchIndex))}<mark>${esc(source.slice(matchIndex, matchIndex + matchLength))}</mark>`;
      cursor = matchIndex + matchLength;
    }
    return output || esc(source);
  }

  function renderSearchResults(results, needle) {
    const groups = new Map();
    results.forEach(result => {
      const path = String(result.path || "Unknown file");
      if (!groups.has(path)) groups.set(path, []);
      groups.get(path).push(result);
    });
    $("articleSearchResults").innerHTML = window.PortalI18n.html`
      <div class="articleSearchSummary"><strong>${results.length} 个结果</strong><span>位于 ${groups.size} 个文件</span></div>
      ${[...groups.entries()].map(([path, fileResults]) => {
        const collapsed = state.collapsedSearchFiles.has(path);
        return window.PortalI18n.html`<section class="articleSearchGroup ${collapsed ? "collapsed" : ""}" data-search-group-section="${esc(path)}">
          <button class="articleSearchGroupHead" type="button" data-search-group="${esc(path)}" aria-expanded="${collapsed ? "false" : "true"}" data-portal-icon="chevron-right">
            <span title="${esc(path)}">${esc(path)}</span><small>${fileResults.length}</small>
          </button>
          <div class="articleSearchGroupBody">
            ${fileResults.map(result => window.PortalI18n.html`
              <button class="articleSearchResult" type="button" data-search-file="${esc(path)}" data-search-line="${Number(result.line || 1)}" data-search-column="${Number(result.column || 1)}">
                <span class="articleSearchLine">${Number(result.line || 1)}</span>
                <span class="articleSearchPreview">${highlightSearchPreview(result.preview, needle)}</span>
              </button>`).join("")}
          </div>
        </section>`;
      }).join("")}`;
  }

  async function searchProject(event) {
    event.preventDefault();
    if (!state.selectedId) return;
    const search = $("articleGlobalSearchInput");
    const needle = search.value.trim();
    if (!needle) {
      $("articleSearchResults").innerHTML = articleStateHtml("Enter text to search the current Article project.");
      return;
    }
    $("articleSearchResults").innerHTML = articleStateHtml("Searching…", { state: "loading" });
    try {
      const payload = await request(`/api/articles/${encodeURIComponent(state.selectedId)}/search?q=${encodeURIComponent(needle)}`);
      const results = Array.isArray(payload.results) ? payload.results : [];
      state.collapsedSearchFiles.clear();
      if (results.length) renderSearchResults(results, needle);
      else $("articleSearchResults").innerHTML = articleStateHtml(`没有“${needle}”的搜索结果。`);
    } catch (error) {
      $("articleSearchResults").innerHTML = articleStateHtml(error.message || error, { state: "error" });
    }
  }

  function currentDirectory() {
    if (state.selectedDirectory && state.filesByPath.get(state.selectedDirectory)?.type === "directory") return state.selectedDirectory;
    if (!state.openFile.includes("/")) return "";
    return state.openFile.slice(0, state.openFile.lastIndexOf("/"));
  }

  function openEntryModal(kind) {
    if (!state.selectedId) return;
    state.entryKind = kind;
    $("articleEntryKind").value = kind;
    $("articleEntryEyebrow").textContent = kind === "directory" ? window.PortalI18n.t("NEW FOLDER") : window.PortalI18n.t("NEW FILE");
    $("articleEntryTitle").textContent = kind === "directory" ? window.PortalI18n.t("创建新文件夹") : window.PortalI18n.t("创建新文件");
    const prefix = currentDirectory();
    $("articleEntryPath").value = prefix ? `${prefix}/` : "";
    $("articleEntryPath").placeholder = kind === "directory" ? "sections" : "sections/method.tex";
    articleEntryDialog.open();
    window.setTimeout(() => {
      const input = $("articleEntryPath");
      input.focus();
      input.setSelectionRange(input.value.length, input.value.length);
    }, 0);
  }

  async function createEntry(event) {
    event.preventDefault();
    const formElement = event.currentTarget;
    const path = $("articleEntryPath").value.trim();
    const kind = $("articleEntryKind").value;
    if (!path || !state.selectedId) return;
    const submit = formElement.querySelector('button[type="submit"]');
    submit.disabled = true;
    try {
      const result = await request(`/api/articles/${encodeURIComponent(state.selectedId)}/entries`, {
        method: "POST",
        body: JSON.stringify({ path, kind }),
      });
      articleEntryDialog.close();
      formElement.reset();
      await loadFiles();
      await loadSemanticIndex();
      if (kind === "file") await openArticleFile(result.path);
      else {
        state.selectedDirectory = result.path;
        state.collapsedDirectories.delete(result.path);
        renderFileTree();
      }
      showToast(window.PortalI18n.text`${result.path} 已创建`, "success");
    } catch (error) {
      showToast(error.message || error, "error");
    } finally {
      submit.disabled = false;
    }
  }

  async function uploadFile(file) {
    if (!file || !state.selectedId) return;
    const form = new FormData();
    form.append("file", file, file.name);
    form.append("directory", currentDirectory());
    try {
      const result = await request(`/api/articles/${encodeURIComponent(state.selectedId)}/upload`, {
        method: "POST",
        body: form,
      });
      await loadFiles();
      await loadSemanticIndex();
      const entry = state.filesByPath.get(result.path);
      if (entry?.editable) await openArticleFile(result.path);
      showToast(window.PortalI18n.text`${result.path} 已上传`, "success");
    } catch (error) {
      showToast(error.message || error, "error");
    } finally {
      $("articleUploadInput").value = "";
    }
  }

  function articleChatContext() {
    const article = selectedArticle();
    const documentState = activeDocument();
    const cursor = editorPosition();
    const selection = editorSelectionContext();
    return {
      article_id: state.selectedId,
      title: article?.title || "",
      root: articleRoot(article)?.path || article?.canonical_root || "",
      project_ids: articleProjects(article),
      file: {
        path: state.openFile,
        revision: documentState?.revision || "",
        dirty: documentIsDirty(documentState),
        language: languageForPath(state.openFile),
        cursor,
        selection: selection || {
          start_line: cursor.line,
          start_column: cursor.column,
          end_line: cursor.line,
          end_column: cursor.column,
          text: "",
          truncated: false,
        },
      },
      diagnostic: state.activeDiagnostic || {},
      pdf: state.pdfSyncLocation || {},
      build: state.build ? {
        status: state.build.status || "",
        compiler: state.build.compiler || "",
        compiled_at: state.build.compiled_at || "",
        counts: {
          error: Number(state.build.counts?.error || 0),
          warning: Number(state.build.counts?.warning || 0),
          info: Number(state.build.counts?.info || 0),
        },
      } : {},
    };
  }

  function addArticleChatReference() {
    const context = articleChatContext();
    const selection = context.file?.selection || {};
    const startLine = selection.start_line || context.file?.cursor?.line || 1;
    const endLine = selection.end_line || startLine;
    return state.chatController?.addReference({
      title: `${state.selectedId} L${startLine}–${endLine}`,
      path: state.selectedId,
      text: JSON.stringify(context, null, 2),
      line_start: startLine,
      line_end: endLine,
    });
  }

  async function bringCurrentContextToChat() {
    if (!state.selectedId) return;
    const articleId = state.selectedId;
    const selection = editorSelectionContext();
    const controller = await setSideView("chat");
    if (!controller || articleId !== state.selectedId) return;

    const prompt = selection?.text
      ? "请结合已附带的稿件选区，帮我审阅并给出可以直接落到 LaTeX 源码的修改建议。"
      : "请结合已附带的当前稿件位置和工程上下文，继续协助我完善这篇 Article。";
    const existing = String(controller?.draftText?.() || "").trim();
    if (!existing) controller?.setDraftText?.(prompt);
    addArticleChatReference();

  }

  function initChatWorkspace() {
    if (state.chatController) return state.chatController;
    const root = $("articleChatWorkspace");
    state.chatPanel = window.PortalWorkbench.createPanel("chat", {
      root,
      id: "articleWorkspaceChat",
      autoLoad: false,
      refsScope: "articleWorkspaceChat",
      title: window.PortalI18n.t("Chat Workspace"),
      draftOwner: () => $("portalUsername")?.textContent || "anonymous",
      resolveResident: target => window.PortalAPI.get(`/knowledge/api/item-chat?kb_id=${encodeURIComponent(target.kb_id || "")}`),
      resolveBeforeSend: (target, payload) => window.PortalAPI.post("/knowledge/api/item-chat/prepare", {
        kb_id: target.kb_id, message: payload.text || payload.plainText,
      }),
      initialContext: (target, detail) => ({
        context_text: `${detail.initial_context?.context_text || ""}\nArticle: ${target.kb_id}\n工程目录：${articleRoot(selectedArticle())?.path || selectedArticle()?.canonical_root || ""}`,
      }),
      enableLinkPreview: true,
      emptyText: window.PortalI18n.t("可以围绕当前 Article 的结构、论证、LaTeX 修改或编译问题和 Agent 讨论。"),
      onError: error => showToast(error.message || error, "error"),
    });
    state.chatController = state.chatPanel?.workspace || null;

    return state.chatController;
  }

  async function loadArticleChat({ force = false } = {}) {
    if (!state.selectedId) {
      initChatWorkspace();
      state.chatPanel?.showEmpty?.({
        emptyText: window.PortalI18n.t("Select an Article project to start a Chat Workspace."),
        placeholder: window.PortalI18n.t("Select an Article project to start a Chat Workspace."),
      });
      return null;
    }
    const id = state.selectedId;
    if (!force && state.chatLoadRequest?.id === id) return state.chatLoadRequest.promise;
    initChatWorkspace();
    const panel = state.chatPanel;
    const request = { id, promise: null };
    state.chatLoadRequest = request;
    request.promise = Promise.resolve(panel?.openSurface?.({ kb_id: id }, { force }))
      .then(detail => detail && id === state.selectedId ? state.chatController : null)
      .finally(() => {
        if (state.chatLoadRequest === request) state.chatLoadRequest = null;
      });
    return request.promise;
  }

  function clearSelectedArticle() {
    cancelPdf();
    window.clearTimeout(state.texlabTimer);
    state.texlabTimer = 0;
    state.texlabVersion += 1;
    state.texlabDiagnostics = [];
    setTexlabStatus(state.monacoReady ? "starting" : "warning");
    window.clearTimeout(state.semanticTimer);
    state.semanticTimer = 0;
    state.semanticIndex = null;
    state.activeDiagnostic = null;
    state.pdfSyncLocation = null;
    state.chatLoadRequest = null;


    state.chatPanel?.closeSurface?.();
    state.chatPanel?.showEmpty?.({
      emptyText: window.PortalI18n.t("Select an Article project to start a Chat Workspace."),
      placeholder: window.PortalI18n.t("Select an Article project to start a Chat Workspace."),
    });
    window.clearTimeout(state.autoCompileTimer);
    state.autoCompileTimer = 0;
    state.autoCompileQueued = false;
    state.files = [];
    state.filesByPath.clear();
    state.linkedFiles = new Map();
    state.linkedFileProbes = new Set();
    state.settings = null;
    state.build = null;
    state.selectedDirectory = "";
    clearEditor();
    renderFileTree();
    renderSettings();
    renderBuildSummary();
    renderSemanticNavigation();

    setOutputView("pdf");
  }

  async function selectArticle(articleId, { initial = false, updateHistory = true } = {}) {
    const id = String(articleId || "");
    if (id === state.selectedId && !initial) {
      setProjectMenu(false);
      return true;
    }
    if (hasDirtyDocuments() && !await window.PortalUI.confirm({
      title: window.PortalI18n.t("切换 Article？"),
      message: window.PortalI18n.t("当前打开的文件中有未保存修改。切换 Article 会放弃这些修改。"),
      confirmLabel: window.PortalI18n.t("切换并放弃修改"),
      tone: "danger",
    })) return false;
    if (updateHistory) {
      if (initial) updateUrl(id);
      else detailHistory.open(id);
    }
    state.selectedId = id;
    state.buildPreflight = null;
    state.stopRequested = false;
    state.autoCompileQueued = false;
    window.clearTimeout(state.autoCompileTimer);
    $("articleCompile").textContent = state.compilingArticles.has(id) ? window.PortalI18n.t("编译中…") : window.PortalI18n.t("重新编译");
    state.buildLoading = Boolean(id);
    articleGitRefresh.stop();
    articleGitHistoryRequested = false;
    state.gitStatus = null;
    state.gitHistory = [];
    renderArticleGitBadge();
    closeArticleGitDiff();
    persistArticleSelection(id);
    setProjectMenu(false);
    clearSelectedArticle();
    renderProjectIdentity();
    if (!id) return true;
    try {
      // Only files/settings determine which document can be opened.
      loadBuild().catch(error => {
        if (id === state.selectedId) showToast(window.PortalI18n.text`构建状态：${error.message || error}`, "warning");
      }).finally(() => {
        if (id !== state.selectedId) return;
        state.buildLoading = false;
        if (state.outputView === "pdf") return renderPdf().catch(showPdfError);
      });
      loadSemanticIndex();
      loadArticleGit({ includeHistory: state.sideView === "git", silent: true })
        .then(() => { if (id === state.selectedId) scheduleArticleGitRefresh(); })
        .catch(error => { if (id === state.selectedId) showToast(window.PortalI18n.text`Git 状态：${error.message || error}`, "warning"); });
      await Promise.all([loadFiles(), loadSettings()]);
      if (id !== state.selectedId) return false;
      const primary = state.settings?.primary_file || articleRoot()?.primary_file || selectedArticle()?.primary_file;
      if (primary) await openArticleFile(primary);
      if (state.sideView === "chat") loadArticleChat().catch(error => showToast(error.message || error, "warning"));
      detailResourceGuest.setResource({ resourceId: id });
      detailResourceGuest.ready();
    } catch (error) {
      detailResourceGuest.failed({ resourceId: id, error: error.message || String(error) });
      showToast(error.message || error, "error");
    }
    return true;
  }

  async function loadArticles({ preserve = true, reloadSelected = true } = {}) {
    const payload = await request("/api/articles");
    state.articles = Array.isArray(payload.articles) ? payload.articles : [];
    const requested = String(queryState().get("item") || "");
    if (requested && !state.articles.some(article => article.id === requested)) {
      try {
        const resolution = await request(`/api/resources/resolve?address=${encodeURIComponent(requested)}`);
        if (resolution.target?.resource_type !== "article" || resolution.target.root_id !== requested) {
          throw new Error(window.PortalI18n.t("这个地址不是可打开的 Article。"));
        }
        state.articles.push({ ...resolution.resource, id: requested });
      } catch (error) {
        detailResourceGuest.failed({ resourceId: requested, error: error.message || String(error) });
        $("articleEditorState").textContent = window.PortalI18n.text`无法打开 Article：${error.message || error}`;
        renderArticleSelectors();
        throw error;
      }
    }
    const next = requested
      || (preserve && state.articles.some(article => article.id === state.selectedId)
        ? state.selectedId
        : state.articles.some(article => article.id === state.lastSelectedId)
          ? state.lastSelectedId
        : state.articles[0]?.id || "");
    renderArticleSelectors();
    if (reloadSelected) await selectArticle(next, { initial: true });
    else renderProjectIdentity();
  }

  function openRegisterModal() {
    setProjectMenu(false);
    state.registerPreflight = null;
    state.registerPrimaryConfirmed = false;
    $("articleRegisterProjects").value = "";
    $("articleRegisterPreflight").dataset.status = "idle";
    $("articleRegisterPreflight").textContent = window.PortalI18n.t("输入目录后检查主文档、编译器与依赖。");
    $("articleRegisterPrimaryField").hidden = true;
    $("articleRegisterCompilerField").hidden = true;
    articleRegisterDialog.open();
    window.setTimeout(() => $("articleRegisterPath").focus(), 0);
  }

  function renderRegistrationPreflight(preflight) {
    const host = $("articleRegisterPreflight");
    host.dataset.status = preflight.status || "idle";
    const issues = Array.isArray(preflight.issues) ? preflight.issues : [];
    const heading = preflight.status === "blocked"
      ? window.PortalI18n.t("目录暂不可注册")
      : preflight.requires_selection && !state.registerPrimaryConfirmed
        ? window.PortalI18n.t("请选择主文档")
        : window.PortalI18n.t("构建配置已识别");
    host.innerHTML = window.PortalI18n.html`<strong>${heading}</strong><span>${esc(preflight.primary_file || window.PortalI18n.t("未识别主文档"))} · ${esc(preflight.compiler || window.PortalI18n.t("未识别编译器"))}</span>${issues.map(issue => window.PortalI18n.html`<span>${esc(issue.message || "")}</span>`).join("")}`;
  }

  async function inspectRegistration({ forSubmit = false } = {}) {
    const path = $("articleRegisterPath").value.trim();
    if (!path) throw new Error("请输入 Article 目录路径");
    const useSelection = state.registerPreflight?.path === path && state.registerPrimaryConfirmed;
    let preflight;
    try {
      preflight = await request("/api/articles/preflight", {
        method: "POST",
        body: JSON.stringify({
          path,
          primary_file: useSelection ? $("articleRegisterPrimary").value : "",
          compiler: useSelection ? $("articleRegisterCompiler").value : "",
        }),
      });
    } catch (error) {
      // A refused directory is a preflight result: show why inside the dialog.
      state.registerPreflight = null;
      $("articleRegisterProjects").value = "";
      renderRegistrationPreflight({ status: "blocked", issues: [{ message: error.message || String(error) }] });
      return false;
    }
    state.registerPreflight = preflight;
    // An Article belongs to the Project its directory is in; the preflight names it and registration sends exactly that.
    $("articleRegisterProjects").value = (preflight.project_ids || []).join(", ");
    const currentPrimary = $("articleRegisterPrimary").value;
    $("articleRegisterPrimary").innerHTML = (preflight.candidates || [])
      .filter(candidate => candidate.standalone)
      .map(candidate => window.PortalI18n.html`<option value="${esc(candidate.path)}" ${(currentPrimary || preflight.primary_file) === candidate.path ? "selected" : ""}>${esc(candidate.path)}${candidate.compiler_hint ? ` · ${esc(candidate.compiler_hint)}` : ""}</option>`)
      .join("");
    $("articleRegisterCompiler").innerHTML = ["pdflatex", "xelatex", "lualatex"]
      .map(name => window.PortalI18n.html`<option value="${name}" ${name === preflight.compiler ? "selected" : ""}>${name}</option>`)
      .join("");
    $("articleRegisterPrimaryField").hidden = !(preflight.candidates || []).some(candidate => candidate.standalone);
    $("articleRegisterCompilerField").hidden = false;
    renderRegistrationPreflight(preflight);
    if (preflight.status === "blocked") return false;
    if (forSubmit && preflight.requires_selection && !state.registerPrimaryConfirmed) {
      showToast(window.PortalI18n.t("检测到多个主文档，请确认要注册的主文档。"), "warning");
      return false;
    }
    return true;
  }

  async function registerArticle(event) {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    const submit = formElement.querySelector('button[type="submit"]');
    submit.disabled = true;
    try {
      if (!await inspectRegistration({ forSubmit: true })) return;
      const projectIds = (state.registerPreflight?.project_ids || []).filter(Boolean);
      if (!projectIds.length) throw new Error(window.PortalI18n.t("Article 目录必须位于已登记的 Project 内"));
      const result = await window.PortalResourceRegistrationService.register({
        resource_type: "article",
        path: form.get("path"),
        project_ids: projectIds,
        primary_file: $("articleRegisterPrimary").value,
        compiler: $("articleRegisterCompiler").value,
      });
      articleRegisterDialog.close();
      formElement.reset();
      state.selectedId = result.resource_id;
      detailHistory.open(state.selectedId);
      await loadArticles({ preserve: true });
      showToast(window.PortalI18n.t("Article 目录已注册。"), "success");
    } catch (error) {
      showToast(error.message || error, "error");
    } finally {
      submit.disabled = false;
    }
  }

  async function unregisterCurrentArticle() {
    const article = selectedArticle();
    if (!article) return;
    if (!await window.PortalUI.confirm({
      title: window.PortalI18n.t("解除 Article 注册？"),
      message: window.PortalI18n.text`解除「${article.title || article.id}」的 Article 注册？\n原始目录和文件不会被删除。`,
      confirmLabel: window.PortalI18n.t("解除注册"),
      tone: "danger",
    })) return;
    try {
      await window.PortalInstructionClient.execute("UnregisterResource", { resource_id: article.id }, {
        idempotencyKey: `article-unregister:${article.id}:${globalThis.PortalUIPrimitives.randomUUID()}`,
      });
      detailResourceGuest.deleted({ resourceId: article.id, kind: "unregistered" });
      state.selectedId = "";
      persistArticleSelection("");
      updateUrl("");
      await loadArticles({ preserve: false });
      showToast(window.PortalI18n.t("Article 注册已解除；原始目录和文件保持不变。"), "success");
    } catch (error) {
      showToast(error.message || error, "error");
    }
  }

  function initArticleWorkbench() {
    const { split, stack } = window.PortalWorkbench.model;
    const writing = split("horizontal", [
      stack(["navigation"]),
      stack(["editor"]),
      stack(["output"]),
    ], [308, 720, 560]);
    const focus = split("horizontal", [stack(["navigation"]), stack(["editor"])], [308, 980]);
    const proof = split("horizontal", [stack(["editor"]), stack(["output"])], [720, 720]);
    articleWorkbench = window.PortalWorkbench.create({
      id: "article-workspace",
      root: "#articleWorkspaceGrid",
      label: window.PortalI18n.t("Article Workbench"),
      insets: { left: 58 },
      initialEditors: [
        { id: "navigation", type: "authored", presentation: "navigation", options: { source: "#articleSidePane" }, title: window.PortalI18n.t("Navigation / Chat") },
        { id: "editor", type: "authored", presentation: "editor", options: { source: "#articleEditorPane" } },
        { id: "output", type: "authored", presentation: "document", options: { source: "#articleOutputPane" }, title: window.PortalI18n.t("PDF / Build") },
      ],
      defaultLayout: writing,
      presets: [
        { id: "writing", name: window.PortalI18n.t("写作"), layout: writing },
        { id: "focus", name: window.PortalI18n.t("专注编辑"), layout: focus, hidden: ["output"] },
        { id: "proof", name: window.PortalI18n.t("校对"), layout: proof, hidden: ["navigation"] },
      ],
      defaultPreset: "writing",
      onChange: () => {
        scheduleArticleEditorLayout();
        if (state.outputView === "pdf") scheduleArticlePdfLayout();
      },
    });
    $("articleEditorPane").addEventListener("portal:workbench-resize", scheduleArticleEditorLayout);
    if (window.ResizeObserver) {
      const editorResizeObserver = new ResizeObserver(scheduleArticleEditorLayout);
      editorResizeObserver.observe($("articleEditorPane"));
      state.monacoDisposables.push({ dispose: () => editorResizeObserver.disconnect() });
    }
  }

  function bindSplitters() {
    window.PortalWorkbench.layout.bindSplitter({
      handle: $("articleOutlineSplitter"),
      axis: "y",
      bodyClass: "articleOutlineResizing",
      min: 80,
      max: () => {
        const panel = document.querySelector('[data-article-panel="files"]');
        const header = panel.querySelector(".articleFileTreeHead");
        return panel.getBoundingClientRect().height - header.offsetHeight - $("articleOutlineSplitter").offsetHeight - 72;
      },
      readSize: () => $("articleFileTree").getBoundingClientRect().height,
      applySize: fileHeight => {
        $("articleFileTree").style.flex = `0 0 ${Math.round(fileHeight)}px`;
        $("articleOutlineSection").style.flex = "1 1 auto";
      },
      store: articleLayoutStore,
      stateKey: "fileTreeHeight",
    });
  }

  function restoreLayout() {
    try {
      const layout = articleLayoutStore.read();
      if (Number(layout.fileTreeHeight) >= 80) {
        $("articleFileTree").style.flex = `0 0 ${Number(layout.fileTreeHeight)}px`;
        $("articleOutlineSection").style.flex = "1 1 auto";
      }
      state.wordWrap = window.PortalState.storage.readText(WORD_WRAP_STORAGE_KEY, "") !== "0";
      const storedPreferences = window.PortalState.storage.readJson(EDITOR_PREFERENCE_KEY, {});
      state.editorPreferences = normalizedEditorPreferences(storedPreferences);
    } catch (_) {}
    applyEditorPreferences();
    applyWordWrap();
  }

  function applyWordWrap() {
    const editor = $("articleSourceEditor");
    $("articleCodeEditor").classList.toggle("wordWrap", state.wordWrap);
    $("articleWordWrap").setAttribute("aria-pressed", state.wordWrap ? "true" : "false");
    $("articleWordWrap").title = state.wordWrap ? window.PortalI18n.t("关闭自动换行") : window.PortalI18n.t("自动换行");
    editor.wrap = state.wordWrap ? "soft" : "off";
    if (state.wordWrap) editor.scrollLeft = 0;
    state.editor?.updateOptions?.({
      wordWrap: state.wordWrap ? "on" : "off",
      scrollbar: { horizontal: state.wordWrap ? "hidden" : "auto" },
    });
    renderEditorPreferenceControls();
    if (state.openFile) renderEditorLayers();
  }

  function toggleWordWrap() {
    captureActiveDocument();
    state.wordWrap = !state.wordWrap;
    window.PortalState.storage.writeText(WORD_WRAP_STORAGE_KEY, state.wordWrap ? "1" : "0");
    applyWordWrap();
    window.requestAnimationFrame(() => {
      if (state.monacoReady) state.editor?.layout?.();
      else syncEditorScroll();
    });
  }

  function resetEditorPreferences() {
    state.editorPreferences = { ...EDITOR_PREFERENCE_DEFAULTS };
    state.wordWrap = true;
    persistEditorPreferences();
    applyEditorPreferences();
    applyWordWrap();
    showToast(window.PortalI18n.t("编辑器设置已恢复默认值。"), "success");
  }

  function handleEditorPairing(event) {
    const editor = event.currentTarget;
    if (!state.editorPreferences.autoCloseBrackets || editor.disabled) return false;
    if (event.ctrlKey || event.metaKey || event.altKey) return false;
    const pairs = { "{": "}", "[": "]", "(": ")" };
    const start = editor.selectionStart;
    const end = editor.selectionEnd;
    const value = editor.value;
    if (Object.hasOwn(pairs, event.key)) {
      event.preventDefault();
      const selected = value.slice(start, end);
      editor.setRangeText(`${event.key}${selected}${pairs[event.key]}`, start, end, "end");
      editor.setSelectionRange(start + 1, start + 1 + selected.length);
      editor.dispatchEvent(new Event("input", { bubbles: true }));
      return true;
    }
    if (Object.values(pairs).includes(event.key) && start === end && value[start] === event.key) {
      event.preventDefault();
      editor.setSelectionRange(start + 1, start + 1);
      updateCursorStatus();
      captureActiveDocument();
      return true;
    }
    if (event.key === "Backspace" && start === end && start > 0 && pairs[value[start - 1]] === value[start]) {
      event.preventDefault();
      editor.setRangeText("", start - 1, start + 1, "end");
      editor.dispatchEvent(new Event("input", { bubbles: true }));
      return true;
    }
    return false;
  }

  initializeArticlePopovers();

  document.addEventListener("click", event => {
    const view = event.target.closest("[data-article-view]");
    if (view) {
      if (view.classList.contains("active") && articleWorkbench?.isPanelVisible("navigation")) setNavigationPanelVisible(false);
      else setSideView(view.dataset.articleView);
      return;
    }
    if (event.target.closest("[data-side-close]")) {
      setNavigationPanelVisible(false);
      return;
    }
    const gitStage = event.target.closest("[data-git-stage]");
    if (gitStage) {
      updateArticleGitStage(gitStage.dataset.gitStage, gitStage.dataset.gitStaged === "true")
        .catch(error => showToast(error.message || error, "error"));
      return;
    }
    if (event.target.closest("[data-git-init]")) {
      initializeArticleGit().catch(error => showToast(error.message || error, "error"));
      return;
    }
    const gitDiff = event.target.closest("[data-git-diff]");
    if (gitDiff) {
      openArticleGitDiff(gitDiff.dataset.gitDiff)
        .catch(error => showToast(error.message || error, "error"));
      return;
    }
    const gitCommit = event.target.closest("[data-git-commit]");
    if (gitCommit) {
      openArticleGitCommit(gitCommit.dataset.gitCommit, gitCommit.dataset.gitCommitShort, gitCommit.dataset.gitCommitSubject)
        .catch(error => showToast(error.message || error, "error"));
      return;
    }
    const gitDiffFile = event.target.closest("[data-git-diff-file]");
    if (gitDiffFile) {
      renderArticleGitDiffFile(Number(gitDiffFile.dataset.gitDiffFile));
      return;
    }
    const headerSelect = event.target.closest("[data-article-header-select]");
    if (headerSelect) {
      setArticleResourceSearchOpen(false);
      selectArticle(headerSelect.dataset.articleHeaderSelect)
        .catch(error => showToast(error.message || error, "error"));
      return;
    }
    const select = event.target.closest("[data-article-select]");
    if (select) {
      selectArticle(select.dataset.articleSelect).catch(error => showToast(error.message || error, "error"));
      return;
    }
    const semanticView = event.target.closest("[data-semantic-view]");
    if (semanticView) {
      state.semanticView = semanticView.dataset.semanticView || "outline";
      renderSemanticNavigation();
      return;
    }
    const editorLocation = event.target.closest("[data-editor-path]");
    if (editorLocation) {
      openArticleFile(editorLocation.dataset.editorPath, {
        line: Number(editorLocation.dataset.editorLine || 1),
        column: Number(editorLocation.dataset.editorColumn || 1),
      });
      return;
    }
    const lineButton = event.target.closest("[data-editor-line]");
    if (lineButton) {
      goToLine(Number(lineButton.dataset.editorLine || 1));
      return;
    }
    const searchGroup = event.target.closest("[data-search-group]");
    if (searchGroup) {
      const path = searchGroup.dataset.searchGroup;
      const section = searchGroup.closest(".articleSearchGroup");
      const collapsed = !section.classList.contains("collapsed");
      section.classList.toggle("collapsed", collapsed);
      searchGroup.setAttribute("aria-expanded", collapsed ? "false" : "true");
      if (collapsed) state.collapsedSearchFiles.add(path);
      else state.collapsedSearchFiles.delete(path);
      return;
    }
    const result = event.target.closest("[data-search-file]");
    if (result) {
      openArticleFile(result.dataset.searchFile, {
        line: Number(result.dataset.searchLine || 1),
        column: Number(result.dataset.searchColumn || 1),
      });
      return;
    }
    const filter = event.target.closest("[data-log-filter]");
    if (filter) {
      state.logFilter = filter.dataset.logFilter;
      renderLog();
      return;
    }
    const askDiagnostic = event.target.closest("[data-ask-build-diagnostic]");
    if (askDiagnostic) {
      askBuildDiagnostic(Number(askDiagnostic.dataset.askBuildDiagnostic)).catch(error => showToast(error.message || error, "error"));
      return;
    }
    const openDiagnostic = event.target.closest("[data-open-build-diagnostic], [data-build-diagnostic]");
    if (openDiagnostic) {
      const index = Number(openDiagnostic.dataset.openBuildDiagnostic ?? openDiagnostic.dataset.buildDiagnostic);
      openBuildDiagnostic(index).catch(error => showToast(error.message || error, "error"));
      return;
    }
    const diagnostic = event.target.closest("[data-diagnostic-file]");
    if (diagnostic) {
      const path = diagnostic.dataset.diagnosticFile.replace(/^\.\//, "");
      if (state.filesByPath.has(path)) {
        openArticleFile(path, { line: Number(diagnostic.dataset.diagnosticLine || 1) });
      }
    }
  });

  document.addEventListener("dblclick", event => {
    const page = event.target.closest(".articlePdfPage");
    if (!page) return;
    event.preventDefault();
    syncPdfToSource(page, event).catch(error => showToast(error.message || error, "error"));
  });

  $("articleProjectButton").addEventListener("click", event => {
    setProjectMenu(!articleProjectPopover?.isOpen(), event);
  });
  $("articleGitRefresh").addEventListener("click", () => loadArticleGit({ includeHistory: true }).catch(error => showToast(error.message || error, "error")));
  $("articleGitCommitForm").addEventListener("submit", event => commitArticleGit(event).catch(error => showToast(error.message || error, "error")));
  $("articleGitDiffClose").addEventListener("click", closeArticleGitDiff);
  $("articleGitDiffLayout").addEventListener("click", toggleArticleGitDiffLayout);
  $("articleProjectMenuClose").addEventListener("click", () => setProjectMenu(false));
  $("articleResourceQuery").addEventListener("focus", () => {
    setProjectMenu(false);
    setArticleResourceSearchOpen(true);
  });
  $("articleResourceQuery").addEventListener("input", () => {
    if (state.semanticSearch?.activeId()) {
      state.semanticSearch.clearActive({ notify: false });
      updateSemanticUrl("");
    }
    renderArticleResourceSearch();
    setArticleResourceSearchOpen(true);
  });
  $("articleResourceQuery").addEventListener("keydown", event => {
    if (event.key !== "Enter") return;
    const first = $("articleResourceSearchResults").querySelector("[data-article-header-select]");
    if (!first) return;
    event.preventDefault();
    first.click();
  });
  $("articleMenuSearch").addEventListener("input", renderArticleSelectors);
  $("articleLibrarySearch").addEventListener("input", renderArticleSelectors);
  $("articleRegisterOpen").addEventListener("click", openRegisterModal);
  $("articleLibraryRegister").addEventListener("click", openRegisterModal);
  $("articleUnregisterCurrent").addEventListener("click", unregisterCurrentArticle);
  [$("articleRegisterClose"), $("articleRegisterCancel")].forEach(button => button.addEventListener("click", () => articleRegisterDialog.close()));
  $("articleRegisterInspect").addEventListener("click", () => inspectRegistration().catch(error => showToast(error.message || error, "error")));
  $("articleRegisterPath").addEventListener("input", () => {
    state.registerPreflight = null;
    state.registerPrimaryConfirmed = false;
    $("articleRegisterProjects").value = "";
    $("articleRegisterPreflight").dataset.status = "idle";
    $("articleRegisterPreflight").textContent = window.PortalI18n.t("目录已变化，请重新检查。");
  });
  $("articleRegisterPrimary").addEventListener("change", () => {
    state.registerPrimaryConfirmed = true;
    inspectRegistration().catch(error => showToast(error.message || error, "error"));
  });
  $("articleRegisterCompiler").addEventListener("change", () => {
    state.registerPrimaryConfirmed = true;
    inspectRegistration().catch(error => showToast(error.message || error, "error"));
  });
  $("articleRegisterForm").addEventListener("submit", registerArticle);

  $("articleNewFile").addEventListener("click", () => openEntryModal("file"));
  $("articleNewFolder").addEventListener("click", () => openEntryModal("directory"));
  $("articleUploadFile").addEventListener("click", () => state.selectedId && $("articleUploadInput").click());
  $("articleUploadInput").addEventListener("change", event => uploadFile(event.target.files?.[0]));
  [$("articleEntryClose"), $("articleEntryCancel")].forEach(button => button.addEventListener("click", () => articleEntryDialog.close()));
  $("articleEntryForm").addEventListener("submit", createEntry);
  $("articleSideCollapse").addEventListener("click", () => setNavigationPanelVisible(false));

  $("articleTreeToggle").addEventListener("click", () => {
    const open = $("articleTreeToggle").getAttribute("aria-expanded") !== "true";
    $("articleTreeToggle").setAttribute("aria-expanded", open ? "true" : "false");
    $("articleFileTree").classList.toggle("collapsed", !open);
    $("articleOutlineSplitter").hidden = !open;
  });
  $("articleOutlineToggle").addEventListener("click", () => {
    const open = $("articleOutlineToggle").getAttribute("aria-expanded") !== "true";
    $("articleOutlineToggle").setAttribute("aria-expanded", open ? "true" : "false");
    $("articleOutlineSection").classList.toggle("collapsed", !open);
  });
  $("articleGlobalSearch").addEventListener("submit", searchProject);
  $("articleSettingsForm").addEventListener("submit", saveSettings);
  document.querySelectorAll("[data-settings-section]").forEach(button => {
    button.addEventListener("click", () => setSettingsSection(button.dataset.settingsSection));
  });
  $("articleEditorAutoClose").addEventListener("change", event => {
    updateEditorPreference("autoCloseBrackets", event.currentTarget.checked);
  });
  $("articleEditorSolidCursor").addEventListener("change", event => {
    updateEditorPreference("solidCursor", event.currentTarget.checked);
  });
  $("articleEditorCodeCheck").addEventListener("change", event => {
    updateEditorCodeCheck(event.currentTarget);
  });
  $("articleEditorWordWrap").addEventListener("change", event => {
    if (state.wordWrap !== event.currentTarget.checked) toggleWordWrap();
  });
  $("articleEditorFontSize").addEventListener("change", event => {
    updateEditorPreference("fontSize", Number(event.currentTarget.value));
  });
  $("articleEditorTabSize").addEventListener("change", event => {
    updateEditorPreference("tabSize", Number(event.currentTarget.value));
  });
  $("articleEditorSettingsReset").addEventListener("click", resetEditorPreferences);

  $("articleSourceEditor").addEventListener("input", event => {
    if (!activeDocument()?.editable) return;
    state.source = event.currentTarget.value;
    renderEditorLayers();
    updateDirtyState();
    updateCursorStatus();
    scheduleAutoCompile();
  });
  $("articleSourceEditor").addEventListener("scroll", () => {
    syncEditorScroll();
    captureActiveDocument();
  });
  ["click", "keyup", "select"].forEach(name => $("articleSourceEditor").addEventListener(name, () => {
    updateCursorStatus();
    captureActiveDocument();
  }));
  $("articleSourceEditor").addEventListener("keydown", event => {
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
      editor.setRangeText(" ".repeat(state.editorPreferences.tabSize), start, end, "end");
      editor.dispatchEvent(new Event("input", { bubbles: true }));
    }
  });
  $("articleSaveFile").addEventListener("click", () => saveOpenFile());
  $("articleCodeMode").addEventListener("click", () => setEditorMode("code"));
  $("articleVisualMode").addEventListener("click", () => {
    captureActiveDocument();
    setEditorMode("visual");
  });
  $("articleUndo").addEventListener("click", () => {
    if (state.monacoReady) state.editor?.trigger?.("article-toolbar", "undo", null);
    else {
      $("articleSourceEditor").focus();
      document.execCommand("undo");
    }
  });
  $("articleRedo").addEventListener("click", () => {
    if (state.monacoReady) state.editor?.trigger?.("article-toolbar", "redo", null);
    else {
      $("articleSourceEditor").focus();
      document.execCommand("redo");
    }
  });
  $("articleWordWrap").addEventListener("click", toggleWordWrap);
  $("articleSyncToPdf").addEventListener("click", syncSourceToPdf);
  $("articleSendContext").addEventListener("click", bringCurrentContextToChat);

  $("articleCompile").addEventListener("click", () => compileArticle());
  $("articleCompileSettings").addEventListener("click", event => {
    setCompileMenu(!articleCompilePopover?.isOpen(), event);
  });
  document.querySelectorAll("[data-compile-setting]").forEach(button => {
    button.addEventListener("click", () => updateCompilePreference(button));
  });
  $("articleStopCompile").addEventListener("click", stopCompilation);
  $("articleRecompileScratch").addEventListener("click", () => compileArticle({ fromScratch: true }));
  $("articleLogButton").addEventListener("click", () => setOutputView("log"));
  $("articleBackToPdf").addEventListener("click", () => setOutputView("pdf"));
  $("articlePdfZoomOut").addEventListener("click", () => {
    setArticlePdfZoom(state.pdfZoom - 0.1).catch(error => showPdfError(error));
  });
  $("articlePdfZoomIn").addEventListener("click", () => {
    setArticlePdfZoom(state.pdfZoom + 0.1).catch(error => showPdfError(error));
  });
  $("articlePdfZoomFit").addEventListener("click", () => {
    setArticlePdfZoom(1).catch(error => showPdfError(error));
  });
  bindArticlePdfGestures();

  window.addEventListener("portal:change", event => {
    const detail = event?.detail || {};
    const changed = Object.values(detail.resource_projection_changes || {}).flat();
    if (detail.snapshot_required || changed.includes(state.selectedId)) scheduleArticleGitRefresh(120);
  });
  window.addEventListener("portal:events-ready", event => {
    if (event?.detail?.snapshot_required) {
      scheduleArticleGitRefresh(0);
    }
  });
  window.addEventListener("beforeunload", event => {
    articleGitRefresh.stop();
    captureActiveDocument();
    if (state.preferenceSaveTimer) flushArticleNavigationPreference({ keepalive: true });
    if (!hasDirtyDocuments()) return;
    event.preventDefault();
    event.returnValue = "";
  });
  window.addEventListener("popstate", () => {
    const requestedId = String(queryState().get("item") || "");
    if (requestedId === state.selectedId) return;
    const currentId = state.selectedId;
    selectArticle(requestedId, {
      initial: true,
      updateHistory: false,
    }).then(accepted => {
      if (accepted === false && currentId === state.selectedId) detailHistory.restoreAccepted();
    }).catch(error => showToast(error.message || error, "error"));
  });

  initArticleWorkbench();
  restoreLayout();
  if (["1", "true", "open"].includes(String(queryState().get("chat") || "").toLowerCase())) {
    setSideView("chat");
  }
  bindSplitters();
  window.PortalPreview.registerHost("article-workspace", request => {
    if (!state.selectedId) return false;
    return openArticleLinkedReference(request).then(() => true);
  }, { priority: 100 });
  if (embedded) document.body.dataset.articleEmbedded = "1";
  window.PortalResourceTabs?.render(document.querySelector("[data-resource-tabs]"));
  initializeArticleSemanticSearch();
  window.ArticleWorkspaceTest = {
    value: () => editorValue(),
    setValue: value => {
      if (state.monacoReady && state.editor) state.editor.setValue(String(value ?? ""));
      else {
        const editor = $("articleSourceEditor");
        editor.value = String(value ?? "");
        editor.dispatchEvent(new Event("input", { bubbles: true }));
      }
    },
    position: () => editorPosition(),
    setPosition: (line, column = 1) => goToLine(line, column),
    selection: () => editorSelectionContext(),
    setSelection: (startLine, startColumn, endLine = startLine, endColumn = startColumn) => {
      if (state.monacoReady && state.editor) {
        const selection = {
          startLineNumber: Number(startLine),
          startColumn: Number(startColumn),
          endLineNumber: Number(endLine),
          endColumn: Number(endColumn),
        };
        state.editor.setSelection(selection);
        state.editor.revealRangeInCenter(selection);
        state.editor.focus();
      }
    },
    openFile: (path, options = {}) => openArticleFile(path, options),
    activeFile: () => state.openFile,
    pdfZoom: () => state.pdfZoom,
    markers: () => {
      const monaco = monacoApi();
      const model = activeEditorModel();
      return monaco && model
        ? monaco.editor.getModelMarkers({ resource: model.uri })
        : [];
    },
    editorOptions: () => state.editor?.getRawOptions?.() || {},
    editorLayout: () => state.editor?.getLayoutInfo?.() || null,
    editorLineTop: line => state.editor?.getTopForLineNumber?.(Number(line)) || 0,
    gitDiff: () => ({
      files: state.gitDiffFiles.map(file => file.path),
      activeFile: state.gitDiffFiles[state.gitDiffActiveFile]?.path || "",
      original: state.gitDiffModels[0]?.getValue?.() || "",
      modified: state.gitDiffModels[1]?.getValue?.() || "",
      sideBySide: state.gitDiffSideBySide,
    }),
    refreshGit: () => loadArticleGit({ includeHistory: state.sideView === "git", silent: true }),
  };
  const articleNavigationReady = loadArticleNavigationPreference();
  const articleListReady = articleNavigationReady.then(() => loadArticles());
  const articleEditorReady = Promise.all([
    initializeMonacoEditor(),
    articleNavigationReady,
  ]).then(() => {
    applyEditorPreferences();
    applyWordWrap();
  });
  window.ArticleWorkspaceReady = Promise.all([articleListReady, articleEditorReady]).catch(error => {
    showToast(error.message || error, "error");
  });
  state.semanticSearch?.load().catch(error => {
    showToast(window.PortalUI?.errorMessage?.(window.PortalI18n.t("加载 Article 语义检索"), error) || error, "error");
  });
}());
