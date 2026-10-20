// Renderer lifetime only. The parent supplies one already-authorized, pinned
// content snapshot; this host has no SDK, resource loading or domain simulator.
export function createExplorationViewHost(root) {
  let active = null, disposed = false;
  const clone = value => value == null ? null : structuredClone(value);
  function retire() {
    const old = active;
    active = null; // Disable callbacks before calling renderer cleanup.
    root.replaceChildren(); // Remove protected content even if cleanup throws.
    if (old) {
      old.controller.abort();
      try { old.instance?.hide?.(); }
      finally { old.instance?.destroy?.(); }
    }
  }
  function capture() {
    return active?.instance ? {
      viewState: clone(active.instance.getViewState?.()),
      scene: clone(active.instance.getScene?.()),
    } : { viewState: null, scene: null };
  }
  async function mount({ factory, subject, content, ports = {}, initialState = null }) {
    if (disposed) throw new Error('exploration_view_host_destroyed');
    if (typeof factory !== 'function') throw new Error('exploration_renderer_unavailable');
    retire();
    const stage = root.ownerDocument.createElement('div');
    stage.className = 'arch-exploration-surface';
    const entry = { controller: new AbortController(), stage, instance: null };
    active = entry; root.replaceChildren(stage);
    const guardedPorts = {};
    for (const name of ['select', 'navigate', 'source', 'change', 'return']) {
      guardedPorts[name] = (...args) => {
        if (active === entry && !entry.controller.signal.aborted) return ports[name]?.(...args);
      };
    }
    try {
      const instance = await factory(stage, {
        subject, content, ports: guardedPorts, signal: entry.controller.signal,
      });
      if (active !== entry || entry.controller.signal.aborted) {
        try { instance?.hide?.(); } finally { instance?.destroy?.(); }
        return { status: 'superseded' };
      }
      if (!instance || typeof instance.destroy !== 'function') {
        throw new Error('invalid_exploration_renderer');
      }
      entry.instance = instance;
      if (initialState !== null) instance.restoreViewState?.(clone(initialState));
      instance.show?.();
      return { status: 'mounted' };
    } catch (e) {
      if (active !== entry || entry.controller.signal.aborted) return { status: 'superseded' };
      retire();
      throw e;
    }
  }
  return {
    mount, capture,
    hide() { try { return capture(); } finally { retire(); } },
    destroy() { if (disposed) return; disposed = true; retire(); },
  };
}

// Adapter for the platform-owned instruction workbench. Keep one parent route
// stack and expose only the approved callbacks; never pass the SDK through.
export function instructionWorkbenchFactory(createInstructionWorkbench) {
  return (root, { subject, content, ports, signal }) =>
    createInstructionWorkbench(root, {
      subject, content, signal,
      onSelect: ports.select, onNavigate: ports.navigate, onSource: ports.source,
      onChange: ports.change, onReturn: ports.return,
    });
}

