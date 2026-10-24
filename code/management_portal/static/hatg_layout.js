(() => {
  "use strict";

  if (window.PortalHatgLayout) return;

  // Where objects sit on the HATG canvas: each viewer's saved camera and Workspace
  // positions, edited positions and the projection they invalidate, separating
  // objects that grew into their neighbours, and arranging a scope automatically.
  const DEPENDENCIES = Object.freeze([
    "$id", "clone", "commitLineage", "focusedFamily", "functionBlockElements",
    "functionBlockInstanceById", "graphItemById", "graphVisibleModel", "renderControls",
    "renderGraph", "saveLineageLayout", "state", "toast",
  ]);

  const HATG_VIEW_UI_NAMESPACE = "lineage.hatg-view";

  const AUTO_LAYOUT_METRICS = Object.freeze({
    agent: Object.freeze({ width: 154, height: 94, columnGap: 88, rowGap: 24, maxRows: 7 }),
  });

  const HATG_WORLD_SIZE = Object.freeze({ width: 1280, height: 900 });

  // Both arrange and Fit all must fit the actual usable canvas. A readability
  // floor can put outer cards behind the toolbar in a narrow or short dock;
  // users can focus or zoom a card after the complete layout is visible.
  const AUTO_LAYOUT_VIEW = Object.freeze({ duration: 280, maxScale: 1, padding: 64 });

  function normalizeHatgViewPreference(value) {
    const source = value && typeof value === "object" && !Array.isArray(value) ? value : {};
    const viewport = source.viewport && typeof source.viewport === "object" && !Array.isArray(source.viewport)
      ? source.viewport
      : null;
    const normalizedViewport = viewport
      && [viewport.centerX, viewport.centerY, viewport.k].every(item => Number.isFinite(Number(item)))
      ? {
          centerX: Number(viewport.centerX),
          centerY: Number(viewport.centerY),
          k: Number(viewport.k),
          updatedAt: Number(viewport.updatedAt) || Date.now(),
        }
      : null;
    return {
      viewport: normalizedViewport,
      workspace_positions: Object.fromEntries(Object.entries(source.workspace_positions || {})
        .filter(([id, position]) => /^(workspace::workspace_|resident_)[a-z0-9]+$/i.test(id)
          && position && Number.isFinite(position.x) && Number.isFinite(position.y))
        .map(([id, { x, y }]) => [id, { x, y }])),
      // Families are folded to their facade unless the viewer expanded them.
      expanded_family_facade_ids: [...new Set(
        (Array.isArray(source.expanded_family_facade_ids)
          ? source.expanded_family_facade_ids
          : [])
          .map(item => String(item || "").trim())
          .filter(item => /^resident_[a-z0-9]+$/i.test(item)),
      )].slice(-160),
    };
  }

  function createLayout(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalHatgLayout is missing: ${missing.join(", ")}`);
    const {
      $id, clone, commitLineage, focusedFamily, functionBlockElements, functionBlockInstanceById,
      graphItemById, graphVisibleModel, renderControls, renderGraph, saveLineageLayout, state,
      toast,
    } = dependencies;

    const hatgViewPreferences = new Map();

    const hatgViewSaveTimers = new Map();

    const hatgViewSaveRequests = new Map();

    async function loadHatgViewPreference(lineageId) {
      const key = String(lineageId || "").trim();
      if (!key) return normalizeHatgViewPreference({});
      try {
        await flushHatgViewPreferenceSave(key);
        const payload = await window.PortalIdentity.listUIAssociations(HATG_VIEW_UI_NAMESPACE);
        const association = (payload?.items || []).find(item => item?.key === key);
        const preference = normalizeHatgViewPreference(association?.value);
        hatgViewPreferences.set(key, preference);
        return preference;
      } catch (_error) {
        return hatgViewPreferences.get(key) || normalizeHatgViewPreference({});
      }
    }

    function scheduleHatgViewPreferenceSave(lineageId, patch = {}) {
      const key = String(lineageId || "").trim();
      if (!key) return;
      const current = hatgViewPreferences.get(key) || normalizeHatgViewPreference({});
      const preference = normalizeHatgViewPreference({ ...current, ...patch });
      hatgViewPreferences.set(key, preference);
      window.clearTimeout(hatgViewSaveTimers.get(key));
      hatgViewSaveTimers.set(key, window.setTimeout(() => {
        flushHatgViewPreferenceSave(key).catch(() => {});
      }, 300));
    }

    function flushHatgViewPreferenceSave(lineageId) {
      const key = String(lineageId || "").trim();
      if (!hatgViewSaveTimers.has(key)) return hatgViewSaveRequests.get(key) || Promise.resolve();
      window.clearTimeout(hatgViewSaveTimers.get(key));
      hatgViewSaveTimers.delete(key);
      const preference = hatgViewPreferences.get(key);
      // Serialize writes, but let a committed structural edit wait for its
      // camera before closing its form or reading preferences again.
      const pending = (hatgViewSaveRequests.get(key) || Promise.resolve()).catch(() => {})
        .then(() => window.PortalIdentity.putUIAssociation(HATG_VIEW_UI_NAMESPACE, key, preference))
        .catch(error => {
          console.warn("HATG personal view save failed", error);
          toast(window.PortalI18n.t("视图位置保存失败，当前调整仍保留；请再次拖动或整理以重试。"), "error");
          throw error;
        }).finally(() => {
          if (hatgViewSaveRequests.get(key) === pending) hatgViewSaveRequests.delete(key);
        });
      hatgViewSaveRequests.set(key, pending);
      return pending;
    }

    function syncWorkspaceNodePosition(node, position = node) {
      const key = state.lineage?.lineage_id;
      if (!key || !["workspace", "workspace_resident"].includes(node?.kind)
        || !Number.isFinite(position?.x) || !Number.isFinite(position?.y)) return false;
      const preference = hatgViewPreferences.get(key) || normalizeHatgViewPreference({});
      hatgViewPreferences.set(key, {
        ...preference,
        workspace_positions: {
          ...preference.workspace_positions, [node.id]: { x: position.x, y: position.y },
        },
      });
      return true;
    }

    function syncGraphNodePosition(node, position = node) {
      if (!node?.id) return false;
      const target = graphItemById(node.id);
      const x = Number(position?.x);
      const y = Number(position?.y);
      if (!target || !Number.isFinite(x) || !Number.isFinite(y)) return false;
      target.x = x;
      target.y = y;
      // The position changes inside the committed Lineage object, so the canvas
      // projection would keep its copied coordinates without a new local version.
      state.localVersion += 1;
      return true;
    }

    function separateGraphObject(id) {
      return separateGraphObjects([id]);
    }

    function separateGraphObjects(objectIds) {
      if (state.graphViewMode !== "context" || state.graphFocusMode) return Promise.resolve(null);
      const moved = new Map();
      for (const id of new Set(objectIds)) {
        const offset = state.graphController?.getObjectSeparation?.(id);
        if (!offset) continue;
        const block = functionBlockInstanceById(id);
        const ids = new Set([id, ...(block ? functionBlockElements(block).map(element => element.id) : [])]);
        for (const objectId of ids) {
          const item = graphItemById(objectId);
          if (!item || !Number.isFinite(item.x) || !Number.isFinite(item.y)) continue;
          item.x += offset.x;
          item.y += offset.y;
          moved.set(objectId, item);
        }
        // Measure the next object against this placement before saving the batch.
        // The offsets edit the committed Lineage in place, so the canvas needs a
        // new local version to project them.
        state.localVersion += 1;
        renderGraph();
      }
      return moved.size ? saveLineageLayout([...moved.values()]) : Promise.resolve(null);
    }

    function autoLayoutScope() {
      return `${state.lineage?.lineage_id || ""}:${focusedFamily()?.roles.facade || ""}`;
    }

    function renderAutoLayoutControl() {
      const button = $id("autoLayoutBtn");
      if (!button) return;
      const workspaceProjection = state.graphViewMode === "workspace";
      const density = workspaceProjection ? "" : state.autoLayoutDensityByScope.get(autoLayoutScope());
      button.disabled = !state.lineage || state.autoLayoutInFlight;
      button.classList.toggle("busy", state.autoLayoutInFlight);
      button.setAttribute("aria-busy", state.autoLayoutInFlight ? "true" : "false");
      button.dataset.layoutDensity = density || "";
      button.setAttribute("aria-pressed", density === "compact" ? "true" : "false");
      const tooltip = workspaceProjection
        ? window.PortalI18n.t("重新自动排列 Workspace 与 Resident 标记，清除当前用户在此视图的手动位置。")
        : window.PortalI18n.t("自动整理：按分支归组并适配画布。再次点击可在紧凑与舒展布局之间切换。");
      button.dataset.portalTooltip = tooltip;
      button.setAttribute("aria-label", tooltip.replace(/。$/, ""));
      const label = button.querySelector("span");
      if (label) label.textContent = state.autoLayoutInFlight
        ? window.PortalI18n.t("整理中…")
        : density === "comfortable" ? window.PortalI18n.t("紧凑整理")
          : density === "compact" ? window.PortalI18n.t("舒展整理") : window.PortalI18n.t("整理");
    }

    function readableAutoLayoutOptions(items = [], density = "comfortable") {
      const measured = state.graphController?.getNodeSizes?.() || {};
      // Titles and runtime labels wrap. The layout engine uses uniform cells,
      // so each cell must accommodate the largest card actually being arranged.
      const metrics = { ...AUTO_LAYOUT_METRICS.agent };
      items.forEach(item => {
        const size = measured[item.id];
        if (size?.width > 0) metrics.width = Math.max(metrics.width, size.width / 100 * HATG_WORLD_SIZE.width);
        if (size?.height > 0) metrics.height = Math.max(metrics.height, size.height / 100 * HATG_WORLD_SIZE.height);
      });
      const compact = density === "compact";
      const horizontalPitch = metrics.width + (compact ? 32 : metrics.columnGap);
      const verticalPitch = metrics.height + (compact ? 12 : metrics.rowGap);
      const viewport = $id("hatgCanvas")?.getBoundingClientRect();
      return {
        horizontalGap: Math.round(horizontalPitch / HATG_WORLD_SIZE.width * 10000) / 100,
        verticalGap: Math.round(verticalPitch / HATG_WORLD_SIZE.height * 10000) / 100,
        nodeWidth: Math.round(metrics.width / HATG_WORLD_SIZE.width * 10000) / 100,
        nodeHeight: Math.round(metrics.height / HATG_WORLD_SIZE.height * 10000) / 100,
        maxRowsPerLane: metrics.maxRows,
        ...(compact ? {
          componentGap: 3,
          componentColumnGap: 3,
          componentWrapSpan: 0,
          // Layout coordinates are percentages of a non-square world.
          componentAspectRatio: viewport?.width > 0 && viewport?.height > 0
            ? (viewport.width / viewport.height) * (HATG_WORLD_SIZE.height / HATG_WORLD_SIZE.width)
            : 1,
        } : {}),
      };
    }

    async function autoLayoutGraph() {
      if (!state.lineage) return;
      if (state.autoLayoutInFlight) return;
      const computeLayout = window.PortalHatgGraph?.computeBranchAwareLayout;
      if (typeof computeLayout !== "function") return toast(window.PortalI18n.t("图布局引擎尚未就绪。"), "error");

      if (state.graphViewMode === "workspace") {
        scheduleHatgViewPreferenceSave(state.lineage.lineage_id, { workspace_positions: {} });
        renderGraph();
        window.requestAnimationFrame(() => state.graphController?.fit?.({ duration: 220, maxScale: 1.15 }));
        return;
      }

      // Arrange the exact unified Context projection that is rendered: Resident
      // nodes, Event/Effect primitives, persistent Function Blocks, seeds, typed
      // wires, and Context relations. Positions are then written back to the
      // corresponding persisted objects instead of mutating projection copies.
      const layoutModel = graphVisibleModel();
      const family = focusedFamily();
      const scope = autoLayoutScope();
      const previousDensity = state.autoLayoutDensityByScope.get(scope);
      const density = previousDensity === "comfortable" ? "compact" : "comfortable";
      const lineageId = state.lineage.lineage_id;
      const leaderId = family?.roles.facade || state.lineage.leader_resident_id;
      const visibleNodes = family ? layoutModel.nodes : layoutModel.overviewNodes;
      const visibleEdges = family ? layoutModel.edges : layoutModel.overviewEdges;
      const items = visibleNodes
        .map(item => graphItemById(item.id))
        .filter(Boolean);
      if (!items.length) return;
      const itemIds = new Set(items.map(item => item.id));
      const layoutNodes = visibleNodes.filter(item => itemIds.has(item.id));
      const layoutEdges = visibleEdges.filter(edge => (
        !edge.draft_preview
        && itemIds.has(edge.source)
        && itemIds.has(edge.target)
      ));
      const positions = computeLayout(layoutNodes, layoutEdges, {
        ...readableAutoLayoutOptions(layoutNodes, density),
        // Object bindings establish membership, without ordering execution.
        feedbackEdgeTypes: ["watch", "target"],
        leaderId,
        rootIds: [leaderId],
      });
      if (!positions.size) return;

      const previousLineage = clone(state.lineage);
      const previousFocusMode = state.graphFocusMode;
      let changed = false;
      items.forEach(item => {
        const position = positions.get(item.id);
        if (!position) return;
        changed = changed
          || Math.abs(Number(item.x || 0) - position.x) > 0.01
          || Math.abs(Number(item.y || 0) - position.y) > 0.01;
        item.x = position.x;
        item.y = position.y;
      });
      // Layout-only edits precede the server's layout_revision. Rebuild the
      // projected node copies now so rendering and fit use these new positions.
      state.localVersion += 1;
      state.autoLayoutDensityByScope.set(scope, density);
      if (!family) state.graphFocusMode = false;
      if (!changed) {
        renderGraph();
        renderControls();
        window.requestAnimationFrame(() => state.graphController?.fit?.(AUTO_LAYOUT_VIEW));
        toast(window.PortalI18n.t("当前 HATG 已经排列整齐。"));
        return;
      }

      state.autoLayoutInFlight = true;
      renderControls();
      renderGraph();
      window.requestAnimationFrame(() => state.graphController?.fit?.(AUTO_LAYOUT_VIEW));
      try {
        await saveLineageLayout(items);
        if (state.lineage?.lineage_id !== lineageId) return;
        toast(density === "compact"
          ? window.PortalI18n.t("HATG 已紧凑整理，节点位置已保存。")
          : itemIds.has(leaderId)
          ? window.PortalI18n.t("HATG 已整理，Leader 已移到关联节点侧方，节点位置已保存。")
          : window.PortalI18n.t("HATG 已按分支关系整理，节点位置已保存。"));
      } catch (error) {
        if (previousDensity) state.autoLayoutDensityByScope.set(scope, previousDensity);
        else state.autoLayoutDensityByScope.delete(scope);
        if (state.lineage?.lineage_id !== lineageId) return;
        commitLineage(previousLineage, {
          replace: true,
          afterMerge: () => { state.graphFocusMode = previousFocusMode; },
        });
        window.requestAnimationFrame(() => state.graphController?.fit?.({ ...AUTO_LAYOUT_VIEW, duration: 180 }));
        toast(window.PortalI18n.text`自动整理失败：${error.message || error}`, "error");
      } finally {
        state.autoLayoutInFlight = false;
        renderControls();
      }
    }

    return Object.freeze({
      hatgViewPreferences, HATG_WORLD_SIZE, AUTO_LAYOUT_VIEW, loadHatgViewPreference,
      scheduleHatgViewPreferenceSave, flushHatgViewPreferenceSave, syncWorkspaceNodePosition,
      syncGraphNodePosition, separateGraphObject, separateGraphObjects, autoLayoutScope,
      renderAutoLayoutControl, readableAutoLayoutOptions, autoLayoutGraph,
    });
  }

  window.PortalHatgLayout = Object.freeze({
    createLayout, dependencies: DEPENDENCIES, HATG_WORLD_SIZE, AUTO_LAYOUT_VIEW,
    normalizeHatgViewPreference,
  });
})();
