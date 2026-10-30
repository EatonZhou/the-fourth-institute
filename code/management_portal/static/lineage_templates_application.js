(() => {
  'use strict';

  if (window.LineageTemplatesApplication) return;

  // Applying a template to a target: the form for its parameters, title and
  // Resident settings, the preview of the existing elements it would change,
  // and applying the Family template.

  const DEPENDENCIES = Object.freeze([
    'behaviorHtml', 'button', 'canvas', 'dialog', 'esc', 'field', 'getContext', 'load', 'optionsHtml', 'readField',
    'renderResidentSettings', 'request', 'residentRoles', 't', 'targetLabels', 'templatesState', 'toast',
    'unsupported',
  ]);
  // Fields of mount's config, which a caller may leave out.
  const OPTIONAL = Object.freeze(['canvas', 'getContext', 'request', 'toast']);

  function createApplication(dependencies) {
    const missing = DEPENDENCIES.filter(name => !OPTIONAL.includes(name) && dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`LineageTemplatesApplication is missing: ${missing.join(', ')}`);
    const {
      behaviorHtml, button, canvas, dialog, esc, field, getContext, load, optionsHtml, readField,
      renderResidentSettings, request, residentRoles, t, targetLabels, templatesState, toast, unsupported,
    } = dependencies;

    async function apply(id, target) {
      const context = getContext();
      const { template } = await request('GetTemplate', { template_id: id });
      if (templatesState.disposed) return;
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
      templatesState.application?.close();
      const view = dialog('apply', `${t('施加')} · ${t(template.title)}`, template);
      templatesState.application = view;
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

    async function applyFamily(target) {
      if (!templatesState.templates.length) await load();
      const family = templatesState.templates.find(item => item.builtin_key === 'family') || templatesState.templates.find(item => item.kind === 'familize');
      if (!family) throw new Error(t('Family 模板已删除，请先新建或恢复模板'));
      return apply(family.template_id, target);
    }

    return {
      apply, applyFamily,
    };
  }

  window.LineageTemplatesApplication = Object.freeze({ createApplication, dependencies: DEPENDENCIES });
})();
