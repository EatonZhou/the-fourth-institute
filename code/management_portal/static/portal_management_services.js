(function () {
  "use strict";

  if (window.PortalManagementServices) return;

  // The services and optional features of the execution settings: Telepus,
  // WALL scheduling and the feature switches, each read when the tab opens
  // and switched from its toggle.

  const DEPENDENCIES = Object.freeze([
    "allows", "executionSection", "included", "panelState", "portalAdmin",
  ]);

  function createServices(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalManagementServices is missing: ${missing.join(", ")}`);
    const {
      allows, executionSection, included, panelState, portalAdmin,
    } = dependencies;

    const telepusSection = executionSection.querySelector("[data-management-telepus]");
    const telepusToggle = telepusSection.querySelector("[data-telepus-enabled]");
    const telepusStatus = telepusSection.querySelector("[data-telepus-status]");
    telepusSection.hidden = !portalAdmin || allows.GetTelepusStatus !== true;
    const maySwitchTelepus = allows.SetTelepusEnabled === true;
    let telepusBusy = false;
    const renderTelepus = status => {
      telepusToggle.checked = status.enabled === true;
      telepusToggle.disabled = telepusBusy || !maySwitchTelepus || status.available !== true
        || (status.missing?.length > 0 && !status.enabled);
      const incomplete = (status.units || []).filter(unit => !unit.active || !unit.enabled).map(unit => unit.name);
      telepusStatus.textContent = status.available !== true
        ? window.PortalI18n.t("Telepus 服务状态不可用：{p0}", {p0: status.error || "systemd"})
        : status.missing?.length
          ? window.PortalI18n.t("缺少 Telepus 服务：{p0}。", {p0: status.missing.join("、")})
          : status.complete ? window.PortalI18n.t("Telepus 已开启，三个服务正在运行。")
            : status.enabled ? window.PortalI18n.t("Telepus 部分开启；需检查：{p0}。", {p0: incomplete.join("、")})
              : window.PortalI18n.t("Telepus 已关闭，三个服务已停止并取消开机自启。");
    };
    const readTelepus = async () => {
      if (telepusSection.hidden || panelState.destroyed || telepusBusy) return;
      try {
        const result = await window.PortalInstructionClient.execute("GetTelepusStatus", {});
        if (!panelState.destroyed) renderTelepus(result.status);
      } catch (error) {
        if (!panelState.destroyed) { telepusToggle.disabled = true; telepusStatus.textContent = error.message; }
      }
    };
    telepusToggle.addEventListener("change", async () => {
      const requested = telepusToggle.checked;
      telepusBusy = true;
      telepusToggle.disabled = true;
      telepusStatus.textContent = window.PortalI18n.t(requested ? "正在启动 Telepus 服务…" : "正在停止 Telepus 服务…");
      try {
        const result = await window.PortalInstructionClient.execute("SetTelepusEnabled", {enabled: requested},
          {idempotencyKey: `telepus-enabled:${globalThis.PortalUIPrimitives.randomUUID()}`});
        if (!panelState.destroyed) { telepusBusy = false; renderTelepus(result.status); }
      } catch (error) {
        if (!panelState.destroyed) {
          telepusBusy = false;
          await readTelepus();
          telepusStatus.textContent = error.message;
        }
      }
    });
    const wallSchedulingSection = executionSection.querySelector("[data-management-wall-scheduling]");
    const wallSchedulingToggle = wallSchedulingSection.querySelector("[data-wall-scheduling-enabled]");
    const wallSchedulingStatus = wallSchedulingSection.querySelector("[data-wall-scheduling-status]");
    wallSchedulingSection.hidden = !portalAdmin;
    const wallSchedulingRequest = async enabled => {
      const response = await fetch("/api/admin/wall-scheduling", {
        method: enabled === undefined ? "GET" : "POST", credentials: "same-origin",
        ...(enabled === undefined ? {} : {headers: {"Content-Type": "application/json"}, body: JSON.stringify({enabled})}),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || window.PortalI18n.t("无法读取 WALL 定时任务状态"));
      return result;
    };
    const readWallScheduling = async () => {
      if (wallSchedulingSection.hidden || panelState.destroyed) return;
      try {
        const result = await wallSchedulingRequest();
        if (panelState.destroyed) return;
        wallSchedulingToggle.checked = result.enabled === true;
        wallSchedulingToggle.disabled = false;
        wallSchedulingStatus.textContent = window.PortalI18n.t(result.enabled ? "WALL 定时任务已开启。" : "WALL 定时任务已暂停；各工程的计划配置保留。");
      } catch (error) {
        wallSchedulingToggle.disabled = true;
        wallSchedulingStatus.textContent = error.message;
      }
    };
    wallSchedulingToggle.addEventListener("change", async () => {
      const enabled = wallSchedulingToggle.checked;
      wallSchedulingToggle.disabled = true;
      try {
        const result = await wallSchedulingRequest(enabled);
        wallSchedulingToggle.checked = result.enabled === true;
        wallSchedulingToggle.disabled = false;
        wallSchedulingStatus.textContent = window.PortalI18n.t(result.enabled ? "WALL 定时任务已开启。" : "WALL 定时任务已暂停；各工程的计划配置保留。");
      } catch (error) {
        await readWallScheduling();
        wallSchedulingStatus.textContent = error.message;
      }
    });
    const featureSwitches = [...executionSection.querySelectorAll("[data-management-feature-switch]")].map(section => {
      const name = section.dataset.managementFeatureSwitch;
      const toggle = section.querySelector("[data-feature-switch-enabled]");
      const status = section.querySelector("[data-feature-switch-status]");
      section.hidden = !portalAdmin || (name === "pulse" && !included("pulse"));
      const request = async enabled => {
        const response = await fetch(enabled === undefined
          ? `/api/admin/feature-switch?name=${encodeURIComponent(name)}` : "/api/admin/feature-switch", {
          method: enabled === undefined ? "GET" : "POST", credentials: "same-origin",
          ...(enabled === undefined ? {} : {headers: {"Content-Type": "application/json"}, body: JSON.stringify({name, enabled})}),
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || window.PortalI18n.t("无法读取功能状态"));
        return result;
      };
      const render = result => {
        toggle.checked = result.enabled === true;
        toggle.indeterminate = result.partial === true;
        toggle.disabled = false;
        status.textContent = window.PortalI18n.t(result.partial ? "部分触发源已暂停，请重新切换以统一状态。"
          : result.enabled ? "已开启。" : "已关闭；已有结果保留。");
      };
      const read = async () => {
        if (section.hidden || panelState.destroyed) return;
        try {
          const result = await request();
          if (!panelState.destroyed) render(result);
        } catch (error) {
          if (!panelState.destroyed) { toggle.disabled = true; status.textContent = error.message; }
        }
      };
      toggle.addEventListener("change", async () => {
        const enabled = toggle.checked;
        toggle.disabled = true;
        try { if (!panelState.destroyed) render(await request(enabled)); }
        catch (error) { await read(); status.textContent = error.message; }
      });
      return read;
    });
    const readFeatureSwitches = () => featureSwitches.forEach(read => { void read(); });

    return {
      readFeatureSwitches, readTelepus, readWallScheduling,
    };
  }

  window.PortalManagementServices = Object.freeze({ createServices, dependencies: DEPENDENCIES });
})();
