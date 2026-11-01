'use strict';

// A complete, bounded enumeration through the caller's authorized read API.
// The caller persists the returned pages, including incomplete observations.
// It is not a database snapshot: concurrent membership changes stay visible.
async function collectResourceBaseline(instruction, filters = {}, { maxPages = 100 } = {}) {
  if (typeof instruction !== 'function' || !Number.isInteger(maxPages) || maxPages < 1 || maxPages > 1000)
    throw new TypeError('Use a read instruction callback and bounded page count');
  if (Object.keys(filters).some(key => !['q', 'resource_type', 'project_id', 'status'].includes(key)))
    throw new TypeError('Baseline filters must describe the same resource population on every page');
  const pages = [], ids = new Set(), cursors = new Set();
  let cursor;
  for (let index = 0; index < maxPages; index++) {
    const page = await instruction('SearchResources', { ...filters, limit: 500, ...(cursor ? { cursor } : {}) });
    const items = Array.isArray(page.items) ? page.items : [];
    const pageIds = items.map(item => item.resource_id || item.id);
    const valid = Array.isArray(page.items) && pageIds.every(id => typeof id === 'string' && /^[a-z][a-z0-9_]*_[0-9a-f]{12}$/.test(id));
    pages.push({ observed_at: new Date().toISOString(), ids: pageIds, total: page.total,
      has_next: !!page.next_cursor });
    if (!valid || !Number.isInteger(page.total) || page.total < 0)
      return { complete: false, reason: 'invalid_page', pages, ids: [...ids] };
    for (const id of pageIds) ids.add(id);
    if (!page.next_cursor) {
      const complete = pages.every(p => p.total === page.total) && ids.size === page.total;
      return { complete, reason: complete ? null : 'membership_changed_or_incomplete', pages, ids: [...ids].sort() };
    }
    if (cursors.has(page.next_cursor)) return { complete: false, reason: 'cursor_cycle', pages, ids: [...ids] };
    cursors.add(page.next_cursor);
    cursor = page.next_cursor;
  }
  return { complete: false, reason: 'page_budget_exhausted', pages, ids: [...ids] };
}

module.exports = { collectResourceBaseline };
