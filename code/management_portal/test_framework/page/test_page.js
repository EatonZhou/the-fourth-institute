(() => {
  "use strict";

  // The Test view of the development projection page (decision_f2a6a5dd4646).
  // The address is its only state: /development?view=test shows the overview,
  // page=cases|runs|plan|tau a collection, case=<ID> a case and run=<ID> a
  // run.  Beside every page sits the conversation with the Test team, the
  // facade the `test` workbench binds; a TAU module's Resident can take its
  // place for a while.
  const T = window.PortalTest;
  const t = value => window.PortalI18n.t(value);
  const esc = T.escapeHtml;

  const TABS = Object.freeze([
    { id: "overview", label: t("总览"), href: () => T.href() },
    { id: "cases", label: t("用例"), href: () => T.catalogHref() },
    { id: "runs", label: t("运行"), href: () => T.pageHref("runs") },
    { id: "tau", label: "TAU", href: () => T.pageHref("tau") },
  ]);
  const FACTORIES = Object.freeze({
    overview: () => window.PortalTestOverview.create,
    cases: () => window.PortalTestCatalog.create,
    case: () => window.PortalTestCase.create,
    run: () => window.PortalTestRun.create,
    runs: () => window.PortalTestRun.createList,
    plan: () => window.PortalTestPlan.create,
    tau: () => window.PortalTestTau.create,
  });

  let shell = null;
  let mounting = null;
  let generation = 0;

  function create(host, options) {
    const page = host.querySelector("#testPage");
    page.innerHTML = window.PortalI18n.html`<div class="testPageMain" id="testPageMain">
        <nav class="testTabs" aria-label="Test">${TABS.map(tab => `<a href="${esc(tab.href())}" data-test-tab="${esc(tab.id)}">${esc(tab.label)}</a>`).join("")}</nav>
        <div class="testPageView" id="testPageView"></div>
      </div>
      <aside class="testPageChat" id="testPageChat" hidden>
        <header class="testPageChatHead">
          <span class="testPageChatTitle" id="testPageChatTitle"></span>
          <button class="portalButton is-compact is-quiet" type="button" data-chat-back hidden>回到测试团队</button>
          <button class="portalButton is-compact" type="button" data-chat-toggle aria-expanded="true" data-portal-icon="chat"><span>收起对话</span></button>
        </header>
        <div class="testPageChatBody" id="testPageChatBody"></div>
      </aside>`;
    window.PortalUIPrimitives?.mountIcons?.(page);
    const main = page.querySelector("#testPageMain");
    const viewHost = page.querySelector("#testPageView");
    const aside = page.querySelector("#testPageChat");
    let view = null;
    let shown = "";
    let shownKey = "";
    let routing = 0;
    let visible = options.isVisible?.() !== false;
    let team = null;
    let chat = null;
    let chatTarget = null;
    let disposed = false;
    const chatPreferences = window.PortalPageState?.create?.("test-chat", {
      user: () => window.PortalIdentity?.username?.() || "anonymous", defaults: { collapsed: false },
    });
    const collapsed = () => Boolean(chatPreferences?.read?.().collapsed);

    function navigate(url, { replace = false } = {}) {
      if (disposed) return;
      window.history[replace ? "replaceState" : "pushState"]({}, "", url);
      void route();
    }

    function kindOf(address) {
      if (address.runId) return ["run", address.runId];
      if (address.caseId) return ["case", address.caseId];
      if (["cases", "runs", "plan", "tau"].includes(address.page)) return [address.page, address.page];
      return ["overview", "overview"];
    }

    function container(kind) {
      if (shown === kind && view) return view;
      view?.destroy?.();
      viewHost.replaceChildren();
      const root = document.createElement("div");
      root.className = "testPageViewRoot";
      root.dataset.testView = kind;
      viewHost.append(root);
      shown = kind;
      page.dataset.view = kind;
      view = FACTORIES[kind]()({ root });
      return view;
    }

    function markTab(kind) {
      const tab = { overview: "overview", cases: "cases", case: "cases", runs: "runs", run: "runs", plan: "cases", tau: "tau" }[kind];
      page.querySelectorAll("[data-test-tab]").forEach(link => {
        if (link.dataset.testTab === tab) link.setAttribute("aria-current", "page");
        else link.removeAttribute("aria-current");
      });
    }

    async function route() {
      const current = ++routing;
      const address = T.addressed();
      const [kind, key] = kindOf(address);
      const changed = kind !== shown || key !== shownKey;
      markTab(kind);
      const target = container(kind);
      shownKey = key;
      if (changed) main.scrollTop = 0;
      document.title = `${{ overview: t("Test"), cases: t("用例目录"), case: t("用例"), run: t("运行"), runs: t("运行记录"), plan: t("新运行"), tau: "TAU" }[kind]} · Portal`;
      try {
        const result = await target.load(kind === "case" ? address.caseId : kind === "run" ? address.runId : address.filters);
        return current === routing ? result : null;
      } catch (error) {
        if (current !== routing || disposed) return null;
        const root = viewHost.querySelector(".testPageViewRoot");
        if (root) root.innerHTML = T.stateHtml("error", kind === "case" ? t("用例读取失败") : kind === "run" ? t("运行读取失败") : t("Test 页面读取失败"), error.message || String(error),
          `<a class="portalButton is-compact" href="${esc(T.href())}">${esc(t("回到总览"))}</a>`);
        return null;
      }
    }

    // Links to another Test page change the address in place.
    function onClick(event) {
      if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      const link = event.target.closest?.("a[href]");
      if (!link || link.target === "_blank" || !page.contains(link)) return;
      const url = new URL(link.href, window.location.href);
      if (url.origin !== window.location.origin || url.pathname !== "/development" || url.searchParams.get("view") !== "test") return;
      event.preventDefault();
      navigate(`${url.pathname}${url.search}`);
    }
    main.addEventListener("click", onClick);

    // The conversation.
    function setCollapsed(value) {
      aside.dataset.collapsed = String(value);
      page.dataset.chat = value ? "collapsed" : "open";
      const toggle = aside.querySelector("[data-chat-toggle]");
      toggle.setAttribute("aria-expanded", String(!value));
      toggle.querySelector("span").textContent = value ? t("展开对话") : t("收起对话");
      chat?.setPresented?.(visible && !value);
    }

    function showChat(target) {
      if (!target?.resident_id) {
        chat?.destroy?.();
        chat = null;
        chatTarget = null;
        aside.hidden = true;
        delete page.dataset.chat;
        return false;
      }
      aside.hidden = false;
      aside.querySelector("#testPageChatTitle").textContent = target.title;
      aside.querySelector("[data-chat-back]").hidden = !team || target.resident_id === team.resident_id;
      if (chatTarget?.resident_id !== target.resident_id) {
        chat?.destroy?.();
        const body = aside.querySelector("#testPageChatBody");
        body.replaceChildren();
        chatTarget = target;
        chat = window.PortalChatWorkspace.panelPackage.create({
          root: body, id: "testTeamChat",
          target: { resident_id: target.resident_id, lineage_id: target.lineage_id || "" },
          title: target.title,
          emptyText: target.emptyText || t("和测试团队讨论：要补哪些用例、为什么失败、下一步验证什么。"),
          submissionContext,
        });
      }
      if (target !== team && collapsed()) chatPreferences?.write?.({ collapsed: false });
      setCollapsed(collapsed());
      return true;
    }

    // What the reader is looking at goes with each message.
    function submissionContext() {
      const address = T.addressed();
      if (address.caseId) return { context_text: window.PortalI18n.text`正在查看测试用例 ${address.caseId}` };
      if (address.runId) return { context_text: window.PortalI18n.text`正在查看测试运行 ${address.runId}` };
      return null;
    }

    async function readTeam() {
      try {
        const result = await window.PortalAPI.get("/api/workbenches/native?name=test");
        const binding = result?.workbench?.bindings?.main || {};
        const residentId = String(binding.residents?.facade || "");
        team = residentId ? { resident_id: residentId, lineage_id: String(binding.lineage_id || ""), title: t("测试团队") } : null;
      } catch (_error) {
        team = null;
      }
      if (!disposed && !chatTarget) showChat(team);
    }

    aside.querySelector("[data-chat-toggle]").addEventListener("click", () => {
      const next = aside.dataset.collapsed !== "true";
      chatPreferences?.write?.({ collapsed: next });
      setCollapsed(next);
    });
    aside.querySelector("[data-chat-back]").addEventListener("click", () => showChat(team));

    T.install({ navigate, openChat: target => showChat(target) });
    void readTeam();

    return {
      route,
      navigate,
      view: () => view,
      setVisible(value) {
        visible = value;
        chat?.setPresented?.(visible && aside.dataset.collapsed !== "true");
      },
      destroy() {
        disposed = true;
        routing += 1;
        main.removeEventListener("click", onClick);
        view?.destroy?.();
        chat?.destroy?.();
        page.replaceChildren();
      },
    };
  }

  function mount(options = {}) {
    if (shell) return shell.route();
    if (!mounting) {
      const host = document.getElementById("wallTestView");
      const current = ++generation;
      mounting = Promise.resolve().then(() => {
        // A view destroyed while it was mounting stays unmounted.
        if (current !== generation) return null;
        if (!host?.querySelector("#testPage")) throw new Error(t("Test 页面模板不完整。"));
        shell = create(host, options);
        window.PortalTestPage = Object.freeze({ mount, route: shell.route, navigate: shell.navigate, view: shell.view });
        return shell.route();
      }).catch(error => {
        if (current !== generation) return null;
        mounting = null;
        shell = null;
        const page = host?.querySelector("#testPage");
        if (page) page.innerHTML = T.stateHtml("error", t("Test 页面初始化失败"), error.message || String(error));
        throw error;
      });
    }
    return mounting;
  }

  function destroy() {
    generation += 1;
    shell?.destroy();
    shell = null;
    mounting = null;
    window.PortalTestPage = Object.freeze({ mount });
  }

  window.PortalTestPage = Object.freeze({ mount });
  window.PortalDevelopmentWorkspace?.registerView("test", {
    activate: mount,
    setVisible: visible => shell?.setVisible(visible),
    destroy,
  });
})();
