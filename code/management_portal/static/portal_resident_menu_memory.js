(() => {
  "use strict";

  if (window.PortalResidentMenuMemory) return;

  // The Memory tab (decision_13725a7900f6): what enters this Resident's
  // context, by the four memory levels, each item with its source. Duty
  // prompts come from the duty records in the Lineage and open in the Duties
  // tab; trigger-time items include the task of every wire that triggers the
  // Resident; the platform's parts come from PreviewPromptInjection and their
  // timing is changed in the Harness tab.

  const DEPENDENCIES = Object.freeze(["$id", "esc", "focusNodeDuty", "menuState", "switchNodeSettingsTab"]);

  const LEVELS = Object.freeze([
    ["trigger", "触发时", "只在相应连线触发这个 Resident 的那一轮"],
    ["turn", "每轮", "每一轮都在，越少越好"],
    ["session", "会话开始和压缩后", "会话启动、重置和压缩之后"],
    ["on_demand", "按需读取", "平台写入 Workspace 的 duty-manuals.md，Resident 需要时自己读；职责清单里留一行指针"],
  ]);
  // Generated from the duty records; the duties themselves are listed instead.
  const DUTY_PARTS = new Set(["duty_memory", "duty_memory_turn"]);
  const PART_SOURCES = Object.freeze({
    core_rules: "平台规则", resident_md: "resident.md", runtime_identity: "平台", authorization_context: "平台",
    turn_skills: "消息要求的 Skill", business_instructions: "业务说明", family_work_guide: "Family",
    personal_assistant: "个人助手", reporting_policy: "汇报策略",
  });

  function createMemory(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalResidentMenuMemory is missing: ${missing.join(", ")}`);
    const { $id, esc, focusNodeDuty, menuState, switchNodeSettingsTab } = dependencies;
    const t = text => window.PortalI18n.t(text);

    // A wire's task: literal text, joined pieces, or a value read from the event.
    function promptText(spec) {
      if (spec == null) return "";
      if (typeof spec === "string") return spec;
      if (typeof spec.value === "string") return spec.value;
      if (Array.isArray(spec.concat)) return spec.concat.map(promptText).filter(Boolean).join(spec.separator ?? "\n\n");
      if (spec.from) return t("（触发事件带来的内容）");
      return "";
    }

    function memoryItems(lineage, preview, residentId) {
      const items = Object.fromEntries(LEVELS.map(([level]) => [level, []]));
      const titles = new Map([
        ...(lineage.nodes || []).map(node => [node.id, node.title || node.id]),
        ...(lineage.program?.components || []).map(item => [item.id, item.title || item.id]),
      ]);
      const held = (lineage.function_blocks || [])
        .filter(block => block.duty?.holder_id === residentId)
        .sort((a, b) => a.duty.position - b.duty.position);
      const dutyOfWire = new Map();
      for (const block of held) {
        for (const element of block.elements || []) if (element.kind === "wire") dutyOfWire.set(element.id, block);
        const memory = block.duty.memory;
        if (!memory) continue;
        items[memory.level]?.push({
          title: block.duty.description || block.title, source: t("职责"), content: memory.prompt, dutyId: block.id,
        });
      }
      for (const wire of lineage.program?.wires || []) {
        if (wire.target !== residentId || wire.kind === "watch") continue;
        const duty = dutyOfWire.get(wire.id);
        const task = promptText(wire.config?.invoke_profiles?.agent?.arguments?.prompt ?? wire.config?.prompt);
        items.trigger.push({
          title: window.PortalI18n.text`${titles.get(wire.source) || wire.source} 触发`,
          source: duty ? window.PortalI18n.text`连线 · 职责“${duty.duty.description || duty.title}”` : t("连线"),
          content: task || t("（这条连线没有写任务说明，送达时使用默认提示）"),
          dutyId: duty?.id || "",
        });
      }
      for (const part of preview?.parts || []) {
        if (part.id === "duty_memory_turn") continue;
        const level = part.timing === "every_turn" ? "turn"
          : String(part.timing || "").startsWith("session_start") ? "session" : "";
        if (!level) continue;
        items[level].push({
          title: part.id === "duty_memory" ? t("职责清单（平台生成，含“会话开始”职责的提示词）") : part.label,
          source: DUTY_PARTS.has(part.id) ? t("职责记录") : t(PART_SOURCES[part.id] || "平台"),
          content: part.content, harness: !DUTY_PARTS.has(part.id),
        });
      }
      return items;
    }

    function itemHtml(item) {
      const action = item.dutyId
        ? window.PortalI18n.html`<button class="portalButton is-compact" type="button" data-memory-duty="${esc(item.dutyId)}">在职责页编辑</button>`
        : item.harness
          ? window.PortalI18n.html`<button class="portalButton is-compact" type="button" data-memory-harness>在 Harness 中调整发送时机</button>`
          : "";
      return window.PortalI18n.html`<li class="residentMenuMemoryItem">
        <details>
          <summary><span class="residentMenuDutyText">${esc(item.title)}</span><span class="residentMenuDutyCategory">${esc(item.source)}</span></summary>
          <div class="residentMenuDutyBody"><pre class="residentMenuDutyPrompt">${esc(item.content)}</pre>${action ? window.PortalI18n.html`<div class="residentMenuDutyActions">${action}</div>` : ""}</div>
        </details>
      </li>`;
    }

    function render(items) {
      $id("nodeMemoryRows").innerHTML = window.PortalI18n.html`<p class="paneNote">按四级列出进入这个 Resident 上下文的内容和来源，越靠上的层级占用上下文越多。展开一项可看到正文。</p>`
        + LEVELS.map(([level, label, note]) => window.PortalI18n.html`<section class="residentMenuMemoryLevel" data-memory-level="${esc(level)}">
          <h3>${esc(t(label))} <span class="residentMenuDutyCategory">${esc(String(items[level].length))}</span></h3>
          <p class="paneNote">${esc(t(note))}</p>
          ${items[level].length
            ? window.PortalI18n.html`<ol class="residentMenuDuties">${items[level].map(itemHtml).join("")}</ol>`
            : window.PortalI18n.html`<p class="residentMenuJournalMeta">没有内容。</p>`}
        </section>`).join("");
    }

    async function loadNodeMemory() {
      const context = menuState.nodeMenuContext;
      const lineageId = menuState.lineage?.lineage_id;
      if (!context || !lineageId) return;
      const root = $id("nodeMemoryRows");
      root.textContent = t("正在读取记忆…");
      try {
        const [lineage, preview] = await Promise.all([
          window.PortalInstructionClient.execute("GetLineage", {lineage_id: lineageId}).then(result => result.lineage),
          window.PortalInstructionClient.execute("PreviewPromptInjection", {resident_id: context.residentId, turn: "first"}),
        ]);
        if (menuState.nodeMenuContext !== context) return;
        render(memoryItems(lineage, preview, context.residentId));
      } catch (error) {
        if (menuState.nodeMenuContext === context) root.textContent = window.PortalI18n.text`记忆读取失败：${error.message || error}`;
      }
    }

    $id("nodeMemoryRows").addEventListener("click", event => {
      const duty = event.target.closest("[data-memory-duty]");
      if (duty) { void focusNodeDuty(duty.dataset.memoryDuty); return; }
      if (event.target.closest("[data-memory-harness]")) void switchNodeSettingsTab("harness");
    });

    return { loadNodeMemory };
  }

  window.PortalResidentMenuMemory = Object.freeze({ createMemory, dependencies: DEPENDENCIES });
})();
