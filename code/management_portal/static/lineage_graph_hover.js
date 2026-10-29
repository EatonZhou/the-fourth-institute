(() => {
  "use strict";

  if (window.PortalHatgGraphHover) return;

  // A node's ports and hover chrome show while the node is hovered, focused or
  // selected; leaving the node hides them after a short delay.

  const DEPENDENCIES = Object.freeze([
    "ownerChromeElements", "portElements", "root", "state",
  ]);

  function createHover(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalHatgGraphHover is missing: ${missing.join(", ")}`);
    const {
      ownerChromeElements, portElements, root, state,
    } = dependencies;

    let hoveredPortNodeId = "";
    let focusedPortNodeId = "";
    let syncedHoveredPortNodeId = "";
    let syncedFocusedPortNodeId = "";
    let syncedSelectedPortNodeIds = new Set();
    let portHoverClearTimer = 0;

    function interactiveNodeId(target) {
      if (!(target instanceof Element)) return "";
      const action = target.closest("button[data-action-node-id]");
      if (action) return String(action.dataset.actionNodeId || "");
      const port = target.closest("button.hatgPort[data-port-node-id]");
      if (port) return String(port.dataset.portNodeId || "");
      const node = target.closest(".hatgNode[data-node-id]");
      return String(node?.dataset.nodeId || "");
    }

    function syncPortOwnerVisibility({ full = false } = {}) {
      if (hoveredPortNodeId && !state.nodeMap.has(hoveredPortNodeId)) {
        hoveredPortNodeId = "";
      }
      if (focusedPortNodeId && !state.nodeMap.has(focusedPortNodeId)) {
        focusedPortNodeId = "";
      }
      if (hoveredPortNodeId) root.dataset.portHoverNodeId = hoveredPortNodeId;
      else delete root.dataset.portHoverNodeId;
      if (focusedPortNodeId) root.dataset.portFocusNodeId = focusedPortNodeId;
      else delete root.dataset.portFocusNodeId;
      const selectedNodeIds = new Set([
        ...(state.model.selectedNodeIds || []),
        ...(state.model.selectedNodeId ? [state.model.selectedNodeId] : []),
      ]);
      const affectedNodeIds = new Set();
      if (full) {
        state.nodeMap.forEach((_node, nodeId) => affectedNodeIds.add(nodeId));
      } else {
        [
          syncedHoveredPortNodeId,
          hoveredPortNodeId,
          syncedFocusedPortNodeId,
          focusedPortNodeId,
        ].filter(Boolean).forEach(nodeId => affectedNodeIds.add(nodeId));
        syncedSelectedPortNodeIds.forEach(nodeId => {
          if (!selectedNodeIds.has(nodeId)) affectedNodeIds.add(nodeId);
        });
        selectedNodeIds.forEach(nodeId => {
          if (!syncedSelectedPortNodeIds.has(nodeId)) affectedNodeIds.add(nodeId);
        });
      }
      affectedNodeIds.forEach(nodeId => {
        const hovered = nodeId === hoveredPortNodeId;
        const focused = nodeId === focusedPortNodeId;
        const selected = selectedNodeIds.has(nodeId);
        portElements(nodeId).forEach(port => {
          port.toggleAttribute("data-port-owner-hovered", hovered);
          port.toggleAttribute("data-port-owner-focused", focused);
          port.toggleAttribute("data-port-owner-selected", selected);
          if (hovered) port.dataset.portOwnerHovered = "true";
          if (focused) port.dataset.portOwnerFocused = "true";
          if (selected) port.dataset.portOwnerSelected = "true";
        });
        ownerChromeElements(nodeId).forEach(action => {
          action.toggleAttribute("data-node-owner-hovered", hovered);
          action.toggleAttribute("data-node-owner-focused", focused);
          if (hovered) action.dataset.nodeOwnerHovered = "true";
          if (focused) action.dataset.nodeOwnerFocused = "true";
        });
      });
      syncedHoveredPortNodeId = hoveredPortNodeId;
      syncedFocusedPortNodeId = focusedPortNodeId;
      syncedSelectedPortNodeIds = selectedNodeIds;
    }

    function revealHoveredPorts(nodeId) {
      window.clearTimeout(portHoverClearTimer);
      portHoverClearTimer = 0;
      hoveredPortNodeId = String(nodeId || "");
      syncPortOwnerVisibility();
    }

    function concealHoveredPorts(nodeId, relatedTarget) {
      if (interactiveNodeId(relatedTarget) === String(nodeId || "")) return;
      window.clearTimeout(portHoverClearTimer);
      portHoverClearTimer = window.setTimeout(() => {
        if (hoveredPortNodeId !== String(nodeId || "")) return;
        hoveredPortNodeId = "";
        syncPortOwnerVisibility();
      }, 240);
    }

    function revealFocusedPorts(nodeId) {
      focusedPortNodeId = String(nodeId || "");
      syncPortOwnerVisibility();
    }

    function concealFocusedPorts(nodeId, relatedTarget) {
      if (interactiveNodeId(relatedTarget) === String(nodeId || "")) return;
      if (focusedPortNodeId !== String(nodeId || "")) return;
      focusedPortNodeId = "";
      syncPortOwnerVisibility();
    }

    function destroyHover() {
      window.clearTimeout(portHoverClearTimer);
    }

    return {
      concealFocusedPorts, concealHoveredPorts, destroyHover, revealFocusedPorts, revealHoveredPorts,
      syncPortOwnerVisibility,
    };
  }

  window.PortalHatgGraphHover = Object.freeze({ createHover, dependencies: DEPENDENCIES });
})();
