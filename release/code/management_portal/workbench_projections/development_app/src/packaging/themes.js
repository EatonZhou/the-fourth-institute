export const normalizeThemes=value=>Array.isArray(value)?[...new Set(value.filter(id=>typeof id==='string'&&id.length))].sort():null;
export const THEME_RULES='themes 表示发行版带出的主题 ID，与当前用户外观设置独立，不改变操作用户当前主题。null 表示未设置，不能解释为不带主题或全带定制主题；数组为空表示用户明确不附带额外主题。所选主题及所需样式、素材和切换支持随发行提供；自动模式所需基础样式由正常基础支持处理，不要求用户拆分依赖。';

export function themeEditor(root,{sdk,signal,value,onChange}) {
  let selected=normalizeThemes(value),catalogue=null,error='';
  const el=(tag,text='',cls='')=>{const node=document.createElement(tag);node.textContent=text;if(cls)node.className=cls;return node;};
  const group=el('fieldset','','packagingThemes'),body=el('div'),status=el('p','','packagingHint');status.setAttribute('role','status');
  group.append(el('legend','带出主题'),el('p','选择发行版提供的主题，不改变你当前的外观。','packagingHint'),body,status);
  root.append(group);group.addEventListener('input',event=>event.stopPropagation());
  const change=next=>{selected=normalizeThemes(next);draw();onChange();};
  const button=(title,fn)=>{const node=el('button',title,'portalButton');node.type='button';node.addEventListener('click',fn);return node;};
  function draw(){
    body.replaceChildren();
    const options=new Map((catalogue||[]).map(v=>[v.id,v.label]));
    for(const id of selected||[])if(!options.has(id))options.set(id,`${id}（当前不可用，待核对）`);
    for(const [id,title] of options){
      const label=el('label','','packagingChoice'),check=el('input');check.type='checkbox';check.value=id;check.checked=!!selected?.includes(id);
      check.addEventListener('change',()=>change(check.checked?[...(selected||[]),id]:(selected||[]).filter(v=>v!==id)));
      label.append(check,document.createTextNode(title));body.append(label);
    }
    status.textContent=error|| (selected===null?'未设置带出主题；请按需选择。':selected.length?'已选择带出主题；自动模式所需基础样式随基础代码保留。':'已明确不附带额外主题；自动模式所需基础样式仍保留。');
  }
  async function load(){
    try{
      const result=await sdk.context();if(signal.aborted)return;
      if(!Array.isArray(result.themes)||!result.themes.length||result.themes.some(v=>typeof v.id!=='string'||typeof v.label!=='string'))throw Error('宿主未提供可用主题列表');
      catalogue=result.themes;error='';
    }catch(e){if(signal.aborted)return;catalogue=null;error=`主题列表加载失败：${e.message}`;}
    draw();
  }
  group.append(button('不附带额外主题',()=>change([])),button('设为未设置',()=>change(null)),button('重新加载主题',()=>void load()));draw();
  return {read:()=>normalizeThemes(selected),ready:load,validate:async()=>{
    await load();if(selected?.length&&(!catalogue||selected.some(id=>!catalogue.some(v=>v.id===id))))throw Error('所选主题当前不可用，请重新加载或调整选择');
  }};
}
