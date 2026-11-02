'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const root=process.env.TFI_TAU_CODE_ROOT || path.resolve(__dirname,'../../..');
const framework=path.join(root,'management_portal/test_framework');
const api=require(path.join(framework,'tau_evidence'));
const modules=['tau_native_controls','tau_chat_ready_evidence','tau_session_dom_evidence',
  'tau_session_prerequisites','tau_readiness_wait','tau_hatg_view_evidence','tau_hatg_fork_evidence',
  'tau_workbench_tabs','tau_trigger_path_evidence','tau_finite_instruction_evidence','tau_workspace_directory_evidence'];
const names=[];
for(const module of modules)for(const [name,implementation] of Object.entries(require(path.join(framework,module)))){
  assert.equal(typeof implementation,'function');assert(!names.includes(name),'one implementation owner for '+name);
  names.push(name);assert.equal(api[name],implementation,'public entry must forward the same function: '+name);
}
assert.deepEqual(Object.keys(api).sort(),names.sort());
assert(Object.isFrozen(api));
assert.equal(typeof api.readSessionLineageIdentity,'function');
assert.equal(typeof api.readResidentMenuSession,'function');
const prompt=fs.readFileSync(path.join(framework,'config/tau_runner_prompt.md'),'utf8');
const example=prompt.match(/```js\n(\/\/ tau-evidence-entry\n[\s\S]*?)\n```/);
assert.ok(example,'stable evidence entry is executable prompt code');
const sample=new Function('require','frameworkRoot',example[1]+'; return {readSessionLineageIdentity,readResidentMenuSession,readSessionMessageCards,readSessionGraphSnapshot,waitForEvidenceReady};')(require,framework);
for(const [name,implementation] of Object.entries(sample))assert.equal(implementation,api[name]);
console.log(JSON.stringify({status:'passed',public_functions:names.length,checks:['unambiguous callable exports before mutations','same authoritative function objects','immutable public entry'],production_calls:0}));
