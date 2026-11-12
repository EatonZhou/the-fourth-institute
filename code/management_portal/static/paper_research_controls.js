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
  window.PortalPaperResearchControls=Object.freeze({create,usable,errorSource,errorText});
})();
