(() => {
  'use strict';

  if (window.LineageTemplatesEditorPages) return;

  // The pages of the template editor: the body page for each template kind,
  // a structure's graph, list and JSON views, and the parameters table.

  const DEPENDENCIES = Object.freeze([
    'bodyPage', 'button', 'disposeCode', 'draft', 'editorState', 'esc', 'field', 'mountCode', 'optionsHtml',
    'parametersPage', 'readDraft', 'renderInspector', 'schedulePreview', 't',
  ]);

  function createEditorPages(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`LineageTemplatesEditorPages is missing: ${missing.join(', ')}`);
    const {
      bodyPage, button, disposeCode, draft, editorState, esc, field, mountCode, optionsHtml, parametersPage,
      readDraft, renderInspector, schedulePreview, t,
    } = dependencies;

    const structureSchemas = {
      roles: [{ name: 'key', label: '角色 key' }, { name: 'from', label: '来源', type: 'enum', options: ['target', 'lineage_leader', 'new', 'fork'] }, { name: 'title', label: '标题' }, { name: 'description', label: '说明' }, { name: 'provider', label: 'Provider' }, { name: 'fork_of', label: 'Fork 来源角色' }, { name: 'fork_mode', label: 'Fork 模式', type: 'enum', options: ['full', 'compacted', 'recent-5', 'recent-10'] }, { name: 'settings', label: '起始设置 JSON', type: 'json' }],
      components: [{ name: 'key', label: '组件 key' }, { name: 'kind', label: '类型', type: 'enum', options: ['event_source', 'effect', 'loop', 'filter', 'selector', 'reducer', 'seed'] }, { name: 'title', label: '标题' }, { name: 'config', label: '配置 JSON', type: 'json' }],
      wires: [{ name: 'key', label: '连线 key' }, { name: 'kind', label: '类型', type: 'enum', options: ['route', 'target', 'watch'] }, { name: 'source', label: '来源 key' }, { name: 'source_port', label: '来源端口' }, { name: 'target', label: '目标 key' }, { name: 'target_port', label: '目标端口' }, { name: 'config', label: '配置 JSON', type: 'json' }],
      match: [{ name: 'key', label: '已有元素 key' }, { name: 'kind', label: '类型' }, { name: 'op', label: '施加时', type: 'enum', options: ['keep', 'delete', 'update'] }, { name: 'count', label: '查找数量', type: 'enum', options: ['one', 'all'] }, { name: 'source', label: '来源 key' }, { name: 'source_port', label: '来源端口' }, { name: 'target', label: '目标 key' }, { name: 'target_port', label: '目标端口' }, { name: 'where', label: '匹配条件 JSON', type: 'json' }, { name: 'update', label: '修改 JSON', type: 'json' }],
    };
    const listLabels = { roles: '角色', components: '组件', wires: '连线', match: '已有元素' };
    const rowFields = (list, row) => structureSchemas[list].map(schema => row.from === 'fork' && schema.name === 'provider'
      ? `<label class="templateField">Provider<span>${esc(t('继承来源'))}</span></label>`
      : field({ multiline: schema.type === 'json', ...schema }, row[schema.name], `data-row-field="${schema.name}" data-field-type="${schema.type || 'text'}"`)).join('');
    const functionBlockField = () => field({ label: '封装为 Function Block', type: 'boolean' }, draft.body.function_block, 'data-body-field="function_block" data-field-type="boolean"');

    // 图视图 edits the structure as a HATG graph; 列表视图 and JSON 视图 edit the same body.
    function showStructure(mode) {
      editorState.structureMode = mode;
      editorState.recipeMenu?.destroy();editorState.recipeMenu=null;
      disposeCode(); editorState.graph?.destroy(); editorState.graph = null;
      bodyPage.closest('form').querySelectorAll('[data-structure-view]').forEach(item => item.setAttribute('aria-pressed', String(item.dataset.structureView === mode)));
      const content = bodyPage.querySelector('[data-structure-content]');
      if (mode === 'json') { content.innerHTML = '<div class="templateCodeEditor" data-code-editor></div>'; mountCode(content.firstElementChild, JSON.stringify(draft.body, null, 2), 'json'); return; }
      if (mode === 'list') { renderStructureLists(); return; }
      content.innerHTML = `<div class="templateGraphWorkspace ${editorState.workshop ? 'is-workshop' : ''}"><div class="templateGraphHost" data-template-graph></div><aside class="templateGraphInspector portalHatgOverlayComponent" data-graph-inspector aria-label="${esc(t('选中元素'))}"></aside></div>${functionBlockField()}`;
      editorState.graph = window.LineageTemplateGraph.mount({ root: content.querySelector('[data-template-graph]'), body: () => draft.body, workshop: editorState.workshop,
        onBeforeChange: readDraft, onChange: schedulePreview, onSelect:key=>{
          renderInspector(key);const binding=window.LineageTemplateGraph.bindings(draft.body)[key];
          if(binding)content.querySelector('[data-graph-inspector]').insertAdjacentHTML('afterbegin',`<p class="templateBindingInfo" data-template-binding-info>${esc(binding.key)} · ${esc(binding.type)} · ${esc(binding.direction)} · ${esc(binding.cardinality)} · ${esc(t(binding.required?'必需':'可选'))}</p>`);
        },onOpenResident:key=>{readDraft();editorState.recipeMenu.open(key);} });
      const menu=window.LineageTemplateResidentMenu.create({root:bodyPage,draft,editorState,readDraft,onChange:schedulePreview,
        projection:()=>({body:{...draft.body,presentation:{residents:window.LineageTemplateGraph.residentContexts(draft.body)}}}),
        onInspect:renderInspector,onExpand:()=>editorState.graph.fit(),onLocalGraph:()=>editorState.graph.fit()});
      editorState.recipeMenu=menu;
      renderInspector('');
    }

    function renderBody() {
      disposeCode();
      if (draft.editor?.graph) {
        renderRecipeGraph();
      } else if (draft.kind === 'prompt') {
        bodyPage.innerHTML = `<div class="templateParameterTokens">${draft.parameters.map(parameter => `<button type="button" data-insert-parameter="${esc(parameter.name)}">{{param.${esc(parameter.name)}}}</button>`).join('')}</div>
            ${field({ label: '消息内容', type: 'text', required: true }, draft.body.message, 'data-body-field="message"')}
            <p class="templateHelp">${esc(t('用 [音频文件]、[条目类型] 等中括号标出待填写内容。拖到聊天输入框或 Resident 节点后编辑草稿，再发送。'))}</p>`;
      } else if (draft.kind === 'structure') {
        bodyPage.innerHTML = editorState.workshop ? '<div data-structure-content></div>' : `<p class="templateHelp">${esc(t('可以先在画布搭好结构，再用“存为模板”开始创作。'))}</p>
            <div class="templateStructureToolbar" role="group" aria-label="${esc(t('结构视图'))}">${[['graph', '图视图'], ['list', '列表视图'], ['json', 'JSON 视图']].map(([mode, label]) => `<button type="button" class="portalButton is-compact" data-structure-view="${mode}" aria-pressed="false">${esc(t(label))}</button>`).join('')}</div><div data-structure-content></div>`;
        showStructure(window.LineageTemplateGraph && window.PortalHatgGraph ? 'graph' : 'list');
      } else if (draft.kind === 'script') {
        bodyPage.innerHTML = `<label class="templateField"><span>${esc(t('Endpoints（每行一个）'))}</span><textarea data-endpoints rows="2">${esc((draft.body.endpoints || []).join('\n'))}</textarea></label><div class="templateCodeEditor" data-code-editor></div>`;
        bodyPage.querySelector('[data-endpoints]').addEventListener('input', event => { draft.body.endpoints = event.target.value.split('\n').map(item => item.trim()).filter(Boolean); });
        mountCode(bodyPage.querySelector('[data-code-editor]'), draft.body.script || '', 'shell');
      } else {
        bodyPage.innerHTML = (draft.editor?.fields || []).map(schema => field(schema, draft.body[schema.name] ?? schema.default, `data-body-field="${esc(schema.name)}" data-field-type="${esc(schema.type)}"`)).join('');
        if (!bodyPage.childElementCount) bodyPage.innerHTML = `<p class="templateHelp">${esc(t('此模块通过参数页配置施加时的默认值。'))}</p>`;
      }
    }

    function renderRecipeGraph() {
      let projection = draft.editor.graph;
      const expanded = new Set();
      let localScope = null;
      editorState.recipeMenu?.destroy();
      editorState.graph?.destroy();
      const rows = () => ['roles', 'components', 'wires'].flatMap(key => projection.body[key] || []);
      bodyPage.innerHTML = `<div class="templateRecipeGraph"><div class="templateRecipeToolbar"><button type="button" class="portalButton is-compact" data-recipe-back hidden>${esc(t('返回'))} · ${esc(draft.title)}</button><span data-recipe-breadcrumb></span><select data-recipe-select aria-label="${esc(t('选择图中元素'))}"><option value="">${esc(t('模板参数'))}</option>${rows().map(row => `<option value="${esc(row.key)}">${esc(row.title || row.key)}</option>`).join('')}</select><button type="button" class="portalButton is-compact" data-recipe-parameters>${esc(t('参数'))}</button><button type="button" class="portalButton is-compact" data-recipe-expand aria-expanded="false" ${projection.body.presentation?.level==='duty'?'hidden':''}>${esc(t('全部内部结构'))}</button></div><div class="templateGraphWorkspace is-workshop"><div class="templateGraphHost" data-template-graph></div><aside class="templateGraphInspector" data-graph-inspector hidden></aside></div></div>`;
      const inspector = bodyPage.querySelector('[data-graph-inspector]');
      const select = bodyPage.querySelector('[data-recipe-select]');
      const schemas = draft.editor.fields || [];
      function inspect(key, node) {
        try{readDraft();}catch(error){if(editorState.invalidTemplateMenu){editorState.recipeMenu?.open(editorState.invalidTemplateMenu.resident,'settings');return;}throw error;}
        inspector.hidden = false;
        select.value = key;
        const row = rows().find(row => row.key === key);
        const binding = node?._template_binding || window.LineageTemplateGraph.bindings(projection.body)[key];
        if(binding){inspector.innerHTML=`<button type="button" class="portalButton is-quiet is-compact" data-close-recipe-inspector aria-label="${esc(t('关闭检查器'))}">×</button><h3>${esc(row?.title || binding.key)}</h3><dl class="templateBindingInfo">${[['绑定 key',binding.key],['类型',binding.type],['方向',binding.direction],['数量',binding.cardinality],['必需',binding.required?t('是'):t('否')]].map(([label,value])=>`<dt>${esc(t(label))}</dt><dd>${esc(value)}</dd>`).join('')}</dl>`;return;}
        const resident=projection.body.presentation?.residents?.[key];
        if(resident){inspector.innerHTML=`<button type="button" class="portalButton is-quiet is-compact" data-close-recipe-inspector aria-label="${esc(t('关闭检查器'))}">×</button><h3>${esc(resident.title)}</h3><button type="button" class="portalButton" data-open-template-menu="${esc(key)}">${esc(t('打开模板 Resident 菜单'))}</button>`;return;}
        const controls = key ? projection.controls[key] || [] : schemas.map(schema => ({field: schema.name}));
        inspector.innerHTML = `<button type="button" class="portalButton is-quiet is-compact" data-close-recipe-inspector aria-label="${esc(t('关闭检查器'))}">×</button><h3>${esc(row?.title || row?.key || t('模板参数'))}</h3>` + controls.map(control => {
          if (control.task) {
            const value = draft.body.tasks?.[control.task]?.[control.field] ?? control.value;
            return field({label: control.label, type:'text', multiline:true}, value, `data-recipe-task="${esc(control.task)}" data-task-field="${esc(control.field)}"`);
          }
          const schema = schemas.find(schema => schema.name === control.field);
          return schema ? field(schema, draft.body[schema.name], `data-body-field="${esc(schema.name)}" data-field-type="${esc(schema.type)}"`) : '';
        }).join('') + (row ? `<p class="templateHelp">${esc(t('连线与运行脚本由 Family 维护；这里的配置随模板保存，施加时生成对应运行图。'))}</p><details><summary>${esc(t('实际配置'))}</summary><pre>${esc(JSON.stringify(row.config || row, null, 2))}</pre></details>` : '');
      }
      inspector.addEventListener('input', event => {
        const input = event.target.closest('[data-recipe-task]');
        if (!input) return;
        draft.body.tasks ||= {};
        draft.body.tasks[input.dataset.recipeTask] ||= {};
        draft.body.tasks[input.dataset.recipeTask][input.dataset.taskField] = input.value;
      });
      inspector.addEventListener('click', event=>{if(event.target.closest('[data-close-recipe-inspector]')){inspector.hidden=true;inspector.innerHTML='';}const menu=event.target.closest('[data-open-template-menu]');if(menu)openMenu(menu.dataset.openTemplateMenu);});
      bodyPage.querySelector('[data-recipe-parameters]').addEventListener('click',()=>inspect(''));
      function openMenu(key){readDraft();inspector.hidden=true;inspector.innerHTML='';editorState.recipeMenu.open(key);}
      editorState.graph = window.LineageTemplateGraph.mount({root:bodyPage.querySelector('[data-template-graph]'), body:() => projection.body,
        workshop:true, readOnly:true, expanded:()=>[...expanded],scope:()=>localScope, onSelect:inspect,onOpenResident:openMenu});
      const menu=window.LineageTemplateResidentMenu.create({root:bodyPage,draft,editorState,projection:()=>projection,readDraft,onChange:schedulePreview,onInspect:inspect,
        onSource:id=>editorState.openTemplate?.(id),onExpand:key=>{expanded.add(key);editorState.graph.render();editorState.graph.fit();},
        onLocalGraph:(key,duty)=>{localScope=[key,...(duty?.members || [])];inspector.hidden=true;inspector.innerHTML='';bodyPage.querySelector('[data-recipe-back]').hidden=false;bodyPage.querySelector('[data-recipe-breadcrumb]').textContent=`${projection.body.presentation.residents[key].title} / ${duty?.title || ''}`;editorState.graph.render();editorState.graph.fit();}});
      editorState.recipeMenu=menu;
      bodyPage.querySelector('[data-recipe-back]').addEventListener('click',()=>{localScope=null;bodyPage.querySelector('[data-recipe-back]').hidden=true;bodyPage.querySelector('[data-recipe-breadcrumb]').textContent='';editorState.graph.render();editorState.graph.fit();});
      bodyPage.querySelector('[data-recipe-expand]').addEventListener('click',event=>{const owners=Object.values(projection.body.presentation?.owners || {}),open=event.currentTarget.getAttribute('aria-expanded')!=='true';expanded.clear();if(open)owners.forEach(key=>expanded.add(key));event.currentTarget.setAttribute('aria-expanded',String(open));editorState.graph.render();editorState.graph.fit();});
      select.addEventListener('change', () => {
        const key=select.value,owner=projection.body.presentation?.owners?.[key];
        if(owner)expanded.add(owner);
        localScope=null;bodyPage.querySelector('[data-recipe-back]').hidden=true;bodyPage.querySelector('[data-recipe-breadcrumb]').textContent='';
        editorState.graph.select(key);
      });
      editorState.refreshRecipeGraph = graph => {
        if (!graph) return;
        graph.body.layout = projection.body.layout;
        projection = graph;
        editorState.graph.render();
      };
      if (draft.kind !== 'role' && !(draft.kind === 'familize' && Object.keys(draft.body.role_templates || {}).length)) inspect('');
    }

    function renderStructureLists() {
      const container = bodyPage.querySelector('[data-structure-content]');
      container.innerHTML = Object.entries(listLabels).map(([key, label]) => `<section class="templateStructureList" data-structure-list="${key}">
          <header><h3>${esc(t(label))} <small>${(draft.body[key] || []).length}</small></h3><button type="button" class="portalButton is-compact" data-add-row="${key}">+ ${esc(t('添加'))}</button></header>
          ${(draft.body[key] || []).map((row, index) => `<details data-structure-row="${index}" data-row-key="${esc(row.key)}"><summary><code>${esc(row.key)}</code><span>${esc(row.title || row.from || row.kind || '')}</span></summary><div class="templateRowFields">${rowFields(key, row)}${button('移除', 'remove-row', 'is-danger')}</div></details>`).join('')}
        </section>`).join('') + functionBlockField();
    }

    function renderParameters() {
      parametersPage.innerHTML = `<p class="templateHelp">${esc(t('参数标识用于 {{param.name}} 引用；显示名称是施加窗口中的字段标题，请写清含义和单位。'))}</p>
          <div class="templateParameterTable"><table><thead><tr>${['参数标识', '类型', '默认值', '必填', '选项（每行一个）', '显示名称', ''].map(label => `<th>${esc(t(label))}</th>`).join('')}</tr></thead><tbody>
          ${draft.parameters.map((parameter, index) => `<tr data-parameter-row="${index}">
            <td><input data-parameter-field="name" aria-label="${esc(t('参数标识'))}" value="${esc(parameter.name)}" required pattern="[A-Za-z_][A-Za-z0-9_]*"></td>
            <td><select data-parameter-field="type" aria-label="${esc(t('类型'))}">${optionsHtml(['text', 'integer', 'number', 'boolean', 'enum', 'duration'], parameter.type)}</select></td>
            <td><textarea rows="2" data-parameter-field="default" aria-label="${esc(t('默认值'))}">${esc(parameter.default ?? '')}</textarea></td>
            <td><input type="checkbox" data-parameter-field="required" aria-label="${esc(t('必填'))}" ${parameter.required ? 'checked' : ''}></td>
            <td><textarea rows="2" data-parameter-field="options" aria-label="${esc(t('选项（每行一个）'))}">${esc((parameter.options || []).join('\n'))}</textarea></td>
            <td><input data-parameter-field="description" aria-label="${esc(t('显示名称'))}" value="${esc(parameter.description || '')}"></td>
            <td><button type="button" data-remove-parameter="${index}" class="portalButton is-quiet" aria-label="${esc(t('移除参数'))}">×</button></td></tr>`).join('')}</tbody></table></div>${button('添加参数', 'add-parameter')}`;
    }

    return {
      listLabels, renderBody, renderParameters, renderStructureLists, showStructure,
    };
  }

  window.LineageTemplatesEditorPages = Object.freeze({ createEditorPages, dependencies: DEPENDENCIES });
})();
