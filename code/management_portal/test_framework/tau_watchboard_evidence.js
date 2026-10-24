'use strict';

// Pure readers of original public responses. These helpers never submit an
// instruction, refresh a page, infer acceptance, or replace a UI-ready check.
function watchboardGraph(lineage, {residentId, previousComponentIds = []} = {}) {
  if (!/^resident_[a-f0-9]+$/.test(residentId || '')) throw Error('Exact Resident identity required');
  if (!Array.isArray(lineage?.program?.components) || !Array.isArray(lineage?.program?.wires))
    throw Error('GetLineage lineage.program components and wires are required');
  if (!Array.isArray(previousComponentIds) || previousComponentIds.some(id => typeof id !== 'string' || !id))
    throw Error('Previous installation component IDs must come from the original graph');
  const {components, wires} = lineage.program;
  for (const collection of [components, wires]) {
    if (collection.some(item => typeof item?.id !== 'string' || !item.id)
      || new Set(collection.map(item => item.id)).size !== collection.length)
      throw Error('Graph entries need distinct exact IDs');
  }
  if (wires.some(wire => typeof wire.source !== 'string' || typeof wire.target !== 'string'))
    throw Error('Graph wires require exact source and target IDs');
  const prefix = `watchboard.${residentId}.`;
  const owned = components.filter(item => typeof item.client_key === 'string' && item.client_key.startsWith(prefix));
  const ids = new Set([...previousComponentIds, ...owned.map(item => item.id)]);
  return {
    resident_id: residentId,
    components: owned.map(({id, client_key}) => ({id, client_key})),
    wires: wires.filter(item => ids.has(item.source) || ids.has(item.target))
      .map(({id, source, target}) => ({id, source, target})),
    tracked_component_ids: [...ids],
    total_component_count: components.length,
    total_wire_count: wires.length,
  };
}

function createScriptOutputCapture({lineageId, scriptRunId, stream = 'stdout'} = {}) {
  if (typeof lineageId !== 'string' || !lineageId || typeof scriptRunId !== 'string' || !scriptRunId
    || !['stdout', 'stderr'].includes(stream)) throw Error('Exact script output request identity required');
  const decoder = new TextDecoder('utf-8');
  let offset = 0, text = '', closed = false, truncated = false;
  return Object.freeze({
    append({request, result, terminal = false}) {
      if (closed) throw Error('Terminal output was already captured');
      if (request?.lineage_id !== lineageId || request?.script_run_id !== scriptRunId || request?.stream !== stream)
        throw Error('Output request identity changed');
      if (typeof terminal !== 'boolean' || !result || typeof result.eof !== 'boolean'
        || typeof result.truncated !== 'boolean' || typeof result.base64 !== 'string'
        || ![request.offset, result.offset, result.next_offset, result.size].every(Number.isSafeInteger))
        throw Error('ReadScriptRunOutput response fields are missing or invalid');
      const bytes = Buffer.from(result.base64, 'base64');
      if (bytes.toString('base64') !== result.base64) throw Error('Invalid output base64');
      if (request.offset !== offset || result.offset !== offset || result.size < 0
        || result.next_offset !== offset + bytes.length || result.next_offset > result.size
        || result.eof !== (result.next_offset === result.size)) throw Error('Output byte offsets are inconsistent');
      closed = terminal && result.eof;
      text += decoder.decode(bytes, {stream: !closed});
      offset = result.next_offset;
      truncated ||= result.truncated;
      return {lineage_id: lineageId, script_run_id: scriptRunId, stream, text,
        next_offset: offset, size: result.size, eof: result.eof, terminal, truncated,
        fully_read: closed && !truncated};
    },
  });
}

module.exports = {watchboardGraph, createScriptOutputCapture};
