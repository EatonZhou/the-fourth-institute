// Audited against portal_management.js execution settings. These are release
// selections, never values to pass to UpdateExecutionSettings or runtime APIs.
export const OPTIONAL_FEATURES = [
  {id:'permission_system',title:'权限系统',mandatory:true,detail:'基础代码固定包含。开启后按生效权限方案检查；关闭后已登录用户和 Resident 不再受方案权限限制，个人 AMA 和管理员管理规则仍保留。仅设置新安装默认值，升级保留目标站点已有选择。'},
  {id:'resident_semantic_search',title:'Resident 向量／语义搜索',detail:'本地向量检索及模型安装支持；不影响普通关键词搜索。'},
  {id:'wall_scheduling',title:'WALL 定时任务',detail:'定时分析、全面回归及全量发布；手动 WALL 操作独立选择。'},
  {id:'model_request_timing',title:'模型请求时间记录',detail:'记录可以可靠取得的请求时间；不改变模型调用路径。'},
  {id:'harness_recording',title:'Harness 记录',detail:'Harness 策略、结果与模型调用记录，以及保留天数配置和清理支持；不复制源站点保留天数值或历史记录。'},
];
export const OPTIONAL_FEATURE_RULES = 'optional_features 表示发行包含的可选能力，不是源站点运行开关。true 表示手动包含，false 表示未手动包含（被其他功能依赖时仍自动包含），null 或缺项表示尚未设置，执行前需核对，不能默认全带或全不带。TAU 与 Telepus 分别沿用 categories.tests 中的“TAU 功能”和 categories.tools 中的“Telepus”，同一能力只有一份选择。发行不包含与已包含但运行时关闭须分别处理：前者不显示对应页面、导航、菜单、设置项、操作按钮及安装提示，后者可保留启用入口。共享页面只保留已带出功能的部分，不向缺失功能发起请求；前后端、测试与文档按同一实际发行范围处理。未带出 Resident 向量／语义搜索时，不显示语义搜索开关、模型状态和一键安装入口，普通关键词搜索继续保留。这些调整只在发行副本执行，不隐藏当前完整 Portal 的设置、不操作源站点启停，也不复制源账号、凭据、本机路径或历史运行。功能依赖按实际代码核实，不因选择定时任务而默认带出全部 WALL 项目或团队。';

export function normalizeOptionalFeatures(value) {
  if(!value || typeof value!=='object' || Array.isArray(value))return null;
  return Object.fromEntries(Object.entries(value).map(([key,selected])=>[key,typeof selected==='boolean'?selected:null]));
}

export function optionalFeatureEditor(root,{value,aliases=[]}) {
  let state=normalizeOptionalFeatures(value);
  const box=document.createElement('fieldset');box.className='packagingOptionalFeatures';
  const legend=document.createElement('legend');legend.textContent='可选功能';box.append(legend);
  const hint=document.createElement('p');hint.className='packagingHint';hint.textContent='选择发行版包含的能力，不改变当前站点的启停。未设置的项目会保留为待核对。';box.append(hint);
  for(const feature of OPTIONAL_FEATURES){
    const row=document.createElement('div'),label=document.createElement('label'),input=document.createElement('input');
    label.className='packagingChoice';input.type='checkbox';input.value=feature.id;
    const selected=state?.[feature.id];input.checked=selected===true;input.indeterminate=selected==null;
    const status=document.createElement('small');status.className='packagingHint';
    const render=()=>{status.textContent=feature.mandatory?feature.detail:`${state?.[feature.id]==null?'未设置':state[feature.id]?'带出':'不带出'} · ${feature.detail}`;};render();
    input.addEventListener('input',()=>{state={...(state||{}),[feature.id]:input.checked};input.indeterminate=false;render();});
    label.append(input,document.createTextNode(feature.title));row.append(label,status);box.append(row);
  }
  for(const alias of aliases)box.append(alias);
  const known=new Set(OPTIONAL_FEATURES.map(v=>v.id));
  const unknown=Object.keys(state||{}).filter(id=>!known.has(id));
  if(unknown.length){const note=document.createElement('p');note.className='packagingHint';note.textContent=`此前的功能选择待核对：${unknown.map(id=>`${id}=${state[id]}`).join('、')}`;box.append(note);}
  root.append(box);
  return {read:()=>structuredClone(state)};
}
