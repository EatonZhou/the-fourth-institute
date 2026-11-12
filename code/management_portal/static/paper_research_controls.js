/* Shared Paper assistant controls; catalog/API state stays with the caller. */
(() => {
  'use strict';
  if(window.PortalPaperResearchControls)return;
  const t=value=>window.PortalI18n.t(value);
  const usable=slot=>['bound','unbound'].includes(slot?.state);
  const errorSource=cause=>cause?.status===404||cause?.code==='instruction.not_found'
    ?'研究助手已不可用，请选择其他助手或新建。'
    :cause?.status===403||cause?.code==='instruction.forbidden'
      ?'没有权限使用该研究助手，请选择其他助手。':'无法连接研究助手，请重试或选择其他助手。';
  const errorText=cause=>t(errorSource(cause));
  function create({document:doc=window.document}={}){
    const root=doc.createElement('div');root.className='paperResearchSelector';
    const select=doc.createElement('select');select.className='portalControl';
    const create=doc.createElement('button');create.type='button';create.className='portalButton is-compact is-quiet';
    root.append(select,create);
    const refreshLabels=()=>{
      root.setAttribute('aria-label',t('持续研究助手'));
      select.setAttribute('aria-label',t('选择研究助手'));select.title=t('选择研究助手');
      create.textContent=t('新建 AI');create.title=t('创建独立的持续研究会话');
    };
    refreshLabels();window.addEventListener('portal:language-changed',refreshLabels);
    return {root,select,create,refreshLabels,destroy(){window.removeEventListener('portal:language-changed',refreshLabels);root.remove();}};
  }
  let titleMenuSequence=0;
  function createTitleMenu({titleElement,onSelect,onCreate}){
    const doc=titleElement.ownerDocument,root=doc.createElement('div');
    root.className='paperResearchTitleMenu';root.setAttribute('role','menu');root.hidden=true;
    root.id='paper-assistant-title-menu-'+(++titleMenuSequence);doc.body.append(root);
    titleElement.classList.add('paperResearchTitleTrigger');
    titleElement.setAttribute('role','button');titleElement.tabIndex=0;
    titleElement.setAttribute('aria-haspopup','menu');titleElement.setAttribute('aria-controls',root.id);titleElement.setAttribute('aria-expanded','false');
    const close=()=>{root.hidden=true;titleElement.setAttribute('aria-expanded','false');};
    const show=()=>{
      if(!root.hidden){close();return;}
      const rect=titleElement.getBoundingClientRect();
      root.style.left=Math.max(8,Math.min(rect.left,doc.defaultView.innerWidth-300))+'px';root.style.top=(rect.bottom+4)+'px';
      root.hidden=false;titleElement.setAttribute('aria-expanded','true');root.querySelector('button:not(:disabled)')?.focus();
    };
    const activate=event=>{if(event.type==='click'||['Enter',' ','ArrowDown'].includes(event.key)){event.preventDefault();show();}};
    const outside=event=>{if(!root.contains(event.target)&&!titleElement.contains(event.target))close();};
    const key=event=>{
      if(root.hidden)return;
      if(event.key==='Escape'){event.preventDefault();close();titleElement.focus();}
      if(['ArrowDown','ArrowUp'].includes(event.key)){
        event.preventDefault();const rows=[...root.querySelectorAll('button:not(:disabled)')],index=rows.indexOf(doc.activeElement);
        rows[(index+(event.key==='ArrowDown'?1:-1)+rows.length)%rows.length]?.focus();
      }
    };
    titleElement.addEventListener('click',activate);titleElement.addEventListener('keydown',activate);
    doc.addEventListener('pointerdown',outside);root.addEventListener('keydown',key);
    doc.defaultView.addEventListener('resize',close);
    return {root,close,update({items,selected,pending=false}){
      root.replaceChildren();
      for(const item of items){
        const option=doc.createElement('button');option.type='button';option.setAttribute('role','menuitemradio');
        option.setAttribute('aria-checked',String(item.id===selected));option.textContent=item.title;option.disabled=pending;
        option.onclick=()=>{close();titleElement.focus();onSelect(item.id);};root.append(option);
      }
      const create=doc.createElement('button');create.type='button';create.className='paperResearchTitleNew';create.setAttribute('role','menuitem');
      create.textContent='+ '+t('新建 AI');create.disabled=pending;create.onclick=()=>{close();onCreate();};root.append(create);
    },destroy(){close();root.remove();titleElement.classList.remove('paperResearchTitleTrigger');
      for(const name of ['role','tabindex','aria-haspopup','aria-controls','aria-expanded'])titleElement.removeAttribute(name);
      titleElement.removeEventListener('click',activate);titleElement.removeEventListener('keydown',activate);doc.removeEventListener('pointerdown',outside);root.removeEventListener('keydown',key);
      doc.defaultView.removeEventListener('resize',close);
    }};
  }
  window.PortalPaperResearchControls=Object.freeze({create,createTitleMenu,usable,errorSource,errorText});
})();
