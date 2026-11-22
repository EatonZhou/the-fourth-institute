(() => {
  "use strict";

  if (window.PortalArticleWorkspaceLayout) return;

  // Compose Article tool packages, source and output in the shared Workbench.
  const DEPENDENCIES = Object.freeze([
    "$", "applyEditorPreferences", "applyWordWrap", "articleLayoutStore", "EDITOR_PREFERENCE_KEY",
    "loadArticleChat", "loadArticleGit", "normalizedEditorPreferences", "panelOptions",
    "setSettingsSection", "showToast", "state", "WORD_WRAP_STORAGE_KEY",
  ]);

  function createLayout(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalArticleWorkspaceLayout is missing: ${missing.join(", ")}`);
    const {
      $, applyEditorPreferences, applyWordWrap, articleLayoutStore, EDITOR_PREFERENCE_KEY, loadArticleChat,
      loadArticleGit, normalizedEditorPreferences, panelOptions, setSettingsSection, showToast,
      state, WORD_WRAP_STORAGE_KEY,
    } = dependencies;

    let articleWorkbench = null;
    state.visibleTools = new Set();

    function syncActivityRail() {
      document.querySelectorAll("[data-article-view]").forEach(button => {
        const visible = state.visibleTools.has(button.dataset.articleView);
        button.classList.toggle("active", visible);
        button.setAttribute("aria-pressed", String(visible));
      });
    }

    function activateTool(target) {
      if (target === "chat") return loadArticleChat();
      if (target === "git") return loadArticleGit({ includeHistory: true });
      if (target === "search") $("articleGlobalSearchInput").focus();
      if (target === "settings") setSettingsSection(state.settingsSection);
      if (target === "favorites") return window.PortalResourceFavorites?.refresh();
    }

    function toolStackFor(node, panelId) {
      if (node?.type === "stack") {
        const toolIds = window.PortalArticleWorkspaceTools.definitions.map(tool => tool.id);
        return node.children.some(child => child.panelId === panelId)
          && node.children.every(child => toolIds.includes(child.panelId)) ? node : null;
      }
      for (const child of node?.children || []) {
        const found = toolStackFor(child, panelId);
        if (found) return found;
      }
      return null;
    }

    function setSideView(view, { forceOpen = true, toggle = false } = {}) {
      const target = ["files", "favorites", "search", "git", "chat", "settings", "library"].includes(view) ? view : "files";
      state.sideView = target;
      articleLayoutStore.write({ sideView: target });
      if (!forceOpen) return;
      if (toggle && state.visibleTools.has(target)) {
        // Keep a tool group's position and width, including independently docked
        // groups. A mixed editor/tool group must retain its content panels.
        return toolStackFor(articleWorkbench.getLayout(), target)
          ? articleWorkbench.collapsePanel(target) : articleWorkbench.hidePanel(target);
      }
      articleWorkbench?.focusPanel(target);
      return articleWorkbench?.getPanel(target)?.activate();
    }

    function setToolVisible(visible, view = state.sideView) {
      if (visible) return setSideView(view);
      articleWorkbench?.hidePanel(view);
    }

    // The leftmost tool group is the sidebar. Independently placed tools
    // (for example Chat on the right) and groups containing source/PDF stay put.
    function sidebarStack() {
      let node = articleWorkbench?.getLayout();
      while (node?.type === "split") node = node.children[0];
      return toolStackFor(node, node?.activePanelId);
    }

    function canToggleArticleSidebar() {
      const root = $("articleWorkspaceGrid");
      return Boolean(root?.isConnected && root.dataset.portalWorkbenchId && !root.closest("[hidden]")
        && !articleWorkbench?.isLayoutLocked() && sidebarStack());
    }

    function toggleArticleSidebar() {
      if (!canToggleArticleSidebar()) return false;
      const stack = sidebarStack(), focused = document.activeElement;
      const collapsed = articleWorkbench.isPanelCollapsed(stack.activePanelId);
      articleWorkbench.toggleCollapsed(stack.activePanelId);
      if (!collapsed && focused && !focused.getClientRects().length) {
        document.querySelector(`[data-article-view="${stack.activePanelId}"]`)?.focus();
      } else if (collapsed) {
        // Tool activation may focus Search. Restoring the sidebar while typing
        // in source must leave the writer's caret in that source editor.
        Promise.resolve().then(() => {
          if (focused?.isConnected && focused.getClientRects().length) focused.focus({ preventScroll: true });
        });
      }
      return true;
    }

    function initArticleWorkbench() {
      const { split, stack } = window.PortalWorkbench.model;
      const tools = window.PortalArticleWorkspaceTools;
      tools.register();
      window.PortalArticleWorkspaceMonaco.register();
      window.PortalArticleWorkspacePdf.register();
      const panels = panelOptions();
      const toolIds = tools.definitions.map(tool => tool.id);
      const preferred = articleLayoutStore.read().sideView || "files";
      const writing = split("horizontal", [
        stack(toolIds, preferred),
        stack(["editor"]),
        stack(["output"]),
      ], [308, 720, 560]);
      const focus = split("horizontal", [stack(toolIds, preferred), stack(["editor"])], [308, 980]);
      const proof = split("horizontal", [stack(["editor"]), stack(["output"])], [720, 720]);
      articleWorkbench = window.PortalWorkbench.create({
        id: "article-workspace",
        root: "#articleWorkspaceGrid",
        label: window.PortalI18n.t("Article Workbench"),
        insets: { left: 58 },
        initialEditors: [
          ...tools.definitions.map(tool => ({
            id: tool.id, type: `article-${tool.id}`, title: window.PortalI18n.t(tool.title), icon: tool.icon,
            presentation: "navigation", singleton: true, collapseToRail: true,
            options: {
              source: document.querySelector(`[data-article-panel="${tool.id}"]`),
              controller: {
                activate: () => activateTool(tool.id),
                visibility: visible => {
                  if (visible) state.visibleTools.add(tool.id);
                  else state.visibleTools.delete(tool.id);
                  syncActivityRail();
                },
                destroy: () => { if (tool.id === "chat") state.chatPanel?.destroy?.(); },
                onError: error => showToast(error.message || error, "error"),
              },
            },
          })),
          { id: "editor", type: "article-editor", presentation: "editor", options: panels.editor },
          { id: "output", type: "article-output", presentation: "document", options: panels.output, title: window.PortalI18n.t("PDF / Build") },
        ],
        defaultLayout: writing,
        migrateState: saved => tools.migrateState(saved, articleLayoutStore.read().sideView),
        presets: [
          { id: "writing", name: window.PortalI18n.t("写作"), layout: writing },
          { id: "focus", name: window.PortalI18n.t("专注编辑"), layout: focus, hidden: ["output"] },
          { id: "proof", name: window.PortalI18n.t("校对"), layout: proof, hidden: toolIds },
        ],
        defaultPreset: "writing",
      });
      $("articleSidePane").hidden = true;

    }

    function bindSplitters() {
      window.PortalWorkbench.layout.bindSplitter({
        handle: $("articleOutlineSplitter"),
        axis: "y",
        bodyClass: "articleOutlineResizing",
        min: 80,
        max: () => {
          const panel = document.querySelector('[data-article-panel="files"]');
          const header = panel.querySelector(".articleFileTreeHead");
          return panel.getBoundingClientRect().height - header.offsetHeight - $("articleOutlineSplitter").offsetHeight - 72;
        },
        readSize: () => $("articleFileTree").getBoundingClientRect().height,
        applySize: fileHeight => {
          $("articleFileTree").style.flex = `0 0 ${Math.round(fileHeight)}px`;
          $("articleOutlineSection").style.flex = "1 1 auto";
        },
        store: articleLayoutStore,
        stateKey: "fileTreeHeight",
      });
    }

    function restoreLayout({ sideView } = {}) {
      let restoredSideView = sideView;
      try {
        const layout = articleLayoutStore.read();
        if (restoredSideView === undefined) restoredSideView = layout.sideView;
        if (Number(layout.fileTreeHeight) >= 80) {
          $("articleFileTree").style.flex = `0 0 ${Number(layout.fileTreeHeight)}px`;
          $("articleOutlineSection").style.flex = "1 1 auto";
        }
        state.wordWrap = window.PortalState.storage.readText(WORD_WRAP_STORAGE_KEY, "") !== "0";
        const storedPreferences = window.PortalState.storage.readJson(EDITOR_PREFERENCE_KEY, {});
        state.editorPreferences = normalizedEditorPreferences(storedPreferences);
      } catch (_) {}
      applyEditorPreferences();
      applyWordWrap();
      if (sideView !== undefined) return setSideView(sideView);
      state.sideView = window.PortalArticleWorkspaceTools.definitions.some(tool => tool.id === restoredSideView) ? restoredSideView : "files";
      // Each restored stack already knows its active tab. Do not let the last
      // activity-rail click override the user's saved tab selection.
      return Promise.all([...state.visibleTools].map(id => articleWorkbench.getPanel(id).activate()));
    }

    return Object.freeze({
      bindSplitters, initArticleWorkbench, restoreLayout, setToolVisible, setSideView,
      canToggleArticleSidebar, toggleArticleSidebar,
      currentArticleWorkbench: () => articleWorkbench,
    });
  }

  window.PortalArticleWorkspaceLayout = Object.freeze({ createLayout, dependencies: DEPENDENCIES });
})();
