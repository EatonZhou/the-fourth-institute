(() => {
  "use strict";

  // The TAU page (decision_f2a6a5dd4646, execution in decision_c953eab654cd):
  // every module's real-use tasks and the Resident that carries them out.
  // Different modules run at the same time; one module's tasks run in turn.
  const T = window.PortalTest;
  const t = value => window.PortalI18n.t(value);
  const esc = T.escapeHtml;

  function create({ root }) {
    let cases = [];
    let admission = null;
    let admissionError = "";
    let admissionBusy = false;
    let rotating = "";
    let disposed = false;
    const selected = new Set();

    const slots = () => new Map((T.overview()?.tau_residents || []).map(item => [item.module_id, item]));
    const grouped = () => {
      const result = new Map();
      for (const item of cases) {
        if (!result.has(item.module)) result.set(item.module, []);
        result.get(item.module).push(item);
      }
      return result;
    };
    // The newest retained run that included each case.
    const latest = () => {
      const found = new Map();
      for (const run of T.recentRuns().filter(T.isTauRun)) for (const caseId of T.runCaseIds(run)) if (!found.has(caseId)) found.set(caseId, run);
      return found;
    };
    const runnable = () => {
      const tau = T.runner("tau");
      return T.canOperate() && tau?.available !== false && admission?.configuration?.admission_state === "open";
    };

    function admissionHtml() {
      const config = admission?.configuration;
      const text = admissionError || (!admission ? t("正在读取 TAU 准入状态…")
        : config?.admission_state === "paused" ? window.PortalI18n.text`已暂停接收新的 TAU 测试；已经开始的继续运行。${config.reason ? window.PortalI18n.text`原因：${config.reason}` : ""}`
          : config?.admission_state === "open" ? t("正在接收新的 TAU 测试。") : t("TAU 尚未配置可用的 Lineage。"));
      const disabled = admissionBusy || !admission?.can_manage || (!config?.target_available && !config?.admission_paused);
      return window.PortalI18n.html`<div class="testTauAdmission" data-admission-state="${esc(config?.admission_state || "")}">
        <label class="testSwitch"><input type="checkbox" role="switch" data-tau-admission-open ${config?.admission_state === "open" ? "checked" : ""} ${disabled ? "disabled" : ""}><span>接收新测试</span></label>
        <span class="testMuted" data-tau-admission-status role="status">${esc(text)}</span>
        <button type="button" class="portalButton is-compact is-quiet" data-tau-admission-refresh data-portal-icon="refresh" aria-label="重新读取准入状态"></button>
      </div>`;
    }

    function moduleMeta(moduleId, items) {
      const count = items.filter(item => selected.has(item.id)).length;
      return [T.moduleInfo(moduleId)?.group?.label || "", window.PortalI18n.text`${items.length} 个任务`, count ? window.PortalI18n.text`已选 ${count}` : ""].filter(Boolean).join(" · ");
    }

    // A selection change updates the marks in place, so focus and scroll stay where the reader is.
    function sync() {
      root.querySelectorAll("[data-tau-case]").forEach(input => {
        input.checked = selected.has(input.dataset.tauCase);
        input.closest("li").dataset.selected = String(input.checked);
      });
      for (const [moduleId, items] of grouped()) {
        const card = root.querySelector(`[data-tau-module-card="${CSS.escape(moduleId)}"]`);
        if (!card) continue;
        const count = items.filter(item => selected.has(item.id)).length;
        card.dataset.selected = String(count > 0);
        card.querySelector("[data-tau-module-meta]").textContent = moduleMeta(moduleId, items);
        const toggle = card.querySelector("[data-tau-module]");
        toggle.setAttribute("aria-pressed", String(count === items.length));
        toggle.textContent = count === items.length ? t("取消本组") : t("选择本组");
      }
      root.querySelector("[data-tau-selection]").textContent = window.PortalI18n.text`已选 ${selected.size} / ${cases.length} 个任务`;
      root.querySelector("[data-tau-all]").textContent = selected.size === cases.length && cases.length ? t("清空") : t("全选");
      const run = root.querySelector("[data-tau-run]");
      run.disabled = !(runnable() && selected.size);
      run.querySelector("span").textContent = selected.size ? window.PortalI18n.text`运行 ${selected.size} 个任务` : t("运行");
    }

    function moduleHtml(moduleId, items, slot, found) {
      const info = T.moduleInfo(moduleId);
      const count = items.filter(item => selected.has(item.id)).length;
      const all = count === items.length;
      const lineageUrl = `/lineage?lineage=${encodeURIComponent(slot?.lineage_id || "")}`;
      const residentUrl = `${lineageUrl}&resident=${encodeURIComponent(slot?.resident_id || "")}`;
      const resident = slot?.resident_id
        ? window.PortalI18n.html`<a href="${esc(residentUrl)}" title="${esc(slot.resident_id)}">${T.personHtml(slot.resident_id)}</a><small>${esc([slot.backend_route_id, slot.updated_at ? window.PortalI18n.text`${T.relativeTime(slot.updated_at)}更换` : ""].filter(Boolean).join(" · "))}</small>`
        : window.PortalI18n.html`<span>首次运行时创建 Resident</span><small>模块的 Lineage 随模块保留</small>`;
      const tasks = items.map(item => {
        const run = found.get(item.id);
        const meta = [item.importance, ...(item.aspects || []).map(aspect => T.aspectLabels[aspect] || aspect)].join(" · ");
        return `<li data-selected="${selected.has(item.id)}"><label><input type="checkbox" data-tau-case="${esc(item.id)}" ${selected.has(item.id) ? "checked" : ""}><span><a href="${esc(T.caseHref(item.id))}">${esc(item.title || item.key)}</a><small>${esc(meta)}</small></span></label>${run ? `<a class="testTauLast" href="${esc(T.runHref(run.run_id))}" title="${esc(window.PortalI18n.text`最近一次 · ${T.relativeTime(run.created_at)}`)}">${T.statusHtml(run.status)}</a>` : ""}</li>`;
      }).join("");
      return window.PortalI18n.html`<article class="testTauModule" data-tau-module-card="${esc(moduleId)}" data-selected="${count > 0}">
        <header>
          <div><strong>${esc(info?.label || moduleId)}</strong><span data-tau-module-meta>${esc(moduleMeta(moduleId, items))}</span></div>
          <button type="button" class="testToggle" data-tau-module="${esc(moduleId)}" aria-pressed="${all}">${esc(all ? t("取消本组") : t("选择本组"))}</button>
        </header>
        <div class="testTauResident">
          <span class="testTauResidentIcon" data-portal-icon="bot" aria-hidden="true"></span>
          <div class="testTauResidentName">${resident}</div>
          <div class="testTauResidentActions">
            ${slot?.resident_id ? `<button type="button" class="portalButton is-compact is-quiet" data-tau-chat="${esc(moduleId)}" data-portal-icon="chat"><span>${esc(t("对话"))}</span></button>` : ""}
            ${T.canOperate() ? `<button type="button" class="portalButton is-compact is-quiet" data-tau-rotate="${esc(moduleId)}" ${rotating ? "disabled" : ""}>${esc(rotating === moduleId ? t("正在更换…") : t("更换"))}</button>` : ""}
          </div>
        </div>
        <ul class="testTauCases">${tasks}</ul>
      </article>`;
    }

    function render() {
      const groups = [...grouped()];
      const assigned = slots();
      const found = latest();
      const runs = T.recentRuns().filter(T.isTauRun);
      const tau = T.runner("tau");
      const passed = runs.filter(run => run.status === "passed").length;
      const problems = runs.filter(run => T.problemStatuses.has(run.status)).length;
      const ready = runnable();
      root.innerHTML = window.PortalI18n.html`<article class="testTauPage">
        ${T.crumbsHtml([{ label: "Test", href: T.href() }, { label: "TAU" }])}
        <header class="testHero is-tau">
          <div class="testHeroIcon" data-portal-icon="chat" aria-hidden="true"></div>
          <div class="testHeroMain">
            <div class="testHeroTitle"><h1>TAU · Agent 真实使用</h1><div class="testHeroMeta">${tau?.available === false ? T.chip(window.PortalI18n.text`不可用：${tau.reason || ""}`, "is-danger") : T.chip(t("执行方式可用"), "is-ok")}</div></div>
            <p class="testHeroDescription">每个功能模块有一个负责的 Resident：它在受测的 Portal 上像真实用户一样完成本模块的任务，写下体验、问题和证据。不同模块同时进行，同一模块的任务依次执行。</p>
            ${admissionHtml()}
          </div>
          <div class="testStats">
            <span class="testStat"><strong>${cases.length}</strong><span>个任务</span></span>
            <span class="testStat"><strong>${groups.length}</strong><span>个模块</span></span>
            <span class="testStat"><strong>${runs.length}</strong><span>${esc(window.PortalI18n.text`次最近运行 · ${passed} 通过 · ${problems} 有问题`)}</span></span>
          </div>
        </header>
        ${T.canOperate() ? "" : window.PortalI18n.html`<p class="testNote" role="note">当前账号可以查看任务和记录；运行测试和更换 Resident 需要 Test 的操作权限。</p>`}
        ${groups.length ? `<div class="testTauModules">${groups.map(([moduleId, items]) => moduleHtml(moduleId, items, assigned.get(moduleId), found)).join("")}</div>` : T.stateHtml("empty", t("还没有 TAU 任务"))}
        <div class="testSelectionBar" data-tau-bar>
          <span data-tau-selection>${esc(window.PortalI18n.text`已选 ${selected.size} / ${cases.length} 个任务`)}</span>
          <button type="button" class="portalButton is-compact is-quiet" data-tau-all>${esc(selected.size === cases.length && cases.length ? t("清空") : t("全选"))}</button>
          <button type="button" class="portalButton is-compact is-primary" data-tau-run data-portal-icon="play" ${ready && selected.size ? "" : "disabled"} title="${esc(ready ? "" : t("需要 TAU 可用、正在接收新测试，并有操作权限"))}"><span>${esc(selected.size ? window.PortalI18n.text`运行 ${selected.size} 个任务` : t("运行"))}</span></button>
        </div>
      </article>`;
      window.PortalUIPrimitives?.mountIcons?.(root);
      T.fillNames(root);
      bind();
    }

    function bind() {
      root.querySelectorAll("[data-tau-case]").forEach(input => input.addEventListener("change", () => {
        input.checked ? selected.add(input.dataset.tauCase) : selected.delete(input.dataset.tauCase);
        sync();
      }));
      root.querySelectorAll("[data-tau-module]").forEach(button => button.addEventListener("click", () => {
        const items = grouped().get(button.dataset.tauModule) || [];
        const clear = items.every(item => selected.has(item.id));
        items.forEach(item => clear ? selected.delete(item.id) : selected.add(item.id));
        sync();
      }));
      root.querySelector("[data-tau-all]")?.addEventListener("click", () => {
        const clear = cases.length > 0 && cases.every(item => selected.has(item.id));
        selected.clear();
        if (!clear) cases.forEach(item => selected.add(item.id));
        sync();
      });
      root.querySelector("[data-tau-run]")?.addEventListener("click", () => T.prepareRun([...selected], { runners: ["tau"], profile: "live.controlled" }, t("TAU 页面的选择")));
      root.querySelector("[data-tau-admission-refresh]")?.addEventListener("click", () => void readAdmission());
      root.querySelector("[data-tau-admission-open]")?.addEventListener("change", async event => {
        const paused = !event.target.checked;
        admissionBusy = true;
        render();
        try {
          admission = await T.api("/test/api/tau/admission", { method: "POST", body: JSON.stringify({ paused, expected_revision: admission.configuration.revision }) });
          admissionError = "";
        } catch (error) {
          admissionBusy = false;
          await readAdmission();
          admissionError = error.message || String(error);
        }
        admissionBusy = false;
        if (!disposed) render();
      });
      root.querySelectorAll("[data-tau-chat]").forEach(button => button.addEventListener("click", () => {
        const slot = slots().get(button.dataset.tauChat);
        if (slot?.resident_id) T.openChat({ resident_id: slot.resident_id, lineage_id: slot.lineage_id || "", title: window.PortalI18n.text`TAU · ${T.moduleLabel(button.dataset.tauChat)}` });
      }));
      root.querySelectorAll("[data-tau-rotate]").forEach(button => button.addEventListener("click", () => void rotate(button.dataset.tauRotate)));
    }

    async function rotate(moduleId) {
      const accepted = await window.PortalUI.confirm({
        title: window.PortalI18n.text`更换 ${T.moduleLabel(moduleId)} 的 Resident？`,
        message: t("之后这个模块的测试由一个新的 Resident 执行；模块的 Lineage 继续保留。"),
        confirmLabel: t("更换"),
      });
      if (!accepted) return;
      rotating = moduleId;
      render();
      try {
        const next = await T.api("/test/api/tau/resident/rotate", { method: "POST", body: JSON.stringify({ module_id: moduleId }) });
        const overview = T.overview();
        if (overview) overview.tau_residents = [...(overview.tau_residents || []).filter(item => item.module_id !== next.module_id), next];
      } catch (error) {
        T.notify(window.PortalI18n.text`更换失败：${error.message || error}`, "error");
      }
      rotating = "";
      if (!disposed) render();
    }

    async function readAdmission() {
      if (admissionBusy) return;
      try {
        admission = await T.api("/test/api/tau/admission");
        admissionError = "";
      } catch (error) {
        admission = null;
        admissionError = error.message || String(error);
      }
      if (!disposed && root.isConnected && cases) render();
    }

    async function load() {
      root.innerHTML = T.stateHtml("loading", t("正在读取 TAU 任务…"));
      void readAdmission();
      const [, page] = await Promise.all([T.loadOverview(), T.api("/test/api/catalog?projection=page&runner=tau&limit=500")]);
      if (disposed) return null;
      cases = page.cases || [];
      render();
      return cases;
    }

    const unsubscribe = T.subscribe(event => { if (!disposed && root.isConnected && cases.length && event === "runs") render(); });
    return {
      load,
      select(caseIds) { caseIds.forEach(id => selected.add(id)); render(); },
      destroy() { disposed = true; unsubscribe(); },
    };
  }

  window.PortalTestTau = Object.freeze({ create });
})();
