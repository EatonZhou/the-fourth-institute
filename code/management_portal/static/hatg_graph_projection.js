(() => {
  "use strict";

  if (window.PortalHatgGraphProjection) return;

  // What the HATG canvas draws: the visible nodes, edges and Function Block
  // boundaries of the context view, with folded Families, focus and relation
  // filters applied, and the Workspace view of the Residents' bound folders.
  const DEPENDENCIES = Object.freeze([
    "activeSeeds", "autoLayoutScope", "displayedProgram", "edgesThroughFoldedFamilies",
    "effectiveLeaderNode", "familyExpanded", "familyForFacade", "familyMembership",
    "familyObjectIds", "familyPigeonLinks", "familySpotlight", "familyTone", "focusedFamily",
    "foldedFamilyObjects", "functionBlockProjection", "graphSelectedId", "hatgViewPreferences",
    "programExecutionExplanation", "readableAutoLayoutOptions", "relationFamily",
    "relationVisibleInView", "state",
  ]);

  function createGraphProjection(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalHatgGraphProjection is missing: ${missing.join(", ")}`);
    const {
      activeSeeds, autoLayoutScope, displayedProgram, edgesThroughFoldedFamilies,
      effectiveLeaderNode, familyExpanded, familyForFacade, familyMembership, familyObjectIds,
      familyPigeonLinks, familySpotlight, familyTone, focusedFamily, foldedFamilyObjects,
      functionBlockProjection, graphSelectedId, hatgViewPreferences, programExecutionExplanation,
      readableAutoLayoutOptions, relationFamily, relationVisibleInView, state,
    } = dependencies;

    let graphProgramProjectionCache = null;

    function decorateGraphEdge(edge) {
      if (edge._program_wire) {
        return edge;
      }
      if (edge.type === "handoff") {
        const statusLabel = ({
          draft: edge.draft_preview ? window.PortalI18n.t("待配置 Handoff") : window.PortalI18n.t("Handoff 草稿"),
          preparing: window.PortalI18n.t("正在准备 Workspace"),
          ready: edge.execution?.status === "queued" ? window.PortalI18n.t("Workspace 就绪 · Agent 已启动") : window.PortalI18n.t("Workspace 已就绪"),
          failed: window.PortalI18n.t("Handoff 失败"),
        })[edge.status] || edge.label || window.PortalI18n.t("Workspace Handoff");
        return {
          ...edge,
          family: "workspace",
          handoff_status: edge.status || "draft",
          display_label: statusLabel,
        };
      }
      if (edge.type === "absorption") {
        return {
          ...edge,
          family: "context",
          absorption_status: edge.absorption?.status || "queued",
          display_label: edge.absorption?.status === "failed" ? "吸收失败" : (edge.label || "节点吸收"),
        };
      }
      return { ...edge, family: relationFamily(edge) };
    }

    function workspaceGraphModel() {
      const projection = window.PortalHatgWorkspaceProjection.project(
        state.lineage?.nodes || [], (state.lineage?.edges || []).map(decorateGraphEdge), state.workspaceGraphBindings,
      );
      const positions = window.PortalHatgGraph.computeWorkspaceGraphLayout(projection.nodes, projection.edges);
      const savedPositions = hatgViewPreferences.get(state.lineage?.lineage_id)?.workspace_positions || {};
      const overviewNodes = projection.nodes.map(node => ({
        ...node, ...(savedPositions[node.id] || positions.get(node.id)), navigation_position: true,
      }));
      const overviewEdges = projection.edges;
      const selectedId = overviewNodes.some(node => node.id === state.selectedWorkspaceId)
        ? state.selectedWorkspaceId : "";
      const visibleIds = new Set(state.graphFocusMode && selectedId
        ? [selectedId] : overviewNodes.map(node => node.id));
      if (state.graphFocusMode && selectedId) overviewEdges.forEach(edge => {
        if (edge.source === selectedId) visibleIds.add(edge.target);
        if (edge.target === selectedId) visibleIds.add(edge.source);
      });
      return {
        overviewNodes, overviewEdges, selectedId,
        functionBlockBoundaries: [], familySpotlightFacadeId: "",
        nodes: overviewNodes.filter(node => visibleIds.has(node.id)),
        edges: overviewEdges.filter(edge => visibleIds.has(edge.source) && visibleIds.has(edge.target)
          && (state.graphRelationFilter === "all" || edge.type === state.graphRelationFilter)),
      };
    }

    function graphVisibleModel() {
      if (state.graphViewMode === "workspace") return workspaceGraphModel();
      const display = state.lineage;
      const mode = state.graphViewMode;
      const shownNodes = display?.nodes || [];
      const projectionKey = display && mode === "context"
        ? [
            display.lineage_id || "",
            Number(display.revision || 0),
            Number(display.layout_revision || 0),
            Number(state.localVersion || 0),
            "latest",
            [...state.expandedFamilyFacadeIds].sort().join(","),
            (display.function_blocks || []).length,
            (display.nodes || []).length,
            (display.program?.components || []).length,
            (display.program?.wires || []).length,
          ].join("\u0000")
        : "";
      const folded = mode === "context"
        ? foldedFamilyObjects()
        : { residents: new Set(), instances: new Set(), standIn: new Map() };
      const programProjection = (
        mode === "context"
        && display
        && window.PortalLineageProgram?.projectGraph
      )
        ? (
            // The projection copies the displayed nodes. Every commit installs
            // a new Lineage object, so a cache entry is reused only for the
            // object it was built from, whatever changed inside the commit.
            graphProgramProjectionCache?.lineage === display
            && graphProgramProjectionCache.key === projectionKey
              ? graphProgramProjectionCache.projection
              : (() => {
                  const program = displayedProgram();
                  const projection = window.PortalLineageProgram.projectGraph(
                    {
                      program,
                      edges: display.edges || [],
                      function_blocks: display.function_blocks || [],
                    },
                    shownNodes,
                  );
                  graphProgramProjectionCache = { lineage: display, key: projectionKey, projection };
                  return projection;
                })()
          )
        : {
            nodes: shownNodes,
            components: [],
            edges: [],
          };
      const eventWireRuntime = display?.program_invoke_wire_runtime || {};
      const programEdges = (programProjection.edges || []).map(edge => {
        const explanation = programExecutionExplanation(edge.id);
        const runtime = ["route"].includes(edge.type)
          ? (eventWireRuntime[edge.id] || {})
          : {};
        const pendingEventCount = Math.max(
          0,
          Math.trunc(Number(runtime.pending_count) || 0),
        );
        const baseLabel = edge.label
          || window.PortalLineageProgram?.manifest?.wire_types?.[edge.type]?.title
          || edge.type;
        return {
          ...edge,
          kind: edge.type,
          family: "context",
          _program_wire: true,
          pending_event_count: pendingEventCount,
          event_unavailable_policy: String(
            runtime.unavailable_policy
            || edge.config?.unavailable_policy
            || "drop",
          ),
          event_queue_limit: Number(
            runtime.queue_limit || edge.config?.queue_limit || 10,
          ),
          event_next_expires_at: String(runtime.next_expires_at || ""),
          execution_summary: String(explanation?.summary || ""),
          execution_next_action: String(explanation?.next_action || ""),
          execution_state: String(explanation?.state || ""),
          display_label: pendingEventCount
            ? `${baseLabel} · ${pendingEventCount} 待处理`
            : baseLabel,
        };
      });
      // The Program projection holds every Seed; the canvas shows the active
      // ones once, each with its projected ports and its own display fields.
      const projectedSeeds = new Map(
        (programProjection.nodes || []).filter(node => node.kind === "seed").map(node => [node.id, node]),
      );
      const seeds = mode === "context"
        ? activeSeeds(display).map(seed => ({ ...(projectedSeeds.get(seed.id) || {}), ...seed, kind: "seed", status: "active" }))
        : [];
      const leaderNodeId = effectiveLeaderNode(display)?.id || "";
      const existingHandoff = (display?.edges || []).some(edge => (
        edge.type === "handoff"
        && edge.source === state.handoffSourceId
        && edge.target === state.handoffTargetId
      ));
      const draftHandoff = (
        state.handoffMode
        && state.handoffSourceId
        && state.handoffTargetId
        && !existingHandoff
      ) ? [{
        id: "handoff_draft_preview",
        source: state.handoffSourceId,
        target: state.handoffTargetId,
        type: "handoff",
        label: window.PortalI18n.t("待配置 Handoff"),
        status: "draft",
        draft_preview: true,
      }] : [];
      const persistedDecoratedEdges = [
        ...(display?.edges || []),
        ...programEdges,
        ...draftHandoff,
      ].map(decorateGraphEdge);
      const decoratedEdges = persistedDecoratedEdges;
      let overviewNodes;
      let overviewEdges;
      const selectedId = graphSelectedId();
      const membership = mode === "context" ? familyMembership(folded.residents) : new Map();
      overviewNodes = [...(programProjection.nodes || []).filter(node => node.kind !== "seed"), ...seeds].filter(node => !folded.residents.has(node.id) && !folded.instances.has(node.id)).map(node => {
        const family = familyForFacade(node.id);
        const member = membership.get(node.id);
        return {
          ...node,
          _is_leader: node.id === leaderNodeId,
          ...(family ? {
            _family_facade: true,
            _family_expanded: familyExpanded(family.roles),
            _family_tone: familyTone(family.roles),
          } : {}),
          ...(member ? { _family_member: member } : {}),
        };
      });
      overviewEdges = [
        ...edgesThroughFoldedFamilies(
          decoratedEdges.filter(edge => relationVisibleInView(edge, mode)),
          folded.standIn,
        ),
        ...familyPigeonLinks(),
      ];

      const blockProjection = mode === "context" ? functionBlockProjection(overviewNodes, overviewEdges) : null;
      if (blockProjection) { overviewNodes = blockProjection.nodes; overviewEdges = blockProjection.edges; }
      let visibleIds = new Set(overviewNodes.map(node => node.id));
      const familyInFocus = mode === "context" ? focusedFamily() : null;
      if (familyInFocus) {
        // Include the Family's workflow and nested visible members.
        visibleIds = familyObjectIds(familyInFocus);
      } else if (state.graphFocusMode && selectedId) {
          visibleIds = new Set([selectedId]);
          overviewEdges.forEach(edge => {
            if (edge.source === selectedId) visibleIds.add(edge.target);
            if (edge.target === selectedId) visibleIds.add(edge.source);
          });
      }
      let nodes = overviewNodes.filter(node => visibleIds.has(node.id));
      if (state.graphFocusMode && selectedId && mode !== "workspace" && !familyInFocus) {
        const neighbors = nodes.filter(node => node.id !== selectedId);
        const positions = new Map([[selectedId, { x: 50, y: 50 }]]);
        neighbors.forEach((node, index) => {
          const angle = Math.PI + (Math.PI * 2 * index / Math.max(1, neighbors.length));
          positions.set(node.id, {
            x: 50 + Math.cos(angle) * 32,
            y: 50 + Math.sin(angle) * 30,
          });
        });
        nodes = nodes.map(node => ({ ...node, ...positions.get(node.id), navigation_position: true }));
      }
      const visibleEndpointIds = visibleIds;
      let edges = overviewEdges.filter(edge => (
        visibleEndpointIds.has(edge.source)
        && visibleEndpointIds.has(edge.target)
        && (
          state.graphRelationFilter === "all"
          || (
            state.graphRelationFilter === "program"
              ? Boolean(edge._program_wire)
              : edge.type === state.graphRelationFilter
          )
        )
      ));
      if (familyInFocus) {
        const positions = window.PortalHatgGraph.computeBranchAwareLayout(nodes, edges, {
          ...readableAutoLayoutOptions(nodes, state.autoLayoutDensityByScope.get(autoLayoutScope())),
          maxRowsPerLane: 5,
          feedbackEdgeTypes: ["watch", "target"],
          leaderId: familyInFocus.roles.facade,
          rootIds: [familyInFocus.roles.facade],
        });
        nodes = nodes.map(node => ({ ...node, ...positions.get(node.id), navigation_position: true }));
      }
      const spotlight = mode === "context" ? familySpotlight() : null;
      if (spotlight) {
        const inFamily = familyObjectIds(spotlight);
        nodes = nodes.map(node => (inFamily.has(node.id) ? node : { ...node, _family_dimmed: true }));
        edges = edges.map(edge => (
          inFamily.has(edge.source) && inFamily.has(edge.target) ? edge : { ...edge, _family_dimmed: true }
        ));
      }
      return {
        nodes,
        edges,
        familySpotlightFacadeId: spotlight?.roles.facade || "",
        functionBlockBoundaries: blockProjection?.boundaries || [],
        overviewNodes,
        overviewEdges,
        selectedId,
      };
    }

    return Object.freeze({
      workspaceGraphModel, graphVisibleModel,
    });
  }

  window.PortalHatgGraphProjection = Object.freeze({
    createGraphProjection, dependencies: DEPENDENCIES,
  });
})();
