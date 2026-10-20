(() => {
  "use strict";
  const T = window.PortalTest;
  const { split, stack } = window.PortalWorkbench.model;
  const layouts = Object.freeze({
    catalog: { id: "catalog", name: window.PortalI18n.t("目录与用例"), layout: split("horizontal", [stack(["test-catalog"]), stack(["test-case"])], [360, 900]), hidden: ["run-console", "tau", "gate-composer", "test-review"] },
    execute: { id: "execute", name: window.PortalI18n.t("运行与观察"), layout: split("horizontal", [stack(["test-catalog"]), stack(["run-console"]), stack(["tau"])], [300, 640, 420]), hidden: ["test-case", "gate-composer", "test-review"] },
    agent: { id: "agent", name: "TAU", layout: split("horizontal", [stack(["test-catalog"]), stack(["tau"]), stack(["run-console"])], [300, 520, 620]), hidden: ["test-case", "gate-composer", "test-review"] },
    gates: { id: "gates", name: window.PortalI18n.t("门禁组合"), layout: split("horizontal", [stack(["gate-composer"]), stack(["test-catalog"]), stack(["run-console"])], [520, 360, 600]), hidden: ["test-case", "tau", "test-review"] },
    review: { id: "review", name: window.PortalI18n.t("覆盖审查"), layout: split("horizontal", [stack(["test-review"]), stack(["test-catalog"]), stack(["test-case"])], [520, 320, 600]), hidden: ["run-console", "tau", "gate-composer"] },
  });
  const primaryPanelByView = Object.freeze({
    catalog: "test-catalog",
    execute: "run-console",
    agent: "tau",
    gates: "gate-composer",
    review: "test-review",
  });

  function descriptor(id, type = id, options = {}) {
    const definition = T.panelDefinitions.get(type);
    const titles = { chat: window.PortalI18n.t("共享对话工作区"), preview: window.PortalI18n.t("共享预览") };
    return { id, type, title: definition?.title || titles[type] || type, icon: definition?.icon || (type === "chat" ? "chat" : "window"), options, minWidth: type === "test-catalog" ? 280 : 360, singleton: true, canHide: true };
  }

  async function init(options) {
    const panelTypes = ["test-catalog", "test-case", "run-console", "tau", "gate-composer", "test-review"];
    if (panelTypes.some(type => !T.panelDefinitions.has(type))) throw new Error("测试工作台的面板登记不完整。" );
    const identity = await window.PortalIdentity.load().catch(() => window.PortalIdentity.peek());
    const username = String(identity?.username || "").trim() || "anonymous";
    const transient = username === "anonymous";
    const scopedStore = (kind) => transient
      ? window.PortalWorkbench.storage.createMemoryStore()
      : window.PortalWorkbench.storage.createUserStore({ kind, id: "test", user: username });
    const workbenchStore = scopedStore("workbench");
    const activityRailStore = scopedStore("activity-rail");
    const pageStateStore = scopedStore("page-state");
    T.store.caseViewStore = scopedStore("test-case-state");
    const savedPageState = pageStateStore.read();
    let activeView = layouts[savedPageState.activeView] ? savedPageState.activeView : "catalog";
    if (savedPageState.selectedCaseId) T.store.selectedId = String(savedPageState.selectedCaseId);
    const workbench = window.PortalWorkbench.create({
      id: "test",
      root: "#testWorkbenchRoot",
      visible: options.isVisible?.() !== false,
      label: window.PortalI18n.t("测试工作台"),
      store: workbenchStore,
      insets: { top: 0, right: 0, bottom: 0, left: 0 },
      initialEditors: panelTypes.map(type => descriptor(type)),
      editorTypes: [{ type: "chat", title: window.PortalI18n.t("共享对话工作区"), icon: "chat" }, { type: "preview", title: window.PortalI18n.t("共享预览"), icon: "window" }],
      defaultLayout: layouts.catalog.layout,
      presets: Object.values(layouts),
      defaultPreset: "catalog",
      createEditorDescriptor: ({ instanceId, type, resource }) => descriptor(instanceId, type, type === "chat" ? { target: resource, refsScope: "test", title: resource?.title || window.PortalI18n.t("共享对话工作区") } : { resource }),
      restorePanel: spec => descriptor(spec.id, spec.type, { target: spec.binding || {} }),
      editorTitle: (type, resource) => resource?.title || type,
    });
    T.setWorkbench(workbench);
    const rail = window.PortalWorkbench.createActivityRail({
      id: "test",
      root: "#testActivityRail",
      label: window.PortalI18n.t("测试工作台视图"),
      store: activityRailStore,
      activeView,
      views: [
        { id: "catalog", icon: "list", label: window.PortalI18n.t("目录") },
        { id: "execute", icon: "play", label: window.PortalI18n.t("运行") },
        { id: "agent", icon: "chat", label: window.PortalI18n.t("TAU · Agent 真实使用") },
        { id: "gates", icon: "check", label: window.PortalI18n.t("门禁") },
        { id: "review", icon: "search", label: window.PortalI18n.t("审查") },
      ],
      onViewSelect: view => {
        if (!layouts[view]) return;
        activeView = view;
        pageStateStore.write({ activeView });
        workbench.applyWorkspace(layouts[view]);
        T.showPanel(primaryPanelByView[view]);
      },
    });
    // Activity Rail is shared chrome; mount its semantic icons explicitly so
    // every view remains legible even when the asynchronous upgrader is late.
    window.PortalUIPrimitives?.mountIcons?.(rail.root);
    window.PortalTestWorkbench = Object.freeze({ mount, workbench, rail, layouts, primaryPanelByView, openPanel: T.showPanel });
    window.PortalUIPrimitives?.mountIcons?.(document);
    T.store.subscribe((event, detail) => {
      if (event === "selection") pageStateStore.write({ selectedCaseId: String(detail?.caseId || "") });
    });
    try {
      await T.store.load();
      const requestedCase = new URLSearchParams(location.search).get("case");
      T.openCase(T.store.selectedId, { reveal: Boolean(requestedCase && requestedCase !== savedPageState.selectedCaseId) });
      const requestedRun = new URLSearchParams(location.search).get("run");
      if (requestedRun) {
        T.showPanel("run-console");
        await workbench.invokePanel("run-console", "openRun", requestedRun);
      }
    } catch (error) {
      T.notify(`测试目录不可用：${error.message}`, "error");
    }
  }

  let mounting = null;
  function mount(options = {}) {
    if (!mounting) mounting = init(options).catch(error => {
      document.querySelector("#testWorkbenchRoot").innerHTML = window.PortalI18n.html`<div class="testEmpty">测试工作台初始化失败：${T.escapeHtml(error.message)}</div>`;
      throw error;
    });
    return mounting;
  }
  window.PortalTestWorkbench = Object.freeze({ mount });
  window.PortalDevelopmentWorkspace?.registerView("test", {
    activate: mount,
    setVisible: visible => window.PortalTestWorkbench.workbench?.setVisible(visible),
    destroy() { window.PortalTestWorkbench.workbench?.destroy(); window.PortalTestWorkbench.rail?.destroy(); },
  });
})();
