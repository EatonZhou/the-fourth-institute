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

  // createMenu builds the Resident Menu's dialog. Its parts live in module
  // scripts that load before the Menu opens; menuPart(name) returns what a
  // script registers:
  //   portal_resident_menu_tabs.js  the Settings, Harness, Package, Mailbox
  //     and Work Journal tabs and switching between them
  //   portal_resident_menu_resident.js  one Resident's Menu: opening, leaving
  //     and disposing it, the status line, rename, intake and removal
  //   portal_resident_menu_transfer.js  moving the Resident to another Lineage
  //   portal_resident_menu_family.js  a Family facade's settings and members
  //   portal_resident_menu_group.js  a Function Block's Menu and its tabs
  //   portal_resident_menu_identity.js  the pin and the avatar editor
  //   portal_resident_menu_journal.js  the Work Journal tab
  //   portal_resident_menu_runtime.js  the Session ID and runtime settings
  //   portal_resident_menu_events.js  the listeners of the dialog's controls
  // createMenu builds the dialog and its controller, holds the state the parts
  // share, creates the parts in that order and returns the Menu; a part that
  // calls a function of a part created after it gets an arrow forwarding the
  // call.
  function menuPart(name) {
    const part = window[name];
    if (!part) throw new Error(`${name} is not loaded.`);
    return part;
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
    // The state the parts share: the Lineage and options the Menu was opened
    // with, the Move in progress, the open Resident's menu context and the
    // group panel's cleanup. Each part keeps the rest of its state to itself.
    const menuState = {
      lineage: null, options: {}, nodeTransferContext: null, nodeTransferInFlight: false, nodeMenuContext: null,
      groupPanelCleanup: null,
    };
    const surface = window.PortalDetailSurface.mount(modal.querySelector("[data-portal-detail-surface]"));
    const changed = async detail => {
      window.dispatchEvent(new CustomEvent("portal:resident-menu-changed", {detail}));
      await menuState.options.onChanged?.(detail);
    };
    // The dialog controller exists before the parts, which open and close it;
    // it calls them only when the Menu opens or closes.
    const dialog = window.PortalUI.createDialogController({
      modal, openClass: "portalDialogOpen", backdropClose: true, escapeClose: true,
      onOpen: () => { modal.hidden = false; },
      beforeClose: () => {
        const context = menuState.nodeMenuContext;
        void leaveNodeMenu().then(allowed => { if (allowed && context === menuState.nodeMenuContext) dialog.close({force: true}); });
        return false;
      },
      onClose: () => { modal.hidden = true; disposeGroupPanel(); disposeNodeMailbox(); disposeNodeHarness(); disposeNodeDetails(); disposeNodeMenu(); menuState.nodeTransferContext = null; },
    });

    const {
      disposeNodeDetails, disposeNodeHarness, disposeNodeMailbox, leaveNodeDetails, moveNodeSettingsTab,
      renderNodeSettingsTab, switchNodeSettingsTab,
    } = menuPart("PortalResidentMenuTabs").createTabs({
      $id, changed, disposeFamilySettings: (...args) => disposeFamilySettings(...args),
      loadNodeJournal: (...args) => loadNodeJournal(...args),
      loadNodeMenuRuntime: (...args) => loadNodeMenuRuntime(...args), menuState, nodeResidentId,
      nodeSettingsNode: (...args) => nodeSettingsNode(...args), toast,
    });

    const {
      disposeNodeMenu, leaveNodeMenu, nodeMenuNotice, nodeSettingsNode, openNodeSettingsModal, removeResident,
      retireResidents, submitNodeSettings, updateIntake,
    } = menuPart("PortalResidentMenuResident").createResident({
      $id, changed, dialog, disposeFamilySettings: (...args) => disposeFamilySettings(...args), disposeNodeDetails,
      disposeNodeHarness, disposeNodeMailbox, leaveNodeDetails,
      loadNodeMenuIdentity: (...args) => loadNodeMenuIdentity(...args),
      loadNodeMenuRuntime: (...args) => loadNodeMenuRuntime(...args), menuState, nodeIntakeState, nodeResidentId,
      renderNodeMenuSession: (...args) => renderNodeMenuSession(...args), renderNodeSettingsTab,
      switchNodeSettingsTab, toast, transferBlocker,
    });

    const { openNodeTransfer, submitNodeTransfer } = menuPart("PortalResidentMenuTransfer").createTransfer({
      $id, changed, dialog, memberFamily, menuState, nodeResidentId, nodeSettingsNode, societyService, toast,
      transferBlocker,
    });

    const { disposeFamilySettings, renderFamilySettings } = menuPart("PortalResidentMenuFamily").createFamily({
      $id, changed, dialog, esc, familyBlocks, familyMemberCandidates, menuState, mountMigrationPriority,
      nodeMenuNotice, nodeResidentId, removeFamily, toast,
    });

    const {
      disposeGroupPanel, groupTab, renderGroupTabs, setGroupPanelMode, switchGroupTab,
    } = menuPart("PortalResidentMenuGroup").createGroup({ $id, esc, leaveNodeMenu, menuState, modal, open });

    const { loadNodeMenuIdentity, toggleNodeMenuPin } = menuPart("PortalResidentMenuIdentity").createIdentity({
      $id, menuState, nodeMenuNotice,
    });

    const { loadNodeJournal } = menuPart("PortalResidentMenuJournal").createJournal({ $id, esc, menuState });

    const { loadNodeMenuRuntime, renderNodeMenuSession } = menuPart("PortalResidentMenuRuntime").createRuntime({
      $id, esc, menuState, nodeMenuNotice, renderFamilySettings,
    });

    menuPart("PortalResidentMenuEvents").createEvents({
      $id, dialog, loadNodeJournal, menuState, moveNodeSettingsTab, nodeMenuNotice, nodeSettingsNode,
      openNodeTransfer, removeResident, submitNodeSettings, submitNodeTransfer, switchGroupTab, switchNodeSettingsTab,
      toast, toggleNodeMenuPin, updateIntake,
    });

    window.PortalUIPrimitives.mountIcons(modal);
    return {
      async open(next) {
        if (!await leaveNodeMenu()) return false;
        menuState.options = next;
        menuState.lineage = next.lineage;
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
          menuState.groupPanelCleanup = menuState.options.group.render?.(tab.key, $id("nodeMenuGroupPanel"), {
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
