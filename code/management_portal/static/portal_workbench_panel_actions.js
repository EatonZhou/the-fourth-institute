(function (global) {
  "use strict";

  if (global.PortalWorkbenchPanelActions) return;

  // What the controller does to Panels: focusing, collapsing, expanding,
  // hiding, closing, showing and adding them, renaming them and applying a
  // preset or saved workspace.

  const DEPENDENCIES = Object.freeze([
    "SPLIT_HANDLE_SIZE", "callPanelLifecycle", "canvas", "clone", "closeMenu", "collectPanelIds", "descriptorOf",
    "descriptors", "findNode", "findPanel", "firstStack", "hiddenPanelsForLayout", "id", "isLayoutLocked",
    "lastOpenTargetByType", "minimumForNode", "normalizeCurrent", "normalizeNode", "options", "panelIds", "panelNode",
    "panelPlacement", "placements", "registerPanelCommands", "removePanelFromNode", "render", "replaceNode", "root",
    "runtimeContext", "shellState", "splitNode", "stackNode", "unregisterPanelCommands",
  ]);

  function createPanelActions(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalWorkbenchPanelActions is missing: ${missing.join(", ")}`);
    const {
      SPLIT_HANDLE_SIZE, callPanelLifecycle, canvas, clone, closeMenu, collectPanelIds, descriptorOf, descriptors,
      findNode, findPanel, firstStack, hiddenPanelsForLayout, id, isLayoutLocked, lastOpenTargetByType,
      minimumForNode, normalizeCurrent, normalizeNode, options, panelIds, panelNode, panelPlacement, placements,
      registerPanelCommands, removePanelFromNode, render, replaceNode, root, runtimeContext, shellState, splitNode,
      stackNode, unregisterPanelCommands,
    } = dependencies;

    function focusPanel(panelId) {
      const descriptor = descriptors.get(String(panelId));
      if (!descriptor) return false;
      if (isLayoutLocked() && !canFocusFixedPanel(descriptor.id)) return false;
      shellState.mobileActivePanelId = descriptor.id;
      if (!descriptor.openSource) shellState.activeOpenTargetPanelId = descriptor.id;
      if (shellState.hidden.has(descriptor.id)) return showPanel(descriptor.id);
      if (global.PortalResponsive?.isMobile?.()) {
        render("focus-panel");
        return true;
      }
      shellState.collapsed.delete(descriptor.id);
      const placement = findPanel(shellState.model, descriptor.id);
      if (placement?.stack) {
        placement.stack.activePanelId = descriptor.id;
        shellState.focusedStackId = placement.stack.id;
      }
      render("focus-panel");
      return true;
    }

    function collapsePanel(panelId) {
      if (isLayoutLocked()) return false;
      const descriptor = descriptors.get(String(panelId));
      if (!descriptor?.canCollapse || shellState.hidden.has(descriptor.id)) return false;
      shellState.collapsed.add(descriptor.id);
      render("collapse-panel");
      return true;
    }

    function expandPanel(panelId) {
      if (isLayoutLocked()) return false;
      const descriptor = descriptors.get(String(panelId));
      if (!descriptor || shellState.hidden.has(descriptor.id)) return false;
      shellState.collapsed.delete(descriptor.id);
      const placement = findPanel(shellState.model, descriptor.id);
      if (placement?.stack) placement.stack.activePanelId = descriptor.id;
      render("expand-panel");
      return true;
    }

    function toggleCollapsed(panelId) {
      return shellState.collapsed.has(String(panelId)) ? expandPanel(panelId) : collapsePanel(panelId);
    }

    function hidePanel(panelId) {
      if (isLayoutLocked()) return false;
      const descriptor = descriptors.get(String(panelId));
      if (!descriptor?.canHide) return false;
      const placement = panelPlacement(shellState.model, descriptor.id);
      if (placement) placements[descriptor.id] = placement;
      shellState.model = removePanelFromNode(clone(shellState.model), descriptor.id);
      shellState.hidden.add(descriptor.id);
      shellState.collapsed.delete(descriptor.id);
      normalizeCurrent();
      render("hide-panel");
      return true;
    }

    function closePanel(panelId) {
      if (isLayoutLocked()) return false;
      const descriptor = descriptors.get(String(panelId));
      if (!descriptor) return false;
      if (!descriptor.closable) return hidePanel(descriptor.id);
      const close = approved => {
        if (approved === false || shellState.destroyed || isLayoutLocked() || descriptors.get(descriptor.id) !== descriptor) return false;
        shellState.model = removePanelFromNode(clone(shellState.model), descriptor.id);
        shellState.hidden.delete(descriptor.id);
        shellState.collapsed.delete(descriptor.id);
        delete placements[descriptor.id];
        const wasVisible = shellState.activeRuntimePanelIds.delete(descriptor.id);
        unregisterPanelCommands(descriptor.id);
        descriptors.delete(descriptor.id);
        panelIds.delete(descriptor.id);
        if (shellState.activeOpenTargetPanelId === descriptor.id) shellState.activeOpenTargetPanelId = "";
        if (lastOpenTargetByType.get(descriptor.type) === descriptor.id) lastOpenTargetByType.delete(descriptor.type);
        if (wasVisible) {
          callPanelLifecycle(descriptor, "hide", runtimeContext);
          descriptor.onHide?.({ workbench: shellState.controller, panel: descriptor });
        }
        callPanelLifecycle(descriptor, "disconnect", runtimeContext);
        if (descriptor.ownsRuntime) callPanelLifecycle(descriptor, "destroy", runtimeContext);
        descriptor.contextScope.dispose();
        descriptor.element.remove();
        normalizeCurrent();
        render("close-editor");
        return true;
      };
      const decision = descriptor.runtime?.canClose?.();
      return decision && typeof decision.then === "function"
        ? Promise.resolve(decision).then(close)
        : close(decision);
    }

    function showPanel(panelId, optionsValue = {}) {
      if (isLayoutLocked()) return focusPanel(panelId);
      const descriptor = descriptors.get(String(panelId));
      if (!descriptor) return false;
      shellState.hidden.delete(descriptor.id);
      shellState.collapsed.delete(descriptor.id);
      shellState.model = removePanelFromNode(clone(shellState.model), descriptor.id);
      const remembered = placements[descriptor.id] || {};
      const targetStackId = String(optionsValue.stackId || remembered.stackId || shellState.focusedStackId);
      const zone = String(optionsValue.zone || remembered.zone || "center");
      const targetStack = findNode(shellState.model, targetStackId)?.node;
      const rememberedSplit = !optionsValue.stackId && !optionsValue.zone
        ? findNode(shellState.model, remembered.splitId)?.node
        : null;
      if (rememberedSplit?.type === "split") {
        const insertAt = Math.max(0, Math.min(
          rememberedSplit.children.length,
          Number.isInteger(remembered.index) ? remembered.index : rememberedSplit.children.length,
        ));
        rememberedSplit.children.splice(insertAt, 0, stackNode([descriptor.id]));
        rememberedSplit.weights.splice(insertAt, 0, Number(remembered.weight) || 1);
      } else if (targetStack?.type === "stack") {
        if (zone === "center") {
          targetStack.children.push(panelNode(descriptor.id));
          targetStack.activePanelId = descriptor.id;
        } else {
          const orientation = ["top", "bottom"].includes(zone) ? "vertical" : "horizontal";
          const sourceStack = stackNode([descriptor.id]);
          const sourceWeight = Number(remembered.weight) || 1;
          const targetWeight = Number(remembered.siblingWeight) || 1;
          const replacement = ["left", "top"].includes(zone)
            ? splitNode(orientation, [sourceStack, targetStack], [sourceWeight, targetWeight])
            : splitNode(orientation, [targetStack, sourceStack], [targetWeight, sourceWeight]);
          shellState.model = replaceNode(shellState.model, targetStack.id, replacement);
        }
      } else if (shellState.model) {
        const sourceStack = stackNode([descriptor.id]);
        const existingMinimum = minimumForNode(shellState.model, "x", descriptors, shellState.collapsed);
        const sourceMinimum = minimumForNode(sourceStack, "x", descriptors, shellState.collapsed);
        const availableWidth = canvas.clientWidth;
        const receivingStack = firstStack(shellState.model.children?.at(-1)) || firstStack(shellState.model);
        if (
          receivingStack && availableWidth > 0
          && existingMinimum + sourceMinimum + SPLIT_HANDLE_SIZE > availableWidth
        ) {
          receivingStack.children.push(panelNode(descriptor.id));
          receivingStack.activePanelId = descriptor.id;
          shellState.focusedStackId = receivingStack.id;
        } else {
          shellState.model = splitNode("horizontal", [shellState.model, sourceStack], [existingMinimum, sourceMinimum]);
        }
      } else {
        shellState.model = stackNode([descriptor.id]);
      }
      render("show-panel");
      return true;
    }

    function addPanel(rawDescriptor = {}, placement = {}) {
      // A hidden panel only joins the hidden rail, so it leaves a fixed layout as it is.
      const hiddenPlacement = placement.hidden === true;
      if (isLayoutLocked() && !hiddenPlacement) throw new Error("布局已固定，请先解锁布局再添加面板。");
      const rawId = String(rawDescriptor.id || "").trim();
      if (!rawId || descriptors.has(rawId)) throw new Error(`PortalWorkbench editor instance already exists: ${rawId}`);
      const descriptor = descriptorOf(rawDescriptor, root, id, runtimeContext);
      descriptors.set(descriptor.id, descriptor);
      if (shellState.controller) registerPanelCommands(descriptor);
      panelIds.add(descriptor.id);
      shellState.hidden.delete(descriptor.id);
      shellState.collapsed.delete(descriptor.id);
      callPanelLifecycle(descriptor, "connect", runtimeContext);
      callPanelLifecycle(descriptor, "setContext", runtimeContext.context, runtimeContext);
      if (hiddenPlacement) {
        shellState.hidden.add(descriptor.id);
        render("create-editor");
        return descriptor;
      }
      const targetStackId = String(placement.stackId || shellState.focusedStackId || "");
      const targetStack = findNode(shellState.model, targetStackId)?.node;
      const zone = ["left", "right", "top", "bottom", "center"].includes(String(placement.zone || ""))
        ? String(placement.zone)
        : "center";
      if (targetStack?.type === "stack" && zone === "center") {
        targetStack.children.push(panelNode(descriptor.id));
        targetStack.activePanelId = descriptor.id;
      } else if (targetStack?.type === "stack") {
        const orientation = ["top", "bottom"].includes(zone) ? "vertical" : "horizontal";
        const sourceStack = stackNode([descriptor.id]);
        const replacement = ["left", "top"].includes(zone)
          ? splitNode(orientation, [sourceStack, targetStack])
          : splitNode(orientation, [targetStack, sourceStack]);
        shellState.model = replaceNode(shellState.model, targetStack.id, replacement);
      } else if (shellState.model) {
        shellState.model = splitNode("horizontal", [shellState.model, stackNode([descriptor.id])]);
      } else {
        shellState.model = stackNode([descriptor.id]);
      }
      shellState.activeOpenTargetPanelId = descriptor.openSource ? shellState.activeOpenTargetPanelId : descriptor.id;
      shellState.focusedStackId = findPanel(shellState.model, descriptor.id)?.stack?.id || shellState.focusedStackId;
      if (!descriptor.openSource) lastOpenTargetByType.set(descriptor.type, descriptor.id);
      render("create-editor");
      return descriptor;
    }

    function canFocusFixedPanel(panelId) {
      const stack = findPanel(shellState.model, panelId)?.stack;
      return !shellState.hidden.has(panelId) && !shellState.collapsed.has(panelId) && !shellState.collapsed.has(stack?.activePanelId);
    }

    function togglePanel(panelId) {
      return shellState.hidden.has(String(panelId)) ? showPanel(panelId) : hidePanel(panelId);
    }

    function setPanelTitle(panelId, title) {
      const descriptor = descriptors.get(String(panelId || ""));
      const nextTitle = String(title || "").trim();
      if (!descriptor || !nextTitle || descriptor.title === nextTitle) return false;
      descriptor.title = nextTitle;
      render("set-panel-title");
      return true;
    }

    function applyWorkspace(workspace) {
      if (isLayoutLocked()) return false;
      if (!workspace) return false;
      [...panelIds].forEach(panelId => {
        const placement = panelPlacement(shellState.model, panelId);
        if (placement) placements[panelId] = placement;
      });
      shellState.hidden = hiddenPanelsForLayout(
        (workspace.hidden || []).filter(panelId => panelIds.has(panelId)),
        collectPanelIds(workspace.layout),
      );
      shellState.collapsed = new Set((workspace.collapsed || []).filter(panelId => panelIds.has(panelId) && !shellState.hidden.has(panelId)));
      shellState.model = normalizeNode(clone(workspace.layout), panelIds);
      shellState.activeWorkspaceName = String(workspace.name || workspace.id || options.label || "工作区");
      normalizeCurrent();
      if (!render("apply-workspace")) return false;
      closeMenu();
      options.onWorkspaceApplied?.(Object.freeze({
        id: String(workspace.id || ""),
        name: shellState.activeWorkspaceName,
      }), shellState.controller);
      return true;
    }

    return {
      addPanel, applyWorkspace, canFocusFixedPanel, closePanel, collapsePanel, expandPanel, focusPanel, hidePanel,
      setPanelTitle, showPanel, toggleCollapsed, togglePanel,
    };
  }

  global.PortalWorkbenchPanelActions = Object.freeze({ createPanelActions, dependencies: DEPENDENCIES });
})(window);
