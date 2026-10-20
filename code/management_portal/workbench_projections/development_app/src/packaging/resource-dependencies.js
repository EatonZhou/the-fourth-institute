// Read-only projection of the distribution service's resolved relationships.
// Never put these objects into manual selection arrays: that would retain them
// after the selecting resource type was removed.
export function resourceDependencyView(root){
  const disclosure=document.createElement('details');disclosure.className='packagingResourceDependencies';
  const summary=document.createElement('summary');summary.textContent='资源关联与必要结构';
  const box=document.createElement('div');disclosure.append(summary,box);root.append(disclosure);
  const node=(tag,text)=>{const n=document.createElement(tag);n.textContent=text;return n;};
  const reasons=item=>(item.required_by||[]).join('、')||'服务端必要结构';
  const flag=value=>value==null?'未设置':value?'带出':'不带出';
  function update(value,snapshot={},message=''){
    box.replaceChildren();
    if(message){box.append(node('p',message));return;}
    if(!['tfi.release-resource-dependencies.v1','tfi.release-resource-dependencies.v2','tfi.release-resource-dependencies.v3'].includes(value?.schema)){
      box.append(node('p','服务端尚未提供资源依赖结果，关联范围待核实。'));return;
    }
    box.append(node('p','以下关联由服务端核实，自动包含不扩大业务实例范围，也不会启动工作。对应 Lineage 已自动列入 Society，可直接配置经历和文件范围。'));
    const group=(title,items,format)=>{
      if(!items?.length)return;
      box.append(node('h4',title));const list=document.createElement('ul');
      for(const item of items)list.append(node('li',format(item)));box.append(list);
    };
    group('资源类型支持',value.types,id=>`${id} · 原因：${(value.type_reasons?.[id]||[]).join('、')||'服务端必要支持'}`);
    group('页面',value.pages,item=>`${item.id} · 提供方：${item.provider||'未提供'}`);
    group('关联投影',value.projections,item=>`${item.id} · 修订 ${item.revision} · 原因：${reasons(item)} · 绑定：${(item.bindings||[]).map(b=>`${b.alias} → ${b.lineage_id}`).join('、')}`);
    group('Lineage',value.lineages,item=>{
      const explicit=snapshot.lineages?.find(v=>v.id===item.id);
      const options=explicit||snapshot.resource_lineage_options?.[item.id]||{history:false,workspace:{own_outputs:false,extra_bindings:'exclude'}};
      const workspace=options.workspace||{};
      return `${item.id} · 原因：${reasons(item)} · ${explicit?'显式选择的范围':'自动包含的基础结构'} · 经历：${flag(options.history)} · 自有成果：${flag(workspace.own_outputs)} · 额外绑定：${({exclude:'不带出',all:'全部',partial:'部分',unset:'未设置'})[workspace.extra_bindings]||'未设置'}${workspace.extra_scope?` · 范围原文：${workspace.extra_scope}`:''}${workspace.exclusions?` · 排除原文：${workspace.exclusions}`:''}`;
    });
    group('Project',value.projects,item=>`${item.id} · 原因：${reasons(item)}`);
    group('关联依据',value.edges,item=>`${item.from} → ${item.to} · ${item.relation}${item.revision!=null?` · 修订 ${item.revision}`:''}${item.application?` · ${item.application}`:''}`);
    group('待核实缺口',value.gaps,item=>`${item.id||item.application||'关联'} · ${item.reason}`);
    if(value.valid===false)box.append(node('p','关联尚有缺口，可保存配置；补齐前不能生成可安装工程。'));
    else if(!(value.types?.length||value.lineages?.length||value.projections?.length))box.append(node('p','当前没有资源关联结构。'));
  }
  update(null,{},'等待资源候选读取完成后核实关联…');return {update};
}
