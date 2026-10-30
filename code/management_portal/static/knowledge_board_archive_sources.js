// Knowledge board: the archive workspace's source text and attached files.

function archiveAttachmentLabel(item) {
  const size = Number(item.size_bytes || 0);
  const unit = size > 1024 * 1024 ? `${(size / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(size / 1024))} KB`;
  return `${item.name || "attachment"} · ${unit}`;
}

function archiveAttachmentKind(item) {
  return String(item?.mime || "").startsWith("image/") ? "image" : "file";
}

function archiveHasLoadingAttachments() {
  return (state.archive.attachments || []).some(item => !!item.loading);
}

function renderArchiveSourceTray() {
  const attachments = state.archive.attachments || [];
  const loadingCount = attachments.filter(item => !!item.loading).length;
  el.archiveSourceMeta.textContent = attachments.length
    ? (loadingCount ? window.PortalI18n.text`正在读取 ${loadingCount}/${attachments.length} 个文件` : `${attachments.length} attachment${attachments.length > 1 ? "s" : ""}`)
    : window.PortalI18n.t("支持文本、链接、路径、图片和文件");
  el.archiveSourceTray.innerHTML = attachments.map(item => {
    const isImage = archiveAttachmentKind(item) === "image";
    const preview = isImage && item.data_url ? window.PortalI18n.html`<img class="archiveSourceThumb" src="${escapeHtml(item.data_url)}" alt="" />` : window.PortalI18n.html`<span class="archiveSourceFileIcon">F</span>`;
    const progress = Math.max(0, Math.min(100, Number(item.progress || 0)));
    const loading = !!item.loading;
    const label = loading ? window.PortalI18n.text`读取中 ${Math.round(progress)}% · ${archiveAttachmentLabel(item)}` : archiveAttachmentLabel(item);
    return window.PortalI18n.html`
            <span class="archiveSourceChip${loading ? " loading" : ""}" ${isImage && item.data_url ? `data-portal-preview-image="${escapeHtml(item.data_url)}"` : ""} title="${escapeHtml(label)}">
              ${preview}
              <span>
                <span class="archiveSourceChipName">${escapeHtml(item.name || "attachment")}</span>
                <span class="archiveSourceChipPath">${escapeHtml(label)}</span>
              </span>
              <button class="archiveSourceRemove" type="button" data-archive-remove-attachment="${escapeHtml(item.id)}" aria-label="Remove attachment" data-portal-icon="close"></button>
              ${loading ? window.PortalI18n.html`<span class="archiveSourceProgress" aria-label="${escapeHtml(label)}"><span style="width: ${progress}%"></span></span>` : ""}
            </span>
          `;
  }).join("");
  window.PortalChatWorkspace.bindImagePreview(el.archiveSourceTray);
}

function archiveSourceRefNode(item) {
  const span = document.createElement("span");
  const kind = archiveAttachmentKind(item);
  span.className = "archiveSourceRef";
  span.contentEditable = "false";
  span.dataset.archiveRefId = item.id || "";
  span.dataset.archiveRefKind = kind;
  span.title = item.name || kind;
  if (kind === "image" && item.data_url) {
    const img = document.createElement("img");
    img.className = "archiveSourceRefThumb";
    img.alt = "";
    img.src = item.data_url;
    span.appendChild(img);
  } else {
    const icon = document.createElement("span");
    icon.className = "archiveSourceRefIcon";
    icon.textContent = kind === "file" ? "F" : "I";
    span.appendChild(icon);
  }
  const label = document.createElement("span");
  label.className = "archiveSourceRefLabel";
  label.textContent = kind;
  span.appendChild(label);
  const remove = document.createElement("button");
  remove.className = "archiveSourceRefRemove";
  remove.type = "button";
  remove.dataset.portalIcon = "close";
  remove.setAttribute("aria-label", window.PortalI18n.t("移除附件引用"));
  remove.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    removeArchiveAttachment(item.id || "");
  });
  span.appendChild(remove);
  return span;
}

function appendArchiveSourceRef(item) {
  const input = el.archiveSourceText;
  if (!input) return;
  const kind = archiveAttachmentKind(item);
  if (input.textContent.trim() && !input.textContent.endsWith("\n")) input.appendChild(document.createTextNode("\n"));
  input.appendChild(document.createTextNode(kind === "image" ? "图片: " : "文件: "));
  input.appendChild(archiveSourceRefNode(item));
  input.appendChild(document.createTextNode("\n"));
  input.focus();
}

function removeArchiveAttachment(attachmentId) {
  const target = String(attachmentId || "");
  if (!target) return;
  state.archive.attachments = (state.archive.attachments || []).filter(item => item.id !== target);
  el.archiveSourceText.querySelectorAll(`[data-archive-ref-id="${CSS.escape(target)}"]`).forEach(node => node.remove());
  renderArchiveWorkspace();
}

function archiveSourceRawValue() {
  const input = el.archiveSourceText;
  if (!input) return "";
  function walk(node) {
    if (node.nodeType === Node.TEXT_NODE) return node.nodeValue || "";
    if (node.nodeType !== Node.ELEMENT_NODE) return "";
    const elNode = node;
    if (elNode.dataset?.archiveRefId) {
      const item = (state.archive.attachments || []).find(row => row.id === elNode.dataset.archiveRefId);
      const kind = archiveAttachmentKind(item || {});
      return `[[archive-${kind}:${item?.name || elNode.dataset.archiveRefId}]]`;
    }
    const tag = elNode.tagName;
    let text = "";
    elNode.childNodes.forEach(child => { text += walk(child); });
    if (tag === "DIV" || tag === "P" || tag === "BR") text += "\n";
    return text;
  }
  return walk(input).replace(/\n{3,}/g, "\n\n").trim();
}

function fileToDataUrl(file, onProgress) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onprogress = (event) => {
      if (!event.lengthComputable || !event.total) return;
      onProgress?.(Math.round((event.loaded / event.total) * 100));
    };
    reader.onload = () => {
      onProgress?.(100);
      resolve(String(reader.result || ""));
    };
    reader.onerror = () => reject(reader.error || new Error("file read failed"));
    reader.readAsDataURL(file);
  });
}

function archiveAttachmentDraft(file) {
  return {
    id: `att_${Date.now().toString(36)}_${Math.random().toString(16).slice(2)}`,
    kind: "file",
    name: file.name || "attachment",
    mime: file.type || "application/octet-stream",
    size_bytes: file.size || 0,
    data_url: "",
    loading: true,
    progress: 0,
    file,
  };
}

async function addArchiveFiles(files) {
  const list = Array.from(files || []).filter(Boolean);
  if (!list.length) return;
  const tooLarge = list.find(file => Number(file.size || 0) > 128 * 1024 * 1024);
  if (tooLarge) {
    window.PortalUI.toast(window.PortalI18n.text`文件过大：${tooLarge.name || "attachment"}。当前单文件上限是 128MB。`, "warning");
    return;
  }
  const drafts = list.map(archiveAttachmentDraft);
  state.archive.attachments = [...(state.archive.attachments || []), ...drafts];
  renderArchiveWorkspace();
  for (const draft of drafts) {
    try {
      let lastProgress = -1;
      const dataUrl = await fileToDataUrl(draft.file, (progress) => {
        const item = (state.archive.attachments || []).find(row => row.id === draft.id);
        if (!item) return;
        const nextProgress = Math.max(0, Math.min(100, Number(progress || 0)));
        if (nextProgress === lastProgress) return;
        lastProgress = nextProgress;
        item.progress = nextProgress;
        renderArchiveSourceTray();
      });
      const item = (state.archive.attachments || []).find(row => row.id === draft.id);
      if (!item) continue;
      item.data_url = dataUrl;
      item.loading = false;
      item.progress = 100;
      delete item.file;
      appendArchiveSourceRef(item);
      renderArchiveWorkspace();
    } catch (error) {
      state.archive.attachments = (state.archive.attachments || []).filter(item => item.id !== draft.id);
      renderArchiveWorkspace();
      throw error;
    }
  }
}
