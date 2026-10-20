import { createResources } from './resources.js';

export async function start(mount, region) {
  const root = document.getElementById('app');
  const abort = new AbortController();
  const disposers = [];
  let runtime, finishWaiting, visible = false;
  const sdk = window.Projection;
  const onDispose = fn => {
    let live = true, pending;
    const once = () => {
      if (!live || pending) return pending;
      let result;
      try { result = fn(); } catch (error) { result = Promise.reject(error); }
      pending = Promise.resolve(result);
      // Lifecycle callers may discard the result (hide/destroy), while callers
      // that await it still receive the original failure and can retry.
      pending.then(() => { live = false; pending = null; }, error => {
        pending = null;
        console.error('Region cleanup failed', error);
      });
      return pending;
    };
    if (abort.signal.aborted) once(); else disposers.push(once);
    return once;
  };
  const dispose = () => {
    if (abort.signal.aborted) return;
    abort.abort();
    finishWaiting?.();
    for (const fn of disposers.splice(0).reverse()) fn();
    runtime?.destroy?.();
  };
  addEventListener('pagehide', dispose, { once: true });
  try {
    const context = Object.freeze({
      region, sdk, signal: abort.signal, resources: createResources(sdk, { signal: abort.signal }), onDispose,
      onFocus: fn => onDispose(sdk.onFocus(fn)),
      watch: async (refs, fn) => onDispose(await sdk.watch(refs, event => {
        if (!abort.signal.aborted) fn(event);
      })),
    });
    // Hidden tabs must not read their reports or sources before first use.
    // Subscribe before mounting so visibility changes during async startup
    // are retained. mount already initializes the first visible view.
    runtime = await new Promise((resolve, reject) => {
      let started = false;
      finishWaiting = () => resolve(undefined);
      onDispose(sdk.onVisibility(next => {
        if (abort.signal.aborted || visible === !!next) return;
        visible = !!next;
        if (!started && visible) {
          started = true;
          finishWaiting = null;
          Promise.resolve().then(() => {
            if (abort.signal.aborted) return;
            return mount(root, context);
          }).then(resolve, reject);
        } else if (runtime) {
          if (visible) runtime.show?.(); else runtime.hide?.();
        }
      }));
    });
    if (abort.signal.aborted) { runtime?.destroy?.(); return; }
    if (!visible) runtime?.hide?.();
    return dispose;
  } catch (error) {
    dispose();
    root.replaceChildren();
    const alert = document.createElement('p');
    alert.setAttribute('role', 'alert');
    alert.textContent = error.message;
    root.append(alert);
  }
}
