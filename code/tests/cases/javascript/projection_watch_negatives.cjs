'use strict';

// Explicit fault probes run one at a time. Only the in-memory evaluation copy
// changes; production files remain untouched. Each probe must be rejected by
// the same assertions as its independently selectable positive contract.
const assert = require('node:assert/strict');
const { contracts } = require('./projection_watch_contracts.cjs');
const probes = {
  deduplication: {
    contract: 'batches', source: 'projection_host.js',
    before: 'const unique = [...new Set(refs)];', after: 'const unique = refs;',
    failure: /Expected values to be strictly deep-equal/,
  },
  stale_disposer: {
    contract: 'replacement', source: 'projection_sdk.js',
    before: 'if (revision !== activeWatchRevision) return;', after: '',
    failure: /stale disposer does not issue a request/,
  },
  late_error: {
    contract: 'inflight', source: 'projection_host.js',
    before: 'if (isCurrent()) post(record, "change", { refs: [], error: error.message, code: error.code || "projection.request_failed" });',
    after: 'post(record, "change", { refs: [], error: error.message, code: error.code || "projection.request_failed" });',
    failure: /hidden panel receives no in-flight result/,
  },
  cancellation: {
    contract: 'disposal', source: 'projection_sdk.js',
    before: 'if (error.code === "projection.cancelled" && stopping < watchRevision) return;',
    after: 'if (error.code === "projection.cancelled") return;',
    failure: /Missing expected rejection/,
  },
  cleanup_failure: {
    contract: 'disposal', source: 'development-lifecycle.bundle.js',
    before: 'pending = Promise.resolve(result);', after: 'pending = Promise.resolve(result).catch(() => {});',
    failure: /Missing expected rejection/,
  },
};

async function main() {
  const name = process.argv[2], probe = probes[name];
  assert.ok(probe, 'select one explicit fault probe');
  let replaced = 0;
  const sourceTransform = (sourceName, source) => {
    if (sourceName !== probe.source) return source;
    assert.equal(source.split(probe.before).length - 1, 1, 'fault must match exactly once');
    replaced++;
    return source.replace(probe.before, probe.after);
  };
  await assert.rejects(contracts[probe.contract]({ sourceTransform }), error => {
    assert.match(error.message, probe.failure, 'probe fails for the intended assertion');
    console.log(JSON.stringify({ result: 'EXPECTED_FAILURE', probe: name,
      source: probe.source, before: probe.before, after: probe.after,
      assertion: error.message, stack: error.stack }));
    return true;
  });
  assert.equal(replaced, 1, 'one complete production module was faulted in memory');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
