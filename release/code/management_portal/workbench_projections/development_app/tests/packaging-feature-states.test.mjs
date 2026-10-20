import test from 'node:test';
import assert from 'node:assert/strict';
import {featureStateFields} from '../src/packaging/feature-states.js';
import {normalizeChecklist} from '../src/packaging/model.js';
import {releaseFeatures} from '../src/packaging/feature-dependencies.js';
test('legacy absence remains distinct; canonical false and null survive checklist save',()=>{
 assert.deepEqual(featureStateFields({optional_features:{tau:true}}),{});
 const raw={feature_states:{tau:{included:false,default_enabled:true},telepus:{included:true,default_enabled:null}},feature_states_origin:'legacy_selection_fields'};
 const saved=normalizeChecklist(raw,{validate:false});
 assert.deepEqual(saved.feature_states,raw.feature_states);assert.equal(saved.feature_states_origin,raw.feature_states_origin);
 saved.feature_states.tau.included=true;assert.equal(raw.feature_states.tau.included,false);
});
test('canonical intent overrides stale aliases while dependencies remain included',()=>{
 const f=releaseFeatures({feature_states:{tau:{included:false,default_enabled:true},harness_recording:{included:true,default_enabled:false}},test_view:true,categories:{tests:['TAU 功能']},optional_features:{resident_semantic_search:true}});
 assert(!f.included.includes('test_view'));assert(!f.included.includes('tau'));assert(!f.included.includes('resident_semantic_search'));assert(f.included.includes('harness_recording'));
 assert(releaseFeatures({feature_states:{tau:{included:true,default_enabled:false}}}).included.includes('test_framework'));
});

test('native mapping survives saved checklist round trips without selecting objects',()=>{
 const mapping={deployment:{pulse:{lineage_id:'lineage_111111111111',event_source_ids:['source_one']}}};
 const raw={native_switch_site_config:mapping};
 const saved=normalizeChecklist(raw,{validate:false});
 assert.deepEqual(saved.native_switch_site_config,mapping);
 assert.deepEqual(normalizeChecklist(JSON.parse(JSON.stringify(saved)),{validate:false}).native_switch_site_config,mapping);
 assert.deepEqual(saved.lineages,[]);assert.deepEqual(saved.manual_lineages,[]);
 saved.native_switch_site_config.deployment.pulse.event_source_ids.push('source_two');
 assert.deepEqual(mapping.deployment.pulse.event_source_ids,['source_one']);
 assert.equal(Object.hasOwn(normalizeChecklist({}, {validate:false}),'native_switch_site_config'),false);
});
