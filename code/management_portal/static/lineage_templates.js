(() => {
  'use strict';

  const MIME = window.PortalCompatibility.identifier("template_transfer");
  const t = value => window.PortalI18n.t(value);
  const esc = value => window.PortalUIPrimitives.escapeHtml(String(value ?? ''));
  const clone = value => JSON.parse(JSON.stringify(value));
  const categories = () => ({ apply: t('施加'), structure: t('结构'), team: t('团队') });
  const targetLabels = () => ({ resident: 'Resident', selection: t('选区'), canvas: t('画布') });
  const button = (label, action, className = '') => `<button type="button" class="portalButton ${className}" data-action="${action}">${esc(t(label))}</button>`;
  const optionsHtml = (choices, chosen) => choices.map(choice => {
    const value = typeof choice === 'object' ? choice.value : choice;
    const label = typeof choice === 'object' ? choice.label : choice;
    return `<option value="${esc(value)}" ${String(value) === String(chosen) ? 'selected' : ''}>${esc(label)}</option>`;
  }).join('');

  function field(schema, value, attribute) {
    const { type = 'text', label, name, description } = schema;
    const title = label || description || name;
    const attrs = `${attribute} aria-label="${esc(t(title))}" ${schema.required ? 'required' : ''}`;
    let control;
    if (type === 'boolean') control = `<input type="checkbox" ${attrs} ${value ? 'checked' : ''}>`;
    else if (type === 'enum' || type === 'select') control = `<select ${attrs}>${optionsHtml(schema.options || [], value)}</select>`;
    else if (['integer', 'number', 'duration'].includes(type)) control = `<input type="number" step="${type === 'number' ? 'any' : '1'}" ${attrs} value="${esc(value ?? '')}">`;
    else if (schema.multiline === false) control = `<input ${attrs} value="${esc(value ?? '')}">`;
    else control = `<textarea rows="${type === 'json' ? 4 : 3}" ${attrs} ${type === 'json' ? 'spellcheck="false"' : ''}>${esc(type === 'json' ? JSON.stringify(value ?? {}, null, 2) : value ?? '')}</textarea>`;
    return `<label class="templateField ${type === 'boolean' ? 'templateCheck' : ''}"><span>${esc(t(title))}</span>${control}${label && description ? `<small>${esc(t(description))}</small>` : ''}</label>`;
  }

  function readField(input, type) {
    if (type === 'boolean') return input.checked;
    if (['integer', 'number', 'duration'].includes(type)) {
      if (!input.value.trim()) return undefined;
      const value = Number(input.value);
      if (!Number.isFinite(value) || (type !== 'number' && !Number.isInteger(value))) throw new Error(t('请输入有效数值'));
      return value;
    }
    if (type === 'json') return JSON.parse(input.value);
    return input.value;
  }

  function behaviorHtml(behavior = {}, parameters = {}) {
    const describe = step => window.PortalI18n.message(step).replace(/\{\{param\.([A-Za-z_][A-Za-z0-9_]*)\}\}/g,
      (token, name) => Object.hasOwn(parameters, name) && parameters[name] !== '' ? String(parameters[name]) : token);
    return `<h3>${esc(t('施加后会做什么'))}</h3><ol>${(behavior.steps || []).map(step => `<li>${esc(describe(step))}</li>`).join('')}</ol>
      <p class="templateOutcome">${esc(t(behavior.persistent ? '结果保留为 Function Block' : '一次性操作'))}</p>`;
  }

  function templatePayload(template) {
    return Object.fromEntries(['kind', 'category', 'title', 'description', 'targets', 'parameters', 'body'].map(key => [key, clone(template[key])]));
  }

  // Builtins keep the pigeons the Function Block shelf gave them; any other template
  // or block takes one from its ID, so every copy of a template looks the same.
  const PIGEONS = Object.freeze({ agent_loop: 0, expand_ideas: 2, revision_return: 3, family: 4, expand_plans: 34, leader_pulse: 35, fixed_reminder: 37, scheduled_work: 38 });

  function pigeonPosition({ builtin_key: key = '', id = '' } = {}) {
    let index = PIGEONS[key];
    if (!Number.isInteger(index)) {
      let hash = 0;
      for (const character of String(id)) hash = (Math.imul(hash, 31) + character.codePointAt(0)) >>> 0;
      index = hash % 50;
    }
    return `${((index % 10) * 100 / 9).toFixed(4)}% ${Math.floor(index / 10) * 25}%`;
  }

  function pigeonHtml(template, className = 'templateGlyph') {
    const persistent = template.behavior?.persistent;
    return `<span class="${className}" aria-hidden="true"><span class="templatePigeon" style="background-position:${pigeonPosition({ builtin_key: template.builtin_key, id: template.template_id })}"></span>${persistent ? '<i class="templateModeIcon">∞</i>' : ''}</span>`;
  }

  // mount and its template editor create their parts from factories that
  // module scripts register before this one runs; templatePart(name)
  // returns what a script registers.
  function templatePart(name) {
    const part = window[name];
    if (!part) throw new Error(`${name} is not loaded.`);
    return part;
  }

  // mount builds the template shelf of a Lineage canvas. Its parts:
  //   lineage_templates_dialogs.js  the shelf's dialogs
  //   lineage_templates_shelf.js  the tiles, loading them and dragging them
  //     onto the canvas
  //   lineage_templates_resident_settings.js  the Residents a template creates
  //   lineage_templates_application.js  applying a template to a target
  //   lineage_templates_editing.js  creating templates and the template editor
  // mount holds the state the parts share, creates the parts in that order and
  // returns the shelf's controller; a part that calls a function of a part
  // created after it gets an arrow forwarding the call.
  function mount(config) {
    const { shelf, canvas, request, getContext, resolveTarget, toast } = config;
    // The state the parts share: the templates and template kinds, whether
    // the shelf is destroyed, and the open editor and application dialogs.
    const templatesState = {
      templates: [], kinds: [], disposed: false, editor: null, application: null,
    };

    const { dialog, dialogs } = templatePart('LineageTemplatesDialogs').createDialogs({
      esc, pigeonHtml, t, templatesState,
    });
    const { abort, dropHint, find, load, render, unsupported } = templatePart('LineageTemplatesShelf').createShelf({
      MIME, apply: (...args) => apply(...args), canvas, categories, config, esc, getContext,
      openEditor: (...args) => openEditor(...args), pigeonHtml, request, resolveTarget, shelf, t, targetLabels,
      templatesState, toast,
    });
    const {
      renderResidentSettings, residentRoles,
    } = templatePart('LineageTemplatesResidentSettings').createResidentSettings({
      clone, config, esc, getContext, optionsHtml, t,
    });
    const { apply, applyFamily } = templatePart('LineageTemplatesApplication').createApplication({
      behaviorHtml, button, canvas, dialog, esc, field, getContext, load, optionsHtml, readField,
      renderResidentSettings, request, residentRoles, t, targetLabels, templatesState, toast, unsupported,
    });
    const { newTemplate, openEditor, saveSelection } = templatePart('LineageTemplatesEditing').createEditing({
      behaviorHtml, button, categories, clone, dialog, esc, field, getContext, load, optionsHtml, readField,
      renderResidentSettings, request, residentRoles, t, targetLabels, templatePart, templatePayload, templatesState,
      toast,
    });

    return Object.freeze({ load, render, find, openEditor, newTemplate, apply, applyFamily, saveSelection,
      destroy() { templatesState.disposed = true; abort.abort(); [...dialogs].forEach(dialog => dialog.close()); dropHint.remove(); },
    });
  }

  window.LineageTemplates = Object.freeze({ mount, pigeonPosition });
})();
