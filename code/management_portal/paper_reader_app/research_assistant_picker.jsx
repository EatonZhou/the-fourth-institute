import React, {useEffect,useRef,useState} from 'react';
import '../static/paper_research_controls.js';
const t=value=>window.PortalI18n.t(value);

// Uses the same authenticated Paper catalog/select/create contract as Paper Chat.
export function ResearchAssistantPicker({catalog,principal,isCurrent,onSelected}) {
  const [pending,setPending]=useState(false),[error,setError]=useState('');
  const host=useRef(null),controls=useRef(null),latest=useRef(null);
  const [missing,setMissing]=useState([]);
  const run=async(endpoint,args)=>{
    if(pending||!isCurrent())return;
    setPending(true);setError('');
    try{
      const response=await fetch('/paper/api/research/chats/'+endpoint,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({...args,expected_principal:principal})});
      const slot=await response.json();
      if(!response.ok)throw Object.assign(new Error(),{status:response.status,code:slot.error?.code});
      if(!isCurrent())return;
      window.dispatchEvent(new CustomEvent('portal:paper-research-selected',{detail:{principal,slot}}));
      window.dispatchEvent(new CustomEvent('portal:paper-research-refresh'));
      await onSelected();
    }catch(cause){if(isCurrent()){
      setError(window.PortalPaperResearchControls.errorSource(cause));
      if(cause.status===404&&args.slot_id)setMissing(values=>[...values,args.slot_id]);
    }}
    finally{setPending(false);}
  };
  latest.current={catalog,pending,run};
  useEffect(()=>{
    const widget=window.PortalPaperResearchControls.create();controls.current=widget;
    host.current.append(widget.root);
    widget.select.onchange=()=>{
      const slot=latest.current.catalog?.slots?.find(row=>row.slot_id===widget.select.value);
      if(slot&&window.PortalPaperResearchControls.usable(slot))void latest.current.run(slot.state==='unbound'?'retry':'select',{slot_id:slot.slot_id});
    };
    widget.create.onclick=()=>{
      const title=window.prompt(t('研究助手名称'));
      if(title?.trim())void latest.current.run('create',{title:title.trim(),operation_id:crypto.randomUUID()});
    };
    return()=>{widget.destroy();controls.current=null;};
  },[]);
  useEffect(()=>{
    const widget=controls.current;if(!widget)return;
    widget.select.replaceChildren(new Option(t('请选择或新建研究助手'),''));
    for(const slot of catalog?.slots||[])if(window.PortalPaperResearchControls.usable(slot)&&!missing.includes(slot.slot_id))
      widget.select.append(new Option(slot.title||t('不可用研究助手'),slot.slot_id));
    widget.select.value='';widget.select.disabled=pending;widget.create.disabled=pending;
  });
  return <section className="paperReaderAssistantPicker" aria-label={t('持续研究助手')}>
    <p>{t('选择或新建持续研究助手，切换论文时保持对话。')}</p>
    <div ref={host}/>
    {error&&<p role="alert">{t(error)}</p>}
  </section>;
}
