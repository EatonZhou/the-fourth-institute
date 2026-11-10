import React, {useEffect, useRef, useState} from 'react';

export function AIPanel({discussion, principal, bridge, isCurrent, close}) {
  const root = useRef(null), panel = useRef(null);
  const saveIdentity = useRef(null);
  const [error,setError] = useState(''), [answers,setAnswers] = useState([]), [draft,setDraft] = useState('');
  const [saving,setSaving] = useState(false), [saved,setSaved] = useState(false);
  useEffect(()=>{
    let disposed=false;
    const current=()=>!disposed&&isCurrent();
    const prepare=async(readOnly, message='Reader 单篇讨论')=>{
      if(!current())throw Error('论文或账号已变化');
      const response=await fetch('/chat-workspace/api/prepare',{method:'POST',credentials:'same-origin',
        headers:{'Content-Type':'application/json'},body:JSON.stringify({surface:'paper',workspace_key:discussion.key,
          message,read_only:readOnly,context:{document:discussion.document,expected_principal:principal,selection:discussion.text||''}})});
      const value=await response.json();if(!response.ok)throw Error(value.error||'无法准备讨论');
      if(!current())throw Error('论文或账号已变化');return value;
    };
    try {
      if(!window.PortalChatWorkspace?.panelPackage?.create)throw Error('Research Chat 组件未加载');
      const chat=window.PortalChatWorkspace.panelPackage.create({root:root.current,id:'reader-ai-'+discussion.key,
        autoLoad:false,draftOwner:()=>principal,title:'论文 AI 讨论',resolveResident:()=>prepare(true),resolveBeforeSend:(_target,payload)=>prepare(false,payload.text||payload.plainText),
        initialContext:(_target,detail)=>({context_text:detail.initial_context?.context_text||''}),
        onDetail:detail=>{if(!current())return;setAnswers((detail.messages||[]).filter(m=>window.PortalChatWorkspace.messageRole(m)==='agent')
          .map(m=>Array.isArray(m.content_parts)?m.content_parts.filter(p=>p.type==='text').map(p=>p.text||'').join('\n'):String(m.content||'')).filter(Boolean));},
        onError:e=>{if(current())setError(e.message);}});
      const send=chat.send.bind(chat);chat.send=(...args)=>{if(!current())throw Error('论文或账号已变化');return send(...args);};
      panel.current=chat;
      chat.openSurface({workspace_key:discussion.key,paper_id:discussion.document.paper_id}).then(()=>{
        if(current())chat.workspace.setDraftText(discussion.text?`请解释以下选区，并区分原文与推断：\n${discussion.text}`:'请围绕本篇论文回答：');
      }).catch(e=>{if(current())setError(e.message);});
    } catch(e){setError(e.message);}
    const watch=setInterval(()=>{if(!disposed&&!isCurrent()){disposed=true;panel.current?.destroy();panel.current=null;setAnswers([]);setDraft('');setError('论文或账号已变化，请重新打开讨论');}},250);
    return()=>{disposed=true;clearInterval(watch);panel.current?.destroy();panel.current=null;};
  },[discussion.key]);
  const save=async()=>{
    if(!isCurrent()||!draft.trim())return;setSaving(true);setError('');
    if(saveIdentity.current?.text!==draft)saveIdentity.current={text:draft,id:crypto.randomUUID().replaceAll('-','').slice(0,8).toUpperCase()};
    try{await bridge().saveAnswer(discussion,draft,saveIdentity.current.id);if(isCurrent())setSaved(true);}catch(e){if(isCurrent())setError(e.message);}finally{setSaving(false);}
  };
  return <aside className="paperAIPanel" aria-label="论文 AI 侧栏">
    <header><strong>论文 AI · 独立讨论</strong><button onClick={close}>收起 AI</button></header>
    <p>仅本篇材料；此处继续提问沿用当前讨论。另开讨论不带旧历史。</p>
    {discussion.position&&<button onClick={()=>{if(isCurrent())bridge().locateSelection(discussion);}}>引用选区 · 第 {discussion.position.pageIndex+1} 页</button>}
    {error&&<p role="alert">{error}</p>}
    <div ref={root} className="paperAIChat" />
    <details><summary>确认保存回答为便签</summary>
      <select aria-label="选择 AI 回答" defaultValue="" onChange={e=>{setDraft(e.target.value);setSaved(false);}}><option value="">选择回答</option>{answers.map((text,i)=><option key={i} value={text}>回答 {i+1}</option>)}</select>
      <textarea aria-label="便签预览" value={draft} onChange={e=>{setDraft(e.target.value);setSaved(false);}} />
      <button disabled={!discussion.position||!draft.trim()||saving||saved} onClick={save}>{saved?'便签已保存':'确认保存便签'}</button>
      {!discussion.position&&<small>请从原文选区开启讨论以确定便签位置。</small>}
    </details>
  </aside>;
}
