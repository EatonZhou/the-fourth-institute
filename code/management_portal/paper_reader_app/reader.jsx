import React, {useEffect, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {createPortal} from 'react-dom';
import {connectZotero, ZOTERO_URL} from './zotero_adapter.js';
import {useTranslation, TranslationResult} from './translation_panel.jsx';
import {translateSelection} from './translation_client.js';
import {AIPanel} from './ai_panel.jsx';
import {ResearchAssistantPicker} from './research_assistant_picker.jsx';
const t=value=>window.PortalI18n.t(value);

function ReaderSplit({content, width}) {
  const handle=useRef(null);
  useEffect(()=>{
    const node=handle.current, area=content.current;
    const limits=()=>{const total=area.clientWidth;return {min:Math.min(260,Math.max(120,total*.4)),max:Math.max(120,total-Math.min(320,total*.5)-7)};};
    const apply=value=>{const {min,max}=limits();width.current=Math.round(Math.min(max,Math.max(min,value)));area.style.setProperty('--reader-ai-width',width.current+'px');node.setAttribute('aria-valuemin',Math.round(min));node.setAttribute('aria-valuemax',Math.round(max));node.setAttribute('aria-valuenow',width.current);};
    apply(width.current||Math.min(360,area.clientWidth*.42));
    const cancel=()=>{node.dispatchEvent(new PointerEvent('pointercancel',{pointerId:active}));document.body.classList.remove('resizingReaderAI');};
    let active=0;
    const down=e=>{active=e.pointerId;};node.addEventListener('pointerdown',down);
    const unbind=window.PortalWorkbench.layout.bindSplitter({handle:node,axis:'x',direction:-1,bodyClass:'resizingReaderAI',min:()=>limits().min,max:()=>limits().max,readSize:()=>width.current,applySize:apply});
    const resize=new ResizeObserver(()=>apply(width.current));resize.observe(area);
    const lost=()=>{if(document.body.classList.contains('resizingReaderAI'))cancel();};
    node.addEventListener('lostpointercapture',lost);window.addEventListener('blur',cancel);
    const observer=new MutationObserver(()=>{if(area.closest('.is-embedded'))cancel();});observer.observe(area.closest('dialog'),{attributes:true,attributeFilter:['class']});
    return()=>{cancel();unbind();resize.disconnect();observer.disconnect();window.removeEventListener('blur',cancel);node.removeEventListener('lostpointercapture',lost);node.removeEventListener('pointerdown',down);};
  },[]);
  return <div ref={handle} className="paperAISplitter" role="separator" aria-orientation="vertical" aria-label="调整 AI 侧栏宽度" tabIndex={0}/>;
}

function Reader({paper, principal, anchor, close, control, readingReady, frameReady, isCurrent, isFullscreen}) {
  const [,setLanguageVersion]=useState(0);
  useEffect(()=>{
    const changed=()=>setLanguageVersion(value=>value+1);
    window.addEventListener('portal:language-changed',changed);
    return()=>window.removeEventListener('portal:language-changed',changed);
  },[]);
  const frame = useRef(null), bridge = useRef(null), content = useRef(null), aiWidth = useRef(0);
  const [status, setStatus] = useState('正在打开论文…');
  const [failed, setFailed] = useState(false);
  const [selection, setSelection] = useState(null);
  const [mode, setMode] = useState('auto');
  const [popup, setPopup] = useState(null), [sidebarOpen, setSidebarOpen] = useState(false), [tab, setTab] = useState('ai');
  const sidebarOpenRef=useRef(false);
  sidebarOpenRef.current=sidebarOpen;
  const translation = useTranslation({selection, mode, isCurrent, request: (input, selectedMode, operationId, signal) => {
    if (!isFullscreen()) throw new Error('请进入全屏后翻译');
    return translateSelection({selection: input, mode: selectedMode, operationId, principal, signal});
  }});
  const [discussion,setDiscussion] = useState(null);
  const [assistantCatalog,setAssistantCatalog]=useState(null);
  const discussionSequence=useRef(0);
  useEffect(()=>{
    const changed=event=>{
      discussionSequence.current++;setStatus('');
      const selectedKey=event.detail?.slot?'research:'+event.detail.slot.slot_id:event.detail?.workspace_key;
      if(event.detail?.principal!==principal || discussion?.key!==selectedKey) setDiscussion(null);
    };
    window.addEventListener('portal:paper-research-selected',changed);
    return()=>window.removeEventListener('portal:paper-research-selected',changed);
  },[discussion?.key,principal]);
  const openDiscussion=async selection=>{
    if(!isCurrent()||!isFullscreen())return;
    setSidebarOpen(true); setTab('ai');
    const sequence=++discussionSequence.current;
    try {
      const response=await fetch('/paper/api/research/chats',{credentials:'same-origin'});
      const choices=await response.json();
      if(!response.ok)throw Error(choices.error||'无法读取研究助手');
      if(sequence!==discussionSequence.current)return;
      if(!isCurrent()||choices.principal!==principal)throw Error('账号已变化，请重新打开论文');
      const selected=choices.selected;
      setAssistantCatalog(choices);
      if(!selected||selected.state!=='bound'){setDiscussion(null);setStatus('');return;}
      const key='research:'+selected.slot_id;
      if(discussion?.key===key&&!selection)return;
      const document=selection?.document||await bridge.current.aiDocument();
      if(isCurrent()&&isFullscreen()&&sequence===discussionSequence.current){setStatus('');setDiscussion({...selection,document,key});}
    } catch(error){if(isCurrent()&&sequence===discussionSequence.current)setStatus(error.message); }
  };
  useEffect(() => {
    frameReady(frame.current.contentWindow);
    const connection = connectZotero({frame: frame.current, paper, principal, ready: readingReady,
      translateSelection: value => {if (isCurrent() && isFullscreen()) {
        setSelection(value);
        if(sidebarOpenRef.current){setPopup(null);setTab('translation');value.dismissPopup?.();}
        else setPopup(value.popup);
      }},
      dismissTranslation: node => setPopup(current => current === node ? null : current),
      askSelection: value=>void openDiscussion(value),
      status: (message, error = false) => { setStatus(message); setFailed(error); }});
    bridge.current = connection;
    control(connection);
    frame.current.src = ZOTERO_URL;
    return () => { connection.dispose(); };
  }, [paper.id]);
  return <div className="paperReadingApp">
    <header className="paperReadingToolbar">
      <strong title={paper.title}>{paper.title}</strong>
      <button aria-expanded={sidebarOpen && tab === 'ai'} onClick={()=>sidebarOpen && tab === 'ai'?setSidebarOpen(false):openDiscussion()}>{t(sidebarOpen && tab === 'ai'?'收起 AI':'打开 AI')}</button>
      <button aria-expanded={sidebarOpen && tab === 'translation'} onClick={()=>{setSidebarOpen(true);setTab('translation');setPopup(null);selection?.dismissPopup?.();}}>{t('翻译')}</button>
      <a href={paper.pdf_url} target="_blank" rel="noopener noreferrer">原始 PDF</a>
      <button onClick={() => close()}><span className="paperLayoutEnter">全屏阅读</span><span className="paperLayoutExit">退出全屏</span></button>
    </header>
    <div className="paperReadingStatus" data-empty={!status} role="status">
      {t(status)} {failed ? <button onClick={() => bridge.current?.retry()}>重试保存</button> : status && <button aria-label={t('关闭提示')} onClick={()=>setStatus('')}>×</button>}
    </div>
    {popup?.isConnected && !sidebarOpen && createPortal(<TranslationResult selection={selection} mode={mode} model={translation}
      expand={()=>{setSidebarOpen(true);setTab('translation');setPopup(null);selection?.dismissPopup?.();}} />, popup)}
    <div ref={content} className="paperReadingContent"><iframe ref={frame} className="paperReadingViewer" title="论文全屏 PDF 阅读器" />
      {sidebarOpen&&<ReaderSplit content={content} width={aiWidth} />}
      <aside className="paperReaderSidePanel" hidden={!sidebarOpen} aria-label={window.PortalI18n.t("阅读侧栏")}>
        <nav role="tablist" aria-label={window.PortalI18n.t("阅读侧栏")}>
          <button role="tab" aria-selected={tab==='ai'} onClick={()=>{setTab('ai');if(!discussion)void openDiscussion();}}>AI</button>
          <button role="tab" aria-selected={tab==='translation'} onClick={()=>setTab('translation')}>{window.PortalI18n.t("翻译")}</button>
          <button aria-label={window.PortalI18n.t("关闭侧栏")} onClick={()=>setSidebarOpen(false)}>×</button>
        </nav>
        <div className="paperReaderTab" role="tabpanel" hidden={tab!=='ai'}>
          {discussion&&<AIPanel key={discussion.key} discussion={discussion} principal={principal} bridge={()=>bridge.current} isCurrent={isCurrent} close={()=>setSidebarOpen(false)} />}
          {!discussion&&<ResearchAssistantPicker catalog={assistantCatalog} principal={principal} isCurrent={isCurrent} onSelected={()=>openDiscussion()} />}
        </div>
        <div className="paperReaderTab" role="tabpanel" hidden={tab!=='translation'}>
          {selection?<TranslationResult selection={selection} mode={mode} setMode={setMode} model={translation} full />:<p>{window.PortalI18n.t("选择论文中的文字开始翻译。")}</p>}
        </div>
      </aside>
    </div>
  </div>;
}

export function openReader({paper, principal, anchor, onClose, onReady, container, isCurrent = () => true}) {
  const previousFocus = document.activeElement;
  const dialog = document.createElement('dialog');
  dialog.className = 'paperReadingDialog'; dialog.setAttribute('aria-label', '全屏论文阅读');
  document.body.append(dialog);
  const root = createRoot(dialog);
  let closed = false, closing = false, bridge;
  const instanceId = crypto.randomUUID();
  let sourceWindow;
  let fullscreen = !container, layoutTick;
  const layout = () => {
    if (closed) return;
    dialog.classList.toggle('is-embedded', !fullscreen);
    // The iframe lives outside the mode's DOM to preserve its document/session.
    // Its old rectangle is not a visible host after navigation removes it.
    const target = container?.();
    const visible = isCurrent() && (fullscreen || Boolean(target?.isConnected && target.getClientRects().length));
    dialog.hidden = !visible;
    dialog.style.display = visible ? '' : 'none';
    dialog.inert = closing || !visible;
    if (!fullscreen) {
      if (target) {
        const rect = target.getBoundingClientRect();
        Object.assign(dialog.style, {left: `${rect.left}px`, top: `${rect.top}px`, width: `${rect.width}px`, height: `${rect.height}px`});
      }
    } else Object.assign(dialog.style, {left: '0', top: '0', width: '100vw', height: '100dvh'});
    layoutTick = requestAnimationFrame(layout);
  };
  const setFullscreen = value => { fullscreen = !!value; bridge?.setFullscreen(fullscreen); dialog.classList.toggle('is-embedded', !fullscreen); dialog.setAttribute('aria-modal', String(fullscreen)); };
  const escape = event => {
    if (container && fullscreen && event.key === 'Escape') {event.preventDefault(); event.stopPropagation(); setFullscreen(false);}
  };
  document.addEventListener('keydown', escape, true);
  // A browser unload cannot await the native save barrier. Keep the document
  // interactive when navigation is cancelled; in-app exits use close() first.
  const beforeUnload = event => {
    if (!closed && bridge?.needsLeaveConfirmation()) {event.preventDefault(); event.returnValue = '';}
  };
  window.addEventListener('beforeunload', beforeUnload);
  const close = async position => {
    if (closed) return true;
    if (closing) return false;
    closing = true;
    dialog.inert = true;
    if (bridge && !await bridge.flush()) { closing = false; dialog.inert = false; setFullscreen(true); return false; }
    closed = true;
    cancelAnimationFrame(layoutTick);
    document.removeEventListener('keydown', escape, true);
    window.removeEventListener('beforeunload', beforeUnload);
    position ||= bridge?.captureAnchor() || anchor;
    root.unmount(); dialog.close(); dialog.remove(); previousFocus?.focus(); onClose?.(position);
    return true;
  };
  const toggle = () => container ? setFullscreen(!fullscreen) : close();
  dialog.addEventListener('cancel', event => { event.preventDefault(); toggle(); });
  if (container) {dialog.show(); layout();} else dialog.showModal();
  root.render(<Reader paper={paper} principal={principal} anchor={anchor} close={toggle} control={value => { bridge = value; bridge.setFullscreen(fullscreen); }}
    isFullscreen={() => fullscreen}
    isCurrent={() => !closed && isCurrent()}
    frameReady={value => { sourceWindow = value; }}
    readingReady={value => { if (!closed && !closing) onReady?.({...value, instanceId}); }} />);
  return {close, setFullscreen, paperId: paper.id, principal, pdfUrl: paper.pdf_url, instanceId, get sourceWindow() { return sourceWindow; }};
}
