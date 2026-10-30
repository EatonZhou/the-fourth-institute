(() => {
  'use strict';

  if (window.LineageTemplatesEditorInspector) return;

  // The inspector beside a structure's graph: it draws the selected
  // element's label and fields, binds them to the draft, and moves wires
  // between the ones the template adds and the ones it finds.

  const DEPENDENCIES = Object.freeze([
    'bodyPage', 'button', 'changesHtml', 'clone', 'draft', 'editorState', 'elementFields', 'elementSettingsHtml',
    'esc', 'field', 'foundJson', 'graphTools', 'holdTokens', 'invokeWire', 'isWire', 'listLabels',
    'readElementSettings', 'recordUpdate', 'renderResidentSettings', 'schedulePreview', 't',
  ]);

  function createEditorInspector(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`LineageTemplatesEditorInspector is missing: ${missing.join(', ')}`);
    const {
      bodyPage, button, changesHtml, clone, draft, editorState, elementFields, elementSettingsHtml, esc, field,
      foundJson, graphTools, holdTokens, invokeWire, isWire, listLabels, readElementSettings, recordUpdate,
      renderResidentSettings, schedulePreview, t,
    } = dependencies;

    const elementLabel = (list, row) => list === 'roles' ? t('角色')
      : list === 'wires' || (list === 'match' && isWire(row)) ? `${String(row.kind || '').toUpperCase()} WIRE`
        : row.kind === 'resident' ? 'RESIDENT' : String(row.kind || '').replace('_', ' ').toUpperCase();

    // The target and Leader roles and found nodes already exist in the Lineage, so
    // a wire between them can be one the template finds or one it adds.
    const existingEnd = key => (draft.body.roles || []).some(role => role.key === key && ['target', 'lineage_leader'].includes(role.from))
      || (draft.body.match || []).some(row => row.key === key && !isWire(row));
    const FOUND_KINDS = ['resident', 'event_source', 'effect', 'loop', 'filter', 'selector', 'reducer', 'seed'];
    const UPDATABLE_WIRES = ['route', 'target'];

    function bindElementSettings(host, list, row) {
      const found = list === 'match';
      const settingsList = found ? (isWire(row) ? 'wires' : 'components') : list;
      const subject = found ? { ...row, config: graphTools().effectiveConfig(row) } : row;
      const root = host.querySelector('[data-element-settings]');
      const status = host.querySelector('[data-element-status]');
      const json = host.querySelector('[data-element-json]');
      const jsonKey = list === 'roles' ? 'settings' : 'config';
      const syncJson = () => { if (json && document.activeElement !== json) json.value = JSON.stringify(found ? foundJson(row) : row[jsonKey] ?? {}, null, 2); };
      let before = null;
      const apply = () => {
        const error = readElementSettings(root, settingsList, subject);
        status.textContent = error ? t(error) : '';
        status.dataset.state = error ? 'error' : '';
        if (error) return;
        if (found) {
          recordUpdate(row, before || {}, subject.config || {});
          before = clone(subject.config || {});
          host.querySelector('[data-found-changes]').innerHTML = changesHtml(row);
        }
        syncJson(); schedulePreview();
      };
      const fields = elementFields();
      if (list !== 'roles') holdTokens(root, subject.config);
      if (settingsList === 'components' && subject.kind === 'event_source') {
        window.PortalLineagePrimitiveInspector.bindInspector(root, { onChange: apply, onTypeChange: apply });
      } else if (settingsList === 'components' && subject.kind === 'effect') {
        fields.bindEffectFields(root, { notify: message => { status.textContent = message; } });
      } else if (settingsList === 'components' && fields.isOperatorKind(subject.kind)) {
        fields.bindOperatorFields(root, false, { onChange: apply });
      } else if (settingsList === 'wires' && invokeWire(subject)) {
        fields.syncDeliveryFields(root);
        window.PortalLineageInvocationInspector.bindMappingEditor(root, { onChange: apply });
      } else if (list === 'roles' && ['new', 'fork'].includes(row.from)) {
        const panel = root.querySelector('[data-role-settings]');
        const values = renderResidentSettings(panel, [row], { resident_ids: [] }, draft)[row.key];
        panel.addEventListener('change', () => {
          if (row.from !== 'fork') row.provider = values.provider;
          row.account = values.account;
          row.settings = { ...values.settings };
          syncJson(); schedulePreview();
        });
      }
      if (list !== 'roles') {
        root.addEventListener('input', apply);
        root.addEventListener('change', event => {
          if (event.target.name === 'unavailable_policy') fields.syncDeliveryFields(root);
          apply();
          editorState.graph?.render();
        });
      }
      if (found && root.childElementCount) {
        // The fields read back slightly normalized; compare later reads with this one.
        const scratch = clone(subject);
        before = clone((readElementSettings(root, settingsList, scratch) ? subject : scratch).config || {});
      }
      json?.addEventListener('change', () => {
        try {
          const value = JSON.parse(json.value || '{}');
          if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error(t('JSON 必须是对象'));
          if (found) {
            Object.keys(foundJson(row)).forEach(name => delete row[name]);
            Object.assign(row, foundJson(value));
          } else row[jsonKey] = value;
          schedulePreview();
          renderInspector(row.key);
          editorState.graph?.render();
        } catch (error) {
          status.textContent = error.message || String(error);
          status.dataset.state = 'error';
        }
      });
      host.querySelector('[data-element-extra]')?.addEventListener('change', event => {
        const control = event.target;
        const name = control.dataset.foundField;
        if (control.matches('[data-wire-nature]')) { moveWire(list, row, control.value); return; }
        if (control.matches('[data-config-from]')) { if (control.value) row.config_from = control.value; else delete row.config_from; }
        else if (name === 'kind') { Object.assign(row, { kind: control.value, config: {}, where: {}, op: 'keep' }); delete row.update; }
        else if (name === 'op') { row.op = control.value; delete row.update; }
        else if (name === 'count') row.count = control.value;
        else if (name === 'event_type') {
          row.where = { ...(row.where || {}) };
          row.config = { ...(row.config || {}) };
          if (control.value) { row.where.event_type = control.value; row.config.event_type = control.value; }
          else delete row.where.event_type;
        } else return;
        schedulePreview(); editorState.graph?.render(); renderInspector(row.key);
      });
    }

    // A wire between existing ends moves between the wires the template adds and
    // the ones it finds, keeping its key, ends and settings.
    function moveWire(from, row, to) {
      if (from === to) return;
      draft.body[from] = draft.body[from].filter(item => item !== row);
      draft.body[to] ||= [];
      const ends = { key: row.key, kind: row.kind, source: row.source, target: row.target,
        ...(row.source_port ? { source_port: row.source_port } : {}), ...(row.target_port ? { target_port: row.target_port } : {}) };
      if (to === 'match') draft.body.match.push({ ...ends, title: row.label || '', config: clone(row.config || {}), where: {}, count: 'one', op: 'keep' });
      else {
        draft.body.wires.push({ ...ends, config: graphTools().effectiveConfig(row) });
        [...draft.body.components, ...draft.body.wires].forEach(item => { if (item.config_from === row.key) delete item.config_from; });
      }
      schedulePreview(); editorState.graph?.render(); renderInspector(row.key);
    }

    function wireNatureHtml(list, row) {
      const wire = list === 'wires' || (list === 'match' && isWire(row));
      if (!wire || !existingEnd(row.source) || !existingEnd(row.target)) return '';
      return field({ label: '这条连线', type: 'enum', options: [{ value: 'wires', label: t('施加时新增') }, { value: 'match', label: t('已有，施加时查找') }] }, list, 'data-wire-nature');
    }

    function configFromHtml(list, row) {
      if (!['components', 'wires'].includes(list)) return '';
      const sources = (draft.body.match || []).filter(item => item.kind === row.kind && item.op !== 'delete');
      if (!sources.length && !row.config_from) return '';
      return field({ label: '沿用已有元素的设置', description: '每个找到的元素各新建一份，设置从它复制，再覆盖这里填写的项。', type: 'enum',
        options: [{ value: '', label: t('不沿用') }, ...sources.map(item => ({ value: item.key, label: item.title ? `${item.key} · ${item.title}` : item.key }))] }, row.config_from || '', 'data-config-from');
    }

    function foundFieldsHtml(row) {
      const wire = isWire(row);
      const manifest = window.PortalLineageProgram?.manifest || {};
      const kindLabel = kind => kind === 'resident' ? 'Resident' : manifest.component_kinds?.[kind]?.title || kind;
      const ops = row.kind === 'resident' ? [{ value: 'keep', label: t('保留') }] : [{ value: 'keep', label: t('保留（可修改设置）') }, { value: 'delete', label: t('删除') }];
      return [
        wire ? '' : field({ label: '类型', type: 'enum', options: FOUND_KINDS.map(kind => ({ value: kind, label: kindLabel(kind) })) }, row.kind, 'data-found-field="kind"'),
        field({ label: '施加时', type: 'enum', options: ops }, row.op === 'delete' ? 'delete' : 'keep', 'data-found-field="op"'),
        wire ? field({ label: '查找数量', type: 'enum', options: [{ value: 'one', label: t('恰好一条') }, { value: 'all', label: t('全部，逐条施加') }] }, row.count || 'one', 'data-found-field="count"') : '',
        row.kind === 'event_source' ? field({ label: '事件类型条件', type: 'enum', options: [{ value: '', label: t('任意事件类型') },
          ...Object.entries(manifest.event_types || {}).map(([value, item]) => ({ value, label: item.title || value }))] }, row.where?.event_type || '', 'data-found-field="event_type"') : '',
      ].join('');
    }

    function renderInspector(key) {
      const host = bodyPage.querySelector('[data-graph-inspector]');
      if (!host) return;
      const list = Object.keys(listLabels).find(name => (draft.body[name] || []).some(row => row.key === key));
      if (!list) { host.innerHTML = `<p class="templateHelp">${esc(t('选中节点或连线后在这里编辑；按 Delete 删除选中的元素。'))}</p>`; return; }
      const index = draft.body[list].findIndex(row => row.key === key);
      const row = draft.body[list][index];
      const found = list === 'match';
      const wire = list === 'wires' || (found && isWire(row));
      const identity = list === 'roles'
        ? [{ name: 'key', label: '角色 key' }, { name: 'title', label: '标题' }, { name: 'description', label: '说明' }, { name: 'from', label: '来源', type: 'enum', options: ['target', 'lineage_leader', 'new', 'fork'] }]
        : [{ name: 'key', label: found ? '已有元素 key' : wire ? '连线 key' : '组件 key' }, ...(list === 'components' ? [{ name: 'title', label: '标题' }] : [])];
      const route = wire ? `<p class="templateInspectorRoute"><code>${esc(row.source)}${row.source_port ? `.${esc(row.source_port)}` : ''}</code> → <code>${esc(row.target)}${row.target_port ? `.${esc(row.target_port)}` : ''}</code></p>` : '';
      const jsonKey = list === 'roles' ? 'settings' : 'config';
      const jsonLabel = found ? '已有元素 JSON' : list === 'roles' ? '起始设置 JSON' : '配置 JSON';
      const jsonValue = found ? foundJson(row) : row[jsonKey] ?? {};
      const advanced = list === 'components' && row.kind === 'event_source' ? '' : `<details class="templateInspectorAdvanced"><summary>${esc(t(jsonLabel))}</summary>
            <textarea data-element-json rows="8" spellcheck="false" aria-label="${esc(t(jsonLabel))}">${esc(JSON.stringify(jsonValue, null, 2))}</textarea></details>`;
      // A deleted found element, a found Resident and found Watch wires
      // have nothing to patch; the others edit the config they would have.
      const editable = !found || (row.op !== 'delete' && (isWire(row) ? UPDATABLE_WIRES.includes(row.kind) : row.kind !== 'resident'));
      const settings = !editable ? '' : found
        ? elementSettingsHtml(isWire(row) ? 'wires' : 'components', { ...row, config: graphTools().effectiveConfig(row) })
        : elementSettingsHtml(list, row);
      const extra = `${found ? foundFieldsHtml(row) : ''}${wireNatureHtml(list, row)}${configFromHtml(list, row)}`;
      host.innerHTML = `<div data-structure-list="${list}"><div class="templateInspectorElement" data-structure-row="${index}" data-row-key="${esc(key)}" ${found ? `data-template-state="${esc(row.op || 'keep')}"` : ''}>
          <header><strong>${found ? `${esc(t('已有'))} · ` : ''}${esc(elementLabel(list, row))}</strong><code>${esc(key)}</code>${button(found ? '移出查找' : '移除', 'remove-row', 'is-danger')}</header>
          <div class="templateRowFields">${identity.map(schema => field({ multiline: false, ...schema }, row[schema.name], `data-row-field="${schema.name}" data-field-type="${schema.type || 'text'}"`)).join('')}</div>${route}
          ${found ? `<p class="templateHelp">${esc(t('施加时从目标出发，沿已有连线查找这个元素；Function Block 内的元素不会被找到。'))}</p>` : ''}
          ${extra ? `<div class="templateRowFields templateInspectorExtra" data-element-extra>${extra}</div>` : ''}
          ${found ? `<div data-found-changes>${changesHtml(row)}</div>` : ''}
          <div class="programInspectorForm templateInspectorSettings" data-element-settings>${settings}</div>
          <p class="templateInspectorStatus" data-element-status role="status"></p>${advanced}</div></div>`;
      bindElementSettings(host, list, row);
    }

    return {
      renderInspector,
    };
  }

  window.LineageTemplatesEditorInspector = Object.freeze({ createEditorInspector, dependencies: DEPENDENCIES });
})();
