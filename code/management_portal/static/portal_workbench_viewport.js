(function (global) {
  "use strict";

  if (global.PortalWorkbenchViewport) return;

  // Fitting the workbench to the viewport: the height left below the page
  // header, the offset of a viewport shell, the insets the page asks for,
  // and following window and header resizes.

  const DEPENDENCIES = Object.freeze([
    "options", "root", "shellState",
  ]);

  function createViewport(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalWorkbenchViewport is missing: ${missing.join(", ")}`);
    const {
      options, root, shellState,
    } = dependencies;

    const viewportShell = root.closest(".portalWorkbenchViewportShell");
    const viewportFit = options.fitViewport === true
      ? {}
      : (options.fitViewport && typeof options.fitViewport === "object" ? options.fitViewport : null);
    let viewportResizeFrame = 0;
    let viewportStabilizeFrame = 0;
    const syncViewportHeight = () => {
      if ((!viewportFit && !viewportShell) || shellState.destroyed) return;
      cancelAnimationFrame(viewportResizeFrame);
      viewportResizeFrame = requestAnimationFrame(() => {
        if (viewportShell) {
          const offset = Math.max(0, Math.ceil(viewportShell.getBoundingClientRect().top + window.scrollY));
          viewportShell.style.setProperty("--portal-workbench-shell-offset", `${offset}px`);
        }
        if (!viewportFit) return;
        const top = root.getBoundingClientRect().top;
        const bottom = Math.max(0, Number(viewportFit.bottom || 0));
        const minimum = Math.max(100, Number(viewportFit.minHeight || 320));
        const maximum = Math.max(minimum, Number(viewportFit.maxHeight || Number.MAX_SAFE_INTEGER));
        const available = Math.floor(window.innerHeight - top - bottom);
        const height = Math.max(minimum, Math.min(maximum, available));
        root.style.setProperty("--portal-workbench-viewport-height", `${height}px`);
      });
    };
    const viewportObserver = (viewportFit || viewportShell) && window.ResizeObserver
      ? new window.ResizeObserver(syncViewportHeight) : null;
    if (viewportFit || viewportShell) {
      if (viewportFit) root.dataset.portalWorkbenchFitViewport = "true";
      window.addEventListener("resize", syncViewportHeight, { passive: true });
      window.visualViewport?.addEventListener("resize", syncViewportHeight, { passive: true });
      const header = document.querySelector(".portalSiteHeader");
      if (header) viewportObserver?.observe(header);
    }
    ["top", "right", "bottom", "left"].forEach(edge => {
      const value = Number(options.insets?.[edge] || 0);
      root.style.setProperty(`--portal-workbench-inset-${edge}`, `${Math.max(0, value)}px`);
    });

    // Measures now and once more after the first frame has laid out the page.
    function followViewport() {
      syncViewportHeight();
      viewportStabilizeFrame = requestAnimationFrame(syncViewportHeight);
    }

    function destroyViewport() {
      if (viewportFit || viewportShell) {
        viewportObserver?.disconnect();
        cancelAnimationFrame(viewportResizeFrame);
        cancelAnimationFrame(viewportStabilizeFrame);
        window.removeEventListener("resize", syncViewportHeight);
        window.visualViewport?.removeEventListener("resize", syncViewportHeight);
        delete root.dataset.portalWorkbenchFitViewport;
        root.style.removeProperty("--portal-workbench-viewport-height");
      }
    }

    return {
      destroyViewport, followViewport,
    };
  }

  global.PortalWorkbenchViewport = Object.freeze({ createViewport, dependencies: DEPENDENCIES });
})(window);
