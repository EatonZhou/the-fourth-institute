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
      disposeCode(); editorState.graph?.destroy(); editorState.graph = null;
      bodyPage.querySelectorAll('[data-structure-view]').forEach(item => item.setAttribute('aria-pressed', String(item.dataset.structureView === mode)));
      const content = bodyPage.querySelector('[data-structure-content]');
      if (mode === 'json') { content.innerHTML = '<div class="templateCodeEditor" data-code-editor></div>'; mountCode(content.firstElementChild, JSON.stringify(draft.body, null, 2), 'json'); return; }
      if (mode === 'list') { renderStructureLists(); return; }
      content.innerHTML = `<div class="templateGraphWorkspace"><div class="templateGraphHost" data-template-graph></div><aside class="templateGraphInspector portalHatgOverlayComponent" data-graph-inspector aria-label="${esc(t('选中元素'))}"></aside></div>${functionBlockField()}`;
      editorState.graph = window.LineageTemplateGraph.mount({ root: content.querySelector('[data-template-graph]'), body: () => draft.body,
        onBeforeChange: readDraft, onChange: schedulePreview, onSelect: renderInspector });
      renderInspector('');
    }

    function renderBody() {
      disposeCode();
      if (draft.kind === 'prompt') {
        bodyPage.innerHTML = `<div class="templateParameterTokens">${draft.parameters.map(parameter => `<button type="button" data-insert-parameter="${esc(parameter.name)}">{{param.${esc(parameter.name)}}}</button>`).join('')}</div>
            ${field({ label: '消息内容', type: 'text', required: true }, draft.body.message, 'data-body-field="message"')}
            ${field({ label: '投递方式', type: 'enum', options: [{ value: 'queue', label: t('加入队列') }, { value: 'attach_to_next', label: t('随下一条消息附带') }] }, draft.body.activation_policy, 'data-body-field="activation_policy"')}`;
      } else if (draft.kind === 'structure') {
        bodyPage.innerHTML = `<p class="templateHelp">${esc(t('可以先在画布搭好结构，再用“存为模板”开始创作。'))}</p>
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

    function renderStructureLists() {
      const container = bodyPage.querySelector('[data-structure-content]');
      container.innerHTML = Object.entries(listLabels).map(([key, label]) => `<section class="templateStructureList" data-structure-list="${key}">
          <header><h3>${esc(t(label))} <small>${(draft.body[key] || []).length}</small></h3><button type="button" class="portalButton is-compact" data-add-row="${key}">+ ${esc(t('添加'))}</button></header>
          ${(draft.body[key] || []).map((row, index) => `<details data-structure-row="${index}" data-row-key="${esc(row.key)}"><summary><code>${esc(row.key)}</code><span>${esc(row.title || row.from || row.kind || '')}</span></summary><div class="templateRowFields">${rowFields(key, row)}${button('移除', 'remove-row', 'is-danger')}</div></details>`).join('')}
        </section>`).join('') + functionBlockField();
    }

    function renderParameters() {
      parametersPage.innerHTML = `<p class="templateHelp">${esc(t('参数在施加时填写；内容中使用 {{param.name}} 引用。'))}</p>
          <div class="templateParameterTable"><table><thead><tr>${['名称', '类型', '默认值', '必填', '选项（每行一个）', '说明', ''].map(label => `<th>${esc(t(label))}</th>`).join('')}</tr></thead><tbody>
          ${draft.parameters.map((parameter, index) => `<tr data-parameter-row="${index}">
            <td><input data-parameter-field="name" aria-label="${esc(t('名称'))}" value="${esc(parameter.name)}" required pattern="[A-Za-z_][A-Za-z0-9_]*"></td>
            <td><select data-parameter-field="type" aria-label="${esc(t('类型'))}">${optionsHtml(['text', 'integer', 'number', 'boolean', 'enum', 'duration'], parameter.type)}</select></td>
            <td><textarea rows="2" data-parameter-field="default" aria-label="${esc(t('默认值'))}">${esc(parameter.default ?? '')}</textarea></td>
            <td><input type="checkbox" data-parameter-field="required" aria-label="${esc(t('必填'))}" ${parameter.required ? 'checked' : ''}></td>
            <td><textarea rows="2" data-parameter-field="options" aria-label="${esc(t('选项（每行一个）'))}">${esc((parameter.options || []).join('\n'))}</textarea></td>
            <td><input data-parameter-field="description" aria-label="${esc(t('说明'))}" value="${esc(parameter.description || '')}"></td>
            <td><button type="button" data-remove-parameter="${index}" class="portalButton is-quiet" aria-label="${esc(t('移除参数'))}">×</button></td></tr>`).join('')}</tbody></table></div>${button('添加参数', 'add-parameter')}`;
    }

    return {
      listLabels, renderBody, renderParameters, renderStructureLists, showStructure,
    };
  }

  window.LineageTemplatesEditorPages = Object.freeze({ createEditorPages, dependencies: DEPENDENCIES });
})();
