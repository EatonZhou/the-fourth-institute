import {createResearchSession,addDiscussion} from './session.js';
import './research.css';

export function start({markup,mount,data,workbenchId,snapshotRef,snapshotHash}) {
  const root=document.getElementById('app');root.innerHTML=markup;
  const panel=root.querySelector('.semanticMatrix'),sdk=window.Projection;
  let runtime,visible,disposed=false;
  const error=e=>{if(disposed)return;const node=panel.querySelector('#matrixError');node.textContent=e.message;node.hidden=false;};
  const session=createResearchSession(sdk,{workbenchId,onError:error,onInvalidate(reason){
    runtime?.invalidate();
    if(session.active&&['show','sources'].includes(reason))void runtime?.refresh();
  }});
  runtime=mount(panel,{session,snapshotRef,snapshotHash},data);
  addDiscussion(panel,session);
  const context=document.createElement('p');context.className='matrixMuted';context.setAttribute('role','status');panel.append(context);
  const stopFocus=sdk.onFocus(object=>{if(!disposed)context.textContent=object?'当前讨论对象：'+object.ref:'';});
  const stopVisibility=sdk.onVisibility(next=>{
    if(disposed||next===visible)return;
    const previous=visible;visible=next;
    if(next){if(previous===false)session.show();}else session.hide();
  });
  const destroy=()=>{if(disposed)return session.destroy();disposed=true;stopFocus();stopVisibility();const released=session.destroy();runtime.destroy();return released;};
  addEventListener('pagehide',destroy,{once:true});
  return {destroy,session,runtime};
}
