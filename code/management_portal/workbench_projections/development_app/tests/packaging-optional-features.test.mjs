import test from 'node:test';
import assert from 'node:assert/strict';
import {normalizeChecklist,requestFor} from '../src/packaging/model.js';
import {OPTIONAL_FEATURES} from '../src/packaging/optional-features.js';
import {PORTAL_SYSTEM,flowCatalogue} from '../src/packaging/system-flows.js';
import {normalizePermissions} from '../src/packaging/permissions.js';
const draft={version:'optional',source:'Release',destination:'/out'};

test('release capability choices keep unset, excluded and included distinct through normalization',()=>{
  assert.equal(normalizeChecklist(draft).optional_features,null);
  assert.ok(OPTIONAL_FEATURES.some(v=>v.id==='resident_semantic_search'));
  for(const optional_features of [null,{}, {resident_semantic_search:false}, {resident_semantic_search:true,wall_scheduling:null}, {future_capability:true}]){
    const value=normalizeChecklist({...draft,optional_features});
    assert.deepEqual(value.optional_features,optional_features);
    assert.deepEqual(normalizeChecklist(JSON.parse(JSON.stringify(value))),value);
    const request=requestFor('workbench_test',value);
    assert.deepEqual(JSON.parse(request.text.split('发布清单：\n')[1]).optional_features,optional_features);
    assert.match(request.text,/普通关键词搜索继续保留/);
    assert.match(request.text,/开关、模型状态和一键安装入口/);
    assert.match(request.text,/不向缺失功能发起请求/);
    assert.match(request.text,/已包含但运行时关闭/);
    assert.match(request.text,/前后端、测试与文档/);
    assert.match(request.text,/不操作源站点启停/);
  }
});

test('existing TAU and Telepus choices and original submission remain separate and intact',()=>{
  const submission={key:'original',text:'original request',message_id:'original-message'};
  const saved={checklist:{...draft,categories:{tests:['TAU 功能'],tools:['Telepus']}},submission};
  const updated={...saved,checklist:normalizeChecklist({...saved.checklist,optional_features:{resident_semantic_search:false}})};
  assert.deepEqual(updated.submission,submission);
  assert.deepEqual(updated.checklist.categories.tests,['TAU 功能']);
  assert.deepEqual(updated.checklist.categories.tools,['Telepus']);
  assert.equal(Object.hasOwn(updated.checklist.optional_features,'tau'),false);
  assert.equal(Object.hasOwn(updated.checklist.optional_features,'telepus'),false);
});

test('actual system definitions expose groups, ungrouped members and boundary wires without inventing dependencies',()=>{
 const lineage={lineage_id:PORTAL_SYSTEM,project_id:'P',nodes:[{id:'resident_a',title:'Writer'},{id:'resident_b',title:'Other'}],program:{components:[{id:'effect_a',title:'Task',kind:'effect'}],wires:[{id:'wire_a',source:'effect_a',target:'resident_a'},{id:'wire_b',source:'resident_b',target:'effect_a'}]},function_blocks:[{id:'fbi_a',title:'Actual flow',elements:[{id:'resident_a'},{id:'effect_a'}]}]};
 const list=flowCatalogue(lineage);assert.equal(list.length,2);assert.deepEqual(list[0].elements,['resident_a','effect_a']);
 assert.deepEqual(list[0].wire_ids,['wire_a']);assert.equal(list[0].boundary_wires[0].id,'wire_b');assert.deepEqual(list[0].missing,[]);
 assert.equal(list[1].id,'resident_b');
 const value=normalizeChecklist({...draft,selection_version:3,manual_projects:[],manual_lineages:[],system_lineage:{project_id:'P',verified:true,revision:5,selected:[list[0]]}});
 assert.deepEqual(value.projects,[{id:'P'}]);assert.equal(value.lineages[0].id,PORTAL_SYSTEM);assert.equal(value.lineages[0].history,false);assert.equal(value.lineages[0].workspace.own_outputs,null);
 const edited=normalizeChecklist({...value,manual_projects:[],manual_lineages:[],lineages:[{...value.lineages[0],history:true,workspace:{own_outputs:false}}]});
 assert.equal(edited.lineages[0].history,true);assert.equal(edited.lineages[0].workspace.own_outputs,false);
 assert.deepEqual(edited.system_lineage.selected,value.system_lineage.selected);
 assert.match(requestFor('w',value).text,/其他 Lineage 仍默认带全部 Resident/);
 assert.match(requestFor('w',value).text,/跨选中范围的关联/);
});

test('permission instance requires its own template; old vague script selection remains raw scope',()=>{
 const value=normalizeChecklist({...draft,permission_selection:{configured:true,schemes:[{id:'real-scheme',instance:true}],description:'  保留实际权限\n不扩大授权  '},categories:{tools:['运维脚本']}});
 assert.deepEqual(value.permission_selection.schemes,[{id:'real-scheme',instance:true,template:true}]);
 assert.equal(value.permission_selection.description,'  保留实际权限\n不扩大授权  ');
 assert.deepEqual(normalizePermissions(),{schemes:[],description:'',configured:false});
 assert.deepEqual(value.categories.tools,['运维脚本']);
 const request=requestFor('w',value);assert.match(request.text,/删除指向未带出主体或资源的权限节点、表项及关联/);assert.match(request.text,/不将失效引用替换成更宽授权/);
 assert.match(request.text,/旧文字仅待核对/);assert.deepEqual(normalizeChecklist(value),value);
});
