(() => {
  'use strict';

  if (window.LineageTemplatesEditorElements) return;

  // The elements of a structure template as the inspector edits them: the
  // settings fields of roles, components and wires, settings bound to
  // template parameters, reading the fields back, and the updates a found
  // element records.

  const DEPENDENCIES = Object.freeze([
    'button', 'draft', 'esc', 'field', 't',
  ]);

  function createEditorElements(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`LineageTemplatesEditorElements is missing: ${missing.join(', ')}`);
    const {
      button, draft, esc, field, t,
    } = dependencies;

    // The inspector beside the graph edits the selected element with the HATG
    // editing area's fields; edits go to the draft and are saved with the template.
    const elementFields = () => window.PortalLineageElementFields;
    const graphTools = () => window.LineageTemplateGraph;
    const roleKeys = () => (draft.body.roles || []).map(role => role.key);
    const elementByKey = key => ['roles', 'components', 'match'].map(list => (draft.body[list] || []).find(row => row.key === key)).find(Boolean) || null;
    const isWire = row => graphTools().isWireKind(row.kind);
    const targetKind = wire => {
      const target = elementByKey(wire.target);
      return roleKeys().includes(wire.target) || !target ? 'resident' : target.kind;
    };

    const invokeWire = row => ['route'].includes(row.kind);

    function roleSettingsHtml(row) {
      if (row.from === 'target') return `<p class="programInspectorNote">${esc(t('施加时由拖放到的 Resident 担任这个角色。'))}</p>`;
      if (row.from === 'lineage_leader') return `<p class="programInspectorNote">${esc(t('施加时由当前 Lineage 的 Leader 担任这个角色。'))}</p>`;
      const fork = row.from === 'fork' ? `${field({ label: 'Fork 来源角色', type: 'enum', options: roleKeys().filter(key => key !== row.key) }, row.fork_of, 'data-row-field="fork_of" data-field-type="enum"')}
          ${field({ label: 'Fork 模式', type: 'enum', options: ['full', 'compacted', 'recent-5', 'recent-10'] }, row.fork_mode || 'full', 'data-row-field="fork_mode" data-field-type="enum"')}` : '';
      return `${fork}<div class="templateInspectorResident" data-role-settings></div>`;
    }

    function elementSettingsHtml(list, row) {
      const fields = elementFields();
      if (list === 'roles') return roleSettingsHtml(row);
      if (list === 'components') {
        const config = row.config || {};
        if (row.kind === 'event_source') {
          const typeId = config.event_type || window.PortalLineageProgram?.primitiveDefaultType?.('event') || '';
          return window.PortalLineagePrimitiveInspector.inspectorHtml({ typeId, config, readonly: false });
        }
        if (row.kind === 'effect') return fields.effectFieldsHtml(config, false);
        if (row.kind === 'loop') return fields.loopFieldsHtml(config, false);
        if (fields.isOperatorKind(row.kind)) return fields.operatorFieldsHtml({ kind: row.kind, config }, false);
        return `<p class="programInspectorNote">${esc(t('这个元素没有额外设置，可在下方 JSON 中编辑。'))}</p>`;
      }
      if (invokeWire(row)) {
        const kind = targetKind(row);
        const profileKey = fields.invokeProfileKey(kind);
        const target = elementByKey(row.target);
        const mappingHtml = window.PortalLineageInvocationInspector.mappingEditorHtml({
          profileKey, mapping: row.config?.invoke_profiles?.[profileKey]?.arguments || {}, wireKind: row.kind,
          targetKind: kind === 'effect' ? 'effect' : profileKey, effectConfig: kind === 'effect' ? target?.config || {} : null,
          editorId: `template-${draft.template_id || 'new'}-${row.key}-${profileKey}`, readonly: false,
        });
        return fields.deliveryFieldsHtml({ config: row.config || {} }, false, { mappingHtml, messageDelivery: profileKey === 'agent' });
      }
      return `<p class="programInspectorNote">${esc(t(row.kind === 'watch' ? 'Watch 只声明 Event 正在观察哪个对象，不触发执行。' : '这条连线只建立引用，不触发执行。'))}</p>`;
    }

    // A setting bound to a template parameter holds a {{param.name}} token. Number
    // and choice fields cannot show one, so they become text fields holding it;
    // typing a concrete value removes the binding.
    const TOKEN = /\{\{[^{}]+\}\}/;
    function holdTokens(root, config) {
      Object.entries(config || {}).forEach(([key, value]) => {
        if (typeof value !== 'string' || !TOKEN.test(value)) return;
        root.querySelectorAll(`[name="${CSS.escape(key)}"]`).forEach(control => {
          if (control.tagName === 'SELECT') {
            if (![...control.options].some(option => option.value === value)) control.add(new Option(value, value));
          } else if (control.tagName !== 'INPUT' || ['checkbox', 'radio'].includes(control.type)) return;
          else if (control.type !== 'text') {
            control.dataset.templateStandIn = control.getAttribute('min') || control.getAttribute('max') || '1';
            control.type = 'text';
            ['min', 'max', 'step', 'pattern'].forEach(name => control.removeAttribute(name));
          }
          control.value = value;
          control.dataset.templateToken = 'true';
          control.title = t('绑定到模板参数；填入具体值即解除绑定。');
        });
      });
    }

    // The readers validate concrete values, so bound fields are read with a stand-in
    // value and their tokens are put back into the result.
    function readWithTokens(root, read) {
      const held = [...root.querySelectorAll('[data-template-token]')]
        .filter(control => TOKEN.test(control.value) && (control.tagName === 'SELECT' || control.dataset.templateStandIn))
        .map(control => [control, control.value]);
      held.forEach(([control]) => { control.value = control.tagName === 'SELECT' ? control.options[0]?.value ?? '' : control.dataset.templateStandIn || ''; });
      try {
        const result = read();
        if (result.config) held.forEach(([control, value]) => { result.config[control.name] = value; });
        return result;
      } finally {
        held.forEach(([control, value]) => { control.value = value; });
      }
    }

    // Reads the visible fields back into the draft element; returns an error message or ''.
    function readElementSettings(root, list, row) {
      const fields = elementFields();
      if (list === 'components') {
        const result = readWithTokens(root, () => {
          if (row.kind === 'event_source') {
            const { config, errors } = window.PortalLineagePrimitiveInspector.readConfig(root);
            return { config, error: errors.join(' ') };
          }
          if (row.kind === 'effect') return { config: fields.effectConfigFromFields(root) };
          if (row.kind === 'loop') return { config: fields.loopConfigFromFields(root) };
          if (fields.isOperatorKind(row.kind)) return fields.operatorConfigFromFields(root, { kind: row.kind, config: row.config || {} });
          return { config: null };
        });
        if (result.error) return result.error;
        if (result.config) row.config = result.config;
        return '';
      }
      if (list === 'wires' && invokeWire(row)) {
        const parsed = window.PortalLineageInvocationInspector.readMapping(root);
        if (parsed.errors.length) return parsed.errors.join(' ');
        const profileKey = fields.invokeProfileKey(targetKind(row));
        const profiles = { ...(row.config?.invoke_profiles || {}) };
        if (Object.keys(parsed.mapping).length) profiles[profileKey] = { arguments: parsed.mapping };
        else delete profiles[profileKey];
        const delivery = readWithTokens(root, () => ({ config: fields.deliveryConfigFromFields(root) })).config;
        const config = { ...(row.config || {}), ...delivery, invoke_profiles: profiles };
        if (!Object.keys(profiles).length) delete config.invoke_profiles;
        row.config = config;
        return '';
      }
      return '';
    }

    // A found element is edited through the config it would have after applying;
    // only the fields that change from its captured config go into its update.
    const same = (left, right) => JSON.stringify(left) === JSON.stringify(right);
    function recordUpdate(row, before, after) {
      const base = row.config || {};
      const update = { ...(row.op === 'update' ? row.update?.config || {} : {}) };
      new Set([...Object.keys(before), ...Object.keys(after)]).forEach(name => {
        if (same(before[name], after[name])) return;
        if (!(name in after)) { if (name in base) update[name] = null; else delete update[name]; }
        else if (same(base[name], after[name])) delete update[name];
        else update[name] = after[name];
      });
      if (Object.keys(update).length) { row.op = 'update'; row.update = { config: update }; }
      else { row.op = 'keep'; delete row.update; }
    }

    const foundJson = row => Object.fromEntries(Object.entries(row).filter(([name]) => !['key', 'source', 'target', 'source_port', 'target_port'].includes(name)));
    const changesHtml = row => {
      const changed = row.op === 'update' ? Object.keys(row.update?.config || {}) : [];
      return changed.length ? `<p class="templateFoundChanges">${esc(t('已修改'))}：<code>${esc(changed.join('、'))}</code>${button('撤销修改', 'revert-update', 'is-quiet is-compact')}</p>` : '';
    };

    return {
      changesHtml, elementFields, elementSettingsHtml, foundJson, graphTools, holdTokens, invokeWire, isWire,
      readElementSettings, recordUpdate,
    };
  }

  window.LineageTemplatesEditorElements = Object.freeze({ createEditorElements, dependencies: DEPENDENCIES });
})();
