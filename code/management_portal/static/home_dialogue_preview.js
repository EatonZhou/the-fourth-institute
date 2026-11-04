(() => {
  "use strict";
  function create({ root, controls, onOpen, onSelection, onChange }) {
    const node = (tag, text) => { const el = document.createElement(tag); if (text) el.textContent = text; return el; };
    const toolbar = controls?.primary || node("div"), secondary = controls?.secondary || node("div"), status = node("p"), progress = node("span"), body = node("div"), editor = node("textarea");
    const renderRoot = node("div"); body.append(renderRoot);
    toolbar.className = "homeDialoguePreviewActions"; secondary.className = "homeDialoguePreviewMenu"; body.className = "homeDialogueDocument";
    editor.className = "homeDialogueEditor"; editor.setAttribute("aria-label", "文件草稿"); editor.hidden = true;
    status.className = "homeDialoguePreviewError"; status.setAttribute("role", "status");
    progress.className = "homeDialogueSaveStatus"; progress.setAttribute("role", "status");
    if (!controls) root.append(toolbar, secondary);
    root.append(status, body, editor);
    const button = (title, fn, parent = toolbar) => { const el = node("button", title); el.type = "button"; el.onclick = fn; parent.append(el); return el; };
    const identityKey = () => {
      const identity = window.PortalIdentity?.peek?.() || {};
      return JSON.stringify([identity.principal?.kind || "user", identity.principal?.id || identity.username || window.PortalIdentity?.username?.() || ""]);
    };
    const ownerIdentity = identityKey();
    let destroyed = false, presented = true, authorized = false, checking = false, address = "";
    let epoch = 0, controller = null, externalSignal = null, verified = null, accessError = "", editError = "", externalUrl = "", rendered = null, session;
    const source = window.PortalAgentPreviewSource.create({
      apiGet: (url, options = {}) => window.PortalAPI.get(url, { ...options, signal: controller?.signal, cache: "no-store" }),
      apiPost: window.PortalAPI.post,
      resourceLoader: (resource, options = {}) => window.PortalResourceChips.loadPreview(resource, { ...options, allowCached: false, readOnly: false, signal: controller?.signal }),
      linkFileUrl: path => `/api/agent-workbench/link-file?path=${encodeURIComponent(path)}`,
    });
    const readable = () => presented && authorized && !destroyed && identityKey() === ownerIdentity;
    const editable = () => readable() && !!verified && source.canEdit(verified);
    const edit = button("编辑", () => session.beginEdit());
    const save = button("保存", () => session.save({ save: ({ payload, draft }) => source.save(payload, draft) }));
    const cancel = button("返回预览", () => { session.cancelEdit(); const menu = secondary.closest("details"); if (menu) { menu.open = false; menu.querySelector("summary").focus(); } }, secondary);
    const retry = button("重试访问", () => { void revalidate(); });
    const link = node("a", "打开原文件 ↗"); link.target = "_blank"; link.rel = "noopener"; link.hidden = true; secondary.append(link); toolbar.append(progress);
    function update(state = session?.snapshot() || {}) {
      if (destroyed) return;
      const allowed = readable(), canEdit = editable();
      edit.hidden = !allowed || !canEdit || state.editing;
      save.hidden = cancel.hidden = !allowed || !state.editing;
      save.disabled = !canEdit || !state.dirty || state.saving; cancel.disabled = state.saving;
      editor.hidden = !allowed || !state.editing; body.hidden = !allowed || state.editing;
      editor.disabled = !canEdit || state.saving;
      if (allowed && state.editing && editor.value !== state.draft) editor.value = state.draft;
      link.hidden = !allowed || !source.rawUrl(state.payload || {});
      if (externalUrl && presented && identityKey() === ownerIdentity) { link.href = externalUrl; link.textContent = "在原页面打开 ↗"; link.hidden = false; }
      retry.hidden = !presented || (!accessError && !editError) || checking;
      status.textContent = accessError || state.error || state.readError || editError || "";
      progress.textContent = checking ? "核验中" : state.status === "loading" ? "读取中" : state.saving ? "保存中" : state.dirty ? "未保存" : state.editing ? "已保存" : allowed && !canEdit ? "只读" : "";
      progress.title = checking ? "正在核对当前访问权限" : state.dirty ? "有未保存草稿，切换展示会保留" : progress.textContent;
      progress.hidden = !progress.textContent;
      status.hidden = !status.textContent; onChange?.(state);
    }
    function disposeView() {
      window.PortalPreview.dispose(renderRoot); renderRoot.replaceChildren(); rendered = null; editor.value = "";
    }
    function render(payload) {
      if (!payload || !readable() || rendered === payload) return;
      rendered = payload; link.href = source.rawUrl(payload) || "#";
      const handle = window.PortalPreview.render({ root: renderRoot, document: payload,
        onPreviewRequest: request => { if (readable()) onOpen({ view: "preview", target: { address: request.resourceAddress || request.path || request.href } }); },
      });
      handle.promise.catch(error => { if (readable() && rendered === payload) session.setReadError(error); });
    }
    session = window.PortalDocumentSession.create({ canEdit: editable, onStateChange: update, onPresent: render });
    editor.addEventListener("input", () => { if (editable()) session.updateDraft(editor.value); });
    const selection = () => {
      if (!readable()) return;
      const selected = window.getSelection();
      const text = editor.hidden ? (selected?.anchorNode && body.contains(selected.anchorNode) ? String(selected) : "") : editor.value.slice(editor.selectionStart, editor.selectionEnd);
      if (text.trim()) onSelection?.({ address, label: session.snapshot().payload?.title || address, text: text.slice(0, 20000) });
    };
    root.addEventListener("mouseup", selection); root.addEventListener("keyup", selection);
    function invalidate(clear = false) {
      epoch++; controller?.abort(); authorized = false; checking = false;
      body.querySelectorAll("video,audio").forEach(media => media.pause());
      onSelection?.(null);
      if (clear) disposeView();
      update();
    }
    async function readInitial() {
      const signal = controller?.signal;
      const resource = window.PortalResourceChips.resourceAddressFromPreviewRequest({ resourceAddress: address, href: address });
      if (resource) {
        const payload = await source.reload({ source: "resource", resource_address: resource });
        if (signal?.aborted) throw signal.reason;
        if (!payload || !/^workspace_[a-f0-9]{12}\/file\//.test(resource)) return payload;
        // Resource content capabilities describe UpdateResource, not the file
        // mutation. Resolve the registered Workspace file and check the exact
        // instruction used by the existing DocumentSession source adapter.
        const resolved = await window.PortalInstructionClient.execute("ResolveResource", { address: resource }, { signal });
        const target = resolved?.target, reference = target?.file_reference;
        if (target?.target_kind !== "file" || target.exists !== true || !/^workspace_[a-f0-9]{12}$/.test(reference?.workspace_id || "")) return payload;
        const prefix = `${reference.workspace_id}/file/`;
        if (!target.address?.startsWith(prefix) || !reference.relative_path) return payload;
        let allowed = false;
        try {
          const identity = window.PortalIdentity?.peek?.() || {};
          const principal = identity.principal?.id || identity.username || window.PortalIdentity?.username?.();
          if (!principal) throw new Error("当前身份尚未就绪");
          const permission = await window.PortalInstructionClient.execute("CheckPermission", {
            principal_id: principal, a: "UpdateWorkspaceFile", resource_id: reference.workspace_id,
          }, { signal });
          allowed = permission?.allowed === true;
        } catch (error) {
          if (signal?.aborted) throw error;
          editError = `文件可读，但无法核对编辑权限：${error.message || error}`;
        }
        // Keep the authenticated resource URLs for reads. The canonical suffix
        // preserves server address escaping; no filesystem path is inferred.
        return source.normalize({ ...payload, source: "lineage-workspace", previewSource: "lineage-workspace",
          workspace_id: reference.workspace_id, rel_path: target.address.slice(prefix.length),
          allows: { UpdateWorkspaceFile: allowed },
        });
      }
      const local = window.PortalFileLinks.describe(address, { allowAnyLocalPath: true });
      if (local?.path) return source.reload({ source: "workspace_link", path: local.path });
      const url = new URL(address, window.location.href);
      if (["http:", "https:"].includes(url.protocol)) externalUrl = url.href;
      throw new Error("此链接不支持内嵌预览，请从原页面打开");
    }
    async function revalidate() {
      invalidate();
      if (destroyed || !presented || !address || externalSignal?.aborted) return;
      const current = epoch;
      if (identityKey() !== ownerIdentity) {
        accessError = "当前身份已变化；原草稿已保留，返回原身份后可重试。";
        disposeView(); update(); return { error: new Error(accessError) };
      }
      controller = new AbortController(); checking = true; accessError = ""; editError = ""; update();
      const valid = () => !destroyed && presented && current === epoch && !externalSignal?.aborted && identityKey() === ownerIdentity;
      try {
        // Re-read through the existing authenticated source, never a cached preview.
        // Retain the editing base/version and draft; reauthorization is not a rebase.
        const cached = session.snapshot().payload;
        const fresh = await readInitial();
        if (!valid()) return;
        if (!fresh) throw new Error("当前资源不可预览或已不可读");
        verified = fresh; authorized = true; checking = false;
        if (!cached) await session.open({ key: address, load: async () => fresh });
        if (!valid()) return;
        render(session.snapshot().payload); update();
        return { payload: session.snapshot().payload };
      } catch (error) {
        if (!valid()) return;
        authorized = false; checking = false; accessError = error.message || String(error);
        disposeView(); update(); return { error };
      }
    }
    const abort = () => { invalidate(true); };
    async function show(target, { signal } = {}) {
      if (destroyed) return;
      const next = String(target.address || "");
      if (address && next !== address) throw new Error("每个预览历史条目只绑定一个来源");
      address = next; externalSignal?.removeEventListener("abort", abort); externalSignal = signal;
      externalSignal?.addEventListener("abort", abort, { once: true });
      return revalidate();
    }
    const accessChanged = () => { invalidate(true); if (presented) void revalidate(); };
    const focus = () => { if (presented && !document.hidden) void revalidate(); };
    const visibility = () => { if (document.hidden) invalidate(); else focus(); };
    const events = ["portal:identity-changed", "portal:permissions-changed", "portal:history-restored", "portal:history-restored-error"];
    for (const event of events) window.addEventListener(event, accessChanged);
    window.addEventListener("focus", focus); document.addEventListener("visibilitychange", visibility);
    return { show, snapshot: session.snapshot, canClose: () => session.guardDiscard(),
      setPresented(value) {
        const next = !!value;
        if (next === presented || destroyed) return;
        presented = next;
        if (!presented) { invalidate(); return; }
        return revalidate();
      },
      destroy() {
        destroyed = true; epoch++; controller?.abort(); externalSignal?.removeEventListener("abort", abort);
        for (const event of events) window.removeEventListener(event, accessChanged);
        window.removeEventListener("focus", focus); document.removeEventListener("visibilitychange", visibility);
        root.removeEventListener("mouseup", selection); root.removeEventListener("keyup", selection);
        session.destroy(); disposeView(); toolbar.remove(); secondary.remove(); root.replaceChildren();
      },
    };
  }
  window.PortalHomeDialoguePreview = Object.freeze({ create });
})();
