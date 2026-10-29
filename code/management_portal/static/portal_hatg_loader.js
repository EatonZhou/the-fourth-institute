(function () {
  "use strict";

  if (window.PortalHatgLoader) return;

  const D3_ASSET = "/static/vendor/d3/d3.v7.9.0.min.js";
  const ASSETS = [
    D3_ASSET,
    "/static/lineage_program.js",
    "/static/lineage_primitive_inspector.js",
    "/static/lineage_invocation_inspector.js",
    "/static/lineage_element_fields.js",
    "/static/projection_libraries/portal-graph/1.0.0/geometry.js",
    "/static/lineage_graph_layout.js",
    "/static/lineage_graph_viewport.js",
    "/static/lineage_graph_nodes.js",
    "/static/lineage_graph_element_index.js",
    "/static/lineage_graph_ports.js",
    "/static/lineage_graph_hover.js",
    "/static/lineage_graph_edge_routes.js",
    "/static/lineage_graph_edges.js",
    "/static/lineage_graph_minimap.js",
    "/static/lineage_graph_function_blocks.js",
    "/static/lineage_graph_camera.js",
    "/static/lineage_graph_selection.js",
    "/static/lineage_graph_wiring.js",
    "/static/lineage_graph_quick_fork.js",
    "/static/lineage_graph_drag.js",
    "/static/lineage_graph_cards.js",
    "/static/lineage_graph_canvas_events.js",
    "/static/lineage_graph_render.js",
    "/static/lineage_graph.js",
    "/static/hatg_panel_features.js",
    "/static/hatg_panel_state.js",
    "/static/hatg_workspace_projection.js",
    "/static/portal_resident_avatar.js",
    "/static/portal_script_runs.js",
    "/static/lineage_template_graph.js",
    "/static/lineage_templates.js",
    "/static/hatg_graph_model.js",
    "/static/hatg_lineage_state.js",
    "/static/hatg_program_edits.js",
    "/static/hatg_family_blocks.js",
    "/static/hatg_node_status.js",
    "/static/hatg_canvas_selection.js",
    "/static/hatg_function_block_menu.js",
    "/static/hatg_program_inspector.js",
    "/static/hatg_relations.js",
    "/static/hatg_layout.js",
    "/static/hatg_toolbar.js",
    "/static/hatg_graph_projection.js",
    "/static/hatg_graph_view.js",
    "/static/hatg_resident_runtime.js",
    "/static/hatg_workspace_results.js",
    "/static/hatg_resident_dialogs.js",
    "/static/hatg_lineage_dialogs.js",
    "/static/hatg_seeds.js",
    "/static/hatg_leader.js",
    "/static/hatg_graph_search.js",
    "/static/hatg_panel.js",
  ];
  let assetsPromise = null;

  function d3IsReady() {
    return !!(window.d3?.zoom && window.d3?.drag && window.d3?.select);
  }

  async function loadD3() {
    if (d3IsReady()) return Promise.resolve();

    const response = await fetch(D3_ASSET, { credentials: "same-origin" });
    if (!response.ok) {
      throw new Error(`无法加载 ${D3_ASSET}（HTTP ${response.status}）`);
    }
    const source = await response.text();
    const script = document.createElement("script");
    const sourceUrl = new URL(D3_ASSET, location.href).href;
    // D3 ships as UMD. Lexically hiding AMD/CommonJS keeps Monaco's loader
    // untouched while making this dependency deterministic for HATG.
    script.textContent = `(function (define, module, exports) {\n${source}\n}).call(window);\n//# sourceURL=${sourceUrl}`;
    document.head.append(script);
    script.remove();
    if (!d3IsReady()) throw new Error("D3 global is incomplete");
  }

  function loadAsset(src) {
    return src === D3_ASSET ? loadD3() : window.PortalUIPrimitives.loadAsset(src);
  }

  // The assets must execute in order, but they need not download in order: a
  // cold HATG fetched about 1.1 MB in ten serial round trips before it could
  // mount. Preloading lets the browser fetch them all while D3 is evaluated.
  function preloadAssets() {
    for (const src of ASSETS) {
      if (src === D3_ASSET) continue;
      const href = new URL(window.PortalUIPrimitives.staticAssetUrl(src), location.href).href;
      if ([...document.scripts].some(script => script.src && new URL(script.src).pathname === src)) continue;
      if ([...document.querySelectorAll('link[rel="preload"]')].some(link => link.href === href)) continue;
      const link = document.createElement("link");
      link.rel = "preload";
      link.as = "script";
      link.href = href;
      document.head.append(link);
    }
  }

  function loadAssets() {
    if (!assetsPromise) {
      preloadAssets();
      void window.PortalUIPrimitives.loadAsset("/static/portal_resident_avatar.css").catch(() => {});
      assetsPromise = ASSETS.reduce(
        (pending, src) => pending.then(() => loadAsset(src)),
        Promise.resolve(),
      ).then(() => {
        if (typeof window.PortalHatgPanel?.mountHatgPanel !== "function") {
          throw new Error("HATG 面板模块没有完成注册");
        }
        return window.PortalHatgPanel;
      }).catch(error => {
        assetsPromise = null;
        throw error;
      });
    }
    return assetsPromise;
  }

  function create(options = {}) {
    const root = options.root;
    let runtime = null;
    let runtimePromise = null;
    let destroyed = false;
    let active = false;
    const initialTarget = {
      lineageId: String(options.lineageId || "").trim(),
      residentId: String(options.residentId || "").trim(),
      programItemId: String(options.programItemId || "").trim(),
    };
    if (options.revealInGraph === true) {
      initialTarget.revealInGraph = true;
    }
    let pendingTarget = Object.values(initialTarget).some(Boolean) ? initialTarget : null;
    let targetRevision = pendingTarget ? 1 : 0;
    let appliedTargetRevision = -1;
    // The target application in flight, so an identical one can share it.
    let applyingTarget = null;
    let pendingContext = null;
    let restoredState = null;

    root.classList.add("portalHatgLazyMount");

    function renderState({ role, title, detail }) {
      const shell = document.createElement("div");
      shell.className = "lineageHatgEmpty";
      shell.setAttribute("role", role);
      const heading = document.createElement("strong");
      heading.textContent = title;
      const message = document.createElement("small");
      message.textContent = detail;
      shell.append(heading, message);
      root.replaceChildren(shell);
    }

    function renderLoading() {
      if (root.childElementCount || !active) return;
      renderState({
        role: "status",
        title: window.PortalI18n.t("正在加载 HATG"),
        detail: "图编辑能力仅在需要时载入。",
      });
    }

    function renderError(error) {
      if (destroyed) return;
      renderState({
        role: "alert",
        title: window.PortalI18n.t("HATG 加载失败"),
        detail: String(error?.message || error || "未知错误"),
      });
    }

    function targetIdentity(target = {}) {
      return `${String(target.lineageId || "").trim()}:${String(target.residentId || "").trim()}`;
    }

    function targetRequestsReveal(target = {}) {
      return target.revealInGraph === true;
    }

    function mergePendingTarget(next = {}) {
      const hasRevealHint = Object.prototype.hasOwnProperty.call(next, "revealInGraph");
      const hasProgramItem = Object.prototype.hasOwnProperty.call(next, "programItemId");
      const nextLineageId = String(next.lineageId || "").trim();
      const pendingLineageId = String(pendingTarget?.lineageId || "").trim();
      const preserveReveal = !hasRevealHint
        && pendingTarget
        && targetIdentity(pendingTarget) === targetIdentity(next)
        && targetRequestsReveal(pendingTarget);
      const merged = { ...next };
      if (targetRequestsReveal(next) || preserveReveal) merged.revealInGraph = true;
      if (hasProgramItem) {
        merged.programItemId = String(next.programItemId || "").trim();
      } else if (
        pendingTarget
        && pendingLineageId
        && pendingLineageId === nextLineageId
        && String(pendingTarget.programItemId || "").trim()
      ) {
        // A descriptor deep link is a one-shot target. Keep it through any
        // same-Lineage synchronization that happens while the lazy runtime is
        // loading, then consume it after the runtime applies it successfully.
        merged.programItemId = String(
          pendingTarget.programItemId || "",
        ).trim();
      }
      return merged;
    }

    function consumeRevealHint(revision) {
      if (revision !== targetRevision || !pendingTarget || !targetRequestsReveal(pendingTarget)) return;
      pendingTarget = { ...pendingTarget, revealInGraph: false };
    }

    function consumeProgramItemHint(revision) {
      if (
        revision !== targetRevision
        || !pendingTarget
        || !String(pendingTarget.programItemId || "").trim()
      ) return;
      pendingTarget = { ...pendingTarget };
      delete pendingTarget.programItemId;
    }

    async function applyPendingTarget(panel) {
      if (!panel) return null;
      await panel.ready;
      if (!active || !pendingTarget || appliedTargetRevision === targetRevision) {
        return pendingTarget ? { ...pendingTarget } : null;
      }
      const revision = targetRevision;
      const target = { ...pendingTarget };
      const key = targetApplicationKey(target);
      // The runtime start and the host page both apply the opening target; a
      // cold HATG read the same Lineage twice and dropped the first answer.
      let application = applyingTarget?.panel === panel && applyingTarget.key === key
        ? applyingTarget.promise
        : null;
      if (!application) {
        application = Promise.resolve(panel.setTarget?.(target));
        const entry = { panel, key, promise: application };
        applyingTarget = entry;
        application.finally(() => {
          if (applyingTarget === entry) applyingTarget = null;
        }).catch(() => {});
      }
      const result = await application;
      if (revision === targetRevision) appliedTargetRevision = revision;
      consumeRevealHint(revision);
      consumeProgramItemHint(revision);
      return result;
    }

    function targetApplicationKey(target = {}) {
      return JSON.stringify([
        String(target.lineageId || "").trim(),
        String(target.residentId || "").trim(),
        String(target.programItemId || "").trim(),
        target.revealInGraph === true,
      ]);
    }

    async function applyRuntimeState(panel) {
      if (!panel) return null;
      if (!active) {
        panel.hide?.();
        return panel;
      }
      panel.show?.();
      await applyPendingTarget(panel);
      return panel;
    }

    function ensureRuntime() {
      if (runtime) return Promise.resolve(runtime);
      if (!runtimePromise) {
        renderLoading();
        runtimePromise = loadAssets()
          .then(async module => {
            if (destroyed) return null;
            root.replaceChildren();
            const mountOptions = { ...options, root };
            mountOptions.onLocalTargetChange = next => {
              // Only an explicit runtime navigation supersedes the pending
              // target. A late location callback from an older load must not.
              pendingTarget = mergePendingTarget(next);
              targetRevision += 1;
            };
            for (const key of [
              "lineageId",
              "residentId",
              "programItemId",
              "revealInGraph",
            ]) delete mountOptions[key];
            runtime = module.mountHatgPanel(mountOptions);
            if (restoredState) runtime.restore?.(restoredState);
            if (pendingContext) await runtime.setContext?.(pendingContext);
            await applyRuntimeState(runtime);
            return runtime;
          })
          .catch(error => {
            runtimePromise = null;
            renderError(error);
            throw error;
          });
      }
      return runtimePromise;
    }

    const controller = Object.freeze({
      root,
      get ready() { return ensureRuntime().then(panel => panel?.ready); },
      setTarget(next = {}) {
        pendingTarget = mergePendingTarget(next);
        targetRevision += 1;
        if (!active) return Promise.resolve({ ...pendingTarget });
        if (runtime) return applyPendingTarget(runtime);
        return ensureRuntime().then(panel => panel?.snapshot?.() || { ...pendingTarget });
      },
      openResource(resource = {}) {
        const lineageId = String(resource.lineageId || "").trim();
        const residentId = String(resource.residentId || "").trim();
        if (!lineageId && !residentId) return Promise.resolve(controller.snapshot());
        const next = { lineageId, residentId };
        if (Object.prototype.hasOwnProperty.call(resource, "programItemId")) {
          next.programItemId = String(resource.programItemId || "").trim();
        }
        if (resource.revealInGraph === true) next.revealInGraph = true;
        return controller.setTarget(next);
      },
      suspendLineage(lineageId) {
        const suspendedId = String(lineageId || "").trim();
        if (!suspendedId) return null;
        const snapshot = controller.snapshot();
        if (String(snapshot.lineageId || "").trim() !== suspendedId) return null;
        const belongsToSuspendedLineage = target => String(
          target?.lineageId || "",
        ).trim() === suspendedId;
        if (belongsToSuspendedLineage(pendingTarget)) pendingTarget = null;
        if (belongsToSuspendedLineage(restoredState)) restoredState = null;
        targetRevision += 1;
        appliedTargetRevision = -1;
        runtime?.suspendLineage?.(suspendedId);
        return snapshot;
      },
      resumeLineage(snapshot = {}) {
        if (!String(snapshot.lineageId || "").trim()) {
          return Promise.resolve(controller.snapshot());
        }
        return controller.setTarget(snapshot);
      },
      applyLineageDeletion(lineageId) {
        const deletedId = String(lineageId || "").trim();
        if (!deletedId) return;
        const belongsToDeletedLineage = target => String(
          target?.lineageId || "",
        ).trim() === deletedId;
        if (belongsToDeletedLineage(pendingTarget)) {
          pendingTarget = null;
          targetRevision += 1;
          appliedTargetRevision = -1;
        }
        if (belongsToDeletedLineage(restoredState)) restoredState = null;
        runtime?.applyLineageDeletion?.(deletedId);
      },
      applyLineageMutation(lineage, options) {
        if (runtime) return runtime.applyLineageMutation?.(lineage, options) || Promise.resolve(false);
        if (!active) return Promise.resolve(false);
        return ensureRuntime().then(panel => panel?.applyLineageMutation?.(lineage, options) || false);
      },
      editLineage(next = {}) {
        return ensureRuntime().then(panel => panel?.editLineage?.(next));
      },
      createLineage(next = {}) {
        return ensureRuntime().then(panel => panel?.createLineage?.(next));
      },
      setContext(context = {}) {
        pendingContext = { ...context };
        if (!runtime && !active) return Promise.resolve({ ...pendingContext });
        return ensureRuntime().then(panel => panel?.setContext?.(context));
      },
      snapshot() {
        const snapshot = { ...(runtime?.snapshot?.() || restoredState || pendingTarget || {}) };
        delete snapshot.revealInGraph;
        return snapshot;
      },
      restore(snapshot = {}) {
        restoredState = { ...snapshot };
        pendingTarget = { ...snapshot };
        targetRevision += 1;
        appliedTargetRevision = -1;
        runtime?.restore?.(snapshot);
      },
      show() {
        active = true;
        root.hidden = false;
        if (runtime) void applyRuntimeState(runtime).catch(renderError);
        else void ensureRuntime().catch(() => {});
      },
      hide() {
        active = false;
        runtime?.hide?.();
        root.hidden = true;
      },
      destroy() {
        destroyed = true;
        runtime?.destroy?.();
        runtime = null;
        root.replaceChildren();
      },
    });
    return controller;
  }

  const panelPackage = {
    type: "hatg",
    version: 1,
    component: "PortalHatgLoader",
    presentation: { title: "HATG", icon: "network" },
    sizing: { minWidth: 420, minHeight: 240 },
    instances: "multiple",
    capabilities: ["graph-edit", "graph-history", "selection", "snapshot", "restore"],
    behavior: { canHide: true, canCollapse: true, canMove: true, closable: true },
    context: { channels: ["lineage", "lineageId", "graphSelection", "inspectionTarget"] },
    commands: [{
      id: "open-lineage",
      title: window.PortalI18n.t("在 HATG 中打开 Lineage"),
      capability: "graph-edit",
      execute: ({ panel, args }) => panel.openResource(args?.resource || args || {}),
    }, {
      id: "edit-lineage",
      title: window.PortalI18n.t("编辑当前 Lineage"),
      capability: "graph-edit",
      execute: ({ panel, args }) => panel.editLineage(args || {}),
    }],
    persistence: { schemaVersion: 1 },
    create,
  };
  window.PortalHatgLoader = Object.freeze({ load: loadAssets, panelPackage });
  if (window.PortalWorkbench) {
    if (typeof window.PortalWorkbench.registerPanelPackage !== "function") {
      throw new Error("PortalHatgLoader requires the PortalWorkbench Panel Package registry.");
    }
    window.PortalWorkbench.registerPanelPackage(panelPackage);
  }
})();
