// Host paths are enumerated by the authenticated directory bridge, not by
// searching Workspace registrations or uploading the browser's local files.
export function chooseDirectory({sdk, signal, initialPath = ''}) {
  if (signal.aborted) return Promise.resolve(null);
  return new Promise(resolve => {
    const node=(tag,text='')=>{const el=document.createElement(tag);el.textContent=text;return el;};
    const dialog=node('dialog');dialog.className='packagingDirectoryPicker';dialog.setAttribute('aria-label','选择发布文件夹');
    const previous=document.activeElement;
    const heading=node('h2','选择发布文件夹');
    const hint=node('p','浏览 Portal 所在主机的文件夹，进入目标目录后点击“选择此文件夹”。');
    const path=node('input');path.className='portalField';path.setAttribute('aria-label','当前路径');path.placeholder='/';
    const location=node('p'),status=node('p'),list=node('div');status.setAttribute('role','status');list.className='packagingDirectoryList';
    let closed=false,busy=false,current=null,next='';
    const finish=value=>{if(closed)return;closed=true;signal.removeEventListener('abort',abort);dialog.close();dialog.remove();previous?.focus();resolve(value);};
    const abort=()=>finish(null);
    const controls=()=>{
      dialog.querySelectorAll('button,input').forEach(el=>{el.disabled=busy;});
      cancel.disabled=false;select.disabled=busy||!current;up.disabled=busy||!current?.parent;
    };
    const run=async action=>{
      if(busy||closed)return;busy=true;status.textContent='正在读取目录…';controls();
      try{await action();}catch(error){if(!closed){current=null;list.replaceChildren();more.hidden=true;location.textContent='';status.textContent=error.message;}}
      finally{busy=false;if(!closed)controls();}
    };
    const button=(title,action)=>{const b=node('button',title);b.type='button';b.className='portalButton';b.addEventListener('click',()=>run(action));return b;};
    const open=async (target,append=false)=>{
      const data=await sdk.directories({path:target,...(append?{after:next}:{})});
      if(closed)return;
      if(!Array.isArray(data.directories)||typeof data.path!=='string'||!data.path.startsWith('/'))throw Error('目录列表无效');
      current=data;path.value=data.path;location.textContent=`当前文件夹：${data.path}`;
      if(!append)list.replaceChildren();
      for(const item of data.directories){
        const b=button('',()=>open(item.path));b.setAttribute('aria-label',item.name);b.title=item.path;
        const icon=node('span','📁');icon.setAttribute('aria-hidden','true');
        const name=node('span',item.name),arrow=node('span','›');arrow.setAttribute('aria-hidden','true');
        b.append(icon,name,arrow);list.append(b);
      }
      next=data.next_after||'';more.hidden=!next;
      status.textContent=list.childElementCount?'':'此文件夹没有可浏览的子文件夹。';
    };
    const go=button('前往',()=>open(path.value.trim()));
    const root=button('根目录',()=>open('/'));
    const up=button('上一级',()=>open(current.parent));
    const more=button('加载更多文件夹',()=>open(current.path,true));more.hidden=true;
    const select=button('选择此文件夹',async()=>{
      const selected=current.path;
      const verified=await sdk.directories({path:selected});
      if(verified.path!==selected)throw Error('目录位置已变化，请重新选择');
      if(!closed)finish(selected);
    });
    const cancel=node('button','取消');cancel.type='button';cancel.className='portalButton';cancel.addEventListener('click',()=>finish(null));
    const pathRow=node('div'),navigation=node('div'),actions=node('div');
    pathRow.className=navigation.className=actions.className='packagingActions';
    pathRow.append(path,go);navigation.append(root,up);actions.append(select,cancel);
    dialog.append(heading,hint,navigation,pathRow,list,more,location,status,actions);
    dialog.addEventListener('cancel',event=>{event.preventDefault();finish(null);});
    path.addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();run(()=>open(path.value.trim()));}});
    path.addEventListener('input',()=>{select.disabled=true;});
    signal.addEventListener('abort',abort,{once:true});document.body.append(dialog);dialog.showModal();path.focus();
    run(()=>open(initialPath.startsWith('/')?initialPath:'/'));
  });
}
