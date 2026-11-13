// Exercise the real plan renderer with an isolated root; no browser or backend.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const escapeHtml = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const html = (strings,...values) => strings.reduce((s,v,i)=>s+v+(values[i]??''),'');
const selected = [{case_id:'test_selected',case_title:'正常用例',runner:{label:'pytest'},implementation:{entrypoint:'test.py'}}];
const plan = {selected, executable:true, request:{select:{case_ids:['test_selected']}},excluded:[
  {case_id:'test_a', title:'预览 <计划>',reason:'no-compatible-implementation'},
  {case_id:'test_b', title:'TAU 流程',reason:'tau-campaign-only'},
  {case_id:'test_c', case_title:'兼容名称',reason:'future-<reason>'},
  {case_id:'test_d', description:'旧描述',error:'旧<错误>'},
  {case_id:'test_e'},
]};
const requests=[]; const root={innerHTML:'',querySelector:()=>null};
const T={escapeHtml, draft:()=>({caseIds:['test_selected','test_a'],implementation:{},options:{}}),
  loadOverview:async()=>{}, overview:()=>({}), canOperate:()=>true,
  api:async(url,options)=>{requests.push([url,JSON.parse(options.body)]);return plan;},
  caseHref:id=>'/case/'+id, crumbsHtml:()=>'',riskLabel:x=>x,riskTone:()=>'',chip:escapeHtml,
  href:()=>'/test',catalogHref:()=>'/cases'};
const window={PortalTest:T,PortalI18n:{t:x=>x,text:html,html}};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../../../management_portal/test_framework/page/test_page_plan.js'),'utf8'),{window});
(async()=>{
 const original=JSON.stringify(plan);const controller=window.PortalTestPlan.create({root});await controller.load();
 const rows=[...root.innerHTML.matchAll(/<li data-plan-excluded>([\s\S]*?)<\/li>/g)].map(m=>m[1]);
 assert.equal(rows.length,5);
 assert.match(rows[0],/>预览 &lt;计划&gt;<\/a>/);
 assert.match(rows[0],/当前选择的实现、执行方式或运行环境没有可用的匹配组合/);
 assert.match(rows[1],/需要作为 TAU Campaign 运行/);
 assert.match(rows[0],/<code[^>]*>no-compatible-implementation<\/code>/);
 assert.match(rows[1],/<code[^>]*>tau-campaign-only<\/code>/);
 assert.match(rows[2],/>兼容名称<\/a>/);
 assert.match(rows[2],/该用例不符合当前运行计划的要求/);
 assert.match(rows[2],/future-&lt;reason&gt;/);
 assert.match(rows[3],/>旧描述<\/a>/);
 assert.match(rows[3],/旧&lt;错误&gt;/);
 assert.match(rows[4],/>test_e<\/a>/);
 assert.match(rows[4],/当前选择不包含它的实现/);
 assert.equal(JSON.stringify(plan),original,'preview rendering leaves plan and selections intact');
 assert.deepEqual(requests,[['/test/api/preview',{select:{case_ids:['test_selected','test_a']},implementation:{}}]]);
 controller.destroy();console.log('plan exclusion display contracts verified');
})().catch(e=>{console.error(e);process.exitCode=1;});
