(() => {
  "use strict";

  if (window.PortalHatgGraphQuickFork) return;

  // A Resident's quick Fork: the "+" beside its card forks it on a click, opens
  // the menu of Fork modes on hover or arrow keys, and dragged onto a Seed makes
  // the Resident that Seed's candidate fork.

  const DEPENDENCIES = Object.freeze([
    "animationDuration", "layers", "nodeHasQuickFork", "nodePosition", "options", "root", "state",
  ]);

  function createQuickFork(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalHatgGraphQuickFork is missing: ${missing.join(", ")}`);
    const {
      animationDuration, layers, nodeHasQuickFork, nodePosition, options, root, state,
    } = dependencies;

    let quickForkMenu = null;
    let quickForkPopover = null;
    let quickForkSourceId = "";
    let quickForkCloseTimer = 0;

    function closeQuickForkMenu() {
      window.clearTimeout(quickForkCloseTimer);
      quickForkPopover?.destroy();
      quickForkPopover = null;
      quickForkMenu?.remove();
      quickForkMenu = null;
      quickForkSourceId = "";
    }

    function scheduleQuickForkClose(event) {
      if (quickForkMenu?.contains(event.relatedTarget)
        || quickForkPopover?.trigger?.contains(event.relatedTarget)) return;
      window.clearTimeout(quickForkCloseTimer);
      quickForkCloseTimer = window.setTimeout(() => {
        if (quickForkMenu?.contains(document.activeElement)) return;
        closeQuickForkMenu();
      }, 220);
    }

    function invokeQuickFork(sourceId, event, mode) {
      event.preventDefault();
      event.stopPropagation();
      const source = state.nodeMap.get(sourceId);
      closeQuickForkMenu();
      if (!source || !nodeHasQuickFork(source) || state.model.quickForkNodeId) return;
      options.onNodeQuickFork?.(source, event, mode);
    }

    // Dragging a Resident's “+” onto a Seed makes that Resident the Seed's
    // candidate fork (decision_4adcc1ff2f2c); a click on “+” still forks.
    const CANDIDATE_FORK_DRAG_DISTANCE = 6;
    let candidateForkDrag = null;
    let suppressQuickForkClick = false;

    function candidateForkDropTarget(event) {
      const element = document.elementFromPoint(event.clientX, event.clientY);
      const seed = element?.closest?.('button.hatgNode[data-node-kind="seed"][data-node-id]');
      return seed && root.contains(seed) ? seed : null;
    }

    function endCandidateForkDrag() {
      if (!candidateForkDrag) return;
      candidateForkDrag.target?.classList.remove("candidateForkDropTarget");
      candidateForkDrag.overlay?.remove();
      root.classList.remove("isCandidateForkDragging");
      candidateForkDrag = null;
    }

    function onCandidateForkPointerDown(event, node) {
      if (
        event.button !== 0
        || event.pointerType === "touch"
        || state.model.readOnly
        || state.model.quickForkNodeId
        || typeof options.onCandidateForkDrop !== "function"
      ) return;
      endCandidateForkDrag();
      candidateForkDrag = {
        sourceId: node.id,
        pointerId: event.pointerId,
        trigger: event.currentTarget,
        startX: event.clientX,
        startY: event.clientY,
        active: false,
        overlay: null,
        line: null,
        target: null,
      };
    }

    function onCandidateForkPointerMove(event) {
      const drag = candidateForkDrag;
      if (!drag || event.pointerId !== drag.pointerId) return;
      if (!drag.active) {
        if (Math.hypot(event.clientX - drag.startX, event.clientY - drag.startY) < CANDIDATE_FORK_DRAG_DISTANCE) return;
        drag.active = true;
        closeQuickForkMenu();
        // The HATG styles apply inside an overlay component root.
        const svgNamespace = "http://www.w3.org/2000/svg";
        drag.overlay = document.createElement("div");
        drag.overlay.className = "portalHatgOverlayComponent";
        const svg = document.createElementNS(svgNamespace, "svg");
        svg.setAttribute("class", "hatgCandidateForkDraft");
        svg.setAttribute("aria-hidden", "true");
        drag.line = document.createElementNS(svgNamespace, "line");
        svg.appendChild(drag.line);
        drag.overlay.appendChild(svg);
        document.body.appendChild(drag.overlay);
        root.classList.add("isCandidateForkDragging");
      }
      event.preventDefault();
      const box = drag.trigger.getBoundingClientRect();
      drag.line.setAttribute("x1", String(box.left + box.width / 2));
      drag.line.setAttribute("y1", String(box.top + box.height / 2));
      drag.line.setAttribute("x2", String(event.clientX));
      drag.line.setAttribute("y2", String(event.clientY));
      const target = candidateForkDropTarget(event);
      if (target !== drag.target) drag.target?.classList.remove("candidateForkDropTarget");
      target?.classList.add("candidateForkDropTarget");
      drag.target = target;
    }

    function onCandidateForkPointerUp(event) {
      const drag = candidateForkDrag;
      if (!drag || event.pointerId !== drag.pointerId) return;
      const target = drag.active ? candidateForkDropTarget(event) : null;
      const source = state.nodeMap.get(drag.sourceId);
      const active = drag.active;
      endCandidateForkDrag();
      if (!active) return;
      // The click that ends this drag is not a Fork.
      suppressQuickForkClick = true;
      window.setTimeout(() => { suppressQuickForkClick = false; }, 0);
      if (source && target?.dataset.nodeId) options.onCandidateForkDrop?.(source, target.dataset.nodeId, event);
    }

    function onCandidateForkPointerCancel(event) {
      if (candidateForkDrag && event.pointerId === candidateForkDrag.pointerId) endCandidateForkDrag();
    }

    function openQuickForkMenu(node, event, { focus = false, last = false } = {}) {
      if (!nodeHasQuickFork(node) || state.model.quickForkNodeId) return;
      const trigger = event.currentTarget;
      if (quickForkSourceId !== node.id || !quickForkPopover?.isOpen()) {
        closeQuickForkMenu();
        const modes = options.nodeQuickForkModes?.(node) || [];
        if (!modes.length) return;
        quickForkSourceId = node.id;
        const panel = document.createElement("div");
        panel.className = "portalHatgOverlayComponent hatgQuickForkMenu";
        panel.setAttribute("role", "menu");
        panel.setAttribute("aria-label", window.PortalI18n.text`从「${node.title || node.id}」Fork`);
        const heading = document.createElement("div");
        heading.className = "hatgQuickForkMenuHeading";
        heading.textContent = window.PortalI18n.t("选择 Fork 方式");
        panel.appendChild(heading);
        for (const mode of modes) {
          const button = document.createElement("button");
          button.type = "button";
          button.setAttribute("role", "menuitem");
          button.dataset.forkMode = mode.value;
          button.dataset.portalTooltipOptOut = "1";
          button.setAttribute("aria-label", mode.label);
          const label = document.createElement("strong");
          label.textContent = mode.label;
          const hint = document.createElement("small");
          hint.textContent = mode.description;
          button.append(label, hint);
          button.addEventListener("click", actionEvent => {
            invokeQuickFork(node.id, actionEvent, mode.value);
          });
          panel.appendChild(button);
        }
        panel.addEventListener("pointerenter", () => window.clearTimeout(quickForkCloseTimer));
        panel.addEventListener("pointerleave", scheduleQuickForkClose);
        panel.addEventListener("focusout", scheduleQuickForkClose);
        panel.addEventListener("keydown", keyEvent => {
          const buttons = Array.from(panel.querySelectorAll('[role="menuitem"]'));
          const index = buttons.indexOf(document.activeElement);
          const target = {
            ArrowDown: (index + 1) % buttons.length,
            ArrowUp: (index - 1 + buttons.length) % buttons.length,
            Home: 0,
            End: buttons.length - 1,
          }[keyEvent.key];
          if (target === undefined) return;
          keyEvent.preventDefault();
          buttons[target].focus();
        });
        quickForkMenu = panel;
        root.appendChild(panel);
        quickForkPopover = window.PortalOverlay.bindPopover({
          trigger, panel, portal: true, placement: "bottom-start", gap: 8,
          onClose: () => { panel.remove(); },
        });
        quickForkPopover.open(event);
      }
      window.clearTimeout(quickForkCloseTimer);
      if (focus) {
        const buttons = quickForkMenu.querySelectorAll('[role="menuitem"]');
        buttons[last ? buttons.length - 1 : 0]?.focus();
      }
    }

    function renderQuickForkButtons(actionNodes) {
      const quickForkNodes = actionNodes.filter(nodeHasQuickFork);
      layers.nodes.selectAll("button.hatgNodeQuickFork")
        .data(quickForkNodes, node => node.id)
        .join(
          enter => enter.append("button").attr("type", "button").style("opacity", 0)
            .call(items => items.transition().duration(animationDuration(140)).style("opacity", 1)),
          update => update,
          exit => exit.remove(),
        )
        .attr("class", "hatgNodeQuickFork")
        .attr("data-node-quick-fork", "")
        .attr("data-source-node-id", node => node.id)
        .attr("data-busy", node => state.model.quickForkNodeId === node.id ? "true" : null)
        .attr("aria-busy", node => state.model.quickForkNodeId === node.id ? "true" : "false")
        .attr("aria-label", node => `从「${node.title || node.id}」完整上下文 Fork 新 Resident`)
        .attr("aria-description", "点击直接 Fork；悬停或按方向键选择其他创建方式；拖到 Seed 上建立候选 Fork。")
        .attr("aria-haspopup", "menu")
        .attr("data-portal-tooltip-opt-out", "1")
        .attr("title", null)
        .attr("data-portal-tooltip", null)
        .property("disabled", () => Boolean(state.model.quickForkNodeId))
        .style("left", node => `${nodePosition(node).x}%`)
        .style("top", node => `${nodePosition(node).y}%`)
        .attr("data-portal-icon", node => state.model.quickForkNodeId === node.id ? null : "plus")
        .text("")
        .each(function () { window.PortalUIPrimitives.mountIcon(this); })
        .on("pointerenter.hatg", (event, node) => {
          if (event.pointerType !== "touch" && !candidateForkDrag?.active) openQuickForkMenu(node, event);
        })
        .on("pointerdown.hatg", (event, node) => onCandidateForkPointerDown(event, node))
        .on("pointerleave.hatg", scheduleQuickForkClose)
        .on("blur.hatg", scheduleQuickForkClose)
        .on("keydown.hatg", (event, node) => {
          if (!["ArrowDown", "ArrowUp"].includes(event.key)) return;
          event.preventDefault();
          event.stopPropagation();
          openQuickForkMenu(node, event, { focus: true, last: event.key === "ArrowUp" });
        })
        .on("dblclick.hatg", event => {
          event.preventDefault();
          event.stopPropagation();
        })
        .on("click.hatg", (event, node) => {
          event.preventDefault();
          event.stopPropagation();
          if (event.detail > 1 || suppressQuickForkClick) return;
          invokeQuickFork(node.id, event, "full");
        });
    }

    // The Seed card under a candidate-fork drag.
    const candidateForkTargetId = () => candidateForkDrag?.target?.dataset.nodeId;

    function repositionQuickForkMenu() {
      quickForkPopover?.reposition();
    }

    // A render closes the open menu when its node can no longer fork.
    function syncQuickForkMenu() {
      if (quickForkMenu) {
        const source = state.nodeMap.get(quickForkSourceId);
        if (!source || !nodeHasQuickFork(source) || state.model.quickForkNodeId) closeQuickForkMenu();
        else quickForkPopover?.reposition();
      }
    }

    window.addEventListener("pointermove", onCandidateForkPointerMove, true);
    window.addEventListener("pointerup", onCandidateForkPointerUp, true);
    window.addEventListener("pointercancel", onCandidateForkPointerCancel, true);

    function destroyQuickFork() {
      closeQuickForkMenu();
      window.removeEventListener("pointermove", onCandidateForkPointerMove, true);
      window.removeEventListener("pointerup", onCandidateForkPointerUp, true);
      window.removeEventListener("pointercancel", onCandidateForkPointerCancel, true);
      endCandidateForkDrag();
    }

    return {
      candidateForkTargetId, closeQuickForkMenu, destroyQuickFork, renderQuickForkButtons, repositionQuickForkMenu,
      syncQuickForkMenu,
    };
  }

  window.PortalHatgGraphQuickFork = Object.freeze({ createQuickFork, dependencies: DEPENDENCIES });
})();
