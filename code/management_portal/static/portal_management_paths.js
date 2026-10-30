(function () {
  "use strict";

  if (window.PortalManagementPaths) return;

  // The data directories form: reading the saved paths, starting a
  // migration with the new ones and following its progress, also one
  // started before the page was loaded.

  const DEPENDENCIES = Object.freeze([
    "panelState", "root",
  ]);

  function createPaths(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalManagementPaths is missing: ${missing.join(", ")}`);
    const {
      panelState, root,
    } = dependencies;

    const pathsForm = root.querySelector("[data-management-paths-form]");
    const pathsStatus = root.querySelector("[data-management-paths-status]");
    const pathInputs = [...pathsForm.querySelectorAll("[data-path-key]")];
    let pathsRead = false, pathMigrationState = "";
    let savedPaths = {};
    const readPaths = async () => {
      if (pathsRead) return;
      pathsRead = true;
      try {
        const response = await fetch("/api/admin/paths", {credentials: "same-origin"});
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || window.PortalI18n.t("无法读取目录配置"));
        savedPaths = result.paths;
        pathInputs.forEach(input => { input.value = savedPaths[input.dataset.pathKey] || ""; });
        if (pathMigrationState !== "completed") pathsStatus.textContent = "";
      } catch (error) { pathsRead = false; pathsStatus.textContent = error.message; }
    };
    const pollPathMigration = async jobId => {
      if (panelState.destroyed) return;
      try {
        const response = await fetch(`/api/admin/paths/migration?id=${encodeURIComponent(jobId)}`, {credentials: "same-origin"});
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || window.PortalI18n.t("无法读取迁移进度"));
        pathMigrationState = result.state; const labels = {queued: window.PortalI18n.t("排队中"), stopping: window.PortalI18n.t("正在停止服务"), copying: window.PortalI18n.t("正在复制并校验数据"), switching: window.PortalI18n.t("正在切换目录"), restarting: window.PortalI18n.t("正在重启服务"), completed: window.PortalI18n.t("迁移完成"), failed: window.PortalI18n.t("迁移失败")};
        pathsStatus.textContent = `${labels[result.state] || result.state}${result.error ? `：${result.error}` : ""}`;
        if (result.state === "completed" || result.state === "failed") {
          sessionStorage.removeItem("portal-path-migration");
          pathsForm.querySelector('[type="submit"]').disabled = false;
          if (result.state === "completed") { pathsRead = false; await readPaths(); }
          return;
        }
      } catch (_) { pathsStatus.textContent = window.PortalI18n.t("Portal 服务暂不可用，迁移状态将在服务恢复后刷新。"); }
      setTimeout(() => pollPathMigration(jobId), 2000);
    };
    pathsForm.addEventListener("submit", async event => {
      event.preventDefault();
      const proposed = Object.fromEntries(pathInputs.map(input => [input.dataset.pathKey, input.value.trim()]));
      const changed = pathInputs.filter(input => proposed[input.dataset.pathKey] !== savedPaths[input.dataset.pathKey]);
      if (!changed.length) { pathsStatus.textContent = window.PortalI18n.t("目录没有变化。"); return; }
      const description = changed.map(input => `${input.previousElementSibling?.textContent || input.dataset.pathKey}: ${savedPaths[input.dataset.pathKey]} → ${proposed[input.dataset.pathKey]}`).join("\n");
      if (!window.confirm(window.PortalI18n.text`要迁移现有数据并重启 Portal 服务吗？\n\n${description}\n\n迁移期间 Portal 将暂时不可用。`)) {
        pathsStatus.textContent = window.PortalI18n.t("未保存新路径。修改目录必须同时迁移数据。");
        pathInputs.forEach(input => { input.value = savedPaths[input.dataset.pathKey] || ""; });
        return;
      }
      const submit = pathsForm.querySelector('[type="submit"]');
      submit.disabled = true;
      pathsStatus.textContent = window.PortalI18n.t("正在启动独立迁移任务…");
      try {
        const response = await fetch("/api/admin/paths", {method: "POST", credentials: "same-origin", headers: {"Content-Type": "application/json"}, body: JSON.stringify({paths: proposed, migrate: true})});
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || window.PortalI18n.t("无法启动迁移"));
        sessionStorage.setItem("portal-path-migration", result.id);
        pollPathMigration(result.id);
      } catch (error) { pathsStatus.textContent = error.message; submit.disabled = false; }
    });
    const pendingPathJob = sessionStorage.getItem("portal-path-migration");
    if (pendingPathJob) pollPathMigration(pendingPathJob);

    return {
      readPaths,
    };
  }

  window.PortalManagementPaths = Object.freeze({ createPaths, dependencies: DEPENDENCIES });
})();
