(() => {
  "use strict";

  if (window.PortalHatgGraphWiring) return;

  // Dragging a wire out of an output port: the dashed preview follows the
  // pointer and the release connects the draft to the port or item under it.

  const DEPENDENCIES = Object.freeze([
    "connectToItem", "options", "portCompatibility", "portIdentity", "root", "state",
  ]);

  function createWiring(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalHatgGraphWiring is missing: ${missing.join(", ")}`);
    const {
      connectToItem, options, portCompatibility, portIdentity, root, state,
    } = dependencies;

    let wireGesture = null;
    let suppressWireClick = false;
    const wirePreview = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    wirePreview.classList.add("hatgWirePreview");
    wirePreview.setAttribute("aria-hidden", "true");
    wirePreview.innerHTML = '<path fill="none" stroke="currentColor" stroke-width="2" stroke-dasharray="5 4" />';
    root.append(wirePreview);
    function cancelWireGesture() {
      if (wireGesture) wireGesture.cancelled = true;
      wireGesture = null;
      wirePreview.style.display = "none";
    }
    function onWirePointerDown(event) {
      const control = event.target?.closest?.("button.hatgPort.out");
      if (event.button !== 0 || !control || state.model.readOnly || control.disabled) return;
      const port = control.__data__;
      if (state.model.wireDraft && portIdentity(port) !== portIdentity(state.model.wireDraft)) return;
      wireGesture = { port, pointerId: event.pointerId, x: event.clientX, y: event.clientY };
    }
    function onWirePointerMove(event) {
      const gesture = wireGesture;
      if (!gesture || gesture.pointerId !== event.pointerId) return;
      if (!gesture.ready && Math.hypot(event.clientX - gesture.x, event.clientY - gesture.y) < 5) return;
      event.preventDefault();
      if (!gesture.ready) {
        gesture.ready = Promise.resolve(state.model.wireDraft ? null : options.onPortClick?.(gesture.port, event))
          .then(() => {
            if (gesture.cancelled && portIdentity(state.model.wireDraft) === portIdentity(gesture.port)) {
              options.onWireDraftCancel?.(state.model.wireDraft);
            }
          });
      }
      const bounds = root.getBoundingClientRect();
      const x = gesture.x - bounds.left, y = gesture.y - bounds.top;
      const tx = event.clientX - bounds.left, ty = event.clientY - bounds.top;
      wirePreview.style.display = "block";
      wirePreview.firstChild.setAttribute("d", `M${x},${y} C${x + 60},${y} ${tx - 60},${ty} ${tx},${ty}`);
    }
    async function onWirePointerUp(event) {
      const gesture = wireGesture;
      if (!gesture || gesture.pointerId !== event.pointerId) return;
      wireGesture = null;
      wirePreview.style.display = "none";
      if (!gesture.ready) return;
      suppressWireClick = true;
      window.setTimeout(() => { suppressWireClick = false; }, 0);
      const element = document.elementFromPoint(event.clientX, event.clientY);
      const port = element?.closest("button.hatgPort")?.__data__;
      const item = element?.closest("button.hatgNode, button.edgeLabelBtn")?.__data__;
      await gesture.ready;
      if (gesture.cancelled || state.model.readOnly || portIdentity(state.model.wireDraft) !== portIdentity(gesture.port)) return;
      if (port && portCompatibility(port) === true) options.onPortClick?.(port, event);
      else if (item) connectToItem(item, event);
    }
    function onWireGestureClick(event) {
      if (!suppressWireClick) return;
      event.preventDefault();
      event.stopImmediatePropagation();
    }
    root.addEventListener("pointerdown", onWirePointerDown);
    root.addEventListener("click", onWireGestureClick, true);
    document.addEventListener("pointermove", onWirePointerMove, { passive: false });
    document.addEventListener("pointerup", onWirePointerUp);
    document.addEventListener("pointercancel", cancelWireGesture);

    function destroyWiring() {
      cancelWireGesture();
      wirePreview.remove();
      root.removeEventListener("pointerdown", onWirePointerDown);
      root.removeEventListener("click", onWireGestureClick, true);
      document.removeEventListener("pointermove", onWirePointerMove);
      document.removeEventListener("pointerup", onWirePointerUp);
      document.removeEventListener("pointercancel", cancelWireGesture);
    }

    return {
      cancelWireGesture, destroyWiring,
    };
  }

  window.PortalHatgGraphWiring = Object.freeze({ createWiring, dependencies: DEPENDENCIES });
})();
