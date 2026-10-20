(function () {
  "use strict";

  let monacoPromise = null;

  function loadMonaco() {
    if (window.monaco?.editor) return Promise.resolve(window.monaco);
    if (monacoPromise) return monacoPromise;
    monacoPromise = new Promise((resolve, reject) => {
      const loader = window.require;
      if (typeof loader !== "function" || typeof loader.config !== "function") {
        reject(new Error("Monaco loader is unavailable"));
        return;
      }
      loader.config({ paths: { vs: "/static/vendor/monaco/vs" } });
      loader(
        ["vs/editor/editor.main"],
        () => window.monaco?.editor
          ? resolve(window.monaco)
          : reject(new Error("Monaco failed to initialize")),
        error => reject(error instanceof Error ? error : new Error("Monaco failed to load")),
      );
    }).catch(error => {
      monacoPromise = null;
      throw error;
    });
    return monacoPromise;
  }

  function languageForPath(path = "") {
    const extension = String(path).split(/[?#]/, 1)[0].split(".").pop()?.toLowerCase() || "";
    return ({
      c: "c",
      cc: "cpp",
      cpp: "cpp",
      css: "css",
      go: "go",
      h: "cpp",
      hpp: "cpp",
      html: "html",
      java: "java",
      js: "javascript",
      json: "json",
      jsonl: "json",
      jsx: "javascript",
      latex: "latex",
      md: "markdown",
      mdx: "markdown",
      py: "python",
      rb: "ruby",
      rs: "rust",
      sh: "shell",
      tex: "latex",
      toml: "ini",
      ts: "typescript",
      tsx: "typescript",
      txt: "plaintext",
      xml: "xml",
      yaml: "yaml",
      yml: "yaml",
    })[extension] || "plaintext";
  }

  function themeName() {
    const selected = document.documentElement.dataset.portalTheme;
    return selected === "dark" || selected === "scifi" ? "vs-dark" : "vs";
  }

  function diffPath(value = "") {
    let path = value.replace(/\r$/, "");
    if (path.startsWith('"') && path.endsWith('"')) {
      const escapes = { a: "\x07", b: "\b", f: "\f", n: "\n", r: "\r", t: "\t", v: "\v", '"': '"', "\\": "\\" };
      path = path.slice(1, -1).replace(/(?:\\[0-7]{1,3})+|\\([abfnrtv"\\])/g, (sequence, escape) => {
        if (escape) return escapes[escape];
        const bytes = Array.from(sequence.matchAll(/\\([0-7]{1,3})/g), match => parseInt(match[1], 8));
        return new TextDecoder().decode(new Uint8Array(bytes));
      });
    }
    return path.replace(/^[ab]\//, "");
  }

  function splitDiffFiles(value = "") {
    const text = String(value);
    const starts = Array.from(text.matchAll(/^diff --git (.+)\r?$/gm));
    return starts.map((start, index) => {
      const diff = text.slice(start.index, starts[index + 1]?.index ?? text.length);
      const header = diff.split(/^@@/m, 1)[0];
      const paths = start[1].replace(/\r$/, "").match(/^("(?:\\.|[^"\\])*"|a\/.*?) ("(?:\\.|[^"\\])*"|b\/.*)$/);
      const before = diffPath(header.match(/^--- (.+)$/m)?.[1] || paths?.[1] || "");
      const after = diffPath(header.match(/^\+\+\+ (.+)$/m)?.[1] || paths?.[2] || "");
      const path = after && after !== "/dev/null" ? after : before;
      const label = before && after && before !== after && before !== "/dev/null" && after !== "/dev/null"
        ? `${before} → ${after}` : path || window.PortalI18n.text`文件 ${index + 1}`;
      return { label, diff, path, deleted: after === "/dev/null" };
    });
  }

  function mountStatusBar(root, editor, model, monaco) {
    const { characterCount, wordCount } = window.PortalDocumentStatistics;
    const bar = document.createElement("div");
    bar.className = "portalCodeEditorStatus";
    bar.setAttribute("role", "group");
    bar.setAttribute("aria-label", window.PortalI18n.t("编辑器状态"));
    const totals = document.createElement("div");
    const location = document.createElement("div");
    totals.className = location.className = "portalCodeEditorStatusGroup";
    bar.append(totals, location);
    const field = (parent, name, title, tag = "span") => {
      const node = document.createElement(tag);
      node.dataset.editorStat = name;
      node.title = title;
      parent.append(node);
      return node;
    };
    const words = field(totals, "words", window.PortalI18n.t("中文逐字、英文按词；不计标点和空白"));
    const characters = field(totals, "characters", window.PortalI18n.t("字符数包含空格、标点和换行"));
    const lines = field(totals, "lines", window.PortalI18n.t("文件总行数，不受自动换行影响"));
    const selected = field(totals, "selected", window.PortalI18n.t("所有选区的字符数，包含空白"));
    const position = field(location, "position", window.PortalI18n.t("跳转到行（Ctrl+G）"), "button");
    position.type = "button";
    const language = field(location, "language", window.PortalI18n.t("文件语言"));
    const eol = field(location, "eol", window.PortalI18n.t("换行符"));
    root.append(bar);

    let contentChanged = true;
    let frame = 0;
    let disposed = false;
    const render = () => {
      frame = 0;
      if (disposed) return;
      if (contentChanged) {
        const text = model.getValue();
        words.textContent = window.PortalI18n.text`字数 ${wordCount(text).toLocaleString()}`;
        characters.textContent = window.PortalI18n.text`字符 ${characterCount(text).toLocaleString()}`;
        lines.textContent = window.PortalI18n.text`共 ${model.getLineCount().toLocaleString()} 行`;
        eol.textContent = model.getEOL() === "\r\n" ? "CRLF" : "LF";
        contentChanged = false;
      }
      const cursor = editor.getPosition();
      position.textContent = window.PortalI18n.text`行 ${cursor.lineNumber.toLocaleString()}，列 ${cursor.column.toLocaleString()}`;
      const count = (editor.getSelections() || []).reduce(
        (sum, selection) => sum + characterCount(model.getValueInRange(selection)), 0,
      );
      selected.hidden = count === 0;
      selected.textContent = count ? window.PortalI18n.text`已选 ${count.toLocaleString()} 字符` : "";
      language.textContent = monaco.languages.getLanguages().find(item => item.id === model.getLanguageId())?.aliases?.[0]
        || model.getLanguageId();
    };
    const schedule = () => {
      if (!disposed && !frame) frame = requestAnimationFrame(render);
    };
    const goToLine = () => {
      editor.focus();
      editor.getAction("editor.action.gotoLine")?.run();
    };
    position.addEventListener("click", goToLine);
    const subscriptions = [
      model.onDidChangeContent(() => { contentChanged = true; schedule(); }),
      model.onDidChangeLanguage(schedule),
      editor.onDidChangeCursorSelection(schedule),
    ];
    render();
    return {
      dispose() {
        disposed = true;
        if (frame) cancelAnimationFrame(frame);
        subscriptions.forEach(subscription => subscription.dispose());
        position.removeEventListener("click", goToLine);
        bar.remove();
      },
    };
  }

  async function mount(options = {}) {
    const root = typeof options.root === "string"
      ? document.querySelector(options.root)
      : options.root;
    if (!(root instanceof HTMLElement)) throw new Error("Code editor root is unavailable");
    const monaco = await loadMonaco();
    const model = monaco.editor.createModel(
      String(options.value ?? ""),
      String(options.language || languageForPath(options.path)),
    );
    const editorRoot = options.statusBar === true ? document.createElement("div") : root;
    if (editorRoot !== root) {
      root.classList.add("portalCodeEditorWithStatus");
      editorRoot.className = "portalCodeEditorViewport";
      root.append(editorRoot);
    }
    const editor = monaco.editor.create(editorRoot, {
      model,
      automaticLayout: true,
      ariaLabel: String(options.ariaLabel || window.PortalI18n.t("编辑当前文件")),
      fontFamily: "var(--mono)",
      fontSize: 12,
      lineHeight: 19,
      lineNumbers: "on",
      lineNumbersMinChars: 3,
      minimap: { enabled: false },
      overviewRulerLanes: 0,
      padding: { top: 10, bottom: 10 },
      renderLineHighlight: "line",
      scrollBeyondLastLine: false,
      readOnly: options.readOnly === true,
      smoothScrolling: true,
      tabSize: 2,
      theme: themeName(),
      wordWrap: options.wordWrap === false ? "off" : "on",
      wrappingIndent: "same",
    });
    const disposables = [];
    if (options.statusBar === true) disposables.push(mountStatusBar(root, editor, model, monaco));
    if (options.language === "diff") {
      const decorations = editor.createDecorationsCollection();
      const updateDiff = () => {
        let inHunk = false;
        decorations.set(model.getLinesContent().flatMap((line, index) => {
          if (line.startsWith("diff ")) inHunk = false;
          const hunk = /^@@ /.test(line);
          if (hunk) inHunk = true;
          const kind = hunk ? "Hunk" : inHunk && line.startsWith("+") ? "Add" : inHunk && line.startsWith("-") ? "Delete" : "";
          if (!kind) return [];
          return [{
            range: new monaco.Range(index + 1, 1, index + 1, model.getLineMaxColumn(index + 1)),
            options: { isWholeLine: true, className: `portalDiffLine${kind}`, inlineClassName: `portalDiffText${kind}` },
          }];
        }));
      };
      updateDiff();
      disposables.push(model.onDidChangeContent(updateDiff));
    }
    const selectionInfo = () => {
      const selection = editor.getSelection?.();
      if (!selection) return null;
      const text = String(model.getValueInRange(selection) || "").trim();
      if (!text) return null;
      const startPosition = selection.getStartPosition?.() || {};
      const endPosition = selection.getEndPosition?.() || {};
      const lineStart = Number(selection.startLineNumber || startPosition.lineNumber || 0);
      const lineEnd = Number(selection.endLineNumber || endPosition.lineNumber || lineStart);
      const visible = editor.getScrolledVisiblePosition?.(
        endPosition.lineNumber ? endPosition : {
          lineNumber: lineEnd,
          column: Number(selection.endColumn || 1),
        },
      );
      const rootRect = root.getBoundingClientRect?.();
      const rect = visible && rootRect
        ? {
            left: rootRect.left + Number(visible.left || 0),
            right: rootRect.left + Number(visible.left || 0),
            top: rootRect.top + Number(visible.top || 0),
            bottom: rootRect.top + Number(visible.top || 0) + Number(visible.height || 19),
            width: 0,
            height: Number(visible.height || 19),
          }
        : rootRect;
      return {
        text,
        lineStart,
        lineEnd,
        rect,
        source: "editor",
      };
    };
    if (typeof options.onChange === "function") {
      disposables.push(editor.onDidChangeModelContent(event => options.onChange(editor.getValue(), event)));
    }
    if (typeof options.onSelectionChange === "function" && typeof editor.onDidChangeCursorSelection === "function") {
      disposables.push(editor.onDidChangeCursorSelection(event => options.onSelectionChange(selectionInfo(), event)));
    }
    if (typeof options.onSave === "function") {
      editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => options.onSave(editor.getValue()));
    }
    const themeObserver = new MutationObserver(() => monaco.editor.setTheme(themeName()));
    themeObserver.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-portal-theme"],
    });
    disposables.push({ dispose: () => themeObserver.disconnect() });

    return Object.freeze({
      focus: () => editor.focus(),
      getValue: () => editor.getValue(),
      setValue(value) {
        model.setValue(String(value ?? ""));
        editor.setScrollTop(0);
        editor.setScrollLeft(0);
      },
      clearSelection() {
        const selection = editor.getSelection?.();
        if (!selection || typeof editor.setSelection !== "function") return;
        const end = selection.getEndPosition?.() || {
          lineNumber: Number(selection.endLineNumber || 1),
          column: Number(selection.endColumn || 1),
        };
        editor.setSelection({
          startLineNumber: end.lineNumber,
          startColumn: end.column,
          endLineNumber: end.lineNumber,
          endColumn: end.column,
        });
      },
      selectedText: () => selectionInfo()?.text || "",
      selection: selectionInfo,
      setWordWrap(enabled) {
        editor.updateOptions({ wordWrap: enabled === false ? "off" : "on" });
      },
      undo() {
        editor.trigger("portal", "undo", null);
        editor.focus();
      },
      redo() {
        editor.trigger("portal", "redo", null);
        editor.focus();
      },
      destroy() {
        disposables.forEach(disposable => disposable?.dispose?.());
        editor.dispose();
        model.dispose();
        if (editorRoot !== root) {
          editorRoot.remove();
          root.classList.remove("portalCodeEditorWithStatus");
        }
      },
    });
  }

  window.PortalCodeEditor = Object.freeze({
    splitDiffFiles,
    loadMonaco,
    mount,
  });
})();
