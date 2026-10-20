(() => {
  "use strict";
  const t = text => window.PortalI18n.t(text);
  const esc = value => window.PortalUIPrimitives.escapeHtml(value);
  let instance = null;
  let instanceSequence = 0;

  function create({execute = (...args) => window.PortalInstructionClient.execute(...args)} = {}) {
    const key = `workspace-menu-${++instanceSequence}`;
    const host = document.createElement("div");
    host.innerHTML = `<div class="portalDetailOverlay" data-portal-detail-overlay hidden aria-hidden="true">
      <section class="portalDetailSurface nodeSettingsSurface workspaceMenuSurface" data-portal-detail-surface role="dialog" aria-modal="true" aria-labelledby="${key}-title">
        <div class="portalDetailHeader residentMenuHeader" data-portal-detail-header>
          <div class="residentMenuHeading"><h2 id="${key}-title">Menu</h2><div class="residentMenuHeaderActions"><button class="portalDetailClose" data-portal-detail-close type="button" aria-label="${esc(t("关闭"))}">${window.PortalUIPrimitives.iconHtml("close")}</button></div></div>
          <div class="residentMenuIdentity"><div class="residentMenuPortrait workspaceMenuPortrait">${window.PortalUIPrimitives.iconHtml("folder")}</div><div class="residentMenuIdentityCopy"><div class="residentMenuName"><strong data-workspace-menu-title>Workspace</strong></div><div class="residentMenuMeta"><code data-workspace-menu-id></code></div></div></div>
        </div>
        <div class="portalDetailBody nodeSettingsBody" data-portal-detail-body>
          <div class="nodeSettingsPanels"><section class="workspaceMenuOverview" aria-label="${esc(t("Workspace 概览"))}">
            <form class="nodeSettingsForm" data-workspace-menu-form>
              <label class="portalField"><span>${esc(t("Workspace 名称"))}</span><input data-workspace-menu-name autocomplete="off" disabled /></label>
              <label class="portalField"><span>${esc(t("描述"))}</span><textarea data-workspace-menu-summary rows="3" maxlength="2000" placeholder="${esc(t("这个 Workspace 用来做什么"))}" disabled></textarea></label>
              <div class="workspaceMenuReadout"><span>${esc(t("Workspace ID"))}</span><div class="workspaceMenuReadoutValue"><code data-workspace-menu-identity></code><button class="portalButton" data-workspace-menu-copy-id type="button">${esc(t("复制 ID"))}</button></div></div>
              <div class="workspaceMenuReadout"><span>${esc(t("目录"))}</span><div class="workspaceMenuReadoutValue"><code data-workspace-menu-path></code><button class="portalButton" data-workspace-menu-copy-path type="button">${esc(t("复制路径"))}</button></div></div>
              <div class="workspaceMenuReadout"><span>${esc(t("目录状态"))}</span><strong data-workspace-menu-directory-status>—</strong></div>
              <button class="portalButton" data-workspace-menu-open type="button">${esc(t("打开 Workspace"))}</button>
              <section class="workspaceMenuMembers" data-workspace-menu-members></section>
              <p class="workspaceMenuStatus" data-workspace-menu-status role="status" aria-live="polite"></p>
              <button class="portalButton" data-workspace-menu-reload type="button" hidden>${esc(t("重新读取"))}</button>
              <div class="portalDetailFooter nodeSettingsFooter" data-portal-detail-footer><button class="portalButton" data-workspace-menu-cancel type="button">${esc(t("取消"))}</button><button class="portalButton is-primary" data-workspace-menu-save type="submit" disabled>${esc(t("保存更改"))}</button></div>
            </form>
          </section></div>
        </div>
      </section>
    </div>`;
    const modal = host.firstElementChild;
    document.body.append(modal);
    const find = name => modal.querySelector(`[data-workspace-menu-${name}]`);
    const input = find("name"), summary = find("summary"), save = find("save"), status = find("status"), reload = find("reload");
    const close = modal.querySelector("[data-portal-detail-close]");
    const surface = window.PortalDetailSurface.mount(modal.querySelector("[data-portal-detail-surface]"));
    let target = null, record = null, sequence = 0, busy = false, loading = false, writable = false, destroyed = false;
    let needsRead = false;
    const dirty = () => !!record && (input.value !== record.title || summary.value !== record.summary);
    const dialog = window.PortalUI.createDialogController({
      modal, openClass: "portalDialogOpen", backdropClose: true, escapeClose: true,
      initialFocus: () => close, beforeClose: () => !busy, dirty,
      onClose: () => { sequence += 1; },
    });
    function showStatus(message, error = false) {
      status.textContent = message;
      status.classList.toggle("is-error", error);
    }
    function controls() {
      input.disabled = busy || loading || !writable;
      summary.disabled = busy || loading || !writable;
      save.disabled = busy || loading || !writable || needsRead || !record?.version || !dirty();
      reload.disabled = busy || loading;
      find("open").disabled = busy || loading || !record || typeof target?.onOpen !== "function" || find("directory-status").dataset.state !== "available";
      find("copy-id").disabled = busy || loading || !record;
      find("copy-path").disabled = busy || loading || !record?.attributes?.path;
      close.disabled = busy;
      find("cancel").disabled = busy;
    }
    function acceptResource(resource) {
      if (resource?.resource_id !== target.workspaceId || !resource.version) throw new Error(t("Workspace 返回的身份或版本无效，请重新读取。"));
      record = {...resource, title: String(resource.title || ""), summary: String(resource.summary || "")};
      find("title").textContent = record.title || "Workspace";
      find("path").textContent = record.attributes?.path || "—";
      find("identity").textContent = record.resource_id;
    }
    function setDirectoryStatus(state) {
      const labels = {available: t("目录可用"), missing: t("目录不存在"), not_directory: t("路径不是目录"), unavailable: t("无法检查目录")};
      find("directory-status").dataset.state = state || "unknown";
      find("directory-status").textContent = labels[state] || t("状态未知");
    }
    async function read({preserveDraft = false} = {}) {
      const epoch = ++sequence, id = target.workspaceId, draft = input.value, summaryDraft = summary.value, previousVersion = record?.version;
      loading = true; writable = false; needsRead = true;
      reload.hidden = true; showStatus(t("正在读取 Workspace…")); controls();
      try {
        const [resolved, type] = await Promise.all([
          execute("ResolveResource", {address: id}), execute("GetResourceType", {resource_type: "workspace"}),
        ]);
        if (destroyed || epoch !== sequence) return;
        if (resolved.target?.exists === false || resolved.resource?.attributes?.status === "deleted") throw new Error(t("Workspace 已不存在。"));
        acceptResource(resolved.resource);
        setDirectoryStatus(resolved.target?.directory_status);
        writable = type.updatable === true && resolved.operations?.includes("update") === true;
        needsRead = false;
        input.value = preserveDraft ? draft : record.title;
        summary.value = preserveDraft ? summaryDraft : record.summary;
        showStatus(preserveDraft ? t("已读取最新信息，草稿已保留；请确认后再保存。")
          : writable ? "" : t("当前 Workspace 仅可查看。"));
        if (preserveDraft && previousVersion !== record.version) {
          try { await target.onChanged?.({workspaceId: id, resource: record}); }
          catch (error) {
            if (epoch === sequence) showStatus(t("已读取最新信息，画布刷新失败：") + (error.message || error), true);
          }
        }
      } catch (error) {
        if (destroyed || epoch !== sequence) return;
        showStatus(error.message || String(error), true); reload.hidden = false;
      } finally {
        if (!destroyed && epoch === sequence) { loading = false; controls(); }
      }
    }
    async function submit(event) {
      event.preventDefault();
      if (busy || loading || !writable || needsRead || !record?.version) return;
      const title = input.value.trim();
      if (!title) { showStatus(t("名称不能为空。"), true); input.focus(); return; }
      if (summary.value.length > 2000) { showStatus(t("描述不能超过 2000 字。"), true); summary.focus(); return; }
      busy = true; controls(); showStatus(t("正在保存…"));
      const epoch = sequence;
      try {
        const result = await execute("UpdateResource", {resource_id: target.workspaceId, changes: {title, summary: summary.value.trim(), expected_version: record.version}});
        if (destroyed || epoch !== sequence) return;
        acceptResource(result.resource);
        input.value = record.title;
        summary.value = record.summary;
        showStatus(t("已保存。"));
        try { await target.onChanged?.({workspaceId: target.workspaceId, resource: record}); }
        catch (error) { showStatus(t("更改已保存，画布刷新失败：") + (error.message || error), true); }
      } catch (error) {
        if (destroyed || epoch !== sequence) return;
        needsRead = true;
        reload.hidden = false;
        showStatus(error.code === "instruction.version_conflict"
          ? t("Workspace 已被更新。请重新读取最新信息，草稿将保留。")
          : (error.message || String(error)) + t("。请重新读取确认后再保存。"), true);
      } finally {
        if (!destroyed && epoch === sequence) { busy = false; controls(); }
      }
    }
    find("form").addEventListener("submit", submit);
    input.addEventListener("input", controls);
    summary.addEventListener("input", controls);
    reload.addEventListener("click", () => void read({preserveDraft: !!record}));
    async function copyValue(value) {
      try {
        if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(value);
        else {
          const buffer = document.createElement("textarea");
          buffer.value = value;
          buffer.style.position = "fixed";
          buffer.style.left = "-9999px";
          document.body.append(buffer);
          try { buffer.select(); if (!document.execCommand("copy")) throw new Error(t("浏览器拒绝复制")); }
          finally { buffer.remove(); }
        }
        showStatus(t("已复制。"));
      }
      catch (error) { showStatus(t("复制失败：") + (error.message || error), true); }
    }
    find("copy-id").addEventListener("click", () => void copyValue(target?.workspaceId || ""));
    find("copy-path").addEventListener("click", () => void copyValue(record?.attributes?.path || ""));
    find("open").addEventListener("click", async () => {
      if (!record || find("open").disabled) return;
      if (dirty()) { showStatus(t("请先保存或取消更改，再打开 Workspace。"), true); return; }
      try { await target.onOpen(target.workspaceId); dialog.close(); }
      catch (error) { showStatus(t("打开 Workspace 失败：") + (error.message || error), true); }
    });
    close.addEventListener("click", () => dialog.close());
    find("cancel").addEventListener("click", () => { if (!busy) dialog.close(); });

    async function open(options) {
      if (destroyed) throw new Error("Workspace Menu was destroyed");
      if (busy) return false;
      if (!/^workspace_[0-9a-f]{12}$/.test(options?.workspaceId || "")) throw new TypeError("Invalid Workspace ID");
      if (dialog.isOpen() && dirty()) return false;
      target = {...options}; record = null; writable = false; input.value = ""; summary.value = "";
      find("title").textContent = "Workspace";
      find("id").textContent = target.workspaceId;
      find("identity").textContent = target.workspaceId;
      find("path").textContent = "—";
      setDirectoryStatus("");
      const members = options.members || [];
      find("members").innerHTML = `<h3>${esc(t("当前图中的使用者"))}</h3><p>${esc(t("仅显示当前图中的 Workspace 绑定；这里不是完整的使用者或权限列表。"))}</p>${members.length ? `<ul>${members.map(member => `<li><span>${esc(member.title || member.id)}</span><code>${esc(member.id)}</code></li>`).join("")}</ul>` : `<p>${esc(t("当前图中没有使用者。"))}</p>`}`;
      dialog.open();
      await read();
      return true;
    }
    return Object.freeze({open, destroy() {
      destroyed = true; sequence += 1;
      dialog.destroy(); surface.destroy(); modal.remove();
    }});
  }
  window.PortalWorkspaceMenu = Object.freeze({create, open: options => {
    instance ||= create();
    return instance.open(options);
  }});
})();
