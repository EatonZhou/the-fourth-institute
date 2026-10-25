(() => {
  if (window.PortalChatWorkspaceHarnessPanel) return;

  // A Resident's Agent Harness panel and menu: the harness mode and feature
  // switches with their tooltips, and the prompt injection presets and preview.
  const DEPENDENCIES = Object.freeze([
    "escapeHtml",
  ]);

  function residentAgentHarnessSettings(settings = {}, defaults = {}) {
    const source = settings && typeof settings === "object" ? settings : {};
    const raw = source.agent_harness && typeof source.agent_harness === "object"
      ? source.agent_harness
      : {};
    const overrides = raw.features && typeof raw.features === "object" ? raw.features : {};
    const serverDefaults = defaults && typeof defaults === "object" ? defaults : {};
    const featureIds = new Set([
      ...Object.keys(serverDefaults),
      ...Object.keys(overrides),
    ]);
    const features = Object.fromEntries([...featureIds].map(featureId => {
      const baseline = serverDefaults[featureId] && typeof serverDefaults[featureId] === "object"
        ? serverDefaults[featureId] : {};
      const override = overrides[featureId] && typeof overrides[featureId] === "object"
        ? overrides[featureId] : {};
      return [featureId, {
        ...baseline,
        ...override,
        enabled: typeof override.enabled === "boolean" ? override.enabled : baseline.enabled === true,
      }];
    }));
    return {
      mode: String(raw.mode || "").trim().toLowerCase() === "manual" ? "manual" : "auto",
      features,
    };
  }

  function residentAgentHarnessFeatureConfigUpdate(settings = {}, featureId = "", patch = {}, defaults = {}) {
    const harness = residentAgentHarnessSettings(settings, defaults);
    const id = String(featureId || "").trim();
    if (!id || !Object.prototype.hasOwnProperty.call(harness.features, id)) return {};
    const update = patch && typeof patch === "object" ? patch : {};
    return {
      agent_harness: {
        ...harness,
        features: {
          ...harness.features,
          [id]: {
            ...harness.features[id],
            ...update,
          },
        },
      },
    };
  }

  function residentAgentHarnessFeatureUpdate(settings = {}, featureId = "", enabled = false, defaults = {}) {
    return residentAgentHarnessFeatureConfigUpdate(
      settings,
      featureId,
      { enabled: enabled === true },
      defaults,
    );
  }

  function residentAgentHarnessModeUpdate(settings = {}, automatic = true, defaults = {}) {
    const harness = residentAgentHarnessSettings(settings, defaults);
    return {
      agent_harness: {
        ...harness,
        mode: automatic === true ? "auto" : "manual",
      },
    };
  }

  function residentAgentHarnessFeatureTooltip(featureId = "", config = {}, options = {}) {
    const feature = config && typeof config === "object" ? config : {};
    const supported = options.supported !== false;
    const unsupported = supported
      ? ""
      : window.PortalI18n.t(" 当前后端不具备这个组件需要的能力，因此开关不可用。");
    const thresholdKiB = Math.round(Number(feature.trigger_inline_media_bytes) / 1024);
    const keepRecentImages = Number(feature.keep_recent_images);
    const offloadKiB = Math.round(Number(feature.offload_trigger_bytes) / 1024);
    const minDuplicateTokens = Number(feature.min_output_tokens);
    const descriptions = {
      inline_media_history: window.PortalI18n.text`当内联图片合计超过 ${thresholdKiB} KiB 时，只让模型继续看到最近 ${keepRecentImages} 张。“工具返回后”在下一次模型调用前治理可拦截的 Harness 工具图片。旧图片仍留在 Portal 对话里，只退出模型的活跃上下文；通常能显著降低长图片会话的输入量和等待时间，但重新分析旧图时需要再次把它带回上下文。`,
      tool_call_granularity: window.PortalI18n.t("在每轮 Prompt 中加入通用执行策略，鼓励模型合并或并行互不依赖的只读搜索、读取和检查，从而减少模型与工具之间的往返轮次。它不会强制模型采纳，也不会并行写入、破坏性操作或有先后依赖的步骤。"),
      tool_output_offloading: window.PortalI18n.text`当单条工具输出超过 ${offloadKiB} KiB 时，活跃上下文只保留首尾预览、哈希和安全句柄，精确原文完整存入 Portal，并可通过 read_harness_artifact 分块召回。“工具返回后”会在下一次模型调用前立即治理 Harness 工具结果，召回工具在新会话开始时挂载；后端自己执行的内置 shell/read 工具结果不经过 Portal，不能拦截。它通常能大幅减少超长日志或命令输出的 tokens；如果关键细节不在预览中，模型会多一次召回操作。`,
      artifact_search_and_slice: window.PortalI18n.t("让模型先在已外置的大型工具输出中做本地关键词搜索，只接收少量命中片段和精确偏移，再按需读取目标切片。它不会搜索未授权给当前 session 的产物，通常比从头分块读取整份日志省很多 tokens；仅对已被“限制并外置大型工具输出”保存的内容有收益，工具在新会话开始时挂载。"),
      hash_aware_read_dedup: window.PortalI18n.text`同一只读工具的工具名、参数和内容哈希完全相同，且输出至少约 ${minDuplicateTokens} tokens 时，只保留一份精确原文。“工具返回后”会在同一 Turn 的下一次模型调用前立即去重 Portal 执行的工具结果；后端自己执行的内置 shell/read 工具结果不经过 Portal，不能拦截。它能安全移除重复读取产生的 tokens；写入操作、参数不同或内容变化的结果不会合并。`,
      unified_resource_recall: window.PortalI18n.t("当当前消息明确引用统一资源 ID 时，让模型先通过 ResourceService 在正文中本地搜索，再只读取命中附近的精确切片。正文不会预先塞进 Prompt，仍按当前用户重新鉴权；它适合大型 Decision、文件或会话归档，能避免整份内容进入上下文。工具在新会话开始时挂载，关闭时 Portal 原有资源读取方式不受影响。"),
      early_context_compaction: window.PortalI18n.text`压缩阈值由后端策略参数计算。“Turn 前检查”会在发送新 Prompt 前检查并按需 compact 已有 Session；“Agent Loop 自动”会配置 Provider 原生阈值，让工具结果推高上下文后在下一次模型调用前自动 compact。它不引入 Portal 自己的摘要模型，但 compact 仍可能丢失细节，保真度取决于 Provider 原生机制。`,
      native_tool_loop_governor: window.PortalI18n.text`观察后端的原生工具事件，维护语义读取账本。后端支持运行中输入时，在工具调用、重复读取或大型输出达到阈值时注入最多 ${Number(feature.max_checkpoints)} 次软检查点；不支持运行中输入的后端只记账，供下一轮策略使用。它会统计内置 shell/read 的输出压力，但不能改写后端自己执行的工具结果。`,
      provider_error_continue: window.PortalI18n.t("独立开关，在智能选择与手动模式下都生效，自动策略不会覆盖你的选择。开启后，Provider 接手这一轮之后出错时，在报错 3 秒后于同一会话补发一次 continue。用户取消（包括这 3 秒内取消）、额度用完、凭证或安全类错误、上下文超长都不补发。补发后再失败，这一轮按失败结束，不会循环。"),
    };
    return `${descriptions[String(featureId || "").trim()] || ""}${unsupported}`.trim();
  }

  const PROMPT_INJECTION_PRESET_LABELS = {
    standard: "标准", full: "每轮完整", minimal: "精简", pure: "纯 TUI", custom: "自定义",
  };
  const PROMPT_INJECTION_PREVIEW_TURNS = [
    ["first", "会话第一轮"],
    ["later", "之后的轮次"],
    ["after_compaction", "上下文压缩后的第一轮"],
  ];

  function promptInjectionPresetLabel(value) {
    const preset = String(value?.preset || "standard");
    return PROMPT_INJECTION_PRESET_LABELS[preset] || preset;
  }

  function createHarnessPanel(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalChatWorkspaceHarnessPanel is missing: ${missing.join(", ")}`);
    const {
      escapeHtml,
    } = dependencies;

    // The Prompt 注入 panel: a preset, each category's timing with the place the
    // Provider puts it, and a preview of what a turn would carry.
    function residentPromptInjectionPanelHtml(view = {}, options = {}) {
      const busy = options.busy === true;
      const preview = options.preview || null;
      const turn = options.previewTurn || "first";
      const presets = (view.presets || []).filter(item => item.id !== "custom" || view.preset === "custom");
      const option = (value, label, selected, disabled = false) =>
        `<option value="${escapeHtml(value)}" ${selected ? "selected" : ""} ${disabled ? "disabled" : ""}>${escapeHtml(label)}</option>`;
      const rows = (view.parts || []).map(part => `<label class="chatHarnessTiming chatPromptInjectionRow">
        <span class="chatHarnessTimingText"><strong>${escapeHtml(part.label)}</strong><small>${escapeHtml(part.description)}</small><small class="chatPromptInjectionPlace">位置：${escapeHtml(part.placement || "")}</small></span>
        <select data-prompt-injection-part="${escapeHtml(part.id)}" aria-label="${escapeHtml(part.label)}注入时机" ${busy ? "disabled" : ""}>
          ${(part.choices || []).map(choice => option(choice.id, choice.label, choice.id === part.choice)).join("")}
        </select>
      </label>`).join("");
      const previewParts = preview
        ? (preview.parts || []).map(part => `<li class="${part.sent ? "sent" : ""}">${part.sent ? "发送" : "不发送"} · ${escapeHtml(part.label)} · ${Number(part.chars || 0)} 字</li>`).join("")
        : "";
      const previewText = preview
        ? `【系统位置】\n${preview.system_prompt || "（无）"}\n\n【本轮消息】\n${preview.prompt || ""}`
        : (options.previewError ? `预览失败：${options.previewError}` : "正在生成预览...");
      return `<div class="chatPromptInjection">
      <button type="button" class="chatPromptInjectionBack" data-prompt-injection-back>‹ Prompt 注入</button>
      <label class="chatHarnessTiming chatPromptInjectionRow">
        <span class="chatHarnessTimingText"><strong>预设</strong><small>纯 TUI 不注入任何 Portal 内容；改单个类别后记为自定义</small></span>
        <select data-prompt-injection-preset aria-label="Prompt 注入预设" ${busy ? "disabled" : ""}>
          ${presets.map(item => option(item.id, item.label, item.id === view.preset, item.id === "custom")).join("")}
        </select>
      </label>
      ${rows}
      <div class="chatPromptInjectionPreview">
        <label class="chatHarnessTiming chatPromptInjectionRow">
          <span class="chatHarnessTimingText"><strong>预览</strong><small>消息正文用占位文字</small></span>
          <select data-prompt-injection-preview-turn aria-label="预览的轮次">
            ${PROMPT_INJECTION_PREVIEW_TURNS.map(([value, label]) => option(value, label, value === turn)).join("")}
          </select>
        </label>
        ${previewParts ? `<ul class="chatPromptInjectionParts">${previewParts}</ul>` : ""}
        <pre class="chatPromptInjectionText" data-prompt-injection-preview>${escapeHtml(previewText)}</pre>
      </div>
    </div>`;
    }

    // The Resident Menu's Harness page: Prompt 注入, sub agent, 失败续跑 and
    // the Harness features, saved as the Resident's own settings.
    function mountResidentHarnessPanel({ container, residentId, onChanged } = {}) {
      if (!container || !residentId) throw new TypeError("container and residentId are required");
      let detail = null;
      let loadError = "";
      let busy = false;
      let destroyed = false;
      let promptInjection = null;
      const execute = (instruction, args, options) => window.PortalInstructionClient.execute(instruction, args, options);
      const load = async ({ force = false } = {}) => {
        try {
          detail = await window.PortalResidentProjection.load(residentId, { force });
          loadError = "";
        } catch (error) {
          loadError = error?.message || String(error);
        }
        render();
      };
      const run = async action => {
        if (busy || destroyed) return;
        busy = true;
        render();
        try {
          await action();
        } catch (error) {
          if (!destroyed) window.PortalUI?.toast?.(error?.message || String(error), "error");
        } finally {
          busy = false;
          render();
        }
      };
      const refresh = async () => {
        window.PortalResidentProjection?.invalidate?.(residentId);
        await onChanged?.();
        await load({ force: true });
      };
      const save = changes => run(async () => {
        await execute("UpdateResidentSettings", { resident_id: residentId, changes });
        await refresh();
      });

      function render() {
        if (destroyed) return;
        if (promptInjection) {
          renderPromptInjection();
          return;
        }
        if (loadError) {
          container.innerHTML = window.PortalI18n.html`<div class="paneNote" role="status">Harness 设置读取失败：${escapeHtml(loadError)}</div>`;
          return;
        }
        if (!detail) {
          container.innerHTML = window.PortalI18n.markup('<div class="chatHarnessLoading">正在读取 Harness 设置...</div>');
          return;
        }
        const settings = detail.runtime_settings || {};
        const harnessDefaults = detail.agent_harness_defaults || {};
        container.innerHTML = `<div class="residentHarnessPanel">${residentHarnessMenuHtml(settings, {
        busy,
        runtimeCapabilities: detail.backend_capabilities?.runtime_capabilities,
        harnessDefaults,
      })}</div>`;
        const root = container.firstElementChild;
        root.querySelector("[data-sub-agent-toggle]")?.addEventListener("change", event => {
          save({ allow_subagents: !!event.currentTarget.checked });
        });
        root.querySelector("[data-prompt-injection-open]")?.addEventListener("click", () => openPromptInjection());
        root.querySelector("[data-agent-harness-mode-toggle]")?.addEventListener("change", event => {
          save(residentAgentHarnessModeUpdate(settings, !!event.currentTarget.checked, harnessDefaults));
        });
        root.querySelectorAll("[data-agent-harness-feature-toggle]").forEach(control => {
          control.addEventListener("change", () => save(residentAgentHarnessFeatureUpdate(settings, control.dataset.agentHarnessFeatureToggle, control.checked, harnessDefaults)));
        });
        root.querySelectorAll("[data-agent-harness-feature-timing]").forEach(control => {
          control.addEventListener("change", () => save(residentAgentHarnessFeatureConfigUpdate(settings, control.dataset.agentHarnessFeatureTiming, { timing: control.value }, harnessDefaults)));
        });
      }

      async function openPromptInjection() {
        if (busy) return;
        const state = { view: null, preview: null, previewTurn: "first", previewError: "" };
        promptInjection = state;
        await run(async () => {
          const result = await execute("GetResidentSettings", { resident_id: residentId });
          if (promptInjection === state) state.view = result?.prompt_injection || null;
        });
        if (promptInjection !== state) return;
        if (!state.view) {
          // A failed read goes back to the Harness rows instead of a stuck loading line.
          promptInjection = null;
          render();
          return;
        }
        loadPromptInjectionPreview();
      }

      async function loadPromptInjectionPreview() {
        const state = promptInjection;
        if (!state || destroyed) return;
        const turn = state.previewTurn;
        try {
          const preview = await execute("PreviewPromptInjection", { resident_id: residentId, turn });
          if (promptInjection !== state || state.previewTurn !== turn) return;
          state.preview = preview;
          state.previewError = "";
        } catch (error) {
          if (promptInjection !== state) return;
          state.preview = null;
          state.previewError = error?.message || String(error);
        }
        render();
      }

      function renderPromptInjection() {
        const state = promptInjection;
        if (!state.view) {
          container.innerHTML = window.PortalI18n.markup('<div class="chatHarnessLoading">正在读取 Prompt 注入设置...</div>');
          return;
        }
        container.innerHTML = `<div class="residentHarnessPanel">${residentPromptInjectionPanelHtml(state.view, {
        busy,
        preview: state.preview,
        previewTurn: state.previewTurn,
        previewError: state.previewError,
      })}</div>`;
        const root = container.firstElementChild;
        const update = changes => run(async () => {
          const result = await execute("UpdateResidentSettings", {
            resident_id: residentId, changes: { prompt_injection: changes },
          }, { idempotencyKey: `prompt-injection:${globalThis.PortalUIPrimitives.randomUUID()}` });
          if (promptInjection !== state) return;
          state.view = result.prompt_injection;
          state.preview = null;
          await refresh();
          loadPromptInjectionPreview();
        });
        root.querySelector("[data-prompt-injection-back]")?.addEventListener("click", () => {
          promptInjection = null;
          render();
        });
        root.querySelector("[data-prompt-injection-preset]")?.addEventListener("change", event => {
          update({ preset: event.currentTarget.value });
        });
        root.querySelectorAll("[data-prompt-injection-part]").forEach(control => {
          control.addEventListener("change", () => update({ parts: { [control.dataset.promptInjectionPart]: control.value } }));
        });
        root.querySelector("[data-prompt-injection-preview-turn]")?.addEventListener("change", event => {
          state.previewTurn = event.currentTarget.value;
          state.preview = null;
          render();
          loadPromptInjectionPreview();
        });
      }

      render();
      void load();
      return {
        reload: () => load({ force: true }),
        destroy() {
          destroyed = true;
          container.replaceChildren();
        },
      };
    }

    function residentHarnessMenuHtml(settings = {}, options = {}) {
      if (!options.harnessDefaults || !Object.keys(options.harnessDefaults).length) {
        return window.PortalI18n.markup('<div class="chatHarnessLoading">正在读取扩展配置...</div>');
      }
      const busy = options.busy === true;
      const supportedFeatures = new Set(options.runtimeCapabilities?.harness_features || []);
      const harness = residentAgentHarnessSettings(settings, options.harnessDefaults || {});
      const harnessAutomatic = harness.mode === "auto";
      const harnessControlsDisabled = busy || harnessAutomatic;
      const inlineMedia = harness.features.inline_media_history;
      const toolGranularity = harness.features.tool_call_granularity;
      const toolOutputOffloading = harness.features.tool_output_offloading;
      const artifactSearchAndSlice = harness.features.artifact_search_and_slice;
      const readDedup = harness.features.hash_aware_read_dedup;
      const unifiedResourceRecall = harness.features.unified_resource_recall;
      const earlyCompaction = harness.features.early_context_compaction;
      const nativeToolLoop = harness.features.native_tool_loop_governor;
      const nativeToolLoopBudgetKnown = Number.isFinite(nativeToolLoop.checkpoint_interval)
        && Number.isFinite(nativeToolLoop.max_checkpoints);
      const providerErrorContinue = harness.features.provider_error_continue;
      const inlineMediaSupported = supportedFeatures.has("inline_media_history");
      const toolOutputOffloadingSupported = supportedFeatures.has("tool_output_offloading");
      const artifactSearchAndSliceSupported = supportedFeatures.has("artifact_search_and_slice");
      const readDedupSupported = supportedFeatures.has("hash_aware_read_dedup");
      const unifiedResourceRecallSupported = supportedFeatures.has("unified_resource_recall");
      const earlyCompactionSupported = supportedFeatures.has("early_context_compaction");
      const nativeToolLoopSupported = supportedFeatures.has("native_tool_loop_governor");
      const providerErrorContinueSupported = supportedFeatures.has("provider_error_continue");
      // Only a Provider whose model can start subagents has the switch (capability "subagents").
      const subagentsSupported = (options.runtimeCapabilities?.harness_capabilities || []).includes("subagents");
      const inlineMediaPolicy = `超过 ${Math.round(inlineMedia.trigger_inline_media_bytes / 1024)} KiB 时仅保留最新 ${inlineMedia.keep_recent_images} 张，旧图片退出活跃上下文`;
      const harnessTooltip = (featureId, featureConfig, supported = true) => escapeHtml(
        residentAgentHarnessFeatureTooltip(featureId, featureConfig, { supported }),
      );
      const harnessTimingControl = (featureId, featureConfig, label, options = {}) => {
        const supported = options.supported !== false;
        const selectable = options.selectable !== false;
        const timing = ["before_turn", "after_tool_batch", "both"].includes(String(featureConfig?.timing || ""))
          ? String(featureConfig.timing)
          : String(options.defaultTiming || "both");
        const detail = String(options.detail || "工具返回后目前覆盖 Harness 工具");
        if (!selectable) {
          const fixedLabel = String(options.fixedLabel || ({
            before_turn: window.PortalI18n.t("发送前"),
            after_tool_batch: window.PortalI18n.t("工具返回后"),
            both: window.PortalI18n.t("发送前 + 工具返回后"),
          }[timing] || timing));
          return window.PortalI18n.html`<div class="chatHarnessTiming" data-agent-harness-timing-display="${escapeHtml(featureId)}" ${supported ? "" : "aria-disabled=\"true\""}>
          <span class="chatHarnessTimingText"><strong>运行时机</strong><small>${escapeHtml(detail)}</small></span>
          <output class="chatHarnessTimingValue" aria-label="${escapeHtml(label)}">${escapeHtml(fixedLabel)}</output>
        </div>`;
        }
        const timingOptions = Array.isArray(options.timingOptions) && options.timingOptions.length
          ? options.timingOptions
          : [
            ["both", "两处都处理（推荐）"],
            ["before_turn", "仅发送前历史"],
            ["after_tool_batch", "仅工具返回后"],
          ];
        const option = (value, text) => window.PortalI18n.html`<option value="${value}" ${timing === value ? "selected" : ""}>${text}</option>`;
        return window.PortalI18n.html`<label class="chatHarnessTiming">
        <span class="chatHarnessTimingText"><strong>运行时机</strong><small>${escapeHtml(detail)}</small></span>
        <select data-agent-harness-feature-timing="${escapeHtml(featureId)}" aria-label="${escapeHtml(label)}" ${harnessControlsDisabled || !supported ? "disabled" : ""}>
          ${timingOptions.map(([value, text]) => option(value, text)).join("")}
        </select>
      </label>`;
      };
      return [
        window.PortalI18n.html`<button type="button" class="chatSessionSwitch chatSessionNav" data-prompt-injection-open ${busy ? "disabled" : ""}>
        <span class="chatSessionSwitchText"><strong>Prompt 注入</strong><small>${escapeHtml(promptInjectionPresetLabel(settings.prompt_injection))} · 按类别选择 Portal 规则、身份与授权等内容的注入时机</small></span>
        <span class="chatSessionNavArrow" aria-hidden="true">›</span>
      </button>`,
        subagentsSupported ? window.PortalI18n.html`<label class="chatSessionSwitch">
        <span class="chatSessionSwitchText"><strong>允许开启 sub agent</strong><small>关闭时模型不能启动子 agent</small></span>
        <input type="checkbox" role="switch" data-sub-agent-toggle ${settings.allow_subagents ? "checked" : ""} ${busy ? "disabled" : ""} />
        <span class="chatSessionSwitchTrack" aria-hidden="true"></span>
      </label>` : "",
        window.PortalI18n.html`<label class="chatSessionSwitch">
        <span class="chatSessionSwitchText"><strong>失败后续跑一次</strong><small>${providerErrorContinueSupported ? window.PortalI18n.t("独立开关；失败后自动发送一次 continue，主动取消不触发。不受智能选择模式限制") : window.PortalI18n.t("当前后端不具备这个组件需要的能力")}</small></span>
        <input type="checkbox" role="switch" data-agent-harness-feature-toggle="provider_error_continue" data-portal-tooltip="${harnessTooltip("provider_error_continue", providerErrorContinue, providerErrorContinueSupported)}" ${providerErrorContinue.enabled ? "checked" : ""} ${busy || !providerErrorContinueSupported ? "disabled" : ""} />
        <span class="chatSessionSwitchTrack" aria-hidden="true"></span>
      </label>`,
        window.PortalI18n.html`<div class="chatExtensionDivider" role="separator"></div>`,
        window.PortalI18n.html`<label class="chatSessionSwitch">
        <span class="chatSessionSwitchText"><strong>智能选择 Harness</strong><small>根据本 Session 的上下文压力、图片、大型输出、重复读取和工具调用反馈，逐 Turn 选择功能、时机与预算；开启时下方只显示保留的手动预设</small></span>
        <input type="checkbox" role="switch" data-agent-harness-mode-toggle ${harnessAutomatic ? "checked" : ""} ${busy ? "disabled" : ""} />
        <span class="chatSessionSwitchTrack" aria-hidden="true"></span>
      </label>`,
        window.PortalI18n.html`<label class="chatSessionSwitch">
        <span class="chatSessionSwitchText"><strong>治理原生工具循环</strong><small>${nativeToolLoopSupported ? (nativeToolLoopBudgetKnown ? window.PortalI18n.text`每 ${nativeToolLoop.checkpoint_interval} 次工具调用检查一次，最多软干预 ${nativeToolLoop.max_checkpoints} 次` : window.PortalI18n.t("使用服务端默认预算")) : window.PortalI18n.t("当前后端不具备这个组件需要的能力")}</small></span>
        <input type="checkbox" role="switch" data-agent-harness-feature-toggle="native_tool_loop_governor" data-portal-tooltip="${harnessTooltip("native_tool_loop_governor", nativeToolLoop, nativeToolLoopSupported)}" ${nativeToolLoop.enabled ? "checked" : ""} ${harnessControlsDisabled || !nativeToolLoopSupported ? "disabled" : ""} />
        <span class="chatSessionSwitchTrack" aria-hidden="true"></span>
      </label>`,
        harnessTimingControl(
          "native_tool_loop_governor",
          nativeToolLoop,
          "原生工具循环治理运行时机",
          {
            supported: nativeToolLoopSupported,
            selectable: false,
            fixedLabel: "Agent Loop · 原生事件 + steer",
            detail: "共享 reader 只记账；实际 steer 由 Turn 执行线程发送",
          },
        ),
        window.PortalI18n.html`<label class="chatSessionSwitch">
        <span class="chatSessionSwitchText"><strong>治理历史内联图片</strong><small>${inlineMediaSupported ? inlineMediaPolicy : window.PortalI18n.t("当前后端不具备这个组件需要的能力")}</small></span>
        <input type="checkbox" role="switch" data-agent-harness-feature-toggle="inline_media_history" data-portal-tooltip="${harnessTooltip("inline_media_history", inlineMedia, inlineMediaSupported)}" ${inlineMedia.enabled ? "checked" : ""} ${harnessControlsDisabled || !inlineMediaSupported ? "disabled" : ""} />
        <span class="chatSessionSwitchTrack" aria-hidden="true"></span>
      </label>`,
        harnessTimingControl(
          "inline_media_history",
          inlineMedia,
          "内联图片治理运行时机",
          {
            selectable: false, fixedLabel: "工具返回后",          supported: inlineMediaSupported,
            detail: "工具返回后仅覆盖可拦截的 Harness 工具图片",
          },
        ),
        window.PortalI18n.html`<label class="chatSessionSwitch">
        <span class="chatSessionSwitchText"><strong>批量化独立工具调用</strong><small>鼓励合并或并行只读的搜索、读取与检查；写入和依赖步骤仍保持串行</small></span>
        <input type="checkbox" role="switch" data-agent-harness-feature-toggle="tool_call_granularity" data-portal-tooltip="${harnessTooltip("tool_call_granularity", toolGranularity)}" ${toolGranularity.enabled ? "checked" : ""} ${harnessControlsDisabled ? "disabled" : ""} />
        <span class="chatSessionSwitchTrack" aria-hidden="true"></span>
      </label>`,
        harnessTimingControl(
          "tool_call_granularity",
          toolGranularity,
          "批量化独立工具调用运行时机",
          {
            selectable: false,
            fixedLabel: "发送前 · Prompt 策略",
            detail: "每轮 Prompt 组装时注入通用执行策略",
          },
        ),
        window.PortalI18n.html`<label class="chatSessionSwitch">
        <span class="chatSessionSwitchText"><strong>限制并外置大型工具输出</strong><small>${toolOutputOffloadingSupported ? window.PortalI18n.text`单条超过 ${Math.round(toolOutputOffloading.offload_trigger_bytes / 1024)} KiB 时，仅保留预览与可召回的精确原文` : window.PortalI18n.t("当前后端不具备这个组件需要的能力")}</small></span>
        <input type="checkbox" role="switch" data-agent-harness-feature-toggle="tool_output_offloading" data-portal-tooltip="${harnessTooltip("tool_output_offloading", toolOutputOffloading, toolOutputOffloadingSupported)}" ${toolOutputOffloading.enabled ? "checked" : ""} ${harnessControlsDisabled || !toolOutputOffloadingSupported ? "disabled" : ""} />
        <span class="chatSessionSwitchTrack" aria-hidden="true"></span>
      </label>`,
        harnessTimingControl(
          "tool_output_offloading",
          toolOutputOffloading,
          "大型工具输出运行时机",
          { supported: toolOutputOffloadingSupported },
        ),
        window.PortalI18n.html`<label class="chatSessionSwitch">
        <span class="chatSessionSwitchText"><strong>搜索并切片外置输出</strong><small>${artifactSearchAndSliceSupported ? window.PortalI18n.t("先搜索命中位置，再读取少量精确片段；需配合已外置的工具输出") : window.PortalI18n.t("当前后端不具备这个组件需要的能力")}</small></span>
        <input type="checkbox" role="switch" data-agent-harness-feature-toggle="artifact_search_and_slice" data-portal-tooltip="${harnessTooltip("artifact_search_and_slice", artifactSearchAndSlice, artifactSearchAndSliceSupported)}" ${artifactSearchAndSlice.enabled ? "checked" : ""} ${harnessControlsDisabled || !artifactSearchAndSliceSupported ? "disabled" : ""} />
        <span class="chatSessionSwitchTrack" aria-hidden="true"></span>
      </label>`,
        harnessTimingControl(
          "artifact_search_and_slice",
          artifactSearchAndSlice,
          "外置输出搜索运行时机",
          {
            supported: artifactSearchAndSliceSupported,
            selectable: false,
            fixedLabel: "发送前 · 工具注册",
            detail: "进入 Provider 前注册搜索与切片召回工具",
          },
        ),
        window.PortalI18n.html`<label class="chatSessionSwitch">
        <span class="chatSessionSwitchText"><strong>去重重复只读结果</strong><small>${readDedupSupported ? window.PortalI18n.text`同一只读调用返回完全相同内容时，仅保留最新原文（至少 ${readDedup.min_output_tokens} tokens）` : window.PortalI18n.t("当前后端不具备这个组件需要的能力")}</small></span>
        <input type="checkbox" role="switch" data-agent-harness-feature-toggle="hash_aware_read_dedup" data-portal-tooltip="${harnessTooltip("hash_aware_read_dedup", readDedup, readDedupSupported)}" ${readDedup.enabled ? "checked" : ""} ${harnessControlsDisabled || !readDedupSupported ? "disabled" : ""} />
        <span class="chatSessionSwitchTrack" aria-hidden="true"></span>
      </label>`,
        harnessTimingControl(
          "hash_aware_read_dedup",
          readDedup,
          "只读结果去重运行时机",
          { supported: readDedupSupported },
        ),
        window.PortalI18n.html`<label class="chatSessionSwitch">
        <span class="chatSessionSwitchText"><strong>按统一资源 ID 召回正文</strong><small>${unifiedResourceRecallSupported ? window.PortalI18n.t("先搜索明确引用的资源，再按偏移读取精确切片") : window.PortalI18n.t("当前后端不具备这个组件需要的能力")}</small></span>
        <input type="checkbox" role="switch" data-agent-harness-feature-toggle="unified_resource_recall" data-portal-tooltip="${harnessTooltip("unified_resource_recall", unifiedResourceRecall, unifiedResourceRecallSupported)}" ${unifiedResourceRecall.enabled ? "checked" : ""} ${harnessControlsDisabled || !unifiedResourceRecallSupported ? "disabled" : ""} />
        <span class="chatSessionSwitchTrack" aria-hidden="true"></span>
      </label>`,
        harnessTimingControl(
          "unified_resource_recall",
          unifiedResourceRecall,
          "统一资源召回运行时机",
          {
            supported: unifiedResourceRecallSupported,
            selectable: false,
            fixedLabel: "发送前 · 仅新 Session",
            detail: "创建新 Backend Session 前注册资源召回工具",
          },
        ),
        window.PortalI18n.html`<label class="chatSessionSwitch">
        <span class="chatSessionSwitchText"><strong>按后端策略提前 compact</strong><small>${earlyCompactionSupported ? window.PortalI18n.text`配置后端原生 compact 的触发位置` : window.PortalI18n.t("当前后端不具备这个组件需要的能力")}</small></span>
        <input type="checkbox" role="switch" data-agent-harness-feature-toggle="early_context_compaction" data-portal-tooltip="${harnessTooltip("early_context_compaction", earlyCompaction, earlyCompactionSupported)}" ${earlyCompaction.enabled ? "checked" : ""} ${harnessControlsDisabled || !earlyCompactionSupported ? "disabled" : ""} />
        <span class="chatSessionSwitchTrack" aria-hidden="true"></span>
      </label>`,
        harnessTimingControl(
          "early_context_compaction",
          earlyCompaction,
          "提前 compact 运行时机",
          {
            supported: earlyCompactionSupported,
            defaultTiming: "after_tool_batch",
            detail: "阈值固定为 70%；Agent Loop 模式使用 Provider 原生 compact",
            timingOptions: [
              ["after_tool_batch", "Agent Loop 自动（推荐）"],
              ["before_turn", "仅 Turn 前检查"],
              ["both", "两处都检查"],
            ],
          },
        ),
      ].join("");
    }

    return Object.freeze({
      residentAgentHarnessSettings, mountResidentHarnessPanel, residentHarnessMenuHtml,
    });
  }

  window.PortalChatWorkspaceHarnessPanel = Object.freeze({
    createHarnessPanel, dependencies: DEPENDENCIES, residentAgentHarnessSettings,
  });
})();
