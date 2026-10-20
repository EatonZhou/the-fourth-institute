// Own-document nonmodal overlays; never inspect the embedding parent document.
export function createFloatingPanels(root) {
  const document = root.ownerDocument, window = document.defaultView, panels = new Map();
  let active = true, destroyed = false;
  const close = (id, restoreFocus = false) => {
    const entry = panels.get(id);
    if (!entry?.isOpen()) return;
    entry.setOpen(false);
    if (restoreFocus && entry.trigger.isConnected) entry.trigger.focus();
  };
  const closeAll = () => { for (const id of panels.keys()) close(id); };
  const open = id => {
    if (!active || destroyed) return;
    for (const other of panels.keys()) if (other !== id) close(other);
    panels.get(id)?.setOpen(true);
  };
  const inside = (entry, event) => {
    const path = event.composedPath();
    return path.includes(entry.trigger) || path.includes(entry.panel);
  };
  const outside = event => {
    for (const [id, entry] of panels) if (entry.isOpen() && !inside(entry, event)) close(id);
  };
  const escape = event => {
    if (event.key !== 'Escape') return;
    const entry = [...panels].find(([, p]) => p.isOpen());
    if (entry) { event.preventDefault(); event.stopImmediatePropagation(); close(entry[0], true); }
  };
  const blur = () => { if (!document.hasFocus()) closeAll(); };
  const visibility = () => { if (document.hidden) closeAll(); };
  const attach = () => {
    document.addEventListener('pointerdown', outside, true);
    document.addEventListener('focusin', outside, true);
    document.addEventListener('keydown', escape, true);
    document.addEventListener('visibilitychange', visibility);
    window.addEventListener('blur', blur); window.addEventListener('pagehide', closeAll);
  };
  const detach = () => {
    document.removeEventListener('pointerdown', outside, true);
    document.removeEventListener('focusin', outside, true);
    document.removeEventListener('keydown', escape, true);
    document.removeEventListener('visibilitychange', visibility);
    window.removeEventListener('blur', blur); window.removeEventListener('pagehide', closeAll);
  };
  attach();
  return {
    register(id, entry) {
      panels.set(id, entry);
      const toggle = () => { if (entry.isOpen()) open(id); };
      entry.panel.addEventListener('toggle', toggle);
      entry.removeToggle = () => entry.panel.removeEventListener('toggle', toggle);
    },
    open, close, closeAll,
    toggle(id) { const entry = panels.get(id); if (entry?.isOpen()) close(id); else open(id); },
    hide() { closeAll(); if (active) detach(); active = false; },
    show() { if (!active && !destroyed) { active = true; attach(); } },
    destroy() { if (destroyed) return; destroyed = true; closeAll(); detach(); for (const p of panels.values()) p.removeToggle(); panels.clear(); },
  };
}
