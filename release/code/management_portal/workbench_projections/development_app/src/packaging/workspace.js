const text=value=>typeof value==='string'?value:'';
export function workspaceOptions(value={}) {
  return {own_outputs:typeof value?.own_outputs==='boolean'?value.own_outputs:null,
    extra_bindings:['exclude','all','partial'].includes(value?.extra_bindings)?value.extra_bindings:'unset',
    extra_scope:text(value?.extra_scope),exclusions:text(value?.exclusions)};
}

export const WORKSPACE_RULES='Workspace 基础支持固定包含，不提供独立 Workspace 带出选择。普通 Lineage 默认包含全部 Resident；Portal System 按 system_lineage 选择功能所需成员，lineages[].workspace 统一应用到该 Lineage 实际带出的所有 Resident。资源关联自动带入的 Lineage 使用 resource_lineage_options[Lineage ID] 保存经历和文件范围；该偏好本身不构成带出理由，仅在当前依赖仍存在时应用，显式 lineages 选项优先。Resident Folder 的 resident.md、diary.md、workspaces.md 等基础文件、自有 workspace/ 目录及必要登记和绑定必带，基础文件保留或重新生成，与实际发行数据一致。own_outputs 表示自有工作成果是否带出，null 为未设置；extra_bindings 的 unset/exclude/all/partial 分别表示未设置、不带额外绑定、带出额外绑定的全部内容、按 extra_scope 原文选择部分内容。未设置不代表全带，需由执行团队与用户核对。exclusions 为排除 Workspace 的 ID、路径或文字说明，仅针对可选绑定及成果，不得移除必需基础文件或自有目录。本地缓存和源站点凭据不带。发行数据库移除未带出的额外绑定，安装路径映射后重新生成 workspaces.md，源数据、绑定与文件不变。共享 Workspace 按同一资源去重，不重复复制。Resident 上下文与经历仅由 history 决定，与文件选择独立。pending_workspace_selections 是旧 Workspace 选择的待核对原文，不是已确认文件范围；不得自动扩展、解析为未经核实的 ID 或独立带出，执行团队需查询理解并核对。';

// One policy per selected Lineage, including those brought in by projections.
export function workspaceEditor(value,onChange) {
  const options=workspaceOptions(value);
  const el=(tag,content='',cls='')=>{const node=document.createElement(tag);node.textContent=content;if(cls)node.className=cls;return node;};
  const group=el('fieldset','','packagingWorkspace');
  group.append(el('legend','工作区与文件'),el('p','统一应用到此 Lineage 实际带出的全部 Resident。基础文件、自有 workspace/ 目录及必要登记和绑定固定包含；本地缓存与源凭据不带。文件选择与“保留经历”独立。','packagingHint'));
  const select=(label,key,choices)=>{
    const holder=el('label',label),input=el('select','','portalField');
    input.setAttribute('aria-label',label);
    for(const [value,title] of choices){const option=el('option',title);option.value=value;input.append(option);}
    input.value=key==='own_outputs'?(options[key]===null?'unset':String(options[key])):options[key];
    input.addEventListener('change',()=>{options[key]=key==='own_outputs'?(input.value==='unset'?null:input.value==='true'):input.value;onChange({...options});updateScope();});
    holder.append(input);group.append(holder);
  };
  select('自有工作成果','own_outputs',[['unset','未设置'],['true','带出'],['false','不带出']]);
  select('额外绑定 Workspace','extra_bindings',[['unset','未设置'],['exclude','不带出'],['all','带出全部内容'],['partial','带出部分内容']]);
  const area=(label,key)=>{const holder=el('label',label),input=el('textarea','','portalField');input.rows=2;input.value=options[key];input.addEventListener('input',()=>{options[key]=input.value;onChange({...options});});holder.append(input);group.append(holder);return holder;};
  const scope=area('额外绑定的部分内容范围','extra_scope');
  const updateScope=()=>{scope.hidden=options.extra_bindings!=='partial'&&!options.extra_scope;};
  updateScope();
  area('排除 Workspace（ID、路径或说明）','exclusions');
  group.append(el('p','部分内容范围仅在选择“带出部分内容”时应用；切换选项会保留已填文字。范围与排除说明由团队核对，排除不影响基础文件与自有目录。','packagingHint'));
  return group;
}
