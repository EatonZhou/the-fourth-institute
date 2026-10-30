(function (global) {
  "use strict";

  if (global.PortalWorkbenchPanelMounts) return;

  // Mounting Panel elements: moving them into their frames without losing
  // live state (focus, selection, embedded documents), the frames
  // themselves, and putting the elements back when a layout cannot be
  // committed.

  const DEPENDENCIES = Object.freeze([
    "descriptors", "parking", "root", "shellState",
  ]);

  function createPanelMounts(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalWorkbenchPanelMounts is missing: ${missing.join(", ")}`);
    const {
      descriptors, parking, root, shellState,
    } = dependencies;

    function moveConnectedPanelElement(parent, element) {
      if (typeof parent.moveBefore === "function") {
        try { parent.moveBefore(element, null); }
        catch (_error) { return false; }
        return element.parentElement === parent && element.isConnected;
      }
      // Older browsers can reparent ordinary DOM, but disconnecting custom
      // elements or embedded documents/media can destroy their live state.
      const nodes = document.createTreeWalker(element, NodeFilter.SHOW_ELEMENT);
      for (let node = nodes.currentNode; node; node = nodes.nextNode()) {
        if (node.localName.includes("-") || node.shadowRoot
          || node.matches("iframe, object, embed, audio, video, [is], [popover], dialog[open]")) return false;
      }
      if (document.fullscreenElement && element.contains(document.fullscreenElement)) return false;
      const focused = element.contains(document.activeElement) ? document.activeElement : null;
      const selection = focused && typeof focused.selectionStart === "number"
        ? [focused.selectionStart, focused.selectionEnd, focused.selectionDirection] : null;
      try { parent.append(element); }
      catch (_error) { return false; }
      if (focused) {
        focused.focus({ preventScroll: true });
        if (selection) focused.setSelectionRange(...selection);
      }
      return element.parentElement === parent && element.isConnected;
    }

    function mountPanelElement(parent, element) {
      if (!parent || !element) return false;
      if (element.parentElement === parent) return !parent.isConnected || element.isConnected;
      if (!parent.isConnected && element.isConnected) {
        shellState.pendingPanelMounts.push([parent, element]);
        return true;
      } else if (parent.isConnected && element.isConnected) {
        return moveConnectedPanelElement(parent, element);
      } else {
        parent.append(element);
        return element.parentElement === parent && (!parent.isConnected || element.isConnected);
      }
    }

    function panelFrameIn(layoutRoot, panelId) {
      // Panel IDs are local to a Workbench. A nested Workbench can contain a
      // frame with the same ID, but never owns this instance's Panel root.
      return [...layoutRoot.querySelectorAll(
        `[data-portal-workbench-panel-frame="${CSS.escape(panelId)}"]`,
      )].find(frame => frame.closest("[data-portal-workbench-id]") === root) || null;
    }

    function reconcilePanelMounts(layoutRoot) {
      let complete = true;
      descriptors.forEach(descriptor => {
        const frame = panelFrameIn(layoutRoot, descriptor.id);
        if (!mountPanelElement(frame || parking, descriptor.element)) complete = false;
      });
      return complete;
    }

    function restorePanelMounts(originalParents) {
      let complete = true;
      descriptors.forEach(descriptor => {
        const originalParent = originalParents.get(descriptor.id) || null;
        const element = descriptor.element;
        if (element.parentElement === originalParent) return;
        if (!originalParent) {
          element.remove();
          return;
        }
        if (originalParent.isConnected && element.isConnected) {
          if (!moveConnectedPanelElement(originalParent, element)) complete = false;
        } else if (!originalParent.isConnected || !element.isConnected) {
          originalParent.append(element);
        }
        if (element.parentElement !== originalParent) complete = false;
      });
      return complete;
    }

    function renderPanelFrame(descriptor, active, panelCollapsed = false) {
      const frame = document.createElement("div");
      frame.className = `portalWorkbenchPanelFrame${descriptor.id === shellState.activeOpenTargetPanelId ? " is-open-target" : ""}`;
      frame.dataset.portalWorkbenchPanelFrame = descriptor.id;
      frame.dataset.portalWorkbenchInset = descriptor.inset;
      frame.hidden = !active || panelCollapsed;
      mountPanelElement(frame, descriptor.element);
      return frame;
    }

    function refreshPanelFrame(frame, descriptor, active, panelCollapsed = false) {
      if (!frame || frame.dataset.portalWorkbenchPanelFrame !== descriptor.id) return false;
      frame.classList.toggle("is-open-target", descriptor.id === shellState.activeOpenTargetPanelId);
      frame.dataset.portalWorkbenchInset = descriptor.inset;
      frame.hidden = !active || panelCollapsed;
      mountPanelElement(frame, descriptor.element);
      return true;
    }

    return {
      mountPanelElement, panelFrameIn, reconcilePanelMounts, refreshPanelFrame, renderPanelFrame, restorePanelMounts,
    };
  }

  global.PortalWorkbenchPanelMounts = Object.freeze({ createPanelMounts, dependencies: DEPENDENCIES });
})(window);
