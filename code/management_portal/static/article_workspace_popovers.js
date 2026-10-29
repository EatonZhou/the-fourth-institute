(() => {
  "use strict";

  if (window.PortalArticleWorkspacePopovers) return;

  // The header's popovers: the Project menu, the Article search results and
  // the compile settings, created by initializeArticlePopovers.
  const DEPENDENCIES = Object.freeze([
    "$", "renderArticleResourceSearch", "renderArticleSelectors", "renderCompileMenu", "state",
  ]);

  function createPopovers(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalArticleWorkspacePopovers is missing: ${missing.join(", ")}`);
    const { $, renderArticleResourceSearch, renderArticleSelectors, renderCompileMenu, state } = dependencies;

    let articleProjectPopover = null;
    let articleResourceSearchPopover = null;
    let articleCompilePopover = null;

    function setProjectMenu(open, context) {
      if (open) articleProjectPopover?.open(context);
      else articleProjectPopover?.close(context);
    }

    function setCompileMenu(open, context) {
      if (open && state.selectedId) articleCompilePopover?.open(context);
      else articleCompilePopover?.close(context);
    }

    function setArticleResourceSearchOpen(open, context) {
      if (open) articleResourceSearchPopover?.open(context);
      else articleResourceSearchPopover?.close(context);
    }

    function initializeArticlePopovers() {
      articleProjectPopover = window.PortalOverlay.bindPopover({
        trigger: $("articleProjectButton"),
        panel: $("articleProjectMenu"),
        placement: "bottom-start",
        ariaHaspopup: "dialog",
        closeOnOutside: true,
        closeOnEscape: true,
        onOpen: () => {
          setCompileMenu(false);
          setArticleResourceSearchOpen(false);
          renderArticleSelectors();
          window.setTimeout(() => {
            if (articleProjectPopover?.isOpen()) $("articleMenuSearch").focus();
          }, 0);
        },
      });
      articleResourceSearchPopover = window.PortalOverlay.bindPopover({
        trigger: $("articleResourceQuery"),
        anchor: $("articleResourceSearch"),
        panel: $("articleResourceSearchPopover"),
        placement: "bottom-start",
        ariaHaspopup: "listbox",
        closeOnOutside: true,
        closeOnEscape: true,
        restoreFocusOnEscape: false,
        onOpen: () => {
          state.resourceSearchOpen = true;
          setProjectMenu(false);
          setCompileMenu(false);
          renderArticleResourceSearch();
        },
        onClose: () => {
          state.resourceSearchOpen = false;
        },
      });
      articleCompilePopover = window.PortalOverlay.bindPopover({
        trigger: $("articleCompileSettings"),
        panel: $("articleCompileMenu"),
        placement: "bottom-start",
        ariaHaspopup: "menu",
        closeOnOutside: true,
        closeOnEscape: true,
        onOpen: () => {
          setProjectMenu(false);
          setArticleResourceSearchOpen(false);
          renderCompileMenu();
        },
      });
    }

    return Object.freeze({
      initializeArticlePopovers, setArticleResourceSearchOpen, setCompileMenu, setProjectMenu,
      currentArticleProjectPopover: () => articleProjectPopover,
      currentArticleCompilePopover: () => articleCompilePopover,
    });
  }

  window.PortalArticleWorkspacePopovers = Object.freeze({ createPopovers, dependencies: DEPENDENCIES });
})();
