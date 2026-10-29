(() => {
  "use strict";

  if (window.PortalArticleWorkspaceSyntax) return;

  // LaTeX in the editor: highlighting for the plain editor, the source checks,
  // markers from checks, the compiler and TexLab, the syntax status, the
  // cursor and dirty state, and bracket pairing while typing.
  const DEPENDENCIES = Object.freeze([
    "$", "activeDocument", "articleRoot", "captureActiveDocument", "compileSettingValue", "editorPosition",
    "editorSelectionContext", "esc", "monacoApi", "renderDocumentTabs", "renderSemanticNavigation",
    "selectedArticle", "state",
  ]);

  function createSyntax(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalArticleWorkspaceSyntax is missing: ${missing.join(", ")}`);
    const {
      $, activeDocument, articleRoot, captureActiveDocument, compileSettingValue, editorPosition,
      editorSelectionContext, esc, monacoApi, renderDocumentTabs, renderSemanticNavigation, selectedArticle,
      state,
    } = dependencies;

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

    return Object.freeze({
      handleEditorPairing, normalizedDiagnosticPath, renderEditorLayers, syncEditorScroll,
      updateAllEditorMarkers, updateCursorStatus, updateDirtyState,
    });
  }

  window.PortalArticleWorkspaceSyntax = Object.freeze({ createSyntax, dependencies: DEPENDENCIES });
})();
