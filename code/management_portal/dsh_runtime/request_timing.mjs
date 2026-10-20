/** Observe model dispatch in Portal's runtime; never patch the Provider package. */
export function observeModelRequests(onStart, originalFetch = globalThis.fetch) {
  const observedFetch = function(input, init) {
    try {
      const headers = new Headers(init?.headers ?? input?.headers);
      const sessionId = headers.get('x-deepseek-harness-session-id');
      const url = typeof input === 'string' || input instanceof URL ? String(input) : input.url;
      if (sessionId && new URL(url).pathname.endsWith('/messages')
          && !headers.has('x-deepseek-harness-compact')) {
        onStart(sessionId, new Date().toISOString());
      }
    } catch {
      // Observation is optional. Pass the exact original arguments through,
      // even when event extraction or the observer fails.
    }
    return originalFetch.call(globalThis, input, init);
  };
  globalThis.fetch = observedFetch;
  return () => { if (globalThis.fetch === observedFetch) globalThis.fetch = originalFetch; };
}
