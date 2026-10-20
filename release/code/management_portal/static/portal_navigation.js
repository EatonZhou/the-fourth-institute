(() => {
  "use strict";

  if (window.PortalNavigation) return;

  const resolveRoot = root => typeof root === "string" ? document.querySelector(root) : root;

  function lineageViews() {
    return [
      {
        id: "agents",
        markup: window.PortalI18n.html`<div class="paneHead"><div class="paneTitle">Lineages</div><div class="paneHeadActions agentsToolbar"><button class="portalButton is-icon is-quiet iconBtn paneToggle" data-portal-icon="chevron-left" type="button" data-side-collapse-toggle aria-label="隐藏左侧栏" title="隐藏左侧栏"></button><button class="portalButton is-icon is-quiet iconBtn" data-portal-icon="refresh" id="refreshBtn" type="button" aria-label="刷新 Lineages" title="刷新"></button><button class="portalButton is-icon is-quiet iconBtn" data-portal-icon="search" id="searchToggleBtn" type="button" aria-label="搜索 Lineages" aria-expanded="false" title="搜索"></button><button class="portalButton is-icon is-quiet iconBtn" data-portal-icon="filter" id="filterToggleBtn" type="button" aria-label="筛选 Lineages" aria-expanded="false" title="筛选"></button><div class="filterPopover" id="agentFilterPanel" aria-hidden="true"></div></div></div><div class="searchWrap" id="agentSearchWrap"><div data-portal-search-field><label for="searchInput">搜索 Lineage</label><input data-portal-search-input id="searchInput" type="search" placeholder="搜索 Lineage 或项目名称" autocomplete="off" /></div></div><div class="portalFilterSummarySlot sideFilterSummary" id="agentFilterSummary"></div><div class="paneBody" id="agentList"></div>`,
      },
      {
        id: "chatrooms",
        markup: window.PortalI18n.html`<div class="paneHead"><div class="paneTitle">Chatroom</div><div class="paneHeadActions agentsToolbar"><button class="portalButton is-icon is-quiet iconBtn paneToggle" data-portal-icon="chevron-left" type="button" data-side-collapse-toggle aria-label="隐藏左侧栏" title="隐藏左侧栏"></button><button class="portalButton is-icon is-quiet iconBtn" data-portal-icon="refresh" id="chatroomRefreshBtn" type="button" aria-label="刷新 Chatroom" title="刷新"></button><button class="portalButton is-icon is-quiet iconBtn" data-portal-icon="plus" id="chatroomCreateBtn" type="button" aria-label="新建聊天室" title="新建聊天室"></button></div></div><div class="paneBody" id="chatroomList"></div>`,
      },
      {
        id: "favorites",
        markup: window.PortalI18n.html`<div class="paneHead"><div class="paneTitle">My Favorite</div><div class="paneHeadActions agentsToolbar"><button class="portalButton is-icon is-quiet iconBtn paneToggle" data-portal-icon="chevron-left" type="button" data-side-collapse-toggle aria-label="隐藏左侧栏" title="隐藏左侧栏"></button><button class="portalButton is-icon is-quiet iconBtn" data-portal-icon="refresh" id="favoriteRefreshBtn" type="button" aria-label="刷新 My Favorite" title="刷新"></button></div></div><div class="paneBody" id="favoriteAgentList"></div>`,
      },
      {
        id: "workspaces",
        markup: window.PortalI18n.markup('<div id="personalWorkspaces"></div>'),
      },
    ];
  }

  function normalizeLineageView(value) {
    const requested = String(value || "");
    return lineageViews().some(view => view.id === requested) ? requested : "agents";
  }

  function mount(options = {}) {
    const root = resolveRoot(options.root);
    if (!root) throw new TypeError("PortalNavigation.mount requires root.");
    const views = Array.isArray(options.views)
      ? options.views
      : (options.variant === "lineage" ? lineageViews() : []);
    let activeView = String(options.activeView || views[0]?.id || "");
    root.classList.add("pane", "sidePane", "portalNavigation");
    root.setAttribute("aria-label", options.label || "Navigation");
    root.innerHTML = views.map(view => window.PortalI18n.html`<div class="sideView" data-side-panel="${String(view.id)}">${String(view.markup || "")}</div>`).join("");

    const setView = (view, meta = {}) => {
      const next = views.some(item => String(item.id) === String(view))
        ? String(view)
        : String(views[0]?.id || "");
      activeView = next;
      root.querySelectorAll("[data-side-panel]").forEach(panel => {
        panel.classList.toggle("active", panel.dataset.sidePanel === next);
      });
      if (meta.emit !== false) options.emit?.("view-select", { view: next });
      return next;
    };
    const controller = Object.freeze({
      root,
      setView,
      snapshot: () => ({ activeView }),
      restore(snapshot = {}) { return setView(snapshot.activeView || activeView, { emit: false }); },
      setContext(context = {}) {
        if (context.navigationView) setView(context.navigationView, { emit: false });
      },
      show() { root.dispatchEvent(new CustomEvent("portal:navigation-visible")); },
      destroy() {
        root.replaceChildren();
        root.classList.remove("pane", "sidePane", "portalNavigation");
      },
    });
    setView(activeView, { emit: false });
    return controller;
  }

  const panelPackage = {
    type: "navigation",
    version: 1,
    component: "PortalNavigation",
    presentation: { title: "Navigation", icon: "folder" },
    sizing: { minWidth: 220, minHeight: 140 },
    instances: "single",
    capabilities: ["view-select", "snapshot", "restore"],
    behavior: { canHide: true, canCollapse: true, canMove: true, closable: false },
    context: { channels: ["navigationView"] },
    commands: [{
      id: "select-view",
      title: window.PortalI18n.t("切换 Navigation 视图"),
      capability: "view-select",
      execute: ({ panel, args }) => panel.setView(args?.view),
    }],
    persistence: { schemaVersion: 1 },
    create: options => mount(options),
  };
  window.PortalNavigation = Object.freeze({ mount, normalizeLineageView, panelPackage });
  if (window.PortalWorkbench) {
    if (typeof window.PortalWorkbench.registerPanelPackage !== "function") {
      throw new Error("PortalNavigation requires the PortalWorkbench Panel Package registry.");
    }
    window.PortalWorkbench.registerPanelPackage(panelPackage);
  }
})();
