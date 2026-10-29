(() => {
  "use strict";

  if (window.PortalHatgGraphPorts) return;

  // Program ports: the ports of each node, where they sit on its card, the
  // wire draft a selected output port starts and which ports it may connect to.

  const PROGRAM_WIRE_TYPES = new Set(["route", "target", "watch"]);
  const PROGRAM_PORT_TYPES = new Set([
    ...PROGRAM_WIRE_TYPES,
    "invoke",
  ]);

  const QUICK_FORK_PORT_CLEARANCE_PX = 24;

  const DEPENDENCIES = Object.freeze([
    "clamp", "isWorkspaceNode", "layers", "nodeHalfExtents", "nodeHasQuickFork", "nodePosition", "options",
    "portElements", "primitiveNodeKind", "root", "state", "worldSize",
  ]);

  function createPorts(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalHatgGraphPorts is missing: ${missing.join(", ")}`);
    const {
      clamp, isWorkspaceNode, layers, nodeHalfExtents, nodeHasQuickFork, nodePosition, options, portElements,
      primitiveNodeKind, root, state, worldSize,
    } = dependencies;

    let portsByNodeId = new Map();

    function resetPortCache() {
      portsByNodeId = new Map();
    }

    function normalizePort(node, port, index = 0) {
      const nodeId = String(node?.id || "");
      const nodeKind = String(port?.node_kind || primitiveNodeKind(node));
      const portId = String(port?.port_id || port?.id || "");
      const type = String(port?.type || "");
      const direction = String(port?.direction || "");
      return {
        ...port,
        key: String(port?.key || `${nodeId}:${direction}:${portId}:${index}`),
        node_id: nodeId,
        node_kind: nodeKind,
        port_id: portId,
        id: portId,
        type,
        direction,
        label: String(port?.label || type || portId),
      };
    }

    function portsForNode(node) {
      if (isWorkspaceNode(node)) return [];
      const nodeId = String(node?.id || "");
      if (nodeId && portsByNodeId.has(nodeId)) {
        return portsByNodeId.get(nodeId);
      }
      let ports = [];
      try {
        ports = typeof options.portsForNode === "function"
          ? options.portsForNode(node)
          : node?.ports;
      } catch (_error) {
        ports = [];
      }
      const normalized = (Array.isArray(ports) ? ports : [])
        .map((port, index) => normalizePort(node, port, index))
        .filter(port => (
          port.node_id
          && port.port_id
          && PROGRAM_PORT_TYPES.has(port.type)
          && ["in", "out"].includes(port.direction)
        ));
      if (nodeId) portsByNodeId.set(nodeId, normalized);
      return normalized;
    }

    function normalizeWireDraft(value) {
      if (!value || typeof value !== "object") return null;
      const nodeId = String(value.node_id || "");
      const node = state.nodeMap.get(nodeId);
      if (!node) return null;
      const normalized = normalizePort(node, value);
      if (
        !normalized.port_id
        || normalized.direction !== "out"
        || !PROGRAM_WIRE_TYPES.has(normalized.type)
      ) return null;
      return portsForNode(node).find(port => portIdentity(port) === portIdentity(normalized)) || null;
    }

    function portIdentity(port) {
      return [
        String(port?.node_id || ""),
        String(port?.direction || ""),
        String(port?.port_id || ""),
        String(port?.type || ""),
      ].join("\u0000");
    }

    function portCompatibility(port) {
      const source = state.model.wireDraft;
      if (!source || portIdentity(source) === portIdentity(port)) return null;
      if (typeof options.isPortCompatible === "function") {
        try {
          const result = options.isPortCompatible(source, port, {
            nodes: state.model.nodes,
            edges: state.model.edges,
            wireDraft: source,
          });
          return typeof result === "object" && result !== null ? !!result.ok : !!result;
        } catch (_error) {
          return false;
        }
      }
      return false;
    }

    function syncWireDraftPresentation() {
      if (state.model.wireDraft) {
        root.dataset.wireDraftType = state.model.wireDraft.type;
        root.dataset.wireDraftSourceId = state.model.wireDraft.node_id;
        root.dataset.wireDraftSourcePort = state.model.wireDraft.port_id;
      } else {
        delete root.dataset.wireDraftType;
        delete root.dataset.wireDraftSourceId;
        delete root.dataset.wireDraftSourcePort;
      }
      layers.nodes.selectAll("button.hatgPort")
        .attr("data-port-compatible", port => {
          const compatible = portCompatibility(port);
          return compatible === null ? null : String(compatible);
        })
        .attr("data-wire-draft-source", port => (
          state.model.wireDraft && portIdentity(port) === portIdentity(state.model.wireDraft) ? "true" : null
        ))
        .attr("aria-pressed", port => (
          state.model.wireDraft && portIdentity(port) === portIdentity(state.model.wireDraft) ? "true" : "false"
        ));
      layers.nodes.selectAll(".hatgNode")
        .attr("data-wire-target", node => targetChoices(node).length || null);
      layers.labels.selectAll("button.edgeLabelBtn")
        .attr("data-wire-target", edge => targetChoices(edge).length || null);
    }

    function targetChoices(item) {
      if (!state.model.wireDraft || state.model.readOnly) return [];
      const candidates = options.connectionTargets?.(state.model.wireDraft, item)
        || portsForNode(item).filter(port => port.direction === "in");
      return candidates.filter(port => portCompatibility(port) === true);
    }

    function connectToItem(item, event) {
      if (!state.model.wireDraft || state.model.readOnly) return false;
      const choices = targetChoices(item);
      if (choices.length === 1) options.onPortClick?.(choices[0], event);
      else if (choices.length > 1) {
        // Loop has two meaningful inputs; let the user choose explicitly.
        const first = portElements(item.id)
          .find(element => portIdentity(element.__data__) === portIdentity(choices[0]));
        first?.focus({ preventScroll: true });
      }
      return true;
    }

    function portPosition(port) {
      const node = state.nodeMap.get(port.node_id);
      if (!node) return { x: 50, y: 50 };
      const position = nodePosition(node);
      const half = nodeHalfExtents(node);
      const siblings = portsForNode(node).filter(candidate => candidate.direction === port.direction);
      const index = Math.max(0, siblings.findIndex(candidate => portIdentity(candidate) === portIdentity(port)));
      // Ports are real controls, not decorative dots. Distribute their
      // centers over the node height with enough room for the 20px pills;
      // dividing by the number of siblings made 3–4 foundational ports stack
      // on top of one another and let a different protocol intercept clicks.
      const portStep = clamp(
        half.y * 1.35 / Math.max(1, siblings.length - 1),
        2.35,
        4.2,
      );
      let offset = (index - (siblings.length - 1) / 2) * portStep;
      if (port.direction === "out" && port.port_id === "output" && primitiveNodeKind(node) === "agent") {
        // Keep Resident results below the central action, including in embedded
        // editors. Edges share this position with the interactive port.
        offset = Math.max(half.y * 0.55, QUICK_FORK_PORT_CLEARANCE_PX / worldSize.height * 100);
      } else if (port.direction === "out" && nodeHasQuickFork(node)) {
        // The quick-fork button owns the Agent's right-edge midpoint. Keep
        // protocol outputs above/below that stable action instead of stacking
        // a higher-z-index port over the original "+" control.
        const centerClearance = QUICK_FORK_PORT_CLEARANCE_PX / worldSize.height * 100;
        const upperCount = Math.ceil(siblings.length / 2);
        offset = index < upperCount
          ? -centerClearance - (upperCount - index - 1) * portStep
          : centerClearance + (index - upperCount) * portStep;
      }
      return {
        x: position.x + (port.direction === "in" ? -1 : 1) * (half.x + 0.28),
        y: position.y + offset,
      };
    }

    function updatePortGeometry() {
      layers.nodes.selectAll("button.hatgPort")
        .style("left", port => `${portPosition(port).x}%`)
        .style("top", port => `${portPosition(port).y}%`);
    }

    function renderPorts(nodes, { geometryChanged = true } = {}) {
      const ports = nodes.flatMap(portsForNode);
      layers.nodes.selectAll("button.hatgPort")
        .data(ports, port => port.key)
        .join(
          enter => enter.append("button").attr("type", "button"),
          update => update,
          exit => exit.remove(),
        )
        .attr("class", port => `hatgPort ${port.direction} ${port.type}`)
        .attr("data-port-node-id", port => port.node_id)
        .attr("data-port-node-kind", port => port.node_kind)
        .attr("data-port-id", port => port.port_id)
        .attr("data-port-type", port => port.type)
        .attr("data-port-direction", port => port.direction)
        .attr("data-node-action", "program-port")
        .attr("aria-label", port => (
          port.tooltip
            ? `${port.label} 端口：${port.tooltip}`
            : `${port.direction === "out" ? "输出" : "输入"} ${port.label}`
        ))
        .attr("title", null)
        .attr("data-portal-tooltip", port => (
          port.tooltip
          || `${port.direction === "out" ? "从这里开始建立连线" : "把兼容连线接到这里"}。`
        ))
        .property("disabled", state.model.readOnly)
        .style("position", "absolute")
        .style("transform", "translate(-50%, -50%)")
        .style("pointer-events", state.model.readOnly ? "none" : null)
        .text(port => port.label);
      if (geometryChanged) updatePortGeometry();
      layers.nodes.selectAll(".hatgNode")
        .attr("aria-keyshortcuts", node => !state.model.readOnly && portsForNode(node).some(port => port.direction === "out") ? "ArrowRight" : null);
      syncWireDraftPresentation();
    }

    return {
      connectToItem, normalizeWireDraft, portCompatibility, portIdentity, portPosition, portsForNode, renderPorts,
      resetPortCache, updatePortGeometry,
    };
  }

  window.PortalHatgGraphPorts = Object.freeze({ createPorts, dependencies: DEPENDENCIES });
})();
