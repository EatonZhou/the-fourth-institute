'use strict';

// Persist this single original-action observation before judging category_ready.
// Native category filtering hides options; it does not detach them. No clicks,
// requests, waits, polling, selection changes or Inspector opening here.
async function readEventCategorySelection(panel, {lineageId, eventId, eventType} = {}) {
  if (!/^lineage_[a-z0-9]+$/.test(lineageId || '') || !/^event_source_[a-z0-9]+$/.test(eventId || '') ||
      typeof eventType !== 'string' || !eventType.trim())
    throw new TypeError('Original lineageId, created eventId and intended eventType are required');
  const result={expected_lineage_id:lineageId,expected_event_id:eventId,expected_event_type:eventType,
    category_ready:false,type_selected:false,candidates:[],error:null};
  let handles=[],phase='candidates';
  try {
    handles=await panel.elementHandles();
    phase='sample';
    for(const handle of handles) result.candidates.push(await handle.evaluate((root,eventType)=>{
      const visible=n=>{
        if(!n?.isConnected) return false;
        for(let p=n;p;p=p.parentElement){
          const s=getComputedStyle(p);
          if(p.hidden || p.inert || p.getAttribute('aria-hidden')==='true' || s.display==='none' ||
            ['hidden','collapse'].includes(s.visibility) || Number(s.opacity)===0) return false;
        }
        const r=n.getBoundingClientRect();return r.width>0 && r.height>0;
      };
      const describe=n=>({tag:n.tagName,id:n.id,visible:visible(n),connected:n.isConnected,
        busy:n.getAttribute('aria-busy'),disabled:n.matches(':disabled'),aria_disabled:n.getAttribute('aria-disabled')});
      const select=n=>({...describe(n),name:n.getAttribute('name'),label:n.getAttribute('aria-label'),value:n.value,
        options:[...n.options].map(o=>({value:o.value,text:o.textContent,category:o.getAttribute('data-primitive-type-category-id'),
          hidden:o.hidden,disabled:o.disabled,selected:o.selected}))});
      const manifest=window.PortalLineageProgram?.manifest;
      const definition=manifest?.event_types?.[eventType];
      const category=definition?.category;
      return {root:describe(root),native:root.matches('.portalHatgPanelComponent[role="region"]'),
        expected_category:category ?? null,category_known:!!manifest?.event_type_categories?.[category],
        identities:[...root.querySelectorAll('#activeLineageIdentity [data-portal-resource-id]')]
          .map(n=>({id:n.getAttribute('data-portal-resource-id'),text:n.textContent,visible:visible(n)})),
        inspectors:[...root.querySelectorAll('#programInspectorPanel')].map(n=>({...describe(n),
          component_id:n.getAttribute('data-component-id'),kind:n.getAttribute('data-inspector-kind'),
          forms:[...n.querySelectorAll('form[data-program-component-form]')].map(form=>({...describe(form),
            primitives:[...form.querySelectorAll('[data-primitive-inspector]')].map(p=>({...describe(p),
              kind:p.getAttribute('data-primitive-kind'),type:p.getAttribute('data-primitive-type'),
              pickers:[...p.querySelectorAll('[data-primitive-inspector-type-picker]')].map(picker=>({...describe(picker),
                categories:[...picker.querySelectorAll('select[data-primitive-type-category]')].map(select),
                types:[...picker.querySelectorAll('select[data-primitive-type-item]')].map(select)}))}))}))}))};
    },eventType));
    const c=result.candidates[0],p=c?.inspectors[0],f=p?.forms[0],primitive=f?.primitives[0],picker=primitive?.pickers[0];
    const category=picker?.categories[0],types=picker?.types[0];
    const usable=n=>!!n && n.visible && n.connected && n.busy!=='true' && !n.disabled && n.aria_disabled!=='true';
    const categoryOptions=category?.options.filter(o=>o.value===c.expected_category) || [];
    const targets=types?.options.filter(o=>o.value===eventType) || [];
    const typed=types?.options.filter(o=>o.category!==null) || [];
    const filtered=typed.length>0 && typed.every(o=>o.hidden===(o.category!==c.expected_category));
    const selectedCategory=category?.options.filter(o=>o.selected) || [];
    const selectedType=types?.options.filter(o=>o.selected) || [];
    result.category_ready=result.candidates.length===1 && c.native && usable(c.root) && c.category_known &&
      c.identities.length===1 && c.identities[0].visible && c.identities[0].id===lineageId && c.identities[0].text===lineageId &&
      c.inspectors.length===1 && usable(p) && p.tag==='ASIDE' && p.kind==='event_source' && p.component_id===eventId &&
      p.forms.length===1 && usable(f) && f.primitives.length===1 && usable(primitive) && primitive.kind==='event' &&
      primitive.pickers.length===1 && usable(picker) && picker.categories.length===1 && picker.types.length===1 &&
      usable(category) && usable(types) && types.name==='event_type' && category.value===c.expected_category &&
      categoryOptions.length===1 && !categoryOptions[0].disabled && !categoryOptions[0].hidden &&
      selectedCategory.length===1 && selectedCategory[0].value===c.expected_category &&
      targets.length===1 && targets[0].category===c.expected_category && !targets[0].hidden && !targets[0].disabled && filtered;
    result.type_selected=result.category_ready && types.value===eventType && selectedType.length===1 &&
      selectedType[0].value===eventType && primitive.type===eventType;
  } catch(error) {
    result.category_ready=false;result.type_selected=false;
    result.error={phase,name:error.name,message:error.message,stack:error.stack || null};
  } finally {
    for(const handle of handles) await handle.dispose().catch(()=>{});
  }
  return result;
}

module.exports={readEventCategorySelection};
