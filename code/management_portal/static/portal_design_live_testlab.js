(() => {
  "use strict";

  const adapterId = "design-testlab";
  const capabilities = new Set([
    "testlab.filter-intersection", "testlab.design-filter", "testlab.live-entry",
    "testlab.run-evidence", "testlab.gate-scope", "testlab.implementation-detail",
    "testlab.case-navigation",
    "testlab.retained-history", "testlab.console-isolation", "testlab.runner-feedback", "testlab.live-write-guard",
  ]);
  const test = () => window.PortalTest;
  const originalCaseRoot = () => document.querySelector('[data-portal-workbench-panel="test-case"]');
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

  function setValue(root, selector, value, event = "change") {
    const input = element(root, selector);
    requireCondition(!input.disabled, `${selector} 不可操作`);
    if (input.type === "checkbox") input.checked = Boolean(value);
    else input.value = value;
    input.dispatchEvent(new Event(event, { bubbles: true }));
  }

  function formSnapshot(root) {
    return [...(root?.querySelectorAll("input[name], select[name], textarea[name]") || [])].map(input => ({
      name: input.name, type: input.type, value: input.value, checked: input.checked,
    }));
  }

  function restoreForm(root, values) {
    for (const saved of values) {
      const inputs = [...root.querySelectorAll(`[name="${CSS.escape(saved.name)}"]`)];
      const input = inputs.find(candidate => !["checkbox", "radio"].includes(saved.type) || candidate.value === saved.value);
      if (!input) continue;
      input.value = saved.value;
      if (["checkbox", "radio"].includes(saved.type)) input.checked = saved.checked;
    }
  }

  function closeStage() {
    session?.secondary?.destroy?.();
    if (session) session.secondary = null;
    session?.controller?.destroy?.();
    if (session) session.controller = null;
    session?.stage?.remove();
    if (session) session.stage = null;
  }

  function mountPanel(type) {
    closeStage();
    const definition = test().panelDefinitions.get(type);
    requireCondition(definition?.create, `生产面板 ${type} 未登记`);
    const stage = document.createElement("section");
    stage.dataset.designLiveTestlab = type;
    stage.setAttribute("aria-label", window.PortalI18n.t("Test 生产组件只读验证"));
    Object.assign(stage.style, {
      position: "fixed", inset: "12% 8% 16%", zIndex: "1000", overflow: "auto",
      background: "var(--panel)", border: "1px solid var(--border)",
    });
    document.body.appendChild(stage);
    stage.addEventListener("submit", event => {
      event.preventDefault();
      event.stopImmediatePropagation();
    }, true);
    stage.addEventListener("click", event => {
      if (!event.target.closest('[data-run-selected], [data-run-case], [data-open-tau], [data-run-impl], [data-open-source], [data-live], [data-live-discuss], [data-run-gate], [data-open-case], [data-select-case], [data-preview], [data-start], [data-cancel], [data-delete-current], [data-delete-run], [data-delete-selected], [data-tau-run], [data-tau-rotate], [data-tau-chat]')) return;
      event.preventDefault();
      event.stopImmediatePropagation();
    }, true);
    session.stage = stage;
    session.controller = definition.create({ root: stage });
    return stage;
  }

  function activeCases() {
    return test().store.cases.filter(item => item.lifecycle !== "retired");
  }

  function chooseCase(context, predicate = () => true) {
    const requested = test().store.case(context?.caseId);
    const chosen = requested && requested.lifecycle !== "retired" && predicate(requested)
      ? requested : activeCases().find(predicate);
    requireCondition(chosen, "当前 Catalog 缺少此场景可验证的条目");
    return chosen;
  }

  function matches(item, filters) {
    return item.lifecycle !== "retired"
      && (!filters.importance || item.importance === filters.importance)
      && (!filters.module || item.module === filters.module)
      && (filters.design === undefined || item.is_design_point === filters.design)
      && (!filters.aspects?.length || item.aspects.some(aspect => filters.aspects.includes(aspect)))
      && (!filters.liveOnly || Boolean(item.live));
  }

  async function verifyQuery(filters, signal) {
    const root = session.stage;
    await waitUntil(() => element(root, "[data-portal-board-meta]").textContent !== "筛选中…", signal, "Catalog 查询未完成");
    const expected = test().store.cases.filter(item => matches(item, filters)).map(item => item.id);
    const actual = session.controller.visibleCaseIds();
    requireCondition(JSON.stringify(actual) === JSON.stringify(expected), "服务端筛选结果违反分类交集/同维并集或退役排除规则");
    const rows = [...root.querySelectorAll("[data-case-id]")].map(row => row.dataset.caseId);
    requireCondition(JSON.stringify(rows) === JSON.stringify(expected.slice(0, rows.length)), "Catalog 渲染结果与规范 ID 集合不一致");
    requireCondition(rows.length === Math.min(100, expected.length), "Catalog 首批结果丢失或重复");
    requireCondition(element(root, "[data-visible-case-count]").textContent === `已显示 ${rows.length} / ${expected.length} 条`, "Catalog 数量与实际结果不一致");
    requireCondition(!expected.length || expected.includes(test().store.selectedId), "筛选后选择没有迁入可见结果");
    return expected;
  }

  const actions = {
    async "testlab.retained-history"(prepared, signal) {
      const root = prepared.target;
      element(root, '[data-section="history"]').open = true;
      const runs = [...test().store.runs];
      const buttons = [...root.querySelectorAll("[data-open-run]")];
      requireCondition(JSON.stringify(buttons.map(button => button.dataset.openRun)) === JSON.stringify(runs.map(run => run.run_id)), "历史记录被按发起人过滤或丢失");
      const retained = runs.find(run => test().terminalStatuses.has(run.status));
      if (!retained) {
        requireCondition(!root.querySelector('[data-section="current"] [data-run-result]'), "未打开历史时擅自加载了运行");
        requireCondition(element(root, "[data-start]").disabled, "没有预览计划却允许启动");
        return { target: element(root, '[data-section="history"]'), caption: "历史目录未丢失记录；当前没有终态记录可重开，未隐式加载或启动运行。" };
      }
      const currentSession = session;
      const pending = session.controller.openRun(retained.run_id);
      currentSession.pending = pending;
      await pending;
      currentSession.runChange = { before: runs, written: test().store.runs.find(run => run.run_id === retained.run_id) };
      checkSignal(signal);
      const record = currentSession.runChange.written;
      const current = element(root, '[data-section="current"]');
      requireCondition(current.querySelector("[data-status]")?.dataset.status === record.status, "控制台没有采用 Run record 的真实状态");
      requireCondition(current.querySelector("[data-run-raw-output]")?.textContent === (record.output || record.error || window.PortalI18n.t("等待输出…")), "历史输出或错误不一致");
      requireCondition(new URL(location.href).searchParams.get("run") === retained.run_id, "历史打开没有记录唯一 Run ID");
      requireCondition(element(root, "[data-cancel]").disabled && element(root, "[data-start]").disabled, "终态历史暴露了运行或取消动作");
      return { target: current, caption: "已重开保留历史；状态采用 Run record，不根据输出中的 passed 等文字猜测，未创建新 Run。" };
    },

    async "testlab.console-isolation"(prepared) {
      const root = prepared.target;
      const cases = activeCases();
      requireCondition(cases.length >= 2, "实例隔离验证需要两个不同的已登记用例");
      const secondRoot = document.createElement("section");
      root.appendChild(secondRoot);
      const first = session.controller;
      const second = test().panelDefinitions.get("run-console").create({ root: secondRoot });
      session.secondary = second;
      first.ensureDraft({ caseIds: [cases[0].id], implementation: {} });
      root.appendChild(secondRoot);
      second.ensureDraft({ caseIds: [cases[1].id], implementation: {} });
      first.ensureDraft({ caseIds: [cases[1].id], implementation: {} });
      const firstPlan = root.querySelector('[data-section="plan"]');
      const secondPlan = secondRoot.querySelector('[data-section="plan"]');
      requireCondition(firstPlan.querySelector("ol code")?.textContent === cases[0].key, "ensureDraft 覆盖了已有控制台草稿");
      requireCondition(secondPlan.querySelector("ol code")?.textContent === cases[1].key, "两个控制台实例共享了草稿");
      for (const target of [root, secondRoot]) {
        requireCondition(element(target, "[data-start]").disabled, "实例无计划时允许启动");
        requireCondition(!target.querySelector('[data-section="current"] [data-run-result]'), "实例隐式恢复了其他运行");
      }
      return { target: secondPlan, caption: "两个生产 Run Console 的草稿独立；ensureDraft 不覆盖已有草稿，也不自动恢复历史或启用 Start。" };
    },

    async "testlab.runner-feedback"(prepared) {
      const root = prepared.target;
      const tasks = activeCases().filter(item => test().hasRunner(item, "tau"));
      const inputs = [...root.querySelectorAll("[data-tau-case]")];
      const actual = inputs.map(input => input.dataset.tauCase).sort();
      requireCondition(JSON.stringify(actual) === JSON.stringify(tasks.map(item => item.id).sort()), "Runner 不可用时任务被丢弃或混入无实现用例");
      element(root, "[data-tau-all]").click();
      requireCondition(root.querySelectorAll("[data-tau-case]:checked").length === tasks.length, "TAU 全选没有选择真实任务");
      const capability = test().store.catalog.capabilities.runners.find(runner => runner.id === "tau");
      requireCondition(Boolean(capability), "Catalog 没有明确声明 TAU Runner 能力");
      const disabled = !test().store.canOperate() || !tasks.length || capability.available === false;
      requireCondition(element(root, "[data-tau-run]").disabled === disabled, "Runner 能力/选择集合没有约束运行按钮");
      if (capability.available === false) {
        requireCondition(Boolean(capability.reason?.trim()), "不可用 Runner 缺少原因");
        requireCondition([...root.querySelectorAll(".testEmpty")].some(node => node.textContent === capability.reason), "不可用原因未投影到界面");
      }
      element(root, "[data-tau-all]").click();
      requireCondition(root.querySelectorAll("[data-tau-case]:checked").length === 0 && element(root, "[data-tau-run]").disabled, "清空后仍能启动 TAU");
      return { target: element(root, ".testTauToolbar"), caption: `已验证 ${tasks.length} 项任务选择和 Runner 能力反馈；没有运行、更换 Resident 或调用模型。` };
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
      const aspects = item.aspects.slice(0, 2);
      const filters = { importance: item.importance, module: item.module, design: item.is_design_point, aspects };
      element(root, "[data-filter-toggle]").click();
      setValue(root, "[data-importance]", item.importance);
      setValue(root, "[data-module]", item.module);
      setValue(root, "[data-design]", String(item.is_design_point));
      for (const aspect of aspects) setValue(root, `[data-aspect="${CSS.escape(aspect)}"]`, true);
      const result = await verifyQuery(filters, signal);
      requireCondition(result.includes(item.id), "分类筛选遗漏种子用例");
      return { target: element(root, ".testCaseList"), caption: `四维 AND、方面 OR 和退役排除已验证；匹配 ${result.length} 项。` };
    },

    async "testlab.design-filter"(prepared, signal) {
      const root = prepared.target;
      element(root, "[data-filter-toggle]").click();
      for (const design of [true, false]) {
        setValue(root, "[data-design]", String(design));
        await verifyQuery({ design }, signal);
      }
      setValue(root, "[data-design]", "");
      setValue(root, "[data-live-only]", true);
      await verifyQuery({ liveOnly: true }, signal);
      setValue(root, "[data-live-only]", false);
      await verifyQuery({}, signal);
      return { target: element(root, ".testCaseList"), caption: "设计点/非设计点/有 Live 的结果均符合 Catalog；清除后恢复全部非退役条目。" };
    },

    async "testlab.live-entry"(prepared, signal) {
      const { target: root, item } = prepared;
      const section = element(root, '[data-section="live"]');
      section.open = true;
      if (!item.live) {
        requireCondition(!section.querySelector("[data-live]"), "无 Live 定义的用例仍暴露播放按钮");
        requireCondition(Boolean(section.querySelector(".testEmpty")?.textContent.trim()), "缺少未登记 Live 的反馈");
        return { target: section, caption: "未登记 Live 的条目没有可启动入口，且提供不可用反馈。" };
      }
      requireCondition(!element(section, "[data-live]").disabled, "已登记 Live 的入口被错误禁用");
      const descriptor = await test().api(`/test/api/demo?case_id=${encodeURIComponent(item.id)}`, { signal });
      checkSignal(signal);
      requireCondition(descriptor.case_id === item.id, "Live 读取了其他用例的定义");
      const live = descriptor.demo;
      requireCondition(live && live.target === item.live.target && live.mode === item.live.mode, "Live 入口与登记目标/模式不一致");
      const target = new URL(live.target, location.href);
      requireCondition(target.origin === location.origin && live.target.startsWith("/") && !live.target.startsWith("//"), "Live 指向站外地址");
      if (live.mode === "direct") {
        requireCondition(live.risk === "read-only" && live.adapter && live.adapter_version === 1, "真实 Live 缺少只读适配器契约");
        requireCondition(Array.isArray(live.direct_steps) && live.direct_steps.length > 0 && live.direct_steps.every(step => step.capability && step.caption), "Live 步骤缺少可执行能力或验收说明");
        requireCondition(live.adapter === item.live.adapter && JSON.stringify(live.direct_steps) === JSON.stringify(item.live.direct_steps), "Live 入口与 Catalog 的执行步骤不一致");
      } else {
        requireCondition(live.mode === "sandbox" && Array.isArray(live.steps) && live.steps.length > 0, "沙盒 Live 没有可执行步骤");
      }
      return { target: section, caption: "Live 入口已读取并核对同一用例的只读执行定义；不递归启动另一个播放器，也不生成 Run。" };
    },

    async "testlab.run-evidence"(prepared) {
      const { target: root, item } = prepared;
      const section = element(root, '[data-section="evidence"]');
      section.open = true;
      const expected = test().store.runs.filter(run => (run.plan?.selected || []).some(entry => entry.case_id === item.id));
      const cards = [...section.querySelectorAll(".testRunCard")];
      requireCondition(cards.length === expected.length, "运行证据混入其他用例或漏掉保留记录");
      expected.forEach((run, index) => {
        const card = cards[index];
        requireCondition(card.querySelector("[data-status]")?.dataset.status === run.status, "运行证据状态与 Run record 不一致");
        requireCondition(card.querySelector("span")?.textContent === ` · ${run.run_id}`, "运行证据身份不匹配");
        requireCondition(card.querySelector("pre")?.textContent === (run.output || run.error || "暂无输出"), "输出或错误没有原样投影");
        requireCondition(!card.querySelector("pre > *"), "运行输出被当作 HTML 执行");
      });
      if (!expected.length) requireCondition(Boolean(section.querySelector(".testEmpty")), "没有运行记录时缺少空态");
      return { target: section, caption: `已按权威 RunPlan 的 case ID 核对 ${expected.length} 条证据；状态和输出保持只读。` };
    },

    async "testlab.gate-scope"(prepared, signal) {
      const root = prepared.target;
      const predicates = {
        critical: item => item.importance === "P0",
        design: item => item.is_design_point,
        "test-workbench": item => ["operations.test-framework", "operations.test-workbench"].includes(item.module),
      };
      for (const [gateId, predicate] of Object.entries(predicates)) {
        const expected = activeCases().filter(predicate).map(item => item.id);
        const button = element(root, `[data-run-gate="${gateId}"]`);
        requireCondition(button.disabled === !expected.length, `${gateId} 的空范围可执行性错误`);
        requireCondition(button.closest(".testGateCard").querySelector(".testGateCount")?.textContent === `${expected.length} 条`, `${gateId} 的计数错误`);
        if (!expected.length) continue;
        element(root, `[data-gate-scope="${gateId}"] > summary`).click();
        const expand = root.querySelector(`[data-toggle-gate-scope="${gateId}"]`);
        if (expand) expand.click();
        await settled(signal);
        const scope = element(root, `[data-gate-scope="${gateId}"]`);
        requireCondition(scope.open, `${gateId} 的范围未展开`);
        const links = [...scope.querySelectorAll("[data-open-case]")];
        requireCondition(JSON.stringify(links.map(link => link.dataset.openCase)) === JSON.stringify(expected), `${gateId} 的实际范围与模板不一致`);
        requireCondition(links.every(link => new URL(link.href).searchParams.get("case") === link.dataset.openCase), "Gate 范围跳转丢失 canonical case ID");
      }
      return { target: root, caption: "三个 Gate 模板的计数、完整范围和入口一致，均排除退役条目；没有提交计划或启动运行。" };
    },

    async "testlab.implementation-detail"(prepared) {
      const { target: root, item } = prepared;
      element(root, '[data-section="implementation"]').open = true;
      const expected = item.implementations;
      const cards = [...root.querySelectorAll(".testImplementation")];
      requireCondition(cards.length === expected.length, "详情中的实现数量错误");
      expected.forEach((implementation, index) => {
        const card = cards[index];
        requireCondition(card.querySelector(".testImplementationPath code")?.textContent === implementation.entrypoint, "实现入口串到了其他用例");
        requireCondition(card.querySelector("[data-open-source]")?.dataset.openSource === implementation.source, "实现源码来源错误");
        requireCondition(card.querySelector("[data-open-source]")?.dataset.implementationId === implementation.id, "源码入口未绑定当前实现身份");
        requireCondition(card.textContent.includes(test().riskLabel(implementation.risk || "unspecified")), "详情没有显示实现自身的风险");
        requireCondition((implementation.profiles || []).every(profile => card.textContent.includes(profile)), "详情遗漏实现 Profile");
      });
      const selected = expected[expected.length - 1];
      element(root, `input[name="implementation"][value="${CSS.escape(selected.id)}"]`).click();
      session.implementationChanges.set(item.id, selected.id);
      const effectiveSelection = test().store.selectedImplementation.get(item.id) || expected[0]?.id;
      requireCondition(effectiveSelection === selected.id, "单选没有选中当前实现");
      const checked = root.querySelectorAll('input[name="implementation"]:checked');
      requireCondition(checked.length === 1 && checked[0].value === selected.id, "实现详情没有保持互斥选择");
      return { target: element(root, '[data-section="implementation"]'), caption: "实现入口、源码身份、独立风险与 Profile 一致；默认实现单选仅改变本地选择。" };
    },

    async "testlab.case-navigation"(prepared, signal) {
      const { target: root, item } = prepared;
      const previousUrl = new URL(location.href);
      const historyLength = history.length;
      test().openCase(item.id, { reveal: false });
      session.controller.setCase(item.id);
      await settled(signal);
      const currentUrl = new URL(location.href);
      requireCondition(test().store.selectedId === item.id && currentUrl.searchParams.get("case") === item.id, "选择和 canonical case URL 未同步");
      requireCondition(history.length === historyLength, "选择用例错误地增加浏览器历史层级");
      previousUrl.searchParams.set("case", item.id);
      requireCondition(currentUrl.href === previousUrl.href, "用例选择丢失了其他导航参数或锚点");
      requireCondition(element(root, ".testCaseKey code").textContent === item.key, "详情未跟随用例选择");
      const validSelection = test().store.selectedId;
      test().openCase("unknown-test-live-id", { reveal: false });
      requireCondition(test().store.selectedId === validSelection && location.href === currentUrl.href, "无效 case ID 覆盖了有效导航");
      return { target: element(root, ".testCaseHero"), caption: "用例身份、详情和 URL 同步且只 replaceState；保留其他导航参数，拒绝无效 ID。" };
    },
  };

  const adapter = {
    id: adapterId,
    version: 1,
    ready() {
      return Boolean(test()?.store.catalogStatus === "loaded" && test()?.workbench() && originalCaseRoot());
    },
    capture() {
      requireCondition(!session, "已有 Test Live 在执行，不能重叠捕获");
      requireCondition(adapter.ready(), "测试工作台尚未就绪");
      const root = originalCaseRoot();
      const focus = document.activeElement;
      const username = window.PortalIdentity?.peek?.()?.username;
      const pageKey = username ? window.PortalWorkbench.storage.userStorageKey("page-state", "test", username) : null;
      session = {
        aborter: new AbortController(), controller: null, stage: null, secondary: null, pending: null, runChange: null,
        selectedId: test().store.selectedId,
        implementations: [...test().store.selectedImplementation], selection: [...test().store.selection], implementationChanges: new Map(),
        url: location.href, historyState: history.state, pageKey,
        pageState: pageKey ? localStorage.getItem(pageKey) : null,
        fields: formSnapshot(root),
        sections: [...root.querySelectorAll("[data-section]")].map(node => [node.dataset.section, node.open]),
        scrollTop: root.querySelector(".testPanelBody")?.scrollTop || 0,
        scroll: [window.scrollX, window.scrollY], focus,
        focusName: root.contains(focus) ? focus?.name : "",
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
      let item = chooseCase(context, candidate => Boolean(candidate) && (
        capability !== "testlab.implementation-detail" || candidate.runners?.length > 0 || candidate.implementations?.length > 0
      ));
      const type = capability.includes("filter") ? "test-catalog" : capability === "testlab.gate-scope" ? "gate-composer" : capability === "testlab.runner-feedback" ? "tau" : ["testlab.retained-history", "testlab.console-isolation"].includes(capability) ? "run-console" : "test-case";
      const target = mountPanel(type);
      if (type === "test-case") {
        await test().store.ensureCaseDetail(item.id);
        checkSignal(signal);
        item = test().store.case(item.id);
        session.controller.setCase(item.id);
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
        const root = originalCaseRoot();
        const runChange = snapshot.runChange;
        if (runChange?.written && test().store.runs.find(run => run.run_id === runChange.written.run_id) === runChange.written) {
          const index = runChange.before.findIndex(run => run.run_id === runChange.written.run_id);
          test().store.runs = test().store.runs.filter(run => run !== runChange.written);
          if (index >= 0) test().store.runs.splice(index, 0, runChange.before[index]);
        }
        const previousImplementations = new Map(snapshot.implementations);
        snapshot.implementationChanges.forEach((written, key) => {
          if (test().store.selectedImplementation.get(key) !== written) return;
          if (previousImplementations.has(key)) test().store.selectedImplementation.set(key, previousImplementations.get(key));
          else test().store.selectedImplementation.delete(key);
        });
        if (test().store.selectedId !== snapshot.selectedId) test().openCase(snapshot.selectedId, { reveal: false });
        history.replaceState(snapshot.historyState, "", snapshot.url);
        if (snapshot.pageKey) {
          if (snapshot.pageState === null) localStorage.removeItem(snapshot.pageKey);
          else localStorage.setItem(snapshot.pageKey, snapshot.pageState);
        }
        restoreForm(root, snapshot.fields);
        snapshot.sections.forEach(([name, open]) => {
          const section = root.querySelector(`[data-section="${CSS.escape(name)}"]`);
          if (section) section.open = open;
        });
        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
        const body = root.querySelector(".testPanelBody");
        if (body) body.scrollTop = snapshot.scrollTop;
        window.scrollTo({ left: snapshot.scroll[0], top: snapshot.scroll[1], behavior: "instant" });
        const focus = snapshot.focus?.isConnected ? snapshot.focus : snapshot.focusName ? root.querySelector(`[name="${CSS.escape(snapshot.focusName)}"]`) : null;
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
