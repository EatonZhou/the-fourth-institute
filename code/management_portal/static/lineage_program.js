(function (global) {
  "use strict";

  const PROGRAM_SCHEMA = "tfi.hatg-program.v2";
  const PACKET_OPERATOR_KINDS = Object.freeze(["filter", "selector", "reducer"]);
  const COMPONENT_KINDS = Object.freeze([
    "event_source",
    "effect",
    "seed",
    ...PACKET_OPERATOR_KINDS,
    "loop",
  ]);
  const WIRE_TYPES = Object.freeze(["route", "target", "watch"]);
  const ID_PREFIX = Object.freeze({
    event_source: "event_source",
    effect: "effect",
    seed: "seed",
    filter: "filter",
    selector: "selector",
    reducer: "reducer",
    loop: "loop",
    wire: "wire",
  });

  function deepFreeze(value) {
    if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
    Object.values(value).forEach(deepFreeze);
    return Object.freeze(value);
  }

  const MANIFEST = {
    schema: "portal.lineage.program-manifest.v1",
    program_schema: PROGRAM_SCHEMA,
    component_kinds: {
      event_source: {
        title: "Event",
        description: window.PortalI18n.t("Portal 识别到事实后，从这个出口触发 Resident 或 Effect。"),
        icon: "event",
        tone: "indigo",
        id_prefix: ID_PREFIX.event_source,
        default_title: "新 Event",
        default_config: {},
        ports: [
          { id: "output", type: "route", direction: "out", label: "输出" },
        ],
        inspector: {
          fields: [
            { path: "title", type: "text", label: window.PortalI18n.t("名称"), required: true, max_length: 120 },
            {
              path: "config.event_type",
              type: "select",
              label: window.PortalI18n.t("系统事件"),
              required: true,
              options_from: "event_types",
            },
          ],
        },
      },
      effect: {
        title: "Effect",
        description: window.PortalI18n.t("被触发时运行一段脚本，按顺序、条件和循环调用一条或多条 HATG ISA 指令；脚本可以读取本次参数、Event、Packet 和具名端点连接的对象。"),
        icon: "effect",
        tone: "amber",
        id_prefix: ID_PREFIX.effect,
        default_title: "新 Effect",
        // The same default as CreateComponent without config
        // (lineage_program_authoring.DEFAULT_EFFECT_CONFIG): deliver this
        // input to the Resident wired to "target".
        default_config: {
          script: 'DeliverMessage resident_id="${endpoint.target}"',
          endpoints: ["target"],
        },
        // Endpoint ports ("target" wires) come from config.endpoints.
        ports: [
          { id: "invoke", type: "invoke", direction: "in", label: "Invoke" },
        ],
      },
      seed: {
        title: "Seed",
        description: window.PortalI18n.t("保存尚未成为 Resident 的候选工作；可由 HATG 指令采纳或收起。"),
        icon: "seed",
        tone: "green",
        id_prefix: ID_PREFIX.seed,
        default_title: "新 Seed",
        default_config: {
          seed_type: "other",
          status: "active",
          summary: "",
          artifact_path: "",
        },
        ports: [
          { id: "invoke", type: "invoke", direction: "in", label: "激活" },
        ],
      },
      filter: {
        title: "Filter",
        description: window.PortalI18n.t("单入单出的 Packet 过滤原语；规则式不创建 Resident。"),
        icon: "filter",
        tone: "slate",
        id_prefix: ID_PREFIX.filter,
        default_title: "Prompt 过滤器",
        default_config: {
          operator_implementation: "rules",
          prompt: "比较同一 Event 下的候选 Packet，只保留符合当前下游契约的候选，不改写内容。",
          selection: {
            mode: "all",
            conditions: [],
            order_by: "priority_desc",
            limit: 1,
          },
        },
        ports: [
          { id: "invoke", type: "invoke", direction: "in", label: "Invoke" },
          { id: "output", type: "route", direction: "out", label: "输出" },
        ],
      },
      selector: {
        title: "Selector",
        description: window.PortalI18n.t("单入多出的 Packet 路由原语；每个具名分支拥有自己的匹配规则。"),
        icon: "selector",
        tone: "cyan",
        id_prefix: ID_PREFIX.selector,
        default_title: "Prompt 选择器",
        default_config: {
          operator_implementation: "rules",
          prompt: "把候选 Packet 路由到合适的具名输出分支，不改写内容。",
          branches: [
            {
              id: "branch_a",
              label: "分支 A",
              description: "接收路由标签为 branch_a 的 Packet。",
              match: {
                mode: "all",
                conditions: [{ from: "packet.key", operator: "equals", value: "branch_a" }],
                order_by: "input",
                limit: null,
              },
            },
            {
              id: "branch_b",
              label: "分支 B",
              description: "接收路由标签为 branch_b 的 Packet。",
              match: {
                mode: "all",
                conditions: [{ from: "packet.key", operator: "equals", value: "branch_b" }],
                order_by: "input",
                limit: null,
              },
            },
          ],
        },
        ports: [
          { id: "invoke", type: "invoke", direction: "in", label: "Invoke" },
        ],
      },
      reducer: {
        title: "Reducer",
        description: window.PortalI18n.t("单入单出的 Packet 汇合原语；可使用规则或 Resident 语义执行器。"),
        icon: "reducer",
        tone: "violet",
        id_prefix: ID_PREFIX.reducer,
        default_title: "Prompt 汇合者",
        default_config: {
          operator_implementation: "resident",
          prompt: "保留每个上游的重要判断，消除重复与冲突，合并为一条清楚、可执行的 Packet。",
          reduce_strategy: "join",
          reduce_separator: "\n\n",
          output_key: "reduced",
        },
        ports: [
          { id: "invoke", type: "invoke", direction: "in", label: "Invoke" },
          { id: "output", type: "route", direction: "out", label: "输出" },
        ],
      },
      loop: {
        title: window.PortalI18n.t("循环"),
        description: window.PortalI18n.t("记录往复交互进行了几轮；达到最大轮数或收到完成信号时从 stop 输出。"),
        icon: "refresh",
        tone: "violet",
        id_prefix: ID_PREFIX.loop,
        default_title: "循环",
        default_config: { max_rounds: 5, completion: "complete" },
        ports: [
          { id: "invoke", type: "invoke", direction: "in", label: "Invoke" },
          { id: "done", type: "invoke", direction: "in", label: window.PortalI18n.t("Done") },
          { id: "continue", type: "route", direction: "out", label: window.PortalI18n.t("Continue") },
          { id: "stop", type: "route", direction: "out", label: window.PortalI18n.t("Stop") },
        ],
      },
    },
    resident_kinds: {
      agent: {
        title: window.PortalI18n.t("Work Resident"),
        description: window.PortalI18n.t("通过输入端口接收路由包；本轮回复从输出端口发出消息包。"),
        ports: [
          { id: "invoke", type: "invoke", direction: "in", label: "Invoke" },
          { id: "output", type: "route", direction: "out", label: "输出" },
        ],
      },
    },
    resident_reference: {
      identity: "resident_id",
      input_mode: "choose_or_enter",
      scope: "current_lineage",
      pattern: "^resident_[0-9a-f]{12}$",
    },
    default_types: {},
    event_type_categories: {},
    event_common_fields: [],
    event_types: {},
    wire_types: {
      route: {
        title: "Route",
        description: window.PortalI18n.t("沿连线传递路由包；包可以包含消息、事件或两者。"),
        tone: "violet",
        source_kinds: ["agent", "event_source", "loop", "filter", "selector", "reducer"],
        target_kinds: ["agent", "filter", "selector", "reducer", "effect", "loop", "seed"],
        delivery_config: ["invoke_profiles", "include_envelope", "unavailable_policy", "queue_limit", "expires_after_seconds", "max_messages", "context_policy", "parallel"],
      },
      target: {
        title: "Target",
        description: window.PortalI18n.t("Effect 的具名端点指向调用对象；它不触发目标。"),
        tone: "amber",
        source_kinds: ["effect"],
        target_kinds: ["agent", "filter", "selector", "reducer", "event_source", "effect", "loop", "seed"],
        delivery_config: [],
      },
      watch: {
        title: "Watch",
        description: window.PortalI18n.t("Event Source 从观察端点直接绑定被观察对象。"),
        tone: "slate",
        source_kinds: ["event_source"],
        target_kinds: ["agent", "event_source", "effect", "wire", "handoff"],
      },
    },
  };

  let primitiveDefinitionsPromise = null;

  function installPrimitiveDefinitions(payload) {
    const source = global.PortalI18n.definition(payload && typeof payload === "object" ? payload : {});
    if (
      !global.PortalCompatibility.schemaMatches(source.schema, "tfi.hatg-primitive-definitions.v1")
    ) {
      throw new Error("HATG 原语定义 schema 无效");
    }
    const requiredObjects = ["default_types", "event_type_categories", "event_types"];
    requiredObjects.forEach(key => {
      if (!source[key] || typeof source[key] !== "object" || Array.isArray(source[key])) {
        throw new Error(`HATG 原语定义缺少 ${key}`);
      }
    });
    if (!Array.isArray(source.event_common_fields)) {
      throw new Error("HATG 原语定义缺少 event_common_fields");
    }
    [...requiredObjects, "event_common_fields"].forEach(key => {
      MANIFEST[key] = deepFreeze(source[key]);
    });
    const eventType = text(source.default_types.event).toLowerCase();
    if (!source.event_types[eventType]) throw new Error("HATG 默认事件类型无效");
    MANIFEST.component_kinds.event_source.default_config = { event_type: eventType };
    MANIFEST.primitive_definitions_schema = source.schema;
    return MANIFEST;
  }

  function loadPrimitiveDefinitions(url = "/static/lineage_primitive_definitions.json") {
    if (MANIFEST.primitive_definitions_schema) {
      return Promise.resolve(MANIFEST);
    }
    if (!primitiveDefinitionsPromise) {
      primitiveDefinitionsPromise = global.PortalAPI.get(url, { cache: "no-store" })
        .then(installPrimitiveDefinitions)
        .catch(error => {
          primitiveDefinitionsPromise = null;
          throw error;
        });
    }
    return primitiveDefinitionsPromise;
  }

  function object(value) {
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  }

  function list(value) {
    return Array.isArray(value) ? value : [];
  }

  function text(value, fallback = "") {
    const normalized = String(value ?? "").trim();
    return normalized || fallback;
  }

  function finiteNumber(value, fallback) {
    const number = Number(value);
    return Number.isFinite(number) ? number : fallback;
  }

  function normalizeOptionalLimit(value, maximum = Number.MAX_SAFE_INTEGER) {
    if (value === null || value === undefined) return null;
    const textValue = String(value).trim().toLowerCase();
    if ([
      "inf",
      "infinite",
      "infinity",
      "unlimited",
      "none",
      "no_limit",
      "no-limit",
      "∞",
    ].includes(textValue)) return null;
    const number = Number(value);
    if (!Number.isFinite(number) || number < 0) return null;
    return Math.min(
      Math.max(0, Math.trunc(Number(maximum) || Number.MAX_SAFE_INTEGER)),
      Math.trunc(number),
    );
  }

  function normalizePosition(value, fallback = 50) {
    return Math.round(finiteNumber(value, fallback) * 100) / 100;
  }

  function clone(value) {
    if (value === undefined) return undefined;
    return JSON.parse(JSON.stringify(value));
  }

  function normalizeArgumentBinding(binding, depth = 0) {
    const item = object(binding);
    if (depth > 8) return null;
    if (Object.hasOwn(item, "value")) {
      return { value: clone(item.value) };
    }
    if (text(item.from)) {
      return {
        from: text(item.from).replace(/^\$/, ""),
        ...(Object.hasOwn(item, "default") ? { default: clone(item.default) } : {}),
      };
    }
    if (Array.isArray(item.concat) && item.concat.length) {
      const parts = item.concat
        .map(part => normalizeArgumentBinding(part, depth + 1))
        .filter(Boolean)
        .slice(0, 24);
      return parts.length
        ? { concat: parts, separator: String(item.separator ?? "").slice(0, 200) }
        : null;
    }
    return null;
  }

  function normalizeArgumentMapping(value) {
    return Object.fromEntries(Object.entries(object(value)).flatMap(([name, binding]) => {
      const normalized = normalizeArgumentBinding(binding);
      return normalized ? [[name, normalized]] : [];
    }));
  }

  function normalizePacketFilter(value) {
    const source = object(value);
    const mode = ["all", "any"].includes(text(source.mode).toLowerCase())
      ? text(source.mode).toLowerCase()
      : "all";
    const orderBy = ["input", "priority_desc"].includes(
      text(source.order_by).toLowerCase(),
    ) ? text(source.order_by).toLowerCase() : "input";
    const operators = new Set([
      "exists", "missing", "equals", "not_equals", "contains",
      "not_contains", "greater_than", "greater_or_equal", "less_than",
      "less_or_equal",
    ]);
    const conditions = list(source.conditions).flatMap(condition => {
      const item = object(condition);
      const from = text(item.from).replace(/^\$/, "");
      const operator = text(item.operator).toLowerCase();
      if (!from || !operators.has(operator)) return [];
      return [{
        from,
        operator,
        ...(["exists", "missing"].includes(operator)
          ? {}
          : { value: clone(item.value) }),
      }];
    }).slice(0, 24);
    const numericLimit = Number(source.limit);
    const limit = source.limit === null || source.limit === undefined || source.limit === ""
      ? null
      : Number.isFinite(numericLimit)
        ? Math.max(1, Math.min(24, Math.trunc(numericLimit)))
        : null;
    return { mode, conditions, order_by: orderBy, limit };
  }

  function selectorBranchPort(branchId) {
    const normalized = text(branchId).trim().toLowerCase();
    return /^[a-z][a-z0-9_-]{0,31}$/.test(normalized)
      ? `branch:${normalized}`
      : "";
  }

  function normalizeSelectorBranches(value) {
    const seen = new Set();
    return list(value).flatMap(branch => {
      const source = object(branch);
      const id = text(source.id).trim().toLowerCase();
      if (!selectorBranchPort(id) || seen.has(id)) return [];
      seen.add(id);
      return [{
        id,
        label: text(source.label, id).slice(0, 80),
        description: text(source.description).slice(0, 1000),
        match: normalizePacketFilter(source.match),
      }];
    }).slice(0, 12);
  }

  function normalizePacketOperatorConfig(kind, value, defaults = {}) {
    const source = object(value);
    const fallback = object(defaults);
    const implementation = ["rules", "resident"].includes(
      text(source.operator_implementation).toLowerCase(),
    ) ? text(source.operator_implementation).toLowerCase() : text(
      fallback.operator_implementation,
      kind === "reducer" ? "resident" : "rules",
    );
    const result = {
      operator_implementation: implementation,
      prompt: text(source.prompt, text(fallback.prompt)).slice(0, 12000),
      ...(text(source.resident_executor_id)
        ? { resident_executor_id: text(source.resident_executor_id) }
        : {}),
    };
    if (kind === "filter") {
      result.selection = normalizePacketFilter(
        Object.hasOwn(source, "selection") ? source.selection : fallback.selection,
      );
    } else if (kind === "selector") {
      result.branches = normalizeSelectorBranches(
        Object.hasOwn(source, "branches") ? source.branches : fallback.branches,
      );
    } else if (kind === "reducer") {
      result.reduce_strategy = ["join", "first", "highest_priority"].includes(
        text(source.reduce_strategy).toLowerCase(),
      ) ? text(source.reduce_strategy).toLowerCase() : text(fallback.reduce_strategy, "join");
      result.reduce_separator = String(
        source.reduce_separator ?? fallback.reduce_separator ?? "\n\n",
      ).slice(0, 200);
      result.output_key = text(source.output_key, text(fallback.output_key, "reduced"))
        .toLowerCase();
    }
    return result;
  }

  function normalizeInvokeProfiles(value) {
    return Object.fromEntries(Object.entries(object(value)).map(([key, profile]) => (
      [text(key).toLowerCase(), {
        arguments: normalizeArgumentMapping(object(profile).arguments),
      }]
    )).filter(([key]) => key));
  }

  // Only Event Sources have types; an Effect names the instruction it calls.
  function primitiveTypeId(value, fallback) {
    return text(value, fallback).toLowerCase();
  }

  function primitiveDefaultType() {
    return text(MANIFEST.default_types.event);
  }

  function eventFields(typeId) {
    const definition = object(object(MANIFEST.event_types)[typeId]);
    const omittedCommonFields = new Set(
      list(definition.omit_common_fields).map(item => text(item)),
    );
    return [
      ...list(MANIFEST.event_common_fields).filter(field => (
        !omittedCommonFields.has(text(field?.name || field?.path).replace(/^config\./, ""))
      )),
      ...list(definition.fields),
    ];
  }

  function normalizedPrimitiveFieldValue(field, rawValue) {
    const hasValue = rawValue !== undefined;
    const value = hasValue ? rawValue : clone(field.default);
    if (value === undefined) return undefined;
    if (field.type === "optional-limit") {
      return normalizeOptionalLimit(value, finiteNumber(field.maximum, 100000));
    }
    if (field.type === "number") {
      const fallback = finiteNumber(field.default, 0);
      const number = finiteNumber(value, fallback);
      const bounded = Math.min(
        field.maximum ?? number,
        Math.max(field.minimum ?? number, number),
      );
      return Number(field.step) === 1 ? Math.trunc(bounded) : bounded;
    }
    if (field.type === "checkbox") return Boolean(value);
    if (field.type === "resident-set") {
      return Array.from(new Set(
        list(value).map(item => text(item).toLowerCase()).filter(Boolean),
      ));
    }
    if (["json", "json-array", "json-object"].includes(field.type)) {
      return clone(value);
    }
    const normalized = text(value);
    return field.type === "resident-id" ? normalized.toLowerCase() : normalized;
  }

  function normalizedPrimitiveConfig(kind, typeId, sourceConfig) {
    const result = {};
    const fields = eventFields(typeId).filter(field => text(field?.path).startsWith("config."));
    const fieldsByKey = new Map(fields.map(field => [
      text(field.path).replace(/^config\./, ""),
      field,
    ]));
    const requestedValue = field => {
      const key = text(field.path).replace(/^config\./, "");
      return sourceConfig[key];
    };
    const resolvedValue = key => {
      if (Object.hasOwn(result, key)) return result[key];
      const dependency = fieldsByKey.get(key);
      return dependency
        ? normalizedPrimitiveFieldValue(dependency, requestedValue(dependency))
        : sourceConfig[key];
    };
    fields.forEach(field => {
      const visibility = object(field.visible_when);
      if (visibility.path) {
        const dependencyKey = text(visibility.path).replace(/^config\./, "");
        if (String(resolvedValue(dependencyKey) ?? "") !== String(visibility.equals ?? "")) {
          return;
        }
      }
      const key = text(field.path).replace(/^config\./, "");
      const value = normalizedPrimitiveFieldValue(field, requestedValue(field));
      if (value !== undefined) result[key] = value;
    });
    return result;
  }

  function normalizeComponent(raw, index = 0) {
    const source = object(raw);
    const kind = text(source.kind, "unknown");
    const definition = MANIFEST.component_kinds[kind];
    const sourceConfig = object(source.config);
    const hasCanonicalConfig = Boolean(
      source.config
      && typeof source.config === "object"
      && !Array.isArray(source.config),
    );
    const typeId = hasCanonicalConfig && kind === "event_source"
      ? primitiveTypeId(sourceConfig.event_type, primitiveDefaultType())
      : "";
    const canonicalConfig = hasCanonicalConfig && kind === "event_source"
      ? {
          ...clone(sourceConfig),
          event_type: typeId,
          ...normalizedPrimitiveConfig("event", typeId, sourceConfig),
        }
      : hasCanonicalConfig && kind === "effect"
        ? {
            script: typeof sourceConfig.script === "string" ? sourceConfig.script : "",
            endpoints: list(sourceConfig.endpoints).map(item => text(item)).filter(Boolean),
            ...(Object.hasOwn(sourceConfig, "failure_recipients")
              ? { failure_recipients: list(sourceConfig.failure_recipients).map(item => text(item)).filter(Boolean) }
              : {}),
          }
      : hasCanonicalConfig && kind === "loop"
        ? {
            max_rounds: Math.max(1, Math.trunc(finiteNumber(sourceConfig.max_rounds, 5))),
            completion: text(sourceConfig.completion) === "rearm" ? "rearm" : "complete",
          }
      : PACKET_OPERATOR_KINDS.includes(kind)
        ? normalizePacketOperatorConfig(
            kind,
            sourceConfig,
            definition?.default_config,
          )
        : kind === "seed"
          ? {
              ...clone(sourceConfig),
              seed_type: ["idea", "plan", "next_step", "other"].includes(
                text(sourceConfig.seed_type).toLowerCase(),
              ) ? text(sourceConfig.seed_type).toLowerCase() : "other",
              status: ["active", "promoted", "dismissed"].includes(
                text(sourceConfig.status).toLowerCase(),
              ) ? text(sourceConfig.status).toLowerCase() : "active",
              summary: text(sourceConfig.summary),
              artifact_path: text(sourceConfig.artifact_path),
            }
        : {};
    const defaultTitle = definition?.default_title || window.PortalI18n.t("未知原语");
    const fallbackX = 18 + (index % 4) * 20;
    const fallbackY = 24 + Math.floor(index / 4) * 20;
    return {
      ...clone(source),
      id: text(source.id),
      kind,
      title: text(source.title, defaultTitle),
      x: normalizePosition(source.x, fallbackX),
      y: normalizePosition(source.y, fallbackY),
      config: hasCanonicalConfig
        ? {
            ...clone(definition?.default_config || {}),
            ...canonicalConfig,
          }
        : null,
    };
  }

  function normalizeWire(raw) {
    const source = object(raw);
    const sourceConfig = object(source.config);
    const type = text(source.kind, "unknown");
    const authoredUnavailablePolicy = text(
      sourceConfig.unavailable_policy,
      "drop",
    );
    const unavailablePolicy = ["drop", "latest", "queue"].includes(
      authoredUnavailablePolicy,
    ) ? authoredUnavailablePolicy : "drop";
    const deliveryConfig = ["route"].includes(type)
      ? {
          include_envelope: sourceConfig.include_envelope !== false,
          invoke_profiles: normalizeInvokeProfiles(sourceConfig.invoke_profiles),
          unavailable_policy: unavailablePolicy,
          max_messages: sourceConfig.max_messages == null ? null : Math.max(0, Math.trunc(Number(sourceConfig.max_messages))),
          ...(unavailablePolicy === "queue"
            ? {
                queue_limit: Math.max(
                  1,
                  Math.min(
                    100,
                    Math.trunc(finiteNumber(
                      sourceConfig.queue_limit,
                      10,
                    )),
                  ),
                ),
              }
            : {}),
          expires_after_seconds: Math.max(
            0,
            Math.min(
              604800,
              Math.trunc(finiteNumber(
                sourceConfig.expires_after_seconds,
                0,
              )),
            ),
          ),
          ...(type === "route"
            ? {
              }
            : {}),
        }
      : {};
    return {
      ...clone(source),
      id: text(source.id),
      type,
      source: text(source.source),
      source_port: text(source.source_port),
      target: text(source.target),
      target_port: text(source.target_port),
      label: text(source.label),
      config: ["route"].includes(type)
          ? deliveryConfig
          : {},
    };
  }

  function normalizeProgram(value) {
    const candidate = object(value);
    const source = candidate.program && typeof candidate.program === "object"
      ? object(candidate.program)
      : candidate;
    const components = list(source.components)
      .map(normalizeComponent)
      .filter(component => component.id);
    const wires = list(source.wires)
      .map(normalizeWire)
      .filter(wire => wire.id && wire.source && wire.target);
    return {
      ...clone(source),
      schema: text(source.schema, PROGRAM_SCHEMA),
      components,
      wires,
    };
  }

  function authoredProgram(value) {
    return normalizeProgram(value);
  }

  function primitiveKind(node) {
    const explicit = text(node?.primitive_kind);
    if (explicit) return explicit;
    const kind = text(node?.kind);
    if (COMPONENT_KINDS.includes(kind)) return kind;
    return "agent";
  }

  function projectComponent(component, index = 0) {
    const normalized = normalizeComponent(component, index);
    return {
      ...normalized,
      kind: normalized.kind,
      primitive_kind: normalized.kind,
      _program_component: true,
      status: "idle",
    };
  }

  function projectWire(wire) {
    const normalized = normalizeWire(wire);
    return {
      ...normalized,
      type: normalized.type,
      wire_type: normalized.type,
      _program_wire: true,
    };
  }

  function projectGraph(programValue, residentNodes = []) {
    const program = normalizeProgram(programValue);
    const components = program.components.map(projectComponent);
    const executorResidentIds = new Set(
      components
        .filter(component => PACKET_OPERATOR_KINDS.includes(component.kind))
        .map(component => text(object(component.config).resident_executor_id))
        .filter(Boolean),
    );
    const functionBlocks = list(programValue?.function_blocks).map(block => ({
      ...clone(block),
      x: finiteNumber(block.x, 50),
      y: finiteNumber(block.y, 50),
      kind: "function_block",
      primitive_kind: "function_block",
      _function_block: true,
      status: "idle",
    }));
    const nodes = [
      ...list(residentNodes)
        .filter(node => (
          !object(node?.operator_executor).component_id
          && !executorResidentIds.has(text(node?.id))
        ))
        .map(node => ({
          ...node,
          primitive_kind: "agent",
        })),
      ...components,
      ...functionBlocks,
    ];
    const nodeIds = new Set(nodes.map(node => text(node?.id)).filter(Boolean));
    const programWiresById = new Map(
      [...program.wires, ...list(programValue?.edges).filter(edge => edge.type === "handoff")]
        .map(wire => [wire.id, wire]),
    );
    const edges = [
      ...program.wires,
    ]
      .map(wire => {
        const projected = projectWire(wire);
        if (projected.type === "watch" && programWiresById.has(projected.target)) {
          const observedWire = programWiresById.get(projected.target);
          return {
            ...projected,
            canonical_target: projected.target,
            watch_subject_wire_id: projected.target,
            target: observedWire.target,
            label: projected.label || window.PortalI18n.t("观察关系"),
          };
        }
        return projected;
      })
      .filter(wire => nodeIds.has(wire.source) && nodeIds.has(wire.target));
    return {
      nodes,
      edges,
      components,
      functionBlocks,
    };
  }

  function portKey(port) {
    return [
      text(port?.node_id),
      text(port?.port_id),
      text(port?.direction),
      text(port?.type),
    ].join(":");
  }

  function portTooltip(portValue) {
    const port = object(portValue);
    const direction = text(port.direction);
    const type = text(port.type);
    const nodeKind = text(port.node_kind);
    if (direction === "in" && type === "invoke") {
      if (nodeKind === "seed") return window.PortalI18n.t("连接 Event，事件发生时激活这个候选。");
      if (nodeKind === "loop") return port.port_id === "done"
        ? window.PortalI18n.t("接收完成信号，结束循环。")
        : window.PortalI18n.t("接收 Event 或结果，推进一轮循环。");
      return nodeKind === "effect"
        ? window.PortalI18n.t("把 Route 连到这里；收到输入后，这个动作会立即执行。")
        : PACKET_OPERATOR_KINDS.includes(nodeKind)
          ? window.PortalI18n.t("把 Route 连到这里；输入会由规则或绑定的 Resident 执行器处理。")
        : window.PortalI18n.t("把 Route 连到这里；收到输入后，这个 Resident 会开始一轮工作。");
    }
    if (direction === "out" && type === "route" && text(port.branch_id)) {
      return window.PortalI18n.text`Selector 分支 ${text(port.label, port.branch_id)}：${text(
        port.branch_description,
        window.PortalI18n.t("匹配到此分支的 Packet 从这里输出。"),
      )}`;
    }
    const outputHelp = {
      route: window.PortalI18n.t("从这里输出消息或事件，沿 Route 交给下游输入。"),
      target: window.PortalI18n.t("从这里为动作绑定作用对象；这条绑定只指定目标，不会启动目标节点。"),
      watch: window.PortalI18n.t("从这里指定事件要监听的对象；监听关系本身不会启动该对象。"),
    };
    return direction === "out" ? text(outputHelp[type]) : "";
  }

  function portsForNode(node) {
    const nodeId = text(node?.id);
    if (!nodeId) return [];
    const kind = primitiveKind(node);
    if (kind === "function_block") return [];
    const definition = MANIFEST.component_kinds[kind] || MANIFEST.resident_kinds[kind];
    // An Effect exposes one Target port per named endpoint in its config.
    const ports = kind === "effect"
      ? [
          ...list(definition?.ports),
          ...list(object(node?.config).endpoints).map(item => text(item)).filter(Boolean).map(endpoint => ({
            id: endpoint, type: "target", direction: "out", label: endpoint,
          })),
        ]
      : kind === "selector"
        ? [
            ...list(definition?.ports),
            ...normalizeSelectorBranches(object(node?.config).branches).map(
              branch => ({
                id: selectorBranchPort(branch.id),
                type: "route",
                direction: "out",
                label: branch.label,
                branch_id: branch.id,
                branch_description: branch.description,
              }),
            ),
          ]
      : list(definition?.ports);
    const contract = watchContract(node);
    const bindingPorts = contract ? [{
      id: "watch", type: "watch", direction: "out", label: window.PortalI18n.t("观察对象"),
      binding: "watch", watch_contract: contract,
    }] : [];
    const labels = {
      invoke: kind === "agent" ? "调用" : kind === "effect" ? "执行"
        : kind === "seed" ? "激活" : kind === "loop" ? "推进" : "输入",
      output: "输出",
      event: "触发", continue: "继续", stop: "结束", done: "完成",
    };
    return [...ports, ...bindingPorts]
      .map((port, index) => {
        const projected = {
          ...clone(port),
          label: port.type === "target" || port.branch_id ? port.label
            : window.PortalI18n.t(labels[port.id] || port.label),
          key: `${nodeId}:${port.direction}:${port.id}:${index}`,
          node_id: nodeId,
          node_kind: kind,
          port_id: port.id,
        };
        return {
          ...projected,
          tooltip: portTooltip(projected),
        };
      });
  }

  function watchContract(node) {
    if (primitiveKind(node) !== "event_source") return null;
    const config = object(node?.config);
    const contract = MANIFEST.event_types[text(config.event_type)]?.watch_contract;
    if (!contract?.allowed_kinds?.length) return null;
    const condition = contract.required_when;
    if (condition) {
      const field = text(condition.path).replace(/^config\./, "");
      const defaultValue = eventFields(config.event_type).find(item => item.path === condition.path)?.default;
      if ((config[field] ?? defaultValue ?? "watched") !== condition.equals) return null;
    }
    return clone(contract);
  }

  // Bindings select an object; activation selects a compatible input.
  function connectionTargets(source, item) {
    if (!item?.id || item.accessible === false || item.visual_only || item._function_block) return [];
    const isRelation = Boolean(item.source && item.target);
    const kind = primitiveKind(item);
    const objectTarget = { node_id: item.id, node_kind: kind, port_id: "", type: "object", direction: "in" };
    if (source?.type === "watch") {
      const subjectKind = isRelation
        ? text(item.type || item.kind) === "handoff" ? "handoff"
          : WIRE_TYPES.includes(text(item.type || item.kind)) ? "wire" : ""
        : kind === "agent" ? "resident" : kind;
      if (!source.watch_contract?.allowed_kinds?.includes(subjectKind)) return [];
      if (subjectKind === "wire" && !["route"].includes(text(item.type || item.kind))) return [];
      return [{ ...objectTarget, node_kind: subjectKind === "resident" ? "agent" : subjectKind }];
    }
    if (isRelation) return [];
    if (source?.type === "target") return [objectTarget];
    return portsForNode(item).filter(port => port.direction === "in");
  }

  function normalizePort(port) {
    const source = object(port);
    return {
      ...clone(source),
      node_id: text(source.node_id),
      node_kind: text(source.node_kind),
      port_id: text(source.port_id),
      type: text(source.type),
      direction: text(source.direction),
    };
  }

  function connectionKey(wire) {
    const normalized = normalizeWire(wire);
    return [
      normalized.type,
      normalized.source,
      normalized.source_port,
      normalized.target,
      normalized.target_port,
    ].join("\u0000");
  }

  function validateConnection(sourceValue, targetValue, options = {}) {
    let source = normalizePort(sourceValue);
    let target = normalizePort(targetValue);
    if (source.direction === "in" && target.direction === "out") {
      [source, target] = [target, source];
    }
    const existingWires = list(options.existingWires).map(normalizeWire);
    if (source.type === "watch") {
      const subjectKind = target.node_kind === "agent" ? "resident" : target.node_kind;
      if (!source.watch_contract?.allowed_kinds?.includes(subjectKind)) {
        return { ok: false, reason: "kind_mismatch", source, target };
      }
      if (subjectKind === "wire") {
        const subject = existingWires.find(wire => wire.id === target.node_id);
        if (!subject || !["route"].includes(subject.type)
) {
          return { ok: false, reason: "kind_mismatch", source, target };
        }
        if (subject.type === "route" && subject.source === source.node_id) {
          return { ok: false, reason: "self_connection", source, target };
        }
      }
      if (!source.watch_contract.multiple
          && existingWires.some(wire => wire.type === "watch" && wire.source === source.node_id)) {
        return { ok: false, reason: "watch_subject_occupied", source, target };
      }
    }
    const binding = ["watch", "target"].includes(source.type);
    if (!source.node_id || !target.node_id || !source.port_id || (!binding && !target.port_id)) {
      return { ok: false, reason: "missing_endpoint", source, target };
    }
    if (source.direction !== "out" || target.direction !== "in") {
      return { ok: false, reason: "direction_mismatch", source, target };
    }
    const invokesTarget = (
      (
        target.port_id === "invoke"
        || (target.node_kind === "loop" && target.port_id === "done")
      )
      && ["route"].includes(source.type)
    );
    const bindsObject = binding && target.type === "object" && !target.port_id;
    if (
      !source.type
      || (!invokesTarget && !bindsObject && source.type !== target.type)
      || !WIRE_TYPES.includes(source.type)
    ) {
      return { ok: false, reason: "type_mismatch", source, target };
    }
    if (source.node_id === target.node_id) {
      return { ok: false, reason: "self_connection", source, target };
    }
    const definition = MANIFEST.wire_types[source.type];
    if (
      !definition
      || !definition.source_kinds.includes(source.node_kind)
      || !definition.target_kinds.includes(target.node_kind)
    ) {
      return { ok: false, reason: "kind_mismatch", source, target };
    }
    if (
      source.type === "route"
      && ["filter", "selector", "reducer"].includes(source.node_kind)
      && ["filter", "selector", "reducer"].includes(target.node_kind)
    ) {
      const operatorIds = new Set(
        list(options.nodes)
          .filter(node => PACKET_OPERATOR_KINDS.includes(primitiveKind(node)))
          .map(node => text(node?.id)),
      );
      operatorIds.add(source.node_id);
      operatorIds.add(target.node_id);
      const routes = new Map([...operatorIds].map(id => [id, new Set()]));
      existingWires.forEach(wire => {
        if (
          wire.type === "route"
          && operatorIds.has(wire.source)
          && operatorIds.has(wire.target)
        ) routes.get(wire.source).add(wire.target);
      });
      routes.get(source.node_id).add(target.node_id);
      const pending = [target.node_id];
      const seen = new Set();
      while (pending.length) {
        const current = pending.pop();
        if (current === source.node_id) {
          return {
            ok: false,
            reason: "packet_operator_cycle",
            source,
            target,
          };
        }
        if (seen.has(current)) continue;
        seen.add(current);
        pending.push(...(routes.get(current) || []));
      }
    }
    if (
      source.type === "target"
      && existingWires.some(wire => (
          wire.type === "target"
          && wire.source === source.node_id
          && wire.source_port === source.port_id
        ))
    ) {
      return {
        ok: false,
        reason: "effect_endpoint_occupied",
        source,
        target,
      };
    }
    const draft = normalizeWire({
      kind: source.type,
      source: source.node_id,
      source_port: source.port_id,
      target: target.node_id,
      target_port: target.port_id,
    });
    const duplicate = existingWires.some(
      wire => connectionKey(wire) === connectionKey(draft)
    );
    if (duplicate) {
      return { ok: false, reason: "duplicate", source, target, type: source.type };
    }
    return {
      ok: true,
      reason: "",
      source,
      target,
      type: source.type,
    };
  }

  function lineageIdentity(lineage) {
    const source = object(lineage);
    return {
      lineage_id: text(source.lineage_id),
    };
  }

  function buildComponentCreateRequest(lineage, kind, position = {}, overrides = {}) {
    if (!MANIFEST.component_kinds[kind]) {
      throw new Error(`Unsupported Lineage program component: ${kind}`);
    }
    const definition = MANIFEST.component_kinds[kind];
    const source = object(overrides);
    return {
      title: definition.default_title,
      config: clone(definition.default_config || {}),
      ...clone(source),
      ...lineageIdentity(lineage),
      kind,
      x: normalizePosition(position.x),
      y: normalizePosition(position.y),
    };
  }

  function buildWireSaveRequest(lineage, source, target, options = {}) {
    const validation = validateConnection(source, target, options);
    if (!validation.ok) {
      const error = new Error(`Invalid Lineage program wire: ${validation.reason}`);
      error.code = validation.reason;
      error.validation = validation;
      throw error;
    }
    return {
      ...lineageIdentity(lineage),
      kind: validation.type,
      source: validation.source.node_id,
      source_port: validation.source.port_id,
      target: validation.target.node_id,
      ...(validation.target.port_id ? { target_port: validation.target.port_id } : {}),
      label: text(options.label),
    };
  }

  global.PortalLineageProgram = Object.freeze({
    schema: "portal.lineage.program.v1",
    manifest: MANIFEST,
    loadPrimitiveDefinitions,
    primitiveDefaultType,
    eventFields,
    normalizedPrimitiveFieldValue,
    componentKinds: COMPONENT_KINDS,
    packetOperatorKinds: PACKET_OPERATOR_KINDS,
    normalizeProgram,
    normalizePacketFilter,
    normalizeSelectorBranches,
    normalizeOptionalLimit,
    authoredProgram,
    primitiveKind,
    projectGraph,
    portsForNode,
    connectionTargets,
    portKey,
    normalizePort,
    validateConnection,
    buildComponentCreateRequest,
    buildWireSaveRequest,
  });
})(window);
