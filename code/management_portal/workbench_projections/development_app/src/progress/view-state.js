// Local reading context only. Resource content is reauthorized by the region.
const nodes = (root, selector) => [...root.querySelectorAll(selector)];
const owner = (node) =>
  node.closest("[data-view-owner]")?.dataset.viewOwner || "";

// The host hides its containing iframe before the asynchronous visibility
// message arrives. At that point scrollY reads zero and focus may be BODY.
// Keep only measurable reading context, never resource bodies, across that gap.
export function observeView(root, active, changed) {
  const document = root.ownerDocument,
    window = document.defaultView;
  let latest = null;
  const measurable = () => root.getBoundingClientRect().width > 0;
  const remember = () => {
    if (!active() || !measurable()) return;
    latest = captureView(root);
    changed?.(latest);
  };
  const events = [
    [window, "scroll"],
    [document, "selectionchange"],
    [root, "focusin"],
    [root, "input"],
    [root, "toggle"],
  ];
  for (const [node, type] of events)
    node.addEventListener(type, remember, { capture: true, passive: true });
  return {
    capture() {
      remember();
      return latest;
    },
    restore(state) {
      if (!measurable()) return false;
      restoreView(root, state);
      remember();
      return true;
    },
    destroy() {
      for (const [node, type] of events)
        node.removeEventListener(type, remember, true);
      latest = null;
    },
  };
}

export function captureView(root) {
  const document = root.ownerDocument,
    window = document.defaultView;
  const active = document.activeElement;
  let focus = null;
  if (root.contains(active)) {
    focus = {
      tag: active.tagName,
      owner: owner(active),
      label: active.getAttribute("aria-label"),
      change: active.getAttribute("data-change-key"),
      text: active.textContent,
      start: active.selectionStart,
      end: active.selectionEnd,
      direction: active.selectionDirection,
    };
  }
  const ancestors = [];
  for (let node = root; node; node = node.parentElement)
    ancestors.push({ node, left: node.scrollLeft, top: node.scrollTop });
  return {
    focus,
    ancestors,
    x: window.scrollX,
    y: window.scrollY,
    details: nodes(root, "details[data-view-key]").map((n) => ({
      key: n.dataset.viewKey,
      open: n.open,
    })),
    scrolls: nodes(root, "[data-scroll-key]").map((n) => ({
      key: n.dataset.scrollKey,
      left: n.scrollLeft,
      top: n.scrollTop,
    })),
  };
}

export function restoreView(root, state) {
  if (!state) return;
  for (const saved of state.details) {
    const node = nodes(root, "details[data-view-key]").find(
      (n) => n.dataset.viewKey === saved.key,
    );
    if (node) node.open = saved.open;
  }
  const f = state.focus;
  if (f) {
    const node = nodes(root, "button,input,select,textarea,[tabindex]").find(
      (n) =>
        n.tagName === f.tag &&
        owner(n) === f.owner &&
        (f.change
          ? n.getAttribute("data-change-key") === f.change
          : f.label
            ? n.getAttribute("aria-label") === f.label
            : n.textContent === f.text),
    );
    if (node) {
      node.focus({ preventScroll: true });
      if (typeof f.start === "number" && node.setSelectionRange)
        node.setSelectionRange(f.start, f.end, f.direction || "none");
    }
  }
  for (const saved of state.scrolls) {
    const node = nodes(root, "[data-scroll-key]").find(
      (n) => n.dataset.scrollKey === saved.key,
    );
    if (node) {
      node.scrollLeft = saved.left;
      node.scrollTop = saved.top;
    }
  }
  for (const saved of state.ancestors)
    if (saved.node.isConnected) {
      saved.node.scrollLeft = saved.left;
      saved.node.scrollTop = saved.top;
    }
  root.ownerDocument.defaultView.scrollTo(state.x, state.y);
}
