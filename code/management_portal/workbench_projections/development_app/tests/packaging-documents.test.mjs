import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {parseDocumentIndex,normalizeDocuments} from '../src/packaging/documents.js';
import {normalizeChecklist,requestFor} from '../src/packaging/model.js';
import {migrateSelection} from '../src/packaging/selection-model.js';

test('real source indexes retain domain headings and only actual indexed entries',()=>{
 const root=new URL('../../../../',import.meta.url);
 const spec=readFileSync(new URL('SPEC.md',root),'utf8'),improve=readFileSync(new URL('IMPROVE.md',root),'utf8');
 const rows=parseDocumentIndex(spec,'SPEC.md','repo_111111111111/file/SPEC.md');
 assert.ok(rows.length>50);assert.equal(new Set(rows.map(v=>v.path[0])).size,11);
 assert.ok(rows.some(v=>v.id==='decision_15aab18c0ab3'));assert.ok(rows.some(v=>v.path.length===2));
 const plans=parseDocumentIndex(improve,'IMPROVE.md','repo_111111111111/file/IMPROVE.md');
 assert.ok(plans.length>0);assert.ok(plans.every(v=>v.id.startsWith('plan_')||v.id.includes('/.wall/plans/')));
 assert.ok(!plans.some(v=>v.id==='decision_9c258743f91e'));
});
test('document types and Projects are dependencies, not recursive instances or manual choices',()=>{
 const value={version:'deps',source:'Release',destination:'/out',document_indexes:{'SPEC.md':{enabled:true,entries:['decision_111111111111'],requirements:[{type:'decision',project_ids:['Docs'],entries:['decision_111111111111']}]},'IMPROVE.md':{enabled:true,entries:['plan_111111111111'],requirements:[{type:'plan',project_ids:['Docs'],entries:['plan_111111111111']}]}}};
 const saved=normalizeChecklist(value);
 assert.deepEqual(saved.projects,[{id:'Docs'}]);assert.deepEqual(saved.lineages,[]);
 assert.deepEqual(saved.resource_types.map(v=>[v.type,v.manual,v.instances]),[['decision',false,[]],['plan',false,[]]]);
 assert.deepEqual(normalizeChecklist(saved),saved);
 const manual=migrateSelection(saved);assert.deepEqual(manual.resource_types,[]);
 const removed=normalizeChecklist({...saved,document_indexes:{}});assert.deepEqual(removed.resource_types,[]);assert.deepEqual(removed.projects,[]);
 const kept=normalizeChecklist({...saved,manual_projects:['Docs'],resource_types:[{type:'decision',instances:[],description:'手动范围'}],document_indexes:{}});
 assert.deepEqual(kept.projects,[{id:'Docs'}]);assert.equal(kept.resource_types[0].description,'手动范围');
 const request=requestFor('w',saved);assert.match(request.text,/二次关联不递归带出/);assert.match(request.text,/元数据 links 和正文资源链接/);assert.match(request.text,/未选目标去链接或改为普通文字/);assert.match(request.text,/不自动启用规范描述的业务功能/);
});
test('index lexer ignores prose and code and retains current module and Plan references',()=>{
 const text='# Index\n\nIntro `decision_111111111111`\n\n## Area\n### Sub\n\n| Name | ID |\n|---|---|\n| A | `decision_222222222222` |\n\n```\n| fake | decision_333333333333 |\n```';
 assert.deepEqual(parseDocumentIndex(text,'SPEC.md','repo_111111111111/file/SPEC.md'),[{id:'decision_222222222222',title:'A',path:['Area','Sub']}]);
});
test('legacy index choice is visible but not expanded; task limits full selection and preserves text',()=>{
 const draft={version:'docs',source:'Release',destination:'/out',categories:{docs:['规范索引','自定义规范说明']},attachment_descriptions:{docs:' 保留原说明 '}};
 const saved=normalizeChecklist(draft);assert.equal(saved.document_indexes['SPEC.md'].pending_legacy,true);
 assert.equal(saved.document_indexes['SPEC.md'].enabled,false);assert.deepEqual(saved.document_indexes['SPEC.md'].entries,[]);
 assert.deepEqual(saved.categories.docs,['自定义规范说明']);assert.equal(saved.attachment_descriptions.docs,' 保留原说明 ');
 const documents=normalizeDocuments({'SPEC.md':{enabled:true,address:'repo_111111111111/file/SPEC.md',entries:['decision_222222222222'],description:'  跨模块范围\n原文  '}});
 const value=normalizeChecklist({...saved,document_indexes:documents});assert.deepEqual(normalizeChecklist(value),value);
 const request=requestFor('workbench_docs',value);assert.match(request.text,/未发布功能的对应测试用例/);assert.match(request.text,/全选与模块内全选也受此范围限制/);assert.match(request.text,/不静默带回功能/);assert.match(request.text,/不沿用已排除条目的源缓存/);assert.match(request.text,/不执行计划/);
 assert.deepEqual(JSON.parse(request.text.split('发布清单：\n')[1]).document_indexes,documents);
});
