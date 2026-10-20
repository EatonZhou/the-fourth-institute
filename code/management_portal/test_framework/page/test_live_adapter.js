(() => {
  "use strict";

  const T = window.PortalTest;
  const workbench = () => T.workbench();
  const panelElement = panelId => document.querySelector(`[data-portal-workbench-panel="${panelId}"]`);
  const afterLayout = () => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));

  function viewButton(view) {
    return document.querySelector(`#testActivityRail [data-activity-view="${CSS.escape(String(view || ""))}"]`);
  }

  const adapter = Object.freeze({
    id: "test-lab",
    version: 1,
    ready() {
      return Boolean(T.store.catalog && workbench()?.getPanel("test-catalog") && panelElement("test-catalog"));
    },
    capture(context = {}) {
      const state = workbench()?.getState?.() || {};
      const activeCase = document.activeElement?.closest?.("[data-case-id]");
      return {
        selectedCaseId: T.store.selectedId,
        focusCaseId: String(activeCase?.dataset?.caseId || context?.caseId || ""),
        scrollY: window.scrollY,
        workspace: {
          name: window.PortalI18n.t("Live 原始状态"),
          layout: state.layout,
          hidden: state.hidden || [],
          collapsed: state.collapsed || [],
        },
      };
    },
    prepare(step, _signal, context = {}) {
      const capability = String(step?.capability || "test.workbench.inspect");
      if (capability === "test.workbench.inspect") {
        const target = document.querySelector("#testWorkbenchRoot");
        if (!target) throw new Error("测试工作台尚未就绪");
        return { target };
      }
      if (capability === "test.catalog.inspect-modules") {
        const target = panelElement("test-catalog")?.querySelector("[data-module]");
        if (!target) throw new Error("测试目录的功能模块筛选尚未就绪");
        return { target };
      }
      if (capability === "test.catalog.open-filters") {
        const target = panelElement("test-catalog")?.querySelector("[data-filter-toggle]");
        if (!target) throw new Error("测试目录的筛选按钮尚未就绪");
        return { target };
      }
      if (capability === "test.workbench.open-case") {
        const caseId = String(context?.caseId || T.store.selectedId || "");
        const target = panelElement("test-catalog")?.querySelector(`[data-case-id="${CSS.escape(caseId)}"]`)
          || panelElement("test-catalog")?.querySelector("[data-case-id]");
        if (!target) throw new Error("测试目录中没有可打开的测试用例");
        return { target };
      }
      if (capability === "test.workbench.select-view") {
        const target = viewButton(step?.value);
        if (!target) throw new Error(`测试工作台视图 ${String(step?.value || "")} 不可用`);
        return { target };
      }
      throw new Error(`未登记的测试工作台演示能力：${capability}`);
    },
    async commit(step, prepared, _signal, context = {}) {
      const target = prepared?.target;
      if (!(target instanceof Element) || !target.isConnected) throw new Error("测试工作台演示目标已经变化");
      const capability = String(step?.capability || "test.workbench.inspect");
      if (capability === "test.workbench.open-case") {
        T.openCase(String(context?.caseId || target.dataset.caseId || T.store.selectedId));
      } else if (capability === "test.workbench.select-view") {
        target.click();
      } else if (capability === "test.catalog.open-filters") {
        if (target.getAttribute("aria-expanded") !== "true") target.click();
      } else if (capability === "test.catalog.inspect-modules") {
        target.focus({ preventScroll: true });
      }
      await afterLayout();
      return {
        target: capability === "test.workbench.select-view"
          ? panelElement("test-catalog")
          : capability === "test.catalog.open-filters"
            ? panelElement("test-catalog")?.querySelector("#testCatalogFilters")
            : target,
      };
    },
    async restore(snapshot = {}) {
      if (snapshot.workspace?.layout) workbench()?.applyWorkspace(snapshot.workspace);
      if (snapshot.selectedCaseId && T.store.case(snapshot.selectedCaseId)) T.openCase(snapshot.selectedCaseId);
      await afterLayout();
      window.scrollTo({ top: Math.max(0, Number(snapshot.scrollY || 0)), behavior: "instant" });
      const focusCaseId = String(snapshot.focusCaseId || "");
      panelElement("test-catalog")?.querySelector(`[data-case-id="${CSS.escape(focusCaseId)}"]`)?.focus?.({ preventScroll: true });
    },
  });

  const player = window.PortalDemoPlayer.create({
    overlay: "testDemoOverlay",
    frame: "testDemoFrame",
    stage: "testDemoStage",
    cursor: "testDemoCursor",
    spotlight: "testDemoSpotlight",
    caption: "testDemoCaption",
    counter: "testDemoCounter",
    progress: "testDemoProgressBar",
    playButton: "testDemoPlayBtn",
    nextButton: "testDemoNextBtn",
    restartButton: "testDemoRestartBtn",
    title: "testDemoTitle",
  });

  async function open(item) {
    const payload = await T.api(`/test/api/demo?case_id=${encodeURIComponent(item.id)}`);
    const caseRecord = { id: payload.case_id, title: payload.title, demo: payload.demo };
    if (!player.open(caseRecord, { originState: adapter.capture({ caseId: item.id }) })) {
      throw new Error("此测试用例没有可播放的 Live 定义");
    }
    return true;
  }

  window.PortalTestLabDemoAdapter = adapter;
  window.PortalTestLive = Object.freeze({ adapter, open, player });
})();
