(() => {
  "use strict";
  const esc = value => window.PortalUIPrimitives.escapeHtml(value);
  // Both Family menus edit the same persisted policy using the account catalog's types.
  function mountMigrationPriority(root, {lineageId, familyId, readonly = false, saved = () => {}}) {
    let disposed = false, loaded = false, busy = false, baseline = [], order = [], types = new Map();
    const current = () => !disposed && root.isConnected;
    const t = value => window.PortalI18n.t(value);
    const execute = (...args) => window.PortalInstructionClient.execute(...args);
    root.innerHTML = `<details class="familyMigrationPriority"><summary>${esc(t("账号种类优先级"))}</summary><div data-priority-body></div></details>`;
    const panel = root.querySelector('[data-priority-body]');
    const defaults = () => [...types.values()].sort((a,b) => a.default_priority - b.default_priority || a.id.localeCompare(b.id)).map(item => item.id);
    const expand = selected => [...new Set([...selected, ...defaults()])];
    const draw = () => {
      panel.innerHTML = `<p class="paneNote">${esc(t("先换同种类的可用账号，再按以下顺序切换。"))}</p>
        <ol>${order.map((key, i) => `<li><span>${esc(types.get(key)?.label || key + '（当前不可用）')}</span><button class="portalButton is-compact" type="button" data-priority-move="${i}" data-direction="-1" aria-label="${esc(t('上移') + ' ' + (types.get(key)?.label || key))}"${readonly || busy || !i ? ' disabled' : ''}>↑</button><button class="portalButton is-compact" type="button" data-priority-move="${i}" data-direction="1" aria-label="${esc(t('下移') + ' ' + (types.get(key)?.label || key))}"${readonly || busy || i === order.length - 1 ? ' disabled' : ''}>↓</button></li>`).join('')}</ol>
        <p role="alert" data-priority-error hidden></p>
        <div><button type="button" class="portalButton is-compact" data-priority-save${readonly || busy ? ' disabled' : ''}>${esc(t('保存顺序'))}</button> <button type="button" class="portalButton is-compact" data-priority-cancel${busy ? ' disabled' : ''}>${esc(t('取消'))}</button> <button type="button" class="portalButton is-compact" data-priority-default${readonly || busy ? ' disabled' : ''}>${esc(t('恢复默认'))}</button></div>`;
      panel.querySelectorAll('[data-priority-move]').forEach(button => button.onclick = () => {
        const i = Number(button.dataset.priorityMove), j = i + Number(button.dataset.direction);
        [order[i], order[j]] = [order[j], order[i]]; draw();
        panel.querySelector(`[data-priority-move="${j}"][data-direction="${button.dataset.direction}"]`)?.focus();
      });
      panel.querySelector('[data-priority-cancel]').onclick = () => { order = expand(baseline); draw(); root.querySelector('details').open = false; };
      panel.querySelector('[data-priority-default]').onclick = () => { order = defaults(); draw(); };
      panel.querySelector('[data-priority-save]').onclick = async () => {
        if (busy || readonly) return;
        busy = true; draw();
        try {
          const snapshot = (await execute('GetLineage', {lineage_id: lineageId})).lineage;
          if (!current()) return;
          const block = snapshot.function_blocks.find(item => item.id === familyId);
          if (!block) throw new Error(t('Family 已不存在，请重新打开菜单。'));
          if (JSON.stringify(block.family.auto_migrate_priority || []) !== JSON.stringify(baseline)) throw new Error(t('顺序已被其他人修改，请重新打开菜单后保存。'));
          const priority = JSON.stringify(order) === JSON.stringify(defaults()) ? [] : [...order];
          const result = await execute('UpdateFamily', {lineage_id: lineageId, family_id: familyId, auto_migrate_priority: priority}, {expectedVersion: snapshot.revision});
          if (!current()) return;
          baseline = priority; busy = false; draw(); root.querySelector('details').open = false;
          await saved(result);
        } catch (error) {
          if (!current()) return;
          busy = false; draw(); const notice = panel.querySelector('[data-priority-error]'); notice.textContent = error.message; notice.hidden = false;
        } finally { busy = false; }
      };
    };
    root.querySelector('details').addEventListener('toggle', async () => {
      if (!root.querySelector('details').open || loaded || busy) return;
      busy = true; panel.textContent = t('正在读取账号种类…');
      try {
        const [catalog, graph] = await Promise.all([execute('ListProviderAccounts', {}), execute('GetLineage', {lineage_id: lineageId})]);
        if (!current()) return;
        const block = graph.lineage.function_blocks.find(item => item.id === familyId);
        if (!block) throw new Error(t('Family 已不存在，请重新打开菜单。'));
        types = new Map((catalog.accounts || []).filter(a => a.migration_type).map(a => [a.migration_type.id, a.migration_type]));
        baseline = [...(block.family.auto_migrate_priority || [])]; order = expand(baseline); loaded = true; busy = false; draw();
      } catch (error) { if (current()) panel.textContent = error.message; }
      finally { busy = false; }
    });
    return {destroy() { disposed = true; }};
  }

  const nodeResidentId = node => String(node?.resident_id || node?.id || "");
  const nodeIntakeState = node => node?.intake_state === "closed" ? "closed" : "open";
  const familyBlocks = lineage => (lineage?.function_blocks || []).filter(block => block.family);
  // A Family member leaves its Family when it moves; the Family's own roles and
  // the personal assistant's default AMA stay with it (decision_44cf587c1721).
  function transferBlocker(lineage, residentId) {
    if (residentId === lineage?.leader_resident_id) return window.PortalI18n.t("请先指定新的 Leader，再转移此 Resident。");
    for (const block of familyBlocks(lineage)) {
      if (["facade", "router", "steward"].some(role => block.family[role] === residentId)) {
        return window.PortalI18n.t("Family 的门面、Router 和 steward 随 Family 一起存在，不能单独迁移。");
      }
      if (block.family.personal_ama_id === residentId) return window.PortalI18n.t("这是个人助手的默认 AMA，不能迁出私人 Lineage。");
    }
    return "";
  }
  const memberFamily = (lineage, residentId) => familyBlocks(lineage).find(block => (block.family.members || []).includes(residentId)) || null;
  function familyMemberCandidates(lineage, family) {
    const blocks = familyBlocks(lineage);
    const excluded = new Set([family.family.facade, ...blocks.flatMap(block => [
      block.family.router, block.family.steward, ...(block.family.members || []),
      ...(block.family.lineage_scope ? [block.family.facade] : []),
    ])]);
    // A parent's facade cannot be adopted by its descendant.
    let parent = memberFamily(lineage, family.family.facade);
    const visited = new Set();
    while (parent && !visited.has(parent.id)) {
      visited.add(parent.id);
      excluded.add(parent.family.facade);
      parent = memberFamily(lineage, parent.family.facade);
    }
    return (lineage.nodes || []).filter(node => nodeResidentId(node) && !excluded.has(nodeResidentId(node))
      && !node.deleted_at && node.accessible !== false);
  }
  const toast = (message, kind = "success", timeout = 3600) => window.PortalUI.toast(message, kind, {duration: timeout});
  let instance = null;
  let openSequence = 0;
  let openingResidentId = "";

  function retireResidents(residentIds) {
    const deleted = new Set(residentIds.map(id => String(id || "").trim()).filter(Boolean));
    if (deleted.has(openingResidentId)) {
      openSequence += 1;
      openingResidentId = "";
    }
    instance?.retireResidents(deleted);
  }

  async function removeFamily({family, lineageId, action, execute = (name, args) => window.PortalInstructionClient.execute(name, args)}) {
    if (!["unfamilize", "delete"].includes(action)) throw new TypeError("Unknown Family action");
    const deleting = action === "delete";
    const confirmed = await window.PortalUI.confirm({
      title: deleting ? window.PortalI18n.t("删除整个 Family？") : window.PortalI18n.t("Unfamilize（保留成员）？"),
      message: deleting
        ? window.PortalI18n.text`删除「${family.title || "Family"}」及其内部全部对象：门面、Router、Steward、所有成员、子 Family 和工作流。正在运行的 Session 会停止，相关图引用会移除；关联 Workspace 仅在没有其他存活 Resident 引用时回收，仍被引用的 Workspace 保留。对话和运行轨迹保留。所在 Lineage 和 Family 外的成员保留。`
        : window.PortalI18n.text`解除「${family.title || "Family"}」的组合，保留成员、子 Family、业务工作流及其会话和工作区；删除门面、Router、Steward 与自动生成的协作结构，并将领导职责和门面连线交还成员。`,
      confirmLabel: deleting ? window.PortalI18n.t("删除整个 Family") : window.PortalI18n.t("Unfamilize · 保留成员"),
      tone: "danger",
    });
    if (!confirmed) return null;
    return execute(deleting ? "DeleteFamily" : "Unfamilize", {lineage_id: lineageId, family_id: family.id});
  }

  function createMenu() {
    const host = document.createElement("div");
    host.innerHTML = window.PortalI18n.html`<div class="portalDetailOverlay" data-portal-detail-overlay id="nodeSettingsModal" hidden aria-hidden="true">
      <section data-portal-detail-surface class="portalDetailSurface nodeSettingsSurface" role="dialog" aria-modal="true" aria-labelledby="nodeSettingsTitle">
        <div class="portalDetailHeader residentMenuHeader" data-portal-detail-header>
          <div class="residentMenuHeading"><h2 id="nodeSettingsTitle">Menu</h2><div class="residentMenuHeaderActions"><button class="portalButton is-icon" id="nodeMenuPin" type="button" aria-label="置顶" aria-pressed="false" disabled data-portal-icon="pin"></button><button class="portalDetailClose" data-portal-detail-close type="button" aria-label="关闭" data-portal-icon="close"></button></div></div>
          <div class="residentMenuIdentity">
            <div class="residentMenuPortrait" id="nodeMenuPortrait"></div>
            <div class="residentMenuIdentityCopy">
              <div class="residentMenuName"><strong id="nodeMenuName"></strong><button class="portalButton is-icon is-quiet" id="nodeMenuRename" type="button" aria-label="重命名" data-portal-icon="edit"></button></div>
              <div id="nodeMenuRenameFields" hidden><label><span id="nodeSettingsNameLabel">Resident 名称</span><input id="nodeSettingsName" form="nodeSettingsForm" maxlength="120" autocomplete="off" /></label><button class="portalButton" type="submit" form="nodeSettingsForm">保存名称</button><button class="portalButton" type="button" id="nodeMenuRenameCancel">取消</button></div>
              <div class="residentMenuMeta">
                <div class="residentMenuIdRow"><code id="nodeResidentIdentityReadout"></code><section id="nodeSettingsIntake"><button class="portalButton is-compact residentMenuToggle" id="nodeSettingsIntakeToggle" type="button" aria-label="切换接单状态"><span id="nodeSettingsIntakeState">OPEN</span></button><span id="nodeSettingsIntakeHint" class="residentMenuIntakeHint"></span></section></div>
                <div class="residentMenuLineage"><span id="nodeLineageIdentityReadout"></span><button class="portalButton is-compact" id="transferNodeBtn" type="button" aria-expanded="false" aria-controls="nodeTransferFields">Move</button></div>
              </div>
              <div id="nodeTransferFields" hidden><p>转移保留 Resident 身份与会话，并迁移自有 Workspace。未保存的名称不会提交。</p><p id="nodeTransferFamily" hidden></p><label class="portalField"><span>目标 Lineage</span><select id="nodeTransferTarget" disabled><option value="">加载中…</option></select></label><p id="nodeTransferStatus" role="status"></p><button class="portalButton is-primary" id="nodeTransferConfirmBtn" type="button" disabled>确认转移</button></div>
            </div>
          </div>
        </div>
        <div class="portalDetailBody nodeSettingsBody" data-portal-detail-body>
        <nav class="nodeSettingsTabs" aria-label="菜单分栏">
          <div class="residentMenuGroupTabs" id="nodeMenuGroupTabs" role="tablist" aria-label="模块中的部件" aria-orientation="vertical" hidden></div>
          <div class="nodeSettingsResidentTabs" role="tablist" aria-label="节点设置分栏" aria-orientation="vertical">
          <button class="nodeSettingsTab active" id="nodeDisplayTab" type="button" role="tab" aria-selected="true" aria-controls="nodeDisplayPanel">Settings</button>
          <button class="nodeSettingsTab" id="nodeHarnessTab" type="button" role="tab" aria-selected="false" aria-controls="nodeHarnessPanel" tabindex="-1">Harness</button>
          <button class="nodeSettingsTab" id="nodeDetailsTab" type="button" role="tab" aria-selected="false" aria-controls="nodeDetailsPanel" tabindex="-1">Package</button>
          <button class="nodeSettingsTab" id="nodeMailboxTab" type="button" role="tab" aria-selected="false" aria-controls="nodeMailboxPanel" tabindex="-1">Mailbox</button>
          <button class="nodeSettingsTab" id="nodeJournalTab" type="button" role="tab" aria-selected="false" aria-controls="nodeJournalPanel" tabindex="-1">Work Journal</button>
          </div>
        </nav>
        <div class="nodeSettingsPanels">
        <div class="residentMenuGroupPanel" id="nodeMenuGroupPanel" role="tabpanel" hidden></div>
        <p id="nodeMenuStatus" role="status" hidden></p>
        <section id="nodeHarnessPanel" class="nodeHarnessPanel" role="tabpanel" aria-labelledby="nodeHarnessTab" hidden></section>
        <section id="nodeDetailsPanel" role="tabpanel" aria-labelledby="nodeDetailsTab" hidden></section>
        <section id="nodeMailboxPanel" class="nodeMailboxPanel" role="tabpanel" aria-labelledby="nodeMailboxTab" hidden></section>
        <section id="nodeJournalPanel" role="tabpanel" aria-labelledby="nodeJournalTab" hidden><div id="nodeJournalRows" aria-live="polite"></div><button class="portalButton" type="button" id="nodeJournalRetry" hidden>重试</button></section>
        <section id="nodeDisplayPanel" role="tabpanel" aria-labelledby="nodeDisplayTab">
        <div id="nodeMenuFamilySettings" hidden></div>
        <form id="nodeSettingsForm" class="nodeSettingsForm">
          <div id="nodeMenuRuntime"><p>正在读取运行设置…</p></div>
          <div class="residentMenuSession"><span>Session ID</span><code id="nodeMenuSessionId" role="status">读取中…</code><button class="portalButton is-icon is-quiet" id="nodeMenuCopySession" type="button" aria-label="复制 Session ID" title="复制当前后端会话的完整 Session ID" data-portal-icon="copy" disabled></button></div>
          <section id="nodeMenuAvatar" aria-label="Resident avatar"></section>
          <div class="portalDetailFooter nodeSettingsFooter" data-portal-detail-footer><button class="portalButton is-danger" id="removeNodeBtn" type="button">删除节点</button></div>
        </form>
        </section>
        </div>
        </div>
      </section>
    </div>`;
    const modal = host.firstElementChild;
    document.body.append(modal);
    const $id = id => modal.id === id ? modal : modal.querySelector(`#${CSS.escape(id)}`);
    const societyService = window.PortalSocietyService.createClient();
    let lineage = null;
    let options = {};
    const surface = window.PortalDetailSurface.mount(modal.querySelector("[data-portal-detail-surface]"));
    const changed = async detail => {
      window.dispatchEvent(new CustomEvent("portal:resident-menu-changed", {detail}));
      await options.onChanged?.(detail);
    };
    let nodeDetailsInstance = null;
    let nodeDetailsEpoch = 0;
    let nodeDetailsTransition = false;

    function disposeNodeDetails() {
      nodeDetailsEpoch += 1;
      nodeDetailsInstance?.destroy?.();
      nodeDetailsInstance = null;
      $id("nodeDetailsPanel")?.replaceChildren();
    }

    async function leaveNodeDetails() {
      if (nodeDetailsTransition || nodeTransferInFlight || nodeMenuContext?.busy || nodeMenuContext?.saving || nodeMenuContext?.intakeBusy) return false;
      if (nodeDetailsInstance?.isBusy?.()) {
        toast(window.PortalI18n.t("凭证操作正在处理，请稍后再试。"), "info");
        return false;
      }
      const epoch = nodeDetailsEpoch;
      nodeDetailsTransition = true;
      try {
        if (nodeDetailsInstance?.hasUnsavedChanges?.() && !await window.PortalUI.confirm({
          title: window.PortalI18n.t("放弃未保存的凭证修改？"), message: window.PortalI18n.t("离开 Package 将丢失尚未保存的凭证修改。"),
          confirmLabel: window.PortalI18n.t("放弃修改"), tone: "danger",
        })) return false;
        return epoch === nodeDetailsEpoch && !nodeDetailsInstance?.isBusy?.();
      } finally {
        nodeDetailsTransition = false;
      }
    }

    // The node dialog's vertical navigation: tab name -> [tab button, panel].
    const NODE_SETTINGS_TABS = Object.freeze({
      display: ["nodeDisplayTab", "nodeDisplayPanel"],
      harness: ["nodeHarnessTab", "nodeHarnessPanel"],
      details: ["nodeDetailsTab", "nodeDetailsPanel"],
      mailbox: ["nodeMailboxTab", "nodeMailboxPanel"],
      journal: ["nodeJournalTab", "nodeJournalPanel"],
    });
    let nodeMailboxInstance = null;
    let nodeHarnessInstance = null;

    // The Harness page holds the Resident's Harness settings, moved here from the Chat gear.
    function disposeNodeHarness() {
      nodeHarnessInstance?.destroy?.();
      nodeHarnessInstance = null;
      $id("nodeHarnessPanel")?.replaceChildren();
    }

    // The Mailbox tab follows the queue only while it is open.
    function disposeNodeMailbox() {
      nodeMailboxInstance?.destroy?.();
      nodeMailboxInstance = null;
      $id("nodeMailboxPanel")?.replaceChildren();
    }

    function currentNodeSettingsTab() {
      return Object.keys(NODE_SETTINGS_TABS).find(
        tab => $id(NODE_SETTINGS_TABS[tab][0])?.getAttribute("aria-selected") === "true",
      ) || "display";
    }

    function renderNodeSettingsTab(tab) {
      for (const [name, [tabId, panelId]] of Object.entries(NODE_SETTINGS_TABS)) {
        const selected = name === tab;
        $id(panelId).hidden = !selected;
        $id(tabId).setAttribute("aria-selected", String(selected));
        $id(tabId).tabIndex = selected ? 0 : -1;
        $id(tabId).classList.toggle("active", selected);
      }
      // The card keeps its size; each tab starts at its top.
      const body = $id(NODE_SETTINGS_TABS[tab]?.[1])?.closest(".nodeSettingsBody");
      if (body) body.scrollTop = 0;
    }

    function moveNodeSettingsTab(event) {
      const names = Object.keys(NODE_SETTINGS_TABS);
      const index = names.indexOf(currentNodeSettingsTab());
      const next = {
        ArrowDown: names[(index + 1) % names.length],
        ArrowUp: names[(index + names.length - 1) % names.length],
        Home: names[0],
        End: names[names.length - 1],
      }[event.key];
      if (!next) return;
      event.preventDefault();
      void switchNodeSettingsTab(next).then(() => $id(NODE_SETTINGS_TABS[currentNodeSettingsTab()][0])?.focus());
    }

    async function switchNodeSettingsTab(tab) {
      if (currentNodeSettingsTab() === tab) return;
      if (nodeMenuContext?.avatarEditor?.isBusy()) { toast(window.PortalI18n.t("头像正在保存，请稍后再试。"), "info"); return; }
      if (!await leaveNodeDetails()) return;
      // Leaving Settings drops an unsaved avatar choice, and the portrait shows the saved one again.
      nodeMenuContext?.avatarEditor?.close();
      disposeNodeDetails();
      disposeNodeMailbox();
      disposeNodeHarness();
      disposeFamilySettings();
      renderNodeSettingsTab(tab);
      const node = nodeSettingsNode();
      if (!node) return;
      if (tab === "mailbox") {
        nodeMailboxInstance = window.PortalMailboxPanel?.mount({
          container: $id("nodeMailboxPanel"),
          residentId: nodeResidentId(node),
          toast,
        }) || null;
        return;
      }
      if (tab === "journal") { await loadNodeJournal(); return; }
      if (tab === "harness") {
        const residentId = nodeResidentId(node);
        nodeHarnessInstance = window.PortalChatWorkspace?.mountResidentHarnessPanel?.({
          container: $id("nodeHarnessPanel"),
          residentId,
          onChanged: () => changed({type: "settings", residentId, lineageId: nodeMenuContext?.lineageId}),
        }) || null;
        return;
      }
      if (tab === "display") { await loadNodeMenuRuntime(); return; }
      if (tab !== "details") return;
      const epoch = nodeDetailsEpoch;
      const container = document.createElement("div");
      container.textContent = window.PortalI18n.t("正在加载 Package…");
      $id("nodeDetailsPanel").replaceChildren(container);
      try {
        const instance = await window.PortalNav.mountPrincipalDetails({ container, principalId: nodeResidentId(node) });
        if (epoch !== nodeDetailsEpoch) {
          instance?.destroy?.();
          return;
        }
        nodeDetailsInstance = instance;
      } catch (error) {
        if (epoch === nodeDetailsEpoch) container.textContent = window.PortalI18n.text`Package 加载失败：${error.message || error}`;
      }
    }

    function renderNodeSettingsIntake(node) {
      const panel = $id("nodeSettingsIntake");
      if (!panel) return;
      const available = !!nodeResidentId(node);
      panel.hidden = !available;
      if (!available) return;
      const intakeState = nodeIntakeState(node);
      const busy = !!nodeMenuContext?.intakeBusy;
      panel.dataset.state = intakeState;
      $id("nodeSettingsIntakeState").textContent = busy ? window.PortalI18n.t("切换中…") : intakeState.toUpperCase();
      $id("nodeSettingsIntakeHint").textContent = intakeState === "closed"
        ? window.PortalI18n.t("当前工作不会中断；新消息只进入 queue，切回 OPEN 后恢复执行。")
        : window.PortalI18n.t("空闲时会立即执行新消息；设为 CLOSED 后，新消息只进入 queue。");
      const button = $id("nodeSettingsIntakeToggle");
      button.disabled = busy;
      button.setAttribute("role", "switch");
      button.setAttribute("aria-checked", String(intakeState === "open"));
      button.title = $id("nodeSettingsIntakeHint").textContent;

    }

    function nodeSettingsNode() {
      return nodeMenuContext?.node || null;
    }

    let nodeTransferContext = null;
    let nodeTransferInFlight = false;

    async function openNodeTransfer() {
      const node = nodeSettingsNode();
      if (!node || transferBlocker(lineage, nodeResidentId(node))) return;
      const family = memberFamily(lineage, nodeResidentId(node));
      const context = { residentId: nodeResidentId(node), lineageId: lineage.lineage_id, title: node.title, family, targets: [], busy: false };
      nodeTransferContext = context;
      $id("nodeTransferFamily").hidden = !family;
      $id("nodeTransferFamily").textContent = family ? window.PortalI18n.text`它会离开 Family「${family.family.title || family.title || "Family"}」：Router 不再把请求交给它；已经转交给它的请求仍由它完成并经门面回复。` : "";
      $id("nodeTransferFields").hidden = false;
      $id("transferNodeBtn").setAttribute("aria-expanded", "true");
      const target = $id("nodeTransferTarget");
      target.disabled = true;
      target.replaceChildren(new Option("加载中…", ""));
      $id("nodeTransferConfirmBtn").disabled = true;
      $id("nodeTransferStatus").textContent = window.PortalI18n.t("正在读取你拥有的 Lineage…");
      try {
        const payload = await societyService.listLineages();
        if (nodeTransferContext !== context) return;
        context.targets = (payload.lineages || []).filter(item => item.lineage_id !== context.lineageId);
        target.replaceChildren(new Option("请选择目标 Lineage", ""), ...context.targets.map(item => new Option(`${item.title} · ${item.lineage_id}`, item.lineage_id)));
        target.disabled = !context.targets.length;
        $id("nodeTransferStatus").textContent = context.targets.length ? window.PortalI18n.t("请选择接收此 Resident 的 Lineage。") : window.PortalI18n.t("没有可接收的其他 Lineage。请先创建目标 Lineage 或由其 owner 授权。");
      } catch (error) {
        if (nodeTransferContext !== context) return;
        target.replaceChildren(new Option("加载失败", ""));
        $id("nodeTransferStatus").textContent = window.PortalI18n.text`加载失败：${error.message || error}。可再次点击 Move 重试。`;
      }
    }

    async function submitNodeTransfer() {
      const context = nodeTransferContext;
      const destination = context?.targets.find(item => item.lineage_id === $id("nodeTransferTarget").value);
      if (!destination || context.busy || nodeTransferInFlight) return;
      nodeTransferInFlight = true;
      context.busy = true;
      const button = $id("nodeTransferConfirmBtn");
      button.disabled = true;
      $id("transferNodeBtn").disabled = true;
      let accepted = false;
      try {
        const confirmed = await window.PortalUI.confirm({
          title: window.PortalI18n.t("转移到其他 Lineage？"),
          message: window.PortalI18n.text`将「${context.title}」转移到「${destination.title}」。Resident 身份与会话保留，自有 Workspace 与成员关系会迁移，访问权限随目标 Lineage 生效。未保存的表单修改不会提交。`
            + (context.family ? " " + $id("nodeTransferFamily").textContent : ""),
          confirmLabel: window.PortalI18n.t("确认转移"),
        });
        if (!confirmed || nodeTransferContext !== context || lineage?.lineage_id !== context.lineageId) return;
        $id("nodeSettingsModal").querySelectorAll("button, input, textarea, select").forEach(control => {
          control.dataset.transferDisabled = String(control.disabled);
          control.disabled = true;
        });
        if (nodeTransferContext !== context || lineage?.lineage_id !== context.lineageId) return;
        $id("nodeTransferStatus").textContent = window.PortalI18n.t("正在提交迁移，请勿重复操作…");

        const result = await window.PortalResidentService.update({
          resident_id: context.residentId,
          lineage_id: context.lineageId,
          target_lineage_id: destination.lineage_id,
        });
        accepted = true;
        window.PortalLineageCatalogs?.invalidate();
        if (nodeTransferContext === context) dialog.close({force: true});
        await changed({
          type: "move", residentId: context.residentId, lineageId: context.lineageId,
          targetLineageId: destination.lineage_id, result,
        });
        toast(result.workspace_pending ? window.PortalI18n.t("成员关系已转移；Workspace 正在后台迁移，请稍后在目标 Lineage 查看。") : window.PortalI18n.t("Resident 已转移到目标 Lineage。"), "success", 9000);
      } catch (error) {
        const message = accepted ? window.PortalI18n.text`迁移已受理，页面刷新失败，请刷新页面查看，勿重复迁移：${error.message || error}` : window.PortalI18n.text`转移失败：${error.message || error}`;
        if (nodeTransferContext === context) $id("nodeTransferStatus").textContent = message;
        toast(message, "error", 9000);
      } finally {
        nodeTransferInFlight = false;
        context.busy = false;
        $id("nodeSettingsModal").querySelectorAll("[data-transfer-disabled]").forEach(control => {
          control.disabled = control.dataset.transferDisabled === "true";
          delete control.dataset.transferDisabled;
        });
        if (nodeTransferContext === context) {
          button.disabled = accepted || !$id("nodeTransferTarget").value;
          $id("transferNodeBtn").disabled = accepted;
        }
      }
    }

    async function openNodeSettingsModal(requestedNode, tab = "display") {
      if (nodeTransferInFlight) {
        toast(window.PortalI18n.t("迁移正在处理，请等待本次操作结束。"), "info");
        return;
      }
      const node = requestedNode;
      if (!node) return;
      disposeNodeDetails();
      disposeNodeMailbox();
      disposeNodeHarness();
      renderNodeSettingsTab("display");
      $id("nodeSettingsNameLabel").textContent = window.PortalI18n.t("Resident 名称");
      $id("nodeSettingsName").value = node.title || "";
      disposeNodeMenu();
      nodeMenuContext = { residentId: nodeResidentId(node), lineageId: lineage.lineage_id, node, pinned: false, pinReady: false, busy: false };
      nodeMenuContext.family = (lineage.function_blocks || []).find(block => block.family?.facade === nodeResidentId(node)) || null;
      $id("nodeSettingsForm").hidden = Boolean(nodeMenuContext.family);
      $id("nodeMenuFamilySettings").hidden = !nodeMenuContext.family;
      // A Family facade has no runtime form; its avatar row sits above the Family settings instead.
      if (nodeMenuContext.family) $id("nodeMenuFamilySettings").before($id("nodeMenuAvatar"));
      else $id("nodeSettingsForm").querySelector(".nodeSettingsFooter").before($id("nodeMenuAvatar"));
      $id("nodeMenuName").textContent = node.title || nodeResidentId(node);
      $id("nodeMenuRenameFields").hidden = true;
      $id("nodeMenuStatus").hidden = true;
      $id("nodeMenuPortrait").textContent = (node.title || "R").slice(0, 1);
      $id("nodeJournalRows").replaceChildren();
      $id("nodeJournalRetry").hidden = true;
      $id("nodeMenuRuntime").textContent = window.PortalI18n.t("正在读取运行设置…");
      $id("nodeMenuSessionId").textContent = window.PortalI18n.t("读取中…");
      $id("nodeMenuCopySession").disabled = true;
      const sessionContext = nodeMenuContext;
      // A session may have been replaced while this menu was closed. Read the
      // binding on each explicit open instead of reusing the last displayed ID.
      if (!sessionContext.family) {
        window.PortalResidentProjection.invalidate(sessionContext.residentId);
        sessionContext.unobserveSession = window.PortalResidentProjection.subscribe(sessionContext.residentId,
          update => renderNodeMenuSession(sessionContext, update));
      }
      void loadNodeMenuIdentity(nodeMenuContext);
      $id("nodeSettingsTitle").textContent = window.PortalI18n.t("Menu");
      renderNodeSettingsIdentity(node);
      renderNodeSettingsIntake(node);

      const removeButton = $id("removeNodeBtn");
      const transferBlocked = transferBlocker(lineage, nodeResidentId(node));
      nodeTransferContext = null;
      $id("nodeTransferFields").hidden = true;
      $id("transferNodeBtn").disabled = Boolean(transferBlocked);
      $id("transferNodeBtn").title = transferBlocked || window.PortalI18n.t("迁移 Resident 身份、成员关系及自有 Workspace");
      $id("transferNodeBtn").setAttribute("aria-expanded", "false");
      removeButton.disabled = false;
      removeButton.textContent = window.PortalI18n.t("删除节点");
      removeButton.title = window.PortalI18n.t("Resident 将标记为已删除，并移除 Session 和图引用；关联 Workspace 仅在没有其他存活 Resident 引用时回收，仍被引用的 Workspace 保留。对话与运行轨迹保留。");
      dialog.open();
      if (tab !== "display") await switchNodeSettingsTab(tab);
      else await loadNodeMenuRuntime();
    }

    let nodeMenuContext = null;

    let groupPanelCleanup = null;
    let familySettingsView = null;
    let familyPriorityView = null;

    function disposeFamilySettings() {
      familyPriorityView?.destroy();
      familyPriorityView = null;
      familySettingsView?.destroy();
      familySettingsView = null;
      $id("nodeMenuFamilySettings").replaceChildren();
    }

    function renderFamilySettings(context) {
      disposeFamilySettings();
      const root = $id("nodeMenuFamilySettings");
      const autoMigrate = context.family.family.auto_migrate !== false;
      root.innerHTML = window.PortalI18n.html`<p class="paneNote">在这里查看和调整 Family 内部成员的工作状态、Backend、账号与接单设置。</p>
        <div class="familyAutoMigrateSetting"><span>当前 AI 后端额度不足时自动转到别的后端</span><button class="portalButton residentMenuToggle" type="button" role="switch" aria-label="额度不足时自动转组" aria-checked="${autoMigrate}" data-family-auto-migrate></button></div>
        <div data-family-priority></div>
        <p role="alert" data-family-settings-error hidden></p>
        <div class="residentMenuMembersHeader"><strong>成员</strong><button class="portalButton is-compact" type="button" data-family-add aria-expanded="false" aria-controls="familyMemberPicker">＋ 添加成员</button></div>
        <div id="familyMemberPicker" class="residentMenuMemberPicker" hidden></div>
        <div data-family-members></div>
        <div class="residentMenuFamilyActions">
          <section><div><strong>Unfamilize · 保留成员</strong><p>解除组合，保留成员、子 Family 和业务工作流；移除门面、Router、Steward 与自动生成的协作结构。</p></div><button class="portalButton" type="button" data-family-action="unfamilize">Unfamilize</button></section>
          <section><div><strong>删除整个 Family</strong><p>停止并删除门面及内部所有成员、子 Family 和工作流，移除 Session；关联 Workspace 仅在没有其他存活 Resident 引用时回收，仍被引用的 Workspace 保留。对话和运行轨迹保留。</p></div><button class="portalButton is-danger" type="button" data-family-action="delete">删除整个 Family</button></section>
        </div>`;
      familyPriorityView = mountMigrationPriority(root.querySelector('[data-family-priority]'), {
        lineageId: context.lineageId, familyId: context.family.id,
        saved: async result => {
          if (nodeMenuContext !== context) return;
          context.family = result.function_block;
          window.PortalResidentProjection.invalidate(context.residentId);
          await changed({type: "family-auto-migrate", residentId: context.residentId, lineageId: context.lineageId, result});
        },
      });
      familySettingsView = window.PortalChatWorkspace.mountFamilyStatus(context.residentId, root.querySelector("[data-family-members]"));
      root.querySelector("[data-family-auto-migrate]").addEventListener("click", () => saveFamilyAutoMigrate(context));
      root.querySelector("[data-family-add]").addEventListener("click", () => openFamilyMemberPicker(context));
      root.querySelectorAll("[data-family-action]").forEach(button => button.addEventListener("click", () => changeFamily(button.dataset.familyAction)));
    }

    async function saveFamilyAutoMigrate(context) {
      if (nodeMenuContext !== context || context.busy || context.saving) return;
      const root = $id("nodeMenuFamilySettings");
      const button = root.querySelector("[data-family-auto-migrate]");
      const errorLine = root.querySelector("[data-family-settings-error]");
      const current = () => nodeMenuContext === context && root.contains(button);
      const autoMigrate = context.family.family.auto_migrate === false;
      context.busy = true;
      button.disabled = true;
      errorLine.hidden = true;
      try {
        const snapshot = (await window.PortalInstructionClient.execute("GetLineage", {lineage_id: context.lineageId})).lineage;
        if (!current()) return;
        const family = snapshot.function_blocks.find(block => block.id === context.family.id && block.family?.facade === context.residentId);
        if (!family) throw new Error(window.PortalI18n.t("Family 已变化，请重新打开菜单。"));
        const result = await window.PortalInstructionClient.execute("UpdateFamily", {
          lineage_id: context.lineageId, family_id: family.id, auto_migrate: autoMigrate,
        }, {expectedVersion: snapshot.revision});
        if (!current()) return;
        context.family = result.function_block;
        lineage = {...lineage, function_blocks: (lineage.function_blocks || []).map(block => block.id === family.id ? result.function_block : block)};
        window.PortalResidentProjection.invalidate(context.residentId);
        renderFamilySettings(context);
        root.querySelector("[data-family-auto-migrate]").focus();
        await changed({type: "family-auto-migrate", residentId: context.residentId, lineageId: context.lineageId, result});
      } catch (error) {
        if (nodeMenuContext === context) {
          const notice = root.querySelector("[data-family-settings-error]");
          notice.textContent = error.message; notice.hidden = false;
        }
      } finally {
        context.busy = false;
        if (current()) button.disabled = false;
      }
    }

    async function openFamilyMemberPicker(context) {
      if (nodeMenuContext !== context || context.busy || context.saving) return;
      const root = $id("nodeMenuFamilySettings");
      const button = root.querySelector("[data-family-add]");
      const panel = root.querySelector("#familyMemberPicker");
      if (!panel.hidden) { panel.querySelector("input")?.focus(); return; }
      const request = {};
      context.memberPickerRequest = request;
      panel.hidden = false;
      button.setAttribute("aria-expanded", "true");
      const close = () => { context.memberPickerRequest = null; panel.hidden = true; panel.replaceChildren(); button.setAttribute("aria-expanded", "false"); button.focus(); };
      const current = () => nodeMenuContext === context && context.memberPickerRequest === request && panel.isConnected && !panel.hidden;
      panel.innerHTML = window.PortalI18n.html`<p role="status">正在读取可添加的成员…</p><button class="portalButton is-compact" type="button" data-member-cancel>取消</button>`;
      panel.querySelector("[data-member-cancel]").onclick = close;
      let snapshot, family;
      try {
        const result = await window.PortalInstructionClient.execute("GetLineage", {lineage_id: context.lineageId});
        if (!current()) return;
        snapshot = result.lineage;
        family = familyBlocks(snapshot).find(block => block.id === context.family.id);
        if (!family) throw new Error(window.PortalI18n.t("Family 已不存在，请重新打开 Menu。"));
      } catch (error) {
        if (!current()) return;
        panel.innerHTML = window.PortalI18n.html`<p role="alert">${esc(error.message)}</p><button class="portalButton is-compact" type="button" data-member-retry>重试</button><button class="portalButton is-compact" type="button" data-member-cancel>取消</button>`;
        panel.querySelector("[data-member-retry]").onclick = () => { close(); void openFamilyMemberPicker(context); };
        panel.querySelector("[data-member-cancel]").onclick = close;
        return;
      }
      const candidates = familyMemberCandidates(snapshot, family);
      const selected = new Set();
      panel.innerHTML = window.PortalI18n.html`<form data-member-form>
        <p class="paneNote">选择同一 Lineage 中已有的 Resident 加入当前 Family。</p>
        <fieldset><input type="search" data-member-search aria-label="搜索名称或 Resident ID" placeholder="搜索名称或 Resident ID" autocomplete="off" />
        <div class="residentMenuMemberOptions" data-member-options></div>
        <p class="paneNote" data-member-empty hidden></p>
        <p class="residentMenuMemberError" role="alert" data-member-error hidden></p>
        <div class="residentMenuMemberActions"><button class="portalButton is-compact" type="button" data-member-cancel>取消</button><button class="portalButton is-primary is-compact" type="submit" data-member-submit disabled>添加所选</button></div>
        </fieldset></form>`;
      const search = panel.querySelector("[data-member-search]");
      const submit = panel.querySelector("[data-member-submit]");
      const errorLine = panel.querySelector("[data-member-error]");
      const draw = () => {
        const query = search.value.trim().toLocaleLowerCase();
        const matches = candidates.filter(node => `${node.title || ""} ${nodeResidentId(node)}`.toLocaleLowerCase().includes(query));
        panel.querySelector("[data-member-options]").innerHTML = matches.map(node => {
          const id = nodeResidentId(node);
          return `<label class="residentMenuMemberOption"><input type="checkbox" value="${esc(id)}"${selected.has(id) ? " checked" : ""} /><span><strong>${esc(node.title || id)}</strong><code>${esc(id)}</code></span></label>`;
        }).join("");
        const empty = panel.querySelector("[data-member-empty]");
        empty.hidden = matches.length > 0;
        empty.textContent = window.PortalI18n.t(candidates.length ? "没有匹配的 Resident。" : "暂无可添加的成员。当前成员、已加入其他 Family 的 Resident 和内部角色不会列出。");
      };
      search.addEventListener("input", draw);
      panel.querySelector("[data-member-options]").addEventListener("change", event => {
        const checkbox = event.target.closest('input[type="checkbox"]');
        if (!checkbox) return;
        if (checkbox.checked) selected.add(checkbox.value); else selected.delete(checkbox.value);
        submit.disabled = !selected.size;
        submit.textContent = window.PortalI18n.text`添加所选（${selected.size}）`;
      });
      panel.querySelector("[data-member-cancel]").onclick = close;
      panel.querySelector("form").onsubmit = async event => {
        event.preventDefault();
        if (!current() || context.busy || !selected.size) return;
        context.busy = true;
        const actions = [...root.querySelectorAll("[data-family-action], [data-family-add]")];
        actions.forEach(action => { action.disabled = true; });
        panel.querySelector("fieldset").disabled = true;
        errorLine.hidden = true;
        let result;
        try {
          result = await window.PortalInstructionClient.execute("UpdateFamily", {
            lineage_id: context.lineageId, family_id: family.id, add_member_ids: [...selected],
          }, {expectedVersion: snapshot.revision});
        } catch (error) {
          if (current()) { errorLine.textContent = error.message; errorLine.hidden = false; }
        } finally {
          context.busy = false;
          actions.forEach(action => { action.disabled = false; });
          panel.querySelector("fieldset").disabled = false;
        }
        if (!result || !current()) return;
        context.family = result.function_block;
        lineage = {...lineage, function_blocks: (lineage.function_blocks || []).map(block => block.id === family.id ? result.function_block : block)};
        window.PortalResidentProjection.invalidate(context.residentId);
        renderFamilySettings(context);
        root.querySelector("[data-family-add]").focus();
        toast(window.PortalI18n.text`已添加 ${selected.size} 位成员。`);
        try {
          await changed({type: "family-members", residentId: context.residentId, lineageId: context.lineageId, result});
        } catch (error) {
          if (nodeMenuContext === context) nodeMenuNotice(window.PortalI18n.text`成员已添加，刷新失败：${error.message}`);
        }
      };
      draw(); search.focus();
    }

    async function changeFamily(action) {
      const context = nodeMenuContext;
      if (!context?.family || context.busy || context.saving) return;
      context.busy = true;
      const buttons = [...$id("nodeMenuFamilySettings").querySelectorAll("[data-family-action]")];
      buttons.forEach(button => {button.disabled = true;});
      try {
        const result = await removeFamily({family: context.family, lineageId: context.lineageId, action});
        if (!result) return;
        if (nodeMenuContext === context) dialog.close({force: true});
        await changed({type: action === "delete" ? "delete-family" : "unfamilize", residentId: context.residentId,
          lineageId: context.lineageId, result});
      } catch (error) { if (nodeMenuContext === context) nodeMenuNotice(error.message); }
      finally { context.busy = false; buttons.forEach(button => {button.disabled = false;}); }
    }

    // A Function Block's Menu: tabs switch between the block's own
    // panel and the Menus of the Residents inside it, in this one dialog.
    function groupTab(key = options.group?.active) {
      return (options.group?.tabs || []).find(tab => tab.key === key) || null;
    }

    function renderGroupTabs() {
      const strip = $id("nodeMenuGroupTabs");
      const tabs = options.group?.tabs || [];
      strip.hidden = tabs.length === 0;
      strip.innerHTML = tabs.map(tab => {
        const active = tab.key === options.group.active;
        return window.PortalI18n.html`<button class="residentMenuGroupTab${active ? " active" : ""}" type="button" role="tab" data-group-tab="${esc(tab.key)}" title="${esc(tab.label)}" aria-selected="${active}" aria-controls="${tab.residentId ? "nodeDisplayPanel" : "nodeMenuGroupPanel"}" tabindex="${active ? 0 : -1}">${esc(tab.label)}</button>`;
      }).join("");
    }

    function disposeGroupPanel() {
      const cleanup = groupPanelCleanup;
      groupPanelCleanup = null;
      cleanup?.();
      $id("nodeMenuGroupPanel").replaceChildren();
    }

    function setGroupPanelMode(panel) {
      $id("nodeMenuGroupPanel").hidden = !panel;
      modal.querySelector(".nodeSettingsResidentTabs").hidden = panel;
      if (panel) modal.querySelectorAll(".nodeSettingsPanels > section").forEach(section => { section.hidden = true; });
      modal.querySelector(".residentMenuIdentity").hidden = panel;
      $id("nodeMenuPin").hidden = panel;
      if (panel) {
        $id("nodeMenuStatus").hidden = true;
        $id("nodeTransferFields").hidden = true;
      }
    }

    async function switchGroupTab(key) {
      const tab = groupTab(key);
      if (!tab || key === options.group.active || !await leaveNodeMenu()) return;
      await open({...options, residentId: tab.residentId || "", node: tab.node || null, tab: "display",
        group: {...options.group, active: key}});
    }

    function disposeNodeMenu() {
      disposeFamilySettings();
      nodeMenuContext?.unobserveSession?.();
      nodeMenuContext?.unobservePin?.();
      nodeMenuContext?.avatarEditor?.destroy();
      nodeMenuContext?.runtimeController?.destroy();
      $id("nodeMenuAvatar").replaceChildren();
      nodeMenuContext = null;

    }

    function retireResidents(deleted) {
      if (deleted.has(nodeMenuContext?.residentId)) dialog.close({force: true});
    }

    async function leaveNodeMenu() {
      if (!await leaveNodeDetails()) return false;
      const context = nodeMenuContext;
      const node = nodeSettingsNode();
      if (node && !$id("nodeMenuRenameFields").hidden && $id("nodeSettingsName").value.trim() !== node.title) {
        const approved = await window.PortalUI.confirm({title: window.PortalI18n.t("放弃未保存的名称？"), message: window.PortalI18n.t("关闭 Menu 会丢失尚未保存的名称。"), confirmLabel: window.PortalI18n.t("放弃修改")});
        if (!approved || context !== nodeMenuContext) return false;
      }
      return !nodeMenuContext?.busy && !nodeMenuContext?.saving && !nodeMenuContext?.avatarEditor?.isBusy();
    }

    function nodeMenuNotice(message, retry = null) {
      $id("nodeMenuStatus").textContent = message;
      $id("nodeMenuStatus").hidden = !message;
      if (retry) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "portalButton is-compact";
        button.textContent = window.PortalI18n.t("重试");
        button.addEventListener("click", retry);
        $id("nodeMenuStatus").append(button);
      }
    }

    function renderNodeMenuPin(context) {
      const button = $id("nodeMenuPin");
      button.disabled = context.busy || !context.pinReady;
      button.setAttribute("aria-pressed", String(context.pinned));
      button.setAttribute("aria-label", context.pinned ? window.PortalI18n.t("取消置顶") : window.PortalI18n.t("置顶"));
    }

    async function loadNodeMenuPin(context) {
      if (nodeMenuContext !== context || context.pinLoading) return;
      context.pinLoading = true;
      const store = window.PortalConversationStore;
      const sync = () => {
        if (nodeMenuContext !== context) return;
        context.pinned = Boolean(store.entry(`resident:${context.residentId}`)?.pinned);
        renderNodeMenuPin(context);
      };
      renderNodeMenuPin(context);
      try {
        context.unobservePin ||= store.subscribe(sync);
        nodeMenuNotice("");
        const result = await store.loadPin(context.residentId);
        if (nodeMenuContext !== context) return;
        if (!result) throw new Error(window.PortalI18n.t("请重试"));
        context.pinReady = true;
        sync();
      } catch (error) {
        if (nodeMenuContext === context) nodeMenuNotice(window.PortalI18n.text`置顶状态读取失败：${error.message}`, () => loadNodeMenuPin(context));
      } finally {
        context.pinLoading = false;
      }
    }

    async function loadNodeMenuIdentity(context) {
      await Promise.allSettled([loadNodeMenuPin(context), mountNodeMenuAvatar(context)]);
    }

    // The portrait shows the saved avatar; 编辑头像 in Settings changes it through UpdateResident.
    async function mountNodeMenuAvatar(context) {
      await window.PortalUIPrimitives.loadAsset("/static/portal_avatar_editor.js", {ready: () => window.PortalAvatarEditor});
      if (nodeMenuContext !== context) return;
      context.avatarEditor = window.PortalAvatarEditor.mount($id("nodeMenuAvatar"), {
        portrait: $id("nodeMenuPortrait"), label: context.node.title || context.residentId,
        load: () => window.PortalAvatarEditor.read("resident", context.residentId, {fresh: true}),
        save: async avatar => (await window.PortalInstructionClient.execute("UpdateResident", {
          lineage_id: context.lineageId, resident_id: context.residentId, avatar,
        }, {idempotencyKey: `resident-avatar:${window.PortalUIPrimitives.randomUUID()}`})).resident.avatar,
        onSaved: avatar => window.PortalAvatarEditor.remember("resident", context.residentId, avatar),
      });
    }

    async function toggleNodeMenuPin() {
      const context = nodeMenuContext;
      if (!context || !context.pinReady || context.busy || context.saving || nodeTransferInFlight) return;
      context.busy = true; renderNodeMenuPin(context);
      try {
        const result = await window.PortalConversationStore.setPinned(`resident:${context.residentId}`, !context.pinned);
        if (nodeMenuContext !== context) return;
        context.pinned = Boolean(result?.item?.pinned);
        nodeMenuNotice("");
      } catch (error) { if (nodeMenuContext === context) nodeMenuNotice(window.PortalI18n.text`置顶状态更新失败：${error.message}`); }
      finally { if (nodeMenuContext === context) { context.busy = false; renderNodeMenuPin(context); } }
    }

    function nodeJournalSection(title, values, empty = "") {
      const items = (Array.isArray(values) ? values : [values]).filter(value => typeof value === "string" && value.trim());
      if (!items.length && !empty) return "";
      return window.PortalI18n.html`<section class="residentMenuJournalSection"><h4>${esc(title)}</h4>${items.length
        ? window.PortalI18n.html`<ul>${items.map(value => window.PortalI18n.html`<li>${esc(value)}</li>`).join("")}</ul>`
        : window.PortalI18n.html`<p class="residentMenuJournalMeta">${esc(empty)}</p>`}</section>`;
    }

    async function loadNodeJournal() {
      const context = nodeMenuContext;
      if (!context || context.journalBusy || context.journalLoaded) return;
      context.journalBusy = true;
      const button = $id("nodeJournalRetry"); button.disabled = true; button.hidden = true;
      $id("nodeJournalRows").textContent = window.PortalI18n.t("正在读取职责与活动摘要…");
      try {
        const result = await window.PortalInstructionClient.execute("GetResidentSummaries", {resident_ids: [context.residentId]});
        if (nodeMenuContext !== context) return;
        const entry = (result.summaries || []).find(item => item.resident_id === context.residentId);
        const summary = entry?.summary;
        const rows = $id("nodeJournalRows");
        if (!summary || typeof summary !== "object") rows.textContent = window.PortalI18n.t("暂无可读取的职责与活动摘要。");
        else {
          const updated = entry.summary_updated_at ? new Date(entry.summary_updated_at) : null;
          const timestamp = updated && !Number.isNaN(updated.getTime()) ? updated.toLocaleString(window.PortalI18n.locale(), {hour12: false}) : "";
          rows.innerHTML = window.PortalI18n.html`<p class="residentMenuJournalMeta">摘要服务维护${timestamp ? window.PortalI18n.text` · 更新于 ${esc(timestamp)}` : ""}${entry.summary_stale ? window.PortalI18n.t(" · 有新活动，摘要待更新") : ""}</p>`
            + nodeJournalSection("职责范围", summary.primary_ownership, "摘要尚未记录职责范围。")
            + nodeJournalSection("当前重点", summary.current_focus)
            + nodeJournalSection("近期成果", summary.recent_results, "暂无近期成果摘要。")
            + nodeJournalSection("进行中的工作", summary.active_threads)
            + nodeJournalSection("近期变化", summary.branch_delta);
        }
        context.journalLoaded = true;
      } catch (error) {
        if (nodeMenuContext !== context) return;
        $id("nodeJournalRows").textContent = window.PortalI18n.text`职责与活动摘要读取失败：${error.message}`;
        button.hidden = false;
      } finally { if (nodeMenuContext === context) { context.journalBusy = false; button.disabled = false; } }
    }

    function renderNodeMenuSession(context, {detail, error}) {
      if (nodeMenuContext !== context) return;
      if (!error) {
        const backend = String(detail?.resident?.backend || "").trim();
        if (backend && backend !== context.node.backend) {
          context.node = {...context.node, backend};
          void loadNodeMenuRuntime();
        }
        context.sessionId = String(detail?.provider_session?.id || "").trim();
        $id("nodeMenuSessionId").textContent = context.sessionId || window.PortalI18n.t("尚未创建");
        $id("nodeMenuCopySession").disabled = !context.sessionId;
      } else {
        context.sessionId = "";
        $id("nodeMenuSessionId").textContent = window.PortalI18n.text`读取失败：${error.message}`;
        $id("nodeMenuCopySession").disabled = true;
      }
    }

    async function loadNodeMenuSession({force = false} = {}) {
      const context = nodeMenuContext;
      if (!context) return;
      try {
        const detail = await window.PortalResidentProjection.load(context.residentId, {force});
        renderNodeMenuSession(context, {detail});
      } catch (error) {
        renderNodeMenuSession(context, {error});
      }
    }

    async function loadNodeMenuRuntime() {
      const context = nodeMenuContext;
      if (!context) return;
      if (context.family) { renderFamilySettings(context); return; }
      if (!context.runtimeController) {
        context.runtimeController = window.PortalResidentRuntimeController.bind(context.residentId, {
          onState: view => {
            if (nodeMenuContext !== context) return;
            context.runtimeBusy = view.loading;
            context.saving = view.busy;
            const rows = [["Backend", view.detail?.resident?.backend || context.node.backend || "—"],
              ...window.PortalChatWorkspace.residentRuntimeSettingRows(view.settings, view.catalog || {})];
            const disabled = view.loading || view.busy || ["accepted", "submitting", "confirming", "cancelling"].includes(view.restart?.status)
              || (!view.canEdit && !view.canRestart);
            const root = $id("nodeMenuRuntime");
            const focus = root.contains(document.activeElement) ? document.activeElement?.dataset.menuRuntime : null;
            root.innerHTML = rows.map(([label, value]) => window.PortalI18n.html`<button class="residentMenuSetting" type="button" data-menu-runtime="${label}" aria-haspopup="dialog" ${disabled ? "disabled" : ""}><span>${label}</span><strong>${esc(value)}</strong><span aria-hidden="true">›</span></button>`).join("");
            if (focus) root.querySelector(`[data-menu-runtime="${CSS.escape(focus)}"]`)?.focus();
          },
          onChanged: () => { if (nodeMenuContext === context) void loadNodeMenuSession({force: true}); },
        });
        $id("nodeMenuRuntime").onclick = async event => {
          const button = event.target.closest("[data-menu-runtime]");
          if (!button || button.disabled || nodeMenuContext !== context) return;
          const field = button.dataset.menuRuntime;
          try {
            await context.runtimeController.openSettings(() => $id("nodeMenuRuntime").querySelector(`[data-menu-runtime="${CSS.escape(field)}"]`));
          } catch (error) { if (nodeMenuContext === context) nodeMenuNotice(error.message); }
        };
      }
      try { await context.runtimeController.load(); }
      catch (error) { if (nodeMenuContext === context) $id("nodeMenuRuntime").textContent = window.PortalI18n.text`运行设置读取失败：${error.message}`; }
    }

    function renderNodeSettingsIdentity(node) {
      if (!node) return;
      $id("nodeLineageIdentityReadout").textContent = [
        lineage?.title,
        lineage?.lineage_id,
      ].filter(Boolean).join(" · ");
      $id("nodeResidentIdentityReadout").textContent = nodeResidentId(node);
    }

    async function submitNodeSettings(event) {
      event.preventDefault();
      if (nodeTransferInFlight || nodeMenuContext?.saving) return;
      const node = nodeSettingsNode();
      if (!node) return;
      const nextTitle = $id("nodeSettingsName").value.trim() || node.title;
      const titleChanged = nextTitle !== node.title;
      const context = nodeMenuContext;
      if (context) context.saving = true;
      try {
        if (titleChanged) {
          const response = await window.PortalResidentService.update({
            resident_id: nodeResidentId(node),
            title: nextTitle,
          });
          context.node = {...context.node, title: nextTitle};
          await changed({type: "rename", residentId: context.residentId, lineageId: context.lineageId, result: response});
        }
        if (nodeMenuContext !== context) return;
        $id("nodeMenuName").textContent = nextTitle;
        $id("nodeSettingsName").value = nextTitle;
        $id("nodeMenuRenameFields").hidden = true;
        nodeMenuNotice("");
        toast(titleChanged ? window.PortalI18n.t("Resident 名称已保存。") : window.PortalI18n.t("名称未改变。"));
      } catch (error) { if (nodeMenuContext === context) nodeMenuNotice(window.PortalI18n.text`名称保存失败：${error.message}`); }
      finally { if (context) context.saving = false; }
    }


    async function updateIntake(node) {
      const context = nodeMenuContext;
      if (!context || context.intakeBusy) return;
      context.intakeBusy = true;
      renderNodeSettingsIntake(node);
      try {
        const result = await window.PortalResidentService.setIntake({resident_id: context.residentId, open: nodeIntakeState(node) === "closed"});
        if (nodeMenuContext !== context) return;
        context.node = {...context.node, intake_state: result.current};
        await changed({type: "intake", residentId: context.residentId, lineageId: context.lineageId, result});
      } catch (error) { if (nodeMenuContext === context) nodeMenuNotice(error.message); }
      finally { if (nodeMenuContext === context) { context.intakeBusy = false; renderNodeSettingsIntake(context.node); } }
    }

    async function removeResident(node) {
      const context = nodeMenuContext;
      if (!context || context.busy || context.saving) return;
      if (options.onDelete) {
        context.busy = true;
        try { if (await options.onDelete(node) && nodeMenuContext === context) dialog.close({force: true}); }
        finally { context.busy = false; }
        return;
      }
      if (!await window.PortalUI.confirm({title: window.PortalI18n.t("删除 Resident？"), message: window.PortalI18n.text`删除「${node.title}」将移除 Session 和图引用；关联 Workspace 仅在没有其他存活 Resident 引用时回收，仍被引用的 Workspace 保留。对话与运行轨迹保留。`, confirmLabel: window.PortalI18n.t("删除"), tone: "danger"}) || nodeMenuContext !== context) return;
      context.busy = true;
      try {
        const result = await window.PortalResidentService.remove({resident_id: context.residentId});
        // Deletion is committed; a failed owner refresh cannot keep its controls active.
        if (nodeMenuContext === context) dialog.close({force: true});
        await changed({type: "delete", residentId: context.residentId, lineageId: context.lineageId, result});
      } catch (error) { if (nodeMenuContext === context) nodeMenuNotice(error.message); }
      finally { context.busy = false; }
    }

    const dialog = window.PortalUI.createDialogController({
      modal, openClass: "portalDialogOpen", backdropClose: true, escapeClose: true,
      onOpen: () => { modal.hidden = false; },
      beforeClose: () => {
        const context = nodeMenuContext;
        void leaveNodeMenu().then(allowed => { if (allowed && context === nodeMenuContext) dialog.close({force: true}); });
        return false;
      },
      onClose: () => { modal.hidden = true; disposeGroupPanel(); disposeNodeMailbox(); disposeNodeHarness(); disposeNodeDetails(); disposeNodeMenu(); nodeTransferContext = null; },
    });
    $id("nodeSettingsModal").querySelector("[data-portal-detail-close]").addEventListener("click", () => dialog.close());
    $id("nodeSettingsForm").addEventListener("submit", submitNodeSettings);
    $id("nodeMenuGroupTabs").addEventListener("click", event => {
      const button = event.target.closest("[data-group-tab]");
      if (button) void switchGroupTab(button.dataset.groupTab).catch(error => toast(error.message || String(error), "error"));
    });
    $id("nodeMenuGroupTabs").addEventListener("keydown", event => {
      const tabs = options.group?.tabs || [];
      const index = tabs.findIndex(tab => tab.key === event.target.closest("[data-group-tab]")?.dataset.groupTab);
      if (index < 0) return;
      const next = { ArrowDown: (index + 1) % tabs.length, ArrowUp: (index + tabs.length - 1) % tabs.length, Home: 0, End: tabs.length - 1 }[event.key];
      if (next === undefined) return;
      event.preventDefault();
      void switchGroupTab(tabs[next].key).then(() => $id("nodeMenuGroupTabs").querySelector("[aria-selected='true']")?.focus())
        .catch(error => toast(error.message || String(error), "error"));
    });
    $id("nodeDisplayTab").addEventListener("click", () => switchNodeSettingsTab("display"));
    $id("nodeHarnessTab").addEventListener("click", () => switchNodeSettingsTab("harness"));
    $id("nodeDetailsTab").addEventListener("click", () => switchNodeSettingsTab("details"));
    $id("nodeMailboxTab").addEventListener("click", () => switchNodeSettingsTab("mailbox"));
    $id("nodeDisplayTab").closest('[role="tablist"]').addEventListener("keydown", moveNodeSettingsTab);
    $id("transferNodeBtn").addEventListener("click", openNodeTransfer);
    $id("nodeTransferConfirmBtn").addEventListener("click", submitNodeTransfer);
    $id("nodeTransferTarget").addEventListener("change", () => {
      $id("nodeTransferConfirmBtn").disabled = !nodeTransferContext || nodeTransferContext.busy || !$id("nodeTransferTarget").value;
    });
    $id("nodeSettingsIntakeToggle").addEventListener("click", () => {
      const node = nodeSettingsNode();
      if (node) updateIntake(node);
    });
    $id("nodeJournalTab").addEventListener("click", () => switchNodeSettingsTab("journal"));
    $id("nodeJournalRetry").addEventListener("click", () => loadNodeJournal());
    $id("nodeMenuPin").addEventListener("click", toggleNodeMenuPin);
    $id("nodeMenuCopySession").addEventListener("click", () => {
      if (nodeMenuContext?.sessionId) {
        void window.PortalUI.copyText(nodeMenuContext.sessionId, window.PortalI18n.t("Session ID")).catch(error => nodeMenuNotice(error.message));
      }
    });
    $id("nodeMenuRename").addEventListener("click", () => {
      $id("nodeMenuRenameFields").hidden = false;
      $id("nodeSettingsName").focus();
    });
    $id("nodeMenuRenameCancel").addEventListener("click", () => {
      $id("nodeSettingsName").value = nodeSettingsNode()?.title || "";
      $id("nodeMenuRenameFields").hidden = true;
    });
    $id("removeNodeBtn").addEventListener("click", () => {
      const node = nodeSettingsNode();
      if (node) removeResident(node);
    });

    window.PortalUIPrimitives.mountIcons(modal);
    return {
      async open(next) {
        if (!await leaveNodeMenu()) return false;
        options = next;
        lineage = next.lineage;
        disposeGroupPanel();
        renderGroupTabs();
        const tab = groupTab();
        setGroupPanelMode(Boolean(tab && !tab.residentId));
        if (tab && !tab.residentId) {
          disposeNodeDetails();
          disposeNodeMailbox();
          disposeNodeHarness();
          disposeNodeMenu();
          $id("nodeSettingsTitle").textContent = window.PortalI18n.t("Menu");
          groupPanelCleanup = options.group.render?.(tab.key, $id("nodeMenuGroupPanel"), {
            close: () => dialog.close(),
            setLabel: label => {
              if (groupTab(tab.key) !== tab || tab.label === label) return;
              tab.label = label;
              renderGroupTabs();
            },
          }) || null;
          window.PortalUIPrimitives.mountIcons($id("nodeMenuGroupPanel"));
          dialog.open();
          // A reused dialog may still be scrolled from another block or tab.
          // Start at its identity before the user begins editing.
          $id("nodeMenuGroupPanel").closest(".nodeSettingsBody").scrollTop = 0;
          return true;
        }
        await openNodeSettingsModal(next.node, next.tab || "display");
        return true;
      },
      retireResidents,
      destroy() { dialog.close({force: true}); dialog.destroy(); surface?.destroy(); modal.remove(); },
    };
  }

  async function open(options = {}) {
    const sequence = ++openSequence;
    openingResidentId = String(options.residentId || options.principalId || nodeResidentId(options.node)).trim();
    const active = (options.group?.tabs || []).find(tab => tab.key === options.group.active);
    if (active && !active.residentId) {
      if (!options.lineage) throw new TypeError("lineage is required");
      instance ||= createMenu();
      await instance.open({...options, node: null});
      return instance;
    }
    const residentId = String(options.residentId || options.principalId || nodeResidentId(options.node));
    if (!residentId) throw new TypeError("residentId is required");
    const node = options.node || (await window.PortalInstructionClient.execute("GetResident", {resident_id: residentId})).resident;
    if (!node) throw new Error("Resident 不存在或不可访问");
    const lineageId = node.lineage_id || options.lineageId;
    const [lineage, runtime] = await Promise.all([
      options.lineage || window.PortalInstructionClient.execute("GetLineage", {lineage_id: lineageId}).then(result => result.lineage),
      options.node ? null : window.PortalInstructionClient.execute("ListResidentStates", {resident_ids: [residentId], include_viewer_state: false})
        .then(result => result.states?.[0] || null),
    ]);
    if (sequence !== openSequence) return null;
    openingResidentId = "";
    instance ||= createMenu();
    await instance.open({...options, node: {...node, id: residentId, backend: node.provider || node.backend, intake_state: runtime?.state?.intake_state || node.intake_state}, lineage});
    return instance;
  }
  window.PortalResidentMenu = Object.freeze({open, removeFamily, retireResidents, mountMigrationPriority});
})();
