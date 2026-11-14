(() => {
  "use strict";

  if (window.PortalResidentMenuDuties) return;

  // The Duties tab (decision_13725a7900f6): one sentence per duty the Resident
  // holds, in its order. Expanding a duty lists the events, Effects and wires
  // it added; a duty can move up or down or be removed, which deletes its
  // Function Block and leaves the Resident and its other duties as they are.
  // System duties are settings the platform runs for the Resident; their
  // column starts folded and links to where each is edited.

  const DEPENDENCIES = Object.freeze([
    "$id", "changed", "esc", "menuState", "switchNodeSettingsTab", "toast",
  ]);

  const CATEGORY_LABELS = Object.freeze({fixed: "固定", common: "常用", custom: "自定义"});
  const KIND_LABELS = Object.freeze({
    event_source: "事件", effect: "Effect", loop: "循环", filter: "过滤", selector: "选择", reducer: "汇总",
    seed: "Seed", resident: "Resident", function_block: "Function Block", wire: "连线",
  });

  function createDuties(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalResidentMenuDuties is missing: ${missing.join(", ")}`);
    const {
      $id, changed, esc, menuState, switchNodeSettingsTab, toast,
    } = dependencies;
    const t = text => window.PortalI18n.t(text);

    function heldDuties(lineage, holderId) {
      return (lineage?.function_blocks || [])
        .filter(block => block.duty?.holder_id === holderId)
        .sort((a, b) => a.duty.position - b.duty.position || String(a.created_at).localeCompare(String(b.created_at)));
    }

    function elementRows(lineage, block) {
      const program = lineage.program || {};
      const titles = new Map([
        ...(lineage.nodes || []).map(node => [node.id, node.title || node.id]),
        ...(program.components || []).map(item => [item.id, item.title || item.id]),
        ...(lineage.function_blocks || []).map(item => [item.id, item.title || item.id]),
      ]);
      const components = new Map((program.components || []).map(item => [item.id, item]));
      const wires = new Map((program.wires || []).map(item => [item.id, item]));
      return (block.elements || []).map(element => {
        if (element.kind === "wire") {
          const wire = wires.get(element.id);
          const label = wire ? `${titles.get(wire.source) || wire.source} → ${titles.get(wire.target) || wire.target}` : element.id;
          return [t(KIND_LABELS.wire), label];
        }
        if (element.kind === "component") {
          const component = components.get(element.id);
          return [t(KIND_LABELS[component?.kind] || "组件"), titles.get(element.id) || element.id];
        }
        return [t(KIND_LABELS[element.kind] || element.kind), titles.get(element.id) || element.id];
      });
    }

    function dutyHtml(lineage, block, index, count, busy) {
      const duty = block.duty;
      const rows = elementRows(lineage, block);
      const disabled = busy ? " disabled" : "";
      return window.PortalI18n.html`<li class="residentMenuDuty" data-duty-id="${esc(block.id)}">
        <details>
          <summary><span class="residentMenuDutyText">${esc(duty.description || block.title)}</span><span class="residentMenuDutyCategory">${esc(t(CATEGORY_LABELS[duty.category] || duty.category))}</span></summary>
          <div class="residentMenuDutyBody">
            <p class="paneNote">${esc(window.PortalI18n.text`来自：${block.title}`)}</p>
            ${rows.length ? window.PortalI18n.html`<dl class="residentMenuDutyElements">${rows.map(([kind, title]) => window.PortalI18n.html`<div><dt>${esc(kind)}</dt><dd>${esc(title)}</dd></div>`).join("")}</dl>` : ""}
            <div class="residentMenuDutyActions">
              <button class="portalButton is-compact" type="button" data-duty-move="-1"${index === 0 ? " disabled" : disabled}>上移</button>
              <button class="portalButton is-compact" type="button" data-duty-move="1"${index === count - 1 ? " disabled" : disabled}>下移</button>
              <button class="portalButton is-compact is-danger" type="button" data-duty-remove${duty.category === "fixed" ? " disabled" : disabled}>撤下</button>
            </div>
          </div>
        </details>
      </li>`;
    }

    function quotaHtml(settings) {
      const value = settings?.quota_switch?.auto_switch_on_quota;
      const state = typeof value === "boolean" ? (value ? t("已开启") : t("已关闭")) : t("无法读取");
      return window.PortalI18n.html`<li class="residentMenuDuty residentMenuSystemDuty">
        <span class="residentMenuDutyText">额度不足时自动切换账号</span>
        <span class="residentMenuDutyCategory" data-duty-quota-state>${esc(state)}</span>
        <button class="portalButton is-compact" type="button" data-duty-open-settings>在 Settings 中修改</button>
      </li>`;
    }

    function render(context) {
      const root = $id("nodeDutiesRows");
      const held = heldDuties(context.dutiesLineage, context.residentId);
      const open = new Set([...root.querySelectorAll(".residentMenuDuty details[open]")].map(item => item.closest("[data-duty-id]")?.dataset.dutyId));
      const systemOpen = root.querySelector(".residentMenuSystemDuties")?.open || false;
      root.innerHTML = window.PortalI18n.html`<p class="paneNote">每条职责一句话；展开可看到它连接的事件、Effect 和连线。把模板施加到这个 Resident 上即可增加职责。</p>`
        + (held.length
          ? window.PortalI18n.html`<ol class="residentMenuDuties">${held.map((block, index) => dutyHtml(context.dutiesLineage, block, index, held.length, context.dutiesBusy)).join("")}</ol>`
          : window.PortalI18n.html`<p class="residentMenuJournalMeta">还没有职责。</p>`)
        + window.PortalI18n.html`<details class="residentMenuSystemDuties"${systemOpen ? " open" : ""}><summary>系统职责</summary><ol class="residentMenuDuties">${quotaHtml(context.dutiesSettings)}</ol></details>`;
      root.querySelectorAll("[data-duty-id]").forEach(item => {
        if (open.has(item.dataset.dutyId)) item.querySelector("details").open = true;
      });
    }

    async function loadNodeDuties() {
      const context = menuState.nodeMenuContext;
      const lineageId = menuState.lineage?.lineage_id;
      if (!context || !lineageId || context.dutiesBusy) return;
      context.dutiesBusy = true;
      const root = $id("nodeDutiesRows");
      if (!context.dutiesLineage) root.textContent = t("正在读取职责…");
      try {
        const [lineage, settings] = await Promise.all([
          window.PortalInstructionClient.execute("GetLineage", {lineage_id: lineageId}).then(result => result.lineage),
          window.PortalInstructionClient.execute("GetResidentSettings", {resident_id: context.residentId}).catch(() => null),
        ]);
        if (menuState.nodeMenuContext !== context) return;
        context.dutiesLineage = lineage;
        context.dutiesSettings = settings;
      } catch (error) {
        if (menuState.nodeMenuContext === context) root.textContent = window.PortalI18n.text`职责读取失败：${error.message || error}`;
        return;
      } finally {
        if (menuState.nodeMenuContext === context) context.dutiesBusy = false;
      }
      render(context);
    }

    async function mutate(context, operation, message) {
      if (context.dutiesBusy) return;
      context.dutiesBusy = true;
      render(context);
      try {
        await operation();
        toast(message, "success");
        await changed({type: "duties", residentId: context.residentId, lineageId: menuState.lineage?.lineage_id});
      } catch (error) {
        toast(error.message || String(error), "error");
      } finally {
        if (menuState.nodeMenuContext === context) context.dutiesBusy = false;
      }
      if (menuState.nodeMenuContext === context) await loadNodeDuties();
    }

    async function onClick(event) {
      const context = menuState.nodeMenuContext;
      if (!context?.dutiesLineage) return;
      if (event.target.closest("[data-duty-open-settings]")) { await switchNodeSettingsTab("display"); return; }
      const item = event.target.closest("[data-duty-id]");
      const button = event.target.closest("button");
      if (!item || !button || button.disabled) return;
      const lineageId = context.dutiesLineage.lineage_id;
      const held = heldDuties(context.dutiesLineage, context.residentId);
      const block = held.find(entry => entry.id === item.dataset.dutyId);
      if (!block) return;
      if (button.hasAttribute("data-duty-move")) {
        const ids = held.map(entry => entry.id);
        const from = ids.indexOf(block.id), to = from + Number(button.dataset.dutyMove);
        if (to < 0 || to >= ids.length) return;
        [ids[from], ids[to]] = [ids[to], ids[from]];
        await mutate(context, () => window.PortalInstructionClient.execute("OrderDuties", {
          lineage_id: lineageId, holder_id: context.residentId, function_block_ids: ids,
        }), t("职责顺序已更新。"));
        return;
      }
      if (button.hasAttribute("data-duty-remove")) {
        const confirmed = await window.PortalUI.confirm({
          title: t("撤下这条职责？"),
          message: window.PortalI18n.text`“${block.duty.description || block.title}”连接的 ${(block.elements || []).length} 个元素会从图上删除；这个 Resident 和它的其他职责不受影响。`,
          confirmLabel: t("撤下"), tone: "danger",
        });
        if (!confirmed || menuState.nodeMenuContext !== context) return;
        await mutate(context, () => window.PortalInstructionClient.execute("DeleteFunctionBlock", {
          lineage_id: lineageId, function_block_id: block.id,
        }), t("职责已撤下。"));
      }
    }

    $id("nodeDutiesRows").addEventListener("click", event => {
      void onClick(event).catch(error => toast(error.message || String(error), "error"));
    });

    return {
      loadNodeDuties,
    };
  }

  window.PortalResidentMenuDuties = Object.freeze({ createDuties, dependencies: DEPENDENCIES });
})();
