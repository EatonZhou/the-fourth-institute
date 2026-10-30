(function (global) {
  "use strict";

  if (global.PortalWorkbenchLayoutView) return;

  // Drawing the layout: stacks with their tabs and Panel frames, splits
  // with their resize handles, refreshing a drawn layout in place, the
  // topology that decides whether it can be refreshed, and activating a
  // tab.

  const DEPENDENCIES = Object.freeze([
    "announceChange", "bindKeyboardResize", "bindPointerResize", "bindSingleTabReveal", "bindStackDrop",
    "bindTabDrag", "bindTabGroupDrop", "clamp", "compactExtentForNode", "descriptors", "executeCommand",
    "expandPanel", "isLayoutLocked", "lastOpenTargetByType", "minimumForNode", "persist", "refreshPanelFrame",
    "render", "renderPanelFrame", "shellState", "splitChildMinimumCss",
  ]);

  function createLayoutView(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalWorkbenchLayoutView is missing: ${missing.join(", ")}`);
    const {
      announceChange, bindKeyboardResize, bindPointerResize, bindSingleTabReveal, bindStackDrop, bindTabDrag,
      bindTabGroupDrop, clamp, compactExtentForNode, descriptors, executeCommand, expandPanel, isLayoutLocked,
      lastOpenTargetByType, minimumForNode, persist, refreshPanelFrame, render, renderPanelFrame, shellState,
      splitChildMinimumCss,
    } = dependencies;

    function activatePanel(stack, panelId) {
      if (isLayoutLocked()) return;
      const descriptor = descriptors.get(String(panelId));
      if (!descriptor?.openSource) {
        shellState.activeOpenTargetPanelId = String(panelId);
        lastOpenTargetByType.set(descriptor.type, descriptor.id);
      }
      if (stack.activePanelId === panelId) {
        shellState.focusedStackId = stack.id;
        if (shellState.collapsed.has(panelId)) expandPanel(panelId);
        else persist();
        return;
      }
      stack.activePanelId = panelId;
      shellState.focusedStackId = stack.id;
      render("activate-panel");
    }

    function renderStack(stack, parentOrientation = "") {
      const mobileMode = global.PortalResponsive?.isMobile?.() === true;
      const mobileOwnsStack = mobileMode
        && stack.children.some(child => child.panelId === shellState.mobileActivePanelId);
      const displayedPanelId = mobileOwnsStack ? shellState.mobileActivePanelId : stack.activePanelId;
      const activeCollapsed = !mobileOwnsStack && shellState.collapsed.has(String(displayedPanelId || ""));
      const element = document.createElement("section");
      element.className = `portalWorkbenchStack${activeCollapsed ? " is-collapsed" : ""}`;
      element.dataset.portalWorkbenchStack = stack.id;
      element.dataset.tabCount = String(stack.children.length);
      element.dataset.portalMobileActive = String(
        mobileOwnsStack,
      );
      if (activeCollapsed) element.dataset.collapseAxis = parentOrientation || "vertical";
      element.addEventListener("pointerdown", () => {
        shellState.focusedStackId = stack.id;
        const descriptor = descriptors.get(String(stack.activePanelId || ""));
        if (descriptor && !descriptor.openSource) {
          const changed = shellState.activeOpenTargetPanelId !== descriptor.id;
          shellState.activeOpenTargetPanelId = descriptor.id;
          lastOpenTargetByType.set(descriptor.type, descriptor.id);
          if (changed) persist();
        }
      });
      const tabs = document.createElement("div");
      tabs.className = "portalWorkbenchTabs";
      if (stack.children.length === 1) tabs.dataset.portalOverlayLayer = "sticky";
      tabs.setAttribute("role", "tablist");
      const body = document.createElement("div");
      body.className = "portalWorkbenchStackBody";
      stack.children.forEach(child => {
        const descriptor = descriptors.get(child.panelId);
        if (!descriptor) return;
        const active = child.panelId === displayedPanelId;
        const tabSlot = document.createElement("span");
        tabSlot.className = "portalWorkbenchTabSlot";
        const tab = document.createElement("button");
        tab.className = `portalWorkbenchTab${active ? " is-active" : ""}`;
        tab.type = "button";
        tab.dataset.portalWorkbenchTab = descriptor.id;
        tab.dataset.portalIcon = descriptor.icon;
        tab.setAttribute("role", "tab");
        tab.setAttribute("aria-selected", String(active));
        tab.title = descriptor.title;
        const tabLabel = document.createElement("span");
        tabLabel.textContent = descriptor.title;
        tab.append(tabLabel);
        tab.addEventListener("click", () => activatePanel(stack, descriptor.id));
        bindTabDrag(tab, descriptor.id);
        tabSlot.append(tab);
        if (descriptor.canHide) {
          const close = document.createElement("button");
          close.className = "portalWorkbenchTabClose";
          close.type = "button";
          close.dataset.portalIcon = "close";
          close.setAttribute("aria-label", window.PortalI18n.text`关闭 ${descriptor.title}`);
          close.title = window.PortalI18n.text`关闭 ${descriptor.title}`;
          close.addEventListener("click", event => {
            event.stopPropagation();
            executeCommand(`panel.${descriptor.id}.${descriptor.closable ? "close" : "toggle-visibility"}`, event, "panel-tab");
          });
          tabSlot.append(close);
        }
        tabs.append(tabSlot);
        body.append(renderPanelFrame(descriptor, active, active && activeCollapsed));
      });
      element.append(tabs, body);
      bindSingleTabReveal(element, tabs, stack);
      bindTabGroupDrop(tabs, element, stack);
      bindStackDrop(element, stack);
      return element;
    }

    function refreshStack(element, stack, parentOrientation = "") {
      if (!element || element.dataset.portalWorkbenchStack !== stack.id) return false;
      const mobileMode = global.PortalResponsive?.isMobile?.() === true;
      const mobileOwnsStack = mobileMode
        && stack.children.some(child => child.panelId === shellState.mobileActivePanelId);
      const displayedPanelId = mobileOwnsStack ? shellState.mobileActivePanelId : stack.activePanelId;
      const activeCollapsed = !mobileOwnsStack && shellState.collapsed.has(String(displayedPanelId || ""));
      element.classList.toggle("is-collapsed", activeCollapsed);
      element.dataset.tabCount = String(stack.children.length);
      element.dataset.portalMobileActive = String(mobileOwnsStack);
      if (activeCollapsed) element.dataset.collapseAxis = parentOrientation || "vertical";
      else delete element.dataset.collapseAxis;
      const tabs = element.querySelector(":scope > .portalWorkbenchTabs");
      const body = element.querySelector(":scope > .portalWorkbenchStackBody");
      if (!tabs || !body) return false;
      for (const child of stack.children) {
        const descriptor = descriptors.get(child.panelId);
        if (!descriptor) return false;
        const active = child.panelId === displayedPanelId;
        const tab = tabs.querySelector(`[data-portal-workbench-tab="${CSS.escape(descriptor.id)}"]`);
        const frame = body.querySelector(`:scope > [data-portal-workbench-panel-frame="${CSS.escape(descriptor.id)}"]`);
        if (!tab || !refreshPanelFrame(frame, descriptor, active, active && activeCollapsed)) return false;
        tab.classList.toggle("is-active", active);
        tab.dataset.portalIcon = descriptor.icon;
        tab.setAttribute("aria-selected", String(active));
        tab.title = descriptor.title;
        const label = tab.querySelector(":scope > span");
        if (label) label.textContent = descriptor.title;
        const close = tab.parentElement?.querySelector(":scope > .portalWorkbenchTabClose");
        if (close) {
          close.setAttribute("aria-label", window.PortalI18n.text`关闭 ${descriptor.title}`);
          close.title = window.PortalI18n.text`关闭 ${descriptor.title}`;
        }
      }
      return true;
    }

    function bindSplitHandle(handle, split, before, after, beforeElement, afterElement, index) {
      const axis = split.orientation === "vertical" ? "y" : "x";
      const beforeMinimum = minimumForNode(before, axis, descriptors, shellState.collapsed);
      const afterMinimum = minimumForNode(after, axis, descriptors, shellState.collapsed);
      const bounds = total => {
        const usable = Math.max(2, Number(total) || 2);
        const minimum = Math.min(beforeMinimum, usable / 2);
        const afterFloor = Math.min(afterMinimum, usable - minimum);
        return { minimum, maximum: Math.max(minimum, usable - afterFloor) };
      };
      const readSizes = () => {
        const children = [...beforeElement.parentElement.children].filter(element => element.classList.contains("portalWorkbenchSplitChild"));
        const sizes = children.map(element => element.getBoundingClientRect()[axis === "x" ? "width" : "height"]);
        return {
          before: sizes[index],
          after: sizes[index + 1],
          children,
          sizes,
        };
      };
      const applySizes = (beforeSize, afterSize, start) => {
        if (isLayoutLocked()) return;
        const sizes = start.sizes.slice();
        sizes[index] = beforeSize;
        sizes[index + 1] = afterSize;
        const edges = axis === "x" ? ["Left", "Right"] : ["Top", "Bottom"];
        const expanded = split.children.map((child, childIndex) => childIndex)
          .filter(childIndex => compactExtentForNode(split.children[childIndex], axis, shellState.collapsed) === null);
        const weight = expanded.reduce((sum, childIndex) => sum + split.weights[childIndex], 0);
        expanded.forEach(childIndex => {
          const style = getComputedStyle(start.children[childIndex]);
          sizes[childIndex] = Math.max(1, sizes[childIndex] - edges.reduce((sum, edge) => (
            sum + (parseFloat(style[`padding${edge}`]) || 0) + (parseFloat(style[`border${edge}Width`]) || 0)
          ), 0));
        });
        const total = expanded.reduce((sum, childIndex) => sum + sizes[childIndex], 0);
        expanded.forEach(childIndex => {
          split.weights[childIndex] = weight * sizes[childIndex] / total;
          start.children[childIndex].style.flexGrow = String(split.weights[childIndex]);
        });
      };
      bindPointerResize({
        handle,
        axis,
        disabled: isLayoutLocked,
        bodyClass: axis === "x" ? "resizingWorkbenchPane" : "resizingWorkbenchRow",
        readStart: readSizes,
        onMove: ({ delta, start }) => {
          const total = start.before + start.after;
          const range = bounds(total);
          const nextBefore = clamp(start.before + delta, range.minimum, range.maximum);
          const nextAfter = total - nextBefore;
          applySizes(nextBefore, nextAfter, start);
          announceChange("resize-live");
        },
        onDone: () => {
          persist();
          announceChange("resize");
        },
      });
      bindKeyboardResize({
        handle,
        axis,
        disabled: isLayoutLocked,
        keyboardStep: 24,
        readStart: readSizes,
        readValue: () => readSizes().before,
        min: () => {
          const sizes = readSizes();
          return bounds(sizes.before + sizes.after).minimum;
        },
        max: () => {
          const sizes = readSizes();
          return bounds(sizes.before + sizes.after).maximum;
        },
        onMove: ({ delta, start }) => {
          const total = start.before + start.after;
          const range = bounds(total);
          const nextBefore = clamp(start.before + delta, range.minimum, range.maximum);
          applySizes(nextBefore, total - nextBefore, start);
        },
        onDone: () => { persist(); announceChange("resize"); },
        valueText: value => `${Math.round(value || 0)} 像素`,
      });
    }

    function renderSplit(split) {
      const element = document.createElement("div");
      element.className = "portalWorkbenchSplit";
      element.dataset.orientation = split.orientation;
      element.dataset.portalWorkbenchSplit = split.id;
      const children = split.children.map((child, index) => {
        const childElement = renderNode(child, split.orientation);
        childElement.classList.add("portalWorkbenchSplitChild");
        const axis = split.orientation === "vertical" ? "y" : "x";
        const compactExtent = compactExtentForNode(child, axis, shellState.collapsed);
        const childCompact = compactExtent !== null;
        childElement.style.flexGrow = childCompact ? "0" : String(Number(split.weights?.[index]) || 1);
        childElement.style.flexShrink = childCompact ? "0" : "1";
        childElement.style.flexBasis = childCompact
          ? `${compactExtent}px`
          : "0px";
        if (split.orientation === "horizontal") {
          childElement.style.minWidth = splitChildMinimumCss(split, child, index, descriptors, shellState.collapsed);
        } else {
          childElement.style.minHeight = splitChildMinimumCss(split, child, index, descriptors, shellState.collapsed);
        }
        element.append(childElement);
        return childElement;
      });
      for (let index = children.length - 2; index >= 0; index -= 1) {
        const handle = document.createElement("div");
        handle.className = "portalWorkbenchSplitHandle";
        handle.dataset.orientation = split.orientation;
        handle.setAttribute("aria-label", window.PortalI18n.t("调整工作区面板大小"));
        handle.hidden = [split.children[index], split.children[index + 1]].some(child => (
          compactExtentForNode(child, split.orientation === "vertical" ? "y" : "x", shellState.collapsed) !== null
        ));
        children[index].after(handle);
        bindSplitHandle(handle, split, split.children[index], split.children[index + 1], children[index], children[index + 1], index);
      }
      return element;
    }

    function refreshSplit(element, split) {
      if (!element || element.dataset.portalWorkbenchSplit !== split.id) return false;
      element.dataset.orientation = split.orientation;
      const children = [...element.querySelectorAll(":scope > .portalWorkbenchSplitChild")];
      const handles = [...element.querySelectorAll(":scope > .portalWorkbenchSplitHandle")];
      if (children.length !== split.children.length || handles.length !== Math.max(0, children.length - 1)) return false;
      for (let index = 0; index < split.children.length; index += 1) {
        const child = split.children[index];
        const childElement = children[index];
        const axis = split.orientation === "vertical" ? "y" : "x";
        const compactExtent = compactExtentForNode(child, axis, shellState.collapsed);
        const childCompact = compactExtent !== null;
        childElement.style.flexGrow = childCompact ? "0" : String(Number(split.weights?.[index]) || 1);
        childElement.style.flexShrink = childCompact ? "0" : "1";
        childElement.style.flexBasis = childCompact
          ? `${compactExtent}px`
          : "0px";
        if (split.orientation === "horizontal") {
          childElement.style.minWidth = splitChildMinimumCss(split, child, index, descriptors, shellState.collapsed);
          childElement.style.removeProperty("min-height");
        } else {
          childElement.style.minHeight = splitChildMinimumCss(split, child, index, descriptors, shellState.collapsed);
          childElement.style.removeProperty("min-width");
        }
        if (!refreshNode(childElement, child, split.orientation)) return false;
      }
      handles.forEach((handle, index) => {
        handle.dataset.orientation = split.orientation;
        const axis = split.orientation === "vertical" ? "y" : "x";
        handle.hidden = [split.children[index], split.children[index + 1]].some(child => (
          compactExtentForNode(child, axis, shellState.collapsed) !== null
        ));
      });
      return true;
    }

    function stackForPanelNode(node) {
      return {
        type: "stack",
        id: `${node.id}-stack`,
        activePanelId: node.panelId,
        children: [node],
      };
    }

    function renderNode(node, parentOrientation = "") {
      if (node.type === "stack") return renderStack(node, parentOrientation);
      if (node.type === "split") return renderSplit(node);
      return renderStack(stackForPanelNode(node), parentOrientation);
    }

    function refreshNode(element, node, parentOrientation = "") {
      if (node.type === "stack") return refreshStack(element, node, parentOrientation);
      if (node.type === "split") return refreshSplit(element, node);
      return refreshStack(element, stackForPanelNode(node), parentOrientation);
    }

    function topologyOf(node) {
      if (!node) return null;
      if (node.type === "panel") return ["panel", node.id, node.panelId];
      return [
        node.type,
        node.id,
        node.type === "split" ? node.orientation : "",
        (node.children || []).map(topologyOf),
      ];
    }

    return {
      refreshNode, renderNode, topologyOf,
    };
  }

  global.PortalWorkbenchLayoutView = Object.freeze({ createLayoutView, dependencies: DEPENDENCIES });
})(window);
