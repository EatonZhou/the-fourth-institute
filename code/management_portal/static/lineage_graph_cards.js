(() => {
  "use strict";

  if (window.PortalHatgGraphCards) return;

  // Node cards and the chrome around them: the card itself, the Family
  // disclosure, node controls, hover actions and the open menu, and where that
  // chrome sits on the measured card.

  const DEPENDENCIES = Object.freeze([
    "animationDuration", "candidateForkTargetId", "concealFocusedPorts", "concealHoveredPorts", "isActiveStatus",
    "isWorkspaceNode", "layers", "nodeContent", "nodeContextHeat", "nodeDrag", "nodeHalfExtents", "nodePosition",
    "nodeStatus", "options", "primitiveNodeKind", "renderQuickForkButtons", "revealFocusedPorts",
    "revealHoveredPorts", "state", "worldSize",
  ]);

  function createCards(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalHatgGraphCards is missing: ${missing.join(", ")}`);
    const {
      animationDuration, candidateForkTargetId, concealFocusedPorts, concealHoveredPorts, isActiveStatus,
      isWorkspaceNode, layers, nodeContent, nodeContextHeat, nodeDrag, nodeHalfExtents, nodePosition, nodeStatus,
      options, primitiveNodeKind, renderQuickForkButtons, revealFocusedPorts, revealHoveredPorts, state, worldSize,
    } = dependencies;

    function renderNodes(nodes) {
      const actionNodes = nodes.filter(node => !isWorkspaceNode(node));
      const contentChanged = renderNodeCards(nodes);
      renderNodeDisclosures(actionNodes);
      renderQuickForkButtons(actionNodes);
      renderNodeControls(actionNodes);
      return contentChanged;
    }

    function renderNodeCards(nodes) {
      let contentChanged = false;
      const selection = layers.nodes.selectAll(".hatgNode")
        .interrupt()
        .style("opacity", 1)
        .data(nodes, node => `${node.kind === "workspace_resident" ? "marker" : "card"}:${node.id}`)
        .join(
          enter => enter.append(node => document.createElement(node.kind === "workspace_resident" ? "div" : "button"))
            .attr("type", node => node.kind === "workspace_resident" ? null : "button").style("opacity", 0)
            .call(items => items.transition().duration(animationDuration(180)).style("opacity", 1)),
          update => update,
          exit => exit.transition().duration(animationDuration(120)).style("opacity", 0).remove(),
        )
        .attr("class", node => {
          const selected = (
            node.id === state.model.selectedNodeId
            || state.model.selectedNodeIds.includes(node.id)
          );
          const handoffSource = state.model.handoffMode && node.id === (state.model.handoffAgentId || state.model.handoffSourceId);
          const seedClass = node.kind === "seed" ? ` seedNode seed-${node.seed_type || "other"}` : "";
          const familyClass = node._family_facade
            ? ` familyFacadeNode${node._family_expanded ? " is-expanded" : ""}`
            : "";
          const familyMemberClass = node._family_member ? " familyMemberNode" : "";
          const familyDimmedClass = node._family_dimmed ? " familyDimmed" : "";
          const dropTarget = candidateForkTargetId() === node.id ? " candidateForkDropTarget" : "";
          return `hatgNode${seedClass}${familyClass}${familyMemberClass}${familyDimmedClass}${selected ? " selected" : ""}${handoffSource ? " handoffSource" : ""}${state.model.readOnly ? " readOnly" : ""}${dropTarget}`;
        })
        .attr("data-family-tone", node => (Number.isInteger(node._family_tone) ? String(node._family_tone) : null))
        .attr("data-family-member-tone", node => (
          Number.isInteger(node._family_member?.tone) ? String(node._family_member.tone) : null
        ))
        .attr("data-family-role", node => node._family_member?.role || null)
        .attr("data-function-block-family", node => node.family ? "true" : null)
        .attr("data-node-id", node => node.id)
        .attr("role", node => node.kind === "workspace_resident" ? "img" : null)
        .attr("aria-label", node => (
          isWorkspaceNode(node) ? [node.title, node.workspace_id || node.resident_id || node.id].filter(Boolean).join(" · ")
            : primitiveNodeKind(node) === "function_block" ? node.title || node.id : null
        ))
        .attr("title", node => isWorkspaceNode(node) ? [node.title, node.workspace_path, node.workspace_id || node.resident_id || node.id].filter(Boolean).join("\n") : null)
        .attr("data-portal-tooltip", node => isWorkspaceNode(node) ? null : options.nodeTooltip?.(node) || null)
        .property("disabled", node => !isWorkspaceNode(node) && options.nodeDisabled?.(node) === true)
        .attr("aria-disabled", node => !isWorkspaceNode(node) && options.nodeDisabled?.(node) === true ? "true" : null)
        .attr("data-node-kind", primitiveNodeKind)
        .attr("data-block-tone", node => (
          primitiveNodeKind(node) === "function_block"
            ? node.tone || "violet"
            : null
        ))
        .attr("data-seed-type", node => node.kind === "seed" ? (node.seed_type || "other") : null)
        .attr("data-status", node => nodeStatus(node))
        .attr("data-context-heat", node => nodeContextHeat(node) || null)
        .attr("data-runtime-effect", node => isActiveStatus(nodeStatus(node)) ? "activity-rail" : null)
        .attr("data-semantic-level", state.semanticZoomLevel || "operate")
        .attr("aria-pressed", node => node.kind === "workspace_resident" ? null : (
          node.id === state.model.selectedNodeId
          || state.model.selectedNodeIds.includes(node.id)
        ) ? "true" : "false")
        .style("left", node => `${nodePosition(node).x}%`)
        .style("top", node => `${nodePosition(node).y}%`)
        .each(function updateNodeContent(node) {
          const content = nodeContent(node);
          if (this.__hatgNodeContent === content) return;
          this.innerHTML = content;
          this.__hatgNodeContent = content;
          contentChanged = true;
        });
      selection.call(nodeDrag);
      return contentChanged;
    }

    function renderNodeDisclosures(actionNodes) {
      const disclosures = typeof options.nodeDisclosure === "function"
        ? actionNodes.map(node => {
            let disclosure = null;
            try {
              disclosure = options.nodeDisclosure(node);
            } catch (_error) {
              disclosure = null;
            }
            return disclosure && typeof disclosure === "object"
              ? { node, disclosure }
              : null;
          }).filter(Boolean)
        : [];
      const disclosureGroups = layers.nodes.selectAll("div.hatgNodeDisclosureGroup")
        .data(disclosures, item => item.node.id)
        .join("div")
        .attr("class", "hatgNodeDisclosureGroup")
        .attr("data-action-node-id", item => item.node.id)
        .style("left", item => `${nodePosition(item.node).x}%`)
        .style("top", item => `${nodePosition(item.node).y}%`)
        .style("margin-left", item => nodeActionOffset(item, "x"))
        .style("margin-top", item => nodeActionOffset(item, "y"));
      disclosureGroups.selectAll("button.hatgNodeDisclosure")
        .data(item => [item]).join("button")
        .attr("type", "button")
        .attr("class", "hatgNodeDisclosure")
        .attr("data-node-action", "family-disclosure")
        .attr("data-disclosure-kind", item => String(item.disclosure.kind || "family"))
        .attr("data-action-node-id", item => item.node.id)
        .attr("data-expanded", item => item.disclosure.expanded ? "true" : "false")
        .attr("aria-expanded", item => item.disclosure.expanded ? "true" : "false")
        .attr("aria-label", item => item.disclosure.label || "展开内部 Channel")
        .attr("title", null)
        .attr("data-portal-tooltip", item => item.disclosure.title || item.disclosure.label || "展开内部 Channel")
        .text(item => item.disclosure.expanded ? "−" : "+")
        .on("click.hatg", (event, item) => {
          event.preventDefault();
          event.stopPropagation();
          options.onNodeDisclosure?.(item.node, item.disclosure, event);
        })
        .on("dblclick.hatg", event => {
          event.preventDefault();
          event.stopPropagation();
        });

      disclosureGroups.selectAll("button.hatgNodeDisclosureAction")
        .data(item => !state.model.readOnly && item.disclosure.action ? [item] : []).join("button")
        .attr("type", "button")
        .attr("class", "hatgNodeDisclosureAction")
        .attr("data-node-action", "disclosure-action")
        .attr("data-action-node-id", item => item.node.id)
        .attr("data-portal-tooltip", item => item.disclosure.action.title || item.disclosure.action.label)
        .attr("aria-label", item => item.disclosure.action.label)
        .attr("data-portal-icon", item => item.disclosure.action.iconName || null)
        .text(item => item.disclosure.action.iconName ? null : (item.disclosure.action.symbol || item.disclosure.action.label))
        .each(function () { window.PortalUIPrimitives.mountIcon(this); })
        .on("click.hatg", (event, item) => {
          event.preventDefault(); event.stopPropagation();
          options.onNodeDisclosureAction?.(item.node, item.disclosure, event);
        })
        .on("dblclick.hatg", event => { event.preventDefault(); event.stopPropagation(); });
    }

    function renderNodeControls(actionNodes) {
      const nodeControls = !state.model.readOnly && typeof options.nodeControl === "function"
        ? actionNodes.map(node => {
            let control = null;
            try {
              control = options.nodeControl(node);
            } catch (_error) {
              control = null;
            }
            return control && typeof control === "object" ? { node, control } : null;
          }).filter(Boolean)
        : [];
      layers.nodes.selectAll("button.hatgNodeControl")
        .data(nodeControls, item => item.node.id)
        .join(
          enter => enter.append("button").attr("type", "button"),
          update => update,
          exit => exit.remove(),
        )
        .attr("class", item => `hatgNodeControl action-${item.control.action || "custom"}${item.control.presentation === "hover" ? " is-hover-action" : ""}`)
        .attr("data-node-action", "")
        .attr("data-node-control", "")
        .attr("data-action-node-id", item => item.node.id)
        .attr("data-control-kind", item => item.control.kind || "custom")
        .attr("data-manual-event-trigger", item => (
          item.control.action === "emit-manual-event" ? "" : null
        ))
        .attr("data-event-source-control", item => (
          item.control.kind === "event_source" ? "" : null
        ))
        .attr("data-event-source-id", item => (
          item.control.kind === "event_source" ? item.node.id : null
        ))
        .attr("data-action", item => item.control.action || "custom")
        .attr("data-busy", item => item.control.busy ? "true" : null)
        .attr("aria-busy", item => item.control.busy ? "true" : "false")
        .attr("aria-label", item => item.control.label || item.control.title || "节点操作")
        .attr("title", null)
        .attr("data-portal-tooltip", item => item.control.title || "执行这个节点操作。")
        .property("disabled", item => Boolean(item.control.busy || item.control.disabled))
        .style("left", item => `${nodePosition(item.node).x}%`)
        .style("top", item => `${nodePosition(item.node).y}%`)
        // "hover" and "corner" controls sit at the node's top right corner;
        // a hover control shows only while the node is pointed at.
        .style("margin-left", item => {
          if (!["hover", "corner"].includes(item.control.presentation)) return null;
          return nodeActionOffset(item, "x");
        })
        .style("margin-top", item => {
          if (!["hover", "corner"].includes(item.control.presentation)) return null;
          return nodeActionOffset(item, "y");
        })
        .attr("data-portal-icon", item => item.control.busy ? null : (item.control.iconName || null))
        .text("")
        .each(function () { window.PortalUIPrimitives.mountIcon(this); })
        .on("click.hatg", (event, item) => {
          event.preventDefault();
          event.stopPropagation();
          if (item.control.busy || item.control.disabled) return;
          options.onNodeControl?.(item.node, item.control.action, event);
        })
        .on("dblclick.hatg", event => {
          event.preventDefault();
          event.stopPropagation();
        })
        .on("pointerenter.hatgPorts", (_event, item) => {
          revealHoveredPorts(item.node.id);
        })
        .on("pointerleave.hatgPorts", (event, item) => {
          concealHoveredPorts(item.node.id, event.relatedTarget);
        })
        .on("focus.hatgPorts", (_event, item) => {
          revealFocusedPorts(item.node.id);
        })
        .on("blur.hatgPorts", (event, item) => {
          concealFocusedPorts(item.node.id, event.relatedTarget);
        });
    }

    function nodeActionOffset(item, axis) {
      if (item.action?.placement === "below") {
        if (axis === "x") return `${(item.action.index - (item.actionCount - 1) / 2) * 28}px`;
        return `${nodeHalfExtents(item.node).y / 100 * worldSize.height + 8}px`;
      }
      const half = nodeHalfExtents(item.node)[axis] / 100
        * (axis === "x" ? worldSize.width : worldSize.height);
      if (primitiveNodeKind(item.node) === "function_block") {
        // Keep expand, run and menu on separate corners of the measured icon.
        const offset = Math.max(12, half - 8);
        if (axis === "x") return `${item.menu ? -offset : offset}px`;
        return `${item.menu || item.control ? offset : -offset}px`;
      }
      const offset = Math.max(item.menu ? 0 : 12, half - (item.menu ? 18 : 8));
      // Keep Family folding opposite the stop action at the top-right corner.
      if (axis === "x" && item.disclosure?.kind === "family") return `${-offset}px`;
      return `${axis === "x" ? offset : -offset}px`;
    }

    function syncNodeHoverActionOffsets() {
      layers.nodes.selectAll("div.hatgNodeDisclosureGroup, button.hatgNodeHoverAction, button.hatgNodeControl, button.hatgNodeOpenMenu")
        // Node controls follow the node's corner only when they sit there.
        .filter(item => !item?.control || ["hover", "corner"].includes(item.control.presentation))
        .style("margin-left", item => nodeActionOffset(item, "x"))
        .style("margin-top", item => nodeActionOffset(item, "y"));
    }

    function renderNodeHoverActions(nodes) {
      const actions = !state.model.readOnly && typeof options.nodeHoverActions === "function"
        ? nodes.filter(node => !isWorkspaceNode(node)).flatMap(node => {
            let actions = [];
            try {
              actions = options.nodeHoverActions(node);
            } catch (_error) {
              actions = [];
            }
            return Array.isArray(actions)
              ? actions.map(action => ({ node, action, actionCount: actions.length }))
              : [];
          })
        : [];
      layers.nodes.selectAll("button.hatgNodeHoverAction")
        .data(actions, item => `${item.node.id}:${item.action.action}`)
        .join("button")
        .attr("type", "button")
        .attr("class", item => `hatgNodeHoverAction action-${item.action.action || "custom"}`)
        .attr("data-node-action", "")
        .attr("data-node-hover-action", "")
        .attr("data-action-node-id", item => item.node.id)
        .attr("data-node-kind", item => primitiveNodeKind(item.node))
        .attr("data-action", item => item.action.action || "custom")
        .attr("data-action-kind", item => item.action.kind || "custom")
        .attr("data-busy", item => item.action.busy ? "true" : null)
        .attr("aria-busy", item => item.action.busy ? "true" : "false")
        .attr("aria-label", item => item.action.label || item.action.title || "节点操作")
        .attr("title", null)
        .attr("data-portal-tooltip", item => item.action.title || "执行这个节点操作。")
        .property("disabled", item => Boolean(item.action.busy || item.action.disabled))
        .style("left", item => `${nodePosition(item.node).x}%`)
        .style("top", item => `${nodePosition(item.node).y}%`)
        .style("margin-left", item => {
          return nodeActionOffset(item, "x");
        })
        .style("margin-top", item => {
          return nodeActionOffset(item, "y");
        })
        .attr("data-portal-icon", item => item.action.busy ? null : (item.action.iconName || null))
        .text("")
        .each(function () { window.PortalUIPrimitives.mountIcon(this); })
        .on("click.hatg", (event, item) => {
          event.preventDefault();
          event.stopPropagation();
          if (item.action.busy || item.action.disabled) return;
          options.onNodeHoverAction?.(item.node, item.action.action, event);
        })
        .on("dblclick.hatg", event => {
          event.preventDefault();
          event.stopPropagation();
        })
        .on("pointerenter.hatgPorts", (_event, item) => {
          revealHoveredPorts(item.node.id);
        })
        .on("pointerleave.hatgPorts", (event, item) => {
          concealHoveredPorts(item.node.id, event.relatedTarget);
        })
        .on("focus.hatgPorts", (_event, item) => {
          revealFocusedPorts(item.node.id);
        })
        .on("blur.hatgPorts", (event, item) => {
          concealFocusedPorts(item.node.id, event.relatedTarget);
        });
      syncNodeHoverActionOffsets();
    }

    function resolveNodeOpenMenu(node) {
      if (isWorkspaceNode(node) || typeof options.nodeOpenMenu !== "function") return null;
      try {
        const menu = options.nodeOpenMenu(node);
        return menu && typeof menu === "object" ? menu : null;
      } catch (_error) {
        return null;
      }
    }

    function renderNodeOpenMenus(nodes) {
      const menus = nodes
        .map(node => {
          const menu = resolveNodeOpenMenu(node);
          return menu ? { node, menu } : null;
        })
        .filter(Boolean);
      layers.nodes.selectAll("button.hatgNodeOpenMenu")
        .data(menus, item => item.node.id)
        .join("button")
        .attr("type", "button")
        .attr("class", "hatgNodeOpenMenu")
        .attr("data-node-action", "")
        .attr("data-node-open-menu", item => item.node.id)
        .attr("data-action-node-id", item => item.node.id)
        .attr("data-node-owner-selected", item => (
          item.node.id === state.model.selectedNodeId || state.model.selectedNodeIds.includes(item.node.id)
        ) ? "true" : null)
        .attr("aria-label", item => item.menu.label || `Open ${item.node.title || item.node.id}`)
        .attr("title", null)
        .attr("data-portal-tooltip", item => item.menu.title || "打开方式")
        .attr("data-portal-icon", "more-horizontal")
        .style("left", item => `${nodePosition(item.node).x}%`)
        .style("top", item => `${nodePosition(item.node).y}%`)
        .style("margin-left", item => {
          return nodeActionOffset(item, "x");
        })
        .style("margin-top", item => {
          return nodeActionOffset(item, "y");
        })
        .text("")
        .each(function () { window.PortalUIPrimitives.mountIcon(this); });
    }

    return {
      renderNodeHoverActions, renderNodeOpenMenus, renderNodes, resolveNodeOpenMenu, syncNodeHoverActionOffsets,
    };
  }

  window.PortalHatgGraphCards = Object.freeze({ createCards, dependencies: DEPENDENCIES });
})();
