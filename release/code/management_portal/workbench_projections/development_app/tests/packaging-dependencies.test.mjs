import test from 'node:test';
import assert from 'node:assert/strict';
import {normalizeChecklist,requestFor,CATEGORIES} from '../src/packaging/model.js';
import {featureClosure,releaseFeatures} from '../src/packaging/feature-dependencies.js';
import {templateSelection,templateRecord} from '../src/packaging/society-templates.js';
import {readInstance} from '../src/packaging/selection-model.js';
const draft={version:'deps',source:'Dev',destination:'/out'};
test('dependency closure handles transitive, shared, cyclic requirements without selecting instances',()=>{
 const graph={a:['b'],b:['c'],c:['b'],d:['c']};
 const state=featureClosure(['a','d'],graph);
 assert.deepEqual(state.included,['a','b','c','d']);assert.deepEqual(state.required_by.c,['b','d']);
 assert.deepEqual(featureClosure(['b'],graph).included,['b','c']);
 assert.deepEqual(featureClosure([],graph).included,[]);
});
test('TAU and native view require framework while retaining manual intent across restore/removal',()=>{
 const value=normalizeChecklist({...draft,categories:{tests:['TAU 功能']}});
 assert.ok(value.effective_features.included.includes('test_framework'));
 assert.deepEqual(value.categories.tests,['TAU 功能']);
 assert.deepEqual(normalizeChecklist(value),value);
 assert.ok(!releaseFeatures({...value,categories:{tests:[]}}).included.includes('test_framework'));
 assert.ok(releaseFeatures({...value,categories:{tests:['测试框架']}}).included.includes('test_framework'));
 assert.ok(releaseFeatures({...draft,test_view:true}).included.includes('test_framework'));
});
test('mandatory notices cannot be excluded and do not remain a UI choice',()=>{
 assert.ok(!CATEGORIES.flat(2).includes('许可证与第三方声明'));
 const value=normalizeChecklist({...draft,categories:{docs:['许可证与第三方声明','README']}});
 assert.deepEqual(value.categories.docs,['README']);
 assert.ok(value.legacy_mandatory_content.includes('许可证与第三方声明'));
 assert.match(requestFor('wb',value).text,/LICENSE.*NOTICE/);
 assert.deepEqual(normalizeChecklist(value),value);
});
test('device instances and raw scope become excluded evidence, never project dependencies',async()=>{
 const value=normalizeChecklist({...draft,selection_version:3,resource_types:[{type:'device',instances:['device_111111111111'],description:'  全部机器\n ',ownership:{device_111111111111:{project_ids:['Machines'],verified:true}}}]});
 assert.deepEqual(value.resource_types[0].instances,[]);assert.equal(value.resource_types[0].description,'');
 assert.deepEqual(value.projects,[]);assert.equal(value.excluded_device_selections[0].description,'  全部机器\n ');
 assert.deepEqual(normalizeChecklist(value),value);
 let called=false;await assert.rejects(readInstance({hatg(){called=true;}},'device','device_111111111111'),/Device/);assert.equal(called,false);
 assert.match(requestFor('wb',value).text,/Repo.*代码/);
});
test('templates save real identities and support requirements without generated data or applying',()=>{
 const template={template_id:'template_111111111111',title:'real',kind:'structure',version:3,body:{components:[{kind:'effect',config:{script:'DeliverMessage {}'}}]}};
 const record=templateRecord(template);assert.equal(record.id,template.template_id);assert.equal(record.version,3);
 assert.ok(record.required_support.includes('effect_script'));
 const value=normalizeChecklist({...draft,society_templates:{configured:true,selected:[record],description:'  用户原文\n '}});
 assert.deepEqual(value.society_templates,templateSelection(value.society_templates));
 assert.equal(value.society_templates.description,'  用户原文\n ');
 assert.ok(value.effective_features.included.includes('society_templates'));
 assert.match(requestFor('wb',value).text,/不执行或施加模板/);
 assert.equal(templateSelection().configured,false);
});
