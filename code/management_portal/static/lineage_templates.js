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

  function mount(config) {
    const { shelf, canvas, request, getContext, resolveTarget, toast } = config;
    let templates = [];
    let kinds = [];
    let loading = null;
    let disposed = false;
    let dragged = '';
    let editor = null;
    let application = null;
    const dialogs = new Set();
    const abort = new AbortController();
    const listen = (node, event, handler, capture = false) => node?.addEventListener(event, handler, { signal: abort.signal, capture });
    const report = error => toast(window.PortalI18n.message(error.message || error), 'error');
    const find = id => templates.find(item => item.template_id === id) || null;
    const dropHint = document.createElement('div');
    dropHint.className = 'templateDropHint';
    dropHint.hidden = true;
    dropHint.setAttribute('role', 'status');
    document.body.append(dropHint);

    function render() {
      if (disposed) return;
      if (application && application.lineageId !== getContext().lineage_id) application.close();
      shelf.innerHTML = Object.entries(categories()).map(([category, label]) => {
        const rows = templates.filter(item => item.category === category);
        if (!rows.length) return '';
        return `<div class="templateCategory" role="group" aria-label="${esc(label)}" data-template-category="${category}"><span class="templateCategoryLabel" aria-hidden="true">${esc(label)}</span>
          ${rows.map(item => {
            const hint = window.PortalI18n.text`${t(item.behavior?.persistent ? '施加后保留为 Function Block' : '一次性操作')} · 拖到 ${item.targets.map(kind => targetLabels()[kind]).join(' / ')} · 点击编辑`;
            return `<button type="button" class="templateTile" draggable="true" data-template-id="${esc(item.template_id)}" aria-label="${esc(t(item.title))}" title="${esc([t(item.title), t(item.description), hint].filter(Boolean).join('\n'))}">
            ${pigeonHtml(item)}<span class="templateTileTitle">${esc(t(item.title))}</span></button>`;
          }).join('')}</div>`;
      }).join('');
      if (!templates.length) shelf.insertAdjacentHTML('beforeend', `<p class="templateEmpty">${esc(t('暂无模板，点击 + 新建'))}</p>`);
    }

    function load() {
      if (loading) return loading;
      loading = request('ListTemplates', {}).then(result => {
        if (disposed) return;
        templates = result.templates || [];
        kinds = result.kinds || [];
        render();
        config.onChange?.();
      }).finally(() => { loading = null; });
      return loading;
    }

    function dialog(type, title, template = null) {
      const root = document.createElement('div');
      root.className = 'portalDetailOverlay lineageDialogOverlay templateOverlay';
      root.dataset.templateDialog = type;
      root.hidden = true;
      root.innerHTML = `<section class="portalDetailSurface templateSurface templateSurface-${type}" role="dialog" aria-modal="true" aria-label="${esc(title)}" data-portal-detail-surface>
        <header class="portalDetailHeader templateDialogHeader"><div class="templateDialogHeading">${template ? pigeonHtml(template, 'templateGlyph templateHeaderGlyph') : ''}<div><span class="portalDetailEyebrow">TEMPLATE</span><h2>${esc(title)}</h2></div></div>
          <button type="button" class="portalDetailClose" data-action="close" aria-label="${esc(t('关闭'))}">×</button></header>
        <form class="templateForm"><div class="templateDialogContent"></div><p class="templateError" role="alert" hidden></p><footer class="portalDetailFooter templateDialogFooter"></footer></form></section>`;
      document.body.append(root);
      const cleanups = [];
      let closed = false;
      const controller = window.PortalUI.createDialogController({ modal: root, openClass: 'portalDialogOpen', bodyClass: 'agentLineageModalOpen', escapeClose: true, backdropClose: false, requestClose: () => result.close(),
        onOpen: () => { root.hidden = false; }, onClose: () => { root.hidden = true; } });
      const result = {
        root, content: root.querySelector('.templateDialogContent'), footer: root.querySelector('footer'), form: root.querySelector('form'), cleanups,
        error(error) { const node = root.querySelector('.templateError'); node.hidden = !error; node.textContent = error ? window.PortalI18n.message(error.message || error) : ''; },
        close() { if (closed) return; closed = true; controller.close(); cleanups.forEach(cleanup => cleanup()); controller.destroy?.(); root.remove(); dialogs.delete(result); if (editor === result) editor = null; if (application === result) application = null; },
        async busy(work) { if (result.working) return; result.working = true; result.error(null); const buttons = [...root.querySelectorAll('footer button')]; buttons.forEach(item => { item.disabled = true; }); try { await work(); } catch (error) { result.error(error); } finally { result.working = false; buttons.forEach(item => { item.disabled = false; }); } },
      };
      root.addEventListener('click', event => { if (event.target.closest('[data-action="close"]')) result.close(); });
      // Escape closes through our owner too, so Monaco and pending previews are disposed.
      root.addEventListener('keydown', event => { if (event.key === 'Escape' && !event.defaultPrevented) { event.stopPropagation(); result.close(); } });
      dialogs.add(result);
      controller.open();
      return result;
    }

    function unsupported(template) {
      return `${t(template.title)} · ${t('请拖到支持的目标')}：${template.targets.map(kind => targetLabels()[kind]).join(' / ')}`;
    }

    function clearDrop() {
      dropHint.hidden = true;
      canvas.classList.remove('templateDropAllowed', 'templateDropForbidden');
      canvas.querySelectorAll('.templateDropTarget').forEach(node => node.classList.remove('templateDropTarget'));
    }

    listen(shelf, 'click', event => {
      const tile = event.target.closest('[data-template-id]');
      if (tile) openEditor(tile.dataset.templateId).catch(report);
    });
    listen(shelf, 'dragstart', event => {
      const tile = event.target.closest('[data-template-id]');
      if (!tile) return;
      dragged = tile.dataset.templateId;
      event.dataTransfer.setData(MIME, dragged);
      event.dataTransfer.effectAllowed = 'copy';
    });
    listen(shelf, 'dragend', () => { dragged = ''; clearDrop(); });
    listen(canvas, 'dragover', event => {
      if (!dragged && !Array.from(event.dataTransfer?.types || []).includes(MIME)) return;
      event.preventDefault(); event.stopPropagation();
      const item = find(dragged);
      if (!item) return;
      const target = resolveTarget(event);
      const supported = item.targets.includes(target.kind);
      clearDrop();
      canvas.classList.add(supported ? 'templateDropAllowed' : 'templateDropForbidden');
      event.target.closest('.hatgNode')?.classList.add('templateDropTarget');
      event.dataTransfer.dropEffect = supported ? 'copy' : 'none';
      dropHint.textContent = supported ? `${t('施加到')} ${targetLabels()[target.kind]}` : `⊘ ${unsupported(item)}`;
      dropHint.dataset.allowed = String(supported);
      dropHint.style.left = `${Math.min(event.clientX + 16, window.innerWidth - 300)}px`;
      dropHint.style.top = `${Math.min(event.clientY + 16, window.innerHeight - 70)}px`;
      dropHint.hidden = false;
    }, true);
    listen(canvas, 'dragleave', event => { if (!canvas.contains(event.relatedTarget)) clearDrop(); }, true);
    listen(canvas, 'drop', event => {
      const id = event.dataTransfer?.getData(MIME) || dragged;
      if (!id) return;
      event.preventDefault(); event.stopPropagation();
      const target = resolveTarget(event);
      clearDrop(); dragged = '';
      apply(id, target).catch(report);
    }, true);

    function residentRoles(template) {
      if (template.kind === 'structure') return (template.body.roles || []).filter(role => ['new', 'fork'].includes(role.from));
      return template.behavior?.roles || template.editor?.resident_roles || [];
    }

    function renderResidentSettings(root, roles, target, template) {
      const values = {};
      const context = getContext();
      const inheritedProvider = (role, visited = new Set()) => {
        if (!role || visited.has(role.key)) return '';
        visited.add(role.key);
        if (role.from === 'new') return role.provider || 'codex';
        if (role.from === 'fork' && role.fork_of) return inheritedProvider(template.body.roles.find(item => item.key === role.fork_of), visited);
        const ids = role.from === 'lineage_leader' ? [context.leader_resident_id] : target.resident_ids || [];
        const providers = new Set(ids.map(id => context.residents.find(item => item.id === id)).map(item => item?.provider || item?.backend).filter(Boolean));
        return providers.size === 1 ? [...providers][0] : '';
      };
      roles.forEach(role => {
        const fork = role.from === 'fork';
        values[role.key] = { ...(!fork ? { provider: role.provider || 'codex' } : {}), account: role.account || '', settings: clone(role.settings || {}) };
        const panel = document.createElement('fieldset');
        panel.dataset.residentRole = role.key;
        panel.className = 'templateResidentSettings';
        panel.innerHTML = `<legend>${esc(t(role.title || role.key))}</legend>${role.description ? `<p>${esc(t(role.description))}</p>` : ''}
          ${fork ? `<label>Provider<span>${esc(t('继承来源'))}</span></label>` : '<label>Provider<select data-setting="provider"></select></label>'}<label>${esc(t('账号'))}<select data-setting="account"></select></label>
          <label>${esc(t('模型'))}<select data-setting="model"></select></label>
          <label hidden>${esc(t('推理强度'))}<select data-setting="reasoning_effort"></select></label>
          <label hidden>Fast<select data-setting="service_tier">${optionsHtml([{ value: '', label: t('关') }, { value: 'priority', label: t('开') }], values[role.key].settings.service_tier || '')}</select></label>`;
        root.append(panel);
        const renderFields = () => {
          if (!panel.isConnected) return;
          const chosen = values[role.key];
          const backends = config.residentSettingsOptions?.()?.backends || [];
          const provider = panel.querySelector('[data-setting="provider"]');
          const account = panel.querySelector('[data-setting="account"]');
          const model = panel.querySelector('[data-setting="model"]');
          if (provider) {
            const providers = backends.map(item => ({ value: item.backend, label: item.label }));
            if (!providers.some(item => item.value === chosen.provider)) providers.push({ value: chosen.provider, label: chosen.provider });
            provider.innerHTML = optionsHtml(providers, chosen.provider);
            chosen.provider = provider.value;
          }
          const definition = backends.find(item => item.backend === (fork ? inheritedProvider(role) : chosen.provider)) || {};
          const routes = definition.routes || [];
          const route = routes.find(item => item.backend_route_id === chosen.account) || routes[0];
          const accounts = [{ value: '', label: t('默认') }, ...routes.map(item => ({ value: item.backend_route_id, label: item.label }))];
          if (chosen.account && !accounts.some(item => item.value === chosen.account)) accounts.push({ value: chosen.account, label: chosen.account });
          account.innerHTML = optionsHtml(accounts, chosen.account);
          chosen.account = account.value;
          const catalog = route ? window.PortalLineageCatalogs.routeRuntimeCatalog(route, renderFields) : null;
          const models = [...new Set([...(catalog?.models || []), ...(chosen.settings.model ? [chosen.settings.model] : [])])];
          model.innerHTML = optionsHtml([{ value: '', label: t('默认') }, ...models], chosen.settings.model || '');
          const fields = definition.runtime_settings || [];
          const effort = panel.querySelector('[data-setting="reasoning_effort"]');
          const tier = panel.querySelector('[data-setting="service_tier"]');
          const efforts = [...new Set([...(catalog?.efforts_by_model?.[model.value || catalog?.default_model] || []), ...(chosen.settings.reasoning_effort ? [chosen.settings.reasoning_effort] : [])])];
          effort.innerHTML = optionsHtml([{ value: '', label: t('默认') }, ...efforts], chosen.settings.reasoning_effort || '');
          tier.value = chosen.settings.service_tier || '';
          for (const input of [effort, tier]) {
            input.disabled = !fields.includes(input.dataset.setting);
            input.closest('label').hidden = input.disabled;
          }
        };
        panel.addEventListener('change', event => {
          const chosen = values[role.key];
          if (event.target.dataset.setting === 'provider') { chosen.provider = event.target.value; chosen.account = ''; chosen.settings = {}; }
          if (event.target.dataset.setting === 'account') { chosen.account = event.target.value; chosen.settings = {}; }
          if (['provider', 'account'].includes(event.target.dataset.setting)) {
            const definition = config.residentSettingsOptions?.()?.backends?.find(item => item.backend === (fork ? inheritedProvider(role) : chosen.provider));
            for (const key of definition?.runtime_settings || []) {
              if (['model', 'reasoning_effort', 'service_tier'].includes(key)) chosen.settings[key] = '';
            }
          }
          if (event.target.dataset.setting === 'model') {
            chosen.settings.model = event.target.value;
            if (!panel.querySelector('[data-setting="reasoning_effort"]').disabled) chosen.settings.reasoning_effort = '';
          }
          if (['reasoning_effort', 'service_tier'].includes(event.target.dataset.setting)) chosen.settings[event.target.dataset.setting] = event.target.value;
          renderFields();
        });
        renderFields();
      });
      return values;
    }

    async function apply(id, target) {
      const context = getContext();
      const { template } = await request('GetTemplate', { template_id: id });
      if (disposed) return;
      if (!template.targets.includes(target.kind)) { toast(unsupported(template), 'warning'); return; }
      if (!context.lineage_id) throw new Error(t('请先选择 Lineage'));
      if (context.lineage_id !== getContext().lineage_id) throw new Error(t('目标 Lineage 已切换，请重新施加'));
      const roles = residentRoles(template);
      const parameters = template.parameters || [];
      // A template that finds existing elements always shows what it would change first.
      const rewrites = template.kind === 'structure' && (template.body.match || []).length > 0;
      const choices = {};
      const send = async (values, title, settings) => {
        if (context.lineage_id !== getContext().lineage_id) throw new Error(t('目标 Lineage 已切换，请重新施加'));
        const result = await request('ApplyTemplate', { lineage_id: context.lineage_id, template_id: id, target,
          ...(parameters.length ? { parameters: values } : {}), ...(title ? { title } : {}), ...(roles.length ? { resident_settings: settings } : {}),
          ...(Object.keys(choices).length ? { match_choices: choices } : {}) });
        if (!result) return;
        toast(window.PortalI18n.message(result.summary || '模板已施加'), 'success');
        return result;
      };
      if (!parameters.length && !roles.length && !rewrites) return send({}, '', {});
      application?.close();
      const view = dialog('apply', `${t('施加')} · ${t(template.title)}`, template);
      application = view;
      view.lineageId = context.lineage_id;
      const names = (target.resident_ids || []).map(id => context.residents?.find(item => item.id === id)?.title || id);
      const summary = target.kind === 'canvas' ? `${t('画布落点')} · ${Math.round(target.x)}, ${Math.round(target.y)}` : `${targetLabels()[target.kind]} · ${names.join('、') || `${target.element_ids?.length || 0} ${t('个元素')}`}`;
      // A Family is named after a random member, as the server does when no title is given.
      const resultTitle = template.kind === 'familize' && names.length ? `${names[Math.floor(Math.random() * names.length)]}-family` : template.title;
      view.content.innerHTML = `<div class="templateApplyIntro"><p>${esc(t(template.description))}</p><section data-template-behavior>${behaviorHtml(template.behavior)}</section></div>
        <p class="templateTarget">${esc(t('目标'))}：${esc(summary)}</p>
        ${rewrites ? `<section class="templateApplyFound" data-apply-found aria-live="polite"><h3>${esc(t('施加时会改动的已有元素'))}</h3><p class="templateHelp">${esc(t('正在查找…'))}</p></section>` : ''}
        ${template.behavior.persistent ? `<label class="templateField"><span>${esc(t('结果标题'))}</span><input data-apply-title value="${esc(resultTitle)}" required></label>` : ''}
        <div class="templateApplyParameters">${parameters.map(parameter => field(parameter, parameter.default, `data-apply-parameter="${esc(parameter.name)}"`)).join('')}</div>
        ${roles.length ? `<section><h3>${esc(t('新建 Resident 的起始设置'))}</h3><div data-resident-settings></div></section>` : ''}`;
      const typedValues = () => Object.fromEntries(parameters.map(parameter => [parameter.name, readField(view.root.querySelector(`[data-apply-parameter="${CSS.escape(parameter.name)}"]`), parameter.type)]));
      const preview = rewrites ? foundPreview(view, { context, id, target, parameters, choices, values: typedValues }) : null;
      const refreshBehavior = () => {
        const values = Object.fromEntries(parameters.map(parameter => {
          const input = view.root.querySelector(`[data-apply-parameter="${CSS.escape(parameter.name)}"]`);
          return [parameter.name, parameter.type === 'boolean' ? input.checked : input.value];
        }));
        view.content.querySelector('[data-template-behavior]').innerHTML = behaviorHtml(template.behavior, values);
      };
      view.content.addEventListener('input', event => {
        if (event.target.matches('[data-apply-parameter]')) { refreshBehavior(); preview?.schedule(); }
      });
      refreshBehavior();
      preview?.schedule(0);
      const settings = roles.length ? renderResidentSettings(view.content.querySelector('[data-resident-settings]'), roles, target, template) : {};
      view.footer.innerHTML = `${button('取消', 'close')}<button type="submit" class="portalButton is-primary">${esc(t('施加'))}</button>`;
      view.form.addEventListener('submit', event => {
        event.preventDefault();
        view.busy(async () => {
          if (await send(typedValues(), view.root.querySelector('[data-apply-title]')?.value.trim() || '', settings)) view.close();
        });
      });
    }

    // Lists what applying would keep, update and delete for each target, asks for a
    // choice where one wire has several candidates, and outlines those elements on
    // the canvas while the dialog is open.
    function foundPreview(view, { context, id, target, parameters, choices, values }) {
      const section = view.content.querySelector('[data-apply-found]');
      const outline = document.head.appendChild(document.createElement('style'));
      const offered = {};
      let timer = null;
      let serial = 0;
      canvas.dataset.templatePreview = 'true';
      view.cleanups.push(() => { clearTimeout(timer); serial += 1; outline.remove(); delete canvas.dataset.templatePreview; });
      const states = { keep: t('已有'), update: t('修改'), delete: t('删除') };
      const residentTitle = identity => context.residents?.find(item => item.id === identity)?.title || identity;
      const row = (element, state) => `<li data-template-state="${state}"><span class="templateFoundState">${esc(states[state])}</span><strong>${esc(element.title)}</strong><code>${esc(element.kind)}</code></li>`;

      function render(entries) {
        const rules = [];
        section.innerHTML = `<h3>${esc(t('施加时会改动的已有元素'))}</h3>${entries.map(entry => {
          const heading = entry.target_resident_id ? `<h4>${esc(residentTitle(entry.target_resident_id))}</h4>` : '';
          if (entry.error) return `<div class="templateFoundTarget" data-state="error">${heading}<p>${esc(window.PortalI18n.message(entry.error))}</p></div>`;
          entry.ambiguous.forEach(item => { offered[item.key] ||= item.candidates; });
          const removed = new Set(entry.removed.map(item => item.id));
          const updated = new Set(entry.updated.map(item => item.id));
          const kept = entry.found.filter(item => !removed.has(item.id) && !updated.has(item.id));
          [[kept, 'keep'], [entry.updated, 'update'], [entry.removed, 'delete']].forEach(([items, state]) => items.forEach(item => {
            const color = { keep: '#7d72b3', update: '#c58a1c', delete: '#c2453b' }[state];
            rules.push(`[data-template-preview] .hatgNode[data-node-id="${CSS.escape(item.id)}"]{outline:3px dashed ${color};outline-offset:4px}`,
              `[data-template-preview] .hatgEdge[data-edge-id="${CSS.escape(item.id)}"]{stroke:${color};stroke-dasharray:6 4}`);
          }));
          const { roles = 0, components = 0, wires = 0 } = entry.created || {};
          return `<div class="templateFoundTarget">${heading}<ul>${[...kept.map(item => row(item, 'keep')), ...entry.updated.map(item => row(item, 'update')), ...entry.removed.map(item => row(item, 'delete'))].join('')}</ul>
            <p class="templateHelp">${esc(window.PortalI18n.text`另外新建 ${roles} 个 Resident、${components} 个组件、${wires} 条连线。`)}</p></div>`;
        }).join('')}`;
        const picks = Object.entries(offered).map(([key, candidates]) => {
          choices[key] ||= [candidates[0].id];
          return `<label class="templateField"><span>${esc(window.PortalI18n.text`已有连线 ${key} 有多个候选，选择一个`)}</span><select data-match-choice="${esc(key)}">${optionsHtml(candidates.map(item => ({ value: item.id, label: `${item.title} · ${item.id}` })), choices[key][0])}</select></label>`;
        }).join('');
        section.insertAdjacentHTML('beforeend', picks);
        outline.textContent = rules.join('\n');
      }

      async function refresh() {
        const current = ++serial;
        try {
          const result = await request('ApplyTemplate', { lineage_id: context.lineage_id, template_id: id, target, dry_run: true,
            ...(parameters.length ? { parameters: values() } : {}), ...(Object.keys(choices).length ? { match_choices: choices } : {}) });
          if (current !== serial || !section.isConnected) return;
          const needsChoice = Object.keys(choices).length === 0 && (result.preview || []).some(entry => entry.ambiguous?.length);
          render(result.preview || []);
          if (needsChoice) schedule(0);
        } catch (error) {
          if (current !== serial || !section.isConnected) return;
          section.innerHTML = `<h3>${esc(t('施加时会改动的已有元素'))}</h3><p class="templateError">${esc(window.PortalI18n.message(error.message || error))}</p>`;
        }
      }

      function schedule(delay = 320) { clearTimeout(timer); timer = setTimeout(refresh, delay); }
      section.addEventListener('change', event => {
        const key = event.target.dataset.matchChoice;
        if (!key) return;
        choices[key] = [event.target.value];
        schedule(0);
      });
      return { schedule };
    }

    function create(kind) {
      const defaults = {
        prompt: { message: '', activation_policy: 'queue' },
        structure: { roles: [], components: [], wires: [], function_block: true },
        script: { script: '', endpoints: ['target'] },
      };
      const metadata = kinds.find(item => item.key === kind);
      return edit({ kind, category: kind === 'structure' ? 'structure' : 'apply', title: t('新模板'), description: '',
        targets: metadata?.targets || (kind === 'prompt' ? ['resident', 'selection'] : ['resident', 'selection', 'canvas']), parameters: [], body: defaults[kind],
        behavior: { steps: [], persistent: kind === 'structure' }, editor: metadata?.editor || { fields: [] } });
    }

    function newTemplate() {
      const view = dialog('new', t('新建模板'));
      view.content.innerHTML = `<p>${esc(t('选择模板类型'))}</p><div class="templateKindChoices">${[
        ['prompt', '施加 Prompt', '给目标 Resident 投递一段消息'], ['structure', '放入结构', '组合角色、组件与连线'], ['script', '执行脚本', '执行一次 Effect 脚本'],
      ].map(([kind, title, description]) => `<button type="button" data-kind="${kind}"><strong>${esc(t(title))}</strong><small>${esc(t(description))}</small></button>`).join('')}</div>`;
      view.content.addEventListener('click', event => { const choice = event.target.closest('[data-kind]'); if (choice) { view.close(); create(choice.dataset.kind); } });
    }

    async function openEditor(id) {
      const result = await request('GetTemplate', { template_id: id });
      if (!disposed) edit(result.template);
    }

    function edit(source) {
      editor?.close();
      const draft = clone(source);
      const view = dialog('editor', `${t('模板编辑器')} · ${source.title}`, source);
      editor = view;
      view.root.querySelector('.templateSurface')?.classList.toggle('templateSurface-structure', draft.kind === 'structure');
      let timer = null;
      let revision = 0;
      let codeEditor = null;
      let codeText = '';
      let codeActive = false;
      let codeGeneration = 0;
      let graph = null;
      const templateRoles = draft.kind === 'familize' ? residentRoles(draft) : [];
      let roleSettings = null;
      view.cleanups.push(() => { clearTimeout(timer); revision += 1; codeGeneration += 1; codeEditor?.destroy(); graph?.destroy(); });
      const supported = kinds.find(item => item.key === draft.kind)?.targets || (draft.kind === 'prompt' ? ['resident', 'selection'] : ['resident', 'selection', 'canvas']);
      view.content.innerHTML = `<div class="templateMetadata">
          <label class="templateField templateTitle"><span>${esc(t('标题'))}</span><input data-meta="title" value="${esc(draft.title)}" required></label>
          <span class="templateKindBadge">${esc(draft.kind)}</span>
          <label class="templateField"><span>${esc(t('分组'))}</span><select data-meta="category">${optionsHtml(Object.entries(categories()).map(([value, label]) => ({ value, label })), draft.category)}</select></label>
          <label class="templateField templateDescription"><span>${esc(t('说明'))}</span><input data-meta="description" value="${esc(draft.description)}"></label>
          <fieldset class="templateTargets"><legend>${esc(t('支持的目标'))}</legend>${supported.map(kind => `<label><input type="checkbox" data-target="${kind}" ${draft.targets.includes(kind) ? 'checked' : ''}>${esc(targetLabels()[kind])}</label>`).join('')}</fieldset>
        </div>
        <div class="templateEditorLayout"><div class="templateEditorMain"><nav class="templateTabs" role="tablist">${[['body', '内容'], ['parameters', '参数'], ...(templateRoles.length ? [['objects', '内部对象']] : [])].map(([key, label], index) => `<button type="button" role="tab" aria-selected="${!index}" data-tab="${key}">${esc(t(label))}</button>`).join('')}</nav>
          <section data-page="body" role="tabpanel"></section><section data-page="parameters" role="tabpanel" hidden></section>${templateRoles.length ? '<section data-page="objects" role="tabpanel" hidden></section>' : ''}</div>
          <aside class="templatePreview" aria-live="polite"><section data-template-behavior>${behaviorHtml(draft.behavior)}</section><p class="templatePreviewStatus" data-preview-status></p></aside></div>`;
      view.footer.innerHTML = `${draft.template_id ? button('删除', 'delete', 'is-danger') : ''}${draft.builtin_key ? button('恢复内置', 'restore', 'is-quiet') : ''}<span class="modalActionSpacer"></span>${button('另存为副本', 'copy')}${button('取消', 'close')}<button type="submit" class="portalButton is-primary">${esc(t('保存'))}</button>`;
      const bodyPage = view.root.querySelector('[data-page="body"]');
      const parametersPage = view.root.querySelector('[data-page="parameters"]');
      const status = view.root.querySelector('[data-preview-status]');
      const disposeCode = () => { codeGeneration += 1; codeEditor?.destroy(); codeEditor = null; codeActive = false; };

      async function mountCode(root, value, language) {
        disposeCode(); codeActive = true; codeText = value;
        const generation = codeGeneration;
        try {
          const instance = await window.PortalCodeEditor.mount({ root, value, language, ariaLabel: language === 'json' ? t('结构 JSON') : t('Effect 脚本'),
            onChange: value => { codeText = value; schedulePreview(); } });
          if (generation !== codeGeneration || !root.isConnected) instance.destroy();
          else codeEditor = instance;
        } catch (error) { view.error(error); }
      }

      function readDraft() {
        view.root.querySelectorAll('[data-meta]').forEach(input => { draft[input.dataset.meta] = input.value; });
        draft.targets = [...view.root.querySelectorAll('[data-target]:checked')].map(input => input.dataset.target);
        if (roleSettings) draft.body.resident_settings = clone(roleSettings);
        if (codeActive) {
          if (draft.kind === 'structure') {
            const value = JSON.parse(codeText);
            if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error(t('结构 JSON 必须是对象'));
            draft.body = value;
          } else if (draft.kind === 'script') draft.body.script = codeText;
        }
        bodyPage.querySelectorAll('[data-body-field]').forEach(input => {
          draft.body[input.dataset.bodyField] = readField(input, input.dataset.fieldType || 'text');
        });
        bodyPage.querySelectorAll('[data-row-field]').forEach(input => {
          const row = input.closest('[data-structure-row]');
          const list = row.closest('[data-structure-list]').dataset.structureList;
          const value = readField(input, input.dataset.fieldType || 'text');
          draft.body[list][Number(row.dataset.structureRow)][input.dataset.rowField] = value;
        });
        parametersPage.querySelectorAll('[data-parameter-row]').forEach(row => {
          const parameter = draft.parameters[Number(row.dataset.parameterRow)];
          row.querySelectorAll('[data-parameter-field]').forEach(input => {
            const key = input.dataset.parameterField;
            if (key === 'required') parameter.required = input.checked;
            else if (key === 'options') parameter.options = input.value.split('\n').map(item => item.trim()).filter(Boolean);
            else if (key !== 'default') parameter[key] = input.value;
          });
          const value = row.querySelector('[data-parameter-field="default"]').value;
          if (parameter.type === 'boolean') parameter.default = value === 'true';
          else if (['integer', 'number', 'duration'].includes(parameter.type)) {
            if (value.trim()) { parameter.default = Number(value); if (!Number.isFinite(parameter.default)) throw new Error(t('请输入有效数值')); }
            else delete parameter.default;
          } else parameter.default = value;
        });
        return templatePayload(draft);
      }

      async function preview() {
        const serial = ++revision;
        try {
          const payload = readDraft();
          const result = await request(draft.template_id ? 'UpdateTemplate' : 'CreateTemplate', draft.template_id ? { template_id: draft.template_id, ...payload, dry_run: true } : { template: payload, dry_run: true });
          if (serial !== revision || !view.root.isConnected) return;
          view.root.querySelector('[data-template-behavior]').innerHTML = behaviorHtml(result.template.behavior);
          status.textContent = t('预览已更新'); status.dataset.state = 'valid';
        } catch (error) {
          if (serial !== revision || !view.root.isConnected) return;
          status.textContent = String(error.message || error); status.dataset.state = 'error';
        }
      }

      function schedulePreview() {
        revision += 1; clearTimeout(timer);
        status.textContent = t('正在校验…'); status.dataset.state = 'pending';
        timer = setTimeout(preview, 320);
      }

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
        disposeCode(); graph?.destroy(); graph = null;
        bodyPage.querySelectorAll('[data-structure-view]').forEach(item => item.setAttribute('aria-pressed', String(item.dataset.structureView === mode)));
        const content = bodyPage.querySelector('[data-structure-content]');
        if (mode === 'json') { content.innerHTML = '<div class="templateCodeEditor" data-code-editor></div>'; mountCode(content.firstElementChild, JSON.stringify(draft.body, null, 2), 'json'); return; }
        if (mode === 'list') { renderStructureLists(); return; }
        content.innerHTML = `<div class="templateGraphWorkspace"><div class="templateGraphHost" data-template-graph></div><aside class="templateGraphInspector portalHatgOverlayComponent" data-graph-inspector aria-label="${esc(t('选中元素'))}"></aside></div>${functionBlockField()}`;
        graph = window.LineageTemplateGraph.mount({ root: content.querySelector('[data-template-graph]'), body: () => draft.body,
          onBeforeChange: readDraft, onChange: schedulePreview, onSelect: renderInspector });
        renderInspector('');
      }

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
      const elementLabel = (list, row) => list === 'roles' ? t('角色')
        : list === 'wires' || (list === 'match' && isWire(row)) ? `${String(row.kind || '').toUpperCase()} WIRE`
          : row.kind === 'resident' ? 'RESIDENT' : String(row.kind || '').replace('_', ' ').toUpperCase();
      const invokeWire = row => ['route'].includes(row.kind);
      // The target and Leader roles and found nodes already exist in the Lineage, so
      // a wire between them can be one the template finds or one it adds.
      const existingEnd = key => (draft.body.roles || []).some(role => role.key === key && ['target', 'lineage_leader'].includes(role.from))
        || (draft.body.match || []).some(row => row.key === key && !isWire(row));
      const FOUND_KINDS = ['resident', 'event_source', 'effect', 'loop', 'filter', 'selector', 'reducer', 'seed'];
      const UPDATABLE_WIRES = ['route', 'target'];

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
            graph?.render();
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
            graph?.render();
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
          schedulePreview(); graph?.render(); renderInspector(row.key);
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
        schedulePreview(); graph?.render(); renderInspector(row.key);
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

      async function save(copy = false) {
        const payload = readDraft();
        if (!view.form.reportValidity()) return;
        const result = await request(draft.template_id && !copy ? 'UpdateTemplate' : 'CreateTemplate', draft.template_id && !copy ? { template_id: draft.template_id, ...payload } : { template: { ...payload, title: copy ? `${payload.title} · ${t('副本')}` : payload.title } });
        await load();
        toast(t('模板已保存'), 'success');
        view.close();
        if (copy) edit(result.template);
      }

      view.form.addEventListener('input', schedulePreview);
      view.form.addEventListener('change', schedulePreview);
      bodyPage.addEventListener('change', event => {
        const name = event.target.dataset.rowField;
        if (!name) return;
        try {
          const row = event.target.closest('[data-structure-row]');
          const list = row.closest('[data-structure-list]').dataset.structureList;
          const index = Number(row.dataset.structureRow);
          readDraft();
          const item = draft.body[list][index];
          if (name === 'key') window.LineageTemplateGraph.renameKey(draft.body, row.dataset.rowKey, item.key);
          if (name === 'from' && item.from === 'fork') delete item.provider;
          if (graph) {
            if (['key', 'from'].includes(name)) graph.select(item.key);
            else graph.render();
          } else if (['key', 'from'].includes(name)) {
            renderStructureLists();
            bodyPage.querySelector(`[data-structure-list="${list}"] [data-structure-row="${index}"]`).open = true;
          }
        } catch (error) { view.error(error); }
      });
      view.form.addEventListener('submit', event => { event.preventDefault(); view.busy(() => save()); });
      view.root.addEventListener('click', event => {
        const element = event.target.closest('button');
        if (!element) return;
        const action = element.dataset.action;
        try {
          if (element.dataset.tab) {
            readDraft();
            view.root.querySelectorAll('[data-tab]').forEach(tab => tab.setAttribute('aria-selected', String(tab === element)));
            view.root.querySelectorAll('[data-page]').forEach(page => { page.hidden = page.dataset.page !== element.dataset.tab; });
            if (element.dataset.tab === 'body' && draft.kind === 'prompt') renderBody();
            // The graph was measured while hidden; fit it again once it is visible.
            else if (element.dataset.tab === 'body') requestAnimationFrame(() => graph?.fit());
          } else if (element.dataset.structureView) {
            readDraft(); showStructure(element.dataset.structureView);
          } else if (element.dataset.insertParameter) {
            const input = bodyPage.querySelector('[data-body-field="message"]');
            input.setRangeText(`{{param.${element.dataset.insertParameter}}}`, input.selectionStart, input.selectionEnd, 'end'); input.focus(); schedulePreview();
          } else if (element.dataset.addRow) {
            readDraft(); const key = element.dataset.addRow;
            draft.body[key] ||= [];
            const base = key === 'roles' ? { from: 'new', title: '', provider: 'codex', settings: {} } : key === 'components' ? { kind: 'event_source', title: '', config: {} }
              : key === 'match' ? { kind: 'event_source', title: '', config: {}, where: {}, op: 'keep' } : { kind: 'route', source: '', target: '', config: {} };
            let index = draft.body[key].length + 1; while (draft.body[key].some(row => row.key === `${key}_${index}`)) index += 1;
            draft.body[key].push({ key: `${key}_${index}`, ...base }); renderStructureLists(); schedulePreview();
          } else if (action === 'remove-row') {
            readDraft();
            const row = element.closest('[data-structure-row]');
            const key = draft.body[row.closest('[data-structure-list]').dataset.structureList][Number(row.dataset.structureRow)].key;
            if (graph) graph.remove(key);
            else { window.LineageTemplateGraph.removeElement(draft.body, key); renderStructureLists(); schedulePreview(); }
          } else if (action === 'revert-update') {
            const row = draft.body.match.find(item => item.key === element.closest('[data-row-key]')?.dataset.rowKey);
            if (row) { row.op = 'keep'; delete row.update; schedulePreview(); graph?.render(); renderInspector(row.key); }
          } else if (action === 'add-parameter') {
            readDraft(); draft.parameters.push({ name: '', type: 'text', default: '', required: false, description: '' }); renderParameters(); schedulePreview();
          } else if (element.hasAttribute('data-remove-parameter')) {
            readDraft(); draft.parameters.splice(Number(element.dataset.removeParameter), 1); renderParameters(); schedulePreview();
          } else if (action === 'copy') view.busy(() => save(true));
          else if (action === 'delete') view.busy(async () => {
            if (!await window.PortalUI.confirm({ title: t('删除模板？'), message: t('已施加的结果会保留。'), confirmLabel: t('删除'), tone: 'danger' })) return;
            await request('DeleteTemplate', { template_id: draft.template_id }); await load(); view.close(); toast(t('模板已删除'), 'success');
          });
          else if (action === 'restore') view.busy(async () => {
            if (!await window.PortalUI.confirm({ title: t('恢复内置模板？'), message: t('当前修改将替换为内置内容。'), confirmLabel: t('恢复内置') })) return;
            const result = await request('RestoreTemplate', { template_id: draft.template_id }); await load(); view.close(); edit(result.template);
          });
        } catch (error) { view.error(error); }
      });
      renderBody(); renderParameters();
      if (templateRoles.length) {
        const objectsPage = view.root.querySelector('[data-page="objects"]');
        objectsPage.innerHTML = `<p class="templateHelp">${esc(t('设置新建 Family 时 Router 和 Steward 的默认配置，施加模板时可以调整。'))}</p>`;
        roleSettings = renderResidentSettings(objectsPage, templateRoles, {}, draft);
      }
      schedulePreview();
    }

    async function applyFamily(target) {
      if (!templates.length) await load();
      const family = templates.find(item => item.builtin_key === 'family') || templates.find(item => item.kind === 'familize');
      if (!family) throw new Error(t('Family 模板已删除，请先新建或恢复模板'));
      return apply(family.template_id, target);
    }

    // With `rewrite`, the selection becomes the existing structure the template finds.
    async function saveSelection(elementIds, { rewrite = false } = {}) {
      const result = await request('CreateTemplate', { from_selection: { lineage_id: getContext().lineage_id, element_ids: elementIds, ...(rewrite ? { rewrite: true } : {}) } });
      await load(); return openEditor(result.template.template_id);
    }

    return Object.freeze({ load, render, find, openEditor, newTemplate, apply, applyFamily, saveSelection,
      destroy() { disposed = true; abort.abort(); [...dialogs].forEach(dialog => dialog.close()); dropHint.remove(); },
    });
  }

  window.LineageTemplates = Object.freeze({ mount, pigeonPosition });
})();
