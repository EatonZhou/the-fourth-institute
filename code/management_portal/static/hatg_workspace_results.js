(() => {
  "use strict";

  if (window.PortalHatgWorkspaceResults) return;

  // The Workspaces of the shown Lineage's Residents: reading the selected Resident's
  // results and the bindings the Workspace view draws, and opening a result file
  // in the unified Preview.
  const DEPENDENCIES = Object.freeze([
    "currentHatgComponentOptions", "currentNode", "displaySeedById", "renderGraph",
    "renderLineageHeader", "seedById", "state", "toast",
  ]);

  function createWorkspaceResults(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalHatgWorkspaceResults is missing: ${missing.join(", ")}`);
    const {
      currentHatgComponentOptions, currentNode, displaySeedById, renderGraph,
      renderLineageHeader, seedById, state, toast,
    } = dependencies;

    async function requestUnifiedPreview(request = {}) {
      const handler = currentHatgComponentOptions().onPreviewRequest
        || window.PortalPreview?.request;
      if (typeof handler !== "function") {
        throw new Error("统一 Preview 路由尚未初始化。");
      }
      await handler(request);
      return true;
    }

    function resolveWorkspacePreviewPath(value) {
      const path = String(value || "").replace(/\\/g, "/").replace(/^\/+|\/+$/g, "");
      if (!path) return "";
      const workspace = state.resultWorkspace || {};
      const entries = Array.isArray(workspace.entries) ? workspace.entries : [];
      const exact = entries.find(entry => String(entry?.path || "") === path);
      if (exact) return String(exact.path || path);

      const roots = Array.isArray(workspace.roots) ? workspace.roots : [];
      const directoryIds = new Set(roots.map(root => String(root?.directory_id || "")).filter(Boolean));
      if (directoryIds.has(path.split("/", 1)[0])) return path;

      const relativeMatches = entries.filter(entry => (
        String(entry?.rel_path || "") === path
        && !["directory", "symlink"].includes(String(entry?.type || entry?.kind || ""))
      ));
      if (relativeMatches.length === 1) return String(relativeMatches[0].path || path);
      const lineageMatch = relativeMatches.find(entry => entry?.is_lineage_workspace === true);
      if (lineageMatch) return String(lineageMatch.path || path);
      const workspaceMatch = relativeMatches.find(entry => String(entry?.root_kind || "") === "workspace");
      if (workspaceMatch) return String(workspaceMatch.path || path);

      const selectedSeed = displaySeedById(state.selectedSeedId) || seedById(state.selectedSeedId);
      const lineageRoot = roots.find(root => root?.is_lineage_workspace === true);
      if (
        lineageRoot?.directory_id
        && String(selectedSeed?.artifact_path || "").replace(/^\/+|\/+$/g, "") === path
      ) {
        return `${lineageRoot.directory_id}/${path}`;
      }
      const defaultRoot = roots.find(root => root?.is_default_cwd === true)
        || roots.find(root => root?.workspace_usage === "primary");
      return defaultRoot?.directory_id ? `${defaultRoot.directory_id}/${path}` : path;
    }

    async function loadResultFile(path, options = {}) {
      if (!state.lineage || !state.resultWorkspaceNodeId || !path) return false;
      const lineageId = state.lineage.lineage_id;
      const residentId = state.resultWorkspaceNodeId;
      const previewPath = resolveWorkspacePreviewPath(path);
      state.selectedResultPath = previewPath;
      return requestUnifiedPreview({
        locatorKind: "lineage-workspace",
        lineageId,
        residentId,
        path: previewPath,
        title: previewPath.split("/").pop() || window.PortalI18n.t("Preview"),
        source: "hatg",
        reload: options.reload === true,
      });
    }

    async function loadWorkspaceGraphBindings(lineage = state.lineage, options = {}) {
      if (!lineage?.lineage_id) {
        return { complete: false, failedCount: 0, stale: true };
      }
      const render = options.render !== false;
      const fit = options.fit !== false;
      const sequence = state.workspaceGraphLoadSeq;
      const lineageId = lineage.lineage_id;
      const missingNodes = (lineage.nodes || []).filter(node => !state.workspaceGraphBindings.has(node.id));
      if (!missingNodes.length) {
        if (render && state.graphViewMode === "workspace") renderGraph();
        return { complete: true, failedCount: 0, stale: false };
      }
      const results = await Promise.allSettled(missingNodes.map(async node => ({
        nodeId: node.id,
        workspace: await window.PortalResidentService.workspaces(node.id),
      })));
      if (sequence !== state.workspaceGraphLoadSeq || state.lineage?.lineage_id !== lineageId) {
        return { complete: false, failedCount: 0, stale: true };
      }
      const bindings = new Map(state.workspaceGraphBindings);
      results.forEach(result => {
        if (result.status === "fulfilled") bindings.set(result.value.nodeId, result.value.workspace);
      });
      state.workspaceGraphBindings = bindings;
      const failedCount = results.filter(result => result.status === "rejected").length;
      const complete = failedCount === 0
        && (lineage.nodes || []).every(node => bindings.has(node.id));
      if (render && state.graphViewMode === "workspace") {
        renderGraph();
        if (fit) {
          window.requestAnimationFrame(() => state.graphController?.fit?.({ duration: 220, maxScale: 1.15 }));
        }
      }
      return { complete, failedCount, stale: false };
    }

    async function loadNodeWorkspaces(node = currentNode()) {
      if (!state.lineage || !node) return null;
      const sequence = ++state.resultWorkspaceSeq;
      const lineageId = state.lineage.lineage_id;
      const nodeId = node.id;
      const workspace = await window.PortalResidentService.workspaces(nodeId);
      if (state.lineage?.lineage_id === lineageId) state.workspaceGraphBindings.set(nodeId, workspace);
      if (sequence !== state.resultWorkspaceSeq || state.lineage?.lineage_id !== lineageId || state.selectedNodeId !== nodeId) return null;
      state.resultWorkspace = workspace;
      state.resultWorkspaceNodeId = nodeId;
      renderLineageHeader();
      return workspace;
    }

    async function loadResults() {
      const node = currentNode();
      if (!state.lineage || !node) return;
      try {
        const workspace = await loadNodeWorkspaces(node);
        if (workspace && state.selectedResultPath) {
          await loadResultFile(state.selectedResultPath, { reload: true });
        }
      } catch (error) {
        toast(window.PortalI18n.text`成果列表刷新失败：${error.message || error}`, "error");
      }
    }

    return Object.freeze({
      loadResultFile, loadWorkspaceGraphBindings, loadResults,
    });
  }

  window.PortalHatgWorkspaceResults = Object.freeze({
    createWorkspaceResults, dependencies: DEPENDENCIES,
  });
})();
