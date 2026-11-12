import React, {useEffect, useRef, useState} from 'react';
const t=value=>window.PortalI18n.t(value);

// Reader owns only its container and selection tools. Paper Chat owns the one
// workspace, assistant selection, title/subtitle, protocol and draft state.
export function PaperChatMount({active, chat, discussion, bridge, isCurrent}) {
  const host=useRef(null),saveIdentity=useRef(null);
  const [answers,setAnswers]=useState([]),[draft,setDraft]=useState('');
  const [saving,setSaving]=useState(false),[saved,setSaved]=useState(false),[error,setError]=useState('');
  useEffect(()=>{
    const node=host.current,dialog=node.closest('dialog');let release=null;
    const sync=()=>{
      if(active&&!dialog.classList.contains('is-embedded')&&isCurrent()){
        if(!release)try{release=chat.mount(node);}catch(e){setError(e.message);}
      }else{release?.();release=null;}
    };
    const observer=new MutationObserver(sync);observer.observe(dialog,{attributes:true,attributeFilter:['class']});sync();
    return()=>{observer.disconnect();release?.();};
  },[chat,active]);
  useEffect(()=>{setDraft('');setSaved(false);},[discussion]);
  const save=async()=>{
    if(!isCurrent()||!discussion?.position||!draft.trim())return;
    setSaving(true);setError('');
    if(saveIdentity.current?.text!==draft)saveIdentity.current={text:draft,id:crypto.randomUUID().replaceAll('-','').slice(0,8).toUpperCase()};
    try{await bridge().saveAnswer(discussion,draft,saveIdentity.current.id);if(isCurrent())setSaved(true);}
    catch(e){if(isCurrent())setError(e.message);}finally{if(isCurrent())setSaving(false);}
  };
  return <div className="paperReaderChatMount">
    <div ref={host} className="paperReaderChatHost" />
    {discussion?.position&&<div className="paperReaderSelectionTools">
      <button onClick={()=>{if(isCurrent())bridge().locateSelection(discussion);}}>{window.PortalI18n.t('引用选区 · 第 {p0} 页',{p0:discussion.position.pageIndex+1})}</button>
      <details onToggle={event=>{if(event.currentTarget.open)setAnswers(chat.answers());}}><summary>{t('确认保存回答为便签')}</summary>
        <select aria-label={t('选择 AI 回答')} value="" onChange={e=>{setDraft(e.target.value);setSaved(false);}}><option value="">{t('选择回答')}</option>{answers.map((text,i)=><option key={i} value={text}>{window.PortalI18n.t('回答 {p0}',{p0:i+1})}</option>)}</select>
        <textarea aria-label={t('便签预览')} value={draft} onChange={e=>{setDraft(e.target.value);setSaved(false);}} />
        <button disabled={!draft.trim()||saving||saved} onClick={save}>{t(saved?'便签已保存':'确认保存便签')}</button>
      </details>
    </div>}
    {error&&<div role="alert">{t(error)}</div>}
  </div>;
}
