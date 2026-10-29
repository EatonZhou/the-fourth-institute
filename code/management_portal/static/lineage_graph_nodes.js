(() => {
  "use strict";

  if (window.PortalHatgGraphNodes) return;

  // What a node is and shows: its kind, runtime status, card content and
  // context heat, its position, and its measured size, which the parts read as
  // half extents in world units.

  const ACTIVE_STATUSES = new Set(["queued", "running", "in_progress"]);

  const isActiveStatus = status => ACTIVE_STATUSES.has(status);

  const isWorkspaceNode = node => ["workspace", "workspace_resident"].includes(node?.kind);

  function workspaceNodeContent(node) {
    const escape = window.PortalUIPrimitives.escapeHtml;
    if (node.kind === "workspace_resident") {
      return `<span class="hatgWorkspaceResidentDot" aria-hidden="true"></span><span class="hatgWorkspaceResidentLabel"><strong>${escape(node.title || node.id)}</strong><span class="hatgWorkspaceNodeId">${escape(node.resident_id || node.id)}</span></span>`;
    }
    return `<span class="hatgWorkspaceNodeKind">Workspace</span><strong>${escape(node.title || node.workspace_id)}</strong><span class="hatgWorkspacePath">${escape(node.workspace_path)}</span><span class="hatgWorkspaceNodeId">${escape(node.workspace_id)}</span>`;
  }

  const DEPENDENCIES = Object.freeze([
    "invalidateEdgeGeometry", "layers", "nodeLayer", "number", "options", "state", "worldSize",
  ]);

  function createNodes(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalHatgGraphNodes is missing: ${missing.join(", ")}`);
    const {
      invalidateEdgeGeometry, layers, nodeLayer, number, options, state, worldSize,
    } = dependencies;

    function nodeStatus(node) {
      if (isWorkspaceNode(node)) return "idle";
      // Read-only projections own their displayed status instead of following
      // the live Resident runtime resolver.
      if (state.model.readOnly) return String(node?.status || "idle");
      return String(options.statusForNode?.(node) || node?.status || "idle");
    }

    const primitiveNodeKind = node => isWorkspaceNode(node) ? node.kind : window.PortalLineageProgram?.primitiveKind(node) || "agent";
    const nodeContent = node => isWorkspaceNode(node) ? workspaceNodeContent(node) : String(options.nodeContent?.(node, nodeStatus(node)) || "");

    function nodeHasQuickFork(node) {
      return (
        !state.model.readOnly
        && !isWorkspaceNode(node)
        && !node._family_facade
        && typeof options.onNodeQuickFork === "function"
        && primitiveNodeKind(node) === "agent"
      );
    }

    function nodeContextHeat(node) {
      if (!node || node.kind === "seed" || isWorkspaceNode(node)) return "";
      const pressure = options.contextPressureForNode?.(node);
      const level = String(pressure?.heat_level || "");
      return ["warm", "hot"].includes(level) ? level : "";
    }

    function nodePosition(node) {
      return {
        x: number(node?.x, 50),
        y: number(node?.y, 50),
      };
    }

    let nodeExtentMap = new Map();

    function refreshNodeExtentCache() {
      const next = new Map();
      layers.nodes.selectAll(".hatgNode").each(function rememberExtent(node) {
        if (!state.nodeMap.has(node?.id)) return;
        next.set(node.id, {
          x: Math.max(1.5, this.offsetWidth / worldSize.width * 50),
          y: Math.max(1.5, this.offsetHeight / worldSize.height * 50),
        });
      });
      const changed = next.size !== nodeExtentMap.size || [...next].some(([nodeId, extent]) => {
        const previous = nodeExtentMap.get(nodeId);
        return !previous || previous.x !== extent.x || previous.y !== extent.y;
      });
      nodeExtentMap = next;
      if (changed) {
        invalidateEdgeGeometry();
      }
      return changed;
    }

    function nodeHalfExtents(node) {
      return nodeExtentMap.get(node?.id) || { x: 7, y: 5 };
    }

    function getNodeSizes() {
      const nodeSizes = {};
      layers.nodes.selectAll(".hatgNode").each(function measureNode(node) {
        if (node?.id && this.offsetWidth && this.offsetHeight) nodeSizes[node.id] = {
          width: this.offsetWidth / worldSize.width * 100,
          height: this.offsetHeight / worldSize.height * 100,
        };
      });
      return nodeSizes;
    }

    function getPlacementGeometry(child) {
      const probe = document.createElement("button");
      probe.className = "hatgNode";
      probe.dataset.nodeKind = primitiveNodeKind(child);
      if (child.kind === "seed") probe.classList.add("seedNode");
      probe.type = "button";
      probe.tabIndex = -1;
      probe.setAttribute("aria-hidden", "true");
      probe.style.visibility = "hidden";
      probe.style.pointerEvents = "none";
      probe.innerHTML = String(options.nodeContent?.(child, "idle") || "");
      nodeLayer.appendChild(probe);
      try {
        return { node_sizes: getNodeSizes(), child_size: {
          width: probe.offsetWidth / worldSize.width * 100,
          height: probe.offsetHeight / worldSize.height * 100,
        } };
      } finally {
        probe.remove();
      }
    }

    return {
      getNodeSizes, getPlacementGeometry, isActiveStatus, isWorkspaceNode, nodeContent, nodeContextHeat,
      nodeHalfExtents, nodeHasQuickFork, nodePosition, nodeStatus, primitiveNodeKind, refreshNodeExtentCache,
    };
  }

  window.PortalHatgGraphNodes = Object.freeze({ createNodes, dependencies: DEPENDENCIES });
})();
