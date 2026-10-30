(() => {
  "use strict";

  if (window.PortalWorkspaceSurfaceSelectionInfo) return;

  // What is selected in a Preview: the selection a page supplies, the one
  // in its editor or the one in the rendered document, with the text, its
  // lines, the document's path and title, and where to show the toolbar.

  const DEPENDENCIES = Object.freeze([
    "options", "previewSelectionLineRangeFromDom", "previewSelectionLineRangeFromEditor", "previewSelectionOption",
    "previewSelectionState", "resolveRoot", "roots",
  ]);

  function createSelectionInfo(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalWorkspaceSurfaceSelectionInfo is missing: ${missing.join(", ")}`);
    const {
      options, previewSelectionLineRangeFromDom, previewSelectionLineRangeFromEditor, previewSelectionOption,
      previewSelectionState, resolveRoot, roots,
    } = dependencies;

    const payload = () => previewSelectionOption(options.payload) || {};
    const editor = () => {
      const candidate = resolveRoot(previewSelectionOption(options.editor));
      return candidate && roots.some(root => root.contains(candidate)) ? candidate : null;
    };
    const source = () => {
      const explicit = previewSelectionOption(options.content);
      if (explicit !== undefined && explicit !== null) return String(explicit);
      const activeEditor = editor();
      if (activeEditor && "value" in activeEditor) return String(activeEditor.value || "");
      return String(payload()?.content || "");
    };
    const location = () => {
      const currentPayload = payload();
      return {
        path: String(
          previewSelectionOption(options.path, currentPayload)
          || currentPayload.path
          || currentPayload.rel_path
          || "",
        ).trim(),
        title: String(
          previewSelectionOption(options.title, currentPayload)
          || currentPayload.title
          || currentPayload.name
          || window.PortalI18n.t("Preview"),
        ).trim(),
      };
    };
    const disabled = () => previewSelectionOption(options.disabled, payload()) === true;
    const rootContaining = node => roots.find(root => root === node || root.contains(node)) || null;
    const pointerRect = (event, fallback) => {
      const x = Number(event?.clientX);
      const y = Number(event?.clientY);
      if (Number.isFinite(x) && Number.isFinite(y) && (x || y)) {
        return { left: x, right: x, top: y, bottom: y, width: 0, height: 0 };
      }
      const rect = fallback?.getBoundingClientRect?.();
      if (!rect) return null;
      const centerX = rect.left + rect.width / 2;
      const centerY = rect.top + Math.min(rect.height / 2, 56);
      return {
        left: centerX,
        right: centerX,
        top: centerY,
        bottom: centerY,
        width: 0,
        height: 0,
      };
    };
    const editorSelection = event => {
      const activeEditor = editor();
      if (!activeEditor || !("selectionStart" in activeEditor)) return null;
      const start = Number(activeEditor.selectionStart || 0);
      const end = Number(activeEditor.selectionEnd || 0);
      if (end <= start) return null;
      const text = String(activeEditor.value || "").slice(start, end).trim();
      if (!text) return null;
      const lineRange = previewSelectionLineRangeFromEditor(activeEditor, start, end);
      return {
        ...location(),
        text,
        lineStart: lineRange.start,
        lineEnd: lineRange.end,
        rect: pointerRect(event || previewSelectionState.lastPointer, activeEditor),
        source: "editor",
      };
    };
    const providedSelection = event => {
      const candidate = previewSelectionOption(options.selection, event);
      if (!candidate || typeof candidate !== "object") return null;
      const text = String(candidate.text || "").trim();
      if (!text) return null;
      return {
        ...location(),
        ...candidate,
        text,
        lineStart: Number(candidate.lineStart || candidate.line_start || 0),
        lineEnd: Number(candidate.lineEnd || candidate.line_end || candidate.lineStart || candidate.line_start || 0),
        rect: candidate.rect || pointerRect(event || previewSelectionState.lastPointer, roots[0]),
        source: String(candidate.source || "editor"),
      };
    };
    const domSelection = () => {
      const selection = window.getSelection?.();
      if (!selection || selection.isCollapsed || selection.rangeCount !== 1) return null;
      const selectedText = String(selection.toString() || "").trim();
      if (!selectedText) return null;
      const range = selection.getRangeAt(0);
      if (!rootContaining(range.commonAncestorContainer)) return null;
      const lineRange = previewSelectionLineRangeFromDom(range, roots, source(), selectedText);
      const rect = range.getBoundingClientRect();
      if (!rect || (!rect.width && !rect.height)) return null;
      return {
        ...location(),
        text: selectedText,
        lineStart: lineRange.start,
        lineEnd: lineRange.end,
        rect,
        source: "visual",
      };
    };
    const selectionInfo = (event = null) => {
      if (previewSelectionState.destroyed || disabled()) return null;
      const supplied = providedSelection(event);
      if (supplied) return supplied;
      const eventTarget = event?.target;
      const activeEditor = editor();
      if (
        activeEditor
        && (
          eventTarget === activeEditor
          || document.activeElement === activeEditor
          || activeEditor.contains?.(eventTarget)
        )
      ) {
        return editorSelection(event);
      }
      return domSelection() || editorSelection(event);
    };

    return {
      editor, selectionInfo,
    };
  }

  window.PortalWorkspaceSurfaceSelectionInfo = Object.freeze({ createSelectionInfo, dependencies: DEPENDENCIES });
})();
