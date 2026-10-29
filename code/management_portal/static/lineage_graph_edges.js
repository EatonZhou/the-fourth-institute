(() => {
  "use strict";

  if (window.PortalHatgGraphEdges) return;

  // Drawing edges and their labels: an edge's kind, whether it is selected,
  // touches the selected node or carries running work, and what its label
  // says and opens.

  const DEPENDENCIES = Object.freeze([
    "connectToItem", "edgeGeometry", "endpointById", "isActiveStatus", "isProgramWire", "layers", "nodeStatus",
    "options", "parallelEdgeLane", "state",
  ]);

  function createEdges(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalHatgGraphEdges is missing: ${missing.join(", ")}`);
    const {
      connectToItem, edgeGeometry, endpointById, isActiveStatus, isProgramWire, layers, nodeStatus, options,
      parallelEdgeLane, state,
    } = dependencies;

    function edgeIsFlowing(edge) {
      if (state.model.readOnly || edge?.type === "workspace_binding") return false;
      if (typeof options.isEdgeFlowing === "function") return !!options.isEdgeFlowing(edge, state.nodeMap);
      return isActiveStatus(nodeStatus(endpointById(edge.source)))
        || isActiveStatus(nodeStatus(endpointById(edge.target)));
    }

    function edgeFlowState(edge) {
      const states = [endpointById(edge?.source), endpointById(edge?.target)].map(nodeStatus);
      return states.some(status => ["running", "in_progress"].includes(status)) ? "running" : "queued";
    }

    function edgeIsActive(edge) {
      const selectedEndpoint = state.model.selectedNodeId;
      return edge.source === selectedEndpoint || edge.target === selectedEndpoint;
    }

    function edgeSelectionId(edge) {
      return String(edge.interaction_edge_id || edge.id || "");
    }

    function edgeIsSelected(edge) {
      return Boolean(state.model.selectedEdgeId) && edgeSelectionId(edge) === state.model.selectedEdgeId;
    }

    function renderEdges(edges, _options = {}) {
      layers.edges.selectAll("path.hatgEdge")
        .interrupt()
        .style("opacity", 1)
        .data(edges, edge => edge.id)
        .join("path")
        .attr("data-edge-id", edge => edge.id)
        .attr("data-source-id", edge => edge.source || null)
        .attr("data-target-id", edge => edge.target || null)
        .attr("data-program-wire", edge => isProgramWire(edge) ? "true" : null)
        .attr("data-wire-type", edge => isProgramWire(edge) ? (edge.wire_type || edge.type || "") : null)
        .attr("data-source-port", edge => isProgramWire(edge) ? (edge.source_port || "") : null)
        .attr("data-target-port", edge => isProgramWire(edge) ? (edge.target_port || "") : null)
        .attr("data-derived-edge", edge => edge.derived_edge ? "true" : null)
        .attr("data-parallel-count", edge => String(parallelEdgeLane(edge.id)?.count || 1))
        .attr("data-parallel-lane", edge => String(parallelEdgeLane(edge.id)?.lane || 0))
        .attr("class", edge => {
          const active = edgeIsActive(edge);
          const selected = edgeIsSelected(edge) || state.model.selectedNodeIds.includes(edge.id);
          const flowing = edgeIsFlowing(edge);
          const dimmed = edge._family_dimmed ? " familyDimmed" : "";
          return `hatgEdge ${edge.type || "handoff"}${active ? " active" : ""}${selected ? " selected" : ""}${flowing ? " flowing" : ""}${dimmed}`;
        })
        .attr("data-through-family", edge => edge.through_family_facade ? "true" : null)
        .attr("data-through-function-block", edge => edge.through_function_block ? "true" : null)
        .attr("data-flow-state", edge => edgeIsFlowing(edge) ? edgeFlowState(edge) : null)
        .attr("data-truth-verified", edge => (edge.type !== "inheritance" || edge.truth?.verified) ? "true" : "false")
        .attr("data-absorption-status", edge => edge.type === "absorption" ? (edge.absorption_status || "queued") : null)
        .attr("data-handoff-status", edge => edge.type === "handoff" ? (edge.handoff_status || "draft") : null)
        .attr("data-draft-preview", edge => edge.draft_preview ? "true" : null)
        .attr("data-routing", edge => edgeGeometry(edge)?.routing || "direct")
        .attr("data-route-axis", edge => edgeGeometry(edge)?.routeAxis || null)
        .attr("d", edge => edgeGeometry(edge)?.d || "");
    }

    function edgeTooltip(edge) {
      if (edge.execution_summary) {
        return [edge.execution_summary, edge.execution_next_action]
          .filter(Boolean)
          .join("；");
      }
      if (["route"].includes(edge.type)) {
        const pending = Math.max(0, Number(edge.pending_event_count || 0));
        if (pending > 0) {
          const policy = ({
            drop: window.PortalI18n.t("目标忙碌时丢弃"),
            latest: window.PortalI18n.t("目标忙碌时只保留最新一次"),
            queue: window.PortalI18n.text`目标忙碌时最多排队 ${Number(edge.event_queue_limit || 10)} 次`,
          })[edge.event_unavailable_policy] || window.PortalI18n.t("使用当前等待策略");
          return window.PortalI18n.text`有 ${pending} 次触发正在等待目标接收；${policy}。点击查看或修改这条触发连线。`;
        }
        return window.PortalI18n.t("打开这条触发连线的设置；可配置发送内容、目标忙碌时如何处理，以及等待多久后过期。");
      }
      if (edge.type === "absorption") {
        return edge.absorption_status === "failed"
          ? window.PortalI18n.t("查看这次上下文吸收为什么投递失败，以及可以如何恢复。")
          : window.PortalI18n.t("查看这次上下文吸收向目标节点传递了什么。");
      }
      if (edge.type === "target") {
        return window.PortalI18n.t("查看这个动作绑定了哪个作用对象；这条目标绑定本身不会启动目标节点。");
      }
      if (edge.type === "watch") {
        return window.PortalI18n.t("查看事件正在监听哪个对象；监听关系本身不会启动被监听对象。");
      }
      if (edge.type === "candidate_fork") {
        return window.PortalI18n.t("候选 Fork：这个 Seed 的候选从来源 Resident 的工作出发，采纳时 Fork 它。点击可以删除这条关系。");
      }
      if (edge.draft_preview) {
        return window.PortalI18n.t("这是一条尚未生效的工作区交接；打开并保存配置后才会正式创建。");
      }
      if (edge.type === "handoff") {
        return window.PortalI18n.t("查看这次工作区交接的来源、目标、准备状态和目标 Agent 启动规则。");
      }
      return window.PortalI18n.t("打开这条关系的详情，并修改其中可编辑的设置。");
    }

    function renderLabels(edges) {
      layers.labels.selectAll("button.edgeLabelBtn")
        .data(edges.filter(edge => edge.type !== "workspace_binding"), edge => edge.id)
        .join("button")
        .attr("class", edge => `edgeLabelBtn ${edge.type || "handoff"}${edgeIsSelected(edge) ? " selected" : ""}${Number(edge.pending_event_count || 0) > 0 ? " hasPendingEvents" : ""}`)
        .attr("type", "button")
        .attr("data-edge-id", edge => edge.id)
        .attr("data-source-id", edge => edge.source || null)
        .attr("data-target-id", edge => edge.target || null)
        .attr("data-program-wire", edge => isProgramWire(edge) ? "true" : null)
        .attr("data-wire-type", edge => isProgramWire(edge) ? (edge.wire_type || edge.type || "") : null)
        .attr("data-source-port", edge => isProgramWire(edge) ? (edge.source_port || "") : null)
        .attr("data-target-port", edge => isProgramWire(edge) ? (edge.target_port || "") : null)
        .attr("data-pending-event-count", edge => (
          ["route"].includes(edge.type)
            ? String(Math.max(0, Number(edge.pending_event_count || 0)))
            : null
        ))
        .attr("data-derived-edge", edge => edge.derived_edge ? "true" : null)
        .attr("data-parallel-count", edge => String(parallelEdgeLane(edge.id)?.count || 1))
        .attr("data-parallel-lane", edge => String(parallelEdgeLane(edge.id)?.lane || 0))
        .attr("data-absorption-status", edge => edge.type === "absorption" ? (edge.absorption_status || "queued") : null)
        .attr("data-handoff-status", edge => edge.type === "handoff" ? (edge.handoff_status || "draft") : null)
        .attr("data-draft-preview", edge => edge.draft_preview ? "true" : null)
        .attr("aria-disabled", edge => state.model.readOnly || edge.visual_only ? "true" : null)
        .attr("disabled", edge => edge.visual_only ? true : null)
        .attr("title", null)
        .attr("data-portal-tooltip", edgeTooltip)
        .style("left", edge => `${edgeGeometry(edge)?.labelX ?? 50}%`)
        .style("top", edge => `${edgeGeometry(edge)?.labelY ?? 50}%`)
        .text(edge => edge.display_label || edge.label || edge.type || window.PortalI18n.t("关系"))
        .on("click.hatg", (event, edge) => {
          event.preventDefault();
          event.stopPropagation();
          if (edge.visual_only) return;
          if (connectToItem(edge, event)) return;
          options.onEdgeClick?.(edge, event);
        });
    }

    return {
      edgeIsActive, edgeIsSelected, edgeSelectionId, renderEdges, renderLabels,
    };
  }

  window.PortalHatgGraphEdges = Object.freeze({ createEdges, dependencies: DEPENDENCIES });
})();
