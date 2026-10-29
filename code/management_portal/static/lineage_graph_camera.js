(() => {
  "use strict";

  if (window.PortalHatgGraphCamera) return;

  // The camera: the D3 zoom behavior and the transform it applies to the
  // world, the semantic zoom levels and level of detail that follow the
  // scale, fitting, centering and focusing animations, the view each context
  // saves, the zoom buttons and following the viewport's size.

  const SEMANTIC_ZOOM = Object.freeze({
    overview: { scale: 0.52, title: window.PortalI18n.t("总览镜头"), hint: window.PortalI18n.t("拓扑、状态与实时活动流") },
    operate: { scale: 1, title: window.PortalI18n.t("操作镜头"), hint: window.PortalI18n.t("目标、状态与协作关系") },
    inspect: { scale: 1.62, title: window.PortalI18n.t("检视镜头"), hint: window.PortalI18n.t("完整节点上下文与产物线索") },
  });

  const DEPENDENCIES = Object.freeze([
    "animationDuration", "clamp", "closeQuickForkMenu", "currentViewportInsets", "currentViewportSize", "d3",
    "element", "functionBlockBoundaryBounds", "invalidateEdgeGeometry", "layers", "nodeHalfExtents", "nodePosition",
    "number", "options", "repositionQuickForkMenu", "root", "rootSelection", "scaleExtent", "semanticZoomHint",
    "semanticZoomRoot", "semanticZoomTitle", "state", "transformForCenter", "updateGeometry",
    "updateMinimapViewport", "viewCenter", "world", "worldSize", "zoomValue",
  ]);

  function createCamera(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalHatgGraphCamera is missing: ${missing.join(", ")}`);
    const {
      animationDuration, clamp, closeQuickForkMenu, currentViewportInsets, currentViewportSize, d3, element,
      functionBlockBoundaryBounds, invalidateEdgeGeometry, layers, nodeHalfExtents, nodePosition, number, options,
      repositionQuickForkMenu, root, rootSelection, scaleExtent, semanticZoomHint, semanticZoomRoot,
      semanticZoomTitle, state, transformForCenter, updateGeometry, updateMinimapViewport, viewCenter, world,
      worldSize, zoomValue,
    } = dependencies;

    let resizeFrame = 0;

    let viewportMoving = false;
    let zoomDetailFrame = 0;
    let focusAnimation = null;
    let navigationSequence = 0;

    let centerOnNextRender = false;
    let pendingViewportFit = null;

    function semanticZoomForScale(scale) {
      if (scale < 0.68) return "overview";
      if (scale >= 1.38) return "inspect";
      return "operate";
    }

    function syncSemanticZoom() {
      const nextLevel = semanticZoomForScale(state.transform.k);
      const changed = state.semanticZoomLevel !== nextLevel;
      if (!changed) return false;
      state.semanticZoomLevel = nextLevel;
      root.dataset.semanticZoom = nextLevel;
      root.setAttribute("aria-description", `${SEMANTIC_ZOOM[nextLevel].title}：${SEMANTIC_ZOOM[nextLevel].hint}`);
      layers.nodes.selectAll(".hatgNode").attr("data-semantic-level", nextLevel);
      semanticZoomRoot?.querySelectorAll?.("[data-semantic-zoom]").forEach(button => {
        const active = button.dataset.semanticZoom === nextLevel;
        button.classList.toggle("active", active);
        if (active) button.setAttribute("aria-current", "true");
        else button.removeAttribute("aria-current");
      });
      if (semanticZoomTitle) semanticZoomTitle.textContent = SEMANTIC_ZOOM[nextLevel].title;
      if (semanticZoomHint) semanticZoomHint.textContent = SEMANTIC_ZOOM[nextLevel].hint;
      options.onSemanticZoomChange?.(nextLevel, { ...SEMANTIC_ZOOM[nextLevel], scale: state.transform.k });
      return true;
    }

    function syncZoomReadout() {
      const text = `${Math.round(state.transform.k * 100)}%`;
      if (zoomValue && zoomValue.textContent !== text) zoomValue.textContent = text;
      const scale = state.transform.k.toFixed(3);
      if (root.dataset.zoom !== scale) root.dataset.zoom = scale;
    }

    function syncZoomDetail() {
      const nextLod = state.transform.k < 0.62 ? "compact" : state.transform.k < 0.9 ? "reduced" : "detail";
      const changed = root.dataset.lod !== nextLod;
      if (changed) root.dataset.lod = nextLod;
      const semanticChanged = syncSemanticZoom();
      if (changed || semanticChanged) updateGeometry({ measureNodes: true });
      return changed || semanticChanged;
    }

    function syncGridPosition() {
      const gridSize = 28 * state.transform.k;
      root.style.setProperty("--hatg-grid-size", `${gridSize}px`);
      root.style.setProperty("--hatg-grid-x", `${state.transform.x % gridSize}px`);
      root.style.setProperty("--hatg-grid-y", `${state.transform.y % gridSize}px`);
    }

    function applyTransform(nextTransform) {
      const previousScale = state.transform.k;
      state.transform = nextTransform;
      world.style.transform = `translate(${state.transform.x}px, ${state.transform.y}px) scale(${state.transform.k})`;
      repositionQuickForkMenu();
      const scaleChanged = !root.dataset.zoom
        || Math.abs(previousScale - state.transform.k) > 1e-6;
      let lodChanged = false;
      if (scaleChanged) {
        syncZoomReadout();
        invalidateEdgeGeometry();
      }
      // The world keeps its geometry throughout a viewport gesture. Changing
      // card sizes here would force layout and reroute every edge mid-frame.
      if (!viewportMoving) {
        syncGridPosition();
        lodChanged = syncZoomDetail();
      }
      updateMinimapViewport();
      root.dispatchEvent(new CustomEvent("hatg:transform", {
        detail: { x: state.transform.x, y: state.transform.y, k: state.transform.k, contextId: state.contextId },
      }));
      return lodChanged;
    }

    function applyTransformImmediately(nextTransform) {
      cancelFocusAnimation();
      rootSelection.property("__zoom", nextTransform);
      return applyTransform(nextTransform);
    }

    function persistTransform() {
      if (!state.contextId) return;
      const center = viewCenter();
      const savedView = {
        centerX: center.x,
        centerY: center.y,
        k: state.transform.k,
        updatedAt: Date.now(),
      };
      options.onViewportChange?.(state.contextId, { ...savedView });
    }

    function zoomFilter(event) {
      if (event.type === "wheel") return true;
      if (event.button !== undefined && event.button !== 0) return false;
      if (event.shiftKey && state.model.selectionEnabled) return false;
      return !event.target?.closest?.(".hatgNode, .edgeLabelBtn, .graphEmpty, button, input, textarea, select");
    }

    const zoomBehavior = d3.zoom()
      .scaleExtent(scaleExtent)
      .filter(zoomFilter)
      .on("start.hatg", event => {
        if (event.sourceEvent) navigationSequence += 1;
        window.cancelAnimationFrame(zoomDetailFrame);
        zoomDetailFrame = 0;
        viewportMoving = true;
        root.classList.add("isViewportMoving");
      })
      .on("zoom.hatg", event => applyTransform(event.transform))
      .on("end.hatg", event => {
        viewportMoving = false;
        root.classList.remove("isViewportMoving");
        syncGridPosition();
        window.cancelAnimationFrame(zoomDetailFrame);
        zoomDetailFrame = window.requestAnimationFrame(() => {
          zoomDetailFrame = 0;
          syncZoomDetail();
        });
        if (event.sourceEvent) persistTransform();
      });
    rootSelection.call(zoomBehavior).on("dblclick.zoom", null);
    root.addEventListener("pointerdown", cancelFocusAnimation, true);
    root.addEventListener("wheel", cancelFocusAnimation, { capture: true, passive: true });

    function cancelFocusAnimation() {
      if (!focusAnimation) return;
      const visual = new DOMMatrixReadOnly(window.getComputedStyle(world).transform);
      focusAnimation.cancel();
      focusAnimation = null;
      rootSelection.call(
        zoomBehavior.transform,
        d3.zoomIdentity.translate(visual.m41, visual.m42).scale(visual.a),
      );
    }

    function animateTransform(target, duration = 220, persist = false, compositor = false) {
      rootSelection.interrupt();
      cancelFocusAnimation();
      window.cancelAnimationFrame(zoomDetailFrame);
      zoomDetailFrame = 0;
      const effectiveDuration = animationDuration(duration);
      if (effectiveDuration <= 0) {
        rootSelection.call(zoomBehavior.transform, target);
        if (persist) persistTransform();
      } else if (compositor && world.animate) {
        const from = window.getComputedStyle(world).transform;
        rootSelection.call(zoomBehavior.transform, target);
        focusAnimation = world.animate(
          [{ transform: from }, { transform: world.style.transform }],
          { duration: effectiveDuration, easing: "ease-out" },
        );
        focusAnimation.onfinish = () => { focusAnimation = null; };
        if (persist) persistTransform();
      } else {
        const transition = rootSelection.transition().duration(effectiveDuration).call(zoomBehavior.transform, target);
        if (persist) transition.on("end.persist", persistTransform);
      }
    }

    function zoomBy(factor) {
      rootSelection.interrupt().call(zoomBehavior.scaleBy, factor);
      persistTransform();
    }

    function setSemanticZoom(level, options = {}) {
      const semantic = SEMANTIC_ZOOM[level];
      if (!semantic) return;
      if (level !== "overview" && state.nodeMap.has(state.model.selectedNodeId)) {
        focusNode(state.model.selectedNodeId, { scale: semantic.scale, duration: number(options.duration, 260), persist: true });
        return;
      }
      const viewport = currentViewportSize();
      state.viewportSize = viewport;
      animateTransform(
        transformForCenter(viewCenter(state.transform, viewport), semantic.scale, viewport),
        number(options.duration, 260),
        true,
      );
    }

    function reset(options = {}) {
      center({ ...options, scale: 1 });
    }

    function graphPixelBounds(nodeIds = null) {
      const wanted = Array.isArray(nodeIds) && nodeIds.length ? new Set(nodeIds.map(String)) : null;
      const nodes = wanted ? state.model.nodes.filter(node => wanted.has(String(node.id))) : state.model.nodes;
      // A card reaches its measured half size around its center, plus a small
      // margin for ports; never less than a standard 154 x 94 card.
      const boxes = (nodes.length ? nodes : state.model.nodes).map(node => {
        const position = nodePosition(node);
        const half = nodeHalfExtents(node);
        const x = position.x / 100 * worldSize.width;
        const y = position.y / 100 * worldSize.height;
        const halfWidth = Math.max(84, half.x / 100 * worldSize.width + 7);
        const halfHeight = Math.max(55, half.y / 100 * worldSize.height + 8);
        return { minX: x - halfWidth, maxX: x + halfWidth, minY: y - halfHeight, maxY: y + halfHeight };
      });
      for (const block of state.model.functionBlockBoundaries || []) {
        if (wanted && !block.member_ids.some(id => wanted.has(id))) continue;
        const bounds = functionBlockBoundaryBounds(block);
        if (bounds) boxes.push({ minX: bounds.left * worldSize.width / 100, maxX: bounds.right * worldSize.width / 100,
          minY: bounds.top * worldSize.height / 100, maxY: bounds.bottom * worldSize.height / 100 });
      }
      if (!boxes.length) {
        return {
          minX: 0,
          maxX: worldSize.width,
          minY: 0,
          maxY: worldSize.height,
        };
      }
      return {
        minX: d3.min(boxes, box => box.minX),
        maxX: d3.max(boxes, box => box.maxX),
        minY: d3.min(boxes, box => box.minY),
        maxY: d3.max(boxes, box => box.maxY),
      };
    }

    function graphFitsViewport(candidate = state.transform, viewport = state.viewportSize, margin = 10, nodeIds = null) {
      if (!state.model.nodes.length) return true;
      const bounds = graphPixelBounds(nodeIds);
      const insets = currentViewportInsets(viewport);
      const left = candidate.x + bounds.minX * candidate.k;
      const right = candidate.x + bounds.maxX * candidate.k;
      const top = candidate.y + bounds.minY * candidate.k;
      const bottom = candidate.y + bounds.maxY * candidate.k;
      return left >= insets.left + margin
        && right <= viewport.width - insets.right - margin
        && top >= insets.top + margin
        && bottom <= viewport.height - insets.bottom - margin;
    }

    function center(options = {}) {
      const viewport = currentViewportSize();
      state.viewportSize = viewport;
      const bounds = graphPixelBounds();
      const centerPoint = {
        x: (bounds.minX + bounds.maxX) / 2 / worldSize.width * 100,
        y: (bounds.minY + bounds.maxY) / 2 / worldSize.height * 100,
      };
      animateTransform(
        transformForCenter(centerPoint, number(options.scale, state.transform.k), viewport),
        number(options.duration, 220),
        options.persist === true,
      );
    }

    function fit(options = {}) {
      // A collapsed panel has no usable viewport. Fitting its synthetic
      // 1px size would leave the graph at minimum zoom when it reopens.
      if (!root.clientWidth || !root.clientHeight) {
        pendingViewportFit = { ...options };
        return;
      }
      pendingViewportFit = null;
      if (!state.model.nodes.length) return reset(options);
      const viewport = currentViewportSize();
      state.viewportSize = viewport;
      const { width, height } = viewport;
      const insets = currentViewportInsets(viewport);
      const availableWidth = Math.max(1, width - insets.left - insets.right);
      const availableHeight = Math.max(1, height - insets.top - insets.bottom);
      const padding = clamp(
        number(options.padding, availableWidth <= 720 ? 40 : 72),
        20,
        Math.min(availableWidth, availableHeight) / 3,
      );
      // `nodeIds` fits a part of the graph, such as an expanded Family.
      const { minX, maxX, minY, maxY } = graphPixelBounds(options.nodeIds);
      const contentWidth = Math.max(168, maxX - minX);
      const contentHeight = Math.max(110, maxY - minY);
      const minimumScale = clamp(
        number(options.minScale, scaleExtent[0]),
        scaleExtent[0],
        scaleExtent[1],
      );
      const targetScale = clamp(Math.min(
        (availableWidth - padding * 2) / contentWidth,
        (availableHeight - padding * 2) / contentHeight,
        number(options.maxScale, 1.45),
      ), minimumScale, scaleExtent[1]);
      const centerX = (minX + maxX) / 2;
      const centerY = (minY + maxY) / 2;
      const target = d3.zoomIdentity
        .translate(
          insets.left + availableWidth / 2 - centerX * targetScale,
          insets.top + availableHeight / 2 - centerY * targetScale,
        )
        .scale(targetScale);
      animateTransform(target, number(options.duration, 260), options.persist === true);
    }

    function focusNode(nodeId, options = {}) {
      const node = state.nodeMap.get(nodeId);
      if (!node) return;
      const viewport = currentViewportSize();
      state.viewportSize = viewport;
      const insets = currentViewportInsets(viewport);
      const availableWidth = Math.max(1, viewport.width - insets.left - insets.right);
      const availableHeight = Math.max(1, viewport.height - insets.top - insets.bottom);
      const bounds = graphPixelBounds([nodeId]);
      const targetScale = clamp(Math.min(
        number(options.scale, Math.max(1.15, state.transform.k)),
        Math.max(1, availableWidth - 20) / (bounds.maxX - bounds.minX),
        Math.max(1, availableHeight - 20) / (bounds.maxY - bounds.minY),
      ), scaleExtent[0], scaleExtent[1]);
      const target = d3.zoomIdentity
        .translate(
          insets.left + availableWidth / 2 - (bounds.minX + bounds.maxX) / 2 * targetScale,
          insets.top + availableHeight / 2 - (bounds.minY + bounds.maxY) / 2 * targetScale,
        )
        .scale(targetScale);
      animateTransform(target, number(options.duration, 240), options.persist === true, options.compositor === true);
    }

    function ensureNodeVisible(nodeId, options = {}) {
      if (!state.nodeMap.has(nodeId)) return;
      // Settle any structural fit before deciding what is visible or saving
      // its camera. A later animation frame must not replace this reveal.
      rootSelection.interrupt();
      cancelFocusAnimation();
      if (graphFitsViewport(state.transform, currentViewportSize(), 10, [nodeId])) {
        // Structural changes may already have fitted the new graph. Save that
        // view when requested, even if revealing the retained card needn't pan.
        if (options.persist) persistTransform();
        return;
      }
      // Placement may avoid an occupied position outside the current viewport.
      // Reveal the committed card without changing coordinates or zooming in.
      focusNode(nodeId, { scale: state.transform.k, duration: 0, persist: true });
    }

    function centerAt(x, y, options = {}) {
      const viewport = currentViewportSize();
      state.viewportSize = viewport;
      const scale = clamp(number(options.scale, state.transform.k), scaleExtent[0], scaleExtent[1]);
      const insets = currentViewportInsets(viewport);
      const centered = transformForCenter({ x, y }, scale, viewport);
      const target = d3.zoomIdentity.translate(
        centered.x + (insets.left - insets.right) / 2,
        centered.y + (insets.top - insets.bottom) / 2,
      ).scale(scale);
      animateTransform(target, number(options.duration, 160), true);
    }

    // Returns to a view read with getTransform, such as the canvas before a Family view.
    function setTransform(view = {}, options = {}) {
      const k = clamp(number(view.k, state.transform.k), scaleExtent[0], scaleExtent[1]);
      animateTransform(
        d3.zoomIdentity.translate(number(view.x, state.transform.x), number(view.y, state.transform.y)).scale(k),
        number(options.duration, 220),
      );
    }

    function setContext(nextContextId) {
      closeQuickForkMenu();
      pendingViewportFit = null;
      state.contextId = String(nextContextId || "");
      const stored = state.contextId ? options.readViewport?.(state.contextId) : null;
      state.viewportSize = currentViewportSize();
      const k = clamp(number(stored?.k, 1), scaleExtent[0], scaleExtent[1]);
      const next = transformForCenter({
        x: number(stored?.centerX, 50),
        y: number(stored?.centerY, 50),
      }, k, state.viewportSize);
      centerOnNextRender = !stored;
      animateTransform(next, 0);
      return Boolean(stored);
    }

    // A context without a saved view is fitted once its graph first renders.
    function fitUnsavedContext() {
      if (!centerOnNextRender) return;
      centerOnNextRender = false;
      fit({ duration: 0, maxScale: 1 });
    }

    function noteNavigation() {
      navigationSequence += 1;
    }

    const buttonBindings = [];
    function bindButton(target, action) {
      const button = element(target);
      if (!button) return;
      const navigate = event => {
        navigationSequence += 1;
        action(event);
      };
      button.addEventListener("click", navigate);
      buttonBindings.push([button, navigate]);
    }
    bindButton(options.zoomInButton, () => zoomBy(1.25));
    bindButton(options.zoomOutButton, () => zoomBy(0.8));
    bindButton(options.resetButton, () => reset({ persist: true }));
    bindButton(options.fitButton, () => fit({ ...options.fitOptions, persist: true }));
    semanticZoomRoot?.querySelectorAll?.("[data-semantic-zoom]").forEach(button => {
      bindButton(button, () => setSemanticZoom(button.dataset.semanticZoom));
    });

    const resizeObserver = typeof ResizeObserver === "function" ? new ResizeObserver(() => {
      window.cancelAnimationFrame(resizeFrame);
      resizeFrame = window.requestAnimationFrame(() => {
        // Keep the last visible geometry while hidden, including its scale.
        if (!root.clientWidth || !root.clientHeight) return;
        if (pendingViewportFit) {
          fit({ ...pendingViewportFit, duration: 0 });
          return;
        }
        const nextViewport = currentViewportSize();
        if (nextViewport.width === state.viewportSize.width && nextViewport.height === state.viewportSize.height) return;
        const keptWholeGraphVisible = graphFitsViewport(state.transform, state.viewportSize);
        const center = viewCenter(state.transform, state.viewportSize);
        const previousScale = state.transform.k;
        state.viewportSize = nextViewport;
        const nextTransform = transformForCenter(center, previousScale, nextViewport);
        const geometryUpdated = applyTransformImmediately(nextTransform);
        if (keptWholeGraphVisible && !graphFitsViewport(nextTransform, nextViewport)) {
          fit({ duration: 0, maxScale: previousScale });
          return;
        }
        if (!geometryUpdated) updateGeometry({ measureNodes: true });
      });
    }) : null;

    function startCamera() {
      resizeObserver?.observe(root);
      applyTransform(state.transform);
    }

    function destroyCamera() {
      resizeObserver?.disconnect();
      window.cancelAnimationFrame(resizeFrame);
      root.removeEventListener("pointerdown", cancelFocusAnimation, true);
      root.removeEventListener("wheel", cancelFocusAnimation, true);
      rootSelection.on(".zoom", null).interrupt();
      focusAnimation?.cancel();
      focusAnimation = null;
      window.cancelAnimationFrame(zoomDetailFrame);
      buttonBindings.forEach(([button, action]) => button.removeEventListener("click", action));
    }

    return {
      applyTransformImmediately, center, centerAt, destroyCamera, ensureNodeVisible, fit, fitUnsavedContext,
      focusNode, getNavigationSequence: () => navigationSequence, noteNavigation, persistTransform, reset,
      setContext, setTransform, startCamera, zoomBy,
    };
  }

  window.PortalHatgGraphCamera = Object.freeze({ createCamera, dependencies: DEPENDENCIES });
})();
