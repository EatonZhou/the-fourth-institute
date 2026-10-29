(() => {
  "use strict";

  if (window.PortalArticleWorkspaceMonaco) return;

  // The Monaco editor of the open document: the LaTeX and BibTeX languages,
  // the themes, editor options and models, and reading the value, caret and
  // selection whether Monaco or the plain textarea is in use.
  const DEPENDENCIES = Object.freeze([
    "$", "activateDocument", "activeDocument", "captureActiveDocument", "registerTexlabProviders",
    "renderEditorLayers", "saveOpenFile", "scheduleAutoCompile", "scheduleSemanticReload",
    "scheduleTexlabSync", "state", "updateCursorStatus", "updateDirtyState",
  ]);

  function createMonaco(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalArticleWorkspaceMonaco is missing: ${missing.join(", ")}`);
    const {
      $, activateDocument, activeDocument, captureActiveDocument, registerTexlabProviders, renderEditorLayers,
      saveOpenFile, scheduleAutoCompile, scheduleSemanticReload, scheduleTexlabSync, state,
      updateCursorStatus, updateDirtyState,
    } = dependencies;

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

    return Object.freeze({
      activeEditorModel, articleModelUri, documentForModel, editorPosition, editorSelectionContext,
      editorValue, initializeMonacoEditor, isMarkdownPath, languageForPath, monacoApi,
    });
  }

  window.PortalArticleWorkspaceMonaco = Object.freeze({ createMonaco, dependencies: DEPENDENCIES });
})();
