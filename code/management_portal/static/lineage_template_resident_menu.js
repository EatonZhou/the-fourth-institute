(() => {
  'use strict';
  if (window.LineageTemplateResidentMenu) return;
  const t=value=>window.PortalI18n.t(value);
  const esc=value=>window.PortalUIPrimitives.escapeHtml(String(value ?? ''));

  // The adapter owns presentation only. All editable values are read from and
  // written to the editor's draft, through its normal history/validation path.
  function create({root,draft,editorState,projection,readDraft,onChange,onLocalGraph,onSource,onInspect,onExpand,isExpanded=()=>false}) {
    const overlay=document.createElement('div');
    overlay.className='portalDetailOverlay templateResidentOverlay';overlay.hidden=true;
    overlay.innerHTML=`<section class="portalDetailSurface nodeSettingsSurface" role="dialog" aria-modal="true" aria-label="${esc(t('模板 Resident 菜单'))}">
      <header class="portalDetailHeader residentMenuHeader"><div class="residentMenuHeading"><strong data-template-menu-title></strong><small class="residentMenuMeta" data-template-menu-provenance></small></div><button type="button" class="portalButton is-icon" data-menu-close aria-label="${esc(t('关闭'))}">×</button></header>
      <div class="portalDetailBody nodeSettingsBody"><nav class="nodeSettingsTabs" aria-label="${esc(t('模板 Resident'))}">${[['duties','职责'],['memory','记忆'],['settings','配置'],['bindings','绑定']].map(([key,label])=>`<button type="button" class="nodeSettingsTab" data-template-menu-tab="${key}">${esc(t(label))}</button>`).join('')}${['会话','运行','Harness','Mailbox'].map(label=>`<button type="button" class="nodeSettingsTab" disabled>${esc(t(label))}</button>`).join('')}</nav><div data-template-menu-content></div></div></section>`;
    root.append(overlay);
    const dialog=window.PortalUI.createDialogController({modal:overlay,escapeClose:true,backdropClose:true});
    let selected='',tab='duties';
    const metadata=()=>projection().body.presentation?.residents?.[selected];
    const editable=()=>!!metadata()?.editable && draft.kind==='role';
    const duties=()=>editable() ? draft.body.duties.map(row=>({...row,...metadata().duties.find(item=>item.key===row.key),...row})) : metadata()?.duties || [];
    const memories=()=>editable() ? draft.body.memories : metadata()?.memories || [];
    const field=(label,value,attributes)=>{label=label==='名称'?'一句话职责说明':label;return `<label class="templateField"><span>${esc(t(label))}</span><textarea rows="4" aria-label="${esc(t(label))}" ${attributes}>${esc(value)}</textarea></label>`;};
    function render() {
      try{readDraft();}catch(error){if(!editorState.invalidTemplateMenu)throw error;}
      const meta=metadata();if(!meta){dialog.close();return;}
      overlay.querySelector('[data-template-menu-title]').textContent=meta.title;
      overlay.querySelector('[data-template-menu-provenance]').textContent=`${t('模板草稿')} · ${meta.source?.template_id || draft.template_id || t('未保存')}${meta.source?.version ? ` · v${meta.source.version}` : ''}`;
      overlay.querySelectorAll('[data-template-menu-tab]').forEach(button=>button.setAttribute('aria-selected',String(button.dataset.templateMenuTab===tab)));
      const rows=overlay.querySelector('[data-template-menu-content]');
      const expansionLabel=isExpanded(selected)?'收起内部结构':'展开内部结构';
      const source=meta.source?.template_id;
      const sourceLink=!editable()&&source ? `<button type="button" class="portalButton" data-template-menu-source>${esc(t('编辑角色源模板'))}</button>` : '';
      if(tab==='duties') rows.innerHTML=sourceLink+`<button type="button" class="portalButton is-compact" data-template-menu-expand aria-expanded="${isExpanded(selected)}">${esc(t(expansionLabel))}</button><ol class="residentMenuDuties">${duties().map(duty=>`<li class="residentMenuDuty"><details><summary><span class="residentMenuDutyText">${esc(duty.title)}</span><span class="residentMenuDutyCategory">${esc(t('职责'))}</span></summary><div class="residentMenuDutyBody">${editable() ? field('名称',duty.title,`data-template-duty="${esc(duty.key)}" data-duty-field="title"`)+field('指南',duty.guide,`data-template-duty="${esc(duty.key)}" data-duty-field="guide"`)+field('触发提示词',duty.prompt,`data-template-duty="${esc(duty.key)}" data-duty-field="prompt"`) : `<pre class="residentMenuDutyPrompt">${esc(duty.memory?.prompt || duty.guide || '')}</pre><pre class="residentMenuDutyPrompt">${esc(duty.prompt || '')}</pre>`}<div class="residentMenuDutyActions"><button type="button" class="portalButton is-compact" data-template-duty-graph="${esc(duty.key)}">${esc(t('查看职责图'))}</button>${duty.control ? `<button type="button" class="portalButton is-compact" data-template-duty-control="${esc(duty.control)}">${esc(t('编辑职责参数'))}</button>` : ''}</div></div></details></li>`).join('')}</ol>`;
      else if(tab==='memory') {
        const levels={trigger:[],turn:[],session:[],on_demand:[]};
        memories().forEach((memory,index)=>levels[memory.level]?.push({...memory,index,content:memory.prompt,source:t('角色记忆')}));
        duties().forEach(duty=>{if(duty.memory)levels[duty.memory.level]?.push({title:duty.title,content:editable()?duty.guide:duty.memory.prompt,source:t('职责指南')});if(duty.prompt)levels.trigger.push({title:duty.title,content:duty.prompt,source:t('职责触发提示词')});});
        rows.innerHTML=sourceLink+window.PortalResidentMenuMemory.levelsHtml(levels,item=>`<li class="residentMenuMemoryItem"><details><summary><span class="residentMenuDutyText">${esc(item.title || item.key)}</span><span class="residentMenuDutyCategory">${esc(item.source)}</span></summary><div class="residentMenuDutyBody">${editable()&&item.index!==undefined ? `<label class="templateField"><span>${esc(t('记忆层级'))}</span><select data-template-memory="${item.index}" data-memory-field="level">${['turn','session','on_demand'].map(level=>`<option ${level===item.level?'selected':''}>${level}</option>`).join('')}</select></label>`+field('内容',item.content,`data-template-memory="${item.index}" data-memory-field="prompt"`) : `<pre class="residentMenuDutyPrompt">${esc(item.content)}</pre>`}</div></details></li>`,{template:true});
      } else if(tab==='settings') rows.innerHTML=sourceLink+(editable()?field('节点配置 JSON',editorState.invalidTemplateMenu?.resident===selected?editorState.invalidTemplateMenu.text:JSON.stringify(draft.body.resident,null,2),'data-template-resident-config'):`<pre>${esc(JSON.stringify(meta.settings || {},null,2))}</pre>${meta.structure ? `<button type="button" class="portalButton" data-template-resident-inspect>${esc(t('编辑节点配置'))}</button>` : ''}`) + `<p class="paneNote">${esc(t('这里只配置模板；施加后产生的会话和运行状态不属于模板草稿。'))}</p>`;
      else rows.innerHTML=Object.values(window.LineageTemplateGraph.bindings(projection().body)).map(binding=>`<section class="residentMenuDuty"><strong>${esc(binding.key)}</strong><p>${esc(binding.type)} · ${esc(binding.direction)} · ${esc(binding.cardinality)} · ${esc(t(binding.required?'必需':'可选'))}</p></section>`).join('');
    }
    overlay.addEventListener('click',event=>{
      if(event.target.closest('[data-menu-close]')){dialog.close();return;}
      const button=event.target.closest('[data-template-menu-tab]');if(button){tab=button.dataset.templateMenuTab;render();return;}
      if(event.target.closest('[data-template-menu-source]')){dialog.close();void onSource?.(metadata().source.template_id);return;}
      if(event.target.closest('[data-template-menu-expand]')){dialog.close();onExpand(selected);return;}
      if(event.target.closest('[data-template-resident-inspect]')){dialog.close();onInspect(selected);return;}
      const graph=event.target.closest('[data-template-duty-graph]');
      if(graph){const duty=metadata().duties.find(duty=>duty.key===graph.dataset.templateDutyGraph);dialog.close();onLocalGraph(selected,duty);return;}
      const control=event.target.closest('[data-template-duty-control]');if(control){dialog.close();onInspect(control.dataset.templateDutyControl);}
    });
    overlay.addEventListener('input',event=>{
      if(!editable())return;
      const input=event.target;
      if(input.matches('[data-template-duty]'))draft.body.duties.find(row=>row.key===input.dataset.templateDuty)[input.dataset.dutyField]=input.value;
      else if(input.matches('[data-template-memory]'))draft.body.memories[Number(input.dataset.templateMemory)][input.dataset.memoryField]=input.value;
      else if(input.matches('[data-template-resident-config]')){
        try{const value=JSON.parse(input.value);if(!value || Array.isArray(value)||typeof value!=='object')throw Error();draft.body.resident=value;input.setCustomValidity('');delete editorState.invalidTemplateMenu;}
        catch{input.setCustomValidity(t('请输入有效 JSON 对象'));editorState.invalidTemplateMenu={resident:selected,text:input.value};}
      }else return;
      input.dataset.templateHistoryKey=input.dataset.templateDuty ? `duty:${input.dataset.templateDuty}:${input.dataset.dutyField}` : input.dataset.templateMemory!==undefined ? `memory:${input.dataset.templateMemory}:${input.dataset.memoryField}` : 'resident:settings';
      event.stopPropagation();onChange(event);
    });
    return {open(key,section='duties'){selected=key;tab=section;render();dialog.open();},close(){dialog.close();},destroy(){dialog.destroy();overlay.remove();}};
  }
  window.LineageTemplateResidentMenu=Object.freeze({create});
})();
