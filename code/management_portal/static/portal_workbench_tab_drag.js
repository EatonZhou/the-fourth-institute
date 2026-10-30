(function (global) {
  "use strict";

  if (global.PortalWorkbenchTabDrag) return;

  // Dragging tabs: drop zones on stacks and tab strips, moving a Panel to
  // where it is dropped, and revealing the tab strip of a single-tab stack.

  const DEPENDENCIES = Object.freeze([
    "canvas", "clone", "descriptors", "findNode", "isLayoutLocked", "normalizeCurrent", "panelNode",
    "removePanelFromNode", "render", "replaceNode", "shellState", "splitNode", "stackNode",
  ]);

  function createTabDrag(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalWorkbenchTabDrag is missing: ${missing.join(", ")}`);
    const {
      canvas, clone, descriptors, findNode, isLayoutLocked, normalizeCurrent, panelNode, removePanelFromNode, render,
      replaceNode, shellState, splitNode, stackNode,
    } = dependencies;

    function bindTabDrag(tab, panelId) {
      if (!descriptors.get(String(panelId))?.canMove) return;
      tab.draggable = true;
      tab.addEventListener("dragstart", event => {
        if (isLayoutLocked()) { event.preventDefault(); return; }
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData("application/x-portal-workbench-panel", panelId);
        tab.classList.add("is-dragging");
        document.body.classList.add("draggingWorkbenchTab");
      });
      tab.addEventListener("dragend", () => {
        tab.classList.remove("is-dragging");
        document.body.classList.remove("draggingWorkbenchTab");
        canvas.querySelectorAll("[data-portal-workbench-drop-zone]").forEach(node => {
          node.removeAttribute("data-portal-workbench-drop-zone");
        });
      });
    }

    function dropZone(stackElement, event) {
      const rect = stackElement.getBoundingClientRect();
      const x = (event.clientX - rect.left) / Math.max(1, rect.width);
      const y = (event.clientY - rect.top) / Math.max(1, rect.height);
      if (x < 0.24) return "left";
      if (x > 0.76) return "right";
      if (y < 0.24) return "top";
      if (y > 0.76) return "bottom";
      return "center";
    }

    function movePanel(panelId, targetStackId, zone) {
      if (isLayoutLocked()) return false;
      if (!descriptors.get(String(panelId))?.canMove) return;
      const targetBefore = findNode(shellState.model, targetStackId)?.node;
      if (!targetBefore || targetBefore.type !== "stack") return;
      if (zone === "center" && targetBefore.children.some(child => child.panelId === panelId)) {
        targetBefore.activePanelId = panelId;
        shellState.focusedStackId = targetBefore.id;
        render("activate-panel");
        return;
      }
      if (zone !== "center" && targetBefore.children.length === 1 && targetBefore.children[0].panelId === panelId) return;
      let next = removePanelFromNode(clone(shellState.model), panelId);
      shellState.hidden.delete(panelId);
      if (!next) {
        shellState.model = stackNode([panelId]);
        render("move-panel");
        return;
      }
      const target = findNode(next, targetStackId)?.node;
      if (!target || target.type !== "stack") {
        shellState.model = splitNode("horizontal", [next, stackNode([panelId])]);
      } else if (zone === "center") {
        target.children.push(panelNode(panelId));
        target.activePanelId = panelId;
        shellState.model = next;
      } else {
        const orientation = ["top", "bottom"].includes(zone) ? "vertical" : "horizontal";
        const sourceStack = stackNode([panelId]);
        const replacement = ["left", "top"].includes(zone)
          ? splitNode(orientation, [sourceStack, target])
          : splitNode(orientation, [target, sourceStack]);
        shellState.model = replaceNode(next, targetStackId, replacement);
      }
      normalizeCurrent();
      render("move-panel");
    }

    function bindStackDrop(stackElement, stack) {
      stackElement.addEventListener("dragover", event => {
        if (isLayoutLocked()) return;
        if (!event.dataTransfer.types.includes("application/x-portal-workbench-panel")) return;
        event.preventDefault();
        event.dataTransfer.dropEffect = "move";
        stackElement.dataset.portalWorkbenchDropZone = dropZone(stackElement, event);
      });
      stackElement.addEventListener("dragleave", event => {
        if (!stackElement.contains(event.relatedTarget)) stackElement.removeAttribute("data-portal-workbench-drop-zone");
      });
      stackElement.addEventListener("drop", event => {
        const panelId = event.dataTransfer.getData("application/x-portal-workbench-panel");
        if (!panelId) return;
        event.preventDefault();
        const zone = stackElement.dataset.portalWorkbenchDropZone || "center";
        stackElement.removeAttribute("data-portal-workbench-drop-zone");
        movePanel(panelId, stack.id, zone);
      });
    }

    function bindTabGroupDrop(tabs, stackElement, stack) {
      tabs.addEventListener("dragover", event => {
        if (isLayoutLocked()) return;
        if (!event.dataTransfer.types.includes("application/x-portal-workbench-panel")) return;
        event.preventDefault();
        event.stopPropagation();
        event.dataTransfer.dropEffect = "move";
        stackElement.dataset.portalWorkbenchDropZone = "center";
      });
      tabs.addEventListener("dragleave", event => {
        event.stopPropagation();
        if (!tabs.contains(event.relatedTarget)) stackElement.removeAttribute("data-portal-workbench-drop-zone");
      });
      tabs.addEventListener("drop", event => {
        const panelId = event.dataTransfer.getData("application/x-portal-workbench-panel");
        if (!panelId) return;
        event.preventDefault();
        event.stopPropagation();
        stackElement.removeAttribute("data-portal-workbench-drop-zone");
        movePanel(panelId, stack.id, "center");
      });
    }

    function bindSingleTabReveal(stackElement, tabs, stack) {
      if (stack.children.length !== 1) return;
      const reveal = () => { if (!isLayoutLocked()) stackElement.classList.add("is-tab-strip-revealed"); };
      const conceal = () => stackElement.classList.remove("is-tab-strip-revealed");
      stackElement.addEventListener("pointermove", event => {
        const bounds = stackElement.getBoundingClientRect();
        const localY = event.clientY - bounds.top;
        if (tabs.contains(event.target) || (stackElement.classList.contains("is-tab-strip-revealed") && localY <= 34)) {
          reveal();
          return;
        }
        const nestedControl = event.target instanceof Element
          ? event.target.closest("button, a, input, select, textarea, [role='tab'], [role='button']")
          : null;
        const ownerWorkbench = stackElement.closest(".portalWorkbench");
        if (nestedControl && nestedControl.closest(".portalWorkbench") !== ownerWorkbench) return;
        if (localY <= 16) reveal();
        else if (localY > 34) conceal();
      }, true);
      stackElement.addEventListener("pointerleave", conceal);
      tabs.addEventListener("focusin", reveal);
      tabs.addEventListener("focusout", () => requestAnimationFrame(() => {
        if (!tabs.contains(document.activeElement)) conceal();
      }));
    }

    return {
      bindSingleTabReveal, bindStackDrop, bindTabDrag, bindTabGroupDrop,
    };
  }

  global.PortalWorkbenchTabDrag = Object.freeze({ createTabDrag, dependencies: DEPENDENCIES });
})(window);
