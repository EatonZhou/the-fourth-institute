// Audited dependencies of the selections exposed by this workbench. Generic
// scripts/text still require source inspection by the executing Resident.
// test_framework/web.py and tau_runtime.py both use the test framework; the
// Society template kind handlers use the existing core application machinery.
export const FEATURE_DEPENDENCIES=Object.freeze({
  tau:['test_framework'],test_view:['test_framework'],test_framework:['portal_core'],
  society_templates:['portal_core'],
});
export const FEATURE_TITLES={tau:'TAU 功能',test_view:'Test 测试页面',test_framework:'测试框架',society_templates:'Society 模板'};
export function featureClosure(manual,graph=FEATURE_DEPENDENCIES){
  const included=new Set(manual),required_by={},queue=[...included];
  for(let i=0;i<queue.length;i++)for(const dependency of graph[queue[i]]||[]){
    const reasons=required_by[dependency]??=[];
    if(!reasons.includes(queue[i]))reasons.push(queue[i]);
    if(!included.has(dependency)){included.add(dependency);queue.push(dependency);}
  }
  return {included:[...included].sort(),required_by:Object.fromEntries(Object.entries(required_by).sort().map(([id,reasons])=>[id,reasons.sort()]))};
}
export function releaseFeatures(value){
  const authoritative=Object.hasOwn(value,'feature_states');
  const manual=['portal_core',...(authoritative?Object.entries(value.feature_states||{}).filter(([,s])=>s?.included===true):Object.entries(value.optional_features||{}).filter(([,s])=>s===true)).map(([id])=>id)];
  if(!authoritative&&value.categories?.tests?.includes('TAU 功能'))manual.push('tau');
  if(!authoritative&&value.categories?.tests?.includes('测试框架'))manual.push('test_framework');
  if(!authoritative&&value.categories?.tools?.includes('Telepus'))manual.push('telepus');
  if(!authoritative&&value.test_view)manual.push('test_view');
  if(value.society_templates?.selected?.length)manual.push('society_templates');
  return featureClosure(manual);
}
export const DEPENDENCY_RULES='功能依赖自动补齐：A 依赖 B 时，选择 A 即包含 B 及传递的必要功能依赖，不因 B 未手动勾选或标为不带出而排除，不要求用户再次选择或确认 B；补齐必要依赖不作为需要再次审核的范围变更。effective_features 是当前已核实依赖的实际范围；feature_states 存在时为唯一手动意图，旧 categories、optional_features、system_lineage 仅缺字段兼容。执行时按实际所选源码、模板定义及流程核实剩余必要依赖并自动补齐、记录原因，不以页面已知依赖表冒充完整代码依赖分析。功能、UI、测试和文档均按补齐后的实际范围处理。功能依赖不扩张实例范围，A 中未选对象不因 A 带出而自动带出；不沿知识链接递归带数据。所选外部资源实例须带出实际内容，Repo 连代码一起带，不能只复制登记信息或源链接。Device 当前只可带类型支持，不带任何具体实例；即使来自旧清单、ID、自然语言或依赖也不得带出。excluded_device_selections 仅为已排除原文说明，不是执行范围。源数据不改。';
