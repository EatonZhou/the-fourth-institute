(() => {
  "use strict";

  if (window.PortalResidentMenuRuntime) return;

  // The Settings tab's runtime: the Session ID, and the runtime setting
  // rows that open the Runtime Settings dialog (a Family facade shows its
  // Family settings instead).

  const DEPENDENCIES = Object.freeze([
    "$id", "esc", "menuState", "nodeMenuNotice", "renderFamilySettings",
  ]);

  function createRuntime(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalResidentMenuRuntime is missing: ${missing.join(", ")}`);
    const {
      $id, esc, menuState, nodeMenuNotice, renderFamilySettings,
    } = dependencies;

    function renderNodeMenuSession(context, {detail, error}) {
      if (menuState.nodeMenuContext !== context) return;
      if (!error) {
        const backend = String(detail?.resident?.backend || "").trim();
        if (backend && backend !== context.node.backend) {
          context.node = {...context.node, backend};
          void loadNodeMenuRuntime();
        }
        context.sessionId = String(detail?.provider_session?.id || "").trim();
        $id("nodeMenuSessionId").textContent = context.sessionId || window.PortalI18n.t("尚未创建");
        $id("nodeMenuCopySession").disabled = !context.sessionId;
      } else {
        context.sessionId = "";
        $id("nodeMenuSessionId").textContent = window.PortalI18n.text`读取失败：${error.message}`;
        $id("nodeMenuCopySession").disabled = true;
      }
    }

    async function loadNodeMenuSession({force = false} = {}) {
      const context = menuState.nodeMenuContext;
      if (!context) return;
      try {
        const detail = await window.PortalResidentProjection.load(context.residentId, {force});
        renderNodeMenuSession(context, {detail});
      } catch (error) {
        renderNodeMenuSession(context, {error});
      }
    }

    async function loadNodeMenuRuntime() {
      const context = menuState.nodeMenuContext;
      if (!context) return;
      void loadNodeMenuReporting(context);
      if (context.family) { renderFamilySettings(context); return; }
      if (!context.runtimeController) {
        context.runtimeController = window.PortalResidentRuntimeController.bind(context.residentId, {
          onState: view => {
            if (menuState.nodeMenuContext !== context) return;
            context.runtimeBusy = view.loading;
            context.runtimeSaving = view.busy;
            context.saving = view.busy || context.reportingSaving;
            const rows = [["Backend", view.detail?.resident?.backend || context.node.backend || "—"],
              ...window.PortalChatWorkspace.residentRuntimeSettingRows(view.settings, view.catalog || {})];
            const disabled = view.loading || view.busy || ["accepted", "submitting", "confirming", "cancelling"].includes(view.restart?.status)
              || (!view.canEdit && !view.canRestart);
            const root = $id("nodeMenuRuntime");
            const focus = root.contains(document.activeElement) ? document.activeElement?.dataset.menuRuntime : null;
            root.innerHTML = rows.map(([label, value]) => window.PortalI18n.html`<button class="residentMenuSetting" type="button" data-menu-runtime="${label}" aria-haspopup="dialog" ${disabled ? "disabled" : ""}><span>${label}</span><strong>${esc(value)}</strong><span aria-hidden="true">›</span></button>`).join("");
            const quotaSwitch = view.quotaSwitch;
            if (typeof quotaSwitch?.auto_switch_on_quota === "boolean") {
              const row = document.createElement("div"); row.className = "familyAutoMigrateSetting";
              const label = document.createElement("span"); label.textContent = window.PortalI18n.t("在当前帐号额度用完时是否自动切换");
              const toggle = document.createElement("button"); toggle.type = "button";
              toggle.className = "portalButton residentMenuToggle"; toggle.dataset.menuQuotaSwitch = "1";
              toggle.setAttribute("role", "switch"); toggle.setAttribute("aria-label", label.textContent);
              toggle.setAttribute("aria-checked", String(quotaSwitch.auto_switch_on_quota));
              toggle.disabled = disabled || !view.canEdit;
              toggle.title = window.PortalI18n.t("开启后仅按默认帐号选择排序切换；关闭时额度不足会打开手动选择。未修改时沿用既有策略。");
              toggle.onclick = async () => {
                toggle.disabled = true;
                try { await context.runtimeController.saveSettings({auto_switch_on_quota: !quotaSwitch.auto_switch_on_quota}); }
                catch (error) { if (menuState.nodeMenuContext === context) nodeMenuNotice(error.message); }
                finally { if (menuState.nodeMenuContext === context) context.runtimeController.load().catch(() => {}); }
              };
              row.append(label, toggle); root.append(row);
            }
            if (focus) root.querySelector(`[data-menu-runtime="${CSS.escape(focus)}"]`)?.focus();
          },
          onChanged: () => { if (menuState.nodeMenuContext === context) void loadNodeMenuSession({force: true}); },
        });
        $id("nodeMenuRuntime").onclick = async event => {
          const button = event.target.closest("[data-menu-runtime]");
          if (!button || button.disabled || context.reportingSaving || menuState.nodeMenuContext !== context) return;
          const field = button.dataset.menuRuntime;
          try {
            await context.runtimeController.openSettings(() => $id("nodeMenuRuntime").querySelector(`[data-menu-runtime="${CSS.escape(field)}"]`));
          } catch (error) { if (menuState.nodeMenuContext === context) nodeMenuNotice(error.message); }
        };
      }
      try { await context.runtimeController.load(); }
      catch (error) { if (menuState.nodeMenuContext === context) $id("nodeMenuRuntime").textContent = window.PortalI18n.text`运行设置读取失败：${error.message}`; }
    }

    async function loadNodeMenuReporting(context) {
      const root = $id("nodeMenuReporting");
      if (!root || menuState.nodeMenuContext !== context || context.reportingLoading || context.reportingSaving) return;
      // Keep an edited form intact when the Settings tab is revisited.
      if (context.reportingForm && context.reportingForm === root.querySelector("form")) return;
      root.dataset.residentId = context.residentId;
      root.textContent = window.PortalI18n.t("正在读取汇报设置…");
      context.reportingLoading = true;
      try {
        const [result, detail] = await Promise.all([
          window.PortalResidentService.runtimeSettings(context.residentId),
          window.PortalResidentService.projection({residentId: context.residentId}),
        ]);
        if (menuState.nodeMenuContext !== context) return;
        const reporting = result.settings?.reporting;
        if (!reporting || result.settings_revision == null) throw new Error(window.PortalI18n.t("汇报设置暂不可用，请重试。"));
        const canEdit = detail.allows?.UpdateResidentSettings ?? Boolean(detail.chat_workspace?.actions?.runtime_settings);
        context.reportingRevision = result.settings_revision;
        context.reportingCommitted = false;
        const choice = (name, value, label, description = "") => `<label class="residentReportingChoice">
          <input type="radio" name="${name}" value="${value}" ${canEdit ? "" : "disabled"}>
          <span><strong>${esc(window.PortalI18n.t(label))}</strong>${description ? `<small>${esc(window.PortalI18n.t(description))}</small>` : ""}</span>
        </label>`;
        root.innerHTML = window.PortalI18n.html`<form class="residentMenuReportingForm">
          <h3>汇报设置</h3>
          <fieldset class="residentReportingInitiative" aria-describedby="resident-reporting-initiative-note">
            <legend>汇报主动性</legend>
            <div class="residentReportingLevels">
              ${choice("initiative", "off", "0（不主动汇报）")}${choice("initiative", "low", "低")}${choice("initiative", "medium", "中")}${choice("initiative", "high", "高")}
            </div>
            <p class="residentReportingNote" id="resident-reporting-initiative-note">主动性只影响主动汇报；用户明确要求的汇报仍会执行。高主动性也按实际价值判断，不保证每次请求都生成汇报。</p>
          </fieldset>
          <fieldset class="residentReportingQuality">
            <legend>汇报质量</legend>
            <div class="residentReportingFormats">
              ${choice("quality", "low", "低", "直接输出 Markdown")}
              ${choice("quality", "medium", "中", "使用网页交互模板")}
              ${choice("quality", "high", "高", "独立 Resident 审查与修改直至达标")}
            </div>
          </fieldset>
          <div data-reporting-conflict hidden></div>
          <footer class="residentReportingActions"><button class="portalButton is-primary" type="submit" ${canEdit ? "" : "disabled"}>保存汇报设置</button><p role="status" data-reporting-status></p></footer>
        </form>`;
        const form = root.querySelector("form");
        context.reportingForm = form;
        form.elements.initiative.value = reporting.initiative;
        form.elements.quality.value = reporting.quality;
        const conflictRoot = form.querySelector("[data-reporting-conflict]");
        const refreshConflict = async () => {
          const current = () => menuState.nodeMenuContext === context && context.reportingForm === form;
          if (!current()) return;
          conflictRoot.hidden = false;
          conflictRoot.replaceChildren();
          const note = document.createElement("p");
          note.textContent = window.PortalI18n.t("编辑已保留，正在读取最新汇报设置…");
          conflictRoot.append(note);
          try {
            const latest = await window.PortalResidentService.runtimeSettings(context.residentId);
            if (!current()) return;
            if (!latest.settings?.reporting || latest.settings_revision == null) throw new Error(window.PortalI18n.t("汇报设置暂不可用，请重试。"));
            const names = {off: "0（不主动汇报）", low: "低", medium: "中", high: "高"};
            note.textContent = window.PortalI18n.text`当前已保存：主动性 ${window.PortalI18n.t(names[latest.settings.reporting.initiative])}；质量 ${window.PortalI18n.t(names[latest.settings.reporting.quality])}。请核对后选择。`;
            const choose = (label, apply) => {
              const button = document.createElement("button");
              button.type = "button"; button.className = "portalButton is-compact";
              button.textContent = window.PortalI18n.t(label);
              button.onclick = () => {
                if (!current() || context.reportingSaving) return;
                context.reportingRevision = latest.settings_revision;
                context.reportingConflict = false;
                conflictRoot.replaceChildren(); conflictRoot.hidden = true;
                form.querySelector('[type="submit"]').disabled = false;
                apply();
              };
              conflictRoot.append(button);
            };
            choose("加载最新设置，替换当前编辑", () => {
              form.elements.initiative.value = latest.settings.reporting.initiative;
              form.elements.quality.value = latest.settings.reporting.quality;
              form.querySelector("[data-reporting-status]").textContent = window.PortalI18n.t("已加载最新设置，尚未提交修改。");
            });
            choose("保留当前编辑，按最新版本重试保存", () => form.requestSubmit());
          } catch (error) {
            if (!current()) return;
            note.textContent = error.message;
            const retry = document.createElement("button"); retry.type = "button"; retry.className = "portalButton is-compact";
            retry.textContent = window.PortalI18n.t("重新读取最新设置"); retry.onclick = refreshConflict;
            conflictRoot.append(retry);
          }
        };
        if (!canEdit) form.querySelector("[data-reporting-status]").textContent = window.PortalI18n.t("无权限");
        form.addEventListener("submit", async event => {
          event.preventDefault();
          if (!canEdit || context.reportingSaving || context.reportingConflict || menuState.nodeMenuContext !== context) return;
          const selected = {initiative: form.elements.initiative.value, quality: form.elements.quality.value};
          const status = form.querySelector("[data-reporting-status]");
          context.reportingSaving = true;
          context.saving = true;
          $id("nodeMenuRuntime").inert = true;
          form.querySelectorAll("input, button").forEach(control => { control.disabled = true; });
          try {
            if (!context.reportingCommitted) {
              const signature = JSON.stringify(selected);
              if (context.reportingPending && signature !== context.reportingPending.signature) {
                throw new Error(window.PortalI18n.t("上次保存结果待确认，请先重试原设置。"));
              }
              context.reportingPending ||= {signature, key: window.PortalUIPrimitives.randomUUID()};
              await window.PortalResidentService.updateRuntimeSettings(context.residentId,
                {reporting: selected}, context.reportingPending.key, context.reportingRevision);
              context.reportingCommitted = true;
              context.reportingPending = null;
            }
            const saved = await window.PortalResidentService.runtimeSettings(context.residentId);
            if (menuState.nodeMenuContext !== context) return;
            context.reportingRevision = saved.settings_revision;
            form.elements.initiative.value = saved.settings.reporting.initiative;
            form.elements.quality.value = saved.settings.reporting.quality;
            context.reportingCommitted = false;
            window.PortalResidentProjection?.invalidate?.(context.residentId);
            status.textContent = window.PortalI18n.t("汇报设置已保存。");
          } catch (error) {
            if (Number(error.status) >= 400 && Number(error.status) < 500 && Number(error.status) !== 408) context.reportingPending = null;
            if (menuState.nodeMenuContext === context) status.textContent = context.reportingCommitted
              ? window.PortalI18n.t("设置已提交，回读失败；再次保存只重试读取。") : error.message;
            if (!context.reportingCommitted && (Number(error.status) === 409 || error.code === "instruction.version_conflict" || error.code === "instruction.conflict")) {
              context.reportingConflict = true;
              await refreshConflict();
            }
          } finally {
            context.reportingSaving = false;
            context.saving = Boolean(context.runtimeSaving);
            if (menuState.nodeMenuContext === context) {
              $id("nodeMenuRuntime").inert = false;
              form.querySelectorAll("input, button").forEach(control => {
                control.disabled = Boolean(context.reportingCommitted || context.reportingPending) && control.tagName === "INPUT";
                if (context.reportingConflict && control.type === "submit") control.disabled = true;
              });
            }
          }
        });
      } catch (error) {
        if (menuState.nodeMenuContext !== context) return;
        root.replaceChildren();
        const status = document.createElement("p"); status.setAttribute("role", "alert"); status.textContent = error.message;
        const retry = document.createElement("button"); retry.type = "button"; retry.className = "portalButton is-compact";
        retry.textContent = window.PortalI18n.t("重试"); retry.onclick = () => loadNodeMenuReporting(context);
        root.append(status, retry);
      } finally { context.reportingLoading = false; }
    }

    return {
      loadNodeMenuRuntime, renderNodeMenuSession,
    };
  }

  window.PortalResidentMenuRuntime = Object.freeze({ createRuntime, dependencies: DEPENDENCIES });
})();
