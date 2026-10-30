(() => {
  'use strict';

  if (window.LineageTemplatesResidentSettings) return;

  // The Residents a template creates: which of its roles start new
  // Residents, and the Provider, account, model and runtime fields that set
  // up each one.

  const DEPENDENCIES = Object.freeze([
    'clone', 'config', 'esc', 'getContext', 'optionsHtml', 't',
  ]);
  // Fields of mount's config, which a caller may leave out.
  const OPTIONAL = Object.freeze(['getContext']);

  function createResidentSettings(dependencies) {
    const missing = DEPENDENCIES.filter(name => !OPTIONAL.includes(name) && dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`LineageTemplatesResidentSettings is missing: ${missing.join(', ')}`);
    const {
      clone, config, esc, getContext, optionsHtml, t,
    } = dependencies;

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

    return {
      renderResidentSettings, residentRoles,
    };
  }

  window.LineageTemplatesResidentSettings = Object.freeze({ createResidentSettings, dependencies: DEPENDENCIES });
})();
