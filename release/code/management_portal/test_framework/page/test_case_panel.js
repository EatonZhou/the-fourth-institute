(() => {
  "use strict";
  const T = window.PortalTest;
  async function renderDesignBinding(item, host) {
    if (!item.design_point_ref) {
      host.dataset.bindingState = "unbound";
      host.textContent = window.PortalI18n.t("未绑定 WALL 设计点；文档引用不代表点级绑定。");
      return;
    }
    host.dataset.bindingState = "loading";
    host.textContent = window.PortalI18n.t("正在验证 WALL 设计点…");
    try {
      const binding = await T.api(`/api/wall/test-binding?case_id=${encodeURIComponent(item.id)}`);
      if (!host.isConnected) return;
      if (binding.ref !== item.design_point_ref) throw new Error("点绑定已变化，请刷新目录。");
      host.dataset.bindingState = binding.status;
      if (binding.status === "valid" && binding.url?.startsWith("/wall?")) {
        const link = document.createElement("a");
        link.href = binding.url;
        link.textContent = window.PortalI18n.text`WALL 设计点 · ${binding.title || binding.ref}`;
        link.dataset.wallDesignPoint = binding.ref;
        host.replaceChildren(link);
      } else {
        host.textContent = window.PortalI18n.text`WALL 关联不可用：${binding.reason || window.PortalI18n.t("请重新选择设计点")}`;
      }
    } catch (error) {
      if (!host.isConnected) return;
      host.dataset.bindingState = "error";
      host.textContent = window.PortalI18n.text`WALL 关联验证失败：${error.message}`;
    }
  }
  function create({ root }) {
    const views = T.store.caseViewStore || window.PortalWorkbench.storage.createMemoryStore();
    let caseId = T.store.selectedId;
    let renderedCaseId = "";
    let expanded;
    const restore = () => {
      const saved = views.read()?.[caseId] || {};
      expanded = new Set(saved.expanded || ["overview", "implementation"]);
    };
    const remember = () => {
      if (renderedCaseId !== caseId) return;
      root.querySelectorAll("[data-section]").forEach(item => item.open ? expanded.add(item.dataset.section) : expanded.delete(item.dataset.section));
      if (caseId) views.write({ [caseId]: { expanded: [...expanded] } });
    };
    restore();
    const render = (options = {}) => {
      remember();
      const previousBody = root.querySelector(".testPanelBody");
      const previousScrollTop = options.preserveScroll === false ? 0 : previousBody?.scrollTop || 0;
      const item = T.store.case(caseId || T.store.selectedId);
      if (!item) {
        const message = T.store.catalogStatus === "loading"
          ? window.PortalI18n.t("正在加载测试目录与当前用例…")
          : T.store.catalogStatus === "error"
            ? window.PortalI18n.text`测试目录加载失败：${T.escapeHtml(T.store.catalogError)}`
            : window.PortalI18n.t("请从测试目录选择一个测试用例。");
        root.innerHTML = window.PortalI18n.html`<div class="testEmpty"${T.store.catalogStatus === "loading" ? ' role="status"' : ""}>${message}</div>`;
        return;
      }
      if (item.detail_loaded === false) {
        root.innerHTML = window.PortalI18n.markup('<div class="testEmpty" role="status">正在加载测试用例详情…</div>');
        T.store.ensureCaseDetail(item.id).catch(error => T.notify(`测试用例详情不可用：${error.message}`, "error"));
        return;
      }
      caseId = item.id;
      const selectedImpl = T.store.selectedImplementation.get(item.id) || item.implementations?.[0]?.id || "";
      const pointBinding = window.PortalI18n.html`<div class="testCaseDesignRefs"><span>WALL 设计点（一对一）</span><div data-design-point-binding role="status"></div>${item.design_point_ref ? window.PortalI18n.html`<code>${T.escapeHtml(item.design_point_ref)}</code>` : ""}</div>`;
      const overviewFacts = window.PortalI18n.html`<div class="testCaseFacts"><div><span>功能模块</span><strong>${T.escapeHtml(T.store.moduleLabel(item))}</strong></div><div><span>重要等级</span><strong>${T.escapeHtml(item.importance)}</strong></div><div><span>测试方面</span><strong>${T.escapeHtml((item.aspects || []).map(aspect => T.aspectLabels[aspect] || aspect).join(" · ") || window.PortalI18n.t("未分类"))}</strong></div><div><span>关键设计点</span><strong>${item.is_design_point ? window.PortalI18n.t("是") : window.PortalI18n.t("否")}</strong></div></div><div class="testCaseDesignRefs"><span>关联设计</span>${item.design_refs?.length ? window.PortalI18n.html`<div>${item.design_refs.map(ref => window.PortalI18n.html`<code>${T.escapeHtml(ref)}</code>`).join("")}</div>` : window.PortalI18n.t("<em>未关联</em>")}</div>`;
      const overviewSource = window.PortalI18n.markup('<div class="testCaseSourceNote" role="note">标题、说明与分类写在测试源码的 docstring 中；修改源码后目录随之更新。</div>');
      const overview = `${pointBinding}${overviewFacts}${overviewSource}`;
      const implementations = item.implementations?.length ? item.implementations.map(impl => {
        const risk = impl.risk || "unspecified";
        const metadata = [window.PortalI18n.html`<span class="testBadge ${T.riskTone(risk)}">执行风险 · ${T.escapeHtml(T.riskLabel(risk))}</span>`, ...(impl.profiles || []).map(profile => window.PortalI18n.html`<span class="testBadge">${T.escapeHtml(profile)}</span>`)].join("");
        return window.PortalI18n.html`<article class="testImplementation ${impl.id === selectedImpl ? "is-selected" : ""}"><div class="testImplementationHead"><label class="testImplementationChoice"><input type="radio" name="implementation" value="${T.escapeHtml(impl.id)}" ${impl.id === selectedImpl ? "checked" : ""}><span><strong>${T.escapeHtml(impl.runner)}</strong><small>${impl.id === selectedImpl ? window.PortalI18n.t("当前实现") : window.PortalI18n.t("可选实现")}</small></span></label><div class="testPanelActions"><button class="portalButton" data-run-impl="${T.escapeHtml(impl.runner)}" data-profile="${T.escapeHtml(impl.profiles?.[0] || "")}" data-portal-tooltip="打开运行控制台并准备生成计划" type="button">准备运行</button><button class="portalButton" data-open-source="${T.escapeHtml(impl.source)}" data-implementation-id="${T.escapeHtml(impl.id)}" type="button">预览源码</button></div></div><div class="testImplementationPath"><span>入口</span><code>${T.escapeHtml(impl.entrypoint)}</code></div><div class="testBadges">${metadata}</div>${impl.task_prompt ? window.PortalI18n.html`<details class="testImplementationPrompt"><summary>任务 Prompt</summary><p class="testPrompt">${T.escapeHtml(impl.task_prompt)}</p></details>` : ""}</article>`;
      }).join("") : window.PortalI18n.markup('<div class="testEmpty">此测试用例尚无可执行实现。</div>');
      const evidence = T.store.runs.filter(run => (run.caseIds || []).includes(item.id));
      const evidenceHtml = evidence.length ? evidence.map(run => window.PortalI18n.html`<article class="testRunCard"><strong class="testStatus" data-status="${T.escapeHtml(run.status)}">${T.escapeHtml(T.statusLabel(run.status))}</strong><span> · ${T.escapeHtml(run.run_id || "")}</span><pre class="testRunOutput">${T.escapeHtml(run.output || run.error || window.PortalI18n.t("暂无输出"))}</pre></article>`).join("") : window.PortalI18n.markup('<div class="testEmpty">当前会话尚无此用例的运行证据。</div>');
      const liveHtml = item.live ? window.PortalI18n.html`<p>${T.escapeHtml(item.live.title || window.PortalI18n.t("已登记动态呈现"))}</p><div class="testPanelActions"><button class="portalButton is-primary" data-live type="button">Live</button><button class="portalButton" data-live-discuss type="button">与 AI 讨论 / Live 化</button></div><div data-live-result></div>` : window.PortalI18n.markup('<div class="testEmpty">尚未 Live 化。可通过 TAU · Agent 真实使用面板创建受控的 Live 化任务。</div>');
      const latest = evidence[0]?.status || "";
      const actions = window.PortalI18n.markup('<button class="portalButton is-compact is-primary" data-run-case type="button" title="为此用例生成运行计划">运行计划</button><button class="portalButton is-compact" data-open-tau type="button">加入 TAU</button>');
      root.innerHTML = window.PortalI18n.html`${T.panelHead(window.PortalI18n.t("测试用例"), item.title, actions)}<div class="testPanelBody"><article class="testCaseHero"><div class="testCaseHeroTop"><h3>${T.escapeHtml(T.caseLabel(item))}</h3><div class="testBadges">${T.badges(item)}</div></div><p>${T.escapeHtml(item.description)}</p><div class="testCaseKey"><span>KEY</span><code>${T.escapeHtml(item.key)}</code></div></article><div class="testSectionFlow">${T.section("overview", window.PortalI18n.t("概览"), `${item.importance} · ${T.store.moduleLabel(item)}`, overview, expanded.has("overview"))}${T.section("implementation", window.PortalI18n.t("测试实现"), window.PortalI18n.text`${item.implementations?.length || 0} 个实现`, implementations, expanded.has("implementation"))}${T.section("evidence", window.PortalI18n.t("运行证据"), evidence.length ? window.PortalI18n.text`${evidence.length} 条当前会话记录` : window.PortalI18n.t("暂无运行证据"), evidenceHtml, expanded.has("evidence"), latest)}${T.section("live", "Live", item.live ? window.PortalI18n.t("已登记动态呈现") : window.PortalI18n.t("尚未 Live 化"), liveHtml, expanded.has("live"))}</div></div>`;
      const currentBody = root.querySelector(".testPanelBody");
      renderedCaseId = caseId;
      const bindingHost = root.querySelector("[data-design-point-binding]");
      if (bindingHost) renderDesignBinding(item, bindingHost);
      if (currentBody) currentBody.scrollTop = previousScrollTop;
      root.querySelectorAll("[data-section]").forEach(node => node.addEventListener("toggle", remember));
      root.querySelectorAll('input[name="implementation"]').forEach(input => input.addEventListener("change", () => { T.store.selectedImplementation.set(item.id, input.value); render(); }));
      root.querySelector("[data-run-case]")?.addEventListener("click", () => T.prepareRun([item.id], selectedImpl ? { implementation_ids: [selectedImpl] } : {}));
      root.querySelector("[data-open-tau]")?.addEventListener("click", () => { T.workbench()?.invokePanel("tau", "setCase", item.id); T.showPanel("tau"); });
      root.querySelectorAll("[data-run-impl]").forEach(button => button.addEventListener("click", () => T.prepareRun([item.id], { implementation_ids: [button.closest(".testImplementation").querySelector('input[name="implementation"]').value], runners: [button.dataset.runImpl], ...(button.dataset.profile ? { profile: button.dataset.profile } : {}) })));
      root.querySelectorAll("[data-open-source]").forEach(button => button.addEventListener("click", async () => {
        try {
        const path = button.dataset.openSource;
        const sourceUrl = `/test/api/source?case_id=${encodeURIComponent(item.id)}&implementation_id=${encodeURIComponent(button.dataset.implementationId || "")}`;
        const document = await T.api(sourceUrl);
        await T.workbench()?.openResource({
          type: "preview",
          resource: {
            kind: "file",
            title: path,
            path,
            line: document.line,
            request: {
              kind: "file",
              title: path,
              path,
              line: document.line,
              load: async () => document,
            },
          },
          mode: "side",
        });
        } catch (error) { T.notify(error.message, "error"); }
      }));
      root.querySelector("[data-live]")?.addEventListener("click", async () => { const target = root.querySelector("[data-live-result]"); try { if (!window.PortalTestLive) throw new Error("Live 播放器尚未就绪"); target.textContent = window.PortalI18n.t("正在准备只读演示…"); await window.PortalTestLive.open(item); } catch (error) { target.textContent = error.message; } });
      root.querySelector("[data-live-discuss]")?.addEventListener("click", () => { T.workbench()?.invokePanel("tau", "setCase", item.id); T.showPanel("tau"); });
    };
    return T.panelController(root, render, { setCase(nextId) { if (caseId !== nextId) { remember(); caseId = nextId; restore(); } render({ preserveScroll: false }); }, target: () => ({ case_id: caseId }) });
  }
  T.registerPanel({ type: "test-case", title: window.PortalI18n.t("测试用例"), icon: "file-text", component: "PortalTest.CasePanel", create });
})();
