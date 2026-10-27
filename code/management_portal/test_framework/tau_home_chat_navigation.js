'use strict';

// A Home link carries a one-shot open_chat intent. Read the location once;
// never wait for the original URL to reappear after the native page consumes it.
// This proves only the destination identity, not visible Chat/read readiness.
// The original action saves this result and readResidentChatReady separately,
// with its observer's page/frame/document identities, before asserting either.
async function readHomeChatNavigation(page, { sourceUrl, entryHref, residentId, lineageId }) {
  if (!/^resident_[a-z0-9]+$/.test(residentId || '') || !/^lineage_[a-z0-9]+$/.test(lineageId || '')
    || typeof sourceUrl !== 'string' || typeof entryHref !== 'string') {
    throw new TypeError('Expected original Home link, source URL and exact Resident/Lineage IDs');
  }
  const result = { resident_id: residentId, lineage_id: lineageId, entry: null, location: null,
    location_matches: false, error: null };
  let phase = 'original_link';
  try {
    const source = new URL(sourceUrl), entry = new URL(entryHref, source);
    const describe = url => ({ origin: url.origin, pathname: url.pathname,
      resident: url.searchParams.getAll('resident'), lineage: url.searchParams.getAll('lineage'),
      open_chat: url.searchParams.getAll('open_chat'), query_keys: [...url.searchParams.keys()],
      credentials_present: Boolean(url.username || url.password) });
    result.entry = describe(entry);
    const one = (values, expected) => values.length === 1 && values[0] === expected;
    const entryValid = /^https?:$/.test(source.protocol) && !source.username && !source.password
      && entry.origin === source.origin && !result.entry.credentials_present && entry.pathname === '/lineage'
      && one(result.entry.resident, residentId) && one(result.entry.lineage, lineageId) && one(result.entry.open_chat, '1');
    result.entry.valid = entryValid;
    phase = 'current_location';
    result.location = await page.evaluate(() => {
      const url = new URL(location.href);
      return { origin: url.origin, pathname: url.pathname,
        resident: url.searchParams.getAll('resident'), lineage: url.searchParams.getAll('lineage'),
        open_chat: url.searchParams.getAll('open_chat'), query_keys: [...url.searchParams.keys()],
        credentials_present: Boolean(url.username || url.password), ready_state: document.readyState };
    });
    const current = result.location;
    current.one_shot_state = !current.open_chat.length ? 'consumed'
      : one(current.open_chat, '1') ? 'present' : 'unexpected';
    result.location_matches = entryValid && current.origin === entry.origin && current.pathname === entry.pathname
      && !current.credentials_present && one(current.resident, residentId) && one(current.lineage, lineageId)
      && current.one_shot_state !== 'unexpected';
  } catch (error) {
    result.error = { phase, name: error.name, message: error.message, stack: error.stack };
  }
  return result;
}

module.exports = { readHomeChatNavigation };
