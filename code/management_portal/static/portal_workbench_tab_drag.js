(function (global) {
  "use strict";

  if (global.PortalWorkbenchTabDrag) return;

  const PANEL_TYPE = "application/x-portal-workbench-panel";
  // A native drag has one source, even when Workbenches are nested.
  let activeDrag = null;

  // Dragging tabs: drop zones on stacks and tab strips, moving a Panel to
  // where it is dropped, and revealing auto-hidden tab strips.

  const DEPENDENCIES = Object.freeze([
    "canvas", "clone", "descriptors", "findNode", "isLayoutLocked", "normalizeCurrent", "panelNode",
    "removePanelFromNode", "render", "replaceNode", "shellState", "splitNode", "stackNode",
  ]);

  function createTabDrag(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalWorkbenchTabDrag is missing: ${missing.join(", ")}`);
    const {
      autoHideTabHeader = false,
      canvas, clone, descriptors, findNode, isLayoutLocked, normalizeCurrent, panelNode, removePanelFromNode, render,
      replaceNode, shellState, splitNode, stackNode,
    } = dependencies;

    let activeTab = null;
    let destroyed = false;
    const previews = new Set();
    function clearPreviews() {
      previews.forEach(node => node.removeAttribute("data-portal-workbench-drop-zone"));
      previews.clear();
    }
    function finishDrag() {
      clearPreviews();
      activeTab?.classList.remove("is-dragging");
      activeTab = null;
      if (activeDrag?.canvas === canvas) {
        activeDrag = null;
        document.body.classList.remove("draggingWorkbenchTab");
      }
      global.removeEventListener("dragend", finishDrag, true);
      global.removeEventListener("drop", finishDrag);
      global.removeEventListener("blur", finishDrag);
      global.removeEventListener("pagehide", finishDrag);
      global.removeEventListener("keydown", cancelDrag, true);
      global.removeEventListener("pointerdown", finishDrag, true);
    }
    function cancelDrag(event) { if (event.key === "Escape") finishDrag(); }
    function ownsDrag(event) {
      if (!event.dataTransfer?.types.includes(PANEL_TYPE)) return false;
      // A nested stack consumes its own hit test, including rejected drops.
      event.stopPropagation();
      return !destroyed && activeDrag?.canvas === canvas && !isLayoutLocked();
    }
    function showPreview(stackElement, zone) {
      clearPreviews();
      previews.add(stackElement);
      stackElement.dataset.portalWorkbenchDropZone = zone;
    }

    function bindTabDrag(tab, panelId) {
      if (!descriptors.get(String(panelId))?.canMove) return;
      tab.draggable = true;
      tab.addEventListener("dragstart", event => {
        if (destroyed || isLayoutLocked()) { event.preventDefault(); return; }
        event.stopPropagation();
        activeDrag?.finish();
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData(PANEL_TYPE, panelId);
        activeTab = tab;
        activeDrag = { canvas, panelId, finish: finishDrag };
        tab.classList.add("is-dragging");
        document.body.classList.add("draggingWorkbenchTab");
        global.addEventListener("dragend", finishDrag, true);
        global.addEventListener("drop", finishDrag);
        global.addEventListener("blur", finishDrag);
        global.addEventListener("pagehide", finishDrag);
        global.addEventListener("keydown", cancelDrag, true);
        global.addEventListener("pointerdown", finishDrag, true);
      });
      tab.addEventListener("dragend", () => {
        if (activeTab === tab) finishDrag();
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
        if (!ownsDrag(event)) return;
        event.preventDefault();
        event.dataTransfer.dropEffect = "move";
        showPreview(stackElement, dropZone(stackElement, event));
      });
      stackElement.addEventListener("dragleave", event => {
        event.stopPropagation();
        if (!stackElement.contains(event.relatedTarget)) stackElement.removeAttribute("data-portal-workbench-drop-zone");
      });
      stackElement.addEventListener("drop", event => {
        if (!ownsDrag(event)) return;
        const panelId = activeDrag.panelId;
        event.preventDefault();
        const zone = stackElement.dataset.portalWorkbenchDropZone || "center";
        // Layout rendering can detach the source tab before dragend is emitted.
        finishDrag();
        movePanel(panelId, stack.id, zone);
      });
    }

    function bindTabGroupDrop(tabs, stackElement, stack) {
      tabs.addEventListener("dragover", event => {
        if (!ownsDrag(event)) return;
        event.preventDefault();
        event.stopPropagation();
        event.dataTransfer.dropEffect = "move";
        showPreview(stackElement, "center");
      });
      tabs.addEventListener("dragleave", event => {
        event.stopPropagation();
        if (!tabs.contains(event.relatedTarget)) stackElement.removeAttribute("data-portal-workbench-drop-zone");
      });
      tabs.addEventListener("drop", event => {
        if (!ownsDrag(event)) return;
        const panelId = activeDrag.panelId;
        event.preventDefault();
        event.stopPropagation();
        finishDrag();
        movePanel(panelId, stack.id, "center");
      });
    }

    // Every stack keeps its tab strip collapsed into a handle on its top edge
    // and reveals it on approach, whatever Panels it holds. autoHideTabHeader
    // additionally keeps the strip on mobile, where other pages hide it.
    function bindTabStripReveal(stackElement, tabs) {
      if (autoHideTabHeader) stackElement.dataset.autoHideTabs = "true";
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
          ? event.target.closest("button, a, input, select, textarea, summary, label, [role='tab'], [role='button']")
          : null;
        // Content toolbars can sit directly below the reveal edge without
        // owning another Workbench. Do not cover their controls on approach.
        if (nestedControl) { conceal(); return; }
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
      bindTabStripReveal, bindStackDrop, bindTabDrag, bindTabGroupDrop,
      destroy() { destroyed = true; finishDrag(); },
    };
  }

  global.PortalWorkbenchTabDrag = Object.freeze({ createTabDrag, dependencies: DEPENDENCIES });
})(window);
