(function () {
  "use strict";

  if (window.PortalManagementPaths) return;

  // The data directories: the instance root, each area's location, size and
  // free space, and the fixed directories. An administrator submits「移动区域」
  // for one area and follows the move, also one started before the page was
  // loaded; the Web service itself stops while it runs.

  const DEPENDENCIES = Object.freeze([
    "panelState", "root",
  ]);

  const AREA_LABELS = Object.freeze({
    projects: "项目", lineages: "Lineage", skills: "共享 Skill", papers: "论文库",
    uploads: "上传文件", features: "功能数据", "provider-state": "Provider 状态",
  });
  const FIXED_LABELS = Object.freeze({
    secrets: "密钥", database: "数据库", cache: "缓存", runtime: "运行文件",
  });
  const STAGES = Object.freeze({
    queued: "排队中", stopping: "正在停止服务", moving: "正在移动文件", starting: "正在启动服务",
    cleaning: "正在删除原目录", completed: "移动完成", failed: "移动失败",
  });

  function formatBytes(value) {
    if (typeof value !== "number") return "";
    const units = ["B", "KB", "MB", "GB", "TB"];
    let size = value, unit = 0;
    while (size >= 1024 && unit < units.length - 1) { size /= 1024; unit += 1; }
    return `${size >= 10 || unit === 0 ? Math.round(size) : size.toFixed(1)} ${units[unit]}`;
  }

  function createPaths(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalManagementPaths is missing: ${missing.join(", ")}`);
    const { panelState, root } = dependencies;
    const t = text => window.PortalI18n.t(text);

    const rootLine = root.querySelector("[data-management-paths-root]");
    const list = root.querySelector("[data-management-paths-list]");
    const status = root.querySelector("[data-management-paths-status]");
    let read = false, moving = false;

    const element = (tag, text, parent) => {
      const node = document.createElement(tag);
      if (text) node.textContent = text;
      parent.append(node);
      return node;
    };

    async function measure(name, cell) {
      try {
        const response = await fetch(`/api/admin/paths/usage?name=${encodeURIComponent(name)}`, {credentials: "same-origin"});
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || t("无法计算占用大小"));
        cell.textContent = `${t("占用")} ${formatBytes(result.size)}`;
      } catch (error) {
        cell.textContent = error.message;
      }
    }

    function renderItem(item, label, movable) {
      const row = element("section", "", list);
      row.className = "managementPathsItem";
      element("h3", `${t(label)} · ${item.name}`, row);
      element("code", item.path, row);
      const facts = element("small", "", row);
      const usage = element("span", t("占用：计算中…"), facts);
      element("span", ` · ${t("剩余空间")} ${formatBytes(item.free)}`, facts);
      if (!item.exists) element("span", ` · ${t("目录尚未创建")}`, facts);
      if (movable) {
        if (!item.configured) element("small", t("默认位置"), row);
        const form = element("form", "", row);
        form.className = "managementPathsMove";
        const input = element("input", "", form);
        input.placeholder = t("新的绝对路径");
        input.setAttribute("aria-label", `${t(label)} ${t("新的绝对路径")}`);
        const button = element("button", t("移动区域"), form);
        button.type = "submit"; button.className = "portalButton";
        button.disabled = moving;
        form.addEventListener("submit", event => { event.preventDefault(); void move(item, label, input.value.trim()); });
      } else {
        element("small", t("固定位置，不可修改"), row);
      }
      return usage;
    }

    async function readPaths() {
      if (read) return;
      read = true;
      try {
        const response = await fetch("/api/admin/paths", {credentials: "same-origin"});
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || t("无法读取数据目录"));
        rootLine.replaceChildren();
        element("span", `${t("实例根目录")}：`, rootLine);
        element("code", result.root.path, rootLine);
        element("span", ` · ${t("剩余空间")} ${formatBytes(result.root.free)}`, rootLine);
        list.replaceChildren();
        const sizes = [
          ...result.areas.map(item => [item.name, renderItem(item, AREA_LABELS[item.name] || item.name, true)]),
          ...result.fixed.map(item => [item.name, renderItem(item, FIXED_LABELS[item.name] || item.name, false)]),
        ];
        // Large areas take a while to measure; one at a time keeps the server responsive.
        for (const [name, cell] of sizes) {
          if (panelState.destroyed) return;
          await measure(name, cell);
        }
      } catch (error) { read = false; status.textContent = error.message; }
    }

    function showOperation(record) {
      if (!record || record.action !== "move-area") return false;
      const label = STAGES[record.stage] || record.stage || record.status;
      status.textContent = `${t(AREA_LABELS[record.area] || record.area)}：${t(label)}${record.error ? `：${record.error}` : ""}`;
      return ["queued", "running"].includes(record.status);
    }

    async function follow() {
      if (panelState.destroyed) return;
      try {
        const response = await fetch("/api/admin/paths/operation", {credentials: "same-origin"});
        const result = await response.json();
        if (!response.ok) throw new Error(result.error);
        if (showOperation(result.operation)) {
          setTimeout(follow, 2000);
          return;
        }
        moving = false;
        read = false;
        await readPaths();
      } catch (_) {
        status.textContent = t("Portal 服务暂不可用，移动状态将在服务恢复后刷新。");
        setTimeout(follow, 2000);
      }
    }

    async function move(item, label, target) {
      if (!target) { status.textContent = t("请填写新的绝对路径。"); return; }
      if (!window.confirm(window.PortalI18n.text`要把区域「${t(label)}」从 ${item.path} 移到 ${target} 吗？\n\n移动期间 Portal 停止服务，完成后自动重启，连接会暂时断开。`)) return;
      moving = true;
      list.querySelectorAll("button").forEach(button => { button.disabled = true; });
      status.textContent = t("正在启动独立的移动任务…");
      try {
        const response = await fetch("/api/admin/paths", {
          method: "POST", credentials: "same-origin", headers: {"Content-Type": "application/json"},
          body: JSON.stringify({area: item.name, target}),
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || t("无法启动区域移动"));
        showOperation(result.operation);
        setTimeout(follow, 2000);
      } catch (error) {
        moving = false;
        status.textContent = error.message;
        list.querySelectorAll("button").forEach(button => { button.disabled = false; });
      }
    }

    // A move started earlier, from this page or another, is still followed.
    void (async () => {
      try {
        const response = await fetch("/api/admin/paths/operation", {credentials: "same-origin"});
        const result = await response.json();
        const record = response.ok ? result.operation : null;
        if (["queued", "running"].includes(record?.status) && showOperation(record)) {
          moving = true;
          setTimeout(follow, 2000);
        }
      } catch (_) { /* The tab reports read errors when it opens. */ }
    })();

    return {
      readPaths,
    };
  }

  window.PortalManagementPaths = Object.freeze({ createPaths, dependencies: DEPENDENCIES });
})();
