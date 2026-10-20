const unique=items=>[...new Set(items)];
export function templateSelection(value){
  return {configured:value?.configured===true,selected:[...new Map((value?.selected||[]).filter(v=>v?.id).map(v=>[v.id,structuredClone(v)])).values()],description:typeof value?.description==='string'?value.description:''};
}
export function templateRecord(template){
  if(!/^template_[0-9a-f]{12}$/.test(template?.template_id||''))throw Error('模板身份无法核实');
  const support=['society_templates'];
  // These are structural requirements from the actual template body, not name
  // heuristics or copied instances. The facilities already belong to core.
  support.push(`template_kind:${template.kind}`);
  for(const component of template.body?.components||[]){
    support.push(`component:${component.kind}`);
    if(component.kind==='effect'&&component.config?.script)support.push('effect_script');
  }
  if(template.kind==='script')support.push('effect_script');
  return {id:template.template_id,title:template.title||template.template_id,kind:template.kind,version:template.version,required_support:unique(support).sort()};
}
export const TEMPLATE_RULES='society_templates 是 Society 模板选择，与权限模板不同；configured=false 表示尚未设置，不默认全带。按真实 template_id 读取所选定义、参数、正文和必要素材，包含其实际功能依赖；required_support 来自真实 kind/组件结构，任意脚本、动态参数和自然语言中的附加依赖由执行团队核实后自动补齐，不按标题猜测。仅携带模板，不执行或施加模板，不自动复制由源站点模板生成的实例或历史。未选模板不出现在发行模板列表，发行初始化不得补回未选的内置模板。已有 Lineage 的组件和流程不因其来源模板未选而删除。自然语言范围原样交执行团队解释，不伪装成已解析 ID；实际读取仍遵守权限。';
export function societyTemplateEditor(root,{sdk,signal,value,onChange}){
  let state=templateSelection(value),catalogue=[],loaded=false,error='',pending=Promise.resolve();
  const el=(tag,text='')=>{const node=document.createElement(tag);node.textContent=text;if(tag==='p'||tag==='small')node.className='packagingHint';if(tag==='button'){node.type='button';node.className='portalButton';}return node;};
  const box=el('fieldset'),body=el('div'),status=el('p');box.className='packagingSocietyTemplates';
  box.append(el('legend','Society 模板'),el('p','选择要提供给发行使用者的模板；不施加模板、不带出生成实例或历史。模板所需功能随定义包含。'),status);
  const all=el('button','全选可读模板'),none=el('button','不带模板'),retry=el('button','重新读取模板');box.append(all,none,retry,body);
  const scopeLabel=el('label','Society 模板范围说明'),scope=el('textarea');scope.className='portalField';scope.rows=3;scope.setAttribute('aria-label','Society 模板范围说明');scope.value=state.description;scopeLabel.append(scope);box.append(scopeLabel);root.append(box);
  function draw(){
    if(signal.aborted)return;body.replaceChildren();all.disabled=!loaded;none.disabled=!loaded;
    status.textContent=error||(!loaded?'正在读取模板…':!state.configured?'尚未设置模板范围。':`${state.selected.length} 项模板已选择。`);
    const records=new Map([...catalogue,...state.selected.filter(v=>!catalogue.some(c=>c.id===v.id))].map(v=>[v.id,v]));
    for(const record of records.values()){
      const row=el('label'),input=el('input');row.className='packagingChoice';input.type='checkbox';input.value=record.id;input.checked=state.selected.some(v=>v.id===record.id);
      input.disabled=!loaded&&!input.checked;
      input.addEventListener('change',()=>{state.configured=true;state.selected=state.selected.filter(v=>v.id!==record.id);if(input.checked)state.selected.push(record);draw();onChange();});
      row.append(input,document.createTextNode(`${record.title} · ${record.kind}`));body.append(row,el('small',`${record.id}${catalogue.some(v=>v.id===record.id)?'':' · 当前不可读取，提交前须核实或移除'}`));
    }
  }
  async function load(){
    try{
      const result=await sdk.hatg('ListTemplates',{});
      if(!Array.isArray(result.templates))throw Error('模板目录格式不可核实');
      catalogue=result.templates.map(templateRecord);loaded=true;error='';
    }catch(e){loaded=false;error=`模板读取失败：${e.message}`;}draw();
  }
  scope.addEventListener('input',()=>{state.description=scope.value;onChange();});
  all.addEventListener('click',()=>{state={...state,configured:true,selected:[...new Map([...state.selected,...catalogue].map(v=>[v.id,v])).values()]};draw();onChange();});
  none.addEventListener('click',()=>{state={...state,configured:true,selected:[]};draw();onChange();});
  retry.addEventListener('click',()=>{pending=load();});draw();
  return {read:()=>templateSelection(state),ready:()=>pending=load(),settle:()=>pending,
    async validate(){
      // Recheck selected definitions at the current actor's authority. No apply.
      for(const record of state.selected){
        const current=templateRecord((await sdk.hatg('GetTemplate',{template_id:record.id})).template);
        if(JSON.stringify(current)!==JSON.stringify(record)){state.selected=state.selected.map(v=>v.id===record.id?current:v);draw();onChange();throw Error('模板定义已变化，已更新选择，请核对后提交');}
      }
    }};
}
