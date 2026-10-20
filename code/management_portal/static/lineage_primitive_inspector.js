(function (global) {
  "use strict";

  const SCHEMA = "portal.lineage.primitive-inspector.v1";
  const TYPE_PICKER_CALLBACKS = new WeakMap();
  const INSPECTOR_CONTEXTS = new WeakMap();
  const ADVANCED_CONFIG_VALUES = new WeakMap();

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

  function manifest() {
    return global.PortalLineageProgram?.manifest || {};
  }

  function catalog() {
    const source = manifest();
    // Only Event Sources have typed configuration fields.
    return {
      definitions: object(source.event_types),
      categories: object(source.event_type_categories),
    };
  }

  function definition(typeId) {
    return object(catalog().definitions[text(typeId).toLowerCase()]);
  }

  function fields(typeId, options = {}) {
    return global.PortalLineageProgram.eventFields(typeId)
      .filter(field => options.includeAdvanced || !field.advanced_only);
  }

  function groups() {
    const source = catalog();
    const grouped = new Map(
      Object.entries(source.categories).map(([id, category]) => [
        id,
        { id, ...object(category), options: [] },
      ]),
    );
    Object.entries(source.definitions).forEach(([value, item]) => {
      const entry = object(item);
      const categoryId = text(entry.category, "other");
      if (!grouped.has(categoryId)) {
        grouped.set(categoryId, {
          id: categoryId,
          title: window.PortalI18n.t("其他"),
          description: window.PortalI18n.t("其他可用类型。"),
          options: [],
        });
      }
      grouped.get(categoryId).options.push({ value, ...entry });
    });
    return [...grouped.values()].filter(group => group.options.length);
  }

  function categoryIdForType(typeId) {
    const availableGroups = groups();
    const configured = text(definition(typeId).category);
    return availableGroups.some(group => group.id === configured)
      ? configured
      : text(availableGroups[0]?.id);
  }

  function categoryOptionsHtml(selectedCategoryId) {
    return groups().map(group => (
      window.PortalI18n.html`<option value="${escapeHtml(group.id)}"${group.id === selectedCategoryId ? " selected" : ""}>${escapeHtml(group.title || group.id)}</option>`
    )).join("");
  }

  function childTypeOptionsHtml(selectedType, selectedCategoryId) {
    return groups().flatMap(group => group.options.map(option => (
      window.PortalI18n.html`<option value="${escapeHtml(option.value)}" data-primitive-type-category-id="${escapeHtml(group.id)}"${group.id === selectedCategoryId ? "" : " hidden"}${option.value === selectedType ? " selected" : ""}>${escapeHtml(option.title || option.value)}</option>`
    ))).join("");
  }

  function typeDetailHtml(value) {
    const source = catalog();
    const item = object(source.definitions[value]);
    const category = object(source.categories[item.category]);
    return window.PortalI18n.html`<span>${escapeHtml(category.title || window.PortalI18n.t("其他"))}</span><strong>${escapeHtml(item.title || value)}</strong><p>${escapeHtml(item.details || item.description || window.PortalI18n.t("暂无说明。"))}</p>`;
  }

  function typePickerHtml(name, selectedType, readonly = false, attrs = "") {
    const selectedCategoryId = categoryIdForType(selectedType);
    const selectedCategory = groups().find(
      group => group.id === selectedCategoryId,
    ) || {};
    return window.PortalI18n.html`<div class="programTypeField" data-primitive-type-field>
      <div class="programTypePicker" data-primitive-type-picker ${attrs}>
        <div class="programTypeHierarchy">
          <label><span>${window.PortalI18n.t("事件大类")}</span>
            <select data-primitive-type-category aria-label="${window.PortalI18n.t("事件大类")}"${readonly ? " disabled" : ""}>${categoryOptionsHtml(selectedCategoryId)}</select>
            <small data-primitive-category-description>${escapeHtml(selectedCategory.description || "")}</small>
          </label>
          <label><span>${window.PortalI18n.t("具体事件")}</span>
            <select class="programTypeNativeSelect" data-primitive-type-item name="${escapeHtml(name)}" aria-label="${window.PortalI18n.t("具体事件")}" required${readonly ? " disabled" : ""}>
              <option value="" disabled>请选择${window.PortalI18n.t("具体事件")}</option>
              ${childTypeOptionsHtml(selectedType, selectedCategoryId)}
            </select>
          </label>
        </div>
        <aside class="programTypeDetail" data-program-type-detail>${typeDetailHtml(selectedType)}</aside>
      </div>
    </div>`;
  }

  function bindTypePicker(picker, options = {}) {
    if (!picker) return;
    if (typeof options.onChange === "function") {
      TYPE_PICKER_CALLBACKS.set(picker, options.onChange);
    }
    if (picker.dataset.primitiveTypePickerBound === "true") return;
    picker.dataset.primitiveTypePickerBound = "true";
    const categorySelect = picker.querySelector("[data-primitive-type-category]");
    const select = picker.querySelector("[data-primitive-type-item]");
    const categoryDescription = picker.querySelector(
      "[data-primitive-category-description]",
    );
    const detail = picker.querySelector("[data-program-type-detail]");
    const configSlot = picker.closest("[data-primitive-inspector]")
      ?.querySelector("[data-primitive-config-slot]");
    const availableGroups = groups();
    const showDetail = value => {
      if (detail) detail.innerHTML = typeDetailHtml(value);
    };
    const showCategoryPrompt = categoryId => {
      const category = availableGroups.find(group => group.id === categoryId) || {};
      if (detail) {
        detail.innerHTML = `<span>${escapeHtml(category.title || categoryId)}</span><strong>请选择具体事件</strong><p>${escapeHtml(category.description || "")}</p>`;
      }
    };
    const filterChildren = (categoryId, preserveSelection = false) => {
      const category = availableGroups.find(group => group.id === categoryId) || {};
      if (categoryDescription) {
        categoryDescription.textContent = category.description || "";
      }
      select?.querySelectorAll("option[data-primitive-type-category-id]").forEach(option => {
        option.hidden = option.dataset.primitiveTypeCategoryId !== categoryId;
      });
      const currentValue = text(select?.value);
      if (
        !preserveSelection
        || categoryIdForType(currentValue) !== categoryId
      ) {
        if (select) select.value = "";
        showCategoryPrompt(categoryId);
        return;
      }
      showDetail(currentValue);
    };
    categorySelect?.addEventListener("change", () => {
      filterChildren(text(categorySelect.value));
      if (configSlot) configSlot.hidden = true;
    });
    select?.addEventListener("change", () => {
      const value = text(select.value);
      if (!value) return;
      const categoryId = categoryIdForType(value);
      if (categorySelect) categorySelect.value = categoryId;
      filterChildren(categoryId, true);
      if (configSlot) configSlot.hidden = false;
      TYPE_PICKER_CALLBACKS.get(picker)?.(value, select);
    });
    const initialCategoryId = categoryIdForType(text(select?.value));
    if (categorySelect) categorySelect.value = initialCategoryId;
    filterChildren(initialCategoryId, true);
    if (configSlot) configSlot.hidden = false;
  }

  function pathKey(path) {
    return text(path).replace(/^config\./, "");
  }

  function fieldName(field) {
    return text(field.name, pathKey(field.path));
  }

  function namedControl(root, name) {
    return root?.elements?.namedItem?.(name)
      || root?.querySelector?.(`[name="${CSS.escape(name)}"]`)
      || null;
  }

  function fieldActiveForConfig(typeId, field, config) {
    const visibility = object(field.visible_when);
    if (!visibility.path) return true;
    const dependencyKey = pathKey(visibility.path);
    const dependency = fields(typeId, { includeAdvanced: true })
      .find(item => pathKey(item.path) === dependencyKey);
    const value = Object.hasOwn(object(config), dependencyKey)
      ? config[dependencyKey]
      : clone(dependency?.default);
    return String(value ?? "") === String(visibility.equals ?? "");
  }

  function configValue(config, field) {
    const key = pathKey(field.path);
    if (Object.hasOwn(object(config), key)) return config[key];
    return clone(field.default);
  }

  function dateTimeLocalValue(value) {
    if (!value) return "";
    const parsed = new Date(value);
    if (!Number.isFinite(parsed.getTime())) return text(value).slice(0, 16);
    const local = new Date(parsed.getTime() - parsed.getTimezoneOffset() * 60000);
    return local.toISOString().slice(0, 16);
  }

  function optionsForField(field, config) {
    const dynamic = object(field.options_by);
    if (dynamic.path) {
      const sourceValue = object(config)[pathKey(dynamic.path)];
      return list(object(dynamic.values)[sourceValue]);
    }
    return list(field.options);
  }

  function fieldAttributes(field, readonly) {
    return [
      field.required ? " required" : "",
      field.max_length ? ` maxlength="${Number(field.max_length)}"` : "",
      field.minimum !== undefined ? ` min="${Number(field.minimum)}"` : "",
      field.maximum !== undefined ? ` max="${Number(field.maximum)}"` : "",
      field.step !== undefined ? ` step="${Number(field.step)}"` : "",
      field.placeholder ? ` placeholder="${escapeHtml(field.placeholder)}"` : "",
      readonly ? " disabled" : "",
    ].join("");
  }

  function standardFieldHtml(field, config, readonly) {
    const name = fieldName(field);
    const value = configValue(config, field);
    const attrs = fieldAttributes(field, readonly);
    const help = field.help ? window.PortalI18n.html`<small>${escapeHtml(field.help)}</small>` : "";
    const visibility = field.visible_when
      ? ` data-visible-path="${escapeHtml(field.visible_when.path)}" data-visible-equals="${escapeHtml(field.visible_when.equals)}"`
      : "";
    const common = `data-primitive-field data-field-path="${escapeHtml(field.path)}" data-field-type="${escapeHtml(field.type)}" data-field-required="${field.required ? "true" : "false"}"${visibility}`;
    if (field.type === "checkbox") {
      return window.PortalI18n.html`<label class="programInspectorCheckbox primitiveInspectorCheckbox" ${common}><input name="${escapeHtml(name)}" type="checkbox"${value ? " checked" : ""}${readonly ? " disabled" : ""}><span>${escapeHtml(field.label)}</span>${help}</label>`;
    }
    if (field.type === "optional-limit") {
      const enabled = value !== null && value !== undefined;
      return window.PortalI18n.html`<div class="primitiveOptionalLimit" ${common}>
        <label class="programInspectorCheckbox"><input name="${escapeHtml(name)}_enabled" type="checkbox"${enabled ? " checked" : ""}${readonly ? " disabled" : ""}><span>${escapeHtml(field.enabled_label || field.label)}</span></label>
        <label data-optional-limit-value${enabled ? "" : " hidden"}><span>${escapeHtml(field.label)}</span><input name="${escapeHtml(name)}" type="number" value="${escapeHtml(enabled ? value : 1)}"${attrs}>${help}</label>
      </div>`;
    }
    if (field.type === "resident-set") {
      const rendered = list(value).map(item => String(item || "").trim())
        .filter(Boolean).join("\n");
      return window.PortalI18n.html`<label ${common}><span>${escapeHtml(field.label)}</span><textarea name="${escapeHtml(name)}" rows="4" spellcheck="false" placeholder="resident_…（每行一个）"${attrs}>${escapeHtml(rendered)}</textarea>${help}</label>`;
    }
    if (field.type === "select") {
      const options = optionsForField(field, config);
      const notes = list(field.value_notes).map(note => {
        const attribute = /^data-[a-z0-9-]+$/.test(text(note.data_attribute))
          ? ` ${text(note.data_attribute)}`
          : "";
        return window.PortalI18n.html`<small data-primitive-value-note data-value-note-path="${escapeHtml(field.path)}" data-value-note-equals="${escapeHtml(note.equals)}"${attribute}>${escapeHtml(note.text || "")}</small>`;
      }).join("");
      return window.PortalI18n.html`<label ${common}><span>${escapeHtml(field.label)}</span><select name="${escapeHtml(name)}" data-options-by-path="${escapeHtml(field.options_by?.path || "")}"${attrs}>${options.map(item => window.PortalI18n.html`<option value="${escapeHtml(item.value)}"${String(item.value) === String(value ?? "") ? " selected" : ""}>${escapeHtml(item.label || item.value)}</option>`).join("")}</select>${help}${notes}</label>`;
    }
    if (["textarea", "json", "json-array", "json-object"].includes(field.type)) {
      const rendered = field.type.startsWith("json")
        ? JSON.stringify(value === undefined ? field.default ?? null : value, null, 2)
        : String(value ?? "");
      return window.PortalI18n.html`<label ${common}><span>${escapeHtml(field.label)}</span><textarea name="${escapeHtml(name)}" rows="${Number(field.rows || (field.type.startsWith("json") ? 6 : 4))}" spellcheck="${field.type.startsWith("json") ? "false" : "true"}"${attrs}>${escapeHtml(rendered)}</textarea>${help}</label>`;
    }
    const inputType = field.type === "number"
      ? "number"
      : field.type === "datetime-local"
        ? "datetime-local"
        : "text";
    const rendered = field.type === "datetime-local"
      ? dateTimeLocalValue(value)
      : String(value ?? "");
    return window.PortalI18n.html`<label ${common}><span>${escapeHtml(field.label)}</span><input name="${escapeHtml(name)}" type="${inputType}" value="${escapeHtml(rendered)}"${field.type === "resident-id" ? ' pattern="resident_[0-9a-f]{12}" spellcheck="false"' : ""}${attrs}>${help}</label>`;
  }

  function configEditorHtml(options = {}) {
    const kind = "event";
    const typeId = text(options.typeId);
    const configTypeKey = "event_type";
    const allowed = new Set([
      configTypeKey,
      ...fields(typeId, { includeAdvanced: true })
        .filter(field => fieldActiveForConfig(typeId, field, options.config))
        .map(field => text(field.path))
        .filter(path => path.startsWith("config."))
        .map(pathKey),
    ]);
    const config = Object.fromEntries(
      Object.entries(object(options.config))
        .filter(([key]) => allowed.has(key)),
    );
    config[configTypeKey] = typeId;
    const readonly = Boolean(options.readonly);
    const item = definition(typeId);
    const renderedFields = fields(typeId, { includeAdvanced: false })
      .map(field => {
        if (field.type === "watch-reference" || (
          field.type === "resident-set" && options.renderExternalField
        )) {
          return options.renderExternalField?.(field, {
            kind,
            typeId,
            config,
            readonly,
            definition: item,
          }) || window.PortalI18n.html`<p class="primitiveInspectorExternalHint" data-primitive-external-field="${escapeHtml(field.path)}">${escapeHtml(field.label)}由画布中的 Reference 连线绑定。</p>`;
        }
        return standardFieldHtml(field, config, readonly);
      })
      .join("");
    const advanced = options.includeAdvanced === false
      ? ""
      : window.PortalI18n.html`<details class="primitiveAdvancedConfig" data-primitive-advanced>
          <summary><span>高级配置</span><small>JSON · 当前配置的同步投影</small></summary>
          <label class="primitiveAdvancedJsonField"><span>配置 JSON</span>
            <textarea name="primitive_config_json" class="primitiveConfigJson" rows="10" spellcheck="false"${readonly ? " disabled" : ""}>${escapeHtml(JSON.stringify(config, null, 2))}</textarea>
          </label>
          <small data-primitive-edit-source>普通字段与这里编辑的是同一份配置；有效 JSON 会立即投影回普通字段。</small>
          <small class="primitiveAdvancedError" data-primitive-advanced-error role="status" hidden></small>
        </details>`;
    return window.PortalI18n.html`<section class="primitiveConfigEditor" data-primitive-config-editor data-primitive-kind="${kind}" data-primitive-type="${escapeHtml(typeId)}" data-primitive-readonly="${readonly ? "true" : "false"}">
      <div class="primitiveStructuredFields" data-primitive-structured-fields>${renderedFields || window.PortalI18n.t('<p class="primitiveInspectorEmpty">这个原语没有额外参数。</p>')}</div>
      ${advanced}
    </section>`;
  }

  function inspectorHtml(options = {}) {
    const kind = "event";
    const typeId = text(options.typeId);
    const readonly = Boolean(options.readonly);
    return window.PortalI18n.html`<section class="primitiveInspector" data-primitive-inspector data-primitive-inspector-schema="${SCHEMA}" data-primitive-kind="${kind}" data-primitive-type="${escapeHtml(typeId)}" data-primitive-readonly="${readonly ? "true" : "false"}">
      ${typePickerHtml("event_type", typeId, readonly, "data-primitive-inspector-type-picker")}
      <div data-primitive-config-slot>${configEditorHtml({ ...options, kind, typeId, readonly })}</div>
    </section>`;
  }

  function fieldByName(typeId) {
    return new Map(
      fields(typeId, { includeAdvanced: true })
        .filter(field => String(field.path || "").startsWith("config."))
        .map(field => [fieldName(field), field]),
    );
  }

  function visibleField(fieldElement, form) {
    const path = text(fieldElement.dataset.visiblePath);
    if (!path) return true;
    const field = [...fieldByName(
      fieldElement.closest("[data-primitive-config-editor]")?.dataset.primitiveType,
    ).values()].find(item => item.path === path);
    const sourceName = field ? fieldName(field) : pathKey(path);
    const source = namedControl(form, sourceName);
    const value = source?.type === "checkbox" ? String(source.checked) : String(source?.value ?? "");
    return value === String(fieldElement.dataset.visibleEquals ?? "");
  }

  function syncDynamicFields(root) {
    const editor = root?.matches?.("[data-primitive-config-editor]")
      ? root
      : root?.querySelector?.("[data-primitive-config-editor]");
    const form = editor?.closest("form") || editor;
    if (!editor || !form) return;
    const readonly = editor.dataset.primitiveReadonly === "true";
    editor.querySelectorAll("[data-primitive-field]").forEach(fieldElement => {
      const visible = visibleField(fieldElement, form);
      fieldElement.hidden = !visible;
      fieldElement.querySelectorAll("input, select, textarea").forEach(input => {
        input.disabled = readonly || !visible;
        if (input.hasAttribute("data-original-required")) {
          input.required = visible && input.dataset.originalRequired === "true";
        } else if (input.required) {
          input.dataset.originalRequired = "true";
          input.required = visible;
        }
      });
    });
    editor.querySelectorAll(".primitiveOptionalLimit").forEach(container => {
      const enabled = container.querySelector('input[name$="_enabled"]')?.checked === true;
      const valueField = container.querySelector("[data-optional-limit-value]");
      if (valueField) valueField.hidden = !enabled;
      valueField?.querySelectorAll("input").forEach(input => {
        input.disabled = readonly || container.hidden || !enabled;
      });
    });
    editor.querySelectorAll("[data-primitive-value-note]").forEach(note => {
      const sourceName = pathKey(note.dataset.valueNotePath);
      const source = namedControl(form, sourceName);
      note.hidden = String(source?.value ?? "") !== String(
        note.dataset.valueNoteEquals ?? "",
      );
    });
    const currentConfig = readStructuredConfig(editor, { tolerateErrors: true }).config;
    const definitions = fieldByName(editor.dataset.primitiveType);
    editor.querySelectorAll("select[data-options-by-path]").forEach(select => {
      const targetField = definitions.get(select.name);
      if (!targetField?.options_by) return;
      const nextOptions = optionsForField(targetField, currentConfig);
      const previous = select.value;
      select.innerHTML = nextOptions.map(item => window.PortalI18n.html`<option value="${escapeHtml(item.value)}">${escapeHtml(item.label || item.value)}</option>`).join("");
      const defaultValue = String(targetField.default ?? "");
      select.value = nextOptions.some(item => String(item.value) === previous)
        ? previous
        : nextOptions.some(item => String(item.value) === defaultValue)
          ? defaultValue
          : String(nextOptions[0]?.value ?? "");
    });
  }

  function parseJson(value, field, errors) {
    try {
      const parsed = JSON.parse(String(value || "null"));
      if (field.type === "json-array" && !Array.isArray(parsed)) {
        errors.push(`${field.label}必须是 JSON array。`);
      }
      if (field.type === "json-object" && (!parsed || typeof parsed !== "object" || Array.isArray(parsed))) {
        errors.push(`${field.label}必须是 JSON object。`);
      }
      if (
        Array.isArray(parsed)
        && Number.isFinite(Number(field.maximum_items))
        && parsed.length > Number(field.maximum_items)
      ) {
        errors.push(`${field.label}最多包含 ${Number(field.maximum_items)} 项。`);
      }
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        const forbidden = list(field.forbidden_keys)
          .filter(key => Object.hasOwn(parsed, key));
        forbidden.forEach(key => { delete parsed[key]; });
      }
      return parsed;
    } catch (_error) {
      errors.push(`${field.label}不是有效 JSON。`);
      return clone(field.default);
    }
  }

  function relativePathError(value, field = {}) {
    const path = String(value || "").trim().replaceAll("\\", "/");
    if (!path) return "不能为空。";
    if (path === "." && field.allow_dot) return "";
    if (
      path.startsWith("/")
      || /^[a-zA-Z]:/.test(path)
      || path.split("/").some(part => ["", ".", ".."].includes(part))
    ) return "必须是 Workspace 内的规范相对路径。";
    return "";
  }

  function readStructuredConfig(editor, options = {}) {
    const form = editor?.closest("form") || editor;
    const typeId = text(editor?.dataset.primitiveType);
    const result = {};
    const errors = [];
    for (const field of fields(typeId, { includeAdvanced: false })) {
      if (!String(field.path || "").startsWith("config.")) continue;
      const name = fieldName(field);
      const fieldElement = editor.querySelector(`[data-field-path="${CSS.escape(field.path)}"]`);
      if (fieldElement?.hidden) continue;
      if (field.type === "watch-reference") continue;
      if (field.type === "resident-set") {
        const checked = [
          ...editor.querySelectorAll(`[name="${CSS.escape(name)}"]:checked`),
        ].map(input => String(input.value || "").trim()).filter(Boolean);
        const textInput = editor.querySelector(`textarea[name="${CSS.escape(name)}"]`);
        const values = checked.length
          ? checked
          : String(textInput?.value || "")
            .split(/[\s,;]+/)
            .map(value => value.trim().toLowerCase())
            .filter(Boolean);
        result[pathKey(field.path)] = global.PortalLineageProgram.normalizedPrimitiveFieldValue(field, values);
        if (field.required && !values.length) {
          errors.push(field.empty_error || `${field.label}不能为空。`);
        }
        continue;
      }
      if (field.type === "optional-limit") {
        const enabled = namedControl(form, `${name}_enabled`)?.checked === true;
        if (!enabled) {
          result[pathKey(field.path)] = null;
          continue;
        }
      }
      const input = namedControl(form, name);
      if (!input) continue;
      let value;
      if (field.type === "checkbox") value = Boolean(input.checked);
      else if (field.type === "number" || field.type === "optional-limit") {
        const number = Number(input.value);
        if (!Number.isFinite(number)) {
          if (field.required) errors.push(`${field.label}必须是数字。`);
          continue;
        }
        value = number;
      } else if (["json", "json-array", "json-object"].includes(field.type)) {
        value = parseJson(input.value, field, errors);
      } else if (field.type === "datetime-local") {
        value = input.value ? new Date(input.value).toISOString() : "";
      } else {
        value = String(input.value || "").trim();
      }
      value = global.PortalLineageProgram.normalizedPrimitiveFieldValue(field, value);
      if (field.type === "relative-path" && value) {
        const error = relativePathError(value, field);
        if (error) errors.push(`${field.label}${error}`);
      }
      if (
        field.type === "resident-id"
        && value
        && !/^resident_[0-9a-f]{12}$/.test(value)
      ) {
        errors.push(`${field.label}必须是规范 resident_* ID。`);
      }
      if (
        field.required
        && (
          value === ""
          || value === undefined
          || (value === null && field.type !== "json")
        )
      ) {
        errors.push(field.empty_error || `${field.label}不能为空。`);
      }
      if (field.omit_if_default && value === field.default) continue;
      if (value !== "" || field.required || field.type === "checkbox" || field.type === "optional-limit") {
        result[pathKey(field.path)] = value;
      }
    }
    return {
      config: result,
      errors: options.tolerateErrors ? [] : errors,
    };
  }

  function allowedConfigKeys(typeId, config = {}) {
    return new Set(
      fields(typeId, { includeAdvanced: true })
        .filter(field => fieldActiveForConfig(typeId, field, config))
        .map(field => text(field.path))
        .filter(path => path.startsWith("config."))
        .map(pathKey),
    );
  }

  function validateConfigObject(typeId, config, errors, options = {}) {
    for (const field of fields(typeId, { includeAdvanced: true })) {
      const path = text(field.path);
      if (!path.startsWith("config.")) continue;
      if (
        field.visible_when
        && String(config[pathKey(field.visible_when.path)] ?? "")
          !== String(field.visible_when.equals ?? "")
      ) continue;
      const key = pathKey(path);
      const present = Object.hasOwn(config, key);
      const value = config[key];
      if (
        options.validateRequired !== false
        && field.required
        && (
          (!present && !Object.hasOwn(field, "default"))
          || value === ""
          || (Array.isArray(value) && value.length === 0)
          || value === undefined
          || (value === null && field.type !== "json")
        )
      ) {
        errors.push(field.empty_error || `${field.label}不能为空。`);
        continue;
      }
      if (!present) continue;
      if (["number", "optional-limit"].includes(field.type)) {
        if (field.type === "optional-limit" && value === null) continue;
        if (typeof value !== "number" || !Number.isFinite(value)) {
          errors.push(`${field.label}必须是 JSON number${field.type === "optional-limit" ? " 或 null" : ""}。`);
          continue;
        }
        if (field.minimum !== undefined && value < Number(field.minimum)) {
          errors.push(`${field.label}不能小于 ${Number(field.minimum)}。`);
        }
        if (field.maximum !== undefined && value > Number(field.maximum)) {
          errors.push(`${field.label}不能大于 ${Number(field.maximum)}。`);
        }
        if (Number(field.step) === 1 && !Number.isInteger(value)) {
          errors.push(`${field.label}必须是整数。`);
        }
      }
      if (field.type === "checkbox" && typeof value !== "boolean") {
        errors.push(`${field.label}必须是 JSON boolean。`);
      }
      if (
        ["text", "textarea", "relative-path", "resident-id", "datetime-local", "select"].includes(field.type)
        && typeof value !== "string"
      ) {
        errors.push(`${field.label}必须是 JSON string。`);
        continue;
      }
      if (
        typeof value === "string"
        && Number.isFinite(Number(field.max_length))
        && value.length > Number(field.max_length)
      ) {
        errors.push(`${field.label}最多 ${Number(field.max_length)} 个字符。`);
      }
      if (field.type === "select") {
        const values = optionsForField(field, config)
          .map(item => String(item.value));
        if (!values.includes(String(value))) {
          errors.push(`${field.label}不是当前类型允许的选项。`);
        }
      }
      if (
        field.type === "datetime-local"
        && value
        && !Number.isFinite(new Date(value).getTime())
      ) {
        errors.push(`${field.label}不是有效时间。`);
      }
      if (field.type === "relative-path" && value) {
        const error = relativePathError(value, field);
        if (error) errors.push(`${field.label}${error}`);
      }
      if (
        field.type === "resident-id"
        && value
        && !/^resident_[0-9a-f]{12}$/.test(String(value).toLowerCase())
      ) {
        errors.push(`${field.label}必须是规范 resident_* ID。`);
      }
      if (field.type === "json-array" && !Array.isArray(value)) {
        errors.push(`${field.label}必须是 JSON array。`);
      }
      if (field.type === "resident-set") {
        if (!Array.isArray(value)) {
          errors.push(`${field.label}必须是 resident_* ID array。`);
        } else if (value.some(item => !/^resident_[0-9a-f]{12}$/.test(String(item)))) {
          errors.push(`${field.label}只能包含规范 resident_* ID。`);
        }
      }
      if (field.type === "json-object" && (!value || typeof value !== "object" || Array.isArray(value))) {
        errors.push(`${field.label}必须是 JSON object。`);
      }
      if (
        Array.isArray(value)
        && Number.isFinite(Number(field.maximum_items))
        && value.length > Number(field.maximum_items)
      ) {
        errors.push(`${field.label}最多包含 ${Number(field.maximum_items)} 项。`);
      }
      if (value && typeof value === "object" && !Array.isArray(value)) {
        const forbidden = list(field.forbidden_keys)
          .filter(keyName => Object.hasOwn(value, keyName));
        forbidden.forEach(keyName => { delete value[keyName]; });
      }
    }
  }

  function advancedOnlyValues(typeId, config) {
    const keys = new Set(
      fields(typeId, { includeAdvanced: true })
        .filter(field => field.advanced_only)
        .map(field => pathKey(field.path)),
    );
    return Object.fromEntries(
      Object.entries(object(config)).filter(([key]) => keys.has(key)),
    );
  }

  function advancedOnlyConfig(editor) {
    if (ADVANCED_CONFIG_VALUES.has(editor)) {
      return clone(ADVANCED_CONFIG_VALUES.get(editor));
    }
    const advanced = editor?.querySelector(".primitiveAdvancedConfig textarea");
    if (!advanced) return {};
    let parsed;
    try {
      parsed = JSON.parse(String(advanced.value || "{}"));
    } catch (_error) {
      return {};
    }
    return advancedOnlyValues(
      text(editor.dataset.primitiveType),
      parsed,
    );
  }

  function advancedConfigResult(editor) {
    const advanced = editor?.querySelector(".primitiveAdvancedConfig textarea");
    if (!advanced) return { config: {}, errors: [] };
    const typeId = text(editor.dataset.primitiveType);
    const configTypeKey = "event_type";
    const errors = [];
    const parsed = parseJson(advanced.value, {
      type: "json-object",
      label: window.PortalI18n.t("高级配置 JSON"),
      default: {},
    }, errors);
    const config = object(parsed);
    const allowed = allowedConfigKeys(typeId, config);
    allowed.add(configTypeKey);
    const unknown = Object.keys(config).filter(key => !allowed.has(key));
    if (unknown.length) {
      errors.push(`当前事件不接受字段：${unknown.join("、")}。`);
    }
    if (
      config[configTypeKey] !== undefined
      && config[configTypeKey] !== typeId
    ) {
      errors.push(`高级配置中的 ${configTypeKey} 必须与类型选择器一致。`);
    }
    validateConfigObject(
      typeId,
      config,
      errors,
      { validateRequired: false },
    );
    return {
      config: { ...clone(config), [configTypeKey]: typeId },
      errors,
    };
  }

  function setAdvancedStatus(editor, errors = [], message = "") {
    const advanced = editor?.querySelector(".primitiveAdvancedConfig textarea");
    const error = editor?.querySelector("[data-primitive-advanced-error]");
    const status = editor?.querySelector("[data-primitive-edit-source]");
    const invalid = errors.length > 0;
    if (advanced) advanced.setAttribute("aria-invalid", invalid ? "true" : "false");
    if (error) {
      error.hidden = !invalid;
      error.textContent = invalid ? errors.join(" ") : "";
    }
    if (status && message) status.textContent = message;
  }

  function setStructuredFieldValue(editor, field, config) {
    const fieldElement = editor.querySelector(
      `[data-field-path="${CSS.escape(field.path)}"]`,
    );
    if (!fieldElement || field.type === "watch-reference") return;
    const name = fieldName(field);
    const value = configValue(config, field);
    if (field.type === "resident-set") {
      const selected = new Set(list(value).map(item => String(item || "").trim()));
      fieldElement.querySelectorAll(`[name="${CSS.escape(name)}"]`).forEach(input => {
        if (input.type === "checkbox") input.checked = selected.has(String(input.value || "").trim());
      });
      const textarea = fieldElement.querySelector(`textarea[name="${CSS.escape(name)}"]`);
      if (textarea) textarea.value = [...selected].join("\n");
      return;
    }
    if (field.type === "optional-limit") {
      const enabled = value !== null && value !== undefined;
      const toggle = fieldElement.querySelector(`[name="${CSS.escape(name)}_enabled"]`);
      const input = fieldElement.querySelector(`[name="${CSS.escape(name)}"]`);
      if (toggle) toggle.checked = enabled;
      if (input && enabled) input.value = String(value);
      return;
    }
    const input = fieldElement.querySelector(`[name="${CSS.escape(name)}"]`);
    if (!input) return;
    if (field.type === "checkbox") {
      input.checked = Boolean(value);
    } else if (["json", "json-array", "json-object"].includes(field.type)) {
      input.value = JSON.stringify(
        value === undefined ? field.default ?? null : value,
        null,
        2,
      );
    } else if (field.type === "datetime-local") {
      input.value = dateTimeLocalValue(value);
    } else {
      input.value = String(value ?? "");
    }
  }

  function applyConfigToStructuredFields(editor, config) {
    const typeId = text(editor?.dataset.primitiveType);
    const visibleFields = fields(typeId, { includeAdvanced: false });
    visibleFields.forEach(field => setStructuredFieldValue(editor, field, config));
    syncDynamicFields(editor);
    visibleFields
      .filter(field => field.type === "select")
      .forEach(field => setStructuredFieldValue(editor, field, config));
    syncDynamicFields(editor);
  }

  function projectedConfig(editor) {
    const typeId = text(editor?.dataset.primitiveType);
    return {
      ...advancedOnlyConfig(editor),
      ...readStructuredConfig(editor, { tolerateErrors: true }).config,
      event_type: typeId,
    };
  }

  function readConfig(root) {
    const editor = root?.matches?.("[data-primitive-config-editor]")
      ? root
      : root?.querySelector?.("[data-primitive-config-editor]");
    if (!editor) return { config: {}, errors: ["找不到原语配置编辑器。"] };
    const typeId = text(editor.dataset.primitiveType);
    const configTypeKey = "event_type";
    const advanced = advancedConfigResult(editor);
    if (advanced.errors.length) {
      setAdvancedStatus(editor, advanced.errors);
      return {
        config: projectedConfig(editor),
        errors: advanced.errors,
      };
    }
    ADVANCED_CONFIG_VALUES.set(
      editor,
      advancedOnlyValues(typeId, advanced.config),
    );
    const structured = readStructuredConfig(editor);
    return {
      config: {
        ...advancedOnlyConfig(editor),
        ...structured.config,
        [configTypeKey]: typeId,
      },
      errors: structured.errors,
    };
  }

  function bindConfigEditor(root, options = {}) {
    const editor = root?.matches?.("[data-primitive-config-editor]")
      ? root
      : root?.querySelector?.("[data-primitive-config-editor]");
    if (!editor || editor.dataset.primitiveConfigBound === "true") return;
    editor.dataset.primitiveConfigBound = "true";
    const advanced = editor.querySelector(".primitiveAdvancedConfig textarea");
    const initialAdvanced = advancedConfigResult(editor);
    ADVANCED_CONFIG_VALUES.set(
      editor,
      initialAdvanced.errors.length
        ? {}
        : advancedOnlyValues(
          text(editor.dataset.primitiveType),
          initialAdvanced.config,
        ),
    );
    const syncJson = () => {
      if (!advanced) return;
      advanced.value = JSON.stringify(projectedConfig(editor), null, 2);
      setAdvancedStatus(
        editor,
        [],
        "这里是普通字段的同步投影；有效 JSON 也会立即更新普通字段。",
      );
    };
    editor.querySelectorAll("[data-primitive-structured-fields] input, [data-primitive-structured-fields] select, [data-primitive-structured-fields] textarea").forEach(input => {
      const update = () => {
        syncDynamicFields(editor);
        syncJson();
        options.onChange?.({ source: "fields", editor });
      };
      input.addEventListener("input", update);
      input.addEventListener("change", update);
    });
    advanced?.addEventListener("input", () => {
      const result = advancedConfigResult(editor);
      if (result.errors.length) {
        setAdvancedStatus(
          editor,
          result.errors,
          "JSON 尚未形成有效配置；普通字段仍保留上一次有效值。",
        );
        return;
      }
      ADVANCED_CONFIG_VALUES.set(
        editor,
        advancedOnlyValues(
          text(editor.dataset.primitiveType),
          result.config,
        ),
      );
      applyConfigToStructuredFields(editor, result.config);
      setAdvancedStatus(
        editor,
        [],
        "有效 JSON 已投影到普通字段；离开编辑框后会规范化格式。",
      );
      options.onChange?.({ source: "json", editor });
    });
    advanced?.addEventListener("blur", () => {
      if (advancedConfigResult(editor).errors.length) return;
      syncJson();
    });
    syncDynamicFields(editor);
    syncJson();
  }

  function bindInspector(root, options = {}) {
    const inspector = root?.matches?.("[data-primitive-inspector]")
      ? root
      : root?.querySelector?.("[data-primitive-inspector]");
    if (!inspector) return;
    const context = {
      ...options,
      kind: text(inspector.dataset.primitiveKind, "event"),
      typeId: text(inspector.dataset.primitiveType),
      readonly: inspector.dataset.primitiveReadonly === "true",
    };
    INSPECTOR_CONTEXTS.set(inspector, context);
    if (inspector.dataset.primitiveInspectorBound === "true") return;
    inspector.dataset.primitiveInspectorBound = "true";

    const bindCurrentEditor = reason => {
      const currentContext = INSPECTOR_CONTEXTS.get(inspector) || context;
      const editor = inspector.querySelector("[data-primitive-config-editor]");
      bindConfigEditor(editor, {
        onChange: detail => currentContext.onChange?.({
          ...detail,
          inspector,
          typeId: currentContext.typeId,
        }),
      });
      currentContext.afterRender?.({
        inspector,
        editor,
        kind: currentContext.kind,
        typeId: currentContext.typeId,
        reason,
      });
    };

    bindTypePicker(
      inspector.querySelector("[data-primitive-inspector-type-picker]"),
      {
        onChange: nextType => {
          const currentContext = INSPECTOR_CONTEXTS.get(inspector) || context;
          if (!nextType || nextType === currentContext.typeId) return;
          const currentEditor = inspector.querySelector(
            "[data-primitive-config-editor]",
          );
          const advancedWasOpen = currentEditor
            ?.querySelector("[data-primitive-advanced]")?.open === true;
          const nextConfig = {
            ...projectedConfig(currentEditor),
            ["event_type"]: nextType,
          };
          currentContext.typeId = nextType;
          currentContext.config = nextConfig;
          inspector.dataset.primitiveType = nextType;
          const slot = inspector.querySelector("[data-primitive-config-slot]");
          if (!slot) return;
          slot.innerHTML = configEditorHtml({
            ...currentContext,
            typeId: nextType,
            config: nextConfig,
          });
          if (advancedWasOpen) {
            const details = slot.querySelector("[data-primitive-advanced]");
            if (details) details.open = true;
          }
          bindCurrentEditor("type-change");
          currentContext.onTypeChange?.(nextType, {
            inspector,
            config: readConfig(inspector).config,
          });
        },
      },
    );
    bindCurrentEditor("initial");
  }

  global.PortalLineagePrimitiveInspector = Object.freeze({
    schema: SCHEMA,
    inspectorHtml,
    bindInspector,
    readConfig,
  });
})(window);
