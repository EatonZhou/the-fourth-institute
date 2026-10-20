export const PERMISSION_RULES='权限系统随基础代码保留。permission_selection 单独选择真实权限方案模板及该方案自己的实例；instance=true 时其模板及必要结构必须一起带出。只在发行副本删除指向未带出主体或资源的权限节点、表项及关联，保留有效对象的权限语义，不将失效引用替换成更宽授权。源权限模板、实例与生效状态不改；首次管理员沿用既有初始化规则。范围说明原样保留供执行团队核实，不能把自然语言当成已经解析的表项或节点。';
export function normalizePermissions(value){return {schemes:(value?.schemes||[]).map(v=>({id:String(v.id),template:v.template===true||v.instance===true,instance:v.instance===true})),description:typeof value?.description==='string'?value.description:'',configured:value?.configured===true};}
export function permissionEditor(root,{sdk,signal,value,onChange}){
  const state=normalizePermissions(value);let catalogue=[],error='',phase='idle',pending=Promise.resolve(),generation=0;
  const el=(tag,text)=>{const n=document.createElement(tag);if(text)n.textContent=text;if(tag==='button')n.className='portalButton';if(tag==='textarea'){n.className='portalField';n.rows=3;}if(tag==='p')n.className='packagingHint';return n;};
  const box=el('fieldset'),body=el('div'),status=el('p');box.className='packagingPermissions';status.setAttribute('role','status');
  box.append(el('legend','权限模板与实例'),el('p','每个方案拥有自己的实例。带出实例同时包含模板；不改变源站点权限或启用状态。'),body,status);
  const notes=el('textarea');notes.setAttribute('aria-label','权限内容范围说明');notes.value=state.description;notes.addEventListener('input',()=>{state.description=notes.value;onChange();});const scopeLabel=el('label','权限内容范围说明');scopeLabel.className='packagingScope';scopeLabel.append(notes);box.append(scopeLabel);
  function draw(){if(signal.aborted)return;body.replaceChildren();
    const selection=state.schemes.length?'已保留所选权限内容。':state.configured?'已选择不附带现有权限内容。':'尚未选择权限带出范围。';
    status.textContent=[phase==='loading'?'正在读取权限方案与实例…':phase==='failed'?error:phase==='ready'?(catalogue.length?'权限方案读取完成。':'权限方案读取成功，列表为空。'):'尚未读取权限方案。',selection].join(' ');

    for(const item of [...catalogue,...state.schemes.filter(v=>!catalogue.some(c=>c.id===v.id))]){
      const group=el('fieldset');group.append(el('legend',`${item.title||item.id} · ${item.id}`));
      const selected=state.schemes.find(v=>v.id===item.id)||{id:item.id,template:false,instance:false};
      for(const [key,title]of [['template','带出模板／方案'],['instance','带出此方案实例']]){
        const label=el('label'),input=el('input');input.type='checkbox';input.checked=selected[key];input.disabled=phase!=='ready'||(key==='template'?!!selected.instance||!item.templateReadable:!item.templateReadable||!item.instanceReadable);
        input.addEventListener('change',()=>{selected[key]=input.checked;if(key==='instance'&&input.checked)selected.template=true;state.schemes=state.schemes.filter(v=>v.id!==item.id);if(selected.template||selected.instance)state.schemes.push(selected);state.configured=true;draw();onChange();});
        label.className='packagingChoice';label.append(input,document.createTextNode(title));group.append(label);
      }
      if(phase==='ready'){
        group.append(el('p',item.templateMessage||'此方案未在本次目录中返回，原选择保留，等待重新核实。'));
        if(item.instanceMessage)group.append(el('p',item.instanceMessage));
      }
      if(selected.instance)group.append(el('small','实例需要，模板已包含。'));
      if(!item.templateReadable&&state.schemes.some(v=>v.id===item.id)){const remove=el('button','移除此保留选择');remove.type='button';remove.addEventListener('click',()=>{state.schemes=state.schemes.filter(v=>v.id!==item.id);draw();onChange();});group.append(remove);}
      body.append(group);
    }
  }
  async function inspect(item){
    const found={id:item.scheme_id,title:item.title,templateReadable:false,instanceReadable:false};
    const [template,entries]=await Promise.allSettled([
      sdk.hatg('GetPermissionScheme',{scheme_id:item.scheme_id}),
      sdk.hatg('ListPermissionEntries',{scheme_id:item.scheme_id,r:'@portal',r_scope:'subtree',temporary:false,limit:1}),
    ]);
    try{
      if(template.status==='rejected')throw template.reason;
      const scheme=template.value;
      if(scheme.scheme_id!==item.scheme_id||!scheme.rules||typeof scheme.rules!=='object')throw Error('方案身份或规则内容不完整');
      found.templateReadable=true;found.templateMessage=`${item.builtin?'内置':'权限'}模板／方案已读取。`;
    }catch(e){found.templateMessage=`权限模板／方案读取失败：${e.message||String(e)}`;}
    try{
      if(entries.status==='rejected')throw entries.reason;
      const instance=entries.value;
      if(instance.scheme_id!==item.scheme_id||!Array.isArray(instance.entries)||!Number.isInteger(instance.total)||instance.total<0)throw Error('实例身份或表项结果不完整');
      found.instanceReadable=true;
      found.instanceMessage=instance.total===0?'未读到权限实例表项：当前为空或尚未生成实例；这不表示模板不可读取。':`此方案实例共有 ${instance.total} 条权限表项。`;
    }catch(e){found.instanceMessage=`权限实例读取失败：${e.message||String(e)}`;}
    return found;
  }
  async function load(){
    const token=++generation;phase='loading';error='';draw();
    try{
      const result=await sdk.hatg('ListPermissionSchemes',{});
      if(!Array.isArray(result.schemes))throw Error('方案目录结果不完整');
      const next=await Promise.all(result.schemes.map(inspect));
      if(signal.aborted||token!==generation)return;
      catalogue=next;phase='ready';
    }catch(e){if(signal.aborted||token!==generation)return;phase='failed';error=`权限方案读取失败：${e.message||String(e)}。已有选择保留，可重新读取。`;}
    draw();
  }
  const retry=el('button','重新读取权限方案');retry.type='button';retry.addEventListener('click',()=>{pending=load();});
  const none=el('button','不附带现有权限内容');none.type='button';none.addEventListener('click',()=>{state.schemes=[];state.configured=true;draw();onChange();});box.append(none,retry);root.append(box);draw();
  return{read:()=>normalizePermissions(state),ready:()=>pending=load(),settle:()=>pending,async validate(){await load();if(error&&state.schemes.length)throw Error(error);for(const item of state.schemes){const found=catalogue.find(v=>v.id===item.id);if(!found?.templateReadable||item.instance&&!found.instanceReadable)throw Error(`所选权限方案或实例不可读取：${item.id}`);}}};
}
