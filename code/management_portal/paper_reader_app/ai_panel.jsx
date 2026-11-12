import React, {useEffect, useRef, useState} from 'react';
import {ResearchAssistantPicker} from './research_assistant_picker.jsx';
import '../static/paper_research_controls.js';
const t=(value,args)=>window.PortalI18n.t(value,args);
const friendlyError=cause=>window.PortalPaperResearchControls.errorSource(cause);

export function AIPanel({discussion, catalog, onSelected, principal, bridge, isCurrent, close}) {
  const root = useRef(null), panel = useRef(null);
  const saveIdentity = useRef(null), activeDiscussion = useRef(discussion), pendingSelection = useRef(discussion.text||'');
  useEffect(()=>{activeDiscussion.current=discussion;pendingSelection.current=discussion.text||'';if(discussion.text)panel.current?.workspace.setDraftText(discussion.text);},[discussion]);
  const [titleElement,setTitleElement]=useState(null);
  const [error,setError] = useState(''), [answers,setAnswers] = useState([]), [draft,setDraft] = useState('');
  const [restoring,setRestoring] = useState(false);
  const [saving,setSaving] = useState(false), [saved,setSaved] = useState(false);
  useEffect(()=>{
    let disposed=false;
    const current=()=>!disposed&&isCurrent();
    const prepare=async(readOnly, message='Reader 单篇讨论')=>{
      try {
      if(!current())throw Error(window.PortalI18n.t('论文或账号已变化'));
      const response=await fetch('/chat-workspace/api/prepare',{method:'POST',credentials:'same-origin',
        headers:{'Content-Type':'application/json'},body:JSON.stringify({surface:'paper',workspace_key:discussion.key,
          message,read_only:readOnly,context:{document:activeDiscussion.current.document,expected_principal:principal,selection:pendingSelection.current}})});
      const value=await response.json();if(!response.ok)throw Object.assign(new Error(),{status:response.status,code:value.error?.code});
      if(!current())throw Error(window.PortalI18n.t('论文或账号已变化'));return value;
      }catch(error){if(current())setError(friendlyError(error));throw error;}
    };
    try {
      if(!window.PortalChatWorkspace?.panelPackage?.create)throw Error('Research Chat 组件未加载');
      const chat=window.PortalChatWorkspace.panelPackage.create({root:root.current,id:'reader-ai-'+discussion.key,
        autoLoad:false,draftOwner:()=>principal,title:t('Chat Workspace')+' - '+(catalog?.selected?.title||catalog?.selected_legacy?.title||t('Research Chat')),resolveResident:()=>prepare(true),resolveBeforeSend:(_target,payload)=>prepare(false,payload.text||payload.plainText),
        initialContext:(_target,detail)=>detail.initial_context || {},
        submissionContext:async()=> (await prepare(false)).initial_context || {},
        onDetail:detail=>{if(!current())return;setAnswers((detail.messages||[]).filter(m=>window.PortalChatWorkspace.messageRole(m)==='agent')
          .map(m=>Array.isArray(m.content_parts)?m.content_parts.filter(p=>p.type==='text').map(p=>p.text||'').join('\n'):String(m.content||'')).filter(Boolean));},
        emit:event=>{if(current()&&event==='binding-unavailable')window.dispatchEvent(new CustomEvent('portal:paper-research-refresh'));},
        onError:e=>{if(current())setError(friendlyError(e));}});
      const send=chat.send.bind(chat);chat.send=(...args)=>{
        if(!current())throw Error(window.PortalI18n.t('论文或账号已变化'));
        const target=chat.target().resident_id, text=chat.workspace.draftText(), attachments=chat.workspace.attachments();
        return send(...args).then(value=>{pendingSelection.current='';return value;}).catch(error=>{
          if(current()&&chat.target().resident_id===target){
            if(text&&!chat.workspace.draftText())chat.workspace.setDraftText(text,attachments);
            setError(friendlyError(error));
          }
          throw error;
        });
      };
      panel.current=chat;setTitleElement(chat.workspace.els.title);
      chat.openSurface({workspace_key:discussion.key,paper_id:discussion.document.paper_id}).then(()=>{
        if(current()&&discussion.text)chat.workspace.setDraftText(discussion.text);
      }).catch(e=>{if(current())setError(friendlyError(e));});
    } catch(e){setError(e.message);}
    const watch=setInterval(()=>{if(!disposed&&!isCurrent()){disposed=true;panel.current?.destroy();panel.current=null;setAnswers([]);setDraft('');setError('论文或账号已变化，请重新打开讨论');}},250);
    return()=>{disposed=true;clearInterval(watch);panel.current?.destroy();panel.current=null;};
  },[discussion.key]);
  useEffect(()=>{
    const renamed=event=>{if(isCurrent()&&event.detail?.principal===principal&&panel.current?.target()?.resident_id===event.detail.resident_id)panel.current.workspace.setTitle(event.detail.title);};
    window.addEventListener('portal:paper-research-renamed',renamed);
    return()=>window.removeEventListener('portal:paper-research-renamed',renamed);
  },[principal]);
  const save=async()=>{
    if(!isCurrent()||!draft.trim())return;setSaving(true);setError('');
    if(saveIdentity.current?.text!==draft)saveIdentity.current={text:draft,id:crypto.randomUUID().replaceAll('-','').slice(0,8).toUpperCase()};
    try{await bridge().saveAnswer(activeDiscussion.current,draft,saveIdentity.current.id);if(isCurrent())setSaved(true);}catch(e){if(isCurrent())setError(e.message);}finally{setSaving(false);}
  };
  return <aside className="paperAIPanel" aria-label={t('论文 AI 侧栏')}>
    {discussion.position&&<button onClick={()=>{if(isCurrent())bridge().locateSelection(discussion);}}>{t('引用选区 · 第 {p0} 页',{p0:discussion.position.pageIndex+1})}</button>}
    {error&&<div role="alert"><p>{t(error)}</p><button className="portalButton is-compact" disabled={restoring} onClick={async()=>{
      if(!isCurrent()||!panel.current)return;
      const capturedDraft=panel.current.workspace.draftText(),capturedAttachments=panel.current.workspace.attachments();
      setRestoring(true);
      try{await panel.current.openSurface({workspace_key:discussion.key,paper_id:activeDiscussion.current.document.paper_id},{force:true});if(isCurrent())setError('');}
      catch(e){if(isCurrent())setError(friendlyError(e));}finally{if(isCurrent()&&capturedDraft&&!panel.current?.workspace.draftText())panel.current?.workspace.setDraftText(capturedDraft,capturedAttachments);setRestoring(false);}
    }}>{window.PortalI18n.t('重新连接讨论')}</button></div>}
    <div ref={root} className="paperAIChat" />
    <ResearchAssistantPicker catalog={catalog} principal={principal} isCurrent={isCurrent} onSelected={onSelected} titleElement={titleElement} attached />
    <details><summary>{t('确认保存回答为便签')}</summary>
      <select aria-label={t('选择 AI 回答')} defaultValue="" onChange={e=>{setDraft(e.target.value);setSaved(false);}}><option value="">{t('选择回答')}</option>{answers.map((text,i)=><option key={i} value={text}>{t('回答 {p0}',{p0:i+1})}</option>)}</select>
      <textarea aria-label={t('便签预览')} value={draft} onChange={e=>{setDraft(e.target.value);setSaved(false);}} />
      <button disabled={!discussion.position||!draft.trim()||saving||saved} onClick={save}>{t(saved?'便签已保存':'确认保存便签')}</button>
      {!discussion.position&&<small>{t('请从原文选区开启讨论以确定便签位置。')}</small>}
    </details>
  </aside>;
}
