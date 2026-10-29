(() => {
  "use strict";

  // The plan page (decision_f2a6a5dd4646): the cases chosen elsewhere become
  // a run plan.  The page previews the plan as soon as it opens; only
  // "开始运行" starts anything, and the run page takes over from there.
  const T = window.PortalTest;
  const t = value => window.PortalI18n.t(value);
  const esc = T.escapeHtml;
  const LIMIT = 40;

  function create({ root }) {
    let draft = T.draft();
    let plan = null;
    let failure = "";
    let pending = "";
    let revision = 0;
    let runnerChoice = "";
    let profileChoice = "";
    let showAll = false;
    let disposed = false;

    const request = () => ({
      select: { case_ids: draft.caseIds },
      implementation: {
        ...draft.implementation,
        ...(runnerChoice ? { runners: [runnerChoice], implementation_ids: undefined } : {}),
        ...(profileChoice ? { profile: profileChoice } : {}),
      },
    });

    async function preview() {
      const current = ++revision;
      pending = "preview";
      failure = "";
      render();
      try {
        const body = request();
        if (!body.implementation.implementation_ids) delete body.implementation.implementation_ids;
        const result = await T.api("/test/api/preview", { method: "POST", body: JSON.stringify(body) });
        if (current !== revision || disposed) return;
        plan = result;
      } catch (error) {
        if (current !== revision || disposed) return;
        plan = null;
        failure = error.message || String(error);
      }
      pending = "";
      render();
    }

    async function start() {
      if (pending || !plan?.executable) return;
      pending = "start";
      render();
      try {
        const run = await T.api("/test/api/run", { method: "POST", body: JSON.stringify(plan.request) });
        T.rememberRun(run);
        T.setDraft([]);
        T.selection.clear();
        T.navigate(T.runHref(run.run_id), { replace: true });
      } catch (error) {
        pending = "";
        failure = window.PortalI18n.text`启动失败：${error.message || error}`;
        if (!disposed) render();
      }
    }

    function optionsHtml() {
      const runners = (T.overview()?.capabilities?.runners || []);
      const profiles = (T.overview()?.capabilities?.profiles || []);
      const option = (value, label, current, disabled = false) => `<option value="${esc(value)}"${value === current ? " selected" : ""}${disabled ? " disabled" : ""}>${esc(label)}</option>`;
      return window.PortalI18n.html`<div class="testPlanOptions">
        <label><span>执行方式</span><select class="portalBoardSelect" data-plan-runner>${option("", t("按用例的实现"), runnerChoice)}${runners.map(item => option(item.id, item.available === false ? window.PortalI18n.text`${item.label}（不可用）` : item.label, runnerChoice, item.available === false)).join("")}</select></label>
        <label><span>运行环境</span><select class="portalBoardSelect" data-plan-profile>${option("", t("按实现的默认环境"), profileChoice)}${profiles.map(item => option(item.id, `${item.label} · ${T.riskLabel(item.risk)}`, profileChoice)).join("")}</select></label>
        ${(draft.implementation?.implementation_ids || []).length && !runnerChoice ? window.PortalI18n.html`<p class="testMuted">只运行选定的实现：${esc(draft.implementation.implementation_ids.join("、"))}</p>` : ""}
      </div>`;
    }

    function selectedHtml() {
      const items = plan?.selected || [];
      const shown = showAll ? items : items.slice(0, LIMIT);
      const rows = shown.map(item => {
        const implementation = item.implementation || {};
        const risk = item.risk || implementation.risk || "unspecified";
        return `<li data-plan-implementation><a href="${esc(T.caseHref(item.case_id))}">${esc(item.case_title || item.description || item.case_id)}</a>
          <span class="testChipRow">${T.chip(item.runner?.label || implementation.runner || "", "is-runner")}${item.profile ? T.chip(item.profile) : ""}${T.chip(window.PortalI18n.text`执行风险 · ${T.riskLabel(risk)}`, T.riskTone(risk))}${item.profile_risk ? T.chip(window.PortalI18n.text`环境风险 · ${T.riskLabel(item.profile_risk)}`, T.riskTone(item.profile_risk)) : ""}</span>
          <code>${esc(implementation.entrypoint || "")}</code></li>`;
      }).join("");
      const more = items.length > LIMIT ? `<button type="button" class="portalButton is-compact is-quiet" data-plan-all>${esc(showAll ? t("收起") : window.PortalI18n.text`显示全部 ${items.length} 个`)}</button>` : "";
      return window.PortalI18n.html`<section class="testSection" data-test-section="selected">
        <div class="testSectionHead"><h2>将要运行</h2><span class="testSectionNote">${esc(window.PortalI18n.text`${items.length} 个实现`)}</span></div>
        ${rows ? `<ol class="testPlanList">${rows}</ol>${more}` : window.PortalI18n.html`<p class="testEmpty">选择中没有可以运行的实现。</p>`}
      </section>`;
    }

    function excludedHtml() {
      const items = plan?.excluded || [];
      if (!items.length) return "";
      return window.PortalI18n.html`<section class="testSection" data-test-section="excluded">
        <div class="testSectionHead"><h2>不会运行</h2><span class="testSectionNote">${esc(window.PortalI18n.text`${items.length} 条`)}</span></div>
        <ul class="testPlanList">${items.map(item => `<li data-plan-excluded><a href="${esc(T.caseHref(item.case_id))}">${esc(item.case_title || item.description || item.case_id || t("已排除的用例"))}</a><span class="testMuted">${esc(item.reason || item.error || t("当前选择不包含它的实现。"))}</span></li>`).join("")}</ul>
      </section>`;
    }

    function render() {
      const groups = plan?.execution?.groups || [];
      const parallel = Math.min(groups.length, Number(plan?.execution?.max_parallel_modules || groups.length));
      const ready = Boolean(plan?.executable) && T.canOperate() && !pending;
      const policy = !plan ? "" : plan.campaign === "tau"
        ? window.PortalI18n.text`${groups.length} 个模块，最多 ${parallel} 个同时执行，模块内依次执行`
        : window.PortalI18n.text`${groups.length} 个分组`;
      root.innerHTML = window.PortalI18n.html`<article class="testPlanPage">
        ${T.crumbsHtml([{ label: "Test", href: T.href() }, { label: t("新运行") }])}
        <header class="testHero is-plan">
          <div class="testHeroIcon" data-portal-icon="play" aria-hidden="true"></div>
          <div class="testHeroMain">
            <div class="testHeroTitle"><h1>新运行</h1><div class="testHeroMeta">${plan ? T.chip(plan.executable ? t("可以运行") : t("不能运行"), plan.executable ? "is-ok" : "is-danger") : ""}${plan?.campaign === "tau" ? T.chip("TAU Campaign") : ""}</div></div>
            <p class="testHeroDescription">${esc(draft.source ? window.PortalI18n.text`来自${draft.source}。确认下面的计划后开始运行；运行页会跟踪进度并保留结果。` : t("确认下面的计划后开始运行；运行页会跟踪进度并保留结果。"))}</p>
          </div>
          <div class="testHeroActions">
            <button class="portalButton is-compact is-primary" type="button" data-plan-start data-portal-icon="play" ${ready ? "" : "disabled"}><span>${esc(pending === "start" ? t("正在启动…") : t("开始运行"))}</span></button>
            <a class="portalButton is-compact" href="${esc(T.catalogHref())}">返回用例目录</a>
          </div>
          <div class="testStats">
            <span class="testStat"><strong>${draft.caseIds.length}</strong><span>条用例</span></span>
            <span class="testStat"><strong>${plan ? plan.selected_implementation_count ?? (plan.selected || []).length : "…"}</strong><span>个实现</span></span>
            <span class="testStat"><strong>${plan ? (plan.excluded || []).length : "…"}</strong><span>条不会运行</span></span>
            ${policy ? `<span class="testStat"><span>${esc(policy)}</span></span>` : ""}
          </div>
        </header>
        ${T.canOperate() ? "" : window.PortalI18n.html`<p class="testNote" role="note">当前账号可以查看运行计划；开始运行需要 Test 的操作权限。</p>`}
        ${failure ? `<p class="testRunError" role="alert" data-plan-error>${esc(failure)} <button type="button" class="portalButton is-compact" data-plan-retry>${esc(t("重试"))}</button></p>` : ""}
        <section class="testSection" data-test-section="options"><div class="testSectionHead"><h2>运行方式</h2>${pending === "preview" ? `<span class="testSectionNote" role="status">${esc(t("正在生成计划…"))}</span>` : ""}</div>${optionsHtml()}</section>
        ${plan ? `${selectedHtml()}${excludedHtml()}` : ""}
      </article>`;
      window.PortalUIPrimitives?.mountIcons?.(root);
      root.querySelector("[data-plan-start]")?.addEventListener("click", () => void start());
      root.querySelector("[data-plan-retry]")?.addEventListener("click", () => void preview());
      root.querySelector("[data-plan-all]")?.addEventListener("click", () => { showAll = !showAll; render(); });
      root.querySelector("[data-plan-runner]")?.addEventListener("change", event => { runnerChoice = event.target.value; void preview(); });
      root.querySelector("[data-plan-profile]")?.addEventListener("change", event => { profileChoice = event.target.value; void preview(); });
    }

    async function load() {
      draft = T.draft();
      plan = null;
      failure = "";
      runnerChoice = "";
      profileChoice = "";
      await T.loadOverview();
      if (disposed) return null;
      if (!draft.caseIds.length) {
        root.innerHTML = window.PortalI18n.html`<article class="testPlanPage">${T.crumbsHtml([{ label: "Test", href: T.href() }, { label: t("新运行") }])}
          ${T.stateHtml("empty", t("还没有选择要运行的用例"), t("在用例目录勾选用例、在用例页点“运行”，或使用总览中的快捷组合。"), `<a class="portalButton is-compact is-primary" href="${esc(T.catalogHref())}">${esc(t("打开用例目录"))}</a>`)}</article>`;
        return null;
      }
      await preview();
      return plan;
    }

    return { load, plan: () => plan, destroy() { disposed = true; revision += 1; } };
  }

  window.PortalTestPlan = Object.freeze({ create });
})();
