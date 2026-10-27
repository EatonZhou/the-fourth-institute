(() => {
  "use strict";

  if (window.PortalHatgFunctionBlockMenu) return;

  // A Function Block's Menu: its objects and their status, who hears about
  // Effect failures, a Family's work guides and progress settings, and the
  // unwrap and delete actions. Double-clicking a block's pigeon opens it.
  const DEPENDENCIES = Object.freeze([
    "acknowledgeFunctionBlockFailures", "applyProgramMutation", "currentTemplateController",
    "definitionRevisions", "displayNodeById", "displayProgramComponentById",
    "emitManualProgramEvent", "esc", "executeHatgInstruction", "expandedFunctionBlockIds",
    "familyExpanded", "familyForFacade", "familyRoles", "formatDate", "functionBlockElements",
    "functionBlockInstanceById", "functionBlockInstanceFailures",
    "functionBlockInstanceTriggersHtml", "functionBlockInstanceUnacknowledgedFailures",
    "graphObjectTitle", "instanceInnerObjects", "nodeIntakeState", "programEffectRuntime",
    "programEventSourceRuntime", "programFeature", "removeFamilyInstance",
    "renderProgramInspector", "residentMenuHandlers", "selectNode", "setFunctionBlockExpanded",
    "state", "toast", "toggleFamilyView",
  ]);

  // Effect failure recipients are saved on their own (decision_4adcc1ff2f2c “Effect 失败通知”):
  // the inspector, a folded module's objects and its overview open the same editor
  // for one Effect or several.
  function effectRecipients(component) {
    return window.PortalLineageElementFields.recipientList(component?.config?.failure_recipients || []);
  }

  // "unset" (an older Effect without a list, not notified) is never shown as "off".
  function effectRecipientsState(component) {
    return window.PortalLineageElementFields.recipientState(component?.config);
  }

  function createFunctionBlockMenu(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalHatgFunctionBlockMenu is missing: ${missing.join(", ")}`);
    const {
      acknowledgeFunctionBlockFailures, applyProgramMutation, currentTemplateController,
      definitionRevisions, displayNodeById, displayProgramComponentById, emitManualProgramEvent,
      esc, executeHatgInstruction, expandedFunctionBlockIds, familyExpanded, familyForFacade,
      familyRoles, formatDate, functionBlockElements, functionBlockInstanceById,
      functionBlockInstanceFailures, functionBlockInstanceTriggersHtml,
      functionBlockInstanceUnacknowledgedFailures, graphObjectTitle, instanceInnerObjects,
      nodeIntakeState, programEffectRuntime, programEventSourceRuntime, programFeature,
      removeFamilyInstance, renderProgramInspector, residentMenuHandlers, selectNode,
      setFunctionBlockExpanded, state, toast, toggleFamilyView,
    } = dependencies;

    // A Function Block's Menu: double-clicking its pigeon opens it. The
    // block's own tab shows what the inspector beside the canvas used to show; a
    // Family adds a tab for its Router and one for its steward, each the
    // Resident's usual Menu, and opens their Chats from the block's tab.
    let functionBlockMenuRefresh = null;

    // Redraws the open Function Block Menu after an inspector change, if one is open.
    function refreshFunctionBlockMenu() {
      functionBlockMenuRefresh?.();
    }

    function openFunctionBlockMenu(instanceId, active = "block") {
      const instance = functionBlockInstanceById(instanceId);
      if (!instance) return null;
      const roles = familyRoles(instance);
      const resident = (key, label, residentId) => {
        const node = residentId ? displayNodeById(residentId) : null;
        return node ? { key, label, residentId, node: { ...node, intake_state: nodeIntakeState(node) } } : null;
      };
      const tabs = [
        { key: "block", label: instance.title || window.PortalI18n.t("Function Block") },
        { key: "objects", label: window.PortalI18n.t("内部对象") },
        { key: "issues", label: window.PortalI18n.t("问题") },
        resident("router", "Router", roles?.router),
        resident("steward", "Steward", roles?.steward),
      ].filter(Boolean);
      const tab = tabs.find(item => item.key === active) || tabs[0];
      return window.PortalNav.openResidentMenu({
        ...residentMenuHandlers(),
        lineage: state.lineage,
        residentId: tab.residentId || "",
        node: tab.node || null,
        group: {
          tabs,
          active: tab.key,
          render: (key, root, { close, setLabel }) => renderFunctionBlockMenuPanel(instance.id, root, close, setLabel, key),
        },
      });
    }

    function functionBlockInsideResidentsHtml(instance) {
      const roles = familyRoles(instance);
      const rows = roles ? [["Router", roles.router], ["Steward", roles.steward]]
        .filter(([, residentId]) => residentId)
        .map(([label, residentId]) => {
          const title = displayNodeById(residentId)?.title || residentId;
          return window.PortalI18n.html`<div><dt>${esc(label)}</dt><dd><span title="${esc(residentId)}">${esc(title)}</span><button class="portalButton" type="button" data-function-block-chat="${esc(residentId)}">打开对话</button></dd></div>`;
        }).join("") : "";
      return rows ? window.PortalI18n.html`<section class="functionBlockInstanceBindings functionBlockInsideResidents"><span>模块内的 Resident</span><dl>${rows}</dl></section>` : "";
    }

    function functionBlockSourceTitle(block) {
      return block.template_id
        ? (currentTemplateController()?.find?.(block.template_id)?.title || block.template_id)
        : window.PortalI18n.t("自定义选区");
    }

    function openEffectRecipientsEditor(effectIds) {
      const effects = effectIds.map(id => displayProgramComponentById(id)).filter(item => item?.kind === "effect");
      if (!effects.length || state.programMutationInFlight) return null;
      const revision = state.lineage.revision;
      return window.PortalLineageElementFields.openRecipientEditor({
        effects: effects.map(item => ({ id: item.id, title: item.title || item.id, recipients: effectRecipients(item), state: effectRecipientsState(item) })),
        me: state.me,
        onSave: changes => saveEffectRecipients(changes, revision),
      });
    }

    async function saveEffectRecipients(changes, revision) {
      const fields = window.PortalLineageElementFields;
      const single = changes.length === 1 ? changes[0] : null;
      const message = single
        ? (single.recipients.length
          ? window.PortalI18n.text`已保存：${single.title} 失败时提醒 ${fields.recipientNames(single.recipients)}。`
          : window.PortalI18n.text`已关闭 ${single.title} 的失败通知。`)
        : window.PortalI18n.text`已更新 ${changes.length} 个 Effect 的失败通知。`;
      // The list the editor showed belongs to this revision; a newer definition is a conflict.
      const response = await applyProgramMutation(() => executeHatgInstruction("ApplyLineageChanges", {
        operations: changes.map(item => ({ op: "update", kind: "component", id: item.id, changes: { "config.failure_recipients": item.recipients } })),
      }, { expectedVersion: revision }), message, { failure: "失败通知没有保存" });
      if (!response) throw new Error(window.PortalI18n.t("失败通知没有保存，请刷新后重试。"));
      // An inspector kept open with an unsaved script draft is not redrawn: show its new
      // recipients and let the draft save on top of this change, which it does not touch.
      document.querySelectorAll("[data-effect-recipients]").forEach(section => {
        const change = changes.find(item => item.id === section.dataset.effectRecipients);
        if (!change) return;
        fields.setRecipientChips(section.querySelector("[data-recipient-chips]"), change.recipients);
        const form = section.closest("form");
        if (form && definitionRevisions.get(form) === revision) definitionRevisions.set(form, state.lineage.revision);
      });
      return response;
    }

    function effectRecipientsChipsHtml(recipients, recipientsState, limit) {
      return `<span class="effectRecipientChips" data-recipient-chips="${esc(JSON.stringify(recipients))}" data-recipients-state="${esc(recipientsState)}" data-recipient-limit="${limit}">${window.PortalLineageElementFields.recipientChipsHtml(recipients, { limit, state: recipientsState })}</span>`;
    }

    function effectRecipientsButtonHtml(effect, { limit = 3 } = {}) {
      return window.PortalI18n.html`<button type="button" class="functionBlockRecipients" data-block-recipients="${esc(effect.id)}" aria-label="${esc(window.PortalI18n.text`修改 ${effect.title || effect.id} 的失败通知`)}" title="修改失败通知收件人"${state.programMutationInFlight ? " disabled" : ""}>${effectRecipientsChipsHtml(effectRecipients(effect), effectRecipientsState(effect), limit)}<span class="functionBlockRecipientsEdit" aria-hidden="true">修改</span></button>`;
    }

    // The overview names who hears about failures, grouped by identical lists; Effects
    // without a list form their own group, apart from those explicitly closed.
    function functionBlockRecipientsSummaryHtml(effects, readonly) {
      if (!effects.length) return "";
      const groups = new Map();
      effects.forEach(effect => {
        const recipients = effectRecipients(effect);
        const recipientsState = effectRecipientsState(effect);
        const key = JSON.stringify([recipientsState, recipients]);
        const group = groups.get(key) || { recipients, recipientsState, items: [] };
        group.items.push(effect);
        groups.set(key, group);
      });
      const rows = [...groups.values()].map(({ recipients, recipientsState, items }) => window.PortalI18n.html`<li data-recipients-group="${esc(recipientsState)}">${effectRecipientsChipsHtml(recipients, recipientsState, 4)}<small title="${esc(items.map(item => item.title || item.id).join("、"))}">${esc(window.PortalI18n.text`${items.length} 个 Effect`)}</small></li>`).join("");
      return window.PortalI18n.html`<section class="functionBlockInstanceBindings functionBlockRecipientsSummary"><span>失败通知</span><ul>${rows}</ul><button class="portalButton" type="button" data-block-recipients-all${readonly ? " disabled" : ""}>${esc(effects.length === 1 ? window.PortalI18n.t("修改收件人…") : window.PortalI18n.text`修改 ${effects.length} 个 Effect 的收件人…`)}</button></section>`;
    }

    function functionBlockObjectStatusHtml(item) {
      if (item.kind === "event_source") {
        const paused = programEventSourceRuntime(item.id).paused;
        return window.PortalI18n.html`<span class="portalStatus" data-tone="${paused ? "warning" : "info"}">${esc(window.PortalI18n.t(paused ? "已暂停" : "监听中"))}</span>`;
      }
      if (item.kind !== "effect") return "";
      const status = programEffectRuntime(item.id).status || "";
      const [tone, label] = status === "executed" ? ["success", "执行成功"] : status === "failed" ? ["danger", "执行失败"] : status === "cancelled" ? ["neutral", "已取消"] : ["neutral", "尚未执行"];
      return window.PortalI18n.html`<span class="portalStatus" data-tone="${tone}">${esc(window.PortalI18n.t(label))}</span>`;
    }

    function functionBlockObjectRowHtml(item, selected, readonly) {
      const title = item.title || item.id;
      const kind = item.kind === "event_source" ? "Event" : item.kind === "effect" ? "Effect" : item.kind;
      const paused = item.kind === "event_source" && programEventSourceRuntime(item.id).paused;
      const action = item.kind === "effect" ? effectRecipientsButtonHtml(item)
        : item.kind === "event_source" ? window.PortalI18n.html`<button type="button" class="portalButton is-compact" data-block-event-switch="${esc(item.id)}" title="${esc(window.PortalI18n.t(paused ? "恢复监听这个 Event" : "暂停这个 Event"))}"${readonly ? " disabled" : ""}>${esc(window.PortalI18n.t(paused ? "恢复" : "暂停"))}</button>`
        : "";
      return window.PortalI18n.html`<li class="functionBlockObjectRow" data-block-object="${esc(item.id)}" data-object-kind="${esc(item.kind)}"${selected ? ' data-selected="true"' : ""}>
        <input type="checkbox" data-block-object-select="${esc(item.id)}" aria-label="${esc(window.PortalI18n.text`选择 ${title}`)}"${selected ? " checked" : ""}${readonly ? " disabled" : ""}>
        <span class="functionBlockObjectMain"><strong>${esc(title)}</strong><small>${esc(kind)} · ${esc(item.id)}</small></span>
        <span class="functionBlockObjectState">${functionBlockObjectStatusHtml(item)}</span>
        <span class="functionBlockObjectAction">${action}</span>
        <button type="button" class="portalButton is-compact" data-block-object-edit="${esc(item.id)}">设置</button>
      </li>`;
    }

    function familyTaskExecutionHtml(component, readonly, shared, isSource = false) {
      if (!component) return "";
      const event = component.kind === "event_source";
      const effect = component.kind === "effect";
      const paused = event && programEventSourceRuntime(component.id).paused;
      return window.PortalI18n.html`<div class="familyTaskExecutionRow">
      <div class="familyTaskExecutionName"><small>${event ? "触发条件" : isSource ? "前置流程" : "执行动作"}</small><strong>${esc(component.title || component.id)}</strong>${functionBlockObjectStatusHtml(component)}</div>
      <div class="familyTaskExecutionActions">
        <button class="portalButton is-compact" type="button" data-block-object-edit="${esc(component.id)}">${event ? "触发设置" : "执行设置"}</button>
        ${event ? window.PortalI18n.html`<button class="portalButton is-compact" type="button" data-block-event-switch="${esc(component.id)}"${readonly ? " disabled" : ""}>${paused ? "恢复监听" : "暂停监听"}</button>` : ""}
        ${event && component.config?.event_type === "manual.triggered" ? window.PortalI18n.html`<button class="portalButton is-compact" type="button" data-function-block-trigger="${esc(component.id)}"${readonly || paused ? " disabled" : ""}>立即触发</button>` : ""}
      </div>
      ${effect ? window.PortalI18n.html`<div class="familyTaskFailureRecipients"><span>失败时通知</span>${effectRecipientsButtonHtml(component)}</div>` : ""}
      ${shared > 1 ? window.PortalI18n.html`<small class="familyTaskShared">${esc(window.PortalI18n.text`此设置由 ${shared} 个场景共用。`)}</small>` : ""}
    </div>`;
    }

    function familyWorkGuidesHtml(block, readonly, drafts = new Map(), expanded = new Set()) {
      if (!familyRoles(block)) return "";
      const wires = new Map((state.lineage?.program?.wires || []).map(item => [item.id, item]));
      const components = new Map((state.lineage?.program?.components || []).map(item => [item.id, item]));
      const groups = { router: [], steward: [] };
      const entries = [];
      for (const element of block.elements || []) {
        const route = wires.get(element.id);
        for (const [profile, config] of Object.entries(route?.config?.invoke_profiles || {})) {
          for (const [trigger, saved] of Object.entries(config.arguments?.family_tasks?.value || {})) {
            if (groups[saved.role]) entries.push({ route, profile, trigger, saved });
          }
        }
      }
      const uses = new Map();
      for (const { route } of entries) {
        for (const id of new Set([route.source, route.target])) uses.set(id, (uses.get(id) || 0) + 1);
      }
      for (const { route, profile, trigger, saved } of entries) {
            const key = `${route.id}:${trigger}`;
            const draft = drafts.get(key);
            const task = { ...saved, ...draft };
            const source = components.get(route.source);
            const target = components.get(route.target);
            groups[task.role].push(window.PortalI18n.html`<details class="familyTaskCard" data-family-scene-key="${esc(key)}"${draft || expanded.has(key) ? " open" : ""}><summary><span>${esc(task.title)}</span>${source?.title && source.title !== task.title ? `<small>${esc(source.title)}</small>` : ""}</summary>
            <div class="familyTaskExecution">${familyTaskExecutionHtml(source, readonly, uses.get(route.source), true)}${target?.id !== source?.id ? familyTaskExecutionHtml(target, readonly, uses.get(route.target)) : ""}</div>
            ${trigger === "progress" ? familyProgressSettingsHtml(block, readonly) : ""}
            <form data-family-task-form data-wire-id="${esc(route.id)}" data-profile="${esc(profile)}" data-trigger="${esc(trigger)}" data-revision="${esc(state.lineage.revision)}">
              <label>工作指南<textarea name="guide" rows="7" required${readonly ? " disabled" : ""}>${esc(task.guide)}</textarea></label>
              <label>每轮任务模板<textarea name="prompt" rows="4" required${readonly ? " disabled" : ""}>${esc(task.prompt)}</textarea></label>
              <small>可用变量：<code>\${facts}</code> 本轮事实、<code>\${guide_path}</code> 阅读路径、<code>\${guide_version}</code> 版本。资料按需读取。</small>
              <button class="portalButton is-primary" type="submit"${readonly ? " disabled" : ""}>保存此场景</button>
              <span data-family-task-feedback role="status"></span>
            </form></details>`);
      }
      return window.PortalI18n.html`<section class="familyWorkGuides"><header><strong>工作流程</strong><p>按场景管理触发、执行、通知和工作指南。首轮发送完整指南，后续只发送本轮任务。</p></header>${Object.entries(groups).map(([role, cards]) => window.PortalI18n.html`<section class="familyWorkRole"><h3>${role === "router" ? "Router · 路由" : "Steward · 组织维护"}</h3>${cards.join("") || "<p>暂无工作场景。</p>"}</section>`).join("")}${functionBlockInstanceTriggersHtml(block, readonly, new Set(uses.keys()))}<footer class="familyWorkflowFooter"><span>全部组件、连接关系与批量操作</span><button class="portalButton" type="button" data-block-open-objects>内部对象</button></footer></section>`;
    }

    function bindFamilyWorkGuideForms(root, draw, drafts = new Map(), expanded = new Set()) {
    root.querySelectorAll("[data-family-scene-key]").forEach(card => card.addEventListener("toggle", () => {
      if (!card.isConnected) return;
      if (card.open) expanded.add(card.dataset.familySceneKey); else expanded.delete(card.dataset.familySceneKey);
    }));
    root.querySelectorAll("[data-family-task-form]").forEach(form => {
        form.addEventListener("input", () => drafts.set(`${form.dataset.wireId}:${form.dataset.trigger}`, {
          guide: form.elements.guide.value, prompt: form.elements.prompt.value,
        }));
        form.addEventListener("submit", async event => {
      event.preventDefault();
      if (!form.reportValidity()) return;
      const feedback = form.querySelector("[data-family-task-feedback]");
      const template = form.elements.prompt.value;
      const unknown = [...template.matchAll(/\$\{([^}]+)\}/g)].map(match => match[1]).filter(key => !["facts", "guide_path", "guide_version"].includes(key));
      if (unknown.length) { feedback.textContent = `未知变量：${unknown.join("、")}`; return; }
      const route = (state.lineage?.program?.wires || []).find(item => item.id === form.dataset.wireId);
      if (!route) return;
      const profiles = structuredClone(route.config.invoke_profiles);
      const task = profiles[form.dataset.profile].arguments.family_tasks.value[form.dataset.trigger];
      task.guide = form.elements.guide.value;
      task.prompt = template;
      try {
        const response = await applyProgramMutation(() => executeHatgInstruction("ApplyLineageChanges", {
          operations: [{ op: "update", kind: "wire", id: route.id, changes: { "config.invoke_profiles": profiles } }],
        }, { expectedVersion: Number(form.dataset.revision) }), "工作指南已保存，下次触发生效。");
        if (response) { drafts.delete(`${route.id}:${form.dataset.trigger}`); draw(); }
      } catch (error) { feedback.textContent = error.message || String(error); }
    });
      });
    }

    function familyProgressSettingsHtml(block, readonly) {
      const routeId = (block.elements || []).find(item => item.key === "request_progress" && item.kind === "wire")?.id;
      const route = (state.lineage?.program?.wires || []).find(item => item.id === routeId);
      if (!block.family || !route) return "";
      const interval = route.config?.invoke_profiles?.effect?.arguments?.every_turns?.value ?? 20;
      return window.PortalI18n.html`<form class="programInspectorForm" data-family-progress-settings data-wire-id="${esc(route.id)}" data-revision="${esc(state.lineage.revision)}">
      <label><span>进展简报：每几轮请 Steward 汇总一次</span><input name="every_turns" type="number" min="1" step="1" required value="${esc(interval)}"${readonly ? " disabled" : ""}></label>
      <small>按门面收到的新工作输入计数，重试和 info 不重复计数。Steward 撰写最多 500 字的简报并更新门面摘要，不向成员群发；有相关信息时由 Steward 单独通知。</small>
      <button class="portalButton" type="submit"${readonly ? " disabled" : ""}>保存简报设置</button>
    </form>`;
    }

    function functionBlockElementListHtml(block) {
      const rows = functionBlockElements(block).map(element => window.PortalI18n.html`<div><dt>${esc(element.kind)}</dt><dd title="${esc(element.id)}">${esc(graphObjectTitle(element.id))}<small>${esc(element.id)}</small></dd></div>`).join("");
      return window.PortalI18n.html`<section class="functionBlockInstanceBindings functionBlockElementList"><span>元素列表</span><dl>${rows}</dl></section>`;
    }

    function functionBlockOverviewWorkflowHtml(block, readonly, drafts, expanded) {
      if (familyRoles(block)) return familyWorkGuidesHtml(block, readonly, drafts, expanded);
      const effects = instanceInnerObjects(block, "components").filter(item => item.kind === "effect");
      return `${functionBlockRecipientsSummaryHtml(effects, readonly)}${functionBlockInstanceTriggersHtml(block, readonly)}${functionBlockElementListHtml(block)}`;
    }

    // Only ordinary components/wires may move into a Family. The server owns
    // the final validation; do not offer another block's or generated objects.
    function familyWorkflowChoices(block, mode) {
      const lineage = state.lineage;
      const blocks = lineage.function_blocks || [];
      const owners = new Map();
      blocks.forEach(owner => (owner.elements || []).forEach(element => {
        const list = owners.get(element.id) || [];
        list.push({ owner, element }); owners.set(element.id, list);
      }));
      const ancestors = new Set();
      let parent = owners.get(block.id);
      while (parent?.length === 1 && !ancestors.has(parent[0].owner.id)) {
        ancestors.add(parent[0].owner.id); parent = owners.get(parent[0].owner.id);
      }
      const inside = new Set(), pending = [block];
      while (pending.length) (pending.pop().elements || []).forEach(element => {
        if (inside.has(element.id)) return;
        inside.add(element.id);
        const nested = blocks.find(item => item.id === element.id);
        if (nested) pending.push(nested);
      });
      const rows = [...(lineage.program?.components || []).map(item => ({ ...item, elementKind: "component" })),
        ...(lineage.program?.wires || []).map(item => ({ ...item, elementKind: "wire" }))].filter(item => {
        const ownership = owners.get(item.id) || [];
        if (!ownership.length) return mode === "add";
        if (ownership.length !== 1) return false;
        const { owner, element } = ownership[0];
        if (!String(element.key || "").startsWith("workflow:")) return false;
        return mode === "remove" ? owner.id === block.id
          : owner.id !== block.id && !!owner.family && ancestors.has(owner.id);
      });
      return { rows, inside, owners };
    }

    function familyWorkflowHtml(block, draft, readonly, error) {
      if (!familyRoles(block)) return "";
      const { rows, inside, owners } = familyWorkflowChoices(block, draft.mode);
      rows.filter(item => item.elementKind === "component" && draft.ids.has(item.id)).forEach(item => inside.add(item.id));
      const stale = state.lineage.lineage_id !== draft.lineageId || state.lineage.revision !== draft.revision;
      const disabled = readonly || stale;
      return window.PortalI18n.html`<details class="familyWorkflowOwnership"${draft.open ? " open" : ""}>
        <summary>管理工作流</summary>
        <form data-family-workflow-form>
          <p>收纳普通组件和连线，或将它们移出 Family；不会删除对象。收纳组件时，内部连线一并收纳；移出组件时，关联的内部连线一并移出。</p>
          <label><span>工作流操作</span><select data-family-workflow-mode${disabled ? " disabled" : ""}>
            <option value="add"${draft.mode === "add" ? " selected" : ""}>收纳</option>
            <option value="remove"${draft.mode === "remove" ? " selected" : ""}>移出</option>
          </select></label>
          <div class="familyWorkflowChoices">${rows.map(item => {
            const available = draft.mode === "remove" || item.elementKind !== "wire" || (inside.has(item.source) && inside.has(item.target));
            const owner = owners.get(item.id)?.[0]?.owner;
            return window.PortalI18n.html`<label><input type="checkbox" data-family-workflow-id="${esc(item.id)}"${draft.ids.has(item.id) && available ? " checked" : ""}${disabled || !available ? " disabled" : ""}>
              <span><strong>${esc(item.title || item.id)}</strong><small>${esc(item.kind)} · ${esc(item.id)}</small>
              ${owner ? `<small>${esc(owner.title || owner.id)}</small>` : ""}
              ${!available ? window.PortalI18n.html`<small>连线两端须在此 Family 内；可先选择对应组件。</small>` : ""}</span></label>`;
          }).join("") || window.PortalI18n.html`<p>没有可操作的普通工作流对象。</p>`}</div>
          ${stale ? window.PortalI18n.html`<p role="alert">Lineage 已变化，请重新选择工作流。</p>` : error ? `<p role="alert">${esc(error)}</p>` : ""}
          <button class="portalButton" type="submit"${disabled || !draft.ids.size ? " disabled" : ""}>保存归属</button>
          <button class="portalButton" type="button" data-family-workflow-reset${readonly ? " disabled" : ""}>重新选择</button>
        </form>
      </details>`;
    }

    function functionBlockObjectsHtml(components, selected, readonly, block, workflowHtml = "") {
      const groups = [
        ["effect", "Effect", components.filter(item => item.kind === "effect")],
        ["event_source", "Event", components.filter(item => item.kind === "event_source")],
        ["other", window.PortalI18n.t("其他组件"), components.filter(item => !["effect", "event_source"].includes(item.kind))],
      ].filter(([, , items]) => items.length);
      const effects = components.filter(item => item.kind === "effect" && selected.has(item.id));
      const events = components.filter(item => item.kind === "event_source" && selected.has(item.id));
      const count = components.filter(item => selected.has(item.id)).length;
      if (!components.length) return `${workflowHtml}${window.PortalI18n.html`<p class="functionBlockObjectsEmpty">这个模块里没有组件。</p>`}`;
      return window.PortalI18n.html`${workflowHtml}${familyProgressSettingsHtml(block, readonly)}<p class="functionBlockObjectsIntro">折叠时也能直接管理模块内的组件：点 Effect 的收件人修改失败通知，勾选多个对象可以一起处理。</p>
      <div class="functionBlockObjectToolbar" role="toolbar" aria-label="批量操作">
        <label class="functionBlockSelectAll"><input type="checkbox" data-block-select-all${readonly ? " disabled" : ""}><span>${esc(count ? window.PortalI18n.text`已选 ${count} 项` : window.PortalI18n.t("全选"))}</span></label>
        <span class="functionBlockBulkButtons">
          <button class="portalButton is-compact" type="button" data-block-bulk-recipients${readonly || !effects.length ? " disabled" : ""}>${esc(window.PortalI18n.text`设置失败通知（${effects.length}）`)}</button>
          <button class="portalButton is-compact" type="button" data-block-events-pause${readonly || !events.length ? " disabled" : ""}>${esc(window.PortalI18n.text`暂停事件（${events.length}）`)}</button>
          <button class="portalButton is-compact" type="button" data-block-events-resume${readonly || !events.length ? " disabled" : ""}>${esc(window.PortalI18n.text`恢复事件（${events.length}）`)}</button>
        </span>
      </div>
      ${groups.map(([key, label, items]) => window.PortalI18n.html`<section class="functionBlockObjectGroup" data-object-group="${key}"><h4><span>${esc(label)}<small>${items.length}</small></span>${key === "effect" ? window.PortalI18n.html`<span class="functionBlockObjectColumn">失败通知</span>` : ""}</h4><ul>${items.map(item => functionBlockObjectRowHtml(item, selected.has(item.id), readonly)).join("")}</ul></section>`).join("")}
      ${familyRoles(block) ? window.PortalI18n.html`<details class="functionBlockObjectInventory"><summary>查看完整元素列表</summary>${functionBlockElementListHtml(block)}</details>` : ""}`;
    }

    function renderFunctionBlockMenuPanel(blockId, root, close, setLabel, pane = "block") {
      let titleDraft;
      const menuLineageId = state.lineage?.lineage_id;
      let workflowDraft = null, workflowPending = false, workflowError = "", disposed = false;
      const taskDrafts = new Map();
      const expandedTasks = new Set();
      let editingId = "";
      const selected = new Set();
      const editor = document.createElement("section");
      editor.className = "functionBlockObjectEditor";
      const draw = () => {
        if (disposed) return;
        const previousWorkflow = root.querySelector(".familyWorkflowOwnership");
        if (workflowDraft && previousWorkflow) workflowDraft.open = previousWorkflow.open;
        if (state.lineage?.lineage_id !== menuLineageId) { root.textContent = window.PortalI18n.t("Lineage 已变化，请重新选择工作流。"); return; }
        const block = functionBlockInstanceById(blockId);
        if (!block) { root.textContent = window.PortalI18n.t("这个 Function Block 已不在 Lineage 中。"); return; }
        setLabel(pane === "objects" ? window.PortalI18n.t("内部对象") : pane === "issues" ? window.PortalI18n.t("问题") : block.title || window.PortalI18n.t("Function Block"));
        const roles = familyRoles(block);
        const readonly = state.programMutationInFlight || workflowPending;
        if (roles && pane === "objects" && !workflowDraft) workflowDraft = {
          lineageId: menuLineageId, revision: state.lineage.revision, mode: "add", ids: new Set(), open: false,
        };
        const failures = functionBlockInstanceFailures(block);
        const components = instanceInnerObjects(block, "components");
        const effects = components.filter(item => item.kind === "effect" && selected.has(item.id));
        const events = components.filter(item => item.kind === "event_source" && selected.has(item.id));
        const pending = new Set(functionBlockInstanceUnacknowledgedFailures(block).map(issue => issue.id));
        const blockEffects = components.filter(item => item.kind === "effect");
        const issueRows = failures.map(issue => window.PortalI18n.html`<article class="functionBlockIssue" data-block-issue="${esc(issue.id)}"><header><strong>${esc(issue.title || issue.id)}</strong><span>${esc(window.PortalI18n.t(pending.has(issue.id) ? "未查看" : "已查看"))}</span></header><small>${esc(issue.kind)} · ${esc(issue.id)}${issue.at ? ` · ${esc(formatDate(issue.at))}` : ""}</small><p>${esc(issue.error)}</p>${issue.kind === "effect" ? window.PortalI18n.html`<div class="functionBlockIssueRecipients"><span>失败通知</span>${effectRecipientsButtonHtml(issue, { limit: 4 })}</div>` : ""}<div>${issue.kind === "resident" ? window.PortalI18n.html`<button class="portalButton" type="button" data-function-block-chat="${esc(issue.id)}">查看对话</button>` : window.PortalI18n.html`<button class="portalButton" type="button" data-block-object-edit="${esc(issue.id)}">查看设置与运行记录</button>`}</div></article>`).join("");
        root.innerHTML = window.PortalI18n.html`<div class="portalHatgOverlayComponent functionBlockMenuPanel" data-function-block-menu-panel="${esc(block.id)}"><header class="functionBlockMenuIdentity"><strong>${esc(block.title || window.PortalI18n.t("Function Block"))}</strong><code>${esc(block.id)}</code></header><div class="programInspectorForm functionBlockInstanceInspector">
        ${pane === "block" ? window.PortalI18n.html`<label>标题<input data-function-block-title value="${esc(titleDraft ?? block.title)}"${readonly ? " disabled" : ""}></label>
        <button class="portalButton is-primary" type="button" data-function-block-rename${readonly ? " disabled" : ""}>保存标题</button>
        ${failures.length ? window.PortalI18n.html`<p data-function-block-failure>内部出错：${esc(failures.map(component => component.title || component.id).join("、"))}</p>` : ""}
        <section class="functionBlockInstanceBindings"><span>来源模板</span><p>${esc(functionBlockSourceTitle(block))}</p>${block.template_id ? '<button class="portalButton" type="button" data-function-block-source>查看来源模板</button>' : ""}</section>
        ${functionBlockInsideResidentsHtml(block)}
        ${functionBlockOverviewWorkflowHtml(block, readonly, taskDrafts, expandedTasks)}
        <div class="programInspectorActions functionBlockInstanceActions">
          <button class="portalButton" type="button" data-function-block-save-template${readonly ? " disabled" : ""}>存为模板</button>
          ${roles ? window.PortalI18n.html`<button class="portalButton" type="button" data-family-fold>${familyExpanded(roles) ? window.PortalI18n.t("折叠为门面") : window.PortalI18n.t("展开成员")}</button><button class="portalButton" type="button" data-family-unfamilize${readonly ? " disabled" : ""}>Unfamilize</button>`
          : window.PortalI18n.html`<button class="portalButton" type="button" data-function-block-expand>${expandedFunctionBlockIds().has(block.id) ? window.PortalI18n.t("折叠") : window.PortalI18n.t("展开")}</button><button class="portalButton" type="button" data-function-block-unfunctionalize${readonly ? " disabled" : ""}>Unfunctionalize</button><button class="portalButton is-danger" type="button" data-function-block-delete${readonly ? " disabled" : ""}>删除</button>`}
        </div>` : pane === "objects" ? functionBlockObjectsHtml(components, selected, readonly, block,
          roles ? familyWorkflowHtml(block, workflowDraft, readonly, workflowError) : "") : window.PortalI18n.html`<p>打开本页会自动消除当前错误提醒，保留错误与运行记录；新的失败会再次提示。成功运行或修正配置后，对应问题会自动移出当前清单。</p>${functionBlockInsideResidentsHtml(block)}${issueRows || '<p data-block-no-issues>当前没有问题。</p>'}`}
        <div data-block-object-editor></div>
      </div></div>`;
        const action = (selector, callback, closing = true) => root.querySelector(selector)?.addEventListener("click", () => {
          if (closing) close();
          Promise.resolve().then(callback).catch(error => toast(error.message || String(error), "error"));
        });
        const workflowForm = root.querySelector("[data-family-workflow-form]");
        if (workflowForm) {
          const draft = workflowDraft;
          workflowForm.querySelector("[data-family-workflow-mode]").addEventListener("change", event => {
            draft.mode = event.target.value; draft.ids.clear(); workflowError = ""; draw();
          });
          workflowForm.querySelectorAll("[data-family-workflow-id]").forEach(input => input.addEventListener("change", () => {
            if (input.checked) draft.ids.add(input.dataset.familyWorkflowId); else draft.ids.delete(input.dataset.familyWorkflowId);
            const { rows, inside } = familyWorkflowChoices(block, draft.mode);
            rows.filter(item => item.elementKind === "component" && draft.ids.has(item.id)).forEach(item => inside.add(item.id));
            if (draft.mode === "add") rows.filter(item => item.elementKind === "wire" && (!inside.has(item.source) || !inside.has(item.target)))
              .forEach(item => draft.ids.delete(item.id));
            draw();
          }));
          workflowForm.querySelector("[data-family-workflow-reset]").addEventListener("click", () => {
            workflowDraft = null; workflowError = ""; draw();
          });
          workflowForm.addEventListener("submit", async event => {
            event.preventDefault();
            if (workflowPending || state.programMutationInFlight || !draft.ids.size) return;
            const ids = [...draft.ids], mode = draft.mode;
            workflowPending = true; workflowError = ""; draw();
            try {
              const result = await applyProgramMutation(() => {
                if (state.lineage?.lineage_id !== draft.lineageId || state.lineage.revision !== draft.revision)
                  throw new Error(window.PortalI18n.t("Lineage 已变化，请重新选择工作流。"));
                return executeHatgInstruction("UpdateFamily", {
                  family_id: blockId, add_element_ids: mode === "add" ? ids : [], remove_element_ids: mode === "remove" ? ids : [],
                }, { expectedVersion: draft.revision });
              }, "工作流归属已保存。");
              if (result) workflowDraft = null;
            } catch (error) { workflowError = error.message || String(error); }
            finally { workflowPending = false; draw(); }
          });
        }
        root.querySelector("[data-function-block-title]")?.addEventListener("input", event => { titleDraft = event.target.value; });
        action("[data-function-block-rename]", async () => {
          const title = root.querySelector("[data-function-block-title]").value.trim();
          const result = await applyProgramMutation(() => executeHatgInstruction(
            roles ? "UpdateFamily" : "UpdateFunctionBlock",
            roles ? { family_id: block.id, title } : { function_block_id: block.id, title },
          ), "Function Block 标题已保存。");
          if (result) { titleDraft = undefined; draw(); }
        }, false);
        action("[data-function-block-source]", () => currentTemplateController().openEditor(block.template_id));
        action("[data-block-open-objects]", () => openFunctionBlockMenu(block.id, "objects"));
        action("[data-function-block-save-template]", async () => {
          await currentTemplateController().saveSelection([block.id]);
          toast(window.PortalI18n.t("已存为模板。"), "success");
        });
        action("[data-function-block-expand]", () => setFunctionBlockExpanded(block.id, !expandedFunctionBlockIds().has(block.id)));
        action("[data-function-block-unfunctionalize]", () => unfunctionalize(block.id));
        action("[data-function-block-delete]", () => deleteFunctionBlock(block.id));
        action("[data-family-unfamilize]", () => removeFamilyInstance(block.id));
        action("[data-family-fold]", () => { const family = familyForFacade(roles.facade); if (family) toggleFamilyView(family); });
        root.querySelectorAll("[data-function-block-trigger]").forEach(button => button.addEventListener("click", () => {
          emitManualProgramEvent(button.dataset.functionBlockTrigger, button).catch(() => {});
        }));
        root.querySelectorAll("[data-function-block-chat]").forEach(button => button.addEventListener("click", () => {
          close(); selectNode(button.dataset.functionBlockChat, { force: true, openChat: true }).catch(() => {});
        }));
        root.querySelectorAll("[data-block-object-edit]").forEach(button => button.addEventListener("click", () => {
          editingId = button.dataset.blockObjectEdit;
          editor.innerHTML = "";
          draw();
          editor.scrollIntoView({ block: "start", behavior: "smooth" });
        }));
        bindFamilyWorkGuideForms(root, draw, taskDrafts, expandedTasks);
        root.querySelector("[data-family-progress-settings]")?.addEventListener("submit", async event => {
          event.preventDefault();
          const form = event.currentTarget;
          if (!form.reportValidity()) return;
          const interval = Number(form.elements.every_turns.value);
          if (!Number.isSafeInteger(interval) || interval < 1) return;
          const route = (state.lineage?.program?.wires || []).find(item => item.id === form.dataset.wireId);
          if (!route) return;
          const profiles = structuredClone(route.config.invoke_profiles);
          profiles.effect.arguments.every_turns = { value: interval };
          const response = await applyProgramMutation(() => executeHatgInstruction("ApplyLineageChanges", {
            operations: [{ op: "update", kind: "wire", id: form.dataset.wireId,
              changes: { "config.invoke_profiles": profiles } }],
          }, { expectedVersion: Number(form.dataset.revision) }), "简报设置已保存。");
          if (response) draw();
        });
        root.querySelectorAll("[data-block-object-select]").forEach(input => input.addEventListener("change", () => {
          if (input.checked) selected.add(input.dataset.blockObjectSelect); else selected.delete(input.dataset.blockObjectSelect);
          draw();
        }));
        const selectAll = root.querySelector("[data-block-select-all]");
        if (selectAll) {
          const count = components.filter(item => selected.has(item.id)).length;
          selectAll.checked = count > 0 && count === components.length;
          selectAll.indeterminate = count > 0 && count < components.length;
          selectAll.addEventListener("change", () => {
            if (selectAll.checked) components.forEach(item => selected.add(item.id)); else selected.clear();
            draw();
          });
        }
        root.querySelectorAll("[data-block-recipients]").forEach(button => button.addEventListener("click", () => openEffectRecipientsEditor([button.dataset.blockRecipients])));
        action("[data-block-bulk-recipients]", () => openEffectRecipientsEditor(effects.map(item => item.id)), false);
        action("[data-block-recipients-all]", () => openEffectRecipientsEditor(blockEffects.map(item => item.id)), false);
        const changeEvents = async (pause, items) => {
          const errors = [];
          for (const item of items) {
            try { await applyProgramMutation(() => executeHatgInstruction("PauseComponent", { component_id: item.id, paused: pause }, { definition: false }), null); }
            catch (error) { errors.push(`${item.title || item.id}：${error.message}`); }
          }
          const done = items.length - errors.length;
          toast(errors.length
            ? (pause ? window.PortalI18n.text`已暂停 ${done} 个事件；失败：${errors.join("；")}` : window.PortalI18n.text`已恢复 ${done} 个事件；失败：${errors.join("；")}`)
            : (pause ? window.PortalI18n.text`已暂停 ${done} 个事件。` : window.PortalI18n.text`已恢复 ${done} 个事件。`), errors.length ? "error" : "success");
        };
        action("[data-block-events-pause]", () => changeEvents(true, events), false);
        action("[data-block-events-resume]", () => changeEvents(false, events), false);
        root.querySelectorAll("[data-block-event-switch]").forEach(button => button.addEventListener("click", () => {
          const item = components.find(component => component.id === button.dataset.blockEventSwitch);
          if (!item) return;
          changeEvents(!programEventSourceRuntime(item.id).paused, [item])
            .catch(error => toast(error.message || String(error), "error"));
        }));
        window.PortalLineageElementFields.hydrateRecipientChips(root);
        if (editingId && components.some(item => item.id === editingId)) {
          root.querySelector("[data-block-object-editor]").append(editor);
          renderProgramInspector({ panel: editor, objectId: editingId, onClose: () => { editingId = ""; draw(); }, onSaved: () => { editor.querySelector("form")?.setAttribute("data-program-draft-dirty", "false"); draw(); } });
        }
      };
      draw();
      functionBlockMenuRefresh = draw;
      if (pane === "issues") {
        const unseen = functionBlockInstanceUnacknowledgedFailures(functionBlockInstanceById(blockId));
        if (unseen.length) acknowledgeFunctionBlockFailures(unseen);
      }
      return () => { disposed = true; if (functionBlockMenuRefresh === draw) functionBlockMenuRefresh = null; };
    }

    async function unfunctionalize(id) {
      const block = functionBlockInstanceById(id);
      if (!block || block.family) return;
      await applyProgramMutation(() => executeHatgInstruction("Unfunctionalize", { function_block_id: id }), "Function Block 已拆开。");
      state.selectedProgramObjectId = "";
      programFeature.clearSelection();
    }

    async function deleteFunctionBlock(id) {
      const block = functionBlockInstanceById(id);
      if (!block || block.family) return;
      const residents = functionBlockElements(block).filter(element => element.kind === "resident");
      const details = residents.map(element => `${graphObjectTitle(element.id)} (${element.id})`).join("\n");
      const confirmed = await window.PortalUI.confirm({
        title: window.PortalI18n.t("删除 Function Block？"),
        message: window.PortalI18n.text`将删除「${block.title}」及其全部元素。将删除的 Resident：\n${details || window.PortalI18n.t("无")}`,
        confirmLabel: window.PortalI18n.t("删除"), tone: "danger",
      });
      if (!confirmed) return;
      await applyProgramMutation(() => executeHatgInstruction("DeleteFunctionBlock", { function_block_id: id }), "Function Block 已删除。");
      state.selectedProgramObjectId = "";
      programFeature.clearSelection();
    }

    return Object.freeze({
      refreshFunctionBlockMenu, openFunctionBlockMenu, functionBlockSourceTitle,
      openEffectRecipientsEditor, effectRecipientsChipsHtml, effectRecipientsButtonHtml,
      functionBlockRecipientsSummaryHtml, functionBlockOverviewWorkflowHtml, bindFamilyWorkGuideForms,
    });
  }

  window.PortalHatgFunctionBlockMenu = Object.freeze({
    createFunctionBlockMenu, dependencies: DEPENDENCIES, effectRecipients, effectRecipientsState,
  });
})();
