(() => {
  "use strict";

  if (window.PortalHatgGraphCanvasEvents) return;

  // The canvas's DOM events: clicks, double clicks and context menus on cards,
  // ports and menus, which a double-click delay may hold back; hovering and
  // focusing them; keyboard navigation and zoom keys; background clicks; and
  // dropping a primitive from the palette.

  const HATG_PRIMITIVE_TRANSFER_TYPE = window.PortalCompatibility.identifier("primitive_transfer");

  const DEPENDENCIES = Object.freeze([
    "backgroundClickSuppressed", "cancelWireGesture", "concealFocusedPorts", "concealHoveredPorts", "connectToItem",
    "fit", "isWorkspaceNode", "nodeDoubleClickDelay", "nodeElement", "nodeLayer", "noteNavigation", "options",
    "portCompatibility", "portElements", "reset", "resolveNodeOpenMenu", "revealFocusedPorts", "revealHoveredPorts",
    "root", "screenToWorld", "state", "zoomBy",
  ]);

  function createCanvasEvents(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalHatgGraphCanvasEvents is missing: ${missing.join(", ")}`);
    const {
      backgroundClickSuppressed, cancelWireGesture, concealFocusedPorts, concealHoveredPorts, connectToItem, fit,
      isWorkspaceNode, nodeDoubleClickDelay, nodeElement, nodeLayer, noteNavigation, options, portCompatibility,
      portElements, reset, resolveNodeOpenMenu, revealFocusedPorts, revealHoveredPorts, root, screenToWorld, state,
      zoomBy,
    } = dependencies;

    const pendingNodeClickTimers = new Map();
    function cancelPendingNodeClick(nodeId) {
      window.clearTimeout(pendingNodeClickTimers.get(nodeId)?.timer);
      pendingNodeClickTimers.delete(nodeId);
    }
    function dispatchNodeClick(node, event, callback, hasDoubleClick) {
      if (!nodeDoubleClickDelay || !hasDoubleClick) return callback?.(node, event);
      const nodeId = String(node.id || "");
      cancelPendingNodeClick(nodeId);
      const pending = { run: () => callback?.(node, event) };
      pending.timer = window.setTimeout(() => {
        if (pendingNodeClickTimers.get(nodeId) === pending) pendingNodeClickTimers.delete(nodeId);
        pending.run();
      }, nodeDoubleClickDelay);
      pendingNodeClickTimers.set(nodeId, pending);
    }
    function flushPendingNodeClicks(keepNodeId = "") {
      for (const [nodeId, pending] of [...pendingNodeClickTimers]) {
        if (nodeId === keepNodeId) continue;
        window.clearTimeout(pending.timer);
        pendingNodeClickTimers.delete(nodeId);
        pending.run();
      }
    }
    function settleNodeSelection(event) {
      if (!pendingNodeClickTimers.size) return;
      // A click elsewhere commits the preceding single click before a toolbar
      // action reads the selection. Only a second click on the same card can
      // still become its double-click action.
      const card = event.target?.closest?.(".hatgNode");
      flushPendingNodeClicks(nodeLayer.contains(card) ? String(card.dataset.nodeId || "") : "");
    }
    root.ownerDocument.addEventListener("click", settleNodeSelection, true);

    // Cards, menus and ports are recreated as the graph changes. Handle their
    // ordinary DOM events at the stable layer; D3's node drag gesture remains
    // attached to each card.
    const delegatedNodeEvents = [
      "click", "dblclick", "contextmenu", "pointerenter", "pointerleave",
      "focusin", "focusout",
    ];
    function onNodeLayerEvent(event) {
      const target = event.target instanceof Element ? event.target : null;
      const control = target?.closest("button.hatgPort, button.hatgNodeOpenMenu, .hatgNode");
      if (!control || !nodeLayer.contains(control)) return;
      const port = control.classList.contains("hatgPort") ? control.__data__ : null;
      const menuItem = control.classList.contains("hatgNodeOpenMenu") ? control.__data__ : null;
      const node = control.classList.contains("hatgNode") ? control.__data__ : null;
      if (!port && !menuItem && !node) return;
      if (node && isWorkspaceNode(node)) {
        if (["click", "dblclick", "contextmenu"].includes(event.type)) {
          event.preventDefault();
          event.stopPropagation();
          if (event.type === "click" && node.kind === "workspace" && event.detail < 2) {
            dispatchNodeClick(node, event, options.onWorkspaceClick, typeof options.onWorkspaceDoubleClick === "function");
          } else if (event.type === "dblclick" && node.kind === "workspace") {
            cancelPendingNodeClick(String(node.id || ""));
            options.onWorkspaceDoubleClick?.(node, event);
          }
        }
        return;
      }

      if (event.type === "pointerenter" || event.type === "pointerleave"
          || event.type === "focusin" || event.type === "focusout") {
        if ((event.type === "pointerenter" || event.type === "pointerleave")
            && event.target !== control) return;
        if (event.relatedTarget instanceof Node && control.contains(event.relatedTarget)) return;
        const nodeId = port?.node_id || menuItem?.node.id || node?.id;
        if (event.type === "pointerenter") revealHoveredPorts(nodeId);
        else if (event.type === "pointerleave") concealHoveredPorts(nodeId, event.relatedTarget);
        else if (event.type === "focusin") {
          if (!node || control.matches(":focus-visible")) revealFocusedPorts(nodeId);
        } else concealFocusedPorts(nodeId, event.relatedTarget);
        return;
      }

      if (event.type === "click") {
        if (node && connectToItem(node, event)) {
          event.preventDefault();
          event.stopPropagation();
          return;
        }
        if (port) {
          event.preventDefault();
          event.stopPropagation();
          if (!state.model.readOnly) options.onPortClick?.(port, event, {
            wireDraft: state.model.wireDraft,
            compatible: portCompatibility(port),
          });
        } else if (menuItem) {
          event.preventDefault();
          event.stopPropagation();
          options.onNodeOpenMenu?.(menuItem.node, menuItem.menu, event, control);
        } else {
          event.stopPropagation();
          if (event.shiftKey || event.metaKey || event.ctrlKey) {
            flushPendingNodeClicks();
            options.onNodeClick?.(node, event);
          } else {
            dispatchNodeClick(node, event, options.onNodeClick, typeof options.onNodeDoubleClick === "function");
          }
        }
        return;
      }
      if (event.type === "dblclick") {
        event.preventDefault();
        event.stopPropagation();
        if (state.model.wireDraft) return;
        if (node) {
          const nodeId = String(node.id || "");
          cancelPendingNodeClick(nodeId);
          options.onNodeDoubleClick?.(node, event);
        }
        return;
      }
      if (event.type === "contextmenu" && node) {
        const menu = resolveNodeOpenMenu(node);
        if (!menu) return;
        event.preventDefault();
        event.stopPropagation();
        options.onNodeOpenMenu?.(node, menu, event, control);
        return;
      }
    }
    delegatedNodeEvents.forEach(type => nodeLayer.addEventListener(
      type, onNodeLayerEvent, type === "pointerenter" || type === "pointerleave",
    ));

    function onKeydown(event) {
      if (event.key === "Escape") cancelWireGesture();
      if (event.key === "Escape" && state.model.wireDraft) {
        event.preventDefault();
        event.stopPropagation();
        options.onWireDraftCancel?.(state.model.wireDraft, event);
        return;
      }
      if (!state.model.readOnly && event.key === "ArrowRight") {
        const node = event.target?.closest?.(".hatgNode")?.__data__;
        const output = node && portElements(node.id)
          .find(element => element.__data__.direction === "out");
        if (output && !state.model.wireDraft) {
          event.preventDefault();
          revealFocusedPorts(node.id);
          output.focus({ preventScroll: true });
          return;
        }
      }
      if (event.key === "ArrowLeft") {
        const port = event.target?.closest?.("button.hatgPort")?.__data__;
        if (port) {
          event.preventDefault();
          nodeElement(port.node_id)?.focus({ preventScroll: true });
          return;
        }
      }
      if (event.target !== root) return;
      if (["+", "=", "-", "_", "0", "f"].includes(event.key.toLowerCase())) noteNavigation();
      if (event.key === "Escape" && state.model.selectedNodeIds.length) {
        event.preventDefault();
        options.onSelectionCancel?.(event);
      } else if (["+", "="].includes(event.key)) {
        event.preventDefault();
        zoomBy(1.25);
      } else if (["-", "_"].includes(event.key)) {
        event.preventDefault();
        zoomBy(0.8);
      } else if (event.key === "0") {
        event.preventDefault();
        reset({ persist: true });
      } else if (event.key.toLowerCase() === "f") {
        event.preventDefault();
        fit({ persist: true });
      }
    }
    root.addEventListener("keydown", onKeydown);

    function onBackgroundClick(event) {
      if (event.defaultPrevented || backgroundClickSuppressed() || event.shiftKey) return;
      if (event.target?.closest?.(".hatgNode, .edgeLabelBtn, button, input, textarea, select, a")) return;
      if (state.model.wireDraft) {
        options.onWireDraftCancel?.(state.model.wireDraft, event);
        return;
      }
      options.onBackgroundClick?.(event);
    }
    root.addEventListener("click", onBackgroundClick);

    function primitiveTransfer(event) {
      return (
        !state.model.readOnly
        && typeof options.onPrimitiveDrop === "function"
        && Array.from(event.dataTransfer?.types || []).includes(HATG_PRIMITIVE_TRANSFER_TYPE)
      );
    }

    function onRootPaletteDragOver(event) {
      if (!primitiveTransfer(event)) return;
      event.preventDefault();
      if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
    }

    function onRootPaletteDrop(event) {
      const primitive = primitiveTransfer(event);
      if (!primitive) return;
      event.preventDefault();
      event.stopPropagation();
      const position = screenToWorld(event.clientX, event.clientY);
      // World coordinates are unbounded, including palette drops after zoom/pan.
      const normalizedPosition = {
        x: Math.round(position.x * 100) / 100,
        y: Math.round(position.y * 100) / 100,
      };
      if (primitive) {
        const kind = event.dataTransfer?.getData(HATG_PRIMITIVE_TRANSFER_TYPE) || "";
        if (kind) options.onPrimitiveDrop?.(kind, normalizedPosition, event);
        return;
      }
    }

    root.addEventListener("dragover", onRootPaletteDragOver);
    root.addEventListener("drop", onRootPaletteDrop);

    function destroyCanvasEvents() {
      for (const pending of pendingNodeClickTimers.values()) {
        window.clearTimeout(pending.timer);
      }
      pendingNodeClickTimers.clear();
      root.ownerDocument.removeEventListener("click", settleNodeSelection, true);
      delegatedNodeEvents.forEach(type => nodeLayer.removeEventListener(
        type, onNodeLayerEvent, type === "pointerenter" || type === "pointerleave",
      ));
      root.removeEventListener("keydown", onKeydown);
      root.removeEventListener("click", onBackgroundClick);
      root.removeEventListener("dragover", onRootPaletteDragOver);
      root.removeEventListener("drop", onRootPaletteDrop);
    }

    return {
      cancelPendingNodeClick, destroyCanvasEvents,
    };
  }

  window.PortalHatgGraphCanvasEvents = Object.freeze({ createCanvasEvents, dependencies: DEPENDENCIES });
})();
