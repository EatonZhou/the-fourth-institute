import React, {useEffect,useRef,useState} from 'react';
import '../static/paper_research_controls.js';
const t=value=>window.PortalI18n.t(value);

// Uses the same authenticated Paper catalog/select/create contract as Paper Chat.
export function ResearchAssistantPicker({catalog,principal,isCurrent,onSelected,titleElement,attached=false}) {
  const [pending,setPending]=useState(false),[error,setError]=useState('');
  const host=useRef(null),controls=useRef(null),latest=useRef(null),busy=useRef(false);
  const [missing,setMissing]=useState([]);
  const run=async(endpoint,args,key)=>{
    if(busy.current||!isCurrent())return;
    busy.current=true;
    setPending(true);setError('');
    try{
      const response=await fetch('/paper/api/research/chats/'+endpoint,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({...args,expected_principal:principal})});
      const slot=await response.json();
      if(!response.ok)throw Object.assign(new Error(),{status:response.status,code:slot.error?.code});
      if(!isCurrent())return;
      window.dispatchEvent(new CustomEvent('portal:paper-research-selected',{detail:{principal,slot:args.workspace_key?null:slot,workspace_key:args.workspace_key||''}}));
      window.dispatchEvent(new CustomEvent('portal:paper-research-refresh'));
      await onSelected();
    }catch(cause){if(isCurrent()){
      setError(window.PortalPaperResearchControls.errorSource(cause));
      if(cause.status===404&&key)setMissing(values=>[...values,key]);
    }}
    finally{busy.current=false;setPending(false);}
  };
  const deleted=new Set((catalog?.deleted||[]).map(row=>row.workspace_key));
  const items=[...(catalog?.slots||[]).filter(window.PortalPaperResearchControls.usable).map(slot=>({id:'research:'+slot.slot_id,title:slot.title||t('不可用研究助手'),slot})),
    ...(catalog?.legacy||[]).map(slot=>({id:slot.workspace_key,title:slot.title,slot}))].filter(row=>!deleted.has(row.id)&&!missing.includes(row.id));
  const selected=catalog?.selected?'research:'+catalog.selected.slot_id:catalog?.selected_legacy?.workspace_key||'';
  const select=key=>{
    const item=items.find(row=>row.id===key);if(!item||key===selected)return;
    if(item.slot.slot_id)void run(item.slot.state==='unbound'?'retry':'select',{slot_id:item.slot.slot_id},key);
    else void run('select',{workspace_key:item.id},key);
  };
  const create=()=>{const title=window.prompt(t('研究助手名称'));if(title?.trim())void run('create',{title:title.trim(),operation_id:crypto.randomUUID()});};
  latest.current={items,selected,pending,select,create};
  useEffect(()=>{
    const target=attached?titleElement:host.current;if(!target)return;
    const widget=window.PortalPaperResearchControls.createTitleMenu({titleElement:target,
      onSelect:key=>latest.current.select(key),onCreate:()=>latest.current.create()});
    // Keep the menu in the Reader stacking/modal surface, not behind it.
    target.closest('dialog')?.append(widget.root);
    controls.current=widget;widget.update(latest.current);
    return()=>{widget.destroy();controls.current=null;};
  },[titleElement,attached]);
  useEffect(()=>{controls.current?.update({items,selected,pending});});
  if(attached)return error?<p role="alert">{t(error)}</p>:null;
  return <section className="paperReaderAssistantPicker" aria-label={t('持续研究助手')}>
    <h3 ref={host}>{items.find(row=>row.id===selected)?.title||t('持续研究助手')}</h3>
    {!items.length&&<><p>{t('选择或新建持续研究助手，切换论文时保持对话。')}</p><button disabled={pending} onClick={create}>{t('新建 AI')}</button></>}
    {error&&<p role="alert">{t(error)}</p>}
  </section>;
}
