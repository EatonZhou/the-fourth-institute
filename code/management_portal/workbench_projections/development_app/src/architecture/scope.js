// Local navigation only. Inputs must already be filtered for the current viewer.
// The adapter resolves saved identities again; this module never stores titles,
// performs resource reads, or publishes focus.
import { batchIdentity } from './discovery.js';
import { semanticNode } from './model.js';

function readableMap(data) {
  if (!Array.isArray(data?.modules) || !Array.isArray(data?.protocols)) throw new Error('架构来源尚未可读');
}

function scopeModule(data, id) {
  readableMap(data);
  if (id == null) return null;
  const item = data.modules.find(m => m.id === id);
  if (!item) throw new Error('该层级不存在或已不可读');
  return item;
}

export { graphForScope } from './model.js';

export function scopeSnapshotKey(data) {
  readableMap(data);
  const pin = data.manifest?.pinned?.sha256;
  if (!data.mapHash || !pin || data.mode !== 'current') {
    throw new Error('层级视野缺少固定事实版本');
  }
  return JSON.stringify([data.mode, data.mapHash, batchIdentity(data.manifest)]);
}

function validSelection(data, id, root, resolveIdentity) {
  if (!id) return null;
  const split = id.indexOf(':');
  return split > 0 && resolveIdentity(data, id, root) ? id : null;
}

function cleanFrame(data, frame, resolveIdentity) {
  const scopeRoot = frame.scopeRoot ?? null;
  scopeModule(data, scopeRoot);
  const c = frame.camera;
  const camera = c && [c.x, c.y, c.k].every(Number.isFinite) && c.k > 0 ? { x: c.x, y: c.y, k: c.k } : null;
  return { snapshotKey: scopeSnapshotKey(data), scopeRoot,
    selectedId: validSelection(data, frame.selectedId, scopeRoot, resolveIdentity), camera,
    filters: structuredClone(frame.filters || {}), returnFocusId: validSelection(data, frame.returnFocusId, scopeRoot, resolveIdentity) };
}

/** Current-batch, per-root memory. Every restore rechecks identities, including hidden views. */
export function createScopeNavigation({ resolveIdentity = (data, id) => { const split = id.indexOf(':'); return semanticNode(data, id.slice(0, split), id.slice(split + 1)); } } = {}) {
  const batches = new Map();
  function bucket(data) {
    const identity = scopeSnapshotKey(data);
    if (!batches.has(identity)) { batches.clear(); batches.set(identity, new Map()); }
    const views = batches.get(identity);
    for (const [root, frame] of views) {
      if (root != null && !data.modules.some(m => m.id === root)) views.delete(root);
      else views.set(root, cleanFrame(data, frame, resolveIdentity));
    }
    return views;
  }
  function save(data, frame) {
    if (frame.snapshotKey && frame.snapshotKey !== scopeSnapshotKey(data)) throw new Error('不能把另一事实版本的视野写入本批');
    const saved = cleanFrame(data, frame, resolveIdentity);
    bucket(data).set(saved.scopeRoot, saved);
    return structuredClone(saved);
  }
  function restore(data, root = null) {
    scopeModule(data, root);
    return cleanFrame(data, bucket(data).get(root) || { scopeRoot: root }, resolveIdentity);
  }
  function enter(data, root, leaving) {
    scopeModule(data, root); // Failed navigation must not destroy the current view.
    if (leaving) save(data, leaving);
    return restore(data, root);
  }
  function parent(data, leaving) {
    const root = scopeModule(data, leaving.scopeRoot);
    return enter(data, root?.parent || null, leaving);
  }
  return { save, restore, enter, parent, clear: () => batches.clear() };
}
