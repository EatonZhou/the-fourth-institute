(() => {
  "use strict";

  // The Test page's overview (decision_f2a6a5dd4646): the catalog's figures,
  // the coverage map of every module, recent runs, the gaps to look at, the
  // ready-made selections and TAU at a glance.
  const T = window.PortalTest;
  const t = value => window.PortalI18n.t(value);
  const esc = T.escapeHtml;

  // Ready-made selections; each is a catalog filter, so "查看用例" and "生成运行计划" use the same cases.
  const TEMPLATES = Object.freeze([
    { id: "critical", name: t("关键路径"), purpose: t("P0 用例：阻断核心能力的路径"), filters: { importance: "P0" } },
    { id: "design", name: t("当前设计点"), purpose: t("直接约束架构或关键产品决策的用例"), filters: { design: "true" } },
    { id: "test-workbench", name: t("测试框架与 Test 页"), purpose: t("测试框架和这个页面自己的用例"), filters: { module: ["operations.test-framework", "operations.test-workbench"] } },
  ]);

  function templateFigures(overview, template) {
    const modules = overview.modules || [];
    if (template.filters.importance) {
      const value = template.filters.importance;
      return { count: overview.summary?.by_importance?.[value] || 0, modules: modules.filter(item => item.by_importance?.[value]).length };
    }
    if (template.filters.design) return { count: overview.summary?.design_points || 0, modules: modules.filter(item => item.design_points).length };
    const chosen = modules.filter(item => template.filters.module.includes(item.id));
    return { count: chosen.reduce((sum, item) => sum + item.total, 0), modules: chosen.filter(item => item.total).length };
  }

  // The newest run that touched each module.
  function latestByModule(runs) {
    const latest = new Map();
    for (const run of runs) for (const moduleId of run.module_ids || []) if (!latest.has(moduleId)) latest.set(moduleId, run);
    return latest;
  }

  function gaps(stats) {
    if (!stats.total) return ["empty"];
    return [
      !(stats.by_importance?.P0 || stats.by_importance?.P1) ? "important" : "",
      !stats.design_points ? "design" : "",
    ].filter(Boolean);
  }

  function create({ root }) {
    let admission = null;
    let disposed = false;
    let busyTemplate = "";

    function heroHtml(overview) {
      const summary = overview.summary || {};
      const modules = overview.modules || [];
      const runs = overview.recent_runs || [];
      const passed = runs.filter(run => run.status === "passed").length;
      const problems = runs.filter(run => T.problemStatuses.has(run.status)).length;
      const active = runs.filter(run => T.activeStatuses.has(run.status)).length;
      const tau = T.runner("tau");
      const pytest = T.runner("pytest");
      const runnerChip = (item, label) => item ? T.chip(item.available === false ? window.PortalI18n.text`${label} 不可用` : label, item.available === false ? "is-missing" : "is-runner", item.reason ? `title="${esc(item.reason)}"` : "") : "";
      const admissionState = admission?.configuration?.admission_state;
      const admissionChip = admissionState === "paused" ? T.chip(t("TAU 已暂停接收"), "is-warning") : "";
      const errors = (overview.catalog_errors || []).length;
      return window.PortalI18n.html`<header class="testHero">
        <div class="testHeroIcon" data-portal-icon="shield-check" aria-hidden="true"></div>
        <div class="testHeroMain">
          <div class="testHeroTitle"><h1>Portal 测试</h1><div class="testHeroMeta">${runnerChip(pytest, "pytest")}${runnerChip(tau, "TAU")}${admissionChip}${errors ? T.chip(window.PortalI18n.text`${errors} 个目录错误`, "is-danger") : ""}</div></div>
          <p class="testHeroDescription">用例的标题、说明与分类写在测试源码中，目录随代码更新。在这里查看各模块的覆盖、运行用例，并阅读每次运行的证据。</p>
        </div>
        <div class="testHeroActions"><a class="portalButton is-compact is-primary" href="${esc(T.catalogHref())}" data-portal-icon="list"><span>浏览用例</span></a><button class="portalButton is-compact" type="button" data-overview-refresh data-portal-icon="refresh"><span>刷新</span></button></div>
        <div class="testStats">
          <span class="testStat"><strong>${summary.total ?? 0}</strong><span>用例</span></span>
          <span class="testStat"><strong>${modules.filter(item => item.total).length}</strong><span>${esc(window.PortalI18n.text`个模块（共 ${modules.length}）`)}</span></span>
          <span class="testStat"><strong>${summary.by_importance?.P0 ?? 0} / ${summary.by_importance?.P1 ?? 0}</strong><span>P0 / P1</span></span>
          <span class="testStat"><strong>${summary.design_points ?? 0}</strong><span>设计点</span></span>
          <span class="testStat"><strong>${modules.reduce((sum, item) => sum + item.tau, 0)}</strong><span>TAU 任务</span></span>
          <span class="testStat${summary.without_implementation ? " is-warning" : ""}"><strong>${summary.without_implementation ?? 0}</strong><span>缺实现</span></span>
          <span class="testStat"><strong>${runs.length}</strong><span>${esc(active ? window.PortalI18n.text`次运行 · ${active} 个进行中` : window.PortalI18n.text`次运行 · ${passed} 通过 · ${problems} 有问题`)}</span></span>
        </div>
      </header>`;
    }

    function coverageHtml(overview) {
      const stats = new Map((overview.modules || []).map(item => [item.id, item]));
      const largest = Math.max(1, ...(overview.modules || []).map(item => item.total));
      const latest = latestByModule(overview.recent_runs || []);
      const groups = T.moduleGroups().map(group => {
        const tiles = (group.modules || []).map(module => {
          const item = stats.get(module.id) || { total: 0, by_importance: {}, design_points: 0, tau: 0 };
          const found = gaps(item);
          const run = latest.get(module.id);
          const shares = ["P0", "P1", "P2", "P3"].map(value => item.total ? `<i data-importance="${value}" style="flex-grow:${item.by_importance?.[value] || 0}"></i>` : "").join("");
          const mass = item.total ? Math.log1p(item.total) / Math.log1p(largest) : 0;
          const notes = [
            found.includes("empty") ? t("没有用例") : "",
            found.includes("important") ? t("缺 P0/P1") : "",
            found.includes("design") ? t("缺设计点") : "",
          ].filter(Boolean);
          const recent = run ? window.PortalI18n.text`最近运行：${T.statusLabel(run.status)} · ${T.relativeTime(run.created_at)}` : "";
          return window.PortalI18n.html`<a class="testModuleTile" href="${esc(T.catalogHref({ module: module.id }))}" data-module-tile="${esc(module.id)}" data-gap="${esc(found[0] || "")}" style="--mass:${mass.toFixed(3)}" title="${esc([module.label, recent].filter(Boolean).join("\n"))}">
            <span class="testModuleTileHead"><span class="testModuleTileLabel">${esc(module.label)}</span>${run ? `<span class="testModuleTileRun" data-status="${esc(run.status)}" aria-label="${esc(recent)}"></span>` : ""}</span>
            <strong class="testModuleTileCount">${item.total}</strong>
            <span class="testModuleBar" aria-hidden="true">${shares}</span>
            <span class="testModuleTileMeta">${esc(notes.length ? notes.join(" · ") : window.PortalI18n.text`设计点 ${item.design_points} · TAU ${item.tau}`)}</span>
          </a>`;
        }).join("");
        const total = (group.modules || []).reduce((sum, module) => sum + (stats.get(module.id)?.total || 0), 0);
        return window.PortalI18n.html`<div class="testCoverageGroup"><h3><a href="${esc(T.catalogHref({ group: group.id }))}">${esc(group.label)}</a><span>${total}</span></h3><div class="testCoverageTiles">${tiles}</div></div>`;
      }).join("");
      return window.PortalI18n.html`<section class="testSection" data-test-section="coverage">
        <div class="testSectionHead"><h2>覆盖地图</h2><span class="testSectionNote">每格一个功能模块：数字是用例数，色条是 P0–P3 的构成，底色越深用例越多；虚线格还没有用例，圆点是最近一次运行的结果。</span></div>
        <div class="testCoverageGroups">${groups}</div>
      </section>`;
    }

    function runsHtml(overview) {
      const rows = (overview.recent_runs || []).slice(0, 8).map(run => T.runRowHtml(run)).join("");
      return window.PortalI18n.html`<section class="testSection" data-test-section="runs">
        <div class="testSectionHead"><h2>最近运行</h2><a class="testSectionLink" href="${esc(T.pageHref("runs"))}">全部 ${(overview.recent_runs || []).length} 次 →</a></div>
        ${rows ? `<div class="testRunRows">${rows}</div>` : window.PortalI18n.html`<p class="testEmpty">还没有运行记录。从用例目录选择用例后生成运行计划。</p>`}
      </section>`;
    }

    function attentionHtml(overview) {
      const stats = overview.modules || [];
      const label = moduleId => T.moduleLabel(moduleId);
      const moduleLinks = items => items.map(item => `<a class="testChip is-link" href="${esc(T.catalogHref({ module: item.id }))}">${esc(label(item.id))}</a>`).join("");
      const groups = [
        { key: "empty", title: t("没有用例的模块"), hint: t("先为这些模块建立最小可执行覆盖。"), items: stats.filter(item => !item.total) },
        { key: "important", title: t("缺少 P0/P1 的模块"), hint: t("确认关键风险是否已有高重要级用例。"), items: stats.filter(item => item.total && !(item.by_importance?.P0 || item.by_importance?.P1)) },
        { key: "design", title: t("缺少设计点的模块"), hint: t("补充设计依据，或把现有用例标为设计点。"), items: stats.filter(item => item.total && !item.design_points) },
      ].filter(group => group.items.length);
      const errors = overview.catalog_errors || [];
      const missing = overview.summary?.without_implementation || 0;
      const blocks = [
        errors.length ? window.PortalI18n.html`<div class="testAttention" data-attention="errors" data-tone="danger"><strong>${errors.length} 个用例声明有误，暂不在目录中</strong><ul>${errors.map(item => `<li><code>${esc(item.resource_id)}</code> ${esc(item.error)}</li>`).join("")}</ul></div>` : "",
        missing ? window.PortalI18n.html`<div class="testAttention" data-attention="implementation" data-tone="warning"><strong>${missing} 条用例还没有实现</strong><p>它们登记了目标，但没有可执行的 pytest 或 TAU 实现。</p><a class="testSectionLink" href="${esc(T.catalogHref({ runner: "none" }))}">查看这些用例 →</a></div>` : "",
        ...groups.map(group => window.PortalI18n.html`<div class="testAttention" data-attention="${esc(group.key)}"><strong>${esc(group.title)} · ${group.items.length}</strong><p>${esc(group.hint)}</p><div class="testChipRow">${moduleLinks(group.items)}</div></div>`),
      ].filter(Boolean);
      return window.PortalI18n.html`<section class="testSection" data-test-section="attention">
        <div class="testSectionHead"><h2>待关注</h2></div>
        ${blocks.length ? blocks.join("") : window.PortalI18n.html`<p class="testEmpty">目录没有需要处理的缺口。</p>`}
      </section>`;
    }

    function templatesHtml(overview) {
      const cards = TEMPLATES.map(template => {
        const figures = templateFigures(overview, template);
        const busy = busyTemplate === template.id;
        return window.PortalI18n.html`<article class="testTemplate" data-template="${esc(template.id)}">
          <div><strong>${esc(template.name)}</strong><p>${esc(template.purpose)}</p></div>
          <div class="testTemplateFigures"><strong>${figures.count}</strong><span>${esc(window.PortalI18n.text`条用例 · ${figures.modules} 个模块`)}</span></div>
          <div class="testTemplateActions"><a class="portalButton is-compact" href="${esc(T.catalogHref(template.filters))}">查看用例</a><button class="portalButton is-compact" type="button" data-template-run="${esc(template.id)}" ${!figures.count || busy ? "disabled" : ""}>${esc(busy ? t("正在选择…") : t("生成运行计划"))}</button></div>
        </article>`;
      }).join("");
      return window.PortalI18n.html`<section class="testSection" data-test-section="templates">
        <div class="testSectionHead"><h2>快捷组合</h2><span class="testSectionNote">按规则即时选出一批用例；运行前仍要在计划页确认。</span></div>
        <div class="testTemplates">${cards}</div>
      </section>`;
    }

    function tauHtml(overview) {
      const modules = (overview.modules || []).filter(item => item.tau);
      const tasks = modules.reduce((sum, item) => sum + item.tau, 0);
      const runs = (overview.recent_runs || []).filter(T.isTauRun);
      const passed = runs.filter(run => run.status === "passed").length;
      const problems = runs.filter(run => T.problemStatuses.has(run.status)).length;
      const config = admission?.configuration;
      const state = !admission ? t("正在读取准入状态…") : config?.admission_state === "open" ? t("正在接收新的 TAU 测试") : config?.admission_state === "paused" ? t("已暂停接收新的 TAU 测试") : t("TAU 尚未配置可用的 Lineage");
      return window.PortalI18n.html`<section class="testSection testTauSummary" data-test-section="tau">
        <div class="testSectionHead"><h2>TAU · Agent 真实使用</h2><a class="testSectionLink" href="${esc(T.pageHref("tau"))}">打开 TAU →</a></div>
        <p class="testTauSummaryLead">每个功能模块有一个负责的 Resident，它像真实用户一样在 Portal 上完成任务，再写下体验、问题和证据。</p>
        <div class="testTauSummaryFigures">
          <span><strong>${tasks}</strong>个任务</span><span><strong>${modules.length}</strong>个模块</span>
          <span><strong>${runs.length}</strong>${esc(window.PortalI18n.text`次最近运行 · ${passed} 通过 · ${problems} 有问题`)}</span>
        </div>
        <p class="testTauAdmissionLine" data-admission-state="${esc(config?.admission_state || "")}">${esc(state)}</p>
      </section>`;
    }

    function render() {
      const overview = T.overview();
      if (!overview) return;
      root.innerHTML = window.PortalI18n.html`<article class="testOverview">
        ${heroHtml(overview)}
        ${coverageHtml(overview)}
        <div class="testColumns">${runsHtml(overview)}${attentionHtml(overview)}</div>
        <div class="testColumns">${templatesHtml(overview)}${tauHtml(overview)}</div>
      </article>`;
      window.PortalUIPrimitives?.mountIcons?.(root);
      T.fillNames(root);
      root.querySelector("[data-overview-refresh]")?.addEventListener("click", () => void refresh());
      root.querySelectorAll("[data-template-run]").forEach(button => button.addEventListener("click", () => void runTemplate(button.dataset.templateRun)));
    }

    async function runTemplate(id) {
      const template = TEMPLATES.find(item => item.id === id);
      if (!template || busyTemplate) return;
      busyTemplate = id;
      render();
      try {
        const query = T.filterQuery(template.filters);
        query.set("current", "true");
        const result = await T.api(`/test/api/catalog/query?${query}`);
        T.prepareRun(result.case_ids || [], {}, window.PortalI18n.text`快捷组合「${template.name}」`);
      } catch (error) {
        T.notify(window.PortalI18n.text`无法选出用例：${error.message || error}`, "error");
      } finally {
        busyTemplate = "";
        if (!disposed && root.isConnected) render();
      }
    }

    async function readAdmission() {
      try { admission = await T.api("/test/api/tau/admission"); } catch (_error) { admission = { configuration: null }; }
      if (!disposed && root.isConnected) render();
    }

    async function refresh() {
      const button = root.querySelector("[data-overview-refresh]");
      if (button) button.disabled = true;
      try {
        await T.loadOverview({ fresh: true });
        void readAdmission();
      } catch (error) {
        T.notify(window.PortalI18n.text`刷新失败：${error.message || error}`, "error");
      }
      if (!disposed) render();
    }

    async function load() {
      const cached = T.overview();
      if (cached) render();
      else root.innerHTML = T.stateHtml("loading", t("正在读取测试目录…"));
      void readAdmission();
      // A cached overview shows at once; the fresh one replaces it when it arrives.
      const overview = await T.loadOverview({ fresh: Boolean(cached) }).catch(error => {
        if (!cached) throw error;
        return cached;
      });
      if (!disposed) render();
      return overview;
    }

    const unsubscribe = T.subscribe(event => { if (!disposed && root.isConnected && event === "runs") render(); });
    return {
      load,
      destroy() { disposed = true; unsubscribe(); },
    };
  }

  window.PortalTestOverview = Object.freeze({ create, TEMPLATES });
})();
