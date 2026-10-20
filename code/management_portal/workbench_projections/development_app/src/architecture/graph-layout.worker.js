import { layoutGraph, layoutRuntime, layoutScope } from './layout.js';

// Each graph owns one worker. Only structural data crosses this boundary.
let previous = {};
self.addEventListener('message', ({ data }) => {
  const { requestId, nodes, edges, runtime, scopeRoot, width, showSupporting, reveal } = data || {};
  if (requestId == null || !Array.isArray(nodes) || (!runtime && !Array.isArray(edges))) return;
  try {
    const result = runtime ? scopeRoot ? layoutScope(data, { width }) : layoutRuntime(nodes, { width, showSupporting, reveal }) : layoutGraph(nodes, edges, { previous });
    if (!runtime) previous = result.positions;
    self.postMessage({ requestId, ...result });
  } catch (error) {
    self.postMessage({ requestId, error: error instanceof Error ? error.message : String(error) });
  }
});
