(() => {
  "use strict";

  if (window.PortalWorkspaceSurfaceSelectionChat) return;

  // Sending a Preview selection to a conversation: the attachment that
  // quotes it replaces an equal one in the chat workspace's draft, and the
  // selection is cleared.

  const DEPENDENCIES = Object.freeze([
    "createPreviewReferenceAttachment", "editor", "hide", "options", "previewSelectionOption",
    "previewSelectionState", "selectionInfo",
  ]);

  function createSelectionChat(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalWorkspaceSurfaceSelectionChat is missing: ${missing.join(", ")}`);
    const {
      createPreviewReferenceAttachment, editor, hide, options, previewSelectionOption, previewSelectionState,
      selectionInfo,
    } = dependencies;

    const workspace = () => previewSelectionOption(options.chatWorkspace) || null;
    const clearSelection = info => {
      if (info?.source === "editor") {
        const activeEditor = editor();
        if (activeEditor && "selectionEnd" in activeEditor) {
          const end = Number(activeEditor.selectionEnd || 0);
          activeEditor.setSelectionRange?.(end, end);
        } else if (typeof options.clearSelection === "function") {
          options.clearSelection(info);
        }
      } else {
        window.getSelection?.()?.removeAllRanges?.();
      }
    };
    const addToChat = () => {
      const info = previewSelectionState.current || selectionInfo();
      const targetWorkspace = workspace();
      if (!info || !targetWorkspace?.setAttachments) return false;
      const attachment = createPreviewReferenceAttachment(info);
      const previous = targetWorkspace.attachments?.() || [];
      targetWorkspace.setAttachments([
        ...previous.filter(item => String(item?.id || "") !== attachment.id),
        attachment,
      ]);
      targetWorkspace.saveDraft?.();
      clearSelection(info);
      hide();
      options.onAdd?.(Object.freeze({ ...info }), attachment, targetWorkspace);
      window.requestAnimationFrame(() => targetWorkspace?.element?.("input")?.focus?.());
      return attachment;
    };

    return {
      addToChat,
    };
  }

  window.PortalWorkspaceSurfaceSelectionChat = Object.freeze({ createSelectionChat, dependencies: DEPENDENCIES });
})();
