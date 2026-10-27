'use strict';

// Component rows only: pair with readFunctionBlockMenuReady(..., pane:'objects')
// in the original action. This does not establish Resident membership, a new
// failed step, or unread-alert retention. Persist each observation separately.
async function readFunctionBlockObjectRows(panel,{blockId,title,components}={}) {
  if(!/^fbi_[a-z0-9]+$/.test(blockId || '') || typeof title!=='string' || !title.trim() ||
      !Array.isArray(components) || !components.length || new Set(components.map(x=>x?.id)).size!==components.length ||
      components.some(x=>!x || !['effect','event_source'].includes(x.kind) ||
        !new RegExp('^'+x.kind+'_[a-z0-9]+$').test(x.id || '')))
    throw new TypeError('Original blockId, title and distinct Effect/Event component identities are required');
  const expected={blockId,title,components:components.map(({id,kind})=>({id,kind}))};
  const result={expected,rows_ready:false,candidates:[],error:null};
  let handles=[],phase='candidates';
  try {
    handles=await panel.elementHandles();phase='sample';
    for(const handle of handles)result.candidates.push(await handle.evaluate(root=>{
      const visible=n=>{
        if(!n?.isConnected)return false;
        for(let p=n;p;p=p.parentElement){const s=getComputedStyle(p);
          if(p.hidden || p.inert || p.getAttribute('aria-hidden')==='true' || s.display==='none' ||
            ['hidden','collapse'].includes(s.visibility) || Number(s.opacity)===0)return false;}
        const r=n.getBoundingClientRect();return r.width>0 && r.height>0;
      };
      const describe=n=>({tag:n.tagName,visible:visible(n),connected:n.isConnected,busy:n.getAttribute('aria-busy'),
        disabled:n.matches(':disabled'),aria_disabled:n.getAttribute('aria-disabled')});
      const group=root.closest('#nodeMenuGroupPanel[role="tabpanel"]'),dialog=root.closest('[role="dialog"]');
      return {root:describe(root),native:root.matches('.functionBlockMenuPanel[data-function-block-menu-panel]'),
        block_id:root.getAttribute('data-function-block-menu-panel'),group:group?describe(group):null,
        codes:[...root.querySelectorAll(':scope > .functionBlockMenuIdentity > code')].map(n=>({...describe(n),text:n.textContent})),
        titles:[...root.querySelectorAll(':scope > .functionBlockMenuIdentity > strong')].map(n=>({...describe(n),text:n.textContent})),
        selected_tabs:[...(dialog?.querySelectorAll('#nodeMenuGroupTabs [data-group-tab][aria-selected="true"]') || [])]
          .map(n=>({...describe(n),key:n.getAttribute('data-group-tab'),controls:n.getAttribute('aria-controls')})),
        rows:[...root.querySelectorAll('.functionBlockObjectRow')].map(n=>({...describe(n),id:n.getAttribute('data-block-object'),
          kind:n.getAttribute('data-object-kind'),group:n.closest('[data-object-group]')?.getAttribute('data-object-group') ?? null,
          text:n.textContent,
          selectors:[...n.querySelectorAll('input[data-block-object-select]')].map(x=>({...describe(x),id:x.getAttribute('data-block-object-select'),type:x.type,checked:x.checked})),
          editors:[...n.querySelectorAll('button[data-block-object-edit]')].map(x=>({...describe(x),id:x.getAttribute('data-block-object-edit')}))}))};
    }));
    const c=result.candidates[0],usable=n=>!!n && n.visible && n.connected && n.busy!=='true' && !n.disabled && n.aria_disabled!=='true';
    result.rows_ready=result.candidates.length===1 && c.native && c.block_id===blockId && usable(c.root) && usable(c.group) &&
      c.codes.length===1 && usable(c.codes[0]) && c.codes[0].text===blockId &&
      c.titles.length===1 && usable(c.titles[0]) && c.titles[0].text===title &&
      c.selected_tabs.length===1 && usable(c.selected_tabs[0]) && c.selected_tabs[0].key==='objects' &&
      c.selected_tabs[0].controls==='nodeMenuGroupPanel' && expected.components.every(item=>{
        const matches=c.rows.filter(row=>row.id===item.id),row=matches[0];
        return matches.length===1 && usable(row) && row.tag==='LI' && row.kind===item.kind && row.group===item.kind &&
          row.selectors.length===1 && usable(row.selectors[0]) && row.selectors[0].id===item.id && row.selectors[0].type==='checkbox' &&
          row.editors.length===1 && usable(row.editors[0]) && row.editors[0].id===item.id;
      });
  }catch(error){result.rows_ready=false;result.error={phase,name:error.name,message:error.message,stack:error.stack || null};}
  finally{for(const handle of handles)await handle.dispose().catch(()=>{});}
  return result;
}

module.exports={readFunctionBlockObjectRows};
