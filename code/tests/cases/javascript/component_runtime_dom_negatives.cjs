'use strict';

const assert = require('node:assert/strict');
const { domContract } = require('./component_runtime_dom.cjs');
const probes = {
  canvas_disabled: {
    kind: 'event_source',
    before: '.property("disabled", item => Boolean(item.control.busy || item.control.disabled))',
    after: '.property("disabled", item => Boolean(item.control.busy))',
    failure: /data-manual-event-trigger.*disabled property follows runtime/,
  },
  node_dom: {
    kind: 'effect', before: 'this.innerHTML = content;', after: 'this.textContent = content;',
    failure: /D3 renders real paused node badge/,
  },
};
async function main() {
  const name = process.argv[2], probe = probes[name];
  assert.ok(probe, 'select one DOM fault probe');
  let replacements = 0;
  await assert.rejects(domContract(probe.kind, (sourceName, source) => {
    if (sourceName !== 'lineage_graph_cards.js') return source;
    assert.equal(source.split(probe.before).length - 1, 1, 'exact production DOM write');
    replacements++;
    return source.replace(probe.before, probe.after);
  }), error => {
    assert.match(error.message, probe.failure);
    console.log(JSON.stringify({ result: 'EXPECTED_FAILURE', probe: name, kind: probe.kind,
      source: 'lineage_graph_cards.js', before: probe.before, after: probe.after,
      assertion: error.message, stack: error.stack }));
    return true;
  });
  assert.equal(replacements, 1);
}
main().catch(error => { console.error(error); process.exitCode = 1; });
