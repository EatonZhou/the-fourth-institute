(function (global) {
  "use strict";

  const SCHEMA = "portal.lineage.invocation-inspector.v1";
  const EDITOR_STATES = new WeakMap();

  function object(value) {
    return value && typeof value === "object" && !Array.isArray(value)
      ? value
      : {};
  }

  function list(value) {
    return Array.isArray(value) ? value : [];
  }

  function clone(value) {
    if (value === undefined) return undefined;
    return JSON.parse(JSON.stringify(value));
  }

  function text(value, fallback = "") {
    const normalized = String(value ?? "").trim();
    return normalized || fallback;
  }

  const escapeHtml = global.PortalUIPrimitives.escapeHtml;

  function safeId(value) {
    return text(value, "invocation")
      .replace(/[^a-zA-Z0-9_-]+/g, "-")
      .slice(0, 120);
  }

  // Names an Effect's argument table reads as invocation.<name>.
  // Arguments an Effect script reads: every ${arguments.<name>} reference (decision_4adcc1ff2f2c).
  function effectInvocationNames(config) {
    const names = new Set();
    const script = text(object(config).script);
    for (const match of script.matchAll(/\$\{arguments\.([A-Za-z0-9_-]+)/g)) names.add(match[1]);
    return [...names].filter(Boolean);
  }

  function argumentDefinitions(options = {}) {
    if (Array.isArray(options.definitions)) return clone(options.definitions);
    if (text(options.targetKind) === "effect") {
      const names = effectInvocationNames(options.effectConfig).filter(name => name !== "prompt");
      return [
        {
          name: "prompt",
          label: "Prompt",
          type: "string",
          description: window.PortalI18n.t("本次调用的输入；调用 DeliverMessage 时追加在固定提示词之后。"),
        },
        ...names.map(name => ({
          name,
          label: name,
          type: "json",
          description: window.PortalI18n.text`Effect 参数表通过 invocation.${name} 读取这个值。`,
        })),
      ];
    }
    return [{
      name: "prompt",
      label: "Prompt",
      type: "string",
      description: window.PortalI18n.t("本次 Invoke 发送给 Resident 的输入；没有覆盖时沿用触发来源。"),
    }];
  }

  function profileKey(options = {}) {
    if (text(options.profileKey)) return text(options.profileKey);
    return text(options.targetKind, "agent");
  }

  function bindingMode(binding) {
    const source = object(binding);
    if (Object.hasOwn(source, "value")) return "value";
    if (text(source.from)) return "from";
    if (Array.isArray(source.concat) && source.concat.length) return "concat";
    return "default";
  }

  function controlValue(value, type) {
    if (value === undefined) return "";
    const normalizedType = text(type, "json").toLowerCase();
    if (normalizedType === "string") return String(value ?? "");
    if (normalizedType === "boolean") return value === true ? "true" : value === false ? "false" : "";
    if (["integer", "number"].includes(normalizedType)) return String(value ?? "");
    try {
      return JSON.stringify(value, null, 2);
    } catch (_error) {
      return "";
    }
  }

  function valueControlHtml(argument, role, value, readonly) {
    const type = text(argument.type, "json").toLowerCase();
    const label = role === "fallback" ? window.PortalI18n.t("缺失时后备值") : window.PortalI18n.t("固定值");
    const attr = role === "fallback"
      ? "data-invocation-fallback-value"
      : "data-invocation-fixed-value";
    const current = controlValue(value, type);
    if (type === "boolean") {
      return window.PortalI18n.html`<label><span>${label}</span><select ${attr}${readonly ? " disabled" : ""}>
        <option value=""${current === "" ? " selected" : ""}>${role === "fallback" ? window.PortalI18n.t("不设置") : window.PortalI18n.t("请选择")}</option>
        <option value="true"${current === "true" ? " selected" : ""}>true</option>
        <option value="false"${current === "false" ? " selected" : ""}>false</option>
      </select></label>`;
    }
    if (["integer", "number"].includes(type)) {
      return window.PortalI18n.html`<label><span>${label}</span><input ${attr} type="number"${type === "integer" ? ' step="1"' : ' step="any"'} value="${escapeHtml(current)}" placeholder="${role === "fallback" ? window.PortalI18n.t("不设置") : window.PortalI18n.t("输入数值")}"${readonly ? " disabled" : ""}></label>`;
    }
    if (type === "string") {
      return window.PortalI18n.html`<label><span>${label}</span><textarea ${attr} rows="2" maxlength="16000" placeholder="${role === "fallback" ? window.PortalI18n.t("留空表示不设置") : window.PortalI18n.t("输入固定文本")}"${readonly ? " disabled" : ""}>${escapeHtml(current)}</textarea></label>`;
    }
    return window.PortalI18n.html`<label><span>${label}（JSON · ${escapeHtml(type)}）</span><textarea ${attr} rows="3" maxlength="65536" spellcheck="false" placeholder="${type === "array" ? "[]" : type === "object" ? "{}" : window.PortalI18n.t("JSON value")}"${readonly ? " disabled" : ""}>${escapeHtml(current)}</textarea></label>`;
  }

  function sourceSuggestions(argumentName, wireKind) {
    const name = text(argumentName, "value");
    const suggestions = [
      `event.payload.${name}`,
      `event.outputs.${name}`,
      "event.payload.prompt",
      "packet.id",
      "packet.key",
      "packet.message.text",
      "packet.priority",
      "packet.from_resident_id",
      "packet.source_wire_id",
      `packet.message.data.${name}`,
    ];
    if (wireKind === "route") suggestions.unshift("packet.message.text");
    return [...new Set(suggestions)];
  }

  function argumentRowHtml(argument, binding, options) {
    const name = text(argument.name);
    const mode = bindingMode(binding);
    const source = object(binding);
    const listId = `${options.editorId}-${safeId(name)}-sources`;
    return window.PortalI18n.html`<article class="programInvokeArgument" data-invocation-argument="${escapeHtml(name)}" data-argument-type="${escapeHtml(argument.type || "json")}">
      <header><code>${escapeHtml(name)}</code><span><strong>${escapeHtml(argument.label || name)}</strong><small>${escapeHtml(argument.type || "json")}</small></span></header>
      <p>${escapeHtml(argument.description || window.PortalI18n.t("本次调用传入的命令参数。"))}</p>
      <label><span>本次调用使用</span><select data-invocation-argument-mode aria-label="${escapeHtml(argument.label || name)}来源"${options.readonly ? " disabled" : ""}>
        <option value="default"${mode === "default" ? " selected" : ""}>${escapeHtml(options.defaultLabel)}</option>
        <option value="value"${mode === "value" ? " selected" : ""}>固定值</option>
        <option value="from"${mode === "from" ? " selected" : ""}>从触发数据取值</option>
        <option value="concat"${mode === "concat" ? " selected" : ""}>拼接多个片段</option>
      </select></label>
      <div class="programInvokeArgumentMode" data-invocation-mode-fields="value"${mode === "value" ? "" : " hidden"}>${valueControlHtml(argument, "value", source.value, options.readonly)}</div>
      <div class="programInvokeArgumentMode" data-invocation-mode-fields="from"${mode === "from" ? "" : " hidden"}>
        <label><span>数据路径</span><input data-invocation-source-path list="${escapeHtml(listId)}" maxlength="240" autocomplete="off" spellcheck="false" placeholder="event.payload.${escapeHtml(name)}" value="${escapeHtml(source.from || "")}"${options.readonly ? " disabled" : ""}></label>
        <datalist id="${escapeHtml(listId)}">${sourceSuggestions(name, options.wireKind).map(item => window.PortalI18n.html`<option value="${escapeHtml(item)}"></option>`).join("")}</datalist>
        ${valueControlHtml(argument, "fallback", source.default, options.readonly)}
      </div>
      <div class="programInvokeArgumentMode" data-invocation-mode-fields="concat"${mode === "concat" ? "" : " hidden"}>
        <label><span>片段（JSON array）</span><textarea data-invocation-concat-json rows="5" maxlength="65536" spellcheck="false" placeholder='[{"value":"分支契约："},{"from":"packet.message.text"}]'${options.readonly ? " disabled" : ""}>${escapeHtml(JSON.stringify(list(source.concat), null, 2))}</textarea></label>
        <label><span>片段分隔符</span><input data-invocation-concat-separator maxlength="200" value="${escapeHtml(String(source.separator ?? ""))}" placeholder="默认直接相连"${options.readonly ? " disabled" : ""}></label>
        <small>每个片段仍使用 value、from 或嵌套 concat；拼接是显式的，不会把普通字符串解释成模板。</small>
      </div>
    </article>`;
  }

  function mappingEditorHtml(options = {}) {
    const definitions = argumentDefinitions(options);
    const mapping = object(options.mapping);
    const key = profileKey(options);
    const editorId = safeId(options.editorId || key);
    const readonly = options.readonly === true;
    const defaultLabel = text(
      options.defaultLabel,
      text(options.targetKind) === "effect"
        ? window.PortalI18n.t("不覆盖（触发参数 / Effect 默认）")
        : window.PortalI18n.t("沿用触发来源"),
    );
    const rows = definitions.length
      ? definitions.map(argument => argumentRowHtml(
          argument,
          mapping[text(argument.name)],
          { editorId, readonly, wireKind: text(options.wireKind, "route"), defaultLabel },
        )).join("")
      : window.PortalI18n.html`<p class="programInvokeEmpty">这个命令没有可由 Invoke 覆盖的 Arguments；到达入口仍会触发执行。</p>`;
    return window.PortalI18n.html`<section class="programInvokeProfile" data-invocation-mapping-editor data-invocation-profile-key="${escapeHtml(key)}" data-invoke-profile-key="${escapeHtml(key)}" data-invocation-editor-id="${escapeHtml(editorId)}">
      <header><span>目标参数档案</span><code>${escapeHtml(key)}</code></header>
      <p class="programInvokeProfileHelp">逐项选择本次调用的数据来源。未覆盖的参数继续使用 Effect 配置或触发来源。</p>
      <div class="programInvokeArguments" data-invocation-argument-rows>${rows}</div>
      <details class="programInvokeAdvanced" data-invocation-advanced>
        <summary><span>高级配置</span><small>Arguments JSON · 当前逐项配置的同步投影</small></summary>
        <label><span>Arguments Mapping（JSON）</span><textarea name="invoke_argument_mapping" rows="8" maxlength="65536" spellcheck="false"${readonly ? " disabled" : ""}>${escapeHtml(JSON.stringify(mapping, null, 2))}</textarea></label>
        <p data-invocation-advanced-status>有效 JSON 会同步回上方逐项配置。</p>
      </details>
    </section>`;
  }

  function parseValue(control, type, options = {}) {
    const raw = String(control?.value ?? "");
    const normalizedType = text(type, "json").toLowerCase();
    if (options.optional && raw.trim() === "") return { ok: true, present: false };
    if (normalizedType === "string") {
      return { ok: true, present: true, value: raw };
    }
    if (normalizedType === "boolean") {
      if (!["true", "false"].includes(raw)) {
        return { ok: false, error: "请选择 true 或 false。" };
      }
      return { ok: true, present: true, value: raw === "true" };
    }
    if (["integer", "number"].includes(normalizedType)) {
      const number = Number(raw);
      if (!raw.trim() || !Number.isFinite(number)) {
        return { ok: false, error: "请输入有效数值。" };
      }
      if (normalizedType === "integer" && !Number.isInteger(number)) {
        return { ok: false, error: "请输入整数。" };
      }
      return { ok: true, present: true, value: number };
    }
    let value;
    try {
      value = JSON.parse(raw);
    } catch (_error) {
      return { ok: false, error: "请输入有效 JSON。" };
    }
    if (normalizedType === "array" && !Array.isArray(value)) {
      return { ok: false, error: "这里需要 JSON array。" };
    }
    if (
      normalizedType === "object"
      && (!value || typeof value !== "object" || Array.isArray(value))
    ) {
      return { ok: false, error: "这里需要 JSON object。" };
    }
    return { ok: true, present: true, value };
  }

  function validateBinding(rawBinding, path, depth = 0) {
    if (depth > 8) return [`${path} 的 concat 嵌套超过 8 层。`];
    const binding = object(rawBinding);
    if (binding !== rawBinding) {
      return [`${path} 必须使用 value、from 或 concat binding。`];
    }
    const modes = [
      Object.hasOwn(binding, "value"),
      Boolean(text(binding.from)),
      Array.isArray(binding.concat),
    ];
    if (modes.filter(Boolean).length !== 1) {
      return [`${path} 必须且只能选择 value、from 或 concat。`];
    }
    if (modes[1] && !text(binding.from)) {
      return [`${path} 的 from 不能为空。`];
    }
    if (Object.hasOwn(binding, "default") && !modes[1]) {
      return [`${path} 只有使用 from 时才能设置 default。`];
    }
    if (modes[2]) {
      if (!binding.concat.length || binding.concat.length > 24) {
        return [`${path} 的 concat 必须包含 1 到 24 个片段。`];
      }
      return binding.concat.flatMap((part, index) => (
        validateBinding(part, `${path}.concat[${index}]`, depth + 1)
      ));
    }
    return [];
  }

  function validateMapping(value) {
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      return ["Arguments Mapping 的最外层必须是 JSON object。"];
    }
    const errors = [];
    Object.entries(value).forEach(([name, rawBinding]) => {
      errors.push(...validateBinding(rawBinding, name));
    });
    return errors;
  }

  function setRowMode(row, mode) {
    const select = row.querySelector("[data-invocation-argument-mode]");
    if (select) select.value = mode;
    row.querySelectorAll("[data-invocation-mode-fields]").forEach(group => {
      group.hidden = group.dataset.invocationModeFields !== mode;
    });
    row.dataset.bindingMode = mode;
  }

  function setValueControl(control, value, type) {
    if (!control) return;
    control.value = controlValue(value, type);
  }

  function applyMappingToRows(editor, mapping) {
    editor.querySelectorAll("[data-invocation-argument]").forEach(row => {
      const name = text(row.dataset.invocationArgument);
      const type = text(row.dataset.argumentType, "json");
      const binding = object(mapping[name]);
      const mode = bindingMode(binding);
      setRowMode(row, mode);
      setValueControl(
        row.querySelector("[data-invocation-fixed-value]"),
        binding.value,
        type,
      );
      const source = row.querySelector("[data-invocation-source-path]");
      if (source) source.value = text(binding.from);
      setValueControl(
        row.querySelector("[data-invocation-fallback-value]"),
        binding.default,
        type,
      );
      const concat = row.querySelector("[data-invocation-concat-json]");
      if (concat) concat.value = JSON.stringify(list(binding.concat), null, 2);
      const separator = row.querySelector("[data-invocation-concat-separator]");
      if (separator) separator.value = String(binding.separator ?? "");
    });
  }

  function mappingFromRows(editor, baseMapping) {
    const mapping = {};
    const errors = [];
    editor.querySelectorAll("[data-invocation-argument]").forEach(row => {
      const name = text(row.dataset.invocationArgument);
      const type = text(row.dataset.argumentType, "json");
      const mode = text(
        row.querySelector("[data-invocation-argument-mode]")?.value,
        "default",
      );
      delete mapping[name];
      if (mode === "default") return;
      if (mode === "value") {
        const parsed = parseValue(
          row.querySelector("[data-invocation-fixed-value]"),
          type,
        );
        if (!parsed.ok) {
          errors.push(`${name}：${parsed.error}`);
          return;
        }
        mapping[name] = { value: parsed.value };
        return;
      }
      if (mode === "concat") {
        let parts;
        try {
          parts = JSON.parse(String(
            row.querySelector("[data-invocation-concat-json]")?.value || "[]",
          ));
        } catch (_error) {
          errors.push(`${name}：片段必须是有效 JSON array。`);
          return;
        }
        const binding = {
          concat: parts,
          separator: String(
            row.querySelector("[data-invocation-concat-separator]")?.value ?? "",
          ),
        };
        const bindingErrors = validateBinding(binding, name);
        if (bindingErrors.length) {
          errors.push(...bindingErrors);
          return;
        }
        mapping[name] = binding;
        return;
      }
      const from = text(
        row.querySelector("[data-invocation-source-path]")?.value,
      ).replace(/^\$/, "");
      if (!from) {
        errors.push(`${name}：请填写触发数据路径。`);
        return;
      }
      const binding = { from };
      const fallback = parseValue(
        row.querySelector("[data-invocation-fallback-value]"),
        type,
        { optional: true },
      );
      if (!fallback.ok) {
        errors.push(`${name} 后备值：${fallback.error}`);
        return;
      }
      if (fallback.present) binding.default = fallback.value;
      mapping[name] = binding;
    });
    return { mapping, errors };
  }

  function setEditorStatus(editor, errors, message = "") {
    const status = editor.querySelector("[data-invocation-advanced-status]");
    editor.dataset.invocationValid = errors.length ? "false" : "true";
    if (!status) return;
    status.dataset.status = errors.length ? "error" : "valid";
    status.textContent = errors.length
      ? errors.join(" ")
      : message || window.PortalI18n.t("逐项配置与高级 JSON 已同步。");
  }

  function bindOneEditor(editor, options = {}) {
    if (!editor) return;
    const existing = EDITOR_STATES.get(editor);
    if (existing) {
      existing.onChange = options.onChange;
      return;
    }
    const advanced = editor.querySelector('[name="invoke_argument_mapping"]');
    let initial = {};
    try {
      initial = JSON.parse(String(advanced?.value || "{}"));
    } catch (_error) {
      initial = {};
    }
    const state = {
      mapping: object(initial),
      errors: [],
      onChange: options.onChange,
    };
    EDITOR_STATES.set(editor, state);

    const syncFromRows = () => {
      const result = mappingFromRows(editor, state.mapping);
      state.errors = result.errors;
      if (result.errors.length) {
        setEditorStatus(editor, result.errors);
        return;
      }
      state.mapping = result.mapping;
      if (advanced) advanced.value = JSON.stringify(state.mapping, null, 2);
      setEditorStatus(editor, [], "逐项配置已同步到高级 JSON。");
      state.onChange?.({ source: "structured", editor, mapping: clone(state.mapping) });
    };

    editor.querySelectorAll("[data-invocation-argument-mode]").forEach(select => {
      select.addEventListener("change", () => {
        setRowMode(select.closest("[data-invocation-argument]"), select.value);
        syncFromRows();
      });
    });
    editor.querySelectorAll(
      "[data-invocation-fixed-value], [data-invocation-source-path], [data-invocation-fallback-value], [data-invocation-concat-json], [data-invocation-concat-separator]",
    ).forEach(control => {
      control.addEventListener("input", syncFromRows);
      control.addEventListener("change", syncFromRows);
    });
    advanced?.addEventListener("input", () => {
      let parsed;
      try {
        parsed = JSON.parse(String(advanced.value || "{}"));
      } catch (_error) {
        state.errors = ["Arguments Mapping 必须是有效 JSON object。"];
        setEditorStatus(editor, state.errors);
        return;
      }
      const errors = validateMapping(parsed);
      state.errors = errors;
      if (errors.length) {
        setEditorStatus(editor, errors);
        return;
      }
      state.mapping = clone(parsed);
      applyMappingToRows(editor, state.mapping);
      setEditorStatus(editor, [], "有效 JSON 已同步到上方逐项配置。");
      state.onChange?.({ source: "json", editor, mapping: clone(state.mapping) });
    });
    advanced?.addEventListener("blur", () => {
      if (!state.errors.length) advanced.value = JSON.stringify(state.mapping, null, 2);
    });
    applyMappingToRows(editor, state.mapping);
    setEditorStatus(editor, []);
  }

  function bindMappingEditor(root, options = {}) {
    const editors = root?.matches?.("[data-invocation-mapping-editor]")
      ? [root]
      : [...(root?.querySelectorAll?.("[data-invocation-mapping-editor]") || [])];
    editors.forEach(editor => bindOneEditor(editor, options));
  }

  function readMapping(root) {
    const editor = root?.matches?.("[data-invocation-mapping-editor]")
      ? root
      : root?.querySelector?.("[data-invocation-mapping-editor]");
    if (!editor) return { mapping: {}, errors: [] };
    const state = EDITOR_STATES.get(editor);
    if (!state) bindOneEditor(editor);
    const current = EDITOR_STATES.get(editor);
    if (current.errors.length) {
      return { mapping: clone(current.mapping), errors: [...current.errors] };
    }
    const result = mappingFromRows(editor, current.mapping);
    current.errors = result.errors;
    if (!result.errors.length) current.mapping = result.mapping;
    setEditorStatus(editor, result.errors);
    return { mapping: clone(current.mapping), errors: [...result.errors] };
  }

  global.PortalLineageInvocationInspector = Object.freeze({
    schema: SCHEMA,
    profileKey,
    mappingEditorHtml,
    bindMappingEditor,
    readMapping,
  });
})(window);
