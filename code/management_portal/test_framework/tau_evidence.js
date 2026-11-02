'use strict';

// Stable import surface for TAU's common UI evidence operations. Each module
// remains the sole owner of its semantics; this entry forwards function objects
// without wrapping calls, sampling, readiness inference or retries.
const owners = [
  require('./tau_native_controls'),
  require('./tau_chat_ready_evidence'),
  require('./tau_session_dom_evidence'),
  require('./tau_session_prerequisites'),
  require('./tau_readiness_wait'),
  require('./tau_hatg_view_evidence'),
  require('./tau_hatg_fork_evidence'),
  require('./tau_workbench_tabs'),
  require('./tau_trigger_path_evidence'),
  require('./tau_finite_instruction_evidence'),
];
const api = {};
for (const owner of owners) {
  for (const [name, implementation] of Object.entries(owner)) {
    if (Object.hasOwn(api, name) || typeof implementation !== 'function')
      throw new Error('Ambiguous or non-callable TAU evidence export');
    api[name] = implementation;
  }
}
module.exports = Object.freeze(api);
