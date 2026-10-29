(() => {
  "use strict";

  if (window.PortalArticleWorkspaceTexlab) return;

  // The TexLab language server behind the editor: completion, hover and
  // definition providers, sending the open document after edits, and the
  // server status shown in the status bar.
  const DEPENDENCIES = Object.freeze([
    "$", "activeDocument", "activeEditorModel", "documentForModel", "openArticleFile", "request", "state",
    "updateAllEditorMarkers",
  ]);

  function createTexlab(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalArticleWorkspaceTexlab is missing: ${missing.join(", ")}`);
    const {
      $, activeDocument, activeEditorModel, documentForModel, openArticleFile, request, state,
      updateAllEditorMarkers,
    } = dependencies;

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

    return Object.freeze({
      registerTexlabProviders, scheduleTexlabSync, setTexlabStatus,
    });
  }

  window.PortalArticleWorkspaceTexlab = Object.freeze({ createTexlab, dependencies: DEPENDENCIES });
})();
