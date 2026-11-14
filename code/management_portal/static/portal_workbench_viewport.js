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
    let destroyed = false;
    const setLength = (element, name, value) => {
      const length = `${value}px`;
      if (element.style.getPropertyValue(name) !== length) element.style.setProperty(name, length);
    };
    // Rects use viewport coordinates, while style lengths use the element's
    // local CSS pixels. Keep them in the same space under page/CSS zoom.
    const cssScale = (element, rect) => {
      const style = getComputedStyle(element);
      let width = parseFloat(style.width);
      if (style.boxSizing !== "border-box") {
        width += ["paddingLeft", "paddingRight", "borderLeftWidth", "borderRightWidth"]
          .reduce((total, key) => total + (parseFloat(style[key]) || 0), 0);
      }
      return width > 0 && rect.width > 0 ? rect.width / width : 1;
    };
    const syncViewportHeight = () => {
      if ((!viewportFit && !viewportShell) || destroyed || shellState.destroyed || viewportResizeFrame) return;
      viewportResizeFrame = requestAnimationFrame(() => {
        viewportResizeFrame = 0;
        if (destroyed || shellState.destroyed || !root.isConnected || !root.getClientRects().length) return;
        if (viewportShell) {
          const rect = viewportShell.getBoundingClientRect();
          const scale = cssScale(viewportShell, rect);
          const offset = Math.max(0, rect.top + window.scrollY);
          setLength(viewportShell, "--portal-workbench-shell-offset", offset / scale);
          // Measure after mount; viewport units remain the pre-mount fallback.
          setLength(viewportShell, "--portal-workbench-shell-height", Math.max(0, (window.innerHeight - offset) / scale));
        }
        if (!viewportFit) return;
        const rect = root.getBoundingClientRect();
        const bottom = Math.max(0, Number(viewportFit.bottom || 0));
        const minimum = Math.max(100, Number(viewportFit.minHeight || 320));
        const maximum = Math.max(minimum, Number(viewportFit.maxHeight || Number.MAX_SAFE_INTEGER));
        const available = Math.floor((window.innerHeight - rect.top) / cssScale(root, rect) - bottom);
        const height = Math.max(minimum, Math.min(maximum, available));
        setLength(root, "--portal-workbench-viewport-height", height);
      });
    };
    const viewportObserver = (viewportFit || viewportShell) && window.ResizeObserver
      ? new window.ResizeObserver(syncViewportHeight) : null;
    if (viewportFit || viewportShell) {
      if (viewportFit) root.dataset.portalWorkbenchFitViewport = "true";
      window.addEventListener("resize", syncViewportHeight, { passive: true });
      window.visualViewport?.addEventListener("resize", syncViewportHeight, { passive: true });
      window.addEventListener("pageshow", syncViewportHeight);
      document.addEventListener("visibilitychange", syncViewportHeight);
      const header = document.querySelector(".portalSiteHeader");
      if (header) viewportObserver?.observe(header);
      viewportObserver?.observe(viewportShell || root);
    }
    ["top", "right", "bottom", "left"].forEach(edge => {
      const value = Number(options.insets?.[edge] || 0);
      root.style.setProperty(`--portal-workbench-inset-${edge}`, `${Math.max(0, value)}px`);
    });

    function followViewport() {
      syncViewportHeight();
      viewportStabilizeFrame = requestAnimationFrame(syncViewportHeight);
    }

    function destroyViewport() {
      destroyed = true;
      if (viewportFit || viewportShell) {
        viewportObserver?.disconnect();
        cancelAnimationFrame(viewportResizeFrame);
        cancelAnimationFrame(viewportStabilizeFrame);
        window.removeEventListener("resize", syncViewportHeight);
        window.visualViewport?.removeEventListener("resize", syncViewportHeight);
        window.removeEventListener("pageshow", syncViewportHeight);
        document.removeEventListener("visibilitychange", syncViewportHeight);
        delete root.dataset.portalWorkbenchFitViewport;
        root.style.removeProperty("--portal-workbench-viewport-height");
        viewportShell?.style.removeProperty("--portal-workbench-shell-height");
        viewportShell?.style.removeProperty("--portal-workbench-shell-offset");
      }
    }

    return {
      destroyViewport, followViewport,
    };
  }

  global.PortalWorkbenchViewport = Object.freeze({ createViewport, dependencies: DEPENDENCIES });
})(window);
