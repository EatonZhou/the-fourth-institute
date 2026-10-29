(() => {
  "use strict";

  if (window.PortalArticleWorkspaceLayout) return;

  // The Workbench around the editor and output: its panels, the side view
  // shown in the navigation panel, the splitters and the kept layout.
  const DEPENDENCIES = Object.freeze([
    "$", "applyEditorPreferences", "applyWordWrap", "articleLayoutStore", "EDITOR_PREFERENCE_KEY",
    "loadArticleChat", "loadArticleGit", "normalizedEditorPreferences", "scheduleArticlePdfLayout",
    "setSettingsSection", "showToast", "state", "WORD_WRAP_STORAGE_KEY",
  ]);

  function createLayout(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalArticleWorkspaceLayout is missing: ${missing.join(", ")}`);
    const {
      $, applyEditorPreferences, applyWordWrap, articleLayoutStore, EDITOR_PREFERENCE_KEY, loadArticleChat,
      loadArticleGit, normalizedEditorPreferences, scheduleArticlePdfLayout, setSettingsSection, showToast,
      state, WORD_WRAP_STORAGE_KEY,
    } = dependencies;

    let articleWorkbench = null;

    function setSideView(view, { forceOpen = true } = {}) {
      const target = ["files", "search", "git", "chat", "settings", "library"].includes(view) ? view : "files";
      state.sideView = target;
      if (forceOpen && !articleWorkbench?.isPanelVisible("navigation")) articleWorkbench?.showPanel("navigation");
      document.querySelectorAll("[data-article-view]").forEach(button => {
        button.classList.toggle("active", button.dataset.articleView === target);
      });
      document.querySelectorAll("[data-article-panel]").forEach(panel => {
        panel.classList.toggle("active", panel.dataset.articlePanel === target);
      });
      if (target === "chat") return loadArticleChat().catch(error => showToast(error.message || error, "error"));
      if (target === "git") loadArticleGit({ includeHistory: true }).catch(error => showToast(error.message || error, "error"));
      if (target === "search") window.setTimeout(() => $("articleGlobalSearchInput").focus(), 0);
      if (target === "settings") setSettingsSection(state.settingsSection);
    }

    function setNavigationPanelVisible(visible) {
      if (visible) articleWorkbench?.showPanel("navigation");
      else articleWorkbench?.hidePanel("navigation");
    }

    function scheduleArticleEditorLayout() {
      window.cancelAnimationFrame(state.editorLayoutFrame);
      state.editorLayoutFrame = window.requestAnimationFrame(() => {
        state.editorLayoutFrame = 0;
        state.editor?.layout?.();
      });
    }

    function initArticleWorkbench() {
      const { split, stack } = window.PortalWorkbench.model;
      const writing = split("horizontal", [
        stack(["navigation"]),
        stack(["editor"]),
        stack(["output"]),
      ], [308, 720, 560]);
      const focus = split("horizontal", [stack(["navigation"]), stack(["editor"])], [308, 980]);
      const proof = split("horizontal", [stack(["editor"]), stack(["output"])], [720, 720]);
      articleWorkbench = window.PortalWorkbench.create({
        id: "article-workspace",
        root: "#articleWorkspaceGrid",
        label: window.PortalI18n.t("Article Workbench"),
        insets: { left: 58 },
        initialEditors: [
          { id: "navigation", type: "authored", presentation: "navigation", options: { source: "#articleSidePane" }, title: window.PortalI18n.t("Navigation / Chat") },
          { id: "editor", type: "authored", presentation: "editor", options: { source: "#articleEditorPane" } },
          { id: "output", type: "authored", presentation: "document", options: { source: "#articleOutputPane" }, title: window.PortalI18n.t("PDF / Build") },
        ],
        defaultLayout: writing,
        presets: [
          { id: "writing", name: window.PortalI18n.t("写作"), layout: writing },
          { id: "focus", name: window.PortalI18n.t("专注编辑"), layout: focus, hidden: ["output"] },
          { id: "proof", name: window.PortalI18n.t("校对"), layout: proof, hidden: ["navigation"] },
        ],
        defaultPreset: "writing",
        onChange: () => {
          scheduleArticleEditorLayout();
          if (state.outputView === "pdf") scheduleArticlePdfLayout();
        },
      });
      $("articleEditorPane").addEventListener("portal:workbench-resize", scheduleArticleEditorLayout);
      if (window.ResizeObserver) {
        const editorResizeObserver = new ResizeObserver(scheduleArticleEditorLayout);
        editorResizeObserver.observe($("articleEditorPane"));
        state.monacoDisposables.push({ dispose: () => editorResizeObserver.disconnect() });
      }
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

    function restoreLayout() {
      try {
        const layout = articleLayoutStore.read();
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
    }

    return Object.freeze({
      bindSplitters, initArticleWorkbench, restoreLayout, setNavigationPanelVisible, setSideView,
      currentArticleWorkbench: () => articleWorkbench,
    });
  }

  window.PortalArticleWorkspaceLayout = Object.freeze({ createLayout, dependencies: DEPENDENCIES });
})();
