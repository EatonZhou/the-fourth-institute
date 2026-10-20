/** Domain authors choose the default; model array order is never a choice. */
export function resolveDomainEntry(model, route, entries = {}) {
  if (!route || route.mechanismId || !['composition', 'protocol'].includes(route.kind)) return route;
  const object = model.objects.find(item => item.id === route.objectId);
  if (!object || (route.kind === 'composition' && object.child_ids.length)) return route;
  const declared = entries[object.id];
  if (!declared) return route;
  // Ordered alternatives are explicit author decisions for different fixed
  // batches. Never choose from the fact model's mechanism array order.
  const chosen = Array.isArray(declared)
    ? declared.find(id => object.mechanism_ids.includes(id)) : declared;
  if (!object.mechanism_ids.includes(chosen) || !model.mechanisms.some(item => item.id === chosen)) {
    throw Object.assign(new Error('对象的默认机制不属于本批已授权内容'), { code: 'invalid_domain_entry' });
  }
  return { ...route, kind: route.kind === 'protocol' ? 'protocol' : 'mechanism', mechanismId: chosen };
}

/** Modes use this object's explicit references; shared mechanisms retain their owner. */
export function resolveDomainModes(model, object, choices = {}) {
  const ids = choices[object.id];
  if (!ids) return undefined;
  const modes = ids.flatMap(id => {
    if (!object.mechanism_ids.includes(id)) return [];
    const mechanism = model.mechanisms.find(item => item.id === id);
    if (!mechanism) {
      throw Object.assign(new Error('对象模式不属于本批已授权内容'), { code: 'invalid_domain_mode' });
    }
    return [mechanism];
  });
  return modes.length > 1 ? modes : undefined;
}
