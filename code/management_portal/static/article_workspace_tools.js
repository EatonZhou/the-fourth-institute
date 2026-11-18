(() => {
  "use strict";
  if (window.PortalArticleWorkspaceTools) return;

  // Each Article-bound package owns a live tool subtree and its activation
  // lifecycle. Controllers supply the Article session's domain services;
  // Workbench owns placement, visibility, drag/drop and layout persistence.
  const definitions = Object.freeze([
    { id: "files", title: "文件树", icon: "files", capability: "navigation" },
    { id: "favorites", title: "收藏", icon: "star", capability: "navigation" },
    { id: "search", title: "搜索", icon: "search", capability: "search" },
    { id: "git", title: "源代码管理", icon: "git-branch", capability: "source-control" },
    { id: "chat", title: "聊天", icon: "chat", capability: "conversation" },
    { id: "settings", title: "设置", icon: "settings", capability: "settings" },
    { id: "library", title: "Article 注册管理", icon: "book", capability: "navigation" },
  ]);
  const ids = definitions.map(tool => tool.id);

  function createTool({ source, controller }) {
    if (!(source instanceof HTMLElement) || !controller) throw new TypeError("Article tool requires source and controller");
    const parent = source.parentNode;
    const sibling = source.nextSibling;
    source.classList.add("articleToolPanel", "active");
    let visible = false, destroyed = false, pending = null;
    function activate() {
      if (destroyed) return Promise.resolve(null);
      if (!pending) {
        pending = Promise.resolve().then(() => destroyed ? null : controller.activate?.())
          .catch(error => { controller.onError?.(error); return null; })
          .finally(() => { pending = null; });
      }
      return pending;
    }
    return Object.freeze({
      root: source,
      activate,
      isVisible: () => visible,
      show() { visible = true; controller.visibility?.(true); void activate(); },
      hide() { visible = false; controller.visibility?.(false); },
      destroy() {
        destroyed = true; visible = false;
        controller.visibility?.(false);
        controller.destroy?.();
        if (parent) parent.insertBefore(source, sibling?.parentNode === parent ? sibling : null);
      },
    });
  }

  function register() {
    const registered = new Set(window.PortalWorkbench.registeredPanelPackages().map(pkg => pkg.type));
    for (const tool of definitions) {
      const type = `article-${tool.id}`;
      if (registered.has(type)) continue;
      window.PortalWorkbench.registerPanelPackage({
        type, version: 1, component: `PortalArticleWorkspaceTools.${tool.id}`,
        presentation: { title: window.PortalI18n.t(tool.title), icon: tool.icon },
        sizing: { minWidth: tool.id === "chat" ? window.PortalUIPrimitives.panelSizing.conversationMinWidth : 180, minHeight: 180 },
        instances: "single", capabilities: [tool.capability],
        behavior: { canHide: true, canCollapse: true, canMove: true, closable: false },
        context: { channels: [] }, persistence: { schemaVersion: 1 }, create: createTool,
      });
    }
  }

  // Expand the old leaf in place, retaining splits, sizes and saved layouts.
  function migrateState(saved, preferred = "files") {
    if (!saved?.panelIds?.includes("navigation")) return saved;
    const active = ids.includes(preferred) ? preferred : "files";
    const expand = list => (list || []).flatMap(id => id === "navigation" ? ids : [id]);
    const visit = node => {
      if (!node) return node;
      if (node.type === "panel" && node.panelId === "navigation") return window.PortalWorkbench.model.stack(ids, active);
      if (node.type === "stack") return { ...node,
        activePanelId: node.activePanelId === "navigation" ? active : node.activePanelId,
        children: node.children.flatMap(child => child.panelId === "navigation"
          ? ids.map(id => ({ type: "panel", id: `${child.id}-${id}`, panelId: id })) : [child]),
      };
      return node.children ? { ...node, children: node.children.map(visit) } : node;
    };
    const migrateLayout = value => ({ ...value, layout: visit(value.layout), hidden: expand(value.hidden), collapsed: expand(value.collapsed) });
    const placements = { ...saved.placements };
    if (placements.navigation) {
      ids.forEach(id => { placements[id] = { ...placements.navigation }; });
      delete placements.navigation;
    }
    const panelStates = { ...saved.panelStates };
    delete panelStates.navigation;
    return { ...migrateLayout(saved), panelIds: expand(saved.panelIds), placements, panelStates,
      activeOpenTargetPanelId: saved.activeOpenTargetPanelId === "navigation" ? active : saved.activeOpenTargetPanelId,
      savedWorkspaces: Object.fromEntries(Object.entries(saved.savedWorkspaces || {}).map(([key, value]) => [key, migrateLayout(value)])),
    };
  }
  window.PortalArticleWorkspaceTools = Object.freeze({ definitions, register, migrateState });
})();
