(() => {
  if (window.PortalChatWorkspaceComposer) return;

  // The message input: its text with the attachment and reference tokens inside
  // it, caret and selection, IME composition, paste and drop, the attachment tray,
  // the slash command and @mention menus, and the content parts a sent input becomes.
  const DEPENDENCIES = Object.freeze([
    "attachmentKind", "attachmentPreviewUrl", "bindImagePreview", "bindResourceChips",
    "escapeHtml", "messageAvatarPlaceholderHtml",
  ]);

  const COMPOSER_DRAFT_ATTACHMENT_RE = /\[\[portal-(image|file):([^\]]+)\]\]/g;

  const WORKSPACE_PASTE_HANDLED_FLAG = "__portalWorkspacePasteHandled";

  function normalizeComposerAttachment(item, index = 0) {
    if (!item || typeof item !== "object") return null;
    const fileId = String(item.file_id || "").trim();
    const path = String(item.path || item.url || "").trim();
    const dataUrl = String(item.data_url || "").trim();
    const previewUrl = String(item.preview_url || dataUrl || item.url || "").trim();
    const contentType = String(item.content_type || item.mime || "").trim();
    const kind = String(item.kind || (contentType && !contentType.toLowerCase().startsWith("image/") ? "file" : "image")).toLowerCase() === "file" ? "file" : "image";
    const id = String(item.id || fileId || path || previewUrl || `attachment-${index}`).trim();
    if (!id) return null;
    return {
      ...item,
      id,
      file_id: fileId,
      kind,
      name: String(item.name || item.filename || (kind === "image" ? window.PortalI18n.t("pasted image") : "attachment")),
      path,
      preview_url: previewUrl,
      data_url: dataUrl,
      mime: String(item.mime || contentType),
      content_type: contentType,
      size: Number(item.size || item.size_bytes || 0),
      size_bytes: Number(item.size_bytes || item.size || 0),
      uploading: !!item.uploading,
      progress: Math.max(0, Math.min(100, Number(item.progress || 0))),
      error: String(item.error || ""),
    };
  }

  function normalizeComposerAttachments(items) {
    return (Array.isArray(items) ? items : [])
      .map((item, index) => normalizeComposerAttachment(item, index))
      .filter(Boolean);
  }

  function createConversationSelectionReference(selection = {}) {
    const text = String(selection.text || "").trim();
    if (!text) return null;
    const title = String(selection.title || window.PortalI18n.t("对话选段")).trim() || window.PortalI18n.t("对话选段");
    const path = String(selection.path || title).trim() || title;
    return normalizeComposerAttachment({
      id: `conversation-reference:${Date.now()}:${Math.random().toString(16).slice(2)}`,
      kind: "file",
      name: title,
      path,
      content_type: "text/x-portal-preview-reference",
      reference_text: text,
      preview_selection: {
        title,
        path,
        text,
        line_start: Number(selection.line_start || 0),
        line_end: Number(selection.line_end || selection.line_start || 0),
        source_message_ids: Array.isArray(selection.source_message_ids)
          ? selection.source_message_ids.map(value => String(value || "").trim()).filter(Boolean)
          : [],
      },
    });
  }

  function mediaOnlyCaption(parts) {
    return parts.filter(part => part.type === "image" || part.type === "file")
      .map(part => window.PortalI18n.text`${part.type === "image" ? window.PortalI18n.t("图片") : window.PortalI18n.t("文件")}附件：${part.name || part.type}`).join("；");
  }

  function composerPreviewReference(item) {
    if (!item || typeof item !== "object") return null;
    const isPreviewRef = item.preview_selection
      || item.reference_text
      || String(item.content_type || "").toLowerCase() === "text/x-portal-preview-reference";
    if (!isPreviewRef) return null;
    const ref = item.preview_selection || item.reference || {};
    const path = String(ref.path || item.path || item.url || "").trim();
    const text = String(ref.text || item.reference_text || "").trim();
    const start = Number(ref.line_start || ref.lineStart || item.line_start || item.lineStart || 0);
    const end = Number(ref.line_end || ref.lineEnd || item.line_end || item.lineEnd || start || 0);
    return {
      path,
      text,
      title: String(ref.title || item.name || path || window.PortalI18n.t("预览")).trim(),
      start,
      end,
      sourceMessageIds: Array.isArray(ref.source_message_ids)
        ? ref.source_message_ids.map(value => String(value || "").trim()).filter(Boolean)
        : [],
    };
  }

  function composerReferenceLineLabel(ref) {
    if (!ref?.start) return "";
    return ref.end && ref.end !== ref.start ? `${ref.start}-${ref.end}` : String(ref.start);
  }

  function composerReferenceDisplayName(ref, item) {
    if (ref?.sourceMessageIds?.length && ref.title) return ref.title;
    const raw = String(ref?.path || ref?.title || item?.name || "预览").trim();
    const base = raw.split("/").filter(Boolean).pop() || raw || "预览";
    return base.replace(/^引用\s+/, "").replace(/#L\d+(?:-L\d+)?$/, "");
  }

  function composerAttachmentPlaceholder(itemOrKind, id = "") {
    const kind = typeof itemOrKind === "string" ? itemOrKind : String(itemOrKind?.kind || "image");
    const safeKind = kind === "file" ? "file" : "image";
    const attachmentId = typeof itemOrKind === "string" ? id : (itemOrKind?.id || itemOrKind?.file_id || "");
    return `[[portal-${safeKind}:${String(attachmentId || "").replace(/\]/g, "")}]]`;
  }

  function composerReferenceText(item) {
    const ref = item?.preview_selection || item?.reference || null;
    const text = String(ref?.text || item?.reference_text || "").trim();
    if (!ref && !text) return "";
    const path = String(ref?.path || item?.path || item?.url || item?.name || "").trim();
    if (!text && !path) return "";
    const start = Number(ref?.line_start || ref?.lineStart || item?.line_start || item?.lineStart || 0);
    const end = Number(ref?.line_end || ref?.lineEnd || item?.line_end || item?.lineEnd || start || 0);
    const linePart = start ? `#L${start}${end && end !== start ? `-L${end}` : ""}` : "";
    const header = `[引用: ${path || "预览"}${linePart}]`;
    if (!text) return header;
    const quote = text.split(/\r?\n/).map(line => `> ${line}`).join("\n");
    return `${header}\n${quote}`;
  }

  function composerAttachmentTokenOrder(input) {
    const ids = [];
    input?.querySelectorAll?.("[data-portal-composer-attachment-token]")?.forEach(node => {
      const id = String(node.dataset.portalComposerAttachmentToken || "").trim();
      if (id) ids.push(id);
    });
    return ids;
  }

  function removeComposerAttachmentRef(input, id) {
    if (!input || !id) return;
    input.querySelectorAll(`[data-portal-composer-attachment-token="${CSS.escape(String(id))}"]`).forEach(node => node.remove());
    input.dispatchEvent?.(new Event("input", { bubbles: true }));
  }

  function normalizeSlashCommands(commands) {
    // The session gives the commands it answers; a session that gives none has no menu.
    const raw = Array.isArray(commands) ? commands : [];
    const seen = new Set();
    return raw.map(item => {
      const command = String(item?.command || item?.value || item?.name || "").trim();
      if (!command || !command.startsWith("/") || seen.has(command)) return null;
      seen.add(command);
      return {
        command,
        title: String(item?.title || item?.label || command.slice(1) || command).trim(),
        description: String(item?.description || item?.hint || "").trim(),
      };
    }).filter(Boolean);
  }

  // A workspace that offers @ candidates (options.mentionCandidates) lists
  // them while the word before the caret starts with "@"; choosing one
  // replaces that word (decision_a6bebf4ac283).
  const MENTION_TOKEN_RE = /(?:^|[\s(（，,。:：])@([^\s@]{0,40})$/u;

  function composerMentionToken(input) {
    const selection = window.getSelection?.();
    if (!input || !selection || !selection.rangeCount || !selection.isCollapsed) return null;
    const range = selection.getRangeAt(0);
    const node = range.startContainer;
    if (node?.nodeType !== Node.TEXT_NODE || !input.contains(node)) return null;
    const match = MENTION_TOKEN_RE.exec(String(node.textContent || "").slice(0, range.startOffset));
    if (!match) return null;
    return { node, start: range.startOffset - match[1].length - 1, end: range.startOffset, query: match[1] };
  }

  function bindComposerAttachmentActions(root, options = {}) {
    if (!root || root.dataset.portalComposerAttachmentActionsBound === "1") return;
    root.dataset.portalComposerAttachmentActionsBound = "1";
    root.addEventListener("click", event => {
      const retry = event.target.closest("[data-retry-portal-composer-attachment]");
      if (retry) {
        event.preventDefault();
        event.stopPropagation();
        options.onRetry?.(retry.dataset.retryPortalComposerAttachment || "");
        return;
      }
      const btn = event.target.closest("[data-remove-portal-composer-attachment]");
      if (!btn) return;
      event.preventDefault();
      event.stopPropagation();
      options.onRemove?.(btn.dataset.removePortalComposerAttachment || "");
    });
  }

  // The composer inputs these helpers take are contenteditable elements.
  function inputText(input) {
    if (!input) return "";
    return String(input.innerText || input.textContent || "").replace(/\u00a0/g, " ").trim();
  }

  function inputMutationValue(input) {
    return String(input?.innerHTML || "");
  }

  function setInputText(input, value, { focus = true } = {}) {
    if (!input) return;
    input.textContent = String(value || "");
    if (focus) placeInputCaretAtEnd(input);
  }

  function nodePathWithin(root, node) {
    if (!root || !node) return null;
    if (node === root) return [];
    const path = [];
    let current = node;
    while (current && current !== root) {
      const parent = current.parentNode;
      if (!parent) return null;
      path.unshift(Array.prototype.indexOf.call(parent.childNodes, current));
      current = parent;
    }
    return current === root ? path : null;
  }

  function nodeFromPath(root, path) {
    let current = root;
    for (const index of Array.isArray(path) ? path : []) {
      const next = current?.childNodes?.[Number(index)];
      if (!next) return null;
      current = next;
    }
    return current || null;
  }

  function composerSelectionSnapshot(input) {
    if (!input) return null;
    const selection = window.getSelection?.();
    if (!selection || selection.rangeCount !== 1) return null;
    const range = selection.getRangeAt(0);
    if (!input.contains(range.startContainer) && input !== range.startContainer) return null;
    if (!input.contains(range.endContainer) && input !== range.endContainer) return null;
    const startPath = nodePathWithin(input, range.startContainer);
    const endPath = nodePathWithin(input, range.endContainer);
    if (!startPath || !endPath) return null;
    return {
      mode: "dom",
      startPath,
      startOffset: range.startOffset,
      endPath,
      endOffset: range.endOffset,
    };
  }

  function placeInputCaretAtEnd(input) {
    if (!input) return;
    input.focus?.();
    const range = document.createRange();
    range.selectNodeContents(input);
    range.collapse(false);
    const selection = window.getSelection?.();
    selection?.removeAllRanges();
    selection?.addRange(range);
  }

  function inputRangeFromPoint(event, input) {
    if (!event || !input) return null;
    if (document.caretRangeFromPoint) return document.caretRangeFromPoint(event.clientX, event.clientY);
    if (document.caretPositionFromPoint) {
      const position = document.caretPositionFromPoint(event.clientX, event.clientY);
      if (!position) return null;
      const range = document.createRange();
      range.setStart(position.offsetNode, position.offset);
      range.collapse(true);
      return range;
    }
    return null;
  }

  function placeInputCaretFromPoint(input, event) {
    if (!input) return false;
    const range = inputRangeFromPoint(event, input);
    if (!range || !input.contains(range.commonAncestorContainer)) {
      input.focus?.();
      return false;
    }
    input.focus?.();
    const selection = window.getSelection?.();
    selection?.removeAllRanges();
    selection?.addRange(range);
    return true;
  }

  function restoreComposerSelection(input, snapshot) {
    if (!input) return false;
    input.focus?.();
    if (snapshot?.mode === "dom") {
      const startNode = nodeFromPath(input, snapshot.startPath);
      const endNode = nodeFromPath(input, snapshot.endPath);
      if (startNode && endNode) {
        try {
          const range = document.createRange();
          range.setStart(startNode, Math.min(Number(snapshot.startOffset || 0), startNode.length ?? startNode.childNodes?.length ?? 0));
          range.setEnd(endNode, Math.min(Number(snapshot.endOffset || 0), endNode.length ?? endNode.childNodes?.length ?? 0));
          const selection = window.getSelection?.();
          selection?.removeAllRanges();
          selection?.addRange(range);
          return true;
        } catch {}
      }
    }
    placeInputCaretAtEnd(input);
    return false;
  }

  function inputCaretLineState(input) {
    if (!input) return { selectable: false };
    const selection = window.getSelection?.();
    if (document.activeElement === input && !String(input.innerText || input.textContent || "").trim()) {
      return { selectable: true, atFirstLine: true, atLastLine: true };
    }
    if (!selection || selection.rangeCount !== 1 || !selection.isCollapsed) return { selectable: false };
    const range = selection.getRangeAt(0);
    if (!input.contains(range.startContainer)) return { selectable: false };
    // Wrapped contenteditable lines have no newline, so compare rendered line positions.
    const style = window.getComputedStyle?.(input);
    const fontSize = Number.parseFloat(style?.fontSize || "") || 13;
    const lineHeight = Number.parseFloat(style?.lineHeight || "") || fontSize * 1.4;
    const usableRects = targetRange => Array.from(targetRange.getClientRects?.() || []).filter(rect => (
      Number.isFinite(rect.top)
      && Number.isFinite(rect.bottom)
      && (rect.width > 0 || rect.height > 0)
      && rect.height <= lineHeight * 1.75
    ));
    const caretRects = usableRects(range);
    const caretRect = caretRects[caretRects.length - 1];
    if (!caretRect) {
      const content = document.createRange();
      content.selectNodeContents(input);
      const lineCenters = usableRects(content)
        .map(rect => (rect.top + rect.bottom) / 2)
        .sort((left, right) => left - right)
        .filter((center, index, centers) => index === 0 || center - centers[index - 1] > lineHeight * 0.45);
      if (lineCenters.length <= 1) return { selectable: true, atFirstLine: true, atLastLine: true };
      const rootOffset = range.startContainer === input ? Number(range.startOffset || 0) : null;
      return {
        selectable: true,
        atFirstLine: rootOffset === 0,
        atLastLine: rootOffset === input.childNodes.length,
        positionUnknown: true,
      };
    }
    const before = range.cloneRange();
    before.selectNodeContents(input);
    before.setEnd(range.startContainer, range.startOffset);
    const after = range.cloneRange();
    after.selectNodeContents(input);
    after.setStart(range.endContainer, range.endOffset);
    const caretCenter = (caretRect.top + caretRect.bottom) / 2;
    const differentLineThreshold = Math.max(4, lineHeight * 0.45);
    return {
      selectable: true,
      atFirstLine: !usableRects(before).some(rect => (
        (rect.top + rect.bottom) / 2 < caretCenter - differentLineThreshold
      )),
      atLastLine: !usableRects(after).some(rect => (
        (rect.top + rect.bottom) / 2 > caretCenter + differentLineThreshold
      )),
    };
  }

  function isComposerImeActive(input, event = null) {
    return Boolean(
      event?.isComposing
      || event?.keyCode === 229
      || input?.dataset?.portalWorkspaceComposerComposing === "1"
    );
  }

  function clipboardAttachmentFiles(clipboardData) {
    const files = [];
    const seen = new Set();
    const pushFile = (file) => {
      if (!file) return;
      const type = String(file.type || "").toLowerCase();
      const size = Number(file.size || 0);
      const name = String(file.name || "").trim();
      const key = type.startsWith("image/")
        ? `image:${type}:${size}`
        : `file:${name}:${type}:${size}:${file.lastModified || 0}`;
      if (seen.has(key)) return;
      seen.add(key);
      files.push(file);
    };
    Array.from(clipboardData?.files || []).forEach(pushFile);
    Array.from(clipboardData?.items || []).forEach((item) => {
      if (item.kind && item.kind !== "file") return;
      pushFile(item.getAsFile?.());
    });
    return files;
  }

  function clipboardPlainText(clipboardData) {
    const text = String(clipboardData?.getData?.("text/plain") || "");
    if (text) return text;
    const html = String(clipboardData?.getData?.("text/html") || "");
    if (!html) return "";
    const node = document.createElement("div");
    node.innerHTML = html;
    return String(node.innerText || node.textContent || "");
  }

  function consumeWorkspaceInputEvent(event) {
    if (!event) return;
    event[WORKSPACE_PASTE_HANDLED_FLAG] = true;
    event.preventDefault?.();
    event.stopImmediatePropagation?.();
    event.stopPropagation?.();
  }

  function insertPlainText(input, text, options = {}) {
    const value = String(text || "");
    if (!input || !value) return "none";
    input.focus?.();
    if (!options.forceDom) {
      try {
        if (document.execCommand?.("insertText", false, value)) return "native";
      } catch {}
    }
    const selection = window.getSelection?.();
    let range = selection && selection.rangeCount ? selection.getRangeAt(0) : null;
    if (!range || !input.contains(range.commonAncestorContainer)) {
      range = document.createRange();
      range.selectNodeContents(input);
      range.collapse(false);
    }
    range.deleteContents();
    const node = document.createTextNode(value);
    range.insertNode(node);
    range.setStartAfter(node);
    range.collapse(true);
    selection?.removeAllRanges();
    selection?.addRange(range);
    input.dispatchEvent?.(new Event("input", { bubbles: true }));
    return "manual";
  }

  function applyAttachmentPaste(input, event, files, text, options = {}, textOptions = {}) {
    consumeWorkspaceInputEvent(event);
    options.onBeforePaste?.({ files, text, event });
    const manualTextUndoFrame = !files.length && text ? options.onBeforeManualTextPaste?.() : null;
    if (files.length && options.onFiles) options.onFiles(files, event);
    if (text) {
      const insertMode = options.onText
        ? options.onText(text, event, { forceDom: false, ...textOptions })
        : insertPlainText(input, text, { forceDom: false, ...textOptions });
      if (manualTextUndoFrame) {
        if (insertMode === "native") options.onCancelManualTextPaste?.(manualTextUndoFrame);
        else options.onAfterManualTextPaste?.(manualTextUndoFrame);
      }
    }
    return true;
  }

  function handleAttachmentPaste(input, event, options = {}) {
    if (event && event[WORKSPACE_PASTE_HANDLED_FLAG]) return false;
    const files = clipboardAttachmentFiles(event?.clipboardData);
    const text = clipboardPlainText(event?.clipboardData);
    const interceptTextOnly = options.interceptTextOnlyPaste !== false;
    if (!files.length && (!text || !interceptTextOnly)) return false;
    if (!files.length && !text) return false;
    return applyAttachmentPaste(input, event, files, text, options);
  }

  async function systemClipboardPayload() {
    const files = [];
    let text = "";
    let readError = null;
    const pushBlob = (blob, name = "") => {
      if (!blob) return;
      const type = String(blob.type || "application/octet-stream");
      if (type === "text/html" || type === "text/rtf") return;
      const fallbackName = type.startsWith("image/") ? "clipboard-image" : "clipboard-attachment";
      const ext = type.split("/")[1]?.split(";")[0] || "bin";
      files.push(new File([blob], name || `${fallbackName}.${ext}`, { type }));
    };
    if (navigator.clipboard?.read) {
      try {
        const items = await navigator.clipboard.read();
        for (const item of items || []) {
          for (const type of item.types || []) {
            const blob = await item.getType(type);
            if (type === "text/plain" && !text) {
              text = await blob.text();
            } else {
              pushBlob(blob);
            }
          }
        }
      } catch (error) {
        readError = error;
      }
    }
    if (!text && navigator.clipboard?.readText) {
      try {
        text = await navigator.clipboard.readText();
      } catch {
        // Binary clipboard reads can succeed even when readText is unavailable.
      }
    }
    if (!files.length && !text && readError) throw readError;
    return { files, text };
  }

  async function pasteInputFromSystemClipboard(input, event, options = {}) {
    if (!input) return false;
    placeInputCaretFromPoint(input, event);
    const targetSelection = composerSelectionSnapshot(input);
    const payload = await systemClipboardPayload();
    const files = Array.isArray(payload.files) ? payload.files : [];
    const text = String(payload.text || "");
    if (!files.length && !text) return false;
    if (targetSelection) restoreComposerSelection(input, targetSelection);
    if (files.length) options.onFiles?.(files, event);
    if (text) {
      const manualTextUndoFrame = !files.length ? options.onBeforeManualTextPaste?.() : null;
      let insertMode = "custom";
      if (options.onPasteText) {
        insertMode = options.onPasteText(text, event) || "custom";
      }
      else insertMode = insertPlainText(input, text, { forceDom: false });
      if (manualTextUndoFrame) {
        if (insertMode === "native") options.onCancelManualTextPaste?.(manualTextUndoFrame);
        else options.onAfterManualTextPaste?.(manualTextUndoFrame);
      }
      const afterTextSelection = composerSelectionSnapshot(input);
      const afterPasteSignature = inputMutationValue(input);
      const restoreAfterContextMenu = () => {
        if (document.activeElement !== input) return;
        if (inputMutationValue(input) !== afterPasteSignature) return;
        if (afterTextSelection) restoreComposerSelection(input, afterTextSelection);
        else placeInputCaretAtEnd(input);
      };
      [0, 50].forEach(delay => window.setTimeout(restoreAfterContextMenu, delay));
    }
    return true;
  }

  function handleAttachmentDrop(event, options = {}) {
    const files = Array.from(event?.dataTransfer?.files || []).filter(Boolean);
    if (!files.length) return false;
    event.preventDefault();
    if (options.onFiles) options.onFiles(files, event);
    return true;
  }

  function handleAttachmentDragOver(event) {
    if (Array.from(event?.dataTransfer?.types || []).includes("text/plain")) {
      event.preventDefault();
      if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
      return true;
    }
    if (!event?.dataTransfer?.files?.length) return false;
    event.preventDefault();
    return true;
  }

  function bindWorkspaceComposer(options = {}) {
    const input = options.input || null;
    if (!input) return;
    input.__portalWorkspaceComposerOptions = options;
    const currentOptions = () => input.__portalWorkspaceComposerOptions || {};
    const bindControl = (node, key, handler) => {
      if (!node || node.dataset[key] === "1") return;
      node.dataset[key] = "1";
      node.addEventListener("click", handler);
    };
    bindControl(options.attachButton, "portalWorkspaceAttachBound", () => currentOptions().fileInput?.click());
    if (options.fileInput && options.fileInput.dataset.portalWorkspaceFileBound !== "1") {
      options.fileInput.dataset.portalWorkspaceFileBound = "1";
      options.fileInput.addEventListener("change", () => {
        const active = currentOptions();
        const files = Array.from(active.fileInput?.files || []);
        if (files.length) active.onFiles?.(files);
        if (active.fileInput) active.fileInput.value = "";
      });
    }
    const submit = event => {
      if (isComposerImeActive(input, event)) return;
      const active = currentOptions();
      if (active.canSend && !active.canSend()) return;
      active.onSend?.();
    };
    bindControl(options.sendButton, "portalWorkspaceSendBound", submit);
    bindControl(options.stopButton, "portalWorkspaceStopBound", () => currentOptions().onStop?.());
    if (input.dataset.portalWorkspaceComposerBound === "1") return;
    input.dataset.portalWorkspaceComposerBound = "1";
    input.dataset.portalWorkspaceComposerFocused = document.activeElement === input ? "1" : "0";
    input.dataset.portalWorkspaceComposerComposing = "0";
    const scope = () => currentOptions().historyScope ? String(currentOptions().historyScope() || "default") : "default";
    const notifyActivity = (event = null) => {
      currentOptions().onInputActivityChange?.({
        input,
        event,
        focused: document.activeElement === input,
        composing: input.dataset.portalWorkspaceComposerComposing === "1",
      });
    };
    const setFocused = (value, event) => {
      input.dataset.portalWorkspaceComposerFocused = value ? "1" : "0";
      notifyActivity(event);
    };
    const setComposing = (value, event) => {
      input.dataset.portalWorkspaceComposerComposing = value ? "1" : "0";
      notifyActivity(event);
    };
    input.addEventListener("dragstart", event => {
      const target = event.target instanceof Element ? event.target : null;
      const node = target?.closest?.("[data-portal-composer-attachment-token]");
      if (!node || !input.contains(node)) return;
      const token = node.dataset.portalComposerAttachmentToken || "";
      if (!token) return;
      if (!event.dataTransfer) return;
      event.dataTransfer.setData("text/plain", `portal-attachment:${token}`);
      event.dataTransfer.effectAllowed = "move";
    });
    input.addEventListener("dragover", event => handleAttachmentDragOver(event));
    input.addEventListener("drop", event => {
      const raw = event.dataTransfer?.getData("text/plain") || "";
      if (raw.startsWith("portal-attachment:") && currentOptions().onMoveAttachment?.(raw.slice("portal-attachment:".length), event)) return;
      handleAttachmentDrop(event, {
        onFiles: files => currentOptions().onFiles?.(files),
      });
    });
    input.addEventListener("focus", event => setFocused(true, event));
    input.addEventListener("blur", event => setFocused(false, event));
    input.addEventListener("compositionstart", event => setComposing(true, event));
    input.addEventListener("compositionend", event => setComposing(false, event));
    const pasteOptions = {
      onBeforePaste: context => currentOptions().onBeforePaste?.(context),
      onFiles: files => currentOptions().onFiles?.(files),
      onText: (text, _event, textOptions = {}) => {
        if (currentOptions().onPasteText) return currentOptions().onPasteText(text, _event) || "custom";
        return insertPlainText(input, text, { ...textOptions });
      },
      onBeforeManualTextPaste: context => currentOptions().onBeforeManualTextPaste?.(context),
      onCancelManualTextPaste: context => currentOptions().onCancelManualTextPaste?.(context),
      onAfterManualTextPaste: context => currentOptions().onAfterManualTextPaste?.(context),
    };
    input.addEventListener("paste", event => handleAttachmentPaste(input, event, pasteOptions));
    input.addEventListener("contextmenu", event => {
      if (currentOptions().rightClickPaste === false) return;
      // An HTTP page off localhost has no Clipboard API: the browser's own
      // menu still pastes there, through the paste event.
      if (!navigator.clipboard?.read && !navigator.clipboard?.readText) return;
      event.preventDefault();
      pasteInputFromSystemClipboard(input, event, {
        onFiles: files => currentOptions().onFiles?.(files),
        // An input without its own text handler takes the text as plain text.
        onPasteText: currentOptions().onPasteText ? (...args) => currentOptions().onPasteText(...args) : null,
        onBeforeManualTextPaste: (...args) => currentOptions().onBeforeManualTextPaste?.(...args),
        onCancelManualTextPaste: (...args) => currentOptions().onCancelManualTextPaste?.(...args),
        onAfterManualTextPaste: (...args) => currentOptions().onAfterManualTextPaste?.(...args),
      }).catch(error => {
        if (currentOptions().onClipboardPasteError) currentOptions().onClipboardPasteError(error);
        else window.PortalUI.toast(window.PortalI18n.t("浏览器没有开放剪贴板读取权限，请使用 Ctrl/⌘ + V 粘贴。"), "warning");
      });
    });
    input.addEventListener("keydown", event => {
      if (isComposerImeActive(input, event)) return;
      if (currentOptions().onBeforeKeydown?.(event)) return;
      if ((event.ctrlKey || event.metaKey) && !event.shiftKey && !event.altKey && String(event.key || "").toLowerCase() === "z") {
        if (currentOptions().onUndo?.(event)) {
          event.preventDefault();
          return;
        }
      }
      if (currentOptions().history?.handleKey?.(event, input, scope())) {
        currentOptions().onInput?.(event, { source: "history" });
        return;
      }
      if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
        event.preventDefault();
        submit(event);
      }
    });
    input.addEventListener("input", event => {
      currentOptions().onInput?.(event, {
        source: event.detail?.source === "history" ? "history" : "input",
      });
    });
  }

  function createComposer(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalChatWorkspaceComposer is missing: ${missing.join(", ")}`);
    const {
      attachmentKind, attachmentPreviewUrl, bindImagePreview, bindResourceChips, escapeHtml,
      messageAvatarPlaceholderHtml,
    } = dependencies;

    function inputMediaPayload(items) {
      return normalizeComposerAttachments(items)
        .filter(item => item.upload_id && !item.uploading && !item.error)
        .map(item => ({
          media_type: attachmentKind(item),
          upload_id: String(item.upload_id),
          mime_type: String(item.content_type || item.mime || ""),
          name: String(item.name || (attachmentKind(item) === "image" ? "image" : "attachment")),
        }));
    }

    function composerPayloadContentParts(payload = {}) {
      const parts = Array.isArray(payload.content_parts)
        ? payload.content_parts.map(part => ({ ...part }))
        : [
          { type: "text", text: String(payload.text || payload.plainText || "") },
          ...inputMediaPayload(payload.attachments).map(media => ({ ...media, type: media.media_type })),
        ];
      if (!parts.some(part => part.type === "text" && String(part.text || "").trim())) {
        const text = mediaOnlyCaption(parts);
        if (text) parts.unshift({ type: "text", text });
      }
      return parts.filter(part => part.type !== "text" || String(part.text || ""));
    }

    function composerAttachmentRefNode(item, options = {}) {
      const id = String(item?.id || "");
      const kind = attachmentKind(item);
      const preview = attachmentPreviewUrl(item);
      const previewRef = composerPreviewReference(item);
      const span = document.createElement("span");
      span.className = `composerImageRef ${item?.uploading ? "uploading" : ""} ${item?.error ? "error" : ""}`;
      span.contentEditable = "false";
      span.draggable = true;
      span.dataset.portalComposerAttachmentToken = id;
      span.dataset.composerAttachmentKind = kind;
      if (item?.path) span.dataset.portalAttachmentPath = item.path;
      if (preview && kind === "image") span.dataset.portalPreviewImage = preview;
      span.title = item?.error || item?.path || item?.name || kind;
      let label = item?.name || kind;
      if (previewRef) {
        const lineLabel = composerReferenceLineLabel(previewRef);
        span.title = [previewRef.path || previewRef.title, lineLabel ? window.PortalI18n.text`L${lineLabel}` : ""].filter(Boolean).join(" ");
        label = `${composerReferenceDisplayName(previewRef, item)}${lineLabel ? ` L${lineLabel}` : ""}`;
      }
      const media = preview && kind === "image"
        ? window.PortalI18n.html`<img class="${options.thumbClass || "composerImageRefThumb"}" src="${escapeHtml(preview)}" alt="" />`
        : window.PortalI18n.html`<span class="${options.iconClass || "composerFileRefIcon"}">${kind === "file" ? "F" : "I"}</span>`;
      span.innerHTML = window.PortalI18n.html`${media}<span class="${options.labelClass || "composerImageRefLabel"}">${escapeHtml(label)}</span><button class="${options.removeClass || "composerImageRefRemove"}" type="button" data-remove-portal-composer-attachment="${escapeHtml(id)}" aria-label="移除附件" data-portal-icon="close"></button>`;
      return span;
    }

    function insertComposerAttachmentRef(input, item, options = {}) {
      if (!input || !item?.id) return "none";
      input.focus?.();
      const node = composerAttachmentRefNode(item, options);
      if (!options.forceDom) {
        try {
          if (document.execCommand?.("insertHTML", false, `${node.outerHTML} `)) {
            bindImagePreview(input, options);
            return "native";
          }
        } catch {}
      }
      const spacer = document.createTextNode(" ");
      const selection = window.getSelection?.();
      let range = selection && selection.rangeCount ? selection.getRangeAt(0) : null;
      if (!range || !input.contains(range.commonAncestorContainer)) {
        range = document.createRange();
        range.selectNodeContents(input);
        range.collapse(false);
      }
      range.deleteContents();
      range.insertNode(spacer);
      range.insertNode(node);
      range.setStartAfter(spacer);
      range.collapse(true);
      selection?.removeAllRanges();
      selection?.addRange(range);
      input.dispatchEvent?.(new Event("input", { bubbles: true }));
      bindImagePreview(input, options);
      return "manual";
    }

    function moveComposerAttachmentRef(input, token, event) {
      const id = String(token || "").trim();
      if (!input || !id) return false;
      const node = input.querySelector(`[data-portal-composer-attachment-token="${CSS.escape(id)}"]`);
      if (!node) return false;
      const range = inputRangeFromPoint(event, input);
      if (!range || !input.contains(range.commonAncestorContainer) || node.contains(range.startContainer)) return false;
      event?.preventDefault?.();
      const spacer = document.createTextNode(" ");
      node.remove();
      range.insertNode(spacer);
      range.insertNode(node);
      range.setStartAfter(spacer);
      range.collapse(true);
      const selection = window.getSelection?.();
      selection?.removeAllRanges();
      selection?.addRange(range);
      input.dispatchEvent?.(new Event("input", { bubbles: true }));
      bindImagePreview(input);
      return true;
    }

    function refreshComposerAttachmentRefs(input, items, options = {}) {
      if (!input) return;
      const byId = new Map((Array.isArray(items) ? items : []).map(item => [String(item?.id || ""), item]));
      input.querySelectorAll("[data-portal-composer-attachment-token]").forEach(node => {
        const id = String(node.dataset.portalComposerAttachmentToken || "");
        const item = byId.get(id);
        if (!item) {
          node.remove();
          return;
        }
        node.replaceWith(composerAttachmentRefNode(item, options));
      });
      bindImagePreview(input, options);
    }

    function ensureComposerAttachmentRefs(input, items, options = {}) {
      if (!input) return;
      const existing = new Set();
      input.querySelectorAll("[data-portal-composer-attachment-token]").forEach(node => {
        const id = String(node.dataset.portalComposerAttachmentToken || "");
        if (id) existing.add(id);
      });
      let inserted = false;
      for (const item of Array.isArray(items) ? items : []) {
        const id = String(item?.id || "");
        if (!id || existing.has(id)) continue;
        if (input.childNodes.length) input.appendChild(document.createTextNode(" "));
        input.appendChild(composerAttachmentRefNode(item, options));
        input.appendChild(document.createTextNode(" "));
        existing.add(id);
        inserted = true;
      }
      if (inserted) {
        bindImagePreview(input, options);
        if (options.dispatchInput) input.dispatchEvent?.(new Event("input", { bubbles: true }));
      }
    }

    function syncComposerAttachmentRefs(input, items, options = {}) {
      refreshComposerAttachmentRefs(input, items, options);
      ensureComposerAttachmentRefs(input, items, options);
    }

    function serializeComposerInput(input, items, options = {}) {
      const itemMap = new Map((Array.isArray(items) ? items : []).map(item => [String(item?.id || ""), item]));
      const mode = options.mode || "final";
      const isBlockNode = (node) => node?.nodeType === Node.ELEMENT_NODE && (node.tagName === "DIV" || node.tagName === "P");
      const appendSerializedChild = (text, child) => {
        if (isBlockNode(child) && text && !text.endsWith("\n")) text += "\n";
        return text + walk(child);
      };
      const walk = (node) => {
        if (!node) return "";
        if (node.nodeType === Node.TEXT_NODE) return node.nodeValue || "";
        if (node.nodeType !== Node.ELEMENT_NODE) return "";
        const el = node;
        if (el.dataset?.mentionValue) return el.dataset.mentionValue;
        const token = el.dataset?.portalComposerAttachmentToken || "";
        if (token) {
          if (mode === "plain") return "";
          const item = itemMap.get(String(token));
          const kind = attachmentKind(item || { kind: el.dataset?.composerAttachmentKind || "image" });
          if (mode === "draft") return composerAttachmentPlaceholder(kind, token);
          if (!item || item.uploading || item.error) return "";
          const referenceText = composerReferenceText(item);
          if (referenceText) return referenceText;
          const path = String(item?.path || item?.url || "").trim();
          return path ? `[${kind}: ${path}]` : "";
        }
        if (el.tagName === "BR") return "\n";
        let text = "";
        el.childNodes.forEach(child => { text = appendSerializedChild(text, child); });
        if (el.tagName === "DIV" || el.tagName === "P") text += "\n";
        return text;
      };
      if (!input) return "";
      let text = "";
      input.childNodes.forEach(child => { text = appendSerializedChild(text, child); });
      const normalized = text.replace(/\u00a0/g, " ");
      return mode === "draft" ? normalized : normalized.trim();
    }

    function serializeComposerContentParts(input, items) {
      const text = serializeComposerInput(input, items, { mode: "draft" });
      const byId = new Map(normalizeComposerAttachments(items).map(item => [item.id, item]));
      const parts = [];
      const appendText = value => {
        if (!value) return;
        const previous = parts[parts.length - 1];
        if (previous?.type === "text") previous.text += value;
        else parts.push({ type: "text", text: value });
      };
      let cursor = 0;
      COMPOSER_DRAFT_ATTACHMENT_RE.lastIndex = 0;
      for (const match of text.matchAll(COMPOSER_DRAFT_ATTACHMENT_RE)) {
        appendText(text.slice(cursor, match.index));
        const item = byId.get(match[2]);
        if (item && !item.uploading && !item.error) {
          const reference = composerReferenceText(item);
          const media = reference ? null : inputMediaPayload([item])[0];
          if (media) parts.push({ ...media, type: media.media_type });
          else appendText(reference || (item.path ? `[${attachmentKind(item)}: ${item.path}]` : ""));
        }
        cursor = match.index + match[0].length;
      }
      appendText(text.slice(cursor));
      return parts;
    }

    function renderComposerAttachmentTray(tray, items, options = {}) {
      if (!tray) return;
      const attachments = (Array.isArray(items) ? items : []).filter(item => !composerPreviewReference(item));
      const chipHtml = (item) => {
        const id = String(item?.id || "");
        const kind = attachmentKind(item);
        const preview = attachmentPreviewUrl(item);
        const progress = Math.max(0, Math.min(100, Number(item?.progress || 0)));
        const status = item?.error || (item?.uploading ? `上传中 ${Math.round(progress)}%` : (item?.path || item?.name || ""));
        const media = preview && kind === "image"
          ? window.PortalI18n.html`<button class="composerImageChipPreview" type="button" data-portal-preview-image="${escapeHtml(preview)}" title="${escapeHtml(item?.name || "image")}" aria-label="放大图片"><img class="composerImageThumb" src="${escapeHtml(preview)}" alt="" /></button>`
          : window.PortalI18n.html`<span class="composerFileThumb">${kind === "file" ? window.PortalI18n.t("文件") : window.PortalI18n.t("图片")}</span>`;
        return window.PortalI18n.html`<div class="composerImageChip ${item?.uploading ? "uploading" : ""} ${item?.error ? "error" : ""}" data-portal-composer-attachment-id="${escapeHtml(id)}" ${item?.path ? `data-portal-attachment-path="${escapeHtml(item.path)}"` : ""} title="${escapeHtml(status)}">
        ${media}
        <div>
          <div class="composerImageName">${escapeHtml(item?.name || kind)}</div>
          <div class="composerImagePath">${escapeHtml(status)}</div>
        </div>
        <span class="composerImageChipActions">${item?.error ? window.PortalI18n.html`<button class="composerImageRetry" type="button" data-retry-portal-composer-attachment="${escapeHtml(id)}" aria-label="重新上传附件" title="重新上传" data-portal-icon="refresh"></button>` : ""}<button class="composerImageRemove" type="button" data-remove-portal-composer-attachment="${escapeHtml(id)}" aria-label="移除附件" title="移除" data-portal-icon="close"></button></span>
        ${item?.uploading ? window.PortalI18n.html`<span class="composerUploadProgress" aria-label="${escapeHtml(status)}"><span style="width: ${progress}%"></span></span>` : ""}
      </div>`;
      };
      tray.innerHTML = attachments.map(chipHtml).join("");
      bindImagePreview(tray, options);
    }

    function slashCommandQuery(input, attachments = []) {
      const text = serializeComposerInput(input, attachments, { mode: "plain" });
      if (!String(text || "").startsWith("/")) return null;
      if (String(text || "").includes("\n")) return null;
      if (/\s/.test(String(text || "").slice(1))) return null;
      return String(text || "").trim().toLowerCase();
    }

    function slashCommandMatches(input, attachments = [], commands = []) {
      const query = slashCommandQuery(input, attachments);
      if (query === null) return [];
      const normalized = normalizeSlashCommands(commands);
      const exact = normalized.filter(item => item.command.toLowerCase() === query);
      const prefix = normalized.filter(item => item.command.toLowerCase().startsWith(query));
      const title = normalized.filter(item => (
        query.length > 1
        && !item.command.toLowerCase().startsWith(query)
        && item.title.toLowerCase().includes(query.slice(1))
      ));
      const merged = [...prefix, ...title];
      return merged.length ? merged.slice(0, 16) : exact.slice(0, 16);
    }

    function slashCommandMenuHtml(items, activeIndex = 0) {
      if (!items.length) return "";
      return window.PortalI18n.html`<div class="composerSlashCommandMenu" role="listbox" aria-label="Portal 命令">
      <div class="composerSlashCommandHeader">Portal 命令</div>
      ${items.map((item, index) => window.PortalI18n.html`<button class="composerSlashCommandItem ${index === activeIndex ? "active" : ""}" type="button" role="option" aria-selected="${index === activeIndex ? "true" : "false"}" data-portal-slash-command="${escapeHtml(item.command)}">
        <span class="composerSlashCommandName">${escapeHtml(item.command)}</span>
        <span class="composerSlashCommandBody">
          <span class="composerSlashCommandTitle">${escapeHtml(item.title)}</span>
          ${item.description ? window.PortalI18n.html`<span class="composerSlashCommandDescription">${escapeHtml(item.description)}</span>` : ""}
        </span>
      </button>`).join("")}
    </div>`;
    }

    // Items: {value, title, label, description, group, user_id, disabled, disabled_reason}.
    // A disabled item shows why it cannot be chosen.
    function mentionMenuHtml(items, activeIndex = 0, title = "", status = "") {
      if (!items.length && !status) return "";
      let group = null;
      return window.PortalI18n.html`<div class="composerSlashCommandMenu composerMentionMenu" role="listbox" aria-label="${escapeHtml(title || window.PortalI18n.t("提及"))}">
      ${items.map((item, index) => {
        const header = item.group !== group || index === 0
          ? window.PortalI18n.html`<div class="composerSlashCommandHeader">${escapeHtml(item.group || title || window.PortalI18n.t("提及"))}</div>`
          : "";
        group = item.group;
        const description = item.disabled ? item.disabled_reason || item.description : item.description;
        const name = String(item.title || item.value);
        const label = String(item.label || item.value);
        const secondary = label.toLocaleLowerCase() === name.toLocaleLowerCase() ? "" : label;
        const tooltip = [name, secondary, description].filter(Boolean).join(" · ");
        // Keep this markup stable as portraits load: arrow navigation must not
        // rebuild the menu. The shared avatar binder fills these placeholders.
        const avatar = item.user_id ? messageAvatarPlaceholderHtml(item.user_id, name)
          : item.kind === "user" ? window.PortalI18n.html`<span class="composerMentionAvatar" aria-hidden="true">${escapeHtml(name.slice(0, 1))}</span>` : "";
        return window.PortalI18n.html`${header}<button class="composerSlashCommandItem composerMentionItem ${index === activeIndex && !item.disabled ? "active" : ""}" type="button" role="option" aria-selected="${index === activeIndex && !item.disabled ? "true" : "false"}" title="${escapeHtml(tooltip)}"${item.disabled ? ' disabled aria-disabled="true"' : ""} data-portal-mention-index="${index}" data-portal-mention="${escapeHtml(item.value)}">
          ${avatar}<span class="composerSlashCommandName">${escapeHtml(name)}</span>
          ${secondary ? window.PortalI18n.html`<span class="composerSlashCommandTitle">${escapeHtml(secondary)}</span>` : ""}
          ${description ? window.PortalI18n.html`<span class="composerSlashCommandDescription">${escapeHtml(description)}</span>` : ""}
        </button>`;
      }).join("")}
      ${status ? window.PortalI18n.html`<div class="composerMentionStatus" role="status">${escapeHtml(status)}</div>` : ""}
    </div>`;
    }

    function setComposerDraftInput(input, value, items, options = {}) {
      if (!input) return;
      const text = String(value || "");
      const attachments = normalizeComposerAttachments(items);
      const byId = new Map();
      attachments.forEach((item) => {
        const ids = [item.id, item.file_id, item.path, item.preview_url].map(value => String(value || "").trim()).filter(Boolean);
        ids.forEach(id => byId.set(id, item));
      });
      input.innerHTML = "";
      let cursor = 0;
      COMPOSER_DRAFT_ATTACHMENT_RE.lastIndex = 0;
      for (const match of text.matchAll(COMPOSER_DRAFT_ATTACHMENT_RE)) {
        const before = text.slice(cursor, match.index);
        if (before) input.appendChild(document.createTextNode(before));
        const item = byId.get(String(match[2] || ""));
        if (item) input.appendChild(composerAttachmentRefNode(item, options));
        else input.appendChild(document.createTextNode(match[0]));
        cursor = match.index + match[0].length;
      }
      const rest = text.slice(cursor);
      if (rest) input.appendChild(document.createTextNode(rest));
      // Older drafts can store attachments separately from their text. Restore
      // those references here instead of relying on a later composer render.
      ensureComposerAttachmentRefs(input, attachments, options);
      // The domain supplies identity labels; the composer owns editing and stable serialization.
      if (options.renderMentions) {
        input.innerHTML = options.renderMentions(input.innerHTML);
        input.querySelectorAll("[data-mention-value]").forEach(node => { node.contentEditable = "false"; });
        bindResourceChips(input, options);
      }
      if (options.focus !== false) placeInputCaretAtEnd(input);
      bindImagePreview(input, options);
    }

    return Object.freeze({
      COMPOSER_DRAFT_ATTACHMENT_RE, normalizeComposerAttachment, normalizeComposerAttachments,
      createConversationSelectionReference, inputMediaPayload, mediaOnlyCaption,
      composerPayloadContentParts, composerPreviewReference, composerAttachmentPlaceholder,
      composerAttachmentTokenOrder, insertComposerAttachmentRef, removeComposerAttachmentRef,
      moveComposerAttachmentRef, refreshComposerAttachmentRefs, syncComposerAttachmentRefs,
      serializeComposerInput, serializeComposerContentParts, renderComposerAttachmentTray,
      normalizeSlashCommands, slashCommandMatches, slashCommandMenuHtml, composerMentionToken,
      mentionMenuHtml, bindComposerAttachmentActions, inputText, setInputText,
      setComposerDraftInput, composerSelectionSnapshot, restoreComposerSelection,
      inputCaretLineState, isComposerImeActive, insertPlainText, handleAttachmentPaste,
      handleAttachmentDrop, handleAttachmentDragOver, bindWorkspaceComposer,
    });
  }

  window.PortalChatWorkspaceComposer = Object.freeze({
    createComposer, dependencies: DEPENDENCIES, COMPOSER_DRAFT_ATTACHMENT_RE,
    normalizeComposerAttachment, normalizeComposerAttachments,
    createConversationSelectionReference, mediaOnlyCaption, composerPreviewReference,
    composerAttachmentPlaceholder, composerAttachmentTokenOrder, removeComposerAttachmentRef,
    normalizeSlashCommands, composerMentionToken, bindComposerAttachmentActions, inputText,
    setInputText, composerSelectionSnapshot, restoreComposerSelection, inputCaretLineState,
    isComposerImeActive, insertPlainText, handleAttachmentPaste, handleAttachmentDrop,
    handleAttachmentDragOver, bindWorkspaceComposer,
  });
})();
