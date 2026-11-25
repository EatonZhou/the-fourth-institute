(() => {
  'use strict';
  // Only component presentation: the existing Chat controller owns its session.
  function mount({root}) {
    const dock = root.querySelector('[data-workshop-dock]'), icon = root.querySelector('[data-workshop-assistant]');
    const panel = dock.querySelector('.workshopChatColumn'), abort = new AbortController();
    let drag, moved = false, disposed = false, wasOpen = false;
    const interactions = new Set();
    const listen = (node,type,handler,options = {}) => node.addEventListener(type,handler,{...options,signal:abort.signal});
    const within = node => node instanceof Node && dock.contains(node);
    function place() {
      const bounds = root.getBoundingClientRect(), current = dock.getBoundingClientRect();
      if (!bounds.width || !bounds.height) return;
      const x = Math.max(0,Math.min(current.left-bounds.left,bounds.width-36));
      const y = Math.max(0,Math.min(current.top-bounds.top,bounds.height-36));
      dock.style.left = `${x}px`; dock.style.top = `${y}px`;
      const width = Math.max(0,Math.min(380,bounds.width-16));
      const height = Math.max(0,Math.min(620,bounds.height-16));
      panel.style.width = `${width}px`; panel.style.height = `${height}px`;
      panel.style.left = `${Math.max(8,Math.min(x+40,bounds.width-width-8))-x}px`;
      panel.style.top = `${Math.max(8,Math.min(y,bounds.height-height-8))-y}px`;
    }
    function show() {if(drag)return;panel.hidden = false;icon.setAttribute('aria-expanded','true');place();}
    function hide() {panel.hidden = true;icon.setAttribute('aria-expanded','false');}
    const hover = window.PortalHatgPanelBindings.bindHoverPanel({trigger:icon,panel,open:show,close:hide,isOpen:()=>!panel.hidden,
      keepOpen:()=>panel.contains(document.activeElement) || interactions.size > 0});
    listen(root,'workshop:assistant-interaction',event=>{
      const {open,source}=event.detail;
      if(open){interactions.add(source);show();}
      else {interactions.delete(source);setTimeout(()=>{if(!disposed&&!interactions.size&&!within(document.activeElement)&&!dock.matches(':hover'))hide();},0);}
    });
    listen(dock,'focusin',show);
    listen(icon,'click',event=>{if(moved){event.preventDefault();moved=false;return;}if(wasOpen)hide();else show();});
    listen(root,'click',event=>{if(event.target.closest('[data-workshop-chat]')){hide();icon.focus({preventScroll:true});hide();}});
    listen(dock,'keydown',event=>{
      if(event.key==='Escape'){event.stopPropagation();hide();icon.focus({preventScroll:true});hide();}
      else if(event.target===icon && ['ArrowUp','ArrowDown','ArrowLeft','ArrowRight'].includes(event.key)) {
        event.preventDefault();const dx=event.key==='ArrowLeft'?-16:event.key==='ArrowRight'?16:0,dy=event.key==='ArrowUp'?-16:event.key==='ArrowDown'?16:0;
        dock.style.left=`${dock.offsetLeft+dx}px`;dock.style.top=`${dock.offsetTop+dy}px`;place();
      } else if(event.target===icon && ['Enter',' '].includes(event.key)) {event.preventDefault();if(panel.hidden)show();else hide();}
    });
    listen(icon,'pointerdown',event=>{if(event.button!==0)return;wasOpen=!panel.hidden;moved=false;drag={x:event.clientX,y:event.clientY,left:dock.offsetLeft,top:dock.offsetTop};icon.setPointerCapture(event.pointerId);});
    listen(icon,'pointermove',event=>{if(!drag)return;const dx=event.clientX-drag.x,dy=event.clientY-drag.y;if(Math.hypot(dx,dy)<5&&!moved)return;
      moved=true;hide();dock.style.left=`${drag.left+dx}px`;dock.style.top=`${drag.top+dy}px`;place();});
    const end = () => {drag=null;};listen(icon,'pointerup',end);listen(icon,'pointercancel',end);
    const observer = new ResizeObserver(()=>{if(!disposed)place();});observer.observe(root);
    return {show,hide,destroy(){disposed=true;hover.destroy();observer.disconnect();abort.abort();}};
  }
  window.TemplateWorkshopSurface = Object.freeze({mount});
})();
