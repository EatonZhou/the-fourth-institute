// Knowledge board: the detail dialog: opening, rendering, editing, saving and deleting an item.

function renderKnowledgeResourceIdentity(item, fallbackId = "") {
  const resourceId = String(item?.kb_id || fallbackId || "").trim();
  // Replace the observed container so the shared owner disposes the previous binding.
  el.mFavorite.replaceChildren();
  if (resourceId) {
    const favorite = document.createElement("span");
    favorite.dataset.resourceFavorite = resourceId;
    el.mFavorite.appendChild(favorite);
  }
  window.PortalResourceHeader?.mount?.(el.mResourceIdentity, {
    title: item?.title || item?.filename || resourceId || window.PortalI18n.t("Knowledge"),
    type: item?.type || "knowledge",
    resource_id: resourceId,
    copy_label: "kb_id",
  }, {
    compact: true,
    headingTag: "h3",
    titleClass: "modalTitle",
    titleId: "mTitle",
    idId: "mResourceId",
  });
  el.mTitle = document.getElementById("mTitle");
}
function initialKnowledgeItemId() {
  const params = new URLSearchParams(window.location.search);
  return String(params.get("item") || "").trim();
}

function knowledgeDetailKind() {
  const match = window.location.pathname.match(/^\/resource\/(skill|experiment|knowledge)(?:\/|$)/);
  return String(match?.[1] || "knowledge");
}

function openModal() {
  detailModalController.open();
  detailResourceGuest?.setResource({
    resourceId: state.detailFileId,
    resourceType: state.selected?.type,
    detailKind: knowledgeDetailKind(),
  });
  detailResourceGuest?.ready();
}

function detailIsDirty() {
  return !!detailDocumentSession?.snapshot?.().dirty;
}

const DETAIL_NON_EDITABLE_KINDS = new Set([
  "audio", "binary", "directory", "image", "office", "pdf", "remote-link", "video",
]);

function detailSelectedFileEditable() {
  return !state.detailReadOnly
    && !state.detailSelectedFileLinked
    && !DETAIL_NON_EDITABLE_KINDS.has(state.detailSelectedFileKind);
}

function disposeDetailPreview() {
  if (detailPreviewRoot) window.PortalPreview?.dispose?.(detailPreviewRoot);
  detailPreviewRoot = null;
}

function initDetailDocumentSession() {
  detailDocumentSession = window.PortalDocumentSession.create({
    canEdit: detailSelectedFileEditable,
    onStateChange: snapshot => {
      state.detailText = String(snapshot.payload?.content || "");
      state.detailDraft = snapshot.draft;
      state.detailEditing = snapshot.editing;
      state.detailSaving = snapshot.saving;
      syncDetailButtons();
    },
    onPresent: payload => {
      if (payload) renderDetailBody();
    },
  });
}

function initDetailResourceGuest() {
  detailResourceGuest = window.PortalResourceDetail.createGuest({
    resourceId: () => state.detailFileId || initialKnowledgeItemId(),
    resourceType: () => state.selected?.type,
    detailKind: knowledgeDetailKind,
    guardClose: () => !detailDocumentSession || detailDocumentSession.guardDiscard(),
  });
}

function usesPackageStyleEditor() {
  return ["skill", "experiment"].includes(String((state.selected || {}).type || ""));
}

async function closeModal(context = {}) {
  if (detailResourceGuest?.embedded()) return detailResourceGuest.requestClose();
  return detailModalController.close(context);
}

function finalizeDetailClose(context = {}) {
  el.mFavorite.replaceChildren();
  state.selected = null;
  state.detailFileId = "";
  state.detailSelectedFileId = "";
  state.detailSelectedFileKind = "markdown";
  state.detailSelectedFileLinked = false;
  state.detailReadOnly = false;
  delete el.modal.dataset.portalLiveTourReadonly;
  state.detailLinkedFiles = new Map();
  state.detailLinkedFileProbes = new Set();
  state.detailLinkDiscoverySequence += 1;
  state.detailExpandedFolders = new Set([""]);
  state.detailPath = "";
  state.detailRawUrl = "";
  state.detailDownloadUrl = "";
  state.detailMediaType = "";
  state.detailLanguage = "";
  disposeDetailPreview();
  detailDocumentSession?.show?.({ status: "empty", payload: null }, { reason: "detail-close" });
  state.detailBodyDraft = "";
  state.detailMetaDraft = null;
  resetDetailGraph();
  resetDetailChat();
  if (!detailResourceGuest?.embedded() && context.updateLocation !== false) {
    detailHistory.close();
  }
}

function syncDetailButtons() {
  const snapshot = detailDocumentSession?.snapshot?.();
  const available = !!snapshot?.payload && !["empty", "loading", "error"].includes(snapshot.status);
  const selectedFile = detailFiles().find(value => String(value.address || "") === state.detailSelectedFileId);
  window.PortalPreview.configureDownloadLink(el.mDownload, {
    url: state.detailDownloadUrl || state.detailRawUrl,
    filename: selectedFile?.relative_path
      || selectedFile?.name
      || state.detailPath
      || state.selected?.filename
      || state.detailSelectedFileId,
  });
  el.mEdit.hidden = !available || !state.detailSelectedFileId
    || state.detailSelectedFileLinked
    || state.detailEditing
    || !detailSelectedFileEditable();
  el.mCancelEdit.hidden = !state.detailEditing;
  el.mSave.hidden = !state.detailEditing;
  el.mSave.disabled = state.detailSaving || !detailIsDirty();
  el.mCancelEdit.disabled = state.detailSaving;
  el.mEdit.disabled = state.detailSaving;
  el.mClose.disabled = state.detailSaving;
  el.mSave.textContent = state.detailSaving ? window.PortalI18n.t("保存中…") : window.PortalI18n.t("保存");
}

function renderDetailBody() {
  disposeDetailPreview();
  syncDetailButtons();
  if (state.detailEditing) {
    if (usesPackageStyleEditor()) {
      el.mText.className = "detailEditorLayout";
      el.mText.innerHTML = window.PortalI18n.html`<textarea class="detailEditor" id="detailEditor" data-detail-body spellcheck="false"></textarea>`;
      const editor = document.getElementById("detailEditor");
      editor.value = state.detailDraft;
      editor.addEventListener("input", updateDetailDraftFromDom);
      setTimeout(() => editor.focus(), 0);
      return;
    }
    if (state.detailSelectedFileId !== state.detailFileId || state.detailSelectedFileKind !== "markdown") {
      el.mText.className = "detailEditorLayout";
      el.mText.innerHTML = window.PortalI18n.html`<textarea class="detailEditor" id="detailEditor" data-detail-body spellcheck="false"></textarea>`;
      const editor = document.getElementById("detailEditor");
      editor.value = state.detailDraft;
      editor.addEventListener("input", updateDetailDraftFromDom);
      setTimeout(() => editor.focus(), 0);
      return;
    }
    const meta = state.detailMetaDraft || parseDetailFrontmatter("");
    el.mText.className = "detailEditorLayout";
    el.mText.innerHTML = window.PortalI18n.html`
            ${renderDetailMetaEditor(meta)}
            <textarea class="detailEditor" id="detailEditor" data-detail-body spellcheck="false"></textarea>
          `;
    const editor = document.getElementById("detailEditor");
    editor.value = state.detailBodyDraft;
    el.mText.querySelectorAll("[data-detail-meta], [data-detail-link], [data-detail-meta-other], [data-detail-body]").forEach(input => {
      input.addEventListener("input", updateDetailDraftFromDom);
      input.addEventListener("change", updateDetailDraftFromDom);
    });
    setTimeout(() => editor.focus(), 0);
    return;
  }
  const markdown = state.detailSelectedFileKind === "markdown";
  const parsed = markdown ? splitDetailMarkdown(state.detailText || "") : null;
  const current = detailFiles().find(value => String(value.address || "") === state.detailSelectedFileId);
  const metadataSchema = String((state.selected || {}).type || "") === "skill"
    && (current?.primary || String(current?.relative_path || "").split("/").pop() === "SKILL.md")
    ? "skill"
    : "knowledge";
  el.mText.className = "detailDocument";
  el.mText.innerHTML = markdown ? detailMetadataHtml(parsed, metadataSchema) : "";
  const previewRoot = document.createElement("div");
  previewRoot.dataset.detailPreviewRoot = "";
  el.mText.appendChild(previewRoot);
  const linkedRawUrl = state.detailPath
    ? `/api/agent-workbench/link-file-raw?path=${encodeURIComponent(state.detailPath)}`
    : "";
  const rawUrl = state.detailRawUrl || (state.detailSelectedFileLinked ? linkedRawUrl : "");
  const downloadUrl = state.detailDownloadUrl || rawUrl;
  detailPreviewRoot = previewRoot;
  const previewHandle = window.PortalPreview.render({
    root: previewRoot,
    document: {
      id: state.detailSelectedFileId,
      title: String(current?.relative_path || state.detailPath || window.PortalI18n.t("Preview")).split("/").pop(),
      path: state.detailPath,
      contentKind: state.detailSelectedFileKind,
      mediaType: state.detailMediaType,
      language: state.detailLanguage,
      content: markdown ? (parsed.body || state.detailText || "") : state.detailText,
      rawUrl,
      downloadUrl,
    },
    context: {
      markdownLayout: "document",
      renderMarkdown,
      unsupportedText: state.detailPath
        ? `此文件已在目录中列出，但不能直接预览：${state.detailPath}`
        : "此文件已在目录中列出，但不能直接预览。",
      bindLinks: root => {
        if (!markdown) return;
        bindSkillInternalFileLinks(el.mText);
        window.PortalFileLinks?.enhance?.(root, {
          basePath: state.detailPath,
          resolvePath: detailFilePathResolver(parsed),
          source: "resource-detail",
          onOpen: state.detailReadOnly
            ? () => openDetailLinkedFile({})
            : openDetailLinkedFile,
          onDiscover: state.detailReadOnly ? undefined : registerDetailLinkedFiles,
        });
      },
    },
  });
  void previewHandle.promise.catch(alertError);
}

function resetDetailEditorDraft() {
  if (!usesPackageStyleEditor()
    && state.detailSelectedFileId === state.detailFileId
    && state.detailSelectedFileKind === "markdown") {
    const parsed = splitDetailMarkdown(state.detailText);
    state.detailMetaDraft = parsed.meta;
    state.detailBodyDraft = parsed.body;
  } else {
    state.detailMetaDraft = null;
    state.detailBodyDraft = state.detailText;
  }
}

async function setDetailEditing(editing) {
  if (!state.detailSelectedFileId || state.detailSaving) return;
  if (editing && !detailSelectedFileEditable()) return;
  if (editing) {
    if (!detailDocumentSession.beginEdit()) return;
    resetDetailEditorDraft();
  } else {
    if (!await detailDocumentSession.cancelEdit()) return;
    state.detailBodyDraft = "";
    state.detailMetaDraft = null;
  }
  renderDetailBody();
}

async function saveDetail() {
  if (!state.detailSelectedFileId || state.detailSelectedFileLinked) return;
  const resourceId = state.detailFileId;
  const address = state.detailSelectedFileId;
  return detailDocumentSession.save({
    save: async ({ draft }) => {
      const data = await knowledgeApi.request("/knowledge/api/detail/save", {
        method: "POST",
        body: JSON.stringify({ kb_id: address, text: draft }),
      });
      return { ...data, content: String(data.text || "") };
    },
    onSaved: async (_payload, saved) => {
      // The session stays in edit mode. Rebuild from the acknowledged text,
      // including server-written metadata, before another input is accepted.
      resetDetailEditorDraft();
      renderDetailBody();
      detailResourceGuest?.changed({ resourceId, kind: "updated" });
      window.PortalUI.toast(window.PortalI18n.t("资源已保存"));
      const stillCurrent = () => {
        const current = detailDocumentSession.snapshot();
        return current.requestId === saved.requestId && current.payload === saved.payload;
      };
      let item = state.selected;
      if (!detailResourceGuest?.embedded()) {
        try {
          await loadIndex({ refresh: true });
        } catch (error) {
          // The write was acknowledged; an index failure must not replay it.
          if (stillCurrent()) alertError(error);
          return;
        }
        if (!stillCurrent()) return;
        item = byId(resourceId) || state.selected;
        state.selected = item;
      }
      if (item) {
        renderKnowledgeResourceIdentity(item, resourceId);
        const file = detailFiles().find(value => String(value.address || "") === address);
        el.mSub.textContent = `${item.project || ""} · ${item.type || ""} · ${file?.relative_path || item.filename || ""}`;
      }
      renderDetailFileTree();
      if (detailGraphEnabled()) loadDetailGraph(resourceId).catch(() => {});
      if (detailChatPayload()) loadDetailChat(resourceId, { silent: true }).catch(() => {});
    },
    onError: (err, failed) => window.PortalUI.toast(window.PortalUI.errorMessage(window.PortalI18n.t("保存"), err), "error", {
      retry: () => {
        const current = detailDocumentSession.snapshot();
        if (current.requestId === failed.requestId && current.payload === failed.payload
          && current.editing && current.draft === failed.draft) return saveDetail();
      },
    }),
  });
}

async function openDetailFile(file, options = {}) {
  const targetFileId = String(file?.address || "").trim();
  if (!targetFileId || !state.detailFileId) return;
  const linkedFile = Boolean(file?.linked);
  const linkedPath = String(file?.path || "").trim();
  if (linkedFile && (!linkedPath || !window.PortalFileLinks)) return;
  const relativeParts = String(file?.relative_path || "")
    .replaceAll("\\", "/")
    .split("/")
    .filter(Boolean);
  let parentPath = "";
  for (const part of relativeParts.slice(0, -1)) {
    parentPath = parentPath ? `${parentPath}/${part}` : part;
    state.detailExpandedFolders.add(parentPath);
  }
  await detailDocumentSession.open({
    key: targetFileId,
    confirmDiscard: options.skipDiscard ? () => true : undefined,
    onLoading: () => {
      state.detailSelectedFileId = targetFileId;
      state.detailSelectedFileKind = String(file?.kind || "markdown");
      state.detailSelectedFileLinked = linkedFile;
      state.detailPath = linkedFile ? linkedPath : "";
      state.detailRawUrl = "";
      state.detailDownloadUrl = "";
      state.detailMediaType = "";
      state.detailLanguage = "";
      state.detailBodyDraft = "";
      state.detailMetaDraft = null;
      el.mText.className = "markdownBody";
      el.mText.textContent = window.PortalI18n.t("加载中…");
      renderDetailFileTree();
    },
    load: async ({ signal } = {}) => {
      if (linkedFile) {
        return window.PortalFileLinks.load(linkedPath, {
          signal,
        });
      }
      const result = await knowledgeApi.requestResult(
        `/knowledge/api/detail?kb_id=${encodeURIComponent(targetFileId)}`,
        { cache: "no-store", detectLoginRedirect: false, signal },
      );
      if (!result.response.ok) throw new Error(`HTTP ${result.response.status}`);
      const data = knowledgeApi.requireJson(result);
      return { ...data, content: String(data.text || "") };
    },
    onPresent: data => {
      state.detailPath = String(data.path || linkedPath || "");
      state.detailRawUrl = String(data.raw_url || "");
      state.detailDownloadUrl = String(data.download_url || data.raw_url || "");
      state.detailMediaType = String(data.media_type || data.content_type || "");
      state.detailLanguage = String(data.language || "");
      const current = detailFiles().find(value => String(value.address || "") === targetFileId);
      state.detailSelectedFileKind = String(data.kind || current?.kind || "markdown");
      const item = state.selected || {};
      if (!linkedFile && targetFileId === state.detailFileId) {
        state.selected = {
          ...item,
          kb_id: String(data.kb_id || item.kb_id || targetFileId),
          title: String(data.title || item.title || ""),
          project: String(data.project || item.project || ""),
          type: String(data.type || item.type || ""),
          filename: String(data.filename || item.filename || ""),
          created_at: String(data.created_at || item.created_at || ""),
          mtime: Number(data.mtime || item.mtime || 0),
          version: data.version && typeof data.version === "object"
            ? data.version
            : (item.version || {}),
          files: Array.isArray(data.files) ? data.files : (item.files || []),
        };
        renderKnowledgeResourceIdentity(state.selected, targetFileId);
      }
      const displayItem = state.selected || item;
      const displayFile = detailFiles().find(value => String(value.address || "") === targetFileId);
      el.mSub.textContent = `${displayItem.project || ""} · ${displayItem.type || ""} · ${displayFile?.relative_path || data.filename || ""}`;
      renderDetailFileTree();
      renderDetailBody();
    },
    onError: e => {
      el.mText.textContent = window.PortalUI.errorMessage(window.PortalI18n.t("加载"), e);
    },
  });
}

async function openDetail(fileId, options = {}) {
  if (
    !options.skipDiscard
    && detailDocumentSession
    && !await detailDocumentSession.guardDiscard()
  ) return false;
  const graphItem = (state.detailGraph?.payload?.nodes || []).find(node => String(node?.kb_id || "") === String(fileId)) || null;
  const item = byId(fileId) || graphItem || { kb_id: fileId };
  if ((item.resource_type || item.type) === "search") {
    window.location.assign(`/r/${encodeURIComponent(fileId)}`);
    return true;
  }
  if (
    options.updateHistory !== false
    && !detailResourceGuest?.embedded()
  ) detailHistory.open(fileId);
  state.selected = item;
  state.detailFileId = fileId;
  state.detailSelectedFileId = fileId;
  state.detailSelectedFileKind = "markdown";
  state.detailSelectedFileLinked = false;
  state.detailReadOnly = options.readonly === true;
  if (state.detailReadOnly) {
    el.modal.dataset.portalLiveTourReadonly = "true";
  } else {
    delete el.modal.dataset.portalLiveTourReadonly;
  }
  state.detailLinkedFiles = new Map();
  state.detailLinkedFileProbes = new Set();
  state.detailLinkDiscoverySequence += 1;
  state.detailExpandedFolders = new Set([""]);
  state.detailPath = "";
  state.detailRawUrl = "";
  state.detailDownloadUrl = "";
  state.detailMediaType = "";
  state.detailLanguage = "";
  detailDocumentSession?.show?.({ status: "empty", payload: null }, { reason: "detail-change" });
  state.detailBodyDraft = "";
  state.detailMetaDraft = null;
  state.detailGraph = { fileId, payload: null, loading: detailGraphEnabled(), positions: {}, basePositions: null };
  resetDetailChat();
  state.detailChat.fileId = fileId;
  renderKnowledgeResourceIdentity(item, fileId);
  el.mSub.textContent = `${item?.project || ""} · ${item?.type || ""} · ${item?.created_at || ""} · importance ${item?.importance ?? 0}`;
  renderDetailFileTree();
  renderDetailGraph();
  syncDetailButtons();
  el.mText.className = "markdownBody";
  el.mText.textContent = window.PortalI18n.t("加载中…");
  openModal();
  if (detailGraphEnabled()) loadDetailGraph(fileId).catch(alertError);
  await openDetailFile(
    detailFiles().find(value => String(value.address || "") === String(fileId)) || { address: fileId, kind: "markdown" },
    { skipDiscard: true },
  );
  if (state.detailFileId === fileId) {
    loadDetailChat(fileId, { silent: true }).catch(alertError);
  }
  return true;
}

async function applyDetailHistoryState() {
  if (detailResourceGuest?.embedded()) return;
  const targetItemId = initialKnowledgeItemId();
  if (targetItemId === state.detailFileId) {
    if (!targetItemId) detailHistory.restoreReturnPosition();
    return;
  }
  if (
    detailDocumentSession
    && detailIsDirty()
    && !await detailDocumentSession.guardDiscard()
  ) {
    detailHistory.forward();
    return;
  }
  if (!targetItemId) {
    const closed = detailModalController.close({
      force: true,
      source: "popstate",
      updateLocation: false,
    });
    if (closed !== false) detailHistory.restoreReturnPosition();
    return;
  }
  await openDetail(targetItemId, {
    skipDiscard: true,
    updateHistory: false,
  });
}
