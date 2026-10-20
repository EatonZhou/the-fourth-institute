(() => {
  "use strict";
  const T = window.PortalTest;
  const distributionPreviewLimit = 12;
  function create({ root }) {
    let showAllDistribution = false;
    const render = () => {
      const groups = T.store.catalog?.filters?.modules || [];
      const rows = groups.flatMap(group => (group.modules || []).map(module => {
        const cases = T.store.cases.filter(item => item.module === module.id);
        return { group, module, cases, important: cases.filter(item => ["P0", "P1"].includes(item.importance)).length, design: cases.filter(item => item.is_design_point).length, agent: cases.filter(item => item.lifecycle !== "retired" && T.hasRunner(item, "tau")).length };
      }));
      if (!T.store.catalog || T.store.cases.length === 0) {
        const message = T.store.catalog ? window.PortalI18n.t("当前目录没有测试用例，暂时无法计算覆盖分布。") : window.PortalI18n.t("正在载入测试目录，覆盖数据随后显示。");
        root.innerHTML = window.PortalI18n.html`${T.panelHead(window.PortalI18n.t("覆盖审查"), window.PortalI18n.t("先看覆盖结论，再按需展开模块明细。"))}<div class="testPanelBody"><div class="testSectionFlow">${T.section("overview", window.PortalI18n.t("审查摘要"), window.PortalI18n.t("等待可审查的目录数据"), window.PortalI18n.html`<div class="testEmpty">${message}</div>`, true)}</div></div>`;
        return;
      }
      const findings = rows.filter(row => row.cases.length === 0 || row.important === 0 || row.design === 0);
      const findingGroups = [
        { label: window.PortalI18n.t("没有测试用例"), hint: window.PortalI18n.t("先为这些模块建立最小可执行覆盖。"), rows: rows.filter(row => row.cases.length === 0) },
        { label: window.PortalI18n.t("缺少 P0/P1"), hint: window.PortalI18n.t("确认关键风险是否已有高重要级用例。"), rows: rows.filter(row => row.cases.length > 0 && row.important === 0) },
        { label: window.PortalI18n.t("缺少设计点"), hint: window.PortalI18n.t("补充设计依据，或确认现有用例为设计点。"), rows: rows.filter(row => row.cases.length > 0 && row.design === 0) },
      ];
      const signalCount = findingGroups.reduce((total, group) => total + group.rows.length, 0);
      const agentCount = rows.reduce((total, row) => total + row.agent, 0);
      const overview = window.PortalI18n.html`<div class="testCatalogMetrics"><div class="testMetric"><strong>${T.store.cases.length}</strong><span>测试用例</span></div><div class="testMetric"><strong>${rows.length}</strong><span>功能模块</span></div><div class="testMetric"><strong>${findings.length}</strong><span>缺口模块</span></div><div class="testMetric"><strong>${agentCount}</strong><span>AI 使用用例</span></div></div><div class="testAgentIntro"><span class="testAgentEyebrow">审查结论</span><p>${findings.length ? window.PortalI18n.text`${findings.length} 个模块出现 ${signalCount} 条分布信号；建议先处理无测试和缺少 P0/P1 的模块。` : window.PortalI18n.t("当前模块分布没有明显空洞，可继续核对用例质量与运行证据。")}</p></div>`;
      const findingContent = findings.length ? findingGroups.filter(group => group.rows.length).map(group => window.PortalI18n.html`<details class="testGateDetails"><summary>${T.escapeHtml(group.label)} · ${group.rows.length} 个模块</summary><p class="testMuted">${T.escapeHtml(group.hint)}</p><ol>${group.rows.map(row => window.PortalI18n.html`<li><strong>${T.escapeHtml(row.module.label)}</strong> <span class="testMuted">${row.cases.length} 条用例</span></li>`).join("")}</ol></details>`).join("") : window.PortalI18n.markup('<div class="testEmpty">当前模块分布没有明显空洞。</div>');
      const visibleRows = showAllDistribution ? rows : rows.slice(0, distributionPreviewLimit);
      const distribution = window.PortalI18n.html`<p class="testMuted">窄面板中可左右滑动统计列；默认先显示前 ${Math.min(distributionPreviewLimit, rows.length)} 个模块。</p><div class="testDistributionScroll" tabindex="0" aria-label="测试覆盖分布表，可横向滚动"><table class="testDistribution"><thead><tr><th>功能模块</th><th>总数</th><th>P0/P1</th><th>设计点</th><th>AI 使用</th></tr></thead><tbody>${visibleRows.map(row => window.PortalI18n.html`<tr><td>${T.escapeHtml(row.module.label)}</td><td>${row.cases.length}</td><td>${row.important}</td><td>${row.design}</td><td>${row.agent}</td></tr>`).join("")}</tbody></table></div>${rows.length > distributionPreviewLimit ? window.PortalI18n.html`<button class="testTextButton" data-toggle-distribution type="button">${showAllDistribution ? window.PortalI18n.text`收起到前 ${distributionPreviewLimit} 个模块` : window.PortalI18n.text`显示全部 ${rows.length} 个模块`}</button>` : ""}`;
      root.innerHTML = window.PortalI18n.html`${T.panelHead(window.PortalI18n.t("覆盖审查"), window.PortalI18n.t("审查当前测试目录的实际分布和待确认缺口。"))}<div class="testPanelBody"><div class="testSectionFlow">
        ${T.section("overview", window.PortalI18n.t("审查摘要"), window.PortalI18n.text`${findings.length} 个缺口模块 · ${signalCount} 条分布信号`, overview, true)}
        ${T.section("suggestions", window.PortalI18n.t("待审查信号"), window.PortalI18n.t("按缺口类型归组，展开后查看模块"), findingContent, true)}
        ${T.section("distribution", window.PortalI18n.t("模块分布"), window.PortalI18n.text`显示 ${visibleRows.length} / ${rows.length} 个二级模块`, distribution, true)}
      </div></div>`;
      root.querySelector("[data-toggle-distribution]")?.addEventListener("click", () => {
        showAllDistribution = !showAllDistribution;
        render();
        root.querySelector('[data-section="distribution"]')?.scrollIntoView({ block: "start" });
        root.querySelector("[data-toggle-distribution]")?.focus();
      });
    };
    return T.panelController(root, render);
  }
  T.registerPanel({ type: "test-review", title: window.PortalI18n.t("覆盖审查"), icon: "search", component: "PortalTest.ReviewPanel", create });
})();
