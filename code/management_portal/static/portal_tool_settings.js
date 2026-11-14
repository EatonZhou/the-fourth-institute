(function () {
  "use strict";
  if (window.PortalToolSettings) return;
  const clone = value => JSON.parse(JSON.stringify(value));
  const t = value => window.PortalI18n.t(value);

  function mount(root) {
    let destroyed = false, generation = 0, searchGeneration = 0, timer;
    let selected = "", data = null, draft = null, baseline = "", saving = false, loading = false, confirming = false;
    let searchController;
    const reads = new Set(), discovered = new Map(), discoveries = new Map();
    const dirty = () => !!draft && JSON.stringify(draft) !== baseline;
    const current = epoch => !destroyed && epoch === generation;
    const editable = () => !!data?.resident?.can_edit && !!draft && !loading && !saving;
    const node = (tag, text, className) => {
      const el = document.createElement(tag);
      if (text !== undefined) el.textContent = t(text);
      if (className) el.className = className;
      return el;
    };
    function button(text, action, disabled = false) {
      const el = node("button", text, "portalButton is-compact");
      el.type = "button"; el.disabled = disabled; el.addEventListener("click", action); return el;
    }
    root.classList.add("toolSettings");
    const intro = node("p", "查看应用工具，选择 Resident 后显式保存注册、停用或撤销。注册不授予资源权限。实际执行仍按 Resident 权限。");
    const searchLabel = node("label", "选择 Resident", "portalField");
    const search = node("input"); search.type = "search"; search.placeholder = t("搜索 Resident 名称或 ID"); searchLabel.append(search);
    const results = node("div", undefined, "toolSettingsResults"); results.setAttribute("aria-live", "polite");
    const target = node("strong", "尚未选择 Resident");
    const status = node("p"); status.setAttribute("role", "status"); status.setAttribute("aria-live", "polite");
    const content = node("div");
    const actions = node("div", undefined, "toolSettingsActions");
    const save = button("保存工具设置", saveDraft, true);
    const reload = button("重新加载", () => load(selected));
    actions.append(save, reload);
    const customLabel = node("label", "添加自定义 MCP 连接资源地址", "portalField");
    const custom = node("input"); custom.type = "text"; custom.placeholder = t("资源 ID 或资源文件地址"); customLabel.append(custom);
    const discoverButton = button("发现工具", () => discover(custom.value.trim()), true);
    const customHelp = node("p", "连接资源由自定义服务提供者或资源区管理，格式为 tfi.mcp-connection.v1。发现仅列出工具，不调用工具，也不自动注册；勾选后保存。");
    root.replaceChildren(intro, searchLabel, results, target, status, actions, content, customLabel, discoverButton, customHelp);
    function notice(text) { if (!destroyed) status.textContent = t(text); }
    function update() {
      save.disabled = !editable() || !dirty();
      reload.disabled = saving || loading;
      custom.disabled = !editable(); discoverButton.disabled = !editable();
      target.textContent = data?.resident ? `${data.resident.title || selected} · ${selected}` : t(selected || "尚未选择 Resident");
    }
    async function read(callback) {
      const controller = new AbortController(); reads.add(controller);
      try { return await callback(controller.signal); } finally { reads.delete(controller); }
    }
    function cancelReads() { reads.forEach(controller => controller.abort()); reads.clear(); discoveries.clear(); }
    async function discard() {
      if (!dirty()) return true;
      if (confirming) return false;
      confirming = true;
      try { return await window.PortalUI.confirm({title: t("放弃未保存的工具修改？"), message: t("重新加载或切换 Resident 将清除草稿。"), confirmLabel: t("放弃修改"), tone: "danger"}); }
      finally { confirming = false; }
    }
    async function load(id, initial = false) {
      if (destroyed || saving || (!initial && !await discard()) || destroyed) return;
      cancelReads(); const epoch = ++generation;
      selected = id; loading = true; data = null; draft = null; baseline = ""; discovered.clear();
      content.replaceChildren(); notice("正在加载工具目录…"); update();
      try {
        const result = await read(signal => window.PortalAPI.get(`/api/tool-settings${id ? `?resident_id=${encodeURIComponent(id)}` : ""}`, {signal}));
        if (!current(epoch)) return;
        data = result;
        if (result.resident) {
          draft = clone(result.resident.capability_config); baseline = JSON.stringify(draft);
        }
        notice(result.resident ? (result.resident.can_edit ? "选择工具后请保存。配置保存不表示旧 Session 已装配。" : "此 Resident 的工具设置只读。") : "请选择有权管理的 Resident。应用目录不会自动注册。");
      } catch (error) { if (current(epoch)) notice(error.message); }
      finally { if (current(epoch)) { loading = false; render(); } }
    }
    function entry(group, ref) { return draft?.[group]?.find(item => item.ref === ref); }
    function change(group, ref, values) {
      if (!editable()) return;
      draft[group] ||= [];
      let item = entry(group, ref);
      if (!item) { item = {ref, enabled: true, required: false}; if (group === "mcp") item.tools = []; draft[group].push(item); }
      Object.assign(item, values);
      if (!item.enabled) item.required = false;
      if (item.required) item.enabled = true;
      notice("草稿尚未保存。点击保存工具设置后生效。");
      render();
    }
    function checkbox(label, checked, disabled, action) {
      const wrapper = node("label"); const input = node("input"); input.type = "checkbox"; input.checked = checked; input.disabled = disabled;
      input.addEventListener("change", () => action(input.checked)); wrapper.append(input, node("span", label)); return wrapper;
    }
    function selectionControls(group, ref, container) {
      const item = entry(group, ref), locked = !editable();
      if (!item) { container.append(button("注册", () => change(group, ref, {}), locked)); return; }
      container.append(checkbox("启用", item.enabled !== false, locked, value => change(group, ref, {enabled: value})),
        checkbox("必须可用", !!item.required, locked || item.enabled === false, value => change(group, ref, {required: value})),
        button("撤销注册", () => { if (!editable()) return; draft[group] = draft[group].filter(row => row.ref !== ref); render(); }, locked));
    }
    function schemaDetails(schema) {
      const details = node("details"); details.append(node("summary", "输入参数"), node("pre", JSON.stringify(schema || {}, null, 2))); return details;
    }
    function applicationState(group, ref) {
      const state = data?.resident?.application?.items?.find(item => item.path === `capability_config.${group}` && item.ref === ref)?.status;
      const labels = {disabled: "已保存：停用", pending_session: "等待新 Session", next_turn: "下一轮应用", unsupported: "当前 Backend 不支持", unknown: "实际装配待确认", pending_compaction_or_session: "等待压缩或新 Session"};
      return node("small", labels[state] || "实际装配状态待确认");
    }
    function render() {
      if (destroyed) return;
      content.replaceChildren();
      content.append(node("h3", "应用工具"));
      for (const app of data?.applications || []) {
        const row = node("section", undefined, "toolSettingsCard");
        row.append(node("strong", app.name || app.ref), node("code", app.ref), node("p", app.description || ""), node("small", app.mutates ? "会修改数据" : "读取工具"), schemaDetails(app.input_schema));
        const controls = node("div", undefined, "toolSettingsActions"); selectionControls("tools", app.ref, controls); row.append(controls);
        if (data?.resident) row.append(applicationState("tools", app.ref)); content.append(row);
      }
      const catalogRefs = new Set((data?.applications || []).map(app => app.ref));
      for (const item of draft?.tools || []) {
        if (!item.ref.startsWith("application:") || catalogRefs.has(item.ref)) continue;
        const row = node("section", undefined, "toolSettingsCard");
        row.append(node("strong", item.ref), node("p", "当前目录未提供此应用工具。已保存的选择保持原状，可停用或撤销注册。"));
        const controls = node("div", undefined, "toolSettingsActions");
        selectionControls("tools", item.ref, controls);
        row.append(controls, applicationState("tools", item.ref)); content.append(row);
      }
      if (!data?.applications?.length) content.append(node("p", "当前没有可显示的应用工具。"));
      content.append(node("h3", "自定义 MCP 工具"));
      const refs = new Set([...(draft?.mcp || []).map(item => item.ref), ...discovered.keys()]);
      for (const ref of refs) {
        const item = entry("mcp", ref), listing = discovered.get(ref), connection = data?.connections?.find(row => row.ref === ref);
        const row = node("section", undefined, "toolSettingsCard");
        row.append(node("strong", listing?.title || connection?.title || ref), node("code", ref));
        const controls = node("div", undefined, "toolSettingsActions"); selectionControls("mcp", ref, controls);
        controls.append(button("刷新工具目录", () => discover(ref), !editable())); row.append(controls);
        row.append(applicationState("mcp", ref));
        if (item && !item.tools?.length) row.append(node("p", "尚未选择工具，此连接不会装配工具。请发现后勾选所需工具。"));
        const names = new Set([...(item?.tools || []), ...(listing?.tools || []).map(tool => tool.name)]);
        for (const name of names) {
          const tool = listing?.tools?.find(tool => tool.name === name);
          const toolRow = node("div", undefined, "toolSettingsTool");
          toolRow.append(checkbox(name, !!item?.tools?.includes(name), !editable(), checked => {
            const names = new Set(entry("mcp", ref)?.tools || []); if (checked) names.add(name); else names.delete(name);
            change("mcp", ref, {tools: [...names]});
          }));
          if (tool) toolRow.append(node("p", tool.description || ""), schemaDetails(tool.inputSchema));
          else toolRow.append(node("small", "已保存的选择；尚未发现或当前目录未返回此工具。"));
          row.append(toolRow);
        }
        content.append(row);
      }
      update();
    }
    async function discover(ref) {
      if (!editable() || !ref) { if (!ref) notice("请输入 MCP 连接资源地址。"); return; }
      const epoch = generation;
      discoveries.get(ref)?.abort();
      const controller = new AbortController();
      discoveries.set(ref, controller); reads.add(controller);
      const latest = () => current(epoch) && discoveries.get(ref) === controller;
      notice("正在发现工具…");
      try {
        const result = await window.PortalAPI.post("/api/tool-settings/discover", {resident_id: selected, ref}, {signal: controller.signal});
        if (!latest()) return;
        discovered.set(result.ref, result); render(); notice("工具目录已加载。勾选工具后保存才会注册。");
      } catch (error) { if (latest()) notice(error.message); }
      finally { reads.delete(controller); if (discoveries.get(ref) === controller) discoveries.delete(ref); }
    }
    async function saveDraft() {
      if (!editable() || !dirty()) return;
      const epoch = generation, id = selected, snapshot = clone(draft);
      saving = true; render(); notice("正在保存…");
      try {
        // Writes complete even if the panel is destroyed. Never cancel a submitted CAS.
        const result = await window.PortalAPI.request("/api/tool-settings", {method: "PUT", body: {resident_id: id, expected_revision: data.resident.settings_revision, capability_config: snapshot}});
        window.PortalResidentProjection?.invalidate?.(id);
        window.PortalResidentRuntimeController?.invalidate?.(id);
        if (!current(epoch)) return;
        draft = clone(result.settings?.capability_config || snapshot); baseline = JSON.stringify(draft);
        data.resident.settings_revision = result.settings_revision; data.resident.application = result.application;
        notice("配置已保存；旧 Session 的实际装配状态待确认。新设置按后端应用时机生效。");
      } catch (error) {
        if (current(epoch)) notice(error.status === 409 ? "设置已被其他操作修改，草稿已保留。请重新加载最新设置后重新选择；重新加载前会确认放弃当前草稿。" : error.message);
      } finally { saving = false; if (current(epoch)) render(); }
    }
    search.addEventListener("input", () => {
      clearTimeout(timer); searchController?.abort(); const epoch = ++searchGeneration;
      timer = setTimeout(async () => {
        if (destroyed) return;
        searchController = new AbortController();
        try {
          const result = await window.PortalInstructionClient.execute("SearchResidents", {q: search.value.trim(), limit: 20}, {signal: searchController.signal});
          if (destroyed || epoch !== searchGeneration) return;
          results.replaceChildren(...(result.residents || []).map(row => button(`${row.title || row.resident_id} · ${row.resident_id}`, () => load(row.resident_id))));
          if (!result.residents?.length) results.append(node("p", "没有找到可见的 Resident。"));
        } catch (error) { if (!destroyed && epoch === searchGeneration) notice(error.message); }
      }, 250);
    });
    void load("", true);
    return {isBusy: () => saving, hasUnsavedChanges: dirty,
      destroy() { destroyed = true; generation++; searchGeneration++; clearTimeout(timer); searchController?.abort(); cancelReads(); root.replaceChildren(); },
    };
  }
  window.PortalToolSettings = Object.freeze({mount});
})();
