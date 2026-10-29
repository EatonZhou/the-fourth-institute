(() => {
  "use strict";

  // Live demos of the Test page itself (adapter "test-lab") and the player
  // the case page opens.  The adapter drives the page through its addresses:
  // each step opens the page it shows, and restore() returns to the address,
  // scroll position and focus that were current before the demo.
  const T = window.PortalTest;
  const t = value => window.PortalI18n.t(value);
  const main = () => document.getElementById("testPageMain");
  const afterLayout = () => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  // The former workbench views name the page that now holds their content.
  const VIEWS = Object.freeze({
    catalog: { tab: "cases", href: () => T.catalogHref(), target: "[data-catalog-toolbar]" },
    execute: { tab: "runs", href: () => T.pageHref("runs"), target: ".testRunsPage" },
    agent: { tab: "tau", href: () => T.pageHref("tau"), target: ".testTauPage" },
    gates: { tab: "overview", href: () => T.href(), target: '[data-test-section="templates"]' },
    review: { tab: "overview", href: () => T.href(), target: '[data-test-section="coverage"]' },
  });

  async function waitFor(selector, signal, message) {
    const deadline = performance.now() + 10_000;
    for (;;) {
      if (signal?.aborted) throw new DOMException(t("Test Live 已取消"), "AbortError");
      const node = main()?.querySelector(selector);
      if (node?.isConnected && !node.closest("[aria-busy='true']")) return node;
      if (performance.now() > deadline) throw new Error(message);
      await afterLayout();
    }
  }

  async function open(href, selector, signal, message) {
    const current = `${window.location.pathname}${window.location.search}`;
    const wanted = new URL(href, window.location.origin);
    if (current !== `${wanted.pathname}${wanted.search}`) window.PortalTestPage?.navigate(`${wanted.pathname}${wanted.search}`);
    return waitFor(selector, signal, message);
  }

  const adapter = Object.freeze({
    id: "test-lab",
    version: 1,
    ready() {
      return Boolean(T.overview() && main()?.querySelector(".testPageViewRoot"));
    },
    capture(context = {}) {
      const active = document.activeElement?.closest?.("[data-case-id]");
      return {
        address: `${window.location.pathname}${window.location.search}`,
        scrollTop: main()?.scrollTop || 0,
        focusCaseId: String(active?.dataset?.caseId || context?.caseId || ""),
      };
    },
    async prepare(step, signal, context = {}) {
      const capability = String(step?.capability || "test.workbench.inspect");
      if (capability === "test.workbench.inspect") return { target: main() };
      if (capability === "test.catalog.open-filters" || capability === "test.catalog.inspect-modules") {
        const toolbar = await open(T.catalogHref(T.addressed().filters), "[data-catalog-toolbar]", signal, t("用例目录的筛选尚未就绪"));
        return { target: capability === "test.catalog.inspect-modules" ? toolbar.querySelector("[data-catalog-module]") : toolbar };
      }
      if (capability === "test.workbench.open-case") {
        await open(T.catalogHref(T.addressed().filters), "[data-case-id]", signal, t("用例目录中没有可打开的用例"));
        const caseId = String(context?.caseId || "");
        const target = (caseId && main().querySelector(`[data-case-id="${CSS.escape(caseId)}"] [data-open-case]`)) || main().querySelector("[data-open-case]");
        if (!target) throw new Error(t("用例目录中没有可打开的用例"));
        return { target };
      }
      if (capability === "test.workbench.select-view") {
        const view = VIEWS[String(step?.value || "")];
        const target = view && main()?.querySelector(`[data-test-tab="${view.tab}"]`);
        if (!target) throw new Error(window.PortalI18n.text`Test 页面没有 ${String(step?.value || "")} 视图`);
        return { target, view };
      }
      throw new Error(window.PortalI18n.text`未登记的 Test 页面演示能力：${capability}`);
    },
    async commit(step, prepared, signal) {
      const target = prepared?.target;
      if (!(target instanceof Element) || !target.isConnected) throw new Error(t("Test 页面演示目标已经变化"));
      const capability = String(step?.capability || "test.workbench.inspect");
      if (capability === "test.workbench.open-case") {
        target.click();
        return { target: await waitFor(".testCasePage .testHero", signal, t("用例页没有打开")) };
      }
      if (capability === "test.workbench.select-view") {
        await open(prepared.view.href(), prepared.view.target, signal, t("Test 页面没有打开对应内容"));
        const section = main().querySelector(prepared.view.target);
        section?.scrollIntoView?.({ block: "start" });
        return { target: section };
      }
      if (capability === "test.catalog.inspect-modules") target.focus({ preventScroll: true });
      await afterLayout();
      return { target };
    },
    async restore(snapshot = {}) {
      const address = String(snapshot.address || "");
      if (address && address !== `${window.location.pathname}${window.location.search}` && window.PortalTestPage?.navigate) {
        window.PortalTestPage.navigate(address, { replace: true });
      }
      await afterLayout();
      await afterLayout();
      if (main()) main().scrollTop = Math.max(0, Number(snapshot.scrollTop || 0));
      const focusCaseId = String(snapshot.focusCaseId || "");
      if (focusCaseId) main()?.querySelector(`[data-case-id="${CSS.escape(focusCaseId)}"] a`)?.focus?.({ preventScroll: true });
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

  async function openDemo(item) {
    const payload = await T.api(`/test/api/demo?case_id=${encodeURIComponent(item.id)}`);
    const caseRecord = { id: payload.case_id, title: payload.title, demo: payload.demo };
    if (!player.open(caseRecord, { originState: adapter.capture({ caseId: item.id }) })) {
      throw new Error(t("此测试用例没有可播放的 Live 定义"));
    }
    return true;
  }

  window.PortalTestLabDemoAdapter = adapter;
  window.PortalTestLive = Object.freeze({ adapter, open: openDemo, player });
})();
