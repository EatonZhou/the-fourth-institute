(() => {
  "use strict";

  const T = window.PortalTest;
  const panelStyles = `
    .testTauTaskView { --tau-edge: var(--border, #dfe3e8); }
    .testTauTaskView .testPanelBody { gap: 7px; }
    .testTauToolbar { position: sticky; z-index: 2; top: -12px; display: flex; align-items: center; justify-content: space-between; gap: 10px; margin: 0 0 1px; padding: 9px 0 8px; border-bottom: 1px solid var(--tau-edge); background: var(--panel, #fff); }
    .testTauSelection { display: grid; min-width: 0; gap: 1px; }
    .testTauSelection strong { font-size: 12px; line-height: 1.3; font-variant-numeric: tabular-nums; }
    .testTauSelection small { overflow: hidden; color: var(--muted, #68707a); font-size: 10px; line-height: 1.35; text-overflow: ellipsis; white-space: nowrap; }
    .testTauToolbar .testPanelActions { flex: 0 0 auto; gap: 5px; }
    .testTauToolbar .portalButton { min-height: 28px; padding: 4px 9px; font-size: 11px; }
    .testTauAdmission { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 5px 8px; border: 1px solid var(--tau-edge); border-radius: 8px; background: var(--panel-2, #fafbfc); }
    .testTauAdmission > span { display: grid; min-width: 0; gap: 2px; }
    .testTauAdmission strong { font-size: 11px; }
    .testTauAdmission small { color: var(--muted, #68707a); font-size: 10px; line-height: 1.35; }
    .testTauAdmissionActions { display: flex; align-items: center; gap: 8px; flex: 0 0 auto; }
    .testTauAdmissionActions button { padding: 2px; border: 0; color: var(--muted, #68707a); background: none; font: inherit; font-size: 10px; cursor: pointer; }
    .testTauTaskView .testTauModules { gap: 6px; }
    .testTauTaskView .testTauModule { overflow: hidden; padding: 0; border-color: var(--tau-edge); border-radius: 8px; }
    .testTauTaskView .testTauModule[data-selected="true"] { border-color: color-mix(in srgb, var(--accent, #0f7f75) 46%, var(--tau-edge)); }
    .testTauTaskView .testTauModule > header { align-items: flex-start; padding: 7px 8px 6px; }
    .testTauTaskView .testTauModule > header > div:first-child { min-width: 0; gap: 1px; }
    .testTauTaskView .testTauModule header strong { overflow: hidden; font-size: 12px; line-height: 1.3; text-overflow: ellipsis; white-space: nowrap; }
    .testTauTaskView .testTauModule header span { font-size: 10px; line-height: 1.3; }
    .testTauModulePick { flex: 0 0 auto; min-height: 25px; padding: 3px 7px; border: 1px solid var(--tau-edge); border-radius: 6px; color: var(--muted, #68707a); background: transparent; font: inherit; font-size: 10px; cursor: pointer; }
    .testTauModulePick:hover, .testTauModulePick[aria-pressed="true"] { border-color: var(--accent, #0f7f75); color: var(--accent, #0f7f75); background: color-mix(in srgb, var(--accent, #0f7f75) 7%, transparent); }
    .testTauTaskView .testTauResident { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: start; gap: 6px; min-width: 0; margin: 0; padding: 0 8px 6px; font-size: 10px; }
    .testTauIdentity { min-width: 0; color: var(--muted, #68707a); }
    .testTauIdentity > summary { display: flex; align-items: center; gap: 6px; min-width: 0; min-height: 19px; cursor: pointer; list-style: none; font-size: 9.5px; line-height: 1.25; }
    .testTauIdentity > summary::-webkit-details-marker { display: none; }
    .testTauIdentityLabel { flex: 0 0 auto; color: var(--text, #1d2733); font-weight: 650; }
    .testTauIdentityLabel::before { display: inline-block; width: 10px; content: "›"; color: var(--muted, #68707a); transform-origin: center; }
    .testTauIdentity[open] .testTauIdentityLabel::before { transform: rotate(90deg); }
    .testTauIdentityState { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .testTauIdentity dl { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 3px 7px; margin: 4px 0 1px 10px; font-size: 9.5px; }
    .testTauIdentity dt { color: var(--muted, #68707a); }
    .testTauIdentity dd { min-width: 0; margin: 0; }
    .testTauTaskView .testTauIdentity code { display: block; min-width: 0; overflow: hidden; color: var(--muted, #68707a); font-size: 9.5px; line-height: 1.25; text-overflow: ellipsis; white-space: nowrap; }
    .testTauResidentActions { display: flex; flex: 0 0 auto; align-items: center; gap: 6px; min-height: 19px; }
    .testTauResidentAction { padding: 1px 0; border: 0; color: var(--muted, #68707a); background: transparent; font: inherit; font-size: 10px; cursor: pointer; }
    .testTauResidentAction:hover { color: var(--accent, #0f7f75); text-decoration: underline; text-underline-offset: 2px; }
    .testTauResidentAction:disabled { cursor: default; opacity: .48; text-decoration: none; }
    .testTauTaskView .testTauCases { gap: 0; border-top: 1px solid var(--tau-edge); }
    .testTauTaskView .testTauCases label { align-items: flex-start; gap: 7px; padding: 6px 8px; border-radius: 0; background: transparent; cursor: pointer; }
    .testTauTaskView .testTauCases label + label { border-top: 1px solid var(--tau-edge); }
    .testTauTaskView .testTauCases label:hover { background: var(--panel-2, #fafbfc); }
    .testTauTaskView .testTauCases label[data-selected="true"] { background: color-mix(in srgb, var(--accent, #0f7f75) 6%, transparent); }
    .testTauTaskView .testTauCases input { flex: 0 0 15px; width: 15px; height: 15px; margin: 1px 0 0; padding: 0; accent-color: var(--accent, #0f7f75); }
    .testTauTaskView .testTauCases label > span { min-width: 0; gap: 1px; }
    .testTauTaskView .testTauCases strong { font-size: 11.5px; font-weight: 650; line-height: 1.32; }
    .testTauTaskView .testTauCases small { font-size: 9.5px; line-height: 1.3; }
    @container (max-width: 420px) {
      .testTauTaskView .testTauToolbar { align-items: flex-start; }
      .testTauTaskView .testTauSelection small { max-width: 165px; }
      .testTauTaskView .testTauModule > header { align-items: center; flex-direction: row; }
      .testTauTaskView .testTauResident { grid-template-columns: minmax(0, 1fr); gap: 2px; }
      .testTauTaskView .testTauResidentActions { flex-wrap: wrap; }
    }
  `;

  function create({ root }) {
    let selected = new Set();
    let admission = null;
    let admissionError = "";
    let admissionBusy = false;
    const expandedIdentity = new Set();
    const conversations = new Map();
    const tauCases = () => T.store.cases.filter(item => item.lifecycle !== "retired" && T.hasRunner(item, "tau"));
    const assignments = () => new Map((T.store.catalog?.tau_residents || []).map(item => [item.module_id, item]));
    const grouped = () => tauCases().reduce((result, item) => {
      if (!result.has(item.module)) result.set(item.module, []);
      result.get(item.module).push(item);
      return result;
    }, new Map());
    const caseMeta = item => [item.importance, ...(item.aspects || []).map(aspect => T.aspectLabels[aspect] || aspect)].filter(Boolean).join(" · ");
    const identityStatus = resident => resident.resident_id
      ? "Resident 已绑定 · Lineage 已保留"
      : "首次运行创建 Resident · Lineage 随模块保留";

    const syncSelection = () => {
      const allCases = tauCases();
      const allSelected = allCases.length > 0 && allCases.every(item => selected.has(item.id));
      root.querySelectorAll("[data-tau-case]").forEach(input => {
        input.checked = selected.has(input.dataset.tauCase);
        input.closest("label").dataset.selected = String(input.checked);
      });
      root.querySelectorAll("[data-tau-module]").forEach(button => {
        const cases = grouped().get(button.dataset.tauModule) || [];
        const count = cases.filter(item => selected.has(item.id)).length;
        button.textContent = count === cases.length ? window.PortalI18n.t("清除本组") : count ? window.PortalI18n.text`补选 ${cases.length - count}` : window.PortalI18n.t("选择本组");
        button.setAttribute("aria-pressed", String(Boolean(cases.length) && count === cases.length));
        button.closest(".testTauModule").dataset.selected = String(count > 0);
        button.closest("header").querySelector("[data-tau-module-count]").textContent = window.PortalI18n.text`${cases.length} 个任务${count ? window.PortalI18n.text` · 已选 ${count}` : ""}`;
      });
      const selection = root.querySelector("[data-tau-selection]");
      if (selection) selection.textContent = window.PortalI18n.text`已选 ${selected.size} / ${allCases.length}`;
      const allButton = root.querySelector("[data-tau-all]");
      if (allButton) {
        allButton.textContent = allSelected ? window.PortalI18n.t("清空") : window.PortalI18n.t("全选");
        allButton.setAttribute("aria-pressed", String(allSelected));
      }
      const runButton = root.querySelector("[data-tau-run]");
      if (runButton) {
        const capability = T.store.catalog?.capabilities?.runners?.find(entry => entry.id === "tau");
        runButton.textContent = selected.size ? window.PortalI18n.text`运行 ${selected.size}` : window.PortalI18n.t("运行");
        runButton.disabled = !T.store.canOperate() || !selected.size || capability?.available === false
          || admission?.configuration?.admission_state !== "open";
      }
    };

    const syncAdmission = () => {
      const input = root.querySelector("[data-tau-admission-open]");
      const status = root.querySelector("[data-tau-admission-status]");
      if (!input || !status) return;
      const config = admission?.configuration;
      input.checked = config?.admission_state === "open";
      input.disabled = admissionBusy || !admission?.can_manage
        || (!config?.target_available && !config?.admission_paused);
      status.textContent = admissionError || (!config ? "正在读取 TAU 准入状态…"
        : config.admission_state === "paused" ? `已暂停接收新测试；已开始的继续运行。${config.reason ? `原因：${config.reason}` : ""}`
          : config.admission_state === "open" ? "正在接收新 TAU 测试。" : "TAU 尚未配置可用的 Lineage。");
      syncSelection();
    };

    const readAdmission = async () => {
      if (admissionBusy) return;
      try {
        admission = await T.api("/test/api/tau/admission");
        admissionError = "";
      } catch (error) { admission = null; admissionError = error.message; }
      syncAdmission();
    };

    const render = () => {
      const capability = T.store.catalog?.capabilities?.runners?.find(entry => entry.id === "tau");
      const canOperate = T.store.canOperate();
      const residentMap = assignments();
      const groups = [...grouped()];
      const modules = groups.map(([moduleId, cases]) => {
        const resident = residentMap.get(moduleId) || {};
        const conversation = conversations.get(moduleId) || resident;
        const tasks = cases.map(item => {
          const description = item.description && item.description !== item.title ? item.description : "";
          return window.PortalI18n.html`<label data-selected="${selected.has(item.id)}" ${description ? `title="${T.escapeHtml(description)}"` : ""}><input type="checkbox" data-tau-case="${T.escapeHtml(item.id)}" ${selected.has(item.id) ? "checked" : ""}><span><strong>${T.escapeHtml(item.title || item.description)}</strong><small>${T.escapeHtml(caseMeta(item))}</small></span></label>`;
        }).join("");
        const residentId = resident.resident_id || "首次运行时创建";
        const lineageId = resident.lineage_id || "随模块保留";
        const status = identityStatus(resident);
        const lineageUrl = `/lineage?lineage=${encodeURIComponent(resident.lineage_id || "")}`;
        const residentUrl = `${lineageUrl}&resident=${encodeURIComponent(resident.resident_id || "")}`;
        const residentIdentity = resident.resident_id ? window.PortalI18n.html`<a href="${residentUrl}" title="打开 Resident"><code>${T.escapeHtml(residentId)}</code></a>` : window.PortalI18n.html`<code>${T.escapeHtml(residentId)}</code>`;
        const lineageIdentity = resident.lineage_id ? window.PortalI18n.html`<a href="${resident.resident_id ? residentUrl : lineageUrl}" title="打开 Lineage"><code>${T.escapeHtml(lineageId)}</code></a>` : window.PortalI18n.html`<code>${T.escapeHtml(lineageId)}</code>`;
        return window.PortalI18n.html`<article class="testTauModule" data-selected="${cases.some(item => selected.has(item.id))}"><header><div><strong title="${T.escapeHtml(T.store.moduleLabel(cases[0]))}">${T.escapeHtml(T.store.moduleLabel(cases[0]))}</strong><span data-tau-module-count>${cases.length} 个任务</span></div><button class="testTauModulePick" data-tau-module="${T.escapeHtml(moduleId)}" type="button" aria-pressed="false">选择本组</button></header><div class="testTauResident"><details class="testTauIdentity" data-tau-identity="${T.escapeHtml(moduleId)}" ${expandedIdentity.has(moduleId) ? "open" : ""}><summary title="${T.escapeHtml(status)}"><span class="testTauIdentityLabel">身份详情</span><span class="testTauIdentityState">${T.escapeHtml(status)}</span></summary><dl><dt>Resident</dt><dd>${residentIdentity}</dd><dt>Lineage</dt><dd>${lineageIdentity}</dd><dt>创建账号</dt><dd>${T.escapeHtml(resident.backend_route_id || window.PortalI18n.t("默认账号"))}</dd></dl></details><div class="testTauResidentActions"><button class="testTauResidentAction" data-tau-rotate="${T.escapeHtml(moduleId)}" type="button" ${canOperate ? "" : "disabled"} title="为此模块更换 Resident，保留 Lineage">更换</button>${conversation?.resident_id ? window.PortalI18n.html`<button class="testTauResidentAction" data-tau-chat="${T.escapeHtml(moduleId)}" type="button">对话</button>` : ""}</div></div><div class="testTauCases">${tasks}</div></article>`;
      }).join("");
      root.classList.add("testTauTaskView");
      root.innerHTML = window.PortalI18n.html`<style data-tau-panel-style>${panelStyles}</style>${T.panelHead(window.PortalI18n.t("TAU · Agent 真实使用"), window.PortalI18n.t("选择任务；模块并发，模块内顺序执行。"))}<div class="testPanelBody testAgentBody"><div class="testTauAdmission"><span><strong>接收新 TAU 测试</strong><small data-tau-admission-status role="status"></small></span><div class="testTauAdmissionActions"><button data-tau-admission-refresh type="button" title="刷新准入状态">刷新</button><input data-tau-admission-open type="checkbox" role="switch" aria-label="接收新 TAU 测试" disabled /></div></div><div class="testTauToolbar"><div class="testTauSelection"><strong data-tau-selection>已选 ${selected.size} / ${tauCases().length}</strong><small>${groups.length} 个模块 · 受控账号 · 每个模块永久复用 Resident</small></div><div class="testPanelActions"><button class="portalButton is-compact" data-tau-all type="button">全选</button><button class="portalButton is-primary is-compact" data-tau-run type="button">运行</button></div></div>${canOperate ? "" : window.PortalI18n.t('<div class="testEmpty" role="note">当前账号可以查看配置与记录；执行测试和更换 Resident 需要 Test Lab 操作权限。</div>')}${capability?.available === false ? window.PortalI18n.html`<div class="testEmpty">${T.escapeHtml(capability.reason)}</div>` : ""}<div class="testTauModules">${modules || window.PortalI18n.t('<div class="testEmpty">暂无 TAU 用例。</div>')}</div></div>`;
      root.querySelector("[data-tau-admission-refresh]")?.addEventListener("click", readAdmission);
      root.querySelector("[data-tau-admission-open]")?.addEventListener("change", async event => {
        const paused = !event.target.checked;
        admissionBusy = true;
        syncAdmission();
        try {
          admission = await T.api("/test/api/tau/admission", {method: "POST", body: JSON.stringify({paused, expected_revision: admission.configuration.revision})});
          admissionError = "";
        } catch (error) {
          admissionBusy = false;
          await readAdmission();
          admissionError = error.message;
        }
        admissionBusy = false;
        syncAdmission();
      });
      root.querySelectorAll("[data-tau-case]").forEach(input => input.addEventListener("change", () => {
        input.checked ? selected.add(input.dataset.tauCase) : selected.delete(input.dataset.tauCase);
        syncSelection();
      }));
      root.querySelectorAll("[data-tau-module]").forEach(button => button.addEventListener("click", () => {
        const cases = grouped().get(button.dataset.tauModule) || [];
        const clear = cases.length > 0 && cases.every(item => selected.has(item.id));
        cases.forEach(item => clear ? selected.delete(item.id) : selected.add(item.id));
        syncSelection();
      }));
      root.querySelector("[data-tau-all]")?.addEventListener("click", () => {
        const cases = tauCases();
        const clear = cases.length > 0 && cases.every(item => selected.has(item.id));
        selected = clear ? new Set() : new Set(cases.map(item => item.id));
        syncSelection();
      });
      root.querySelector("[data-tau-run]")?.addEventListener("click", () => T.prepareRun([...selected], { runners: ["tau"], profile: "live.controlled" }));
      root.querySelectorAll("[data-tau-identity]").forEach(details => details.addEventListener("toggle", () => {
        details.open ? expandedIdentity.add(details.dataset.tauIdentity) : expandedIdentity.delete(details.dataset.tauIdentity);
      }));
      root.querySelectorAll("[data-tau-rotate]").forEach(button => button.addEventListener("click", async () => {
        if (!window.confirm(window.PortalI18n.t("更换后，该模块后续测试将使用一个新的 Resident；模块 Lineage 会继续保留。"))) return;
        try {
          const next = await T.api("/test/api/tau/resident/rotate", { method: "POST", body: JSON.stringify({ module_id: button.dataset.tauRotate }) });
          const values = T.store.catalog.tau_residents || [];
          T.store.catalog.tau_residents = [...values.filter(item => item.module_id !== next.module_id), next];
          conversations.delete(next.module_id);
          render();
        } catch (error) {
          T.notify(error.message, "error");
        }
      }));
      root.querySelectorAll("[data-tau-chat]").forEach(button => button.addEventListener("click", () => {
        const conversation = conversations.get(button.dataset.tauChat) || assignments().get(button.dataset.tauChat);
        T.workbench()?.openResource({ type: "chat", resource: { kind: "chat", resident_id: conversation.resident_id, title: window.PortalI18n.text`TAU · ${button.dataset.tauChat}` }, mode: "side" }).catch(error => T.notify(error.message, "error"));
      }));
      syncAdmission();
    };

    const unsubscribeRun = T.store.subscribe((event, detail) => {
      if (event !== "run") return;
      (detail.results || []).forEach(result => {
        if (result.details?.conversation) conversations.set(result.module_id, result.details.conversation);
      });
      render();
    });
    const controller = T.panelController(root, render, {
      setCase(caseId) {
        selected = new Set([caseId]);
        render();
      },
    });
    void readAdmission();
    const destroy = controller.destroy;
    controller.destroy = () => {
      destroy();
      unsubscribeRun();
    };
    return controller;
  }

  T.registerPanel({ type: "tau", title: window.PortalI18n.t("TAU · Agent 真实使用"), icon: "chat", component: "PortalTest.TauPanel", create });
})();
