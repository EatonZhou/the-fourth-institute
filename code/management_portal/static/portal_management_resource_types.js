const t = value => window.PortalI18n.t(value);
const tx = (...args) => window.PortalI18n.text(...args);
const esc = value => window.PortalUIPrimitives.escapeHtml(String(value ?? ""));
const stateTitle = value => t({enabled: "已启用", disabled: "已停用", not_installed: "未安装"}[value] || value);
const categoryTitle = value => t({resource: "独立资源", knowledge_entry: "知识库条目"}[value] || value);
const title = row => row.title?.[window.PortalI18n.locale() === "en" ? "en" : "zh"] || row.title?.zh || row.type;

export async function mount(root, {me} = {}) {
  me ||= await window.PortalIdentity.load();
  let canWrite = me.access?.permission_system === "open" || me.access?.allows?.UpdateResourceType === true;
  let rows = [], selected = "", busy = false, destroyed = false, detailRequest = 0;
  const execute = (name, args, options) => window.PortalInstructionClient.execute(name, args, options);
  root.innerHTML = window.PortalI18n.html`
    <section class="settingsSection managementResourceTypes">
      <header><h2>资源类型</h2><button class="portalButton" type="button" data-types-refresh>刷新</button></header>
      <div class="accessFieldGrid">
        <label class="portalField"><span>分类</span><select data-types-category><option value="">全部分类</option><option value="resource">独立资源</option><option value="knowledge_entry">知识库条目</option></select></label>
        <label class="portalField"><span>状态</span><select data-types-state><option value="">全部状态</option><option value="enabled">已启用</option><option value="disabled">已停用</option><option value="not_installed">未安装</option></select></label>
      </div>
      <p data-types-status role="status"></p>
      <div class="managementResourceTypeTable"><table><thead><tr>
        <th>类型</th><th>分类</th><th>标题</th><th>状态</th><th>实例数</th><th>提供者与发行单元</th><th>目录位置</th><th>可登记</th><th>操作</th>
      </tr></thead><tbody data-types-rows></tbody></table></div>
      <section data-types-detail hidden><h3 data-types-detail-title></h3><h4>类型声明</h4><pre data-types-declaration></pre><h4>动作目录</h4><pre data-types-actions></pre></section>
    </section>
    <details class="settingsSection" data-tags>
      <summary>知识标签</summary>
      <form class="backendForm" data-tags-form>
        <p>按 JSON 数组编辑完整的标签列表。</p>
        <label class="portalField"><span>标签列表</span><textarea name="tags" rows="12" readonly spellcheck="false"></textarea></label>
        <div class="backendActions">
          <button class="portalButton" type="button" data-tags-reload>重新读取</button>
          <button class="portalButton is-primary" type="submit" disabled>保存</button>
        </div>
        <p role="status" data-tags-status></p>
      </form>
    </details>`;
  const $ = selector => root.querySelector(selector);
  const status = message => { if (!destroyed) $("[data-types-status]").textContent = message; };
  function render() {
    if (destroyed) return;
    const category = $("[data-types-category]").value, state = $("[data-types-state]").value;
    const visible = rows.filter(row => (!category || row.category === category) && (!state || row.state === state));
    $("[data-types-rows]").innerHTML = visible.map(row => `<tr data-resource-type="${esc(row.type)}">
      <td><button class="portalButton is-compact" type="button" data-type-details="${esc(row.type)}" aria-expanded="${selected === row.type}">${esc(row.type)}</button></td>
      <td>${esc(categoryTitle(row.category))}</td><td>${esc(title(row))}</td><td>${esc(stateTitle(row.state))}</td><td>${esc(row.instance_count ?? "—")}</td>
      <td><code>${esc(row.provider || "—")}</code><br>${esc(row.unit)}</td><td>${esc(row.catalog_section || "—")} ${row.catalog_section ? esc(row.catalog_order) : ""}</td><td>${esc(t(row.registerable ? "是" : "否"))}</td>
      <td>${canWrite && row.state !== "not_installed" && row.installed !== false ? `<button class="portalButton is-compact" type="button" data-type-toggle="${esc(row.type)}" ${busy ? "disabled" : ""}>${esc(t(row.enabled ? "停用" : "启用"))}</button>` : ""}</td>
    </tr>`).join("") || `<tr><td colspan="9">${esc(t("没有符合筛选条件的资源类型。"))}</td></tr>`;
    $("[data-types-refresh]").disabled = busy;
  }
  function showDetail(row) {
    if (destroyed) return;
    $("[data-types-detail]").hidden = false;
    $("[data-types-detail-title]").textContent = `${row.type} · ${title(row)}`;
    $("[data-types-declaration]").textContent = JSON.stringify(row.declaration, null, 2);
    $("[data-types-actions]").textContent = row.actions?.length ? JSON.stringify(row.actions, null, 2) : t("此类型没有专用动作。");
  }
  async function detail(type) {
    selected = type;
    const request = ++detailRequest;
    showDetail(rows.find(row => row.type === type)); render();
    try {
      const value = await execute("GetResourceType", {resource_type: type});
      if (destroyed || request !== detailRequest) return;
      const index = rows.findIndex(row => row.type === type);
      Object.assign(rows[index], value); showDetail(rows[index]); render();
    } catch (error) { if (request === detailRequest) status(error.message); }
  }
  async function refresh() {
    if (busy || destroyed) return false;
    detailRequest++;
    busy = true; render(); status(t("正在读取资源类型…"));
    try {
      const result = await execute("ListResourceTypes", {});
      if (destroyed) return false;
      if (!Array.isArray(result.types)) throw new Error(t("无法读取资源类型。"));
      rows = result.types;
      const row = rows.find(row => row.type === selected);
      if (row) showDetail(row); else { selected = ""; $("[data-types-detail]").hidden = true; }
      status(tx`共 ${rows.length} 种资源类型。`);
      return true;
    } catch (error) { status(error.message); return false; }
    finally { busy = false; render(); }
  }
  async function toggle(type) {
    if (busy || destroyed || !canWrite) return;
    const row = rows.find(row => row.type === type);
    if (!row || row.state === "not_installed" || row.installed === false) return;
    busy = true; render();
    try {
      if (row.enabled) {
        const pages = row.catalog_section ? tx`Resource 目录（${row.catalog_section}）、搜索` : t("搜索");
        const approved = await window.PortalUI.confirm({
          title: tx`停用资源类型 ${row.type}？`,
          message: tx`现有 ${row.instance_count ?? "—"} 个实例。停用后将退出${pages}，并拒绝新登记；已有实例仍可按 ID 读取。`,
          confirmLabel: t("停用"), tone: "danger",
        });
        if (!approved || destroyed) return;
      }
      const updated = await execute("UpdateResourceType", {resource_type: type, enabled: !row.enabled}, {expectedVersion: row.version});
      if (destroyed) return;
      Object.assign(row, updated);
      if (selected === type) showDetail(row);
      status(tx`${type}：${stateTitle(row.state)}`);
      window.dispatchEvent(new CustomEvent("portal:resource-types-changed"));
    } catch (error) {
      if (error.code === "instruction.version_conflict") {
        try {
          const updated = await execute("GetResourceType", {resource_type: type});
          if (destroyed) return;
          Object.assign(row, updated); if (selected === type) showDetail(row);
          status(t("类型状态已被其他人修改，已刷新，请重试。"));
          window.dispatchEvent(new CustomEvent("portal:resource-types-changed"));
        } catch (readError) { status(tx`刷新类型状态失败：${readError.message}`); }
      } else {
        if (error.code === "instruction.forbidden") canWrite = false;
        status(error.message);
      }
    } finally { busy = false; render(); }
  }
  $("[data-types-category]").addEventListener("change", render);
  $("[data-types-state]").addEventListener("change", render);
  $("[data-types-refresh]").addEventListener("click", refresh);
  $("[data-types-rows]").addEventListener("click", event => {
    const action = event.target.closest("[data-type-toggle]");
    if (action) { void toggle(action.dataset.typeToggle); return; }
    const row = event.target.closest("[data-resource-type]");
    if (row) void detail(row.dataset.resourceType);
  });
  const tagPanel = $("[data-tags]");
  const tagForm = $("[data-tags-form]");
  const tagText = tagForm.elements.tags;
  const tagSave = tagForm.querySelector('button[type="submit"]');
  const tagReload = $("[data-tags-reload]");
  const tagStatus = $("[data-tags-status]");
  const tagAbort = new AbortController();
  let tagBinding, tagVersion, savedTags = "", tagBusy = false;
  const tagCall = (name, args, options = {}) => execute(name, args, {...options, signal: tagAbort.signal});
  async function editTags(work) {
    if (tagBusy || destroyed) return;
    tagBusy = true; tagSave.disabled = tagReload.disabled = true; tagText.readOnly = true;
    try {
      await work();
    } catch (error) {
      if (!destroyed) tagStatus.textContent = error.message;
    } finally {
      tagBusy = false;
      if (!destroyed) {
        tagReload.disabled = false;
        tagText.readOnly = !tagBinding?.can_update || !tagVersion;
        tagSave.disabled = tagText.readOnly || tagText.value === savedTags;
      }
    }
  }
  async function readTags() {
    return editTags(async () => {
      if (tagText.value !== savedTags && !await window.PortalUI.confirm({
        title: t("放弃未保存的标签修改？"), confirmLabel: t("重新读取"),
      })) return;
      if (destroyed) return;
      tagBinding = await window.PortalAPI.get("/api/management/knowledge-tags", {signal: tagAbort.signal, cache: "no-store"});
      if (destroyed) return;
      const result = await tagCall("ReadResource", {address: tagBinding.resource_id});
      if (destroyed) return;
      const tags = JSON.parse(result.text);
      if (!Array.isArray(tags) || !result.version) throw new Error(t("无法读取知识标签。"));
      tagVersion = result.version;
      savedTags = tagText.value = JSON.stringify(tags, null, 2);
      tagStatus.textContent = "";
    });
  }
  tagPanel.addEventListener("toggle", () => { if (tagPanel.open && !tagVersion) void readTags(); });
  tagReload.addEventListener("click", readTags);
  tagText.addEventListener("input", () => { tagSave.disabled = tagBusy || tagText.value === savedTags; });
  tagForm.addEventListener("submit", event => {
    event.preventDefault();
    if (!tagBinding?.can_update || !tagVersion || tagSave.disabled) return;
    void editTags(async () => {
      let tags;
      try { tags = JSON.parse(tagText.value); }
      catch { throw new Error(t("标签列表必须是有效的 JSON 数组。")); }
      if (!Array.isArray(tags)) throw new Error(t("标签列表必须是有效的 JSON 数组。"));
      try {
        const result = await tagCall("UpdateResource", {
          resource_id: tagBinding.resource_id, changes: {content: JSON.stringify(tags)},
        }, {expectedVersion: tagVersion});
        if (destroyed) return;
        savedTags = tagText.value;
        tagVersion = result.version;
        tagStatus.textContent = t("已保存知识标签。");
      } catch (error) {
        if (error.code === "instruction.version_conflict") tagVersion = "";
        throw error;
      }
    });
  });
  await refresh();
  return {refresh, isBusy: () => busy || tagBusy, hasUnsavedChanges: () => tagText.value !== savedTags,
    destroy() { destroyed = true; tagAbort.abort(); detailRequest++; root.replaceChildren(); }};
}
