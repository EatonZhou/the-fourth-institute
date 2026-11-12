import React, {useState} from 'react';
const t=value=>window.PortalI18n.t(value);

// Uses the same authenticated Paper catalog/select/create contract as Paper Chat.
export function ResearchAssistantPicker({catalog,principal,isCurrent,onSelected}) {
  const [pending,setPending]=useState(false),[error,setError]=useState('');
  const [title,setTitle]=useState('');
  const run=async(endpoint,args)=>{
    if(pending||!isCurrent())return;
    setPending(true);setError('');
    try{
      const response=await fetch('/paper/api/research/chats/'+endpoint,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({...args,expected_principal:principal})});
      const slot=await response.json();
      if(!response.ok)throw Error(slot.error?.message||slot.error||t('无法读取研究助手'));
      if(!isCurrent())return;
      window.dispatchEvent(new CustomEvent('portal:paper-research-selected',{detail:{principal,slot}}));
      window.dispatchEvent(new CustomEvent('portal:paper-research-refresh'));
      await onSelected();
    }catch(cause){if(isCurrent())setError(cause.message);}
    finally{setPending(false);}
  };
  return <section className="paperReaderAssistantPicker" aria-label={t('持续研究助手')}>
    <p>{t('选择或新建持续研究助手，切换论文时保持对话。')}</p>
    <select aria-label={t('选择研究助手')} disabled={pending} value="" onChange={event=>{
      const slot=catalog?.slots?.find(row=>row.slot_id===event.target.value);
      if(slot)void run(slot.state==='unbound'?'retry':'select',{slot_id:slot.slot_id});
    }}>
      <option value="">{t('选择研究助手')}</option>
      {(catalog?.slots||[]).map(slot=><option key={slot.slot_id} value={slot.slot_id}>{slot.title||slot.name||slot.slot_id}</option>)}
    </select>
    <form onSubmit={event=>{event.preventDefault();if(title.trim())void run('create',{title:title.trim(),operation_id:crypto.randomUUID()});}}>
      <label>{t('研究助手名称')}<input value={title} onChange={event=>setTitle(event.target.value)} disabled={pending}/></label>
      <button type="submit" className="portalButton is-compact" disabled={pending||!title.trim()}>{t('新建研究助手')}</button>
    </form>
    {error&&<p role="alert">{error}</p>}
  </section>;
}
