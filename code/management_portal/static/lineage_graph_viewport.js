(() => {
  "use strict";

  if (window.PortalHatgGraphViewport) return;

  // The viewport: its size, the insets a caller and the minimap take from it,
  // the world it shows, and conversion between screen and world points.

  const DEPENDENCIES = Object.freeze([
    "d3", "graphGeometry", "minimap", "number", "options", "root", "scaleExtent", "state", "worldSize",
  ]);

  function createViewport(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalHatgGraphViewport is missing: ${missing.join(", ")}`);
    const {
      d3, graphGeometry, minimap, number, options, root, scaleExtent, state, worldSize,
    } = dependencies;

    function currentViewportSize() {
      return {
        width: Math.max(1, root.clientWidth),
        height: Math.max(1, root.clientHeight),
      };
    }

    function currentViewportInsets(viewport = currentViewportSize()) {
      let configured = {};
      try {
        configured = typeof options.viewportInsets === "function"
          ? options.viewportInsets()
          : options.viewportInsets || {};
      } catch (_error) {
        configured = {};
      }
      // The engine owns the bottom minimap. Fit/focus must keep cards above
      // its actual footprint, including CSS sizing and responsive placement.
      // Do not reserve space when it is hidden or outside this graph root.
      if (minimap && !minimap.hasAttribute("hidden") && minimap.getClientRects().length) {
        const canvasBounds = root.getBoundingClientRect();
        const minimapBounds = minimap.getBoundingClientRect();
        if (minimapBounds.left < canvasBounds.right && minimapBounds.right > canvasBounds.left
          && minimapBounds.top < canvasBounds.bottom && minimapBounds.bottom > canvasBounds.top) {
          configured = { ...configured, bottom: Math.max(number(configured?.bottom, 0),
            canvasBounds.bottom - minimapBounds.top + 10) };
        }
      }
      // An overlay can cover most of a narrow canvas. Preserve its actual
      // occlusion; only constrain opposing insets when they leave no space.
      return graphGeometry.viewportInsets(configured, viewport);
    }

    function viewCenter(viewTransform = state.transform, viewport = state.viewportSize) {
      return graphGeometry.viewCenter(viewTransform, viewport, worldSize);
    }

    function visibleWorldBounds(
      viewTransform = state.transform,
      viewport = currentViewportSize(),
      insets = { top: 0, right: 0, bottom: 0, left: 0 },
    ) {
      return graphGeometry.visibleWorldBounds(viewTransform, viewport, worldSize, insets);
    }

    function transformForCenter(center, scale = 1, viewport = state.viewportSize) {
      const next = graphGeometry.transformForCenter(center, scale, viewport, worldSize, scaleExtent);
      return d3.zoomIdentity.translate(next.x, next.y).scale(next.k);
    }

    function screenToWorld(clientX, clientY, rect) {
      const bounds = rect || root.getBoundingClientRect();
      return graphGeometry.screenToWorld({ x: clientX, y: clientY }, state.transform, worldSize, bounds);
    }

    return {
      currentViewportInsets, currentViewportSize, screenToWorld, transformForCenter, viewCenter, visibleWorldBounds,
    };
  }

  window.PortalHatgGraphViewport = Object.freeze({ createViewport, dependencies: DEPENDENCIES });
})();
