(() => {
  'use strict';
  const t = value => window.PortalI18n.t(value);
  const esc = value => window.PortalUIPrimitives.escapeHtml(String(value ?? ''));
  function mount({root,lineageId,functionBlockId,execute = (name,args,options) => window.PortalInstructionClient.execute(name,args,options),onSaved = () => {},onOpenWorkshop = () => {},onSwitchTemplate = onOpenWorkshop}) {
    root.querySelectorAll('[id]').forEach(node => {node.dataset.workshopId ||= node.id;});
    const element = id => root.querySelector(`[data-workshop-id="${id}"],#${id}`);
    const editor = element('workshopEditor'), error = element('workshopError');
    const sessionId = crypto.randomUUID();
    let model, graph, fields, tools, busy = false, generation = 0, selected = '', disposed = false, disposeInspector = null;
    const abort = new AbortController();
    const surface = window.TemplateWorkshopSurface.mount({root});
    let restoring = false;
    const history = window.LineageTemplateWorkshopDraft.history(updateHistory);
    function updateHistory(state) {root.querySelectorAll('[data-workshop-history]').forEach(button=>{button.disabled=!state[button.dataset.workshopHistory] || busy;});}
    const snapshot = () => ({content:JSON.parse(JSON.stringify(model.draft)),...(editor.querySelector('[data-duty-config]:invalid') ? {invalidConfig:editor.querySelector('[data-duty-config]').value,selected} : {})});
    const dirty = () => !!model && (model.dirty() || !!editor.querySelector(':invalid'));
    const notify = (group = '') => {if(model&&!restoring)history.record(snapshot(),group);root.dispatchEvent(new CustomEvent('workshop:draft-changed',{bubbles:true}));};
    const report = err => { error.textContent = window.PortalI18n.message(err.message || err); error.hidden = false; };
    const protect = () => { if (busy) throw new Error(t('正在校验或保存，请稍候')); };
    const run = async action => {
      if (busy) return;
      busy = true; error.hidden = true; editor.inert = true;
      try { return await action(); } catch (err) { report(err); throw err; }
      finally { busy = false; editor.inert = false; updateHistory(history.state()); }
    };
    const preview = args => execute('ApplyLineageChanges',{...args,dry_run:true},{expectedVersion:model.revision});
    root.addEventListener('workshop:review-effect',event => void run(async () => {
      if (dirty()) throw new Error(t('请先保存职责草稿，再确认此版本'));
      const {effectId,draftHash,decision} = event.detail;
      const draft = model.draft.body.components.find(row => row.key === effectId)?.config?.script_draft;
      if (!draft) throw new Error(t('请重新读取候选脚本'));
      if (!await window.PortalUI.confirm({title:t(decision === 'confirm' ? '确认候选脚本？' : '取消脚本草稿？'),message:`${draft.description}\n\n${draft.script}`,confirmLabel:t(decision === 'confirm' ? '确认' : '取消草稿')})) return;
      await execute('ReviewComponentScript',{lineage_id:lineageId,effect_id:effectId,draft_hash:draftHash,decision},{expectedVersion:model.revision});
      await load(); onSaved({function_block_id:functionBlockId,title:model.block.title});
    }).catch(() => {}),{signal:abort.signal});
    function inspect(key = '') {
      disposeInspector?.(); disposeInspector = null;
      selected = key;
      const panel = editor.querySelector('[data-duty-inspector]');
      const list = ['components','wires','match'].find(name => model.draft.body[name].some(row => row.key === key));
      const row = list && model.draft.body[list].find(item => item.key === key);
      if (!row) { panel.hidden = true; return; }
      panel.hidden = false;
      if (list === 'match') {
        panel.innerHTML = `<strong>${esc(row.title || key)}</strong><p>${esc(t('外部对象只读。通过连线引用，配置由对象所属范围管理。'))}</p><code>${esc(key)}</code>`;
        return;
      }
      panel.innerHTML = `<header><strong>${esc(row.kind)}</strong><button type="button" class="portalButton is-danger is-compact" data-duty-remove>${esc(t('删除'))}</button></header>
        <label>${esc(t('名称'))}<input data-duty-element-title value="${esc(row.title || row.label || '')}"></label>
        <div data-duty-settings>${fields.elementSettingsHtml(list,row)}</div>
        <details><summary>${esc(t('高级配置 JSON'))}</summary><textarea data-duty-config rows="10">${esc(JSON.stringify(row.config || {},null,2))}</textarea></details>`;
      const settings = panel.querySelector('[data-duty-settings]');
      const applySettings = () => {
        try {
          protect(); const message = fields.readElementSettings(settings,list,row);
          if (message) throw new Error(message);
          panel.querySelector('[data-duty-config]').value = JSON.stringify(row.config || {},null,2);
          error.hidden = true; graph.render(); notify();
        } catch (err) { report(err); }
      };
      const elementFields = window.PortalLineageElementFields;
      if (row.kind === 'event_source') window.PortalLineagePrimitiveInspector.bindInspector(settings,{onChange:applySettings,onTypeChange:() => { applySettings(); inspect(key); }});
      if (row.kind === 'effect') elementFields.bindEffectFields(settings,{notify:applySettings});
      if (elementFields.isOperatorKind(row.kind)) elementFields.bindOperatorFields(settings,false,{onChange:applySettings});
      if (row.kind === 'route') {
        elementFields.syncDeliveryFields(settings);
        const binding = window.PortalLineageInvocationInspector.bindMappingEditor(settings,{onChange:applySettings});
        disposeInspector = () => binding?.destroy?.();
      }
      settings.addEventListener('change',applySettings);
      panel.querySelector('[data-duty-element-title]').addEventListener('input',event => {
        row[list === 'wires' ? 'label' : 'title'] = event.target.value; graph.render(); notify();
      });
      panel.querySelector('[data-duty-remove]').onclick = () => graph.remove(key);
      panel.querySelector('[data-duty-config]').addEventListener('input',event => {
        try {
          const config = JSON.parse(event.target.value);
          if (!config || Array.isArray(config) || typeof config !== 'object') throw new Error(t('配置必须是 JSON 对象'));
          event.target.setCustomValidity(''); row.config = config; error.hidden = true; graph.render(); notify('config');
        } catch (err) { event.target.setCustomValidity(err.message); report(err); notify('config'); }
      });
    }
    function draw() {
      disposeInspector?.(); tools?.destroy(); graph?.destroy();
      const meta = model.draft.meta;
      const residentHolder = model.all.get(model.block.duty.holder_id)?.kind === 'resident';
      editor.innerHTML = `<header class="workshopBar"><strong>${esc(t('职责实例'))}</strong><input aria-label="${esc(t('职责名称'))}" data-duty-meta="title" value="${esc(meta.title)}">
        <button class="portalButton is-primary" type="button" data-duty-save>${esc(t('预览并保存'))}</button><button class="portalButton" type="button" data-duty-reload>${esc(t('重新载入'))}</button>
        <button class="portalButton" type="button" data-duty-template>${esc(t('存为模板'))}</button><button type="button" class="portalButton is-icon" data-workshop-store data-portal-icon="archive" aria-label="${esc(t('模板商店'))}"></button>
        <div class="workshopHistoryGroup" role="group" aria-label="${esc(t('草稿历史'))}">${['undo','redo'].map(key=>`<button type="button" class="portalButton is-icon" data-workshop-history="${key}" data-portal-icon="${key}" aria-label="${esc(t(key==='undo'?'撤销':'重做'))}"></button>`).join('')}</div></header>
        <main class="workshopEditorContent"><details class="dutyWorkshopMemory"><summary>${esc(t('职责说明与记忆'))} · <span data-duty-version>v${model.revision}</span></summary>
          <label>${esc(t('说明'))}<input data-duty-meta="description" value="${esc(meta.description)}"></label>
          <label>${esc(t('类别'))}<select data-duty-meta="category">${['fixed','common','custom'].map(value => `<option value="${value}" ${value === meta.category ? 'selected' : ''}>${esc(t({fixed:'固定',common:'通用',custom:'自定义'}[value]))}</option>`).join('')}</select></label>
          ${residentHolder ? `<label>${esc(t('职责提示词'))}<textarea data-duty-meta="prompt" rows="4">${esc(meta.prompt)}</textarea></label>
          ${meta.task_prompt !== undefined ? `<label>${esc(t('触发提示词'))}<textarea data-duty-meta="task_prompt" rows="4">${esc(meta.task_prompt)}</textarea></label>` : ''}
          <label>${esc(t('记忆层'))}<select data-duty-meta="memory_level">${['session','turn','trigger','on_demand'].map(value => `<option ${value === meta.memory_level ? 'selected' : ''}>${value}</option>`).join('')}</select></label>` : ''}
          <p>${esc(t('此处只编辑当前职责。Resident 的其他记忆与职责保持独立。'))}</p></details>
        <div class="dutyWorkshopContent"><div data-duty-graph></div><aside class="portalHatgOverlayComponent" data-duty-inspector hidden></aside></div></main>`;
      fields = window.LineageTemplatesEditorElements.createEditorElements({draft:model.draft,esc,t,button:() => '',field:() => ''});
      graph = window.LineageTemplateGraph.mount({root:editor.querySelector('[data-duty-graph]'),body:() => model.draft.body,workshop:true,
        isReadonly:key => model.draft.body.match.some(row => row.key === key),onBeforeChange:protect,onChange:notify,onSelect:inspect});
      editor.querySelectorAll('[data-duty-meta]').forEach(control => control.addEventListener('input',() => {model.draft.meta[control.dataset.dutyMeta] = control.value; notify(control.dataset.dutyMeta);}));
      editor.onsubmit = event => event.preventDefault();
      const palette = element('workshopTools');
      const items = window.LineageTemplateGraph.authoringItems().filter(item => item.toolbox_group === 'primitives');
      tools = window.LineageTemplateGraph.mountTools({root:palette,items,primitiveDrag:true,onInsert:id => graph.addComponent(items.find(item => item.template_id === id).body.components[0].kind)});
      const reference = document.createElement('label');
      reference.className = 'workshopToolGroup';
      reference.innerHTML = `<span>${esc(t('引用已有对象'))}</span><select data-duty-reference aria-label="${esc(t('引用已有对象'))}"><option value="">${esc(t('选择对象'))}</option>${[...model.all.values()].filter(item => !model.owned.has(item.id)).map(item => `<option value="${esc(item.id)}">${esc(item.title || item.id)}</option>`).join('')}</select>`;
      palette.append(reference);
      reference.querySelector('select').onchange = event => { protect(); model.reference(event.target.value); graph.render(); graph.select(event.target.value); event.target.value = ''; };
      updateHistory(history.state());
    }
    async function load(keepHistory = false) {
      const {lineage} = await execute('GetLineage',{lineage_id:lineageId});
      if (disposed) return;
      model = window.LineageDutyWorkshopModel.create(lineage,functionBlockId);
      if(keepHistory)history.saved(snapshot());else history.reset(snapshot());
      generation++; draw(); await templates.load();
    }
    async function save() {
      return run(async () => {
        if (!editor.checkValidity()) { editor.reportValidity(); throw new Error(t('请先修正表单中的错误')); }
        if (!dirty()) return;
        const args = model.request();
        const result = await preview(args);
        const changes = result.preview.elements.map(item => `${item.before ? item.after ? t('修改') : t('删除') : t('新增')} ${item.after?.title || item.before?.title || item.id}\n${JSON.stringify({before:item.before,after:item.after},null,2)}`);
        const metadata = Object.keys(args.duty_changes).map(key => `${key}: ${String(args.duty_changes[key])}`);
        if (!await window.PortalUI.confirm({title:t('保存职责变更？'),message:[...changes,...metadata].join('\n'),confirmLabel:t('保存')})) return;
        await execute('ApplyLineageChanges',args,{expectedVersion:model.revision});
        history.rebase(value=>({...value,content:model.committedDraft(value.content),...(value.selected ? {selected:model.committedKey(value.selected)} : {})}));
        await load(true); onSaved({function_block_id:functionBlockId,title:model.block.title});
      });
    }
    async function acceptDraft(value) {
      return run(async () => {
        if (value.lineage_id !== lineageId || value.function_block_id !== functionBlockId || value.revision !== model.revision || !Array.isArray(value.operations)) throw new Error(t('助手草稿的职责或版本不匹配，请基于当前版本重新生成'));
        if (dirty() && !await window.PortalUI.confirm({title:t('载入助手草稿？'),message:t('将替换当前未保存的职责修改'),confirmLabel:t('载入')})) return;
        const args = {lineage_id:lineageId,function_block_id:functionBlockId,operations:value.operations,duty_changes:value.duty_changes || {}};
        const result = await preview(args), candidate = model.fromPreview(result.preview);
        // Preserve the original base so accepting a draft never writes the Lineage.
        Object.assign(model.draft.body,candidate.body); model.draft.meta = candidate.meta; draw(); notify();
      });
    }
    const templates = window.LineageTemplates.mount({shelf:element('workshopShelf'),canvas:element('workshopCanvas'),
      request:execute,getContext:() => ({lineage_id:lineageId}),iconOnly:true,collapsible:true,
      toast:message => report(new Error(message)),onSelectTemplate:template_id => onSwitchTemplate({lineage_id:lineageId,template_id}),
      acceptTemplate:() => false,resolveTarget:() => ({kind:'canvas'})});
    root.addEventListener('click',event => {
      const action=event.target.closest('[data-workshop-history]')?.dataset.workshopHistory;
      if(action&&!busy){const value=history[action]();if(value){restoring=true;try{model.restore(value.content);draw();if(value.invalidConfig!==undefined){inspect(value.selected);const control=editor.querySelector('[data-duty-config]');control.value=value.invalidConfig;control.setCustomValidity(t('配置必须是 JSON 对象'));}}finally{restoring=false;}notify();}}
      if (event.target.closest('[data-workshop-store]')) void window.PortalTemplateStore.open({request:execute,onOpenWorkshop}).catch(report);
      if (event.target.closest('[data-duty-save]')) void save().catch(() => {});
      if (event.target.closest('[data-duty-reload]')) void run(async () => {
        if (!dirty() || await window.PortalUI.confirm({title:t('重新载入职责？'),message:t('将丢弃当前草稿'),confirmLabel:t('重新载入')})) await load();
      }).catch(() => {});
      if (event.target.closest('[data-duty-template]')) void run(async () => {
        if (dirty()) throw new Error(t('请先保存职责，再存为模板'));
        const args = {from_selection:{lineage_id:lineageId,element_ids:[functionBlockId]},template:{title:model.block.title}};
        await execute('CreateTemplate',{...args,dry_run:true});
        const {template} = await execute('CreateTemplate',args); onOpenWorkshop({lineage_id:lineageId,template_id:template.template_id});
      }).catch(() => {});
    },{signal:abort.signal});
    window.addEventListener('beforeunload',event => {
      if (dirty()) {event.preventDefault();event.returnValue='';}
    },{signal:abort.signal});
    return {sessionId,lineageId,load,save,acceptDraft,dirty,fit:() => graph?.fit(),
      documentKey:() => `${sessionId}:${generation}`,template:() => ({title:model?.block.title || t('职责工坊')}),
      draftFileKey:() => `duty-${functionBlockId}-${sessionId}`,
      assistantContext:() => `当前编辑职责实例，遵循职责草稿协议：先 GetLineage {lineage_id:"${lineageId}"}，目标 function_block_id=${functionBlockId}，基准 revision=${model.revision}。只能改该职责拥有的组件、连线及职责元数据，不得改外部元素或 Resident 配置。交回完整 JSON {lineage_id,function_block_id,revision,operations,duty_changes}，operations 使用 ApplyLineageChanges 格式，duty_changes 仅 title/description/category/prompt/memory_level；Family角色职责另可含task_prompt，须保留facts并遵守模板校验。先 ApplyLineageChanges 加 dry_run:true 和 expected_version:${model.revision} 校验；禁止正式提交。将 JSON 写入本轮指定草稿路径，由用户在工坊预览、保存。`,
      destroy() {disposed=true;surface.destroy();abort.abort();templates.destroy();tools?.destroy();disposeInspector?.();graph?.destroy();editor.replaceChildren();},
    };
  }
  window.LineageDutyWorkshop = Object.freeze({mount});
})();
