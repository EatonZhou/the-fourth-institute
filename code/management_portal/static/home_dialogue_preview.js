(() => {
  "use strict";
  function create({ root, onOpen, onSelection, onChange }) {
    const node = (tag, text) => { const el = document.createElement(tag); if (text) el.textContent = text; return el; };
    const toolbar = node("div"), status = node("p"), body = node("div"), editor = node("textarea");
    toolbar.className = "homeDialogueToolbar"; body.className = "homeDialogueDocument";
    editor.className = "homeDialogueEditor"; editor.setAttribute("aria-label", "文件草稿"); editor.hidden = true;
    status.setAttribute("role", "status"); root.append(toolbar, status, body, editor);
    const button = (title, fn) => { const el = node("button", title); el.type = "button"; el.onclick = fn; toolbar.append(el); return el; };
    let destroyed = false, address = "";
    const source = window.PortalAgentPreviewSource.create({
      apiGet: window.PortalAPI.get, apiPost: window.PortalAPI.post,
      linkFileUrl: path => `/api/agent-workbench/link-file?path=${encodeURIComponent(path)}`,
    });
    const edit = button("编辑", () => session.beginEdit());
    const save = button("保存", () => session.save({ save: ({ payload, draft }) => source.save(payload, draft) }));
    const cancel = button("返回预览", () => session.cancelEdit());
    const link = node("a", "打开原文件 ↗"); link.target = "_blank"; link.rel = "noopener"; link.hidden = true; toolbar.append(link);
    const session = window.PortalDocumentSession.create({
      canEdit: source.canEdit,
      onStateChange(state) {
        const editable = !!state.payload && source.canEdit(state.payload);
        edit.hidden = !editable || state.editing;
        save.hidden = cancel.hidden = !state.editing;
        save.disabled = !editable || !state.dirty || state.saving; cancel.disabled = state.saving;
        editor.hidden = !state.editing; body.hidden = state.editing;
        editor.disabled = state.saving;
        if (state.editing && editor.value !== state.draft) editor.value = state.draft;
        status.textContent = state.error || state.readError || (state.status === "loading" ? "正在读取…" : state.saving ? "正在保存…" : state.dirty ? "有未保存草稿（切换展示会保留）" : "");
        status.hidden = !status.textContent; onChange?.(state);
      },
      onPresent(payload) {
        if (!payload || destroyed) return;
        link.href = source.rawUrl(payload) || "#"; link.hidden = !source.rawUrl(payload);
        const handle = window.PortalPreview.render({ root: body, document: payload,
          onPreviewRequest: request => onOpen({ view: "preview", target: { address: request.resourceAddress || request.path || request.href } }),
        });
        handle.promise.catch(error => { if (!destroyed) session.setReadError(error); });
      },
    });
    editor.addEventListener("input", () => session.updateDraft(editor.value));
    const selection = () => {
      const selected = window.getSelection();
      const text = editor.hidden ? (selected?.anchorNode && body.contains(selected.anchorNode) ? String(selected) : "") : editor.value.slice(editor.selectionStart, editor.selectionEnd);
      if (text.trim()) onSelection?.({ address, label: session.snapshot().payload?.title || address, text: text.slice(0, 20000) });
    };
    root.addEventListener("mouseup", selection); root.addEventListener("keyup", selection);
    async function show(target, { signal } = {}) {
      address = String(target.address || "");
      return session.open({ key: address, load: async () => {
        if (signal?.aborted || destroyed) throw new DOMException("已取消", "AbortError");
        const resource = window.PortalResourceChips.resourceAddressFromPreviewRequest({ resourceAddress: address, href: address });
        if (resource) {
          const payload = await window.PortalResourceChips.loadPreview(resource, { allowCached: false, signal });
          if (!payload) throw new Error("该资源不支持内嵌预览，请从原资源页面打开");
          return source.normalize(payload, { source: "resource", resource_address: resource, path: resource });
        }
        const local = window.PortalFileLinks.describe(address, { allowAnyLocalPath: true });
        if (local?.path) {
          const payload = await window.PortalAPI.get(`/api/agent-workbench/link-file?path=${encodeURIComponent(local.path)}`, { signal });
          return source.normalize(payload, { source: "workspace_link", path: local.path });
        }
        const url = new URL(address, window.location.href);
        if (!["http:", "https:"].includes(url.protocol)) throw new Error("不支持的预览地址");
        link.href = url.href; link.hidden = false; link.textContent = "在原页面打开 ↗";
        throw new Error("此链接无法内嵌预览，请在原页面打开");
      } });
    }
    return { show, snapshot: session.snapshot, canClose: () => session.guardDiscard(),
      setPresented(value) { if (!value) body.querySelectorAll("video,audio").forEach(media => media.pause()); },
      destroy() { destroyed = true; session.destroy(); window.PortalPreview.dispose(body); root.replaceChildren(); },
    };
  }
  window.PortalHomeDialoguePreview = Object.freeze({ create });
})();
