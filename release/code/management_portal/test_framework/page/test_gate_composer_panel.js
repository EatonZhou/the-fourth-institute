(() => {
  "use strict";
  const T = window.PortalTest;
  const templates = [
    { id: "critical", name: window.PortalI18n.t("关键路径"), purpose: "快速验证最关键的人类使用路径", select: { importance: ["P0"] } },
    { id: "design", name: window.PortalI18n.t("当前设计点"), purpose: "验证当前被选为设计点且可执行的用例", select: { is_design_point: true } },
    { id: "test-workbench", name: window.PortalI18n.t("测试工作台"), purpose: "验证测试框架与测试工作台", select: { modules: ["operations.test-framework", "operations.test-workbench"] } },
  ];
  const scopePreviewLimit = 8;
  function create({ root }) {
    const expandedScopes = new Set();
    const resolve = gate => T.store.cases.filter(item => item.lifecycle !== "retired" && (!gate.select.importance || gate.select.importance.includes(item.importance)) && (!gate.select.modules || gate.select.modules.includes(item.module)) && (gate.select.is_design_point === undefined || item.is_design_point === gate.select.is_design_point));
    const scopeHtml = (gate, cases) => {
      const expanded = expandedScopes.has(gate.id);
      const visibleCases = expanded ? cases : cases.slice(0, scopePreviewLimit);
      return window.PortalI18n.html`<ol>${visibleCases.map(item => window.PortalI18n.html`<li><a class="testTextButton" data-open-case="${T.escapeHtml(item.id)}" href="/wall?case=${encodeURIComponent(item.id)}">${T.escapeHtml(item.description || item.title || item.id)}</a></li>`).join("")}</ol>${cases.length > scopePreviewLimit ? window.PortalI18n.html`<button class="testTextButton" data-toggle-gate-scope="${T.escapeHtml(gate.id)}" type="button">${expanded ? window.PortalI18n.text`收起到前 ${scopePreviewLimit} 条` : window.PortalI18n.text`显示全部 ${cases.length} 条`}</button>` : ""}`;
    };
    const cardHtml = gate => {
      const cases = resolve(gate);
      const unavailable = cases.length === 0;
      const moduleCount = new Set(cases.map(item => item.module)).size;
      const importantCount = cases.filter(item => ["P0", "P1"].includes(item.importance)).length;
      const designCount = cases.filter(item => item.is_design_point).length;
      return window.PortalI18n.html`<article class="testGateCard"><div class="testGateCardHead"><div><strong>${T.escapeHtml(gate.name)}</strong><p>${T.escapeHtml(gate.purpose)}</p></div><span class="testGateCount">${cases.length} 条</span></div><div class="testBadges"><span class="testBadge">${moduleCount} 个模块</span><span class="testBadge">P0/P1 ${importantCount}</span><span class="testBadge">设计点 ${designCount}</span></div><div class="testPanelActions"><button class="portalButton is-primary" data-run-gate="${T.escapeHtml(gate.id)}" type="button" ${unavailable ? window.PortalI18n.t('disabled title="当前模板没有匹配的测试用例"') : ""}>生成运行计划</button>${unavailable ? window.PortalI18n.t('<span class="testMuted">当前目录没有匹配用例</span>') : window.PortalI18n.html`<details class="testGateDetails" data-gate-scope="${T.escapeHtml(gate.id)}"><summary>查看 ${cases.length} 条范围</summary>${scopeHtml(gate, cases)}</details>`}</div></article>`;
    };
    const render = () => {
      root.innerHTML = window.PortalI18n.html`${T.panelHead(window.PortalI18n.t("门禁编排"), window.PortalI18n.t("按目录即时组合测试，并送往运行控制台确认。"))}<div class="testPanelBody"><div class="testSectionFlow">
        ${T.section("templates", window.PortalI18n.t("即时模板"), window.PortalI18n.text`${templates.length} 个选择规则 · 不会登记或发布门禁`, templates.map(cardHtml).join(""), true)}
      </div></div>`;
      root.querySelectorAll("[data-run-gate]").forEach(button => button.addEventListener("click", () => { const gate = templates.find(item => item.id === button.dataset.runGate); T.prepareRun(resolve(gate).map(item => item.id)); }));
      root.querySelectorAll("[data-toggle-gate-scope]").forEach(button => button.addEventListener("click", () => {
        const gateId = button.dataset.toggleGateScope;
        if (expandedScopes.has(gateId)) expandedScopes.delete(gateId);
        else expandedScopes.add(gateId);
        render();
        const details = [...root.querySelectorAll("[data-gate-scope]")].find(item => item.dataset.gateScope === gateId);
        if (details) details.open = true;
        [...root.querySelectorAll("[data-toggle-gate-scope]")].find(item => item.dataset.toggleGateScope === gateId)?.focus();
      }));
      root.querySelectorAll("[data-open-case]").forEach(link => link.addEventListener("click", event => {
        if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        T.openCase(link.dataset.openCase);
      }));
    };
    return T.panelController(root, render);
  }
  T.registerPanel({ type: "gate-composer", title: window.PortalI18n.t("门禁编排"), icon: "check", component: "PortalTest.GateComposerPanel", create });
})();
