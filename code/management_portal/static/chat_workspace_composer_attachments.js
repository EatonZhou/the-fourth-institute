(() => {
  if (window.PortalChatWorkspaceComposerAttachments) return;

  // Chat attachments are uploads (CreateUpload + its data channel); the
  // message then refers to the upload ID (decision_ad3e21094f14).
  async function defaultWorkspaceUpload(file, options = {}) {
    const kind = String(file?.type || "").toLowerCase().startsWith("image/") ? "image" : "file";
    // The receiving Resident, or the Chatroom a message is posted to.
    const target = options.upload_target || options.resident_id || "";
    const uploaded = await window.PortalInstructionClient.upload(file, {
      purpose: "conversation_attachment",
      target,
      fileName: file.name || (kind === "image" ? "pasted-image.png" : "attachment"),
      mediaType: file.type || (kind === "image" ? "image/png" : "application/octet-stream"),
      onProgress: progress => options.workspace?.updateAttachment?.(options.item?.id, { progress }),
    });
    options.workspace?.updateAttachment?.(options.item?.id, { progress: 100 });
    return {
      upload_id: uploaded.upload_id,
      filename: uploaded.file_name,
      content_type: uploaded.media_type,
      size: uploaded.size,
      kind,
    };
  }

  function defaultWorkspaceDeleteAttachment(item = {}) {
    const uploadId = String(item.upload_id || "").trim();
    if (!uploadId) return Promise.resolve({ deleted: false });
    return window.PortalInstructionClient.execute("DeleteUpload", { upload_id: uploadId }, {
      idempotencyKey: `upload-delete:${uploadId}`,
    });
  }

  // One Workspace's composer attachments: the files and passages the message
  // being written carries. Adding one puts its token in the input and its chip
  // in the tray, and a file uploads; a chip retries a failed upload or removes
  // its attachment, deleting a token from the input removes it too, and an
  // edited image replaces its attachment once the edit is uploaded. An upload
  // that lands after its attachment is gone is deleted again. The preview URLs
  // made from files are the Workspace's own: they are released when their
  // attachment goes, and destroyComposerAttachments releases the rest.
  const DEPENDENCIES = Object.freeze([
    "composerAttachmentTokenOrder", "composerSelectionSnapshot",
    "createConversationSelectionReference", "currentController", "els", "historyScope",
    "insertComposerAttachmentRef", "markComposerUndoAfterSnapshot", "mediaPartPath", "normalizeComposerAttachment",
    "normalizeComposerAttachments", "options", "pushComposerUndoSnapshot", "removeComposerAttachmentRef",
    "renderComposerAttachmentTray", "requireImageEditor", "restoreComposerSelection", "saveActiveDraft", "state",
    "syncComposerAttachmentRefs", "updateControls",
  ]);

  function createComposerAttachments(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalChatWorkspaceComposerAttachments is missing: ${missing.join(", ")}`);
    const {
      composerAttachmentTokenOrder, composerSelectionSnapshot,
      createConversationSelectionReference, currentController, els, historyScope,
      insertComposerAttachmentRef, markComposerUndoAfterSnapshot, mediaPartPath, normalizeComposerAttachment,
      normalizeComposerAttachments, options, pushComposerUndoSnapshot, removeComposerAttachmentRef,
      renderComposerAttachmentTray, requireImageEditor, restoreComposerSelection, saveActiveDraft, state,
      syncComposerAttachmentRefs, updateControls,
    } = dependencies;

    const reportAttachmentError = error => {
      if (options.onError) options.onError(error);
      else window.PortalUI.toast(error.message || error, "error");
    };
    const ownedAttachmentPreviews = new Set();
    const releaseAttachmentPreview = value => {
      if (!ownedAttachmentPreviews.delete(value)) return;
      URL.revokeObjectURL(value);
    };
    const releaseAttachmentPreviews = items => {
      for (const item of items || []) releaseAttachmentPreview(item?.preview_url);
    };

    // While the Workspace writes tokens into the input itself, the input does
    // not decide which attachments remain.
    let suppressAttachmentReconcile = false;
    const withoutAttachmentReconcile = write => {
      const suppressed = suppressAttachmentReconcile;
      suppressAttachmentReconcile = true;
      try {
        return write();
      } finally {
        suppressAttachmentReconcile = suppressed;
      }
    };

    const notifyAttachmentsChange = () => {
      saveActiveDraft();
      options.onAttachmentsChange?.(currentController().payload(), currentController());
    };
    const attachmentPreviewOptions = () => ({
      renderMentions: options.renderMentions,
      attachmentById: id => state.attachments.find(item => String(item?.id || "") === String(id || "")) || null,
      onImagePreview: context => openComposerAttachmentEditor({ ...context, workspace: currentController() }),
    });
    const renderAttachments = (renderOptions = {}) => {
      const selection = document.activeElement === els.input
        ? composerSelectionSnapshot(els.input)
        : null;
      state.attachments = normalizeComposerAttachments(state.attachments);
      renderComposerAttachmentTray(els.tray, state.attachments, attachmentPreviewOptions());
      syncComposerAttachmentRefs(els.input, state.attachments, attachmentPreviewOptions());
      if (selection) restoreComposerSelection(els.input, selection);
      updateControls();
      if (renderOptions.notify) notifyAttachmentsChange();
    };
    const renderAttachmentTrayOnly = (renderOptions = {}) => {
      state.attachments = normalizeComposerAttachments(state.attachments);
      renderComposerAttachmentTray(els.tray, state.attachments, attachmentPreviewOptions());
      updateControls();
      if (renderOptions.notify) notifyAttachmentsChange();
    };
    const setAttachments = items => {
      state.attachments = normalizeComposerAttachments(items);
      renderAttachments({ notify: true });
    };
    const updateAttachment = (id, patch = {}) => {
      const target = String(id || "");
      if (!target) return;
      state.attachments = normalizeComposerAttachments(state.attachments.map(item => String(item?.id || "") === target ? { ...item, ...patch } : item));
      renderAttachments({ notify: true });
    };

    const attachmentUploadContext = () => {
      const configured = typeof options.attachmentUploadContext === "function"
        ? options.attachmentUploadContext(currentController())
        : options.attachmentUploadContext;
      const contractContext = state.composer.attachment_upload_context;
      return {
        ...(configured && typeof configured === "object" ? configured : {}),
        ...(contractContext && typeof contractContext === "object" ? contractContext : {}),
      };
    };
    const deleteUploadedAttachment = item => {
      if (!String(item?.upload_id || "").trim()) return Promise.resolve({ deleted: false });
      const remove = options.deleteAttachment || defaultWorkspaceDeleteAttachment;
      return Promise.resolve(remove(item, attachmentUploadContext())).catch(error => {
        reportAttachmentError(error);
        return { deleted: false, error };
      });
    };
    const discardComposerAttachments = items => {
      const removed = Array.isArray(items) ? items.filter(Boolean) : [];
      releaseAttachmentPreviews(removed);
      removed.forEach(item => { deleteUploadedAttachment(item); });
    };
    // A chip's remove button. Taking the token out of the input tells the host
    // the input changed.
    const removeComposerAttachment = id => {
      discardComposerAttachments(state.attachments.filter(item => item.id === id));
      state.attachments = state.attachments.filter(item => item.id !== id);
      removeComposerAttachmentRef(els.input, id);
      renderAttachments({ notify: true });
    };
    // The input's tokens decide which attachments remain, in their order.
    const reconcileAttachmentsFromInput = (reconcileOptions = {}) => {
      if (suppressAttachmentReconcile || !els.input || !state.attachments.length) return false;
      const order = composerAttachmentTokenOrder(els.input);
      const present = new Set(order);
      const byId = new Map(state.attachments.map(item => [String(item?.id || ""), item]));
      const next = order.map(id => byId.get(id)).filter(Boolean);
      const currentIds = state.attachments.map(item => String(item?.id || ""));
      const nextIds = next.map(item => String(item?.id || ""));
      if (next.length === state.attachments.length && nextIds.every((id, idx) => id === currentIds[idx])) return false;
      discardComposerAttachments(state.attachments.filter(item => !present.has(String(item?.id || ""))));
      state.attachments = normalizeComposerAttachments(next);
      renderAttachmentTrayOnly({ notify: reconcileOptions.notify });
      return true;
    };

    const uploadComposerAttachment = async attachmentId => {
      const target = state.attachments.find(item => String(item.id || "") === String(attachmentId || ""));
      if (!target?.file) throw new Error("原始附件已不可用，请重新选择文件。");
      state.attachments = normalizeComposerAttachments(state.attachments.map(item => item.id === target.id ? {
        ...item,
        uploading: true,
        progress: 0,
        error: "",
      } : item));
      renderAttachments({ notify: true });
      try {
        const upload = options.uploadAttachment || defaultWorkspaceUpload;
        const result = await upload(target.file, {
          ...attachmentUploadContext(),
          item: target,
          workspace: currentController(),
        });
        if (!state.attachments.some(item => String(item?.id || "") === String(target.id || ""))) {
          await deleteUploadedAttachment({ ...target, ...result, kind: target.kind });
          return;
        }
        if (result?.preview_url && result.preview_url !== target.preview_url) {
          releaseAttachmentPreview(target.preview_url);
        }
        state.attachments = normalizeComposerAttachments(state.attachments.map(item => item.id === target.id ? {
          ...item,
          upload_id: result?.upload_id || "",
          file_id: result?.file_id || "",
          name: result?.filename || item.name,
          path: result?.path || item.path || "",
          preview_url: result?.preview_url || item.preview_url || "",
          data_url: result?.data_url || item.data_url || "",
          mime: result?.mime || result?.content_type || item.mime || item.content_type || "",
          content_type: result?.content_type || item.content_type || "",
          size: Number(result?.size || item.size || 0),
          size_bytes: Number(result?.size_bytes || result?.size || item.size_bytes || item.size || 0),
          uploading: false,
          progress: 100,
          error: "",
          file: null,
        } : item));
      } catch (error) {
        state.attachments = normalizeComposerAttachments(state.attachments.map(item => item.id === target.id ? {
          ...item,
          uploading: false,
          error: error.message || String(error || "上传失败"),
        } : item));
        throw error;
      } finally {
        renderAttachments({ notify: true });
      }
    };
    const fileSignature = (file) => {
      const type = String(file?.type || "").toLowerCase();
      const name = String(file?.name || "").trim();
      const size = Number(file?.size || 0);
      const kind = type.startsWith("image/") ? "image" : "file";
      return kind === "image"
        ? `image:${type}:${size}:${name}`
        : `file:${name}:${type}:${size}:${file?.lastModified || 0}`;
    };
    const addFiles = async (files) => {
      const now = Date.now();
      const seen = new Set();
      const list = Array.from(files || []).filter(file => {
        if (!file) return false;
        const signature = fileSignature(file);
        if (seen.has(signature)) return false;
        seen.add(signature);
        const previous = Number(state.recentFileSignatures.get(signature) || 0);
        if (previous && now - previous < 900) return false;
        state.recentFileSignatures.set(signature, now);
        return true;
      });
      state.recentFileSignatures.forEach((timestamp, signature) => {
        if (now - Number(timestamp || 0) > 3000) state.recentFileSignatures.delete(signature);
      });
      if (!list.length) return;
      const maxFileSize = Number(options.maxFileSize || 512 * 1024 * 1024);
      const tooLarge = list.find(file => Number(file.size || 0) > maxFileSize);
      if (tooLarge) throw new Error(`文件过大：${tooLarge.name || "attachment"}，上限 ${Math.round(maxFileSize / 1024 / 1024)} MB`);
      const undoFrame = pushComposerUndoSnapshot();
      const drafts = list.map(file => {
        const kind = String(file.type || "").toLowerCase().startsWith("image/") ? "image" : "file";
        const previewUrl = kind === "image" ? URL.createObjectURL(file) : "";
        if (previewUrl) ownedAttachmentPreviews.add(previewUrl);
        return {
          id: `uploading:${Date.now()}:${Math.random().toString(16).slice(2)}`,
          kind,
          name: file.name || (kind === "image" ? window.PortalI18n.t("pasted image") : "attachment"),
          content_type: file.type || "",
          size: Number(file.size || 0),
          preview_url: previewUrl,
          path: "",
          uploading: true,
          progress: 0,
          error: "",
          file,
        };
      });
      if (undoFrame) {
        undoFrame.kind = "attachment-insert";
        undoFrame.attachmentIds = drafts.map(item => item.id);
      }
      state.attachments = normalizeComposerAttachments([...state.attachments, ...drafts]);
      withoutAttachmentReconcile(() => {
        drafts.forEach(item => insertComposerAttachmentRef(els.input, item, { forceDom: true }));
      });
      renderAttachments({ notify: true });
      markComposerUndoAfterSnapshot(undoFrame);
      for (const draft of drafts) {
        try {
          await uploadComposerAttachment(draft.id);
        } catch {}
      }
    };
    // A passage of a conversation or a preview, quoted into the message.
    const addReference = (reference) => {
      const item = reference?.content_type === "text/x-portal-preview-reference"
        ? normalizeComposerAttachment(reference)
        : createConversationSelectionReference(reference);
      if (!item) return null;
      const undoFrame = pushComposerUndoSnapshot();
      if (undoFrame) {
        undoFrame.kind = "attachment-insert";
        undoFrame.attachmentIds = [item.id];
      }
      state.attachments = normalizeComposerAttachments([...state.attachments, item]);
      withoutAttachmentReconcile(() => {
        insertComposerAttachmentRef(els.input, item, { forceDom: true });
      });
      renderAttachments({ notify: true });
      markComposerUndoAfterSnapshot(undoFrame);
      return item;
    };

    // An edited composer image replaces its attachment once the edit is
    // uploaded, keeping its place and the image it replaced.
    const attachmentOriginal = item => {
      const raw = item && typeof item === "object" ? item : {};
      return raw.original && typeof raw.original === "object" ? raw.original : {
        upload_id: String(raw.upload_id || ""),
        file_id: String(raw.file_id || ""),
        kind: String(raw.kind || "image"),
        name: String(raw.name || ""),
        path: String(raw.path || ""),
        preview_url: String(raw.preview_url || raw.data_url || ""),
        content_type: String(raw.content_type || raw.mime || ""),
        size: Number(raw.size || raw.size_bytes || 0),
      };
    };
    const replaceEditedAttachment = async (context, file, previewUrl) => {
      const attachmentId = String(context?.attachmentId || "").trim();
      const current = state.attachments.find(item => String(item?.id || "") === attachmentId);
      if (!attachmentId || !current) throw new Error("未找到需要替换的图片附件");
      if (current.uploading) throw new Error("图片还在上传，请稍后编辑。");
      const previous = { ...current };
      const scope = historyScope();
      if (String(previewUrl || "").startsWith("blob:")) ownedAttachmentPreviews.add(previewUrl);
      updateAttachment(attachmentId, {
        upload_id: "",
        file_id: "",
        original: attachmentOriginal(current),
        name: file.name,
        path: "",
        url: "",
        preview_url: previewUrl,
        data_url: "",
        mime: "image/png",
        content_type: "image/png",
        size: file.size,
        size_bytes: file.size,
        uploading: true,
        progress: 0,
        error: "",
      });
      try {
        const upload = options.uploadAttachment || defaultWorkspaceUpload;
        const result = await upload(file, {
          ...attachmentUploadContext(),
          item: current,
          workspace: currentController(),
          editedAttachmentId: attachmentId,
        });
        if (historyScope() !== scope || !state.attachments.some(item => String(item.id || "") === attachmentId)) {
          await deleteUploadedAttachment({ ...result, kind: "image" });
          releaseAttachmentPreview(previewUrl);
          return;
        }
        const uploadedPreview = result?.preview_url || result?.data_url || mediaPartPath({ upload_id: result?.upload_id }) || previewUrl;
        if (uploadedPreview !== previous.preview_url) releaseAttachmentPreview(previous.preview_url);
        if (uploadedPreview !== previewUrl) releaseAttachmentPreview(previewUrl);
        updateAttachment(attachmentId, {
          upload_id: result?.upload_id || "",
          file_id: result?.file_id || "",
          name: result?.filename || file.name,
          path: result?.path || "",
          url: result?.url || "",
          preview_url: uploadedPreview,
          data_url: result?.data_url || "",
          mime: result?.mime || result?.content_type || "image/png",
          content_type: result?.content_type || "image/png",
          size: Number(result?.size || file.size || 0),
          size_bytes: Number(result?.size_bytes || result?.size || file.size || 0),
          uploading: false,
          progress: 100,
          error: "",
          file: null,
        });
      } catch (error) {
        releaseAttachmentPreview(previewUrl);
        if (historyScope() === scope) updateAttachment(attachmentId, previous);
        throw error;
      }
    };
    const openComposerAttachmentEditor = async context => {
      try {
        await requireImageEditor();
        const attachment = context?.attachment || {};
        context.open?.({
          editable: true,
          name: attachment.name || context.title || "annotated-image",
          meta: { attachmentId: context.attachmentId || "" },
          onSave: ({ file, previewUrl }) => replaceEditedAttachment(context, file, previewUrl),
        });
      } catch (error) {
        reportAttachmentError(error);
      }
    };

    const destroyComposerAttachments = () => {
      ownedAttachmentPreviews.forEach(releaseAttachmentPreview);
    };

    return {
      attachmentPreviewOptions, renderAttachments, renderAttachmentTrayOnly, withoutAttachmentReconcile,
      setAttachments, updateAttachment, addFiles, addReference, uploadComposerAttachment, removeComposerAttachment,
      reconcileAttachmentsFromInput, discardComposerAttachments, releaseAttachmentPreviews, reportAttachmentError,
      destroyComposerAttachments,
    };
  }

  window.PortalChatWorkspaceComposerAttachments = Object.freeze({
    createComposerAttachments, defaultWorkspaceUpload, dependencies: DEPENDENCIES,
  });
})();
