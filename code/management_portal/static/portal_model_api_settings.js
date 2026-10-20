/* Direct Model API fields used by the shared Resident Settings dialog. */
(() => {
  const html = (...args) => window.PortalI18n.html(...args);
  const t = value => window.PortalI18n.t(value);
  const clone = value => JSON.parse(JSON.stringify(value));
  const object = (text, label) => {
    let value;
    try { value = JSON.parse(text || '{}'); } catch { throw new Error(`${label}：请输入有效 JSON。`); }
    if (!value || Array.isArray(value) || typeof value !== 'object') throw new Error(`${label}：需要 JSON 对象。`);
    return value;
  };
  function create() {
    const element = document.createElement('div');
    element.className = 'portalModelApiSettings';
    element.hidden = true;
    element.innerHTML = html`<fieldset><legend>上下文</legend>
      <label class="portalApiHistory"><input type="checkbox" data-api-history />携带会话历史</label>
      <label class="portalRuntimeSettingsField"><span>历史预算（估算 token）</span><input data-api-budget type="number" min="256" max="200000" step="1" /></label>
      <p class="portalRuntimeSettingsHint">只携带预算内最近的完整问答，较早轮次会省略。使用保守估算，不等同于账单 token 数。关闭后仍发送固定指令及当前输入；独立调用始终隔离。</p>
      <details><summary>固定上下文</summary>
        <label class="portalRuntimeSettingsField"><span>系统提示词</span><textarea data-api-system rows="3"></textarea></label>
        <label class="portalRuntimeSettingsField"><span>预设消息（JSON 数组）</span><textarea data-api-presets rows="3" spellcheck="false"></textarea></label>
      </details>
    </fieldset>
    <fieldset data-api-generation><legend>模型参数</legend><div class="portalRuntimeSettingsGrid" data-api-parameters></div>
      <p class="portalRuntimeSettingsHint">留空使用 API 默认值。仅展示协议通用参数或模型目录明确声明的参数。</p>
    </fieldset>
    <fieldset data-api-jev><legend>Jev 判定</legend>
      <p class="portalRuntimeSettingsHint">当前输入、固定指令及选中的历史组成判定 state。每个问题返回 Choice、Score 或 Noul 结果。</p>
      <div data-jev-questions></div><button type="button" class="portalButton" data-jev-add>添加问题</button>
    </fieldset>
    <fieldset><legend>高级</legend><div class="portalRuntimeSettingsGrid">
      <label class="portalRuntimeSettingsField"><span>请求超时（秒）</span><input data-api-timeout type="number" min="1" step="any" /></label>
      <label class="portalRuntimeSettingsField"><span>请求重试次数</span><input data-api-retries type="number" min="0" max="10" step="1" /></label>
    </div><details data-api-extra><summary>其他生成参数（JSON）</summary><textarea data-api-extra-parameters rows="4" spellcheck="false"></textarea></details></fieldset>`;
    const find = selector => element.querySelector(selector);
    let settings = {}, catalog = {}, originalParameters = {}, controls = [];
    const parameterMeta = {
      temperature: ['Temperature', 'number', 0, 2], top_p: ['Top P', 'number', 0, 1],
      max_tokens: ['最大输出 token', 'number', 1, 2147483647], max_completion_tokens: ['最大 completion token', 'number', 1, 2147483647],
      presence_penalty: ['Presence penalty', 'number', -2, 2], frequency_penalty: ['Frequency penalty', 'number', -2, 2],
      seed: ['Seed', 'number'], thinking: ['Thinking', 'thinking'], reasoning_effort: ['Effort', 'effort'],
    };
    function addQuestion(id = '', question = {type: 'noul', instructions: ''}) {
      const row = document.createElement('fieldset');
      row.dataset.jevQuestion = '';
      row.innerHTML = html`<legend>问题</legend><div class="portalRuntimeSettingsGrid">
        <label class="portalRuntimeSettingsField"><span>问题 ID</span><input data-jev-id maxlength="120" required /></label>
        <label class="portalRuntimeSettingsField"><span>类型</span><select data-jev-type><option value="noul">Noul</option><option value="choice">Choice</option><option value="score">Score</option></select></label>
        <label class="portalRuntimeSettingsField"><span>指令格式</span><select data-jev-format><option value="text">文本</option><option value="json">JSON</option></select></label>
      </div><label class="portalRuntimeSettingsField"><span>判定指令</span><textarea data-jev-instructions rows="2" required></textarea></label>
      <label class="portalRuntimeSettingsField"><span data-jev-criteria-label></span><textarea data-jev-criteria rows="3" spellcheck="false"></textarea></label>
      <button type="button" class="portalButton" data-jev-remove>删除问题</button>`;
      const q = sel => row.querySelector(sel);
      q('[data-jev-id]').value = id;
      q('[data-jev-type]').value = question.type;
      const structured = typeof question.instructions !== 'string';
      q('[data-jev-format]').value = structured ? 'json' : 'text';
      q('[data-jev-instructions]').value = structured ? JSON.stringify(question.instructions, null, 2) : question.instructions;
      q('[data-jev-criteria]').value = question.criteria === undefined ? '' : JSON.stringify(question.criteria, null, 2);
      const labels = {
        choice: '选项与标准（JSON 对象，如 {"approve":"符合要求","reject":"不符合要求"}）',
        score: '评分等级（JSON 数组，按低到高排列，2–10 级）',
        noul: '真／假标准（可选 JSON 对象，使用 true 和 false 两个键）',
      };
      const sync = () => { q('[data-jev-criteria-label]').textContent = t(labels[q('[data-jev-type]').value]); };
      q('[data-jev-type]').addEventListener('change', sync);
      q('[data-jev-remove]').addEventListener('click', () => row.remove());
      sync(); find('[data-jev-questions]').append(row);
    }
    find('[data-jev-add]').addEventListener('click', () => addQuestion());
    find('[data-api-history]').addEventListener('change', () => { find('[data-api-budget]').disabled = !find('[data-api-history]').checked; });
    function syncModel(model) {
      if (element.hidden) return;
      // Retain edits while switching models; compatibility is checked on save.
      for (const input of controls) {
        const key = input.dataset.apiParameter;
        if (!input.value) delete originalParameters[key];
        else originalParameters[key] = input.type === 'number' ? Number(input.value)
          : key === 'thinking' ? {type: input.value} : input.value;
      }
      const selected = model || catalog.default_model;
      const declared = catalog.api_settings.parameters_by_model?.[selected] || [];
      find('[data-api-parameters]').replaceChildren();
      controls = [];
      for (const key of declared) {
        if (!parameterMeta[key]) continue;
        const [label, kind, min, max] = parameterMeta[key];
        const field = document.createElement('label'); field.className = 'portalRuntimeSettingsField';
        const span = document.createElement('span'); span.textContent = t(label);
        const input = document.createElement(kind === 'number' ? 'input' : 'select');
        input.dataset.apiParameter = key;
        if (kind === 'number') {
          input.type = 'number'; input.step = ['seed','max_tokens','max_completion_tokens'].includes(key) ? '1' : 'any';
          if (min !== undefined) input.min = min;
          if (max !== undefined) input.max = max;
        } else {
          const choices = kind === 'thinking' ? ['enabled', 'disabled'] : catalog.api_settings.reasoning_efforts_by_model?.[selected] || [];
          for (const value of ['', ...choices]) input.add(new Option(value || t('API 默认'), value));
        }
        input.value = key === 'thinking' ? originalParameters[key]?.type || '' : originalParameters[key] ?? '';
        field.append(span, input); find('[data-api-parameters]').append(field); controls.push(input);
      }
    }
    function read() {
      if (element.hidden) return {};
      const history = {enabled: find('[data-api-history]').checked, max_tokens: Number(find('[data-api-budget]').value)};
      if (!Number.isInteger(history.max_tokens) || history.max_tokens < 256 || history.max_tokens > 200000) throw new Error('历史预算必须是 256–200000 的整数。');
      let presets;
      try { presets = JSON.parse(find('[data-api-presets]').value || '[]'); } catch { throw new Error('预设消息：请输入有效 JSON 数组。'); }
      if (!Array.isArray(presets)) throw new Error('预设消息必须是 JSON 数组。');
      const parameters = catalog.api_settings.view === 'jev' ? {} : object(find('[data-api-extra-parameters]').value, '其他生成参数');
      if (catalog.api_settings.view === 'jev') {
        const questions = {};
        for (const row of element.querySelectorAll('[data-jev-question]')) {
          const val = key => row.querySelector(`[data-jev-${key}]`).value.trim();
          const id = val('id');
          if (!id || Object.hasOwn(questions, id)) throw new Error('问题 ID 不能为空或重复。');
          let instructions = val('instructions');
          if (val('format') === 'json') {
            try { instructions = JSON.parse(instructions); } catch { throw new Error(`${id}：指令 JSON 无效。`); }
          }
          if (!instructions) throw new Error(`${id}：需要判定指令。`);
          const question = {type: val('type'), instructions};
          if (val('criteria')) {
            try { question.criteria = JSON.parse(val('criteria')); } catch { throw new Error(`${id}：标准 JSON 无效。`); }
          }
          const criteria = question.criteria;
          if (question.type === 'choice' && (!criteria || Array.isArray(criteria) || typeof criteria !== 'object' || !Object.keys(criteria).length)) throw new Error(`${id}：Choice 需要选项对象。`);
          if (question.type === 'score' && (!Array.isArray(criteria) || criteria.length < 2 || criteria.length > 10)) throw new Error(`${id}：Score 需要 2–10 个有序等级。`);
          if (question.type === 'noul' && criteria !== undefined && (!criteria || Array.isArray(criteria) || Object.keys(criteria).sort().join(',') !== 'false,true')) throw new Error(`${id}：Noul 标准需要 true 和 false 两个键。`);
          Object.defineProperty(questions, id, {value: question, enumerable: true});
        }
        if (!Object.keys(questions).length) throw new Error('Jev 至少需要一个问题。');
        parameters.questions = questions;
      } else {
        for (const input of controls) {
          const key = input.dataset.apiParameter;
          if (!input.value) delete parameters[key];
          else parameters[key] = input.type === 'number' ? Number(input.value) : key === 'thinking' ? {type: input.value} : input.value;
        }
      }
      return {
        prompt_context: {...(settings.prompt_context || {}), history, system_prompt: find('[data-api-system]').value, messages: presets},
        backend_options: {parameters, timeout_seconds: Number(find('[data-api-timeout]').value), max_retries: Number(find('[data-api-retries]').value)},
      };
    }
    return {element, syncModel, read, open(value, runtimeCatalog) {
      settings = clone(value); catalog = runtimeCatalog || {};
      element.hidden = !catalog.api_settings;
      element.querySelectorAll('input, select, textarea').forEach(input => { input.disabled = element.hidden; });
      controls = [];
      if (element.hidden) return;
      const policy = settings.prompt_context?.history || {};
      find('[data-api-history]').checked = !!policy.enabled;
      find('[data-api-budget]').value = policy.max_tokens ?? catalog.api_settings.history_max_tokens;
      find('[data-api-budget]').disabled = !policy.enabled;
      find('[data-api-system]').value = settings.prompt_context?.system_prompt || '';
      find('[data-api-presets]').value = JSON.stringify(settings.prompt_context?.messages || [], null, 2);
      find('[data-api-timeout]').max = catalog.api_settings.timeout_max;
      find('[data-api-timeout]').value = settings.backend_options?.timeout_seconds ?? 120;
      find('[data-api-retries]').value = settings.backend_options?.max_retries ?? 0;
      originalParameters = clone(settings.backend_options?.parameters || {});
      const jev = catalog.api_settings.view === 'jev';
      find('[data-api-jev]').hidden = !jev;
      find('[data-api-generation]').hidden = jev;
      find('[data-api-extra]').hidden = jev;
      find('[data-jev-questions]').replaceChildren();
      if (jev) for (const [id, question] of Object.entries(originalParameters.questions || {})) addQuestion(id, question);
      const declared = catalog.api_settings.parameters_by_model?.[settings.model || catalog.default_model] || [];
      find('[data-api-extra-parameters]').value = JSON.stringify(Object.fromEntries(Object.entries(originalParameters).filter(([key]) => !declared.includes(key) || !parameterMeta[key])), null, 2);
      syncModel(settings.model);
    }};
  }
  window.PortalModelApiSettings = {create};
})();
