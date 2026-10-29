(() => {
  "use strict";

  // Live checks of the Test page's design points (adapter "design-testlab").
  // Each step mounts the production view module it checks (catalog, case,
  // overview, runs, TAU) in its own stage above the page, reads only, and
  // compares what the view shows with the catalog and the run records.  The
  // page's own address, view and conversation stay as they were.
  const adapterId = "design-testlab";
  const capabilities = new Set([
    "testlab.filter-intersection", "testlab.design-filter", "testlab.live-entry",
    "testlab.run-evidence", "testlab.gate-scope", "testlab.implementation-detail",
    "testlab.case-navigation", "testlab.retained-history", "testlab.runner-feedback", "testlab.live-write-guard",
  ]);
  const CASE_CAPABILITIES = new Set(["testlab.live-entry", "testlab.run-evidence", "testlab.implementation-detail", "testlab.case-navigation"]);
  // Controls that would write, navigate or open something outside the stage.
  const BLOCKED = [
    "a[href]", "[data-select-case]", "[data-select-all]", "[data-clear-selection]", "[data-run-selection]", "[data-load-more]",
    "[data-case-run]", "[data-case-copy]", "[data-run-impl]", "[data-source-impl]", "[data-live]",
    "[data-template-run]", "[data-overview-refresh]", "[data-select-run]", "[data-runs-delete]", "[data-runs-clear]",
    "[data-run-cancel]", "[data-run-again]", "[data-run-copy]", "[data-run-delete]", "[data-result-chat]",
    "[data-tau-run]", "[data-tau-rotate]", "[data-tau-chat]", "[data-tau-admission-open]", "[data-tau-admission-refresh]",
    "[data-plan-start]",
  ].join(", ");
  const test = () => window.PortalTest;
  const main = () => document.getElementById("testPageMain");
  let session = null;

  function requireCondition(condition, message) {
    if (!condition) throw new Error(`Test Live 验证失败：${message}`);
  }

  function checkSignal(signal) {
    if (signal?.aborted || session?.aborter.signal.aborted) {
      throw new DOMException("Test Live 已取消", "AbortError");
    }
  }

  async function settled(signal) {
    checkSignal(signal);
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    checkSignal(signal);
  }

  async function waitUntil(predicate, signal, message) {
    const deadline = performance.now() + 10000;
    while (true) {
      checkSignal(signal);
      if (predicate()) break;
      requireCondition(performance.now() < deadline, message);
      await settled(signal);
    }
    checkSignal(signal);
  }

  function element(root, selector) {
    const target = root?.querySelector(selector);
    requireCondition(target?.isConnected, `缺少生产控件 ${selector}`);
    return target;
  }

  const same = (left, right) => JSON.stringify(left) === JSON.stringify(right);
  const sorted = values => [...values].sort();

  function closeStage() {
    session?.restoreChoice?.();
    if (session) session.restoreChoice = null;
    session?.secondary?.destroy?.();
    if (session) session.secondary = null;
    session?.controller?.destroy?.();
    if (session) session.controller = null;
    session?.stage?.remove();
    if (session) session.stage = null;
  }

  const FACTORIES = Object.freeze({
    catalog: () => window.PortalTestCatalog.create,
    case: () => window.PortalTestCase.create,
    overview: () => window.PortalTestOverview.create,
    runs: () => window.PortalTestRun.createList,
    tau: () => window.PortalTestTau.create,
    empty: () => null,
  });

  // The production view module in a stage of its own; the page underneath keeps its address and view.
  function mountView(kind) {
    closeStage();
    const factory = FACTORIES[kind]();
    requireCondition(kind === "empty" || typeof factory === "function", `生产视图 ${kind} 未加载`);
    const stage = document.createElement("section");
    stage.dataset.designLiveTestlab = kind;
    stage.setAttribute("aria-label", window.PortalI18n.t("Test 生产组件只读验证"));
    Object.assign(stage.style, {
      position: "fixed", inset: "8% 6% 12%", zIndex: "1000", overflow: "auto", padding: "16px 20px",
      background: "var(--bg)", border: "1px solid var(--border)", borderRadius: "16px",
    });
    const root = document.createElement("div");
    root.className = "testPageViewRoot";
    stage.appendChild(root);
    document.body.appendChild(stage);
    stage.addEventListener("submit", event => {
      event.preventDefault();
      event.stopImmediatePropagation();
    }, true);
    stage.addEventListener("click", event => {
      if (!event.target.closest(BLOCKED)) return;
      event.preventDefault();
      event.stopImmediatePropagation();
    }, true);
    // Filter changes would replace the page's own address.
    for (const type of ["change", "input"]) {
      stage.addEventListener(type, event => {
        if (!event.target.closest("[data-catalog-toolbar], [data-select-case], [data-select-run], [data-tau-admission-open]")) return;
        event.stopImmediatePropagation();
      }, true);
    }
    session.stage = stage;
    session.controller = factory ? factory({ root }) : null;
    return stage;
  }

  // Every case's browse fields, read once per Live session to compute the expected sets.
  function catalogCases() {
    session.cases ||= test().api("/test/api/catalog?projection=workspace", { timeoutMs: 60_000 }).then(payload => payload.cases || []);
    return session.cases;
  }

  async function activeCases(signal) {
    const cases = await catalogCases();
    checkSignal(signal);
    return cases.filter(item => item.lifecycle !== "retired");
  }

  // The requested case when it fits, else the first one that does; a preference falls back to any current case.
  async function chooseCase(context, signal, { required, preferred } = {}) {
    const active = await activeCases(signal);
    const fits = required || preferred || (() => true);
    const requested = active.find(item => item.id === context?.caseId);
    let chosen = requested && fits(requested) ? requested : active.find(fits);
    if (!chosen && !required) chosen = requested || active[0];
    requireCondition(chosen, "当前 Catalog 缺少此场景可验证的条目");
    return chosen;
  }

  // The catalog page's filter semantics: dimensions combine with AND, aspects with OR, retired cases never show.
  function matches(item, filters) {
    const modules = [].concat(filters.module || []);
    const runners = item.runners || [];
    return item.lifecycle !== "retired"
      && (!filters.importance || item.importance === filters.importance)
      && (!modules.length || modules.includes(item.module))
      && (!filters.design || String(item.is_design_point) === filters.design)
      && (!filters.aspect?.length || item.aspects.some(aspect => filters.aspect.includes(aspect)))
      && (!filters.live || Boolean(item.live))
      && (!filters.runner || (filters.runner === "none" ? !runners.length : runners.includes(filters.runner)));
  }

  async function verifyCatalog(filters, signal) {
    const root = session.stage;
    const controller = session.controller;
    const pending = controller.load(filters);
    session.pending = pending;
    await pending;
    checkSignal(signal);
    const snapshot = controller.snapshot();
    requireCondition(!snapshot.failure && !snapshot.loading, `Catalog 查询没有完成：${snapshot.failure || "仍在读取"}`);
    const expected = (await activeCases(signal)).filter(item => matches(item, filters)).map(item => item.id);
    const wanted = new Set(expected);
    requireCondition(snapshot.count === expected.length, `服务端匹配 ${snapshot.count} 条，按分类交集/同维并集与退役排除应为 ${expected.length} 条`);
    requireCondition(new Set(snapshot.caseIds).size === snapshot.caseIds.length && snapshot.caseIds.every(id => wanted.has(id)), "Catalog 首页混入了不符合筛选的条目或重复条目");
    requireCondition(snapshot.caseIds.length === Math.min(100, expected.length), "Catalog 首批结果丢失");
    const rows = [...root.querySelectorAll("[data-case-id]")].map(row => row.dataset.caseId);
    requireCondition(same(rows, snapshot.caseIds), "Catalog 渲染结果与查询结果不一致");
    requireCondition(element(root, "[data-catalog-count]").textContent.includes(String(expected.length)), "Catalog 数量与实际结果不一致");
    return expected;
  }

  const actions = {
    async "testlab.retained-history"(prepared, signal) {
      const root = prepared.target;
      const runs = test().recentRuns();
      const rows = [...root.querySelectorAll(".testRunsPage [data-run-row]")].map(row => row.dataset.runRow);
      requireCondition(same(rows, runs.map(run => run.run_id)), "运行记录被按发起人过滤或丢失");
      const retained = runs.find(run => test().terminalStatuses.has(run.status));
      if (!retained) {
        return { target: element(root, ".testRunsPage"), caption: "运行记录列出全部保留运行；当前没有已结束的运行可打开，也没有启动新运行。" };
      }
      const host = document.createElement("div");
      host.className = "testPageViewRoot";
      host.style.marginTop = "18px";
      root.appendChild(host);
      const view = window.PortalTestRun.create({ root: host });
      session.secondary = view;
      session.touchedRuns = true;
      const pending = view.load(retained.run_id);
      session.pending = pending;
      const record = await pending;
      checkSignal(signal);
      requireCondition(record?.run_id === retained.run_id, "运行页打开了其他运行");
      requireCondition(element(host, ".testHero[data-run-status]").dataset.runStatus === record.status, "运行页没有采用 Run record 的真实状态");
      const output = record.output || record.error || window.PortalI18n.t("运行已结束，没有 Runner 原始输出。");
      requireCondition(element(host, "[data-run-raw-output]").textContent === output, "原始输出或错误没有原样呈现");
      requireCondition(!host.querySelector("[data-run-cancel]"), "已结束的运行仍可中止");
      requireCondition(location.href === session.url, "打开运行改动了页面地址");
      return { target: element(host, ".testRunPage"), caption: "运行记录不按发起人隐藏；重开已结束的运行时，状态取自 Run record，原始输出原样呈现，没有启动新运行。" };
    },

    async "testlab.runner-feedback"(prepared, signal) {
      const root = prepared.target;
      const loading = window.PortalI18n.t("正在读取 TAU 准入状态…");
      await waitUntil(() => root.querySelector("[data-tau-admission-status]")?.textContent && root.querySelector("[data-tau-admission-status]").textContent !== loading, signal, "TAU 准入状态没有读出");
      const admission = await test().api("/test/api/tau/admission");
      checkSignal(signal);
      const tasks = sorted((await activeCases(signal)).filter(item => (item.runners || []).includes("tau")).map(item => item.id));
      const actual = sorted([...root.querySelectorAll("[data-tau-case]")].map(input => input.dataset.tauCase));
      requireCondition(same(actual, tasks), "TAU 任务被丢弃或混入没有 TAU 实现的用例");
      element(root, "[data-tau-all]").click();
      requireCondition(root.querySelectorAll("[data-tau-case]:checked").length === tasks.length, "TAU 全选没有选择全部任务");
      requireCondition(element(root, "[data-tau-selection]").textContent.includes(String(tasks.length)), "已选数量与任务不一致");
      const tau = test().runner("tau");
      requireCondition(Boolean(tau), "目录没有明确声明 TAU 执行方式");
      const runnable = test().canOperate() && tau.available !== false && admission?.configuration?.admission_state === "open";
      requireCondition(element(root, "[data-tau-run]").disabled === !(runnable && tasks.length), "执行方式、准入状态与选择没有约束运行按钮");
      if (tau.available === false) {
        requireCondition(Boolean(tau.reason?.trim()), "不可用的执行方式缺少原因");
        requireCondition(element(root, ".testHero").textContent.includes(tau.reason), "不可用原因没有显示在 TAU 页上");
      }
      element(root, "[data-tau-all]").click();
      requireCondition(root.querySelectorAll("[data-tau-case]:checked").length === 0 && element(root, "[data-tau-run]").disabled, "清空后仍能启动 TAU");
      return { target: element(root, "[data-tau-bar]"), caption: `已核对 ${tasks.length} 个 TAU 任务的选择、执行方式与准入状态对运行按钮的约束；没有运行、更换 Resident 或调用模型。` };
    },

    async "testlab.live-write-guard"(prepared) {
      requireCondition(window.PortalLiveTour?.state().committing, "只读防护必须在生产 PortalLiveTour commit 阶段验证");
      const dialog = document.createElement("dialog");
      const form = document.createElement("form");
      form.method = "dialog";
      const button = document.createElement("button");
      button.type = "submit";
      button.textContent = window.PortalI18n.t("验证只读表单保护（不会发送网络请求）");
      form.appendChild(button);
      dialog.appendChild(form);
      prepared.target.appendChild(dialog);
      dialog.open = true;
      let blocked = false;
      try {
        form.requestSubmit();
      } catch (error) {
        blocked = error.code === "blocked_effect";
      }
      requireCondition(blocked, "生产 Live 未阻止表单提交");
      requireCondition(dialog.open, "只读保护失败后改变了本地对话框");
      return { target: dialog, caption: "生产 Live 在提交前抛出 blocked_effect；探针采用 method=dialog 且宿主拦截提交，保护缺失时也不会发送网络写请求。" };
    },

    async "testlab.filter-intersection"(prepared, signal) {
      const { target: root, item } = prepared;
      const filters = { importance: item.importance, module: [item.module], design: String(item.is_design_point), aspect: item.aspects.slice(0, 2) };
      const result = await verifyCatalog(filters, signal);
      requireCondition(result.includes(item.id), "分类筛选遗漏种子用例");
      requireCondition(element(root, "[data-catalog-module]").value === item.module && element(root, "[data-catalog-importance]").value === item.importance
        && element(root, "[data-catalog-design]").value === filters.design, "工具栏没有显示当前筛选");
      requireCondition(filters.aspect.every(aspect => element(root, `[data-catalog-aspect="${CSS.escape(aspect)}"]`).getAttribute("aria-pressed") === "true"), "测试方面的选择没有显示");
      return { target: element(root, ".testCaseList"), caption: `重要等级、模块、设计点之间取交集，测试方面之间取并集，退役条目不出现；匹配 ${result.length} 条。` };
    },

    async "testlab.design-filter"(prepared, signal) {
      const root = prepared.target;
      for (const design of ["true", "false"]) await verifyCatalog({ design }, signal);
      await verifyCatalog({ live: "1" }, signal);
      const all = await verifyCatalog({}, signal);
      return { target: element(root, ".testCaseList"), caption: `设计点、非设计点、有 Live 三种筛选的结果都符合目录；清除筛选后回到全部 ${all.length} 条当前用例。` };
    },

    async "testlab.live-entry"(prepared, signal) {
      const { target: root, item } = prepared;
      const section = root.querySelector('[data-test-section="live"]');
      if (!item.live) {
        requireCondition(!section && !root.querySelector("[data-live]"), "没有 Live 定义的用例仍显示播放入口");
        return { target: element(root, ".testHero"), caption: "没有登记 Live 的用例不显示 Live 区域，也没有播放入口。" };
      }
      requireCondition(section?.isConnected, "已登记 Live 的用例缺少 Live 区域");
      requireCondition(!element(section, "[data-live]").disabled, "已登记 Live 的入口被错误禁用");
      const descriptor = await test().api(`/test/api/demo?case_id=${encodeURIComponent(item.id)}`);
      checkSignal(signal);
      requireCondition(descriptor.case_id === item.id, "Live 读取了其他用例的定义");
      const live = descriptor.demo;
      requireCondition(live && live.target === item.live.target && live.mode === item.live.mode, "Live 入口与登记目标/模式不一致");
      const target = new URL(live.target, location.href);
      requireCondition(target.origin === location.origin && live.target.startsWith("/") && !live.target.startsWith("//"), "Live 指向站外地址");
      if (live.mode === "direct") {
        requireCondition(live.risk === "read-only" && live.adapter && live.adapter_version === 1, "真实 Live 缺少只读适配器契约");
        requireCondition(Array.isArray(live.direct_steps) && live.direct_steps.length > 0 && live.direct_steps.every(step => step.capability && step.caption), "Live 步骤缺少可执行能力或验收说明");
        requireCondition(live.adapter === item.live.adapter && same(live.direct_steps, item.live.direct_steps), "Live 入口与 Catalog 的执行步骤不一致");
      } else {
        requireCondition(live.mode === "sandbox" && Array.isArray(live.steps) && live.steps.length > 0, "沙盒 Live 没有可执行步骤");
      }
      return { target: section, caption: "用例页的 Live 入口读取并核对同一用例的只读执行定义；不递归启动另一个播放器，也不生成运行。" };
    },

    async "testlab.run-evidence"(prepared) {
      const { target: root, item } = prepared;
      const section = element(root, '[data-test-section="runs"]');
      const expected = test().recentRuns().filter(run => test().runCaseIds(run).includes(item.id));
      const rows = [...section.querySelectorAll("[data-run-row]")];
      requireCondition(same(rows.map(row => row.dataset.runRow), expected.map(run => run.run_id)), "运行记录混入其他用例或漏掉保留记录");
      expected.forEach((run, index) => {
        const row = rows[index];
        requireCondition(row.querySelector(".testStatus")?.dataset.status === run.status, "运行记录的状态与 Run record 不一致");
        requireCondition(new URL(element(row, "a[href]").href).searchParams.get("run") === run.run_id, "运行入口没有指向同一 Run ID");
        requireCondition(!row.querySelector(".testRunRowMain strong *"), "运行标题被当作 HTML 呈现");
      });
      if (!expected.length) requireCondition(Boolean(section.querySelector(".testEmpty")), "没有运行记录时缺少空态");
      return { target: section, caption: `按 RunPlan 的 case ID 核对了 ${expected.length} 次包含这条用例的运行；状态取自运行记录，不混入其他用例。` };
    },

    async "testlab.gate-scope"(prepared, signal) {
      const root = prepared.target;
      const active = await activeCases(signal);
      for (const template of window.PortalTestOverview.TEMPLATES) {
        const expected = active.filter(item => matches(item, template.filters)).map(item => item.id);
        const card = element(root, `[data-template="${CSS.escape(template.id)}"]`);
        requireCondition(element(card, ".testTemplateFigures strong").textContent === String(expected.length), `${template.name} 的计数错误`);
        requireCondition(element(card, "[data-template-run]").disabled === !expected.length, `${template.name} 的空范围可执行性错误`);
        const address = test().addressed(new URL(element(card, "a[href]").href).search);
        const filters = { ...template.filters, ...(template.filters.module ? { module: [].concat(template.filters.module) } : {}) };
        requireCondition(address.page === "cases" && same(address.filters, filters), `${template.name} 的“查看用例”没有带上同一组筛选`);
        if (!expected.length) continue;
        const query = test().filterQuery(template.filters);
        query.set("current", "true");
        const result = await test().api(`/test/api/catalog/query?${query}`);
        checkSignal(signal);
        requireCondition(same(sorted(result.case_ids || []), sorted(expected)), `${template.name} 的完整范围与模板规则不一致`);
      }
      return { target: element(root, '[data-test-section="templates"]'), caption: "三个快捷组合的计数、完整范围和“查看用例”的筛选一致，都排除退役条目；没有生成计划或启动运行。" };
    },

    async "testlab.implementation-detail"(prepared) {
      const { target: root, item } = prepared;
      const section = element(root, '[data-test-section="implementations"]');
      const expected = item.implementations || [];
      const cards = [...section.querySelectorAll("[data-implementation-id]")];
      requireCondition(cards.length === expected.length, "用例页的实现数量错误");
      expected.forEach((implementation, index) => {
        const card = cards[index];
        const facts = [...card.querySelectorAll(".testImplFacts dd code")].map(node => node.textContent);
        requireCondition(card.dataset.implementationId === implementation.id, "实现身份错位");
        requireCondition(facts[0] === (implementation.entrypoint || "—"), "实现入口串到了其他用例");
        requireCondition(facts[1] === (implementation.source || "—"), "实现源码来源错误");
        requireCondition(card.querySelector("[data-source-impl]")?.dataset.sourceImpl === implementation.id, "源码入口未绑定当前实现身份");
        requireCondition(card.textContent.includes(test().riskLabel(implementation.risk || "unspecified")), "没有显示实现自身的风险");
        requireCondition((implementation.profiles || []).every(profile => card.textContent.includes(profile)), "遗漏实现的运行环境");
      });
      if (expected.length < 2) {
        requireCondition(!section.querySelector('input[name="testImplementation"]') && cards.every(card => card.dataset.chosen === "true"), "只有一个实现时仍要求选择");
        return { target: section, caption: "实现的入口、源码、风险与运行环境一致；只有一个实现时它就是默认实现，无需选择。" };
      }
      const previous = cards.find(card => card.dataset.chosen === "true")?.dataset.implementationId;
      const controller = session.controller;
      session.restoreChoice = () => { if (previous) controller.choose(previous); };
      const selected = expected[expected.length - 1];
      element(section, `input[name="testImplementation"][value="${CSS.escape(selected.id)}"]`).click();
      const checked = section.querySelectorAll('input[name="testImplementation"]:checked');
      requireCondition(checked.length === 1 && checked[0].value === selected.id, "默认实现没有保持单选");
      requireCondition(same(cards.filter(card => card.dataset.chosen === "true").map(card => card.dataset.implementationId), [selected.id]), "选中的实现没有成为“运行”使用的那一个");
      return { target: section, caption: "实现的入口、源码、风险与运行环境一致；默认实现单选，只改变这一页的本地选择，演示结束后恢复。" };
    },

    async "testlab.case-navigation"(prepared, signal) {
      const { target: root, item } = prepared;
      const href = new URL(test().caseHref(item.id), location.origin);
      requireCondition(href.pathname === "/development" && href.searchParams.get("view") === "test" && href.searchParams.get("case") === item.id
        && [...href.searchParams.keys()].length === 2, "用例地址不是规范的 /development?view=test&case=<ID>");
      requireCondition(test().addressed(href.search).caseId === item.id, "用例地址没有解析回同一用例");
      requireCondition(element(root, ".testHero h1").textContent === (item.title || item.key) && element(root, ".testHeroKey").textContent === item.key, "用例页没有显示地址所指的用例");
      const unknown = "test_000000000000";
      requireCondition(!(await activeCases(signal)).some(candidate => candidate.id === unknown), "探针 ID 恰好存在于目录");
      let refused = false;
      try {
        await session.controller.load(unknown);
      } catch (error) {
        refused = error.status === 404 || Boolean(error.message);
      }
      checkSignal(signal);
      requireCondition(refused, "未知用例 ID 被当作有效用例");
      requireCondition(!root.querySelector(".testCasePage"), "未知用例 ID 显示了另一条用例");
      requireCondition(location.href === session.url, "读取用例改动了页面地址");
      const pending = session.controller.load(item.id);
      session.pending = pending;
      await pending;
      checkSignal(signal);
      return { target: element(root, ".testHero"), caption: "用例的地址只含 case=<ID>，读回的是同一条用例；未知 ID 读取失败，不显示其他用例。" };
    },
  };

  const adapter = {
    id: adapterId,
    version: 1,
    ready() {
      return Boolean(test()?.overview?.() && main() && window.PortalTestCatalog && window.PortalTestCase);
    },
    capture() {
      requireCondition(!session, "已有 Test Live 在执行，不能重叠捕获");
      requireCondition(adapter.ready(), "Test 页面尚未就绪");
      const focus = document.activeElement;
      session = {
        aborter: new AbortController(), controller: null, stage: null, secondary: null, pending: null,
        restoreChoice: null, touchedRuns: false, cases: null,
        url: location.href, historyState: history.state, selection: [...test().selection],
        mainScroll: main()?.scrollTop || 0, scroll: [window.scrollX, window.scrollY], focus,
        focusSelection: focus?.selectionStart == null ? null : [focus.selectionStart, focus.selectionEnd, focus.selectionDirection],
      };
      return session;
    },
    async prepare(step, signal, context = {}) {
      checkSignal(signal);
      requireCondition(session && capabilities.has(step?.capability), `未登记能力 ${step?.capability}`);
      requireCondition(Object.keys(step).every(key => ["capability", "caption"].includes(key)), "能力不能接收任意 selector、URL 或动作参数");
      signal = signal ? AbortSignal.any([signal, session.aborter.signal]) : session.aborter.signal;
      const capability = step.capability;
      let item = null;
      if (CASE_CAPABILITIES.has(capability) || capability === "testlab.filter-intersection") {
        const recent = new Set(test().recentRuns().flatMap(run => test().runCaseIds(run)));
        item = await chooseCase(context, signal, {
          "testlab.implementation-detail": { required: candidate => (candidate.runners || []).length > 0 },
          "testlab.live-entry": { preferred: candidate => Boolean(candidate.live) },
          "testlab.run-evidence": { preferred: candidate => recent.has(candidate.id) },
        }[capability] || {});
      }
      const kind = capability.includes("filter") ? "catalog"
        : capability === "testlab.gate-scope" ? "overview"
          : capability === "testlab.runner-feedback" ? "tau"
            : capability === "testlab.retained-history" ? "runs"
              : capability === "testlab.live-write-guard" ? "empty" : "case";
      const target = mountView(kind);
      if (session.controller) {
        const pending = session.controller.load(kind === "case" ? item.id : kind === "catalog" ? {} : undefined);
        session.pending = pending;
        const loaded = await pending;
        if (kind === "case") item = loaded;
      }
      await settled(signal);
      return { target, item, capability };
    },
    async commit(step, prepared, signal) {
      checkSignal(signal);
      requireCondition(session && prepared?.target === session.stage && prepared.target.isConnected, "准备好的生产组件已失效");
      signal = signal ? AbortSignal.any([signal, session.aborter.signal]) : session.aborter.signal;
      requireCondition(prepared.capability === step?.capability && capabilities.has(step?.capability), "执行能力与 prepare 不一致");
      const result = await actions[step.capability](prepared, signal);
      checkSignal(signal);
      requireCondition(result.target?.isConnected, "验证结果没有有效目标");
      return result;
    },
    async restore(snapshot) {
      if (!snapshot || snapshot !== session) return;
      snapshot.aborter.abort();
      await snapshot.pending?.catch?.(() => {});
      closeStage();
      try {
        test().selection.clear();
        snapshot.selection.forEach(id => test().selection.add(id));
        if (location.href !== snapshot.url) {
          const url = new URL(snapshot.url);
          if (window.PortalTestPage?.navigate) window.PortalTestPage.navigate(`${url.pathname}${url.search}`, { replace: true });
          else history.replaceState(snapshot.historyState, "", snapshot.url);
        }
        // Opening a run moves it to the front of the page's run list; the page reads the list again.
        if (snapshot.touchedRuns) {
          await test().loadOverview({ fresh: true }).catch(() => null);
          test().forgetRuns([]);
        }
        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
        if (main()) main().scrollTop = snapshot.mainScroll;
        window.scrollTo({ left: snapshot.scroll[0], top: snapshot.scroll[1], behavior: "instant" });
        const focus = snapshot.focus?.isConnected ? snapshot.focus : null;
        focus?.focus?.({ preventScroll: true });
        if (snapshot.focusSelection && focus?.setSelectionRange) focus.setSelectionRange(...snapshot.focusSelection);
      } finally {
        session = null;
      }
    },
  };

  window.PortalDesignLiveAdapters = window.PortalDesignLiveAdapters || {};
  window.PortalDesignLiveAdapters[adapterId] = Object.freeze(adapter);
})();
