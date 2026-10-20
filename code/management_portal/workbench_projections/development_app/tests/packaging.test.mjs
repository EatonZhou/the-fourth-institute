import test from 'node:test';
import assert from 'node:assert/strict';
import { normalizeChecklist, restoreChecklist, requestFor, requestState } from '../src/packaging/model.js';
import {workspaceOptions} from '../src/packaging/workspace.js';

test('testing selection migrates names without inventing a Workbench and preserves scope',()=>{
 const draft={version:'testing',source:'Release',destination:'/out',categories:{tests:['Test Workbench','公开版本测试用例','个人用例范围']},attachment_descriptions:{tests:'  仅验证安装\n保留范围  '}};
 const value=normalizeChecklist(draft);
 assert.deepEqual(value.categories.tests,['个人用例范围','测试用例']);
 assert.equal(value.test_view,false);assert.equal(value.pending_test_view,true);
 assert.deepEqual(value.projections,[]);assert.deepEqual(normalizeChecklist(value),value);
 assert.equal(value.attachment_descriptions.tests,draft.attachment_descriptions.tests);
 const resolved=normalizeChecklist({...value,test_view:true,pending_test_view:false,categories:{tests:['TAU 功能','测试用例']}});
 const request=requestFor('workbench_testing',resolved);
 assert.match(request.text,/测试目录、执行与结果记录/);
 assert.match(request.text,/TAU.*Agent.*实际使用/);
 assert.match(request.text,/测试账号.*执行槽位.*历史运行.*全部.*团队/);
 assert.match(request.text,/builtin=test.*Workbench/);
 assert.equal(JSON.parse(request.text.split('发布清单：\n')[1]).test_view,true);
 assert.equal(resolved.pending_test_view,false);
});

test('legacy base enumeration is removed without changing lineage data', () => {
  const value={version:'v-new',source:'Dev',destination:'/out',categories:{base:['Lineage','Society']},projects:[{id:'A'}],lineages:[{id:'lineage_111111111111',project_id:'A',history:true}]};
  const result=normalizeChecklist(value);
  assert.equal(Object.hasOwn(result.categories,'base'),false);
  assert.equal(result.notes,'');
  assert.deepEqual(result.lineages,value.lineages.map(v=>({...v,workspace:workspaceOptions()})));
  assert.match(requestFor('workbench_75b51c00005e',value).text,/Portal 基础代码随发行完整保留/);
});

test('base is implicit; legacy custom content stays in notes without becoming mandatory', () => {
  for (const base of [undefined, [], ['Home'], ['Home','原自定义服务']]) {
    const draft = {version:'v1',source:'Dev',destination:'/out',categories:{base},notes:'用户说明'};
    const normalized = normalizeChecklist(draft);
    assert.equal(Object.hasOwn(normalized.categories,'base'),false);
    assert.deepEqual(normalizeChecklist(normalized), normalized);
    assert.match(normalized.notes,/用户说明/);
    if(base?.includes('原自定义服务'))assert.match(normalized.notes,/待核对，未列为固定包含[\s\S]*原自定义服务/);
    const request = requestFor('workbench_75b51c00005e',draft);
    const payload = JSON.parse(request.text.split('发布清单：\n')[1]);
    assert.equal(Object.hasOwn(payload.categories,'base'),false);
    assert.match(request.text,/Portal 基础代码随发行完整保留/);
    assert.doesNotMatch(request.text,/基础功能固定包含：/);
  }
});

test('theme selection distinguishes unset, explicit empty and selected IDs, retaining original request text separately',()=>{
 const draft={version:'themes',source:'Release',destination:'/out'};
 assert.equal(normalizeChecklist(draft).themes,null);
 for(const themes of [null,[],['auto','moonwalker'],['future-theme']]){
   const value=normalizeChecklist({...draft,themes});assert.deepEqual(value.themes,themes);
   assert.deepEqual(normalizeChecklist(value),value);
   const request=requestFor('workbench_test',value);
   assert.deepEqual(JSON.parse(request.text.split('发布清单：\n')[1]).themes,themes);
   assert.match(request.text,/当前用户.*外观/);assert.match(request.text,/样式.*素材.*切换/);
 }
});

test('request retries retain identity and include the full reviewed selection', () => {
  const draft = { version: 'v1', source: '/release/source', destination: '/out', categories: { base: ['Home'] }, projects:[{id:'AI员工'}], lineages: [{ id: 'lineage_8794980b1bb7', project_id:'AI员工', history: true }] };
  const checklist = normalizeChecklist(draft);
  const first = requestFor('workbench_75b51c00005e', checklist, {runId:'run_first'});
  assert.deepEqual(requestFor('workbench_75b51c00005e', JSON.parse(JSON.stringify(checklist)), {runId:'run_first'}), first);
  assert.match(first.text, /TFI portal/);
  assert.match(first.text, /lineage_8794980b1bb7/);
  assert.match(first.text, /无源 Git 历史/);
  assert.match(first.text, /"history": true/);
  assert.match(first.text, /"project_id": "AI员工"/);
  assert.equal(checklist.projects_page,false);
  assert.deepEqual(checklist.resource_types,[]);
  assert.match(first.text,/未选中时仍须保留 Project 后端资源、相关服务及项目归属能力/);
  assert.match(first.text,/不受 Projects 页面是否选中影响/);
  assert.equal(requestState({state:'completed'}), '团队已回复，请核对结果');
  assert.equal(requestState({accepted:true}), '已提交');
  assert.throws(() => normalizeChecklist({...draft, version:''}), /版本名称/);
});

test('project deselection excludes its lineages, without selecting any other content', () => {
  const value = {version:'v1',source:'/src',destination:'/out',projects:[{id:'A'}],lineages:[
    {id:'lineage_111111111111',project_id:'A',history:true},
    {id:'lineage_222222222222',project_id:'B',history:true},
  ]};
  const result=normalizeChecklist(value);
  assert.deepEqual(result.projects,[{id:'A'}]);
  assert.deepEqual(result.lineages,[{...value.lineages[0],workspace:workspaceOptions()}]);
  assert.deepEqual(normalizeChecklist({...value,projects:[]}).lineages,[]);
});

test('legacy drafts recover only verified ownership; unavailable selections stay visible for review', async () => {
  const old={lineages:[{id:'lineage_111111111111',history:true},{id:'lineage_222222222222',history:false}]};
  const resolve=async id=>{if(id.endsWith('222222222222'))throw Error('unavailable');return {resource:{resource_id:id,resource_type:'lineage',attributes:{project_ids:['A']}}};};
  const project=async id=>({project:{resource_id:id,resource_type:'project'}});
  const result=await restoreChecklist(old,resolve,project);
  assert.deepEqual(result.projects,[{id:'A'}]);
  assert.equal(result.lineages[0].project_id,'A');assert.equal(result.lineages[0].history,true);
  assert.deepEqual(result.unresolved_lineages,old.lineages.slice(1).map(v=>({...v,workspace:workspaceOptions()})));
  assert.deepEqual(old.lineages[0],{id:'lineage_111111111111',history:true});
  assert.throws(()=>normalizeChecklist({...result,version:'v1',source:'/src',destination:'/out'}),/归属/);
  const cancelled=await restoreChecklist({...result,manual_projects:[],manual_lineages:[],projects:[],unresolved_lineages:[]},resolve,project);
  assert.deepEqual(cancelled.projects,[]);assert.deepEqual(cancelled.lineages,[]);
  const moved=await restoreChecklist({...result,manual_projects:['B'],projects:[{id:'B'}],lineages:[{id:'lineage_111111111111',project_id:'B',history:true}],unresolved_lineages:[]},resolve,project);
  assert.deepEqual(moved.projects,[{id:'B'}]);assert.deepEqual(moved.lineages,[]);
  assert.deepEqual(moved.unresolved_lineages,[{id:'lineage_111111111111',history:true,workspace:workspaceOptions()}]);
});
