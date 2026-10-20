import {resolvedType} from './selection-model.js';

let nextId=0;
// Queries belong to this rendered picker and sequence. Detached pickers and
// older responses cannot replace candidates for the current text.
export function instancePicker({sdk,type,selected,signal,onSelect}) {
  const root=document.createElement('div');root.className='packagingInstancePicker';
  const input=document.createElement('input');input.className='portalField';
  input.setAttribute('role','combobox');input.setAttribute('aria-label',`${type} · 名称或 ID`);
  input.setAttribute('aria-autocomplete','list');input.setAttribute('aria-expanded','false');input.autocomplete='off';
  input.placeholder='按名称或资源 ID 选择';
  const popup=document.createElement('div');popup.className='packagingCandidatePopup';popup.hidden=true;
  const status=document.createElement('p');status.setAttribute('role','status');
  const list=document.createElement('div');list.setAttribute('role','listbox');list.id=`packaging-candidates-${++nextId}`;
  list.setAttribute('aria-label',`${type} 候选资源`);input.setAttribute('aria-controls',list.id);
  const more=document.createElement('button');more.type='button';more.textContent='加载更多';more.className='portalButton';more.hidden=true;
  popup.append(status,list,more);root.append(input,popup);
  let sequence=0,timer,items=[],active=-1,cursor='',open=false;
  const live=()=>!signal.aborted&&root.isConnected;
  const close=()=>{open=false;sequence++;clearTimeout(timer);popup.hidden=true;input.setAttribute('aria-expanded','false');input.removeAttribute('aria-activedescendant');};
  const highlight=index=>{
    active=index;const options=[...list.children];
    options.forEach((option,i)=>option.setAttribute('aria-selected',String(i===active)));
    if(options[active]){input.setAttribute('aria-activedescendant',options[active].id);options[active].scrollIntoView({block:'nearest'});}
    else input.removeAttribute('aria-activedescendant');
  };
  const choose=item=>{if(!selected.includes(item.resource_id)){close();onSelect(item.resource_id);}};
  const render=()=>{
    list.replaceChildren();active=-1;input.removeAttribute('aria-activedescendant');
    for(const item of items){
      const option=document.createElement('div');option.setAttribute('role','option');option.id=`${list.id}-${list.children.length}`;
      const label=typeof item.title==='string'?item.title:item.resource_id;
      option.textContent=`${label} · ${item.resource_id}${selected.includes(item.resource_id)?'（已选择）':''}`;
      option.setAttribute('aria-disabled',String(selected.includes(item.resource_id)));option.setAttribute('aria-selected','false');
      let pointerStart;
      option.addEventListener('pointerdown',event=>{pointerStart={x:event.clientX,y:event.clientY};});
      option.addEventListener('click',event=>{
        const selection=option.ownerDocument.getSelection();
        const dragged=pointerStart&&Math.hypot(event.clientX-pointerStart.x,event.clientY-pointerStart.y)>3;
        pointerStart=null;
        if(dragged&&selection&&!selection.isCollapsed&&(option.contains(selection.anchorNode)||option.contains(selection.focusNode)))return;
        choose(item);
      });list.append(option);
    }
    status.textContent=items.length?'选择资源加入清单':'没有匹配的可读资源';more.hidden=!cursor;more.disabled=false;
  };
  async function query(token,append=false){
    const q=input.value.trim();
    try{
      let result;
      if(/^[a-z][a-z0-9_]*_[0-9a-f]{12}$/.test(q)){
        const resolved=await sdk.hatg('ResolveResource',{address:q});
        if(resolved.resource?.resource_id!==q||resolvedType(resolved)!==type)throw Error('资源类型不匹配或不可读取');
        result={items:[resolved.resource]};
      }else result=await sdk.hatg('SearchResources',{resource_type:type,q,limit:30,...(append&&cursor?{cursor}:{})});
      if(!live()||token!==sequence||!open)return;
      items=[...new Map([...(append?items:[]),...(result.items||[])].map(v=>[v.resource_id,v])).values()];cursor=result.next_cursor||'';render();
    }catch(error){if(live()&&token===sequence&&open){items=[];list.replaceChildren();cursor='';more.hidden=true;status.textContent=`查询失败：${error.message}`;}}
  }
  const load=(delay=0,append=false)=>{
    if(!live())return;open=true;popup.hidden=false;input.setAttribute('aria-expanded','true');
    clearTimeout(timer);const token=++sequence;status.textContent='正在加载候选…';more.disabled=true;
    active=-1;input.removeAttribute('aria-activedescendant');list.replaceChildren();
    // No selectable old candidates while a new query is pending.
    if(!append)items=[];
    timer=setTimeout(()=>void query(token,append),delay);
  };
  input.addEventListener('focus',()=>load());input.addEventListener('click',()=>{if(!open)load();});
  input.addEventListener('input',()=>load(180));more.addEventListener('click',()=>load(0,true));
  input.addEventListener('keydown',event=>{
    if(event.key==='Escape'){event.preventDefault();close();return;}
    if(['ArrowDown','ArrowUp'].includes(event.key)){
      event.preventDefault();if(!open){load();return;}
      const available=items.map((v,i)=>selected.includes(v.resource_id)?-1:i).filter(i=>i>=0);
      const at=available.indexOf(active),step=event.key==='ArrowDown'?1:-1;
      if(list.children.length&&available.length)highlight(available[(at<0?(step>0?0:available.length-1):(at+step+available.length)%available.length)]);
    }else if(event.key==='Enter'){event.preventDefault();if(open&&active>=0&&items[active])choose(items[active]);}
  });
  // Scrollbar interaction and window blur can lose focus without moving it
  // elsewhere. Keep the candidates available until an actual outside action.
  root.addEventListener('focusout',event=>{if(event.relatedTarget&&!root.contains(event.relatedTarget))close();});
  document.addEventListener('pointerdown',event=>{if(live()&&!root.contains(event.target))close();},{signal});
  signal.addEventListener('abort',close,{once:true});
  return root;
}
