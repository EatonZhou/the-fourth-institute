(() => {
  'use strict';
  // Only component presentation: the existing Chat controller owns its session.
  function mount({root}) {
    const dock = root.querySelector('[data-workshop-dock]'), icon = root.querySelector('[data-workshop-assistant]');
    const panel = dock.querySelector('.workshopChatColumn'), abort = new AbortController();
    let region = null, disposed = false, wasOpen = false;
    const interactions = new Set();
    const listen = (node,type,handler,options = {}) => node.addEventListener(type,handler,{...options,signal:abort.signal});
    const within = node => node instanceof Node && dock.contains(node);
    function place() {
      const bounds = root.getBoundingClientRect(), area = region?.getBoundingClientRect();
      dock.hidden = !area || area.width < 36 || area.height < 36;
      if (dock.hidden) return;
      const gap = Math.min(12,(area.width-36)/2,(area.height-36)/2);
      const x = area.left-bounds.left+gap, y = area.bottom-bounds.top-36-gap;
      dock.style.left = `${x}px`; dock.style.top = `${y}px`;
      const width = Math.max(0,Math.min(380,area.width-2*gap));
      const height = Math.max(0,Math.min(620,area.height-36-3*gap));
      panel.style.width = `${width}px`; panel.style.height = `${height}px`;
      panel.style.left = '0px'; panel.style.top = `${-height-gap}px`;
    }
    function show() {panel.hidden = false;icon.setAttribute('aria-expanded','true');place();}
    function hide() {panel.hidden = true;icon.setAttribute('aria-expanded','false');}
    const hover = window.PortalHatgPanelBindings.bindHoverPanel({trigger:icon,panel,open:show,close:()=>{if(!interactions.size)hide();},isOpen:()=>!panel.hidden,
      keepOpen:()=>panel.contains(document.activeElement) || interactions.size > 0});
    listen(root,'workshop:assistant-interaction',event=>{
      const {open,source}=event.detail;
      if(open){interactions.add(source);show();}
      else {interactions.delete(source);setTimeout(()=>{if(!disposed&&!interactions.size&&!within(document.activeElement)&&!dock.matches(':hover'))hide();},0);}
    });
    listen(dock,'focusin',show);
    listen(icon,'click',()=>{if(wasOpen)hide();else show();});
    listen(root,'click',event=>{if(event.target.closest('[data-workshop-chat]')){hide();icon.focus({preventScroll:true});hide();}});
    listen(dock,'keydown',event=>{
      if(event.key==='Escape'){event.stopPropagation();hide();icon.focus({preventScroll:true});hide();}
      else if(event.target===icon && ['Enter',' '].includes(event.key)) {event.preventDefault();if(panel.hidden)show();else hide();}
    });
    listen(icon,'pointerdown',event=>{if(event.button===0)wasOpen=!panel.hidden;});
    const observer = new ResizeObserver(()=>{if(!disposed)place();});observer.observe(root);
    function syncRegion() {
      const next = root.querySelector('.workshopEditorContent > [data-page="body"], .dutyWorkshopContent');
      if(next===region)return;
      if(region)observer.unobserve(region);
      region=next;if(region)observer.observe(region);place();
    }
    const content = new MutationObserver(syncRegion);
    content.observe(root,{childList:true,subtree:true});syncRegion();
    return {show,hide,destroy(){disposed=true;hover.destroy();content.disconnect();observer.disconnect();abort.abort();}};
  }
  window.TemplateWorkshopSurface = Object.freeze({mount});
})();
