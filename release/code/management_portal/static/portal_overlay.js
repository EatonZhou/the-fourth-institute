(function (global) {
  "use strict";

  const RUNTIME_KEY = Symbol.for(window.PortalCompatibility.identifier("overlay_runtime"));
  if (global.PortalOverlay) {
    // document.open()/embedded shell replacement removes Window listeners
    // without necessarily replacing Window-owned module objects.
    global[RUNTIME_KEY]?.install?.();
    global.dispatchEvent(new Event("portal:overlay-ready"));
    return;
  }

  const registrations = new WeakMap();
  const hostRegistrations = new WeakMap();
  const active = [];
  const KIND_ORDER = Object.freeze({
    sticky: 0,
    popover: 1,
    dialog: 2,
    toast: 3,
    critical: 4,
  });

  const elementOf = ref => window.PortalUIPrimitives.elementOf(ref);

  function normalizeKind(value) {
    const kind = String(value || "dialog").trim().toLowerCase();
    return Object.hasOwn(KIND_ORDER, kind) ? kind : "dialog";
  }

  function isEscapeContext(value) {
    return !!value && String(value.key || "") === "Escape";
  }

  function isVisible(element) {
    if (!element?.isConnected || element.hidden) return false;
    if (element instanceof HTMLDialogElement) return element.open;
    return element.getClientRects().length > 0;
  }

  function sortedActive() {
    return active
      .filter(entry => entry.active && isVisible(entry.element))
      .sort((left, right) => {
        const kindDelta = KIND_ORDER[left.kind] - KIND_ORDER[right.kind];
        return kindDelta || left.sequence - right.sequence;
      });
  }

  let sequence = 0;

  function createsStackingContext(element) {
    const style = global.getComputedStyle(element);
    if (style.position !== "static" && style.zIndex !== "auto") return true;
    if (style.position === "fixed" || style.position === "sticky") return true;
    if (style.transform !== "none" || style.perspective !== "none" || style.filter !== "none") return true;
    if (style.isolation === "isolate" || Number(style.opacity) < 1) return true;
    return false;
  }

  function containingStackingHosts(element) {
    const hosts = [];
    for (let parent = element.parentElement; parent && parent !== document.body; parent = parent.parentElement) {
      if (createsStackingContext(parent)) hosts.push(parent);
    }
    return hosts;
  }

  function syncHost(host) {
    const entries = [...(hostRegistrations.get(host) || [])]
      .filter(entry => entry.active)
      .sort((left, right) => {
        const kindDelta = KIND_ORDER[left.kind] - KIND_ORDER[right.kind];
        return kindDelta || left.sequence - right.sequence;
      });
    const topEntry = entries.at(-1);
    if (!topEntry) {
      host.removeAttribute("data-portal-overlay-host-layer");
      host.style.removeProperty("--portal-overlay-host-order");
      return;
    }
    host.dataset.portalOverlayHostLayer = topEntry.kind;
    host.style.setProperty("--portal-overlay-host-order", "1");
  }

  function activateHosts(entry) {
    entry.hosts = containingStackingHosts(entry.element);
    entry.hosts.forEach(host => {
      const entries = hostRegistrations.get(host) || new Set();
      entries.add(entry);
      hostRegistrations.set(host, entries);
      syncHost(host);
    });
  }

  function deactivateHosts(entry) {
    (entry.hosts || []).forEach(host => {
      const entries = hostRegistrations.get(host);
      entries?.delete(entry);
      syncHost(host);
    });
    entry.hosts = [];
  }

  function syncLayers() {
    const ordered = sortedActive();
    ordered.forEach((entry, index) => {
      entry.element.dataset.portalOverlayLayer = entry.kind;
      entry.element.style.setProperty("--portal-overlay-order", String(index + 1));
      entry.element.dataset.portalOverlayTop = index === ordered.length - 1 ? "true" : "false";
    });
    document.documentElement.dataset.portalOverlayCount = String(ordered.length);
    document.dispatchEvent(new CustomEvent("portal:overlaychange", {
      detail: {
        count: ordered.length,
        top: ordered.at(-1)?.element || null,
      },
    }));
  }

  function top() {
    return sortedActive().at(-1) || null;
  }

  function register(ref, options = {}) {
    const element = elementOf(ref);
    if (!element) return null;
    const existing = registrations.get(element);
    if (existing) {
      existing.update(options);
      return existing.controller;
    }

    const entry = {
      active: false,
      element,
      kind: normalizeKind(options.kind || element.dataset.portalOverlayLayer),
      options: { ...options },
      sequence: 0,
    };

    let backdropPress = null;
    const resetBackdropPress = () => {
      backdropPress = null;
    };
    const isBackdropTarget = event => {
      if (typeof entry.options.isBackdropTarget === "function") {
        return entry.options.isBackdropTarget(event, element) === true;
      }
      return event.target === element;
    };
    const handleBackdropPointerDown = event => {
      backdropPress = null;
      if (!entry.active || !isBackdropTarget(event) || !entry.options.closeOnBackdrop) return;
      if (event.isPrimary === false || event.button !== 0) return;
      backdropPress = {
        pointerId: event.pointerId,
        x: event.clientX,
        y: event.clientY,
      };
    };
    const handleBackdropPointerUp = event => {
      const press = backdropPress;
      backdropPress = null;
      if (!press || press.pointerId !== event.pointerId) return;
      if (!entry.active || !isBackdropTarget(event) || !entry.options.closeOnBackdrop) return;
      if (Math.hypot(event.clientX - press.x, event.clientY - press.y) > 6) return;
      if (top() !== entry) return;
      entry.options.onRequestClose?.(event, controller);
    };

    const handleCancel = event => {
      if (!entry.active) return;
      event.preventDefault();
      if (entry.options.closeOnEscape && top() === entry) {
        entry.options.onRequestClose?.(event, controller);
      }
    };

    const controller = {
      element,
      activate(context) {
        if (entry.active) deactivateHosts(entry);
        entry.active = true;
        entry.sequence = ++sequence;
        entry.context = context;
        if (!active.includes(entry)) active.push(entry);
        activateHosts(entry);
        syncLayers();
        return controller;
      },
      deactivate() {
        deactivateHosts(entry);
        entry.active = false;
        entry.context = undefined;
        element.removeAttribute("data-portal-overlay-top");
        element.style.removeProperty("--portal-overlay-order");
        const index = active.indexOf(entry);
        if (index >= 0) active.splice(index, 1);
        syncLayers();
        return controller;
      },
      destroy() {
        controller.deactivate();
        element.removeEventListener("pointerdown", handleBackdropPointerDown);
        element.removeEventListener("pointerup", handleBackdropPointerUp);
        element.removeEventListener("pointercancel", resetBackdropPress);
        element.removeEventListener("cancel", handleCancel);
        element.removeAttribute("data-portal-overlay-layer");
        registrations.delete(element);
      },
      isActive: () => entry.active,
      isTopmost: () => top() === entry,
      update(next = {}) {
        entry.options = { ...entry.options, ...next };
        entry.kind = normalizeKind(next.kind || entry.kind);
        if (entry.active) {
          entry.hosts.forEach(syncHost);
          syncLayers();
        }
        return controller;
      },
    };

    entry.controller = controller;
    entry.update = controller.update;
    element.dataset.portalOverlayLayer = entry.kind;
    element.addEventListener("pointerdown", handleBackdropPointerDown);
    element.addEventListener("pointerup", handleBackdropPointerUp);
    element.addEventListener("pointercancel", resetBackdropPress);
    if (element instanceof HTMLDialogElement) element.addEventListener("cancel", handleCancel);
    registrations.set(element, entry);
    return controller;
  }

  function requestTopClose(event) {
    if (event.key !== "Escape") return;
    const entry = top();
    if (!entry || !entry.options.closeOnEscape) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    entry.options.onRequestClose?.(event, entry.controller);
  }

  function positionAnchored(triggerRef, panelRef, options = {}) {
    const trigger = elementOf(triggerRef);
    const panel = elementOf(panelRef);
    if (!trigger || !panel || panel.hidden) return null;

    const margin = Number.isFinite(Number(options.margin)) ? Number(options.margin) : 8;
    const gap = Number.isFinite(Number(options.gap)) ? Number(options.gap) : 6;
    const placement = String(options.placement || "bottom-end");
    const triggerRect = trigger.getBoundingClientRect();
    const viewportWidth = document.documentElement.clientWidth;
    const viewportHeight = document.documentElement.clientHeight;
    const boundaryRef = typeof options.boundary === "function"
      ? options.boundary()
      : options.boundary;
    const boundary = elementOf(boundaryRef);
    const boundaryRect = boundary?.getBoundingClientRect();
    const minLeft = Math.max(margin, boundaryRect ? boundaryRect.left + margin : margin);
    const maxRight = Math.min(
      viewportWidth - margin,
      boundaryRect ? boundaryRect.right - margin : viewportWidth - margin,
    );
    panel.style.maxWidth = `${Math.max(120, maxRight - minLeft)}px`;
    panel.style.maxHeight = `${Math.max(120, viewportHeight - margin * 2)}px`;
    let panelRect = panel.getBoundingClientRect();
    const preferTop = placement.startsWith("top");
    const alignStart = placement.endsWith("start");
    const below = triggerRect.bottom + gap;
    const roomBelow = Math.max(0, viewportHeight - margin - below);
    const roomAbove = Math.max(0, triggerRect.top - gap - margin);
    const fitsBelow = panelRect.height <= roomBelow;
    const fitsAbove = panelRect.height <= roomAbove;
    let placeAbove = preferTop;
    if (preferTop && !fitsAbove) placeAbove = fitsBelow ? false : roomAbove >= roomBelow;
    if (!preferTop && !fitsBelow) placeAbove = fitsAbove ? true : roomAbove > roomBelow;
    const availableHeight = placeAbove ? roomAbove : roomBelow;
    panel.style.maxHeight = `${Math.max(120, availableHeight)}px`;
    panelRect = panel.getBoundingClientRect();
    let topPosition = placeAbove
      ? triggerRect.top - panelRect.height - gap
      : below;
    let leftPosition = alignStart ? triggerRect.left : triggerRect.right - panelRect.width;
    leftPosition = Math.max(minLeft, Math.min(leftPosition, maxRight - panelRect.width));
    topPosition = Math.max(margin, Math.min(topPosition, viewportHeight - panelRect.height - margin));
    panel.style.position = "fixed";
    panel.style.right = "auto";
    panel.style.bottom = "auto";
    panel.style.left = `${Math.round(leftPosition)}px`;
    panel.style.top = `${Math.round(topPosition)}px`;
    return { left: leftPosition, top: topPosition };
  }

  function positionAtPoint(panelRef, point = {}, options = {}) {
    const panel = elementOf(panelRef);
    if (!panel || panel.hidden) return null;

    const margin = Number.isFinite(Number(options.margin)) ? Number(options.margin) : 8;
    const gap = Number.isFinite(Number(options.gap)) ? Number(options.gap) : 6;
    const viewportWidth = document.documentElement.clientWidth;
    const viewportHeight = document.documentElement.clientHeight;
    const x = Number.isFinite(Number(point.x ?? point.clientX))
      ? Number(point.x ?? point.clientX)
      : viewportWidth / 2;
    const y = Number.isFinite(Number(point.y ?? point.clientY))
      ? Number(point.y ?? point.clientY)
      : viewportHeight / 2;
    const placement = String(options.placement || "top");
    const align = String(options.align || "center");

    panel.style.position = "fixed";
    panel.style.right = "auto";
    panel.style.bottom = "auto";
    panel.style.maxWidth = `${Math.max(120, viewportWidth - margin * 2)}px`;
    panel.style.maxHeight = `${Math.max(120, viewportHeight - margin * 2)}px`;

    const panelRect = panel.getBoundingClientRect();
    let leftPosition = x - panelRect.width / 2;
    if (align === "start") leftPosition = x;
    if (align === "end") leftPosition = x - panelRect.width;

    const preferTop = placement.startsWith("top");
    const above = y - panelRect.height - gap;
    const below = y + gap;
    let topPosition = preferTop ? above : below;
    if (preferTop && above < margin && below + panelRect.height <= viewportHeight - margin) {
      topPosition = below;
    }
    if (!preferTop && below + panelRect.height > viewportHeight - margin && above >= margin) {
      topPosition = above;
    }

    leftPosition = Math.max(margin, Math.min(leftPosition, viewportWidth - panelRect.width - margin));
    topPosition = Math.max(margin, Math.min(topPosition, viewportHeight - panelRect.height - margin));
    panel.style.left = `${Math.round(leftPosition)}px`;
    panel.style.top = `${Math.round(topPosition)}px`;
    return { left: leftPosition, top: topPosition };
  }

  function bindPopover(options = {}) {
    const trigger = elementOf(options.trigger);
    const anchor = elementOf(options.anchor) || trigger;
    const panel = elementOf(options.panel);
    if (!trigger || !panel) return null;
    const overlay = register(panel, {
      kind: "popover",
      closeOnEscape: options.closeOnEscape !== false,
      onRequestClose: event => controller.close(event),
    });
    let opened = false;
    let raf = 0;
    let placeholder = null;
    let originalParent = null;
    let disconnectObserver = null;
    let point = null;

    const mountInPortal = () => {
      if (options.portal !== true || panel.parentNode === document.body) return;
      originalParent = panel.parentNode;
      placeholder = document.createComment("portal-popover");
      originalParent?.insertBefore(placeholder, panel);
      document.body.appendChild(panel);
    };

    const restoreFromPortal = () => {
      if (options.portal !== true || !placeholder) return;
      if (placeholder.isConnected) placeholder.replaceWith(panel);
      else originalParent?.appendChild(panel);
      placeholder = null;
      originalParent = null;
    };

    const observeTrigger = () => {
      disconnectObserver?.disconnect();
      disconnectObserver = new MutationObserver(() => {
        if (opened && !trigger.isConnected) controller.close();
      });
      disconnectObserver.observe(document.documentElement, { childList: true, subtree: true });
    };

    const position = () => point
      ? positionAtPoint(panel, point, { ...options, align: "start" })
      : positionAnchored(anchor, panel, options);

    const reposition = () => {
      if (!opened) return;
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(position);
    };

    const repositionForExternalScroll = event => {
      if (event?.target instanceof Node && panel.contains(event.target)) return;
      reposition();
    };

    const handleOutside = event => {
      if (!opened || options.closeOnOutside === false) return;
      const target = event.target;
      if (panel.contains(target)) return;
      if (options.closeOnTrigger !== true && (trigger.contains(target) || anchor.contains(target))) return;
      controller.close(event);
    };

    const controller = {
      element: panel,
      anchor,
      trigger,
      open(context) {
        point = context?.point || null;
        const parentOverlay = sortedActive().reverse().find(entry => entry.element.contains(trigger));
        const effectiveLayer = parentOverlay && KIND_ORDER[parentOverlay.kind] > KIND_ORDER.popover
          ? parentOverlay.kind
          : "popover";
        panel.dataset.portalOverlayKind = "popover";
        overlay.update({ kind: effectiveLayer });
        if (opened) {
          overlay.activate(context);
          position();
          return controller;
        }
        opened = true;
        mountInPortal();
        panel.hidden = false;
        position();
        trigger.setAttribute("aria-expanded", "true");
        overlay.activate(context);
        document.addEventListener("pointerdown", handleOutside, true);
        global.addEventListener("resize", reposition);
        global.addEventListener("scroll", repositionForExternalScroll, true);
        observeTrigger();
        options.onOpen?.(context);
        // Some panels become measurable only after their open-state class is applied.
        position();
        return controller;
      },
      close(context) {
        if (!opened) return controller;
        const restoreFocus = (
          options.restoreFocusOnEscape !== false
          && isEscapeContext(context)
        );
        opened = false;
        cancelAnimationFrame(raf);
        overlay.deactivate();
        panel.hidden = true;
        trigger.setAttribute("aria-expanded", "false");
        document.removeEventListener("pointerdown", handleOutside, true);
        global.removeEventListener("resize", reposition);
        global.removeEventListener("scroll", repositionForExternalScroll, true);
        disconnectObserver?.disconnect();
        disconnectObserver = null;
        restoreFromPortal();
        options.onClose?.(context);
        if (restoreFocus) {
          global.setTimeout(() => {
            if (!opened && trigger.isConnected) trigger.focus?.({ preventScroll: true });
          }, 0);
        }
        return controller;
      },
      toggle(context) {
        return opened ? controller.close(context) : controller.open(context);
      },
      isOpen: () => opened,
      reposition,
      destroy() {
        controller.close();
        overlay.destroy();
      },
    };
    panel.hidden = true;
    trigger.setAttribute("aria-haspopup", options.ariaHaspopup || "menu");
    trigger.setAttribute("aria-expanded", "false");
    return controller;
  }

  function bindDynamicPopover(options = {}) {
    const panel = elementOf(options.panel);
    if (!panel) return null;
    let binding = null;
    let currentTrigger = null;
    let suppressClose = false;

    const destroyBinding = ({ notify = false } = {}) => {
      if (!binding) return;
      suppressClose = !notify;
      binding.destroy();
      suppressClose = false;
      binding = null;
      currentTrigger = null;
    };

    const controller = {
      element: panel,
      open(triggerRef, context) {
        const trigger = elementOf(triggerRef);
        if (!trigger) {
          controller.close(context);
          return controller;
        }
        if (binding && currentTrigger !== trigger) destroyBinding();
        if (!binding) {
          currentTrigger = trigger;
          binding = bindPopover({
            ...options,
            trigger,
            panel,
            onOpen: openContext => options.onOpen?.(openContext, controller),
            onClose: closeContext => {
              if (suppressClose) return;
              const previousTrigger = currentTrigger;
              const restoreAfterRender = (
                options.restoreFocusOnEscape !== false
                && isEscapeContext(closeContext)
              );
              options.onClose?.(closeContext, controller);
              if (restoreAfterRender && previousTrigger && !previousTrigger.isConnected) {
                elementOf(options.resolveTriggerAfterClose?.(previousTrigger))?.focus?.();
              }
            },
          });
        }
        binding.open(context);
        return controller;
      },
      close(context) {
        binding?.close(context);
        return controller;
      },
      isOpen: () => binding?.isOpen() || false,
      reposition() {
        binding?.reposition();
        return controller;
      },
      trigger: () => currentTrigger,
      destroy() {
        destroyBinding({ notify: true });
      },
    };
    panel.hidden = true;
    return controller;
  }

  function bindFloatingPopover(options = {}) {
    const panel = elementOf(options.panel);
    if (!panel) return null;
    const overlay = register(panel, {
      kind: "popover",
      closeOnEscape: options.closeOnEscape !== false,
      onRequestClose: event => controller.close(event),
    });
    let opened = false;
    let lastContext = null;
    let raf = 0;
    let placeholder = null;
    let originalParent = null;

    const mountInPortal = () => {
      if (options.portal === false || panel.parentNode === document.body) return;
      originalParent = panel.parentNode;
      placeholder = document.createComment("portal-floating-popover");
      originalParent?.insertBefore(placeholder, panel);
      document.body.appendChild(panel);
    };

    const restoreFromPortal = () => {
      if (options.portal === false || !placeholder) return;
      if (placeholder.isConnected) placeholder.replaceWith(panel);
      else originalParent?.appendChild(panel);
      placeholder = null;
      originalParent = null;
    };

    const reposition = () => {
      if (!opened) return;
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => positionAtPoint(panel, lastContext, options));
    };

    const handleOutside = event => {
      if (!opened || options.closeOnOutside === false || panel.contains(event.target)) return;
      controller.close(event);
    };

    const handleScroll = event => {
      if (!opened) return;
      if (options.closeOnScroll === true) controller.close(event);
      else reposition();
    };

    const controller = {
      element: panel,
      open(context = {}) {
        lastContext = context || {};
        panel.dataset.portalOverlayKind = "popover";
        if (opened) {
          overlay.activate(context);
          reposition();
          return controller;
        }
        opened = true;
        mountInPortal();
        panel.hidden = false;
        overlay.activate(context);
        document.addEventListener("pointerdown", handleOutside, true);
        global.addEventListener("resize", reposition);
        global.addEventListener("scroll", handleScroll, true);
        options.onOpen?.(context);
        reposition();
        return controller;
      },
      close(context) {
        if (!opened) return controller;
        opened = false;
        cancelAnimationFrame(raf);
        overlay.deactivate();
        panel.hidden = true;
        document.removeEventListener("pointerdown", handleOutside, true);
        global.removeEventListener("resize", reposition);
        global.removeEventListener("scroll", handleScroll, true);
        restoreFromPortal();
        options.onClose?.(context);
        return controller;
      },
      toggle(context = {}) {
        return opened ? controller.close(context) : controller.open(context);
      },
      isOpen: () => opened,
      reposition,
      destroy() {
        controller.close();
        overlay.destroy();
      },
    };
    panel.hidden = true;
    return controller;
  }

  // Own Escape at the browsing-context boundary. Reinstalling the same
  // function is idempotent and lets embedded shells replace their document.
  const installGlobalListeners = () => {
    global.addEventListener("keydown", requestTopClose, true);
  };
  global[RUNTIME_KEY] = Object.freeze({ install: installGlobalListeners });
  installGlobalListeners();

  global.PortalOverlay = Object.freeze({
    bindDynamicPopover,
    bindFloatingPopover,
    bindPopover,
    positionAnchored,
    register,
    top: () => top()?.controller || null,
  });
  global.dispatchEvent(new Event("portal:overlay-ready"));
})(window);
