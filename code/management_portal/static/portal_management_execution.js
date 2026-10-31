(function () {
  "use strict";

  if (window.PortalManagementExecution) return;

  // The execution settings: the setting inputs the distribution includes,
  // the values read and saved, reading the tab when it opens, and saving
  // an input when it changes.

  const DEPENDENCIES = Object.freeze([
    "allows", "executionSection", "me", "panelState", "readFeatureSwitches", "readSchemes",
    "readTelepus", "readWallScheduling", "root", "savePermissionSystem", "showReadNote", "syncPermissionSystem",
  ]);

  function createExecution(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalManagementExecution is missing: ${missing.join(", ")}`);
    const {
      allows, executionSection, me, panelState, readFeatureSwitches, readSchemes, readTelepus,
      readWallScheduling, root, savePermissionSystem, showReadNote, syncPermissionSystem,
    } = dependencies;

    // Portal-wide execution settings; read when the tab first opens, each control saved on change.
    const included = name => !Array.isArray(window.__PORTAL_FRONTEND__?.distribution_features) || window.__PORTAL_FRONTEND__.distribution_features.includes(name);
    const executionInputs = [...root.querySelectorAll("[data-execution-setting]")].filter(input => {
      const feature = input.dataset.executionSetting.startsWith("harness_") ? "harness_recording" : input.dataset.executionSetting === "model_request_timing_enabled" ? "model_request_timing" : null;
      if (!feature || included(feature)) return true;
      input.closest("label").hidden = true;
      return false;
    });
    const executionStatus = executionSection.querySelector("[data-management-execution-status]");

    const switchMessages = {
      model_request_timing_enabled: on => on ? window.PortalI18n.t("请求时间记录已开启。") : window.PortalI18n.t("请求时间记录已关闭，缺失的时间保持未知。"),
      harness_recording_enabled: on => on ? window.PortalI18n.t("Harness 记录已开启。") : window.PortalI18n.t("Harness 记录已关闭，已有记录按保留天数清理。"),
      permission_system_enabled: on => on ? window.PortalI18n.t("权限系统已开启，启用方案的实例已补齐。") : window.PortalI18n.t("权限系统已关闭，方案暂不参与判定。"),
    };
    let executionRead = false;

    const showSetting = input => {
      const value = panelState.savedExecution[input.dataset.executionSetting];
      if (input.type === "checkbox") input.checked = value === true;
      else input.value = typeof value === "number" ? String(value) : "";
      const state = input.dataset.executionSetting === "permission_system_enabled" && input.closest("label").querySelector("[data-permission-system-state]");
      if (state) {
        state.hidden = typeof value !== "boolean";
        state.textContent = window.PortalI18n.t(value ? "当前：已开启" : "当前：已关闭");
      }
    };
    // Only the given controls are redrawn, so a value typed into another one survives a save.
    const acceptExecution = (settings, inputs) => {
      for (const input of inputs) {
        if (typeof settings?.[input.dataset.executionSetting] !== (input.type === "checkbox" ? "boolean" : "number")) {
          throw new Error(window.PortalI18n.t("未能读取执行设置"));
        }
      }
      const accepted = {...settings};
      // An unrelated setting's older response cannot undo a newer permission toggle.
      if (!inputs.some(input => input.dataset.executionSetting === "permission_system_enabled")) delete accepted.permission_system_enabled;
      panelState.savedExecution = {...panelState.savedExecution, ...accepted};
      inputs.forEach(showSetting);
      if (typeof accepted.permission_system_enabled === "boolean") syncPermissionSystem(accepted.permission_system_enabled);
    };

    // /api/me asks UpdateExecutionSettings about the switch alone, which a Portal 管理员 always passes
    // whatever the scheme (b820 §3.1); the switch shows /api/me's mode, so a scheme that refuses
    // GetExecutionSettings cannot take it away.
    const permissionSwitches = executionInputs.filter(input => input.dataset.executionSetting === "permission_system_enabled");
    const switchInput = permissionSwitches[0];
    const maySwitch = allows.UpdateExecutionSettings === true;

    const readExecutionSettings = () => {
      if (executionRead) { void readTelepus(); void readWallScheduling(); readFeatureSwitches(); return; }
      executionRead = true;
      void readTelepus();
      void readWallScheduling();
      readFeatureSwitches();
      readSchemes();
      if (maySwitch && typeof me.access?.permission_system === "string" && typeof panelState.savedExecution.permission_system_enabled !== "boolean") {
        panelState.savedExecution.permission_system_enabled = me.access.permission_system !== "open";
        showSetting(switchInput);
        switchInput.disabled = false;
      }
      const revision = panelState.permissionRevision;
      void window.PortalInstructionClient.execute("GetExecutionSettings", {}).then(result => {
        if (panelState.destroyed) return;
        const settings = {...result.settings};
        if (revision !== panelState.permissionRevision) settings.permission_system_enabled = panelState.savedExecution.permission_system_enabled;
        acceptExecution(settings, executionInputs);
        showReadNote("settings", "");
        executionInputs.forEach(input => { input.disabled = permissionSwitches.includes(input) && (!maySwitch || panelState.permissionSaving); });
      }).catch(error => {
        if (panelState.destroyed) return;
        // Opening the tab again reads again.
        executionRead = false;
        showReadNote("settings", switchInput.disabled ? error.message
          : window.PortalI18n.t("{p0} 权限系统开关仍可使用。", {p0: error.message}));
      });
    };

    // Saves an input when it changes; the permission system switch asks first.
    function startExecutionInputs() {
      for (const input of executionInputs) {
        input.addEventListener("change", async () => {
          const field = input.dataset.executionSetting;
          let value = input.checked;
          if (field === "permission_system_enabled") { await savePermissionSystem(value); return; }
          if (input.type === "number") {
            value = Number(input.value);
            const minimum = Number(input.min), maximum = Number(input.max);
            if (!input.value.trim() || !Number.isInteger(value) || value < minimum || value > maximum) {
              executionStatus.textContent = window.PortalI18n.t("请输入 {p0}–{p1} 之间的整数天数", {p0: minimum, p1: maximum});
              showSetting(input);
              return;
            }
            if (value === panelState.savedExecution[field]) return;
          }
          panelState.pending++;
          input.disabled = true;
          try {
            const result = await window.PortalInstructionClient.execute("UpdateExecutionSettings", {[field]: value},
              {idempotencyKey: `execution-settings:${globalThis.PortalUIPrimitives.randomUUID()}`});
            if (panelState.destroyed) return;
            acceptExecution(result.settings, [input]);
            executionStatus.textContent = window.PortalI18n.t(switchMessages[field]?.(value) || "保留天数已保存。");
          } catch (error) {
            if (!panelState.destroyed) { showSetting(input); executionStatus.textContent = error.message; }
          } finally {
            panelState.pending--;
            if (!panelState.destroyed) input.disabled = false;
          }
        });
      }
    }

    return {
      acceptExecution, executionStatus, included, maySwitch, permissionSwitches, readExecutionSettings, showSetting,
      startExecutionInputs, switchMessages,
    };
  }

  window.PortalManagementExecution = Object.freeze({ createExecution, dependencies: DEPENDENCIES });
})();
