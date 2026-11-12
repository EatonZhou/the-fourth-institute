/* Paper-only header presentation. No shared Chat state or renderer changes. */
(() => {
  'use strict';
  const titleSelector='.paneTitle,.readerHeadTitle,.reviewCard h3,.apiCard h3,.refTitle';
  const inPaper=element=>element.closest('#paperPageFrame,.paperResearchChatHost,.paperReadingDialog,.paperResearchTitleMenu');
  function trimTooltip(element){
    element.dataset.portalTooltipOptOut='1';
    element.removeAttribute('data-portal-tooltip');
    element.removeAttribute('data-portal-generated-tooltip');
    element.removeAttribute('title');
  }
  function refresh(){
    for(const element of document.querySelectorAll('button,a.portalButton,[data-portal-tooltip]')){
      if(inPaper(element)&&!element.closest('.message,.markdownBody,.composerRichInput'))trimTooltip(element);
    }
    document.getElementById('workspaceTitle')?.removeAttribute('title');
  }
  let pending=false;
  const schedule=()=>{if(!pending){pending=true;requestAnimationFrame(()=>{pending=false;refresh();});}};
  const observer=new MutationObserver(schedule);
  observer.observe(document.body,{subtree:true,childList:true});
  // Native title only when a heading is actually clipped. The paper title is
  // deliberately excluded, including the resource-header generated title.
  document.addEventListener('pointerover',event=>{
    const element=event.target.closest?.(titleSelector);
    if(!element||!inPaper(element)||element.id==='workspaceTitle')return;
    const text=element.textContent.trim();
    if(element.scrollWidth>element.clientWidth+1)element.title=text;
    else element.removeAttribute('title');
  },true);
  window.addEventListener('portal:language-changed',schedule);
  refresh();
})();
