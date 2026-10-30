(() => {
  "use strict";

  if (window.PortalLineageWorkbenchPreviewEditing) return;

  // Editing a text document in the Preview panel: the code editor, entering
  // and leaving the editing mode, undo and redo, and saving.

  const DEPENDENCIES = Object.freeze([
    "$id", "activePreviewPayload", "activePreviewSnapshot", "applyPreviewWordWrap", "loadFolder", "previewIsEditable",
    "previewState", "renderPreviewActions",
  ]);

  function createPreviewEditing(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalLineageWorkbenchPreviewEditing is missing: ${missing.join(", ")}`);
    const {
      $id, activePreviewPayload, activePreviewSnapshot, applyPreviewWordWrap, loadFolder, previewIsEditable,
      previewState, renderPreviewActions,
    } = dependencies;

    let previewCodeEditorGeneration = 0;

    function destroyPreviewCodeEditor() {
      previewCodeEditorGeneration += 1;
      previewState.previewCodeEditor?.destroy?.();
      previewState.previewCodeEditor = null;
    }

    async function renderPreviewEditor() {
      const current = previewState.previewController?.snapshot?.();
      const payload = current?.payload || activePreviewPayload();
      if (!previewIsEditable(payload)) return;
      destroyPreviewCodeEditor();
      const generation = previewCodeEditorGeneration;
      const session = previewState.previewController;
      const box = $id("previewBox");
      box.className = "previewBox editing";
      box.innerHTML = window.PortalI18n.markup('<div class="previewMonacoEditor" id="previewMonacoEditor"></div>');
      applyPreviewWordWrap();
      renderPreviewActions();
      try {
        const editor = await window.PortalCodeEditor.mount({
          root: "#previewMonacoEditor",
          statusBar: true,
          path: payload.path || payload.rel_path || "",
          value: current?.draft ?? activePreviewSnapshot().draft,
          wordWrap: previewState.previewEditorTools?.wordWrap?.() !== false,
          onSelectionChange: () => previewState.previewSelectionController?.show?.(),
          onChange: value => {
            if (previewState.previewController !== session) return;
            session?.updateDraft?.(value);
          },
          onSave: () => {
            if (previewState.previewController === session) savePreviewEdit();
          },
        });
        if (
          generation !== previewCodeEditorGeneration
          || previewState.previewController !== session
          || !session?.snapshot?.().editing
        ) {
          editor.destroy();
          return;
        }
        previewState.previewCodeEditor = editor;
        editor.focus();
        renderPreviewActions();
      } catch (error) {
        if (generation !== previewCodeEditorGeneration) return;
        box.className = "previewBox";
        box.textContent = window.PortalI18n.text`编辑器加载失败：${error.message || error}`;
        window.PortalUI.toast(window.PortalI18n.t("编辑器加载失败，请重试。"), "error");
        renderPreviewActions();
      }
    }

    function enterPreviewEdit() {
      if (!previewIsEditable()) return;
      if (!previewState.previewController.snapshot().editing) previewState.previewController.beginEdit();
    }

    function cancelPreviewEdit() {
      return previewState.previewController?.cancelEdit?.();
    }

    async function setPreviewMode(mode) {
      const editing = !!previewState.previewController?.snapshot?.().editing;
      if (mode === "code") {
        if (!editing) enterPreviewEdit();
        return;
      }
      if (mode === "visual" && editing) await cancelPreviewEdit();
    }

    function runPreviewEditorCommand(command) {
      if (!previewState.previewCodeEditor || !["undo", "redo"].includes(command)) return false;
      previewState.previewCodeEditor[command]?.();
      return true;
    }

    async function savePreviewEdit() {
      const result = await previewState.previewController.save({
        save: ({ payload, draft }) => previewState.agentPreviewSource.save(payload, draft),
        onSaved: async updated => {
        if (String(updated?.source || "") !== "workspace_link") await loadFolder();
        },
        onError: err => window.PortalUI.toast(window.PortalUI.errorMessage("save", err), "error"),
      });
      return result;
    }

    return {
      destroyPreviewCodeEditor, enterPreviewEdit, renderPreviewEditor, runPreviewEditorCommand, savePreviewEdit,
      setPreviewMode,
    };
  }

  window.PortalLineageWorkbenchPreviewEditing = Object.freeze({ createPreviewEditing, dependencies: DEPENDENCIES });
})();
