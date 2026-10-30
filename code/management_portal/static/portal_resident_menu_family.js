(() => {
  "use strict";

  if (window.PortalResidentMenuFamily) return;

  // A Family facade's settings: automatic migration and its account
  // priority, the members' status, adding members, and Unfamilize or
  // deleting the Family.

  const DEPENDENCIES = Object.freeze([
    "$id", "changed", "dialog", "esc", "familyBlocks", "familyMemberCandidates", "menuState",
    "mountMigrationPriority", "nodeMenuNotice", "nodeResidentId", "removeFamily", "toast",
  ]);

  function createFamily(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalResidentMenuFamily is missing: ${missing.join(", ")}`);
    const {
      $id, changed, dialog, esc, familyBlocks, familyMemberCandidates, menuState, mountMigrationPriority,
      nodeMenuNotice, nodeResidentId, removeFamily, toast,
    } = dependencies;

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
          if (menuState.nodeMenuContext !== context) return;
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
      if (menuState.nodeMenuContext !== context || context.busy || context.saving) return;
      const root = $id("nodeMenuFamilySettings");
      const button = root.querySelector("[data-family-auto-migrate]");
      const errorLine = root.querySelector("[data-family-settings-error]");
      const current = () => menuState.nodeMenuContext === context && root.contains(button);
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
        menuState.lineage = {...menuState.lineage, function_blocks: (menuState.lineage.function_blocks || []).map(block => block.id === family.id ? result.function_block : block)};
        window.PortalResidentProjection.invalidate(context.residentId);
        renderFamilySettings(context);
        root.querySelector("[data-family-auto-migrate]").focus();
        await changed({type: "family-auto-migrate", residentId: context.residentId, lineageId: context.lineageId, result});
      } catch (error) {
        if (menuState.nodeMenuContext === context) {
          const notice = root.querySelector("[data-family-settings-error]");
          notice.textContent = error.message; notice.hidden = false;
        }
      } finally {
        context.busy = false;
        if (current()) button.disabled = false;
      }
    }

    async function openFamilyMemberPicker(context) {
      if (menuState.nodeMenuContext !== context || context.busy || context.saving) return;
      const root = $id("nodeMenuFamilySettings");
      const button = root.querySelector("[data-family-add]");
      const panel = root.querySelector("#familyMemberPicker");
      if (!panel.hidden) { panel.querySelector("input")?.focus(); return; }
      const request = {};
      context.memberPickerRequest = request;
      panel.hidden = false;
      button.setAttribute("aria-expanded", "true");
      const close = () => { context.memberPickerRequest = null; panel.hidden = true; panel.replaceChildren(); button.setAttribute("aria-expanded", "false"); button.focus(); };
      const current = () => menuState.nodeMenuContext === context && context.memberPickerRequest === request && panel.isConnected && !panel.hidden;
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
        menuState.lineage = {...menuState.lineage, function_blocks: (menuState.lineage.function_blocks || []).map(block => block.id === family.id ? result.function_block : block)};
        window.PortalResidentProjection.invalidate(context.residentId);
        renderFamilySettings(context);
        root.querySelector("[data-family-add]").focus();
        toast(window.PortalI18n.text`已添加 ${selected.size} 位成员。`);
        try {
          await changed({type: "family-members", residentId: context.residentId, lineageId: context.lineageId, result});
        } catch (error) {
          if (menuState.nodeMenuContext === context) nodeMenuNotice(window.PortalI18n.text`成员已添加，刷新失败：${error.message}`);
        }
      };
      draw(); search.focus();
    }

    async function changeFamily(action) {
      const context = menuState.nodeMenuContext;
      if (!context?.family || context.busy || context.saving) return;
      context.busy = true;
      const buttons = [...$id("nodeMenuFamilySettings").querySelectorAll("[data-family-action]")];
      buttons.forEach(button => {button.disabled = true;});
      try {
        const result = await removeFamily({family: context.family, lineageId: context.lineageId, action});
        if (!result) return;
        if (menuState.nodeMenuContext === context) dialog.close({force: true});
        await changed({type: action === "delete" ? "delete-family" : "unfamilize", residentId: context.residentId,
          lineageId: context.lineageId, result});
      } catch (error) { if (menuState.nodeMenuContext === context) nodeMenuNotice(error.message); }
      finally { context.busy = false; buttons.forEach(button => {button.disabled = false;}); }
    }

    return {
      disposeFamilySettings, renderFamilySettings,
    };
  }

  window.PortalResidentMenuFamily = Object.freeze({ createFamily, dependencies: DEPENDENCIES });
})();
