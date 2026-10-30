(function (global) {
  "use strict";

  if (global.PortalWorkbenchLayoutLock) return;

  // Locking the layout: the lock of this workbench and the one it inherits
  // from an outer workbench, the toolbar, tabs and split handles that follow
  // the lock, the fixed-Panel switchers and the chrome animation of a lock
  // change.

  const DEPENDENCIES = Object.freeze([
    "announceBeforeLayout", "announceChange", "canvas", "closeMenu", "customizeButton", "descriptors",
    "fixedSwitchers", "focusPanel", "instances", "layoutLockSynchronizers", "lockButton", "persist", "root",
    "shellState", "stage", "toolbar",
  ]);

  function createLayoutLock(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalWorkbenchLayoutLock is missing: ${missing.join(", ")}`);
    const {
      announceBeforeLayout, announceChange, canvas, closeMenu, customizeButton, descriptors, fixedSwitchers,
      focusPanel, instances, layoutLockSynchronizers, lockButton, persist, root, shellState, stage, toolbar,
    } = dependencies;

    let fixedSwitcherSignature = "";
    const layoutLockAnimations = new Set();
    const reducedLayoutMotion = global.matchMedia?.("(prefers-reduced-motion: reduce)");

    function cancelLayoutLockAnimations() {
      layoutLockAnimations.forEach(animation => animation.cancel());
      layoutLockAnimations.clear();
    }

    function onLayoutMotionPreferenceChange() {
      if (reducedLayoutMotion.matches) cancelLayoutLockAnimations();
    }
    reducedLayoutMotion?.addEventListener("change", onLayoutMotionPreferenceChange);

    function animateLayoutChrome(element, from, to) {
      const animation = element.animate?.([from, to], {
        duration: 220, easing: "cubic-bezier(.2,.8,.2,1)",
      });
      if (!animation) return;
      animation.id = "portal-workbench-layout-lock";
      layoutLockAnimations.add(animation);
      animation.finished.then(
        () => layoutLockAnimations.delete(animation),
        () => layoutLockAnimations.delete(animation),
      );
    }

    // Layout locking belongs to the shell, not to Panel runtimes. Nested shells
    // inherit the lock without persisting it as their own user preference.
    function inheritedLayoutLock() {
      const ancestor = root.parentElement?.closest("[data-portal-workbench-id]");
      return instances.get(ancestor?.dataset.portalWorkbenchId)?.isLayoutLocked() === true;
    }

    function isLayoutLocked() { return shellState.layoutLocked || inheritedLayoutLock(); }

    function syncLayoutLock(animate = false) {
      if (shellState.destroyed) return;
      const locked = isLayoutLocked();
      const inherited = inheritedLayoutLock();
      const changed = root.dataset.portalWorkbenchLayoutLocked !== String(locked);
      const own = selector => [...canvas.querySelectorAll(selector)]
        .filter(element => element.closest("[data-portal-workbench-id]") === root);
      // Snapshot shell chrome only. Panel bodies take their final geometry once;
      // no content transform, clone, remount or per-frame resize is involved.
      const chromeBefore = animate && changed && !reducedLayoutMotion?.matches
        ? own(".portalWorkbenchStack:not(.is-collapsed), .portalWorkbenchStack:not(.is-collapsed) > .portalWorkbenchTabs")
          .filter(element => element.getClientRects().length || element.classList.contains("portalWorkbenchTabs"))
          .map(element => {
            const style = getComputedStyle(element);
            return {
              element, visible: style.display !== "none", height: element.getBoundingClientRect().height,
              opacity: style.opacity, transform: style.transform,
              frame: { borderColor: style.borderColor, borderRadius: style.borderRadius, boxShadow: style.boxShadow },
            };
          })
        : [];
      if (changed) cancelLayoutLockAnimations();
      root.dataset.portalWorkbenchLayoutLocked = String(locked);
      stage.classList.toggle("is-layout-locked", locked);
      toolbar.classList.toggle("is-layout-locked", locked);
      toolbar.classList.toggle("is-layout-lock-inherited", inherited);
      const lockLabel = locked ? window.PortalI18n.t("解锁") : window.PortalI18n.t("固定");
      lockButton.title = inherited ? window.PortalI18n.t("布局已由外层工作台固定") : lockLabel;
      lockButton.setAttribute("aria-label", lockButton.title);
      lockButton.setAttribute("aria-pressed", String(locked));
      lockButton.disabled = inherited;
      customizeButton.disabled = locked;
      if (locked) closeMenu();
      own(".portalWorkbenchTabs").forEach(tabs => { tabs.inert = locked; });
      own("[data-portal-workbench-tab]").forEach(tab => {
        tab.draggable = !locked && descriptors.get(tab.dataset.portalWorkbenchTab)?.canMove === true;
      });
      own(".portalWorkbenchStack").forEach(stack => {
        if (locked) {
          stack.classList.remove("is-tab-strip-revealed");
          delete stack.dataset.portalWorkbenchDropZone;
        }
      });
      own(".portalWorkbenchSplitHandle").forEach(handle => {
        handle.tabIndex = locked ? -1 : 0;
        handle.setAttribute("aria-disabled", String(locked));
        handle.setAttribute("aria-label", locked ? window.PortalI18n.t("面板分隔线") : window.PortalI18n.t("调整工作区面板大小"));
      });
      const tabGroups = [];
      const collectTabGroups = node => {
        if (node?.type === "stack" && node.children.length > 1) tabGroups.push(node);
        else if (node?.type === "split") node.children.forEach(collectTabGroups);
      };
      if (locked) collectTabGroups(shellState.model);
      const signature = JSON.stringify(tabGroups.map(node => [node.id, node.children.map(child => (
        [child.panelId, descriptors.get(child.panelId).title, shellState.collapsed.has(child.panelId)]
      ))]));
      if (signature !== fixedSwitcherSignature) {
        fixedSwitcherSignature = signature;
        fixedSwitchers.replaceChildren();
        tabGroups.forEach(node => {
          const select = document.createElement("select");
          select.className = "portalWorkbenchFixedPanelSelect";
          select.setAttribute("aria-label", window.PortalI18n.t("切换固定面板"));
          select.disabled = shellState.collapsed.has(node.activePanelId);
          node.children.forEach(child => {
            const descriptor = descriptors.get(child.panelId);
            const option = document.createElement("option");
            option.value = child.panelId;
            option.textContent = descriptor.title;
            option.disabled = shellState.collapsed.has(child.panelId);
            select.append(option);
          });
          select.addEventListener("change", () => focusPanel(select.value));
          fixedSwitchers.append(select);
        });
      }
      tabGroups.forEach((node, index) => { fixedSwitchers.children[index].value = node.activePanelId; });
      fixedSwitchers.hidden = !locked || !fixedSwitchers.childElementCount || global.PortalResponsive?.isMobile?.() === true;
      toolbar.hidden = inherited && fixedSwitchers.hidden;
      chromeBefore.forEach(before => {
        const { element } = before;
        const after = getComputedStyle(element);
        if (element.classList.contains("portalWorkbenchStack")) {
          animateLayoutChrome(element, before.frame, {
            borderColor: after.borderColor, borderRadius: after.borderRadius, boxShadow: after.boxShadow,
          });
        } else if (locked && before.visible && before.height) {
          // The departing header is an inert overlay: it cannot reserve a grid
          // row or intercept the now-available content beneath it.
          const overlay = {
            display: "flex", position: "absolute", top: "0px", left: "0px", right: "0px",
            height: `${before.height}px`, zIndex: 3, pointerEvents: "none",
          };
          const transform = before.transform === "none" ? "" : before.transform;
          animateLayoutChrome(element,
            { ...overlay, opacity: before.opacity, transform: before.transform },
            { ...overlay, opacity: 0, transform: `${transform} translateY(-8px)` });
        } else if (!locked && element.getClientRects().length) {
          const transform = after.transform === "none" ? "" : after.transform;
          animateLayoutChrome(element,
            { opacity: 0, transform: `${transform} translateY(-8px)` },
            { opacity: after.opacity, transform: after.transform });
        }
      });
    }

    function setLayoutLocked(value) {
      if (shellState.destroyed || inheritedLayoutLock()) return false;
      const next = value === true;
      if (shellState.layoutLocked === next) return true;
      announceBeforeLayout("layout-lock");
      shellState.layoutLocked = next;
      layoutLockSynchronizers.forEach(sync => sync(true));
      persist();
      announceChange("layout-lock");
      return true;
    }
    layoutLockSynchronizers.add(syncLayoutLock);

    function destroyLayoutLock() {
      layoutLockSynchronizers.delete(syncLayoutLock);
      cancelLayoutLockAnimations();
      reducedLayoutMotion?.removeEventListener("change", onLayoutMotionPreferenceChange);
    }

    return {
      destroyLayoutLock, inheritedLayoutLock, isLayoutLocked, setLayoutLocked, syncLayoutLock,
    };
  }

  global.PortalWorkbenchLayoutLock = Object.freeze({ createLayoutLock, dependencies: DEPENDENCIES });
})(window);
