(() => {
  "use strict";
  // Project and Lineage Menus, built on the Resident Menu's dialog:
  // Settings, Package (permission entries on this resource), the members one
  // step down with batch grants, and a Work Journal.
  const esc = value => window.PortalUIPrimitives.escapeHtml(value);
  const t = value => window.PortalI18n.t(value);
  const run = (name, args, key) => window.PortalInstructionClient.execute(name, args, key ? {idempotencyKey: `${key}:${window.PortalUIPrimitives.randomUUID()}`} : {});
  const toast = (message, kind = "success") => window.PortalUI.toast(message, kind, {duration: 3600});
  // A grant names a class node of the active scheme's A tree; the scheme's rules decide which exist.
  let classNodes = null;
  const aClassNodes = () => classNodes ||= run("GetPermissionScheme", {})
    .then(result => (result.rules?.trees?.A?.nodes || []).filter(node => String(node.id || "").startsWith("@")))
    .catch(error => { classNodes = null; throw error; });
  const fillANodes = async select => {
    const nodes = await aClassNodes().catch(() => []);
    select.insertAdjacentHTML("beforeend", nodes.map(node => `<option value="${esc(node.id)}">${esc(node.title ? `${node.title}（${node.id}）` : node.id)}</option>`).join(""));
  };
  const aSelect = `<select name="a" required><option value="">${esc(t("选择 A 节点"))}</option></select>`;
  const PROJECT_STATUSES = {not_started: "未开始", active: "进行中", paused: "已暂停", done: "已完成", archived: "已归档"};
  const PROJECT_TYPES = {company: "公司项目", academic: "学术项目", personal: "个人项目", other: "其他"};
  const selectOptions = labels => Object.entries(labels).map(([value, label]) => `<option value="${esc(value)}">${esc(t(label))}</option>`).join("");
  const SUMMARY_BATCH = 128;
  // Member rows show avatars for this many members; the rest keep their initials.
  const MEMBER_AVATARS = 120;
  const avatarModule = () => window.PortalUIPrimitives.loadAsset("/static/portal_avatar_editor.js", {ready: () => window.PortalAvatarEditor})
    .then(() => window.PortalAvatarEditor.loadAssets());
  let instance = null;

  function createMenu() {
    const host = document.createElement("div");
    host.innerHTML = window.PortalI18n.html`<div class="portalDetailOverlay" data-portal-detail-overlay id="scopeMenuModal" hidden aria-hidden="true">
      <section data-portal-detail-surface class="portalDetailSurface nodeSettingsSurface scopeMenuSurface" role="dialog" aria-modal="true" aria-labelledby="scopeMenuTitle">
        <div class="portalDetailHeader residentMenuHeader" data-portal-detail-header>
          <div class="residentMenuHeading"><h2 id="scopeMenuTitle">Menu</h2><div class="residentMenuHeaderActions"><button class="portalDetailClose" data-portal-detail-close type="button" aria-label="关闭" data-portal-icon="close"></button></div></div>
          <div class="residentMenuIdentity">
            <div class="residentMenuPortrait" id="scopeMenuPortrait"></div>
            <div class="residentMenuIdentityCopy">
              <div class="residentMenuName"><strong id="scopeMenuName"></strong></div>
              <div class="residentMenuIdRow"><code id="scopeMenuId"></code><span id="scopeMenuKind"></span></div>
              <div class="residentMenuLineage" id="scopeMenuParent"></div>
            </div>
          </div>
        </div>
        <div class="portalDetailBody nodeSettingsBody" data-portal-detail-body>
          <div class="nodeSettingsTabs" role="tablist" aria-label="Menu 分栏" aria-orientation="vertical">
            <button class="nodeSettingsTab active" id="scopeSettingsTab" data-scope-tab="settings" type="button" role="tab" aria-selected="true" aria-controls="scopeSettingsPanel">Settings</button>
            <button class="nodeSettingsTab" id="scopePackageTab" data-scope-tab="package" type="button" role="tab" aria-selected="false" aria-controls="scopePackagePanel" tabindex="-1">Package</button>
            <button class="nodeSettingsTab" id="scopeMembersTab" data-scope-tab="members" type="button" role="tab" aria-selected="false" aria-controls="scopeMembersPanel" tabindex="-1">Members</button>
            <button class="nodeSettingsTab" id="scopeJournalTab" data-scope-tab="journal" type="button" role="tab" aria-selected="false" aria-controls="scopeJournalPanel" tabindex="-1">Work Journal</button>
          </div>
          <div class="nodeSettingsPanels">
            <section id="scopeSettingsPanel" role="tabpanel" aria-labelledby="scopeSettingsTab"></section>
            <section id="scopePackagePanel" role="tabpanel" aria-labelledby="scopePackageTab" hidden></section>
            <section id="scopeMembersPanel" role="tabpanel" aria-labelledby="scopeMembersTab" hidden></section>
            <section id="scopeJournalPanel" role="tabpanel" aria-labelledby="scopeJournalTab" hidden></section>
          </div>
        </div>
      </section>
    </div>`;
    const modal = host.firstElementChild;
    document.body.append(modal);
    const $id = id => modal.querySelector(`#${CSS.escape(id)}`);
    const surface = window.PortalDetailSurface.mount(modal.querySelector("[data-portal-detail-surface]"));
    const TABS = ["settings", "package", "members", "journal"];
    // One context per opened scope; async work checks it is still current.
    let context = null;
    let options = {};

    const panel = tab => $id(`scope${tab[0].toUpperCase()}${tab.slice(1)}Panel`);
    const tabButton = tab => modal.querySelector(`[data-scope-tab="${tab}"]`);
    const current = () => TABS.find(tab => tabButton(tab).getAttribute("aria-selected") === "true") || "settings";
    const busy = () => Boolean(context?.busy);
    const status = (node, message, tone = "") => { node.textContent = message; node.dataset.tone = tone; };
    const changed = async detail => {
      window.dispatchEvent(new CustomEvent("portal:scope-menu-changed", {detail}));
      await options.onChanged?.(detail);
    };

    async function switchTab(tab) {
      if (busy()) { toast(t("权限操作正在处理，请稍后再试。"), "info"); return; }
      if (context?.avatarEditor?.isBusy()) { toast(t("头像正在保存，请稍后再试。"), "info"); return; }
      // Leaving Settings drops an unsaved avatar choice, and the portrait shows the saved one again.
      context?.avatarEditor?.close();
      for (const name of TABS) {
        const selected = name === tab;
        panel(name).hidden = !selected;
        tabButton(name).setAttribute("aria-selected", String(selected));
        tabButton(name).tabIndex = selected ? 0 : -1;
        tabButton(name).classList.toggle("active", selected);
      }
      const body = panel(tab).closest(".nodeSettingsBody");
      if (body) body.scrollTop = 0;
      const owner = context;
      if (!owner) return;
      try {
        await {settings: renderSettings, package: renderPackage, members: renderMembers, journal: renderJournal}[tab](owner);
      } catch (error) {
        if (tab === "journal") owner.journal = null;
        if (owner === context) panel(tab).textContent = window.PortalI18n.text`读取失败：${error.message || error}`;
      }
    }

    // Settings: the avatar row, the registry fields of a Project or the metadata
    // of a Lineage, then a Lineage's Family and advanced rows.
    async function renderSettings(owner) {
      const node = panel("settings");
      owner.avatarEditor?.destroy();
      owner.avatarEditor = null;
      const save = window.PortalI18n.html`<div class="scopeMenuSave"><p class="scopeMenuStatus" role="status"></p><button class="portalButton is-compact is-primary" type="submit">保存设置</button></div>`;
      if (owner.kind === "project") {
        const project = owner.record;
        node.innerHTML = window.PortalI18n.html`<section data-scope-avatar aria-label="Project avatar"></section>
          <form class="menuRow scopeMenuDetails" id="scopeSettingsForm">
            <div class="scopeMenuFields is-three">
              <label class="portalField"><span>状态</span><select name="status">${selectOptions(PROJECT_STATUSES)}</select></label>
              <label class="portalField"><span>类型</span><select name="project_type">${selectOptions(PROJECT_TYPES)}</select></label>
              <label class="portalField"><span>截止时间</span><input name="ddl" placeholder="2026-01-31T18:00:00" autocomplete="off" /></label>
            </div>
            ${save}
          </form>`;
        const form = node.querySelector("form");
        form.status.value = project.status || "active";
        form.project_type.value = project.project_type || "other";
        form.ddl.value = project.ddl || "";
        form.addEventListener("submit", event => saveSettings(event, owner, () => ({
          status: form.status.value, project_type: form.project_type.value, ddl: form.ddl.value.trim(),
        })));
        lockSettings(owner, form);
        mountAvatar(owner, node.querySelector("[data-scope-avatar]"));
        return;
      }
      const lineage = owner.record;
      // Sidebar metadata is compact; only read Family boundaries when it has no graph.
      if (!Array.isArray(lineage.function_blocks)) {
        const result = await run("GetLineage", {lineage_id: owner.id});
        if (owner !== context) return;
        lineage.function_blocks = result.lineage?.function_blocks || [];
      }
      const family = (lineage.function_blocks || []).find(block => block.family?.lineage_scope);
      node.innerHTML = window.PortalI18n.html`<section data-scope-avatar aria-label="Lineage avatar"></section>
        <form class="menuRow scopeMenuDetails" id="scopeSettingsForm">
          <div class="scopeMenuFields">
            <label class="portalField"><span>名称</span><input name="title" maxlength="120" autocomplete="off" /></label>
            <label class="portalField"><span>Leader</span><select name="leader_resident_id"><option value="">未指定</option>${(lineage.nodes || []).map(item => `<option value="${esc(item.id)}">${esc(item.title || item.id)}</option>`).join("")}</select></label>
            <label class="portalField is-wide"><span>说明</span><textarea name="description" rows="3"></textarea></label>
          </div>
          ${save}
        </form>
        <section class="menuRow" aria-label="Family">
          <div class="menuRowCopy"><strong>Family</strong><small>${family ? "解除整条 Lineage 的 Family，保留业务成员和子 Family。" : "将整条 Lineage 组成 Family；已有 Family 保留为子 Family，之后新建的成员自动加入。"}</small></div>
          <button class="portalButton is-compact" type="button" data-scope-family>${family ? "Unfamilize Lineage" : "Familize Lineage"}</button>
          <p class="scopeMenuStatus" role="status" data-scope-family-status></p>
        </section>
        ${options.onOpenAdvanced ? window.PortalI18n.html`<section class="menuRow" aria-label="所属项目、导航与删除">
          <div class="menuRowCopy"><strong>所属项目、导航与删除</strong><small>在 Lineage 设置中修改所属项目、导航位置，或删除这条 Lineage。</small></div>
          <button class="portalButton is-compact" type="button" data-scope-advanced>打开</button>
        </section>` : ""}`;
      const form = node.querySelector("form");
      form.title.value = lineage.title || "";
      form.description.value = lineage.description || "";
      form.leader_resident_id.value = lineage.leader_resident_id || "";
      node.querySelector("[data-scope-family]").addEventListener("click", () => void toggleLineageFamily(owner, family));
      node.querySelector("[data-scope-advanced]")?.addEventListener("click", () => { dialog.close(); options.onOpenAdvanced(); });
      form.addEventListener("submit", event => saveSettings(event, owner, () => ({
        title: form.title.value.trim() || lineage.title, description: form.description.value.trim(),
        leader_resident_id: form.leader_resident_id.value,
      })));
      lockSettings(owner, form);
      mountAvatar(owner, node.querySelector("[data-scope-avatar]"));
    }

    // 保存设置 runs UpdateProject / UpdateLineage; the listed record's allows says whether the caller may.
    function lockSettings(owner, form) {
      const instruction = owner.kind === "project" ? "UpdateProject" : "UpdateLineage";
      if (owner.record?.allows?.[instruction] !== false) return;
      form.querySelectorAll("input, select, textarea, button").forEach(control => {
        control.disabled = true;
        control.dataset.scopeLocked = "";
      });
      status(form.querySelector(".scopeMenuStatus"), owner.kind === "project" ? t("你不能修改这个 Project 的设置。") : t("你不能修改这条 Lineage 的设置。"));
    }

    // 编辑头像 saves through UpdateProject / UpdateLineage changes.avatar; 恢复默认 goes back to the initial.
    function mountAvatar(owner, row) {
      void avatarModule().then(() => {
        if (owner !== context || !row.isConnected) return;
        owner.avatarEditor = window.PortalAvatarEditor.mount(row, {
          portrait: $id("scopeMenuPortrait"), label: identityTitle(owner), clearable: true,
          load: () => owner.avatar,
          save: async avatar => {
            if (owner.kind === "project") {
              return (await run("UpdateProject", {project_id: owner.id, changes: {avatar}}, "project-menu-avatar")).project?.avatar || null;
            }
            return (await run("UpdateLineage", {lineage_id: owner.id, changes: {avatar}}, "lineage-menu-avatar")).lineage?.avatar || null;
          },
          onSaved: avatar => {
            owner.avatar = Promise.resolve(avatar);
            window.PortalAvatarEditor.remember(owner.kind, owner.id, avatar);
            void changed({type: "avatar", kind: owner.kind, id: owner.id, avatar});
          },
        });
      }).catch(() => {});
    }

    async function toggleLineageFamily(owner, family) {
      if (busy()) return;
      const node = panel("settings");
      const line = node.querySelector("[data-scope-family-status]");
      const buttons = [...node.querySelectorAll("form button, [data-scope-family], [data-scope-advanced]")];
      owner.busy = true;
      buttons.forEach(button => { button.disabled = true; });
      status(line, t(family ? "正在 Unfamilize…" : "正在 Familize…"));
      try {
        if (family) {
          await run("Unfamilize", {lineage_id: owner.id, family_id: family.id}, "lineage-unfamilize");
        } else {
          await run("Familize", {lineage_id: owner.id, scope: "lineage", goal: owner.record.description || ""}, "lineage-familize");
        }
        const lineage = await load("lineage", owner.id);
        if (owner !== context) return;
        owner.record = lineage;
        owner.members = null;
        owner.journal = null;
        renderIdentity(owner);
        await renderSettings(owner);
        status(node.querySelector("[data-scope-family-status]"), t(family ? "已 Unfamilize。" : "已 Familize。"), "success");
        await changed({type: "family", kind: "lineage", id: owner.id, lineage});
      } catch (error) {
        if (owner === context) status(line, error.message || String(error), "error");
      } finally {
        owner.busy = false;
        buttons.forEach(button => { if (button.isConnected && !("scopeLocked" in button.dataset)) button.disabled = false; });
      }
    }

    async function saveSettings(event, owner, read) {
      event.preventDefault();
      const form = event.currentTarget;
      const button = form.querySelector('[type="submit"]');
      const line = form.querySelector(".scopeMenuStatus");
      const before = owner.record;
      const changes = Object.fromEntries(Object.entries(read()).filter(([key, value]) => String(before[key] ?? "") !== value));
      if (!Object.keys(changes).length) { status(line, t("没有需要保存的修改。")); return; }
      button.disabled = true;
      status(line, t("保存中…"));
      try {
        if (owner.kind === "project") {
          await run("UpdateProject", {project_id: owner.id, changes}, "project-menu-update");
        } else {
          await run("UpdateLineage", {lineage_id: owner.id, changes}, "lineage-menu-update");
        }
        if (owner !== context) return;
        owner.record = {...before, ...changes};
        renderIdentity(owner);
        status(line, t("已保存。"), "success");
        await changed({type: "settings", kind: owner.kind, id: owner.id, changes});
      } catch (error) {
        if (owner === context) status(line, window.PortalI18n.text`保存失败：${error.message || error}`, "error");
      } finally { button.disabled = false; }
    }

    // Package: the active scheme's instance entries on this resource and its ancestors; rows are added and deleted here.
    async function renderPackage(owner) {
      const node = panel("package");
      node.textContent = t("正在读取权限…");
      const listed = await run("ListPermissionEntries", {r: owner.id, r_scope: "ancestors", limit: 500});
      if (owner !== context) return;
      const entries = listed.entries || [];
      // A list read with r says whether the caller may write entries on r; built-in schemes always answer false.
      const mayGrant = listed.allows?.CreatePermissionEntry === true;
      // Rows on ancestors are judged on their own r: each row says whether the caller may delete it
      // (DeletePermissionEntry is free at entry, so the list's own allows says yes to everyone).
      const mayDelete = entry => entry.allows?.DeletePermissionEntry === true;
      const temporary = entry => Boolean(entry.expires_at || entry.issued_by || entry.message_id);
      const aNode = value => `<span class="scopeMenuLevel">${esc(value)}</span>`;
      const rowState = entry => temporary(entry) ? t("临时凭证") : entry.disabled ? t("已停用") : "";
      const row = entry => `<tr><td><code>${esc(entry.p)}</code></td><td>${aNode(entry.a)}${entry.effect === "deny" ? " · deny" : ""}</td><td><code>${esc(entry.r)}</code></td><td>${esc(rowState(entry))}</td><td>${temporary(entry) || !mayDelete(entry) ? "" : `<button class="portalButton is-compact is-quiet" type="button" data-delete-entry="${esc(entry.entry_id)}" data-principal="${esc(entry.p)}">${esc(t("删除"))}</button>`}</td></tr>`;
      const more = (listed.total ?? entries.length) > entries.length ? `<p class="scopeMenuNote">${esc(window.PortalI18n.text`只列出前 ${entries.length} 条，共 ${listed.total} 条；完整的表在 Portal 管理的“权限方案 · 实例表”中。`)}</p>` : "";
      node.innerHTML = window.PortalI18n.html`<div class="scopeMenuPackage">
        <section class="scopeMenuSection"><h4>本资源及其上级的权限表项</h4>${entries.length ? `<table class="scopeMenuTable"><thead><tr><th>${esc(t("主体节点"))}</th><th>${esc(t("A 节点"))}</th><th>${esc(t("资源节点"))}</th><th>${esc(t("状态"))}</th><th></th></tr></thead><tbody>${entries.map(row).join("")}</tbody></table>${more}` : `<p class="scopeMenuEmpty">${esc(t("本资源上没有权限表项。"))}</p>`}</section>
        <section class="scopeMenuSection"><h4>写入表项</h4><form class="scopeMenuGrantForm" data-grant-form>
          <label class="portalField"><span>主体</span><input name="principal" list="scopeMenuPrincipals" placeholder="User 或 resident_…" autocomplete="off" required /></label>
          <label class="portalField"><span>A 节点</span>${aSelect}</label>
          <button class="portalButton is-compact is-primary" type="submit" ${mayGrant ? "" : "disabled"}>授予</button>
        </form><p class="scopeMenuStatus" role="status" data-grant-status>${mayGrant ? "" : esc(t("启用中的方案不允许你在这里写入表项。"))}</p></section>
        <p class="scopeMenuNote">${esc(owner.kind === "project" ? t("对下属 Lineage 或 Resident 批量授予，请到 Lineages 页。") : t("对本 Lineage 的 Resident 批量授予，请到 Residents 页。"))}</p>
      </div><datalist id="scopeMenuPrincipals"></datalist>`;
      void fillPrincipals(owner, node.querySelector("datalist"));
      void fillANodes(node.querySelector("[data-grant-form] select[name=a]"));
      const line = node.querySelector("[data-grant-status]");
      node.querySelector("[data-grant-form]").addEventListener("submit", async event => {
        event.preventDefault();
        const form = event.currentTarget;
        const principal = form.principal.value.trim();
        if (!principal) return;
        await guarded(owner, line, async () => {
          await run("CreatePermissionEntry", {p: principal, a: form.a.value, r: owner.id}, "scope-menu-grant");
          toast(window.PortalI18n.text`已授予 ${principal} ${form.a.value}`);
        });
        if (owner === context) await renderPackage(owner);
      });
      node.querySelectorAll("[data-delete-entry]").forEach(button => button.addEventListener("click", async () => {
        const confirmed = await window.PortalUI.confirm({
          title: t("删除这条表项？"), message: window.PortalI18n.text`删除 ${button.dataset.principal} 的这条表项。`,
          confirmLabel: t("删除"), tone: "danger",
        });
        if (!confirmed) return;
        await guarded(owner, line, () => run("DeletePermissionEntry", {entry_id: button.dataset.deleteEntry}, "scope-menu-delete"));
        if (owner === context) await renderPackage(owner);
      }));
    }

    async function fillPrincipals(owner, list) {
      try {
        const users = (await run("ListUsers", {})).users || [];
        const residents = owner.kind === "lineage" ? (owner.record.nodes || []).map(item => [item.id, item.title]) : [];
        if (owner !== context || !list.isConnected) return;
        list.innerHTML = [...users.map(user => [user.username || user.user_id || user.id, user.display_name || ""]), ...residents]
          .filter(([id]) => id).map(([id, label]) => `<option value="${esc(id)}">${esc(label || "")}</option>`).join("");
      } catch { /* Suggestions only. */ }
    }

    async function guarded(owner, line, work) {
      owner.busy = true;
      status(line, t("处理中…"));
      try {
        await work();
        if (owner === context) status(line, "");
        return true;
      } catch (error) {
        if (owner === context) status(line, error.message || String(error), "error");
        return false;
      } finally { owner.busy = false; }
    }

    // Members: the Lineages of a Project or the Residents of a Lineage, with
    // batch issuance either ON the selected members or TO the Residents in them.
    async function loadMembers(owner) {
      if (owner.members) return owner.members;
      if (owner.kind === "lineage") {
        owner.members = (owner.record.nodes || []).map(item => ({id: item.id, title: item.title || item.resident_title || item.id, meta: [item.backend, item.status].filter(Boolean).join(" · ")}));
      } else {
        const result = await run("SearchResources", {resource_type: "lineage", project_id: owner.id, limit: 500});
        owner.members = (result.items || []).map(item => ({id: item.resource_id, title: item.title || item.resource_id, meta: item.summary || ""}));
      }
      return owner.members;
    }

    async function residentsIn(owner, ids) {
      if (owner.kind === "lineage") return ids;
      const lineages = await Promise.all(ids.map(id => run("GetLineage", {lineage_id: id}).then(result => result.lineage)));
      return [...new Set(lineages.flatMap(lineage => (lineage?.nodes || []).map(item => item.id)))];
    }

    async function renderMembers(owner) {
      const node = panel("members");
      node.textContent = t("正在读取成员…");
      const members = await loadMembers(owner);
      if (owner !== context) return;
      const noun = owner.kind === "project" ? "Lineage" : "Resident";
      const initial = title => esc(Array.from(String(title || "?"))[0]?.toUpperCase() || "?");
      node.innerHTML = window.PortalI18n.html`<div class="scopeMenuMembers">
        <div class="scopeMenuMembersHead"><label class="scopeMenuCheck"><input type="checkbox" data-select-all /><span>全选</span></label><span class="scopeMenuNote">${esc(window.PortalI18n.text`共 ${members.length} 个 ${noun}`)}</span></div>
        <ul class="scopeMenuMemberList">${members.map(item => `<li><label><input type="checkbox" value="${esc(item.id)}" data-member /><span class="scopeMenuMemberAvatar is-initial" data-member-avatar="${esc(item.id)}" aria-hidden="true">${initial(item.title)}</span><span class="scopeMenuMemberCopy"><strong>${esc(item.title)}</strong><small><code>${esc(item.id)}</code>${item.meta ? ` · ${esc(item.meta)}` : ""}</small></span></label><button class="portalButton is-compact is-quiet" type="button" data-open-member="${esc(item.id)}" aria-label="${esc(window.PortalI18n.text`打开 ${item.title} 的 Menu`)}">Menu ›</button></li>`).join("") || `<li class="scopeMenuEmpty">${esc(t("暂无成员。"))}</li>`}</ul>
        <section class="scopeMenuSection scopeMenuBatch"><h4>批量授予</h4><form data-batch-form>
          <label class="portalField is-wide"><span>方向</span><select name="mode">
            <option value="resource">${esc(window.PortalI18n.text`给一个主体授予对所选 ${noun} 的 A 节点`)}</option>
            <option value="holder">${esc(owner.kind === "project" ? t("给所选 Lineage 中的全部 Resident 授予对一个资源的 A 节点") : t("给所选 Resident 授予对一个资源的 A 节点"))}</option>
          </select></label>
          <label class="portalField"><span data-target-label>主体</span><input name="target" list="scopeMenuPrincipals" autocomplete="off" required /></label>
          <label class="portalField"><span>A 节点</span>${aSelect}</label>
          <div class="scopeMenuSave"><p class="scopeMenuStatus" role="status"></p><button class="portalButton is-compact is-danger" type="button" data-batch="delete">批量删除同一 A 节点的表项</button><button class="portalButton is-compact is-primary" type="submit" data-batch="grant">批量授予</button></div>
        </form></section>
      </div><datalist id="scopeMenuPrincipals"></datalist>`;
      node.querySelector(".scopeMenuBatch").hidden = owner.permissionsOff;
      void fillPrincipals(owner, node.querySelector("datalist"));
      void fillANodes(node.querySelector("[data-batch-form] select[name=a]"));
      void paintMemberAvatars(owner, node);
      const form = node.querySelector("[data-batch-form]");
      const boxes = [...node.querySelectorAll("[data-member]")];
      node.querySelector("[data-select-all]").addEventListener("change", event => boxes.forEach(box => { box.checked = event.target.checked; }));
      form.mode.addEventListener("change", () => {
        node.querySelector("[data-target-label]").textContent = form.mode.value === "resource" ? t("主体") : t("资源 ID");
        form.target.setAttribute("list", form.mode.value === "resource" ? "scopeMenuPrincipals" : "");
      });
      node.querySelectorAll("[data-open-member]").forEach(button => button.addEventListener("click", () => {
        const id = button.dataset.openMember;
        if (owner.kind === "lineage") void window.PortalNav?.openResidentMenu?.({residentId: id, lineageId: owner.id});
        else void open({kind: "lineage", id, onChanged: options.onChanged});
      }));
      const submit = action => batch(owner, form, boxes.filter(box => box.checked).map(box => box.value), action);
      form.addEventListener("submit", event => { event.preventDefault(); void submit("grant"); });
      form.querySelector('[data-batch="delete"]').addEventListener("click", () => void submit("delete"));
    }

    // A Lineage's Residents and a Project's Lineages show their avatars; a member without one keeps its initial.
    async function paintMemberAvatars(owner, node) {
      try { await avatarModule(); } catch { return; }
      const kind = owner.kind === "project" ? "lineage" : "resident";
      const slots = [...node.querySelectorAll("[data-member-avatar]")].slice(0, MEMBER_AVATARS);
      await Promise.all(slots.map(async slot => {
        const avatar = await window.PortalAvatarEditor.read(kind, slot.dataset.memberAvatar).catch(() => null);
        if (owner !== context || !slot.isConnected || !avatar) return;
        window.PortalAvatarEditor.paint(slot, avatar, {label: slot.closest("li")?.querySelector("strong")?.textContent || "", size: 28});
      }));
    }

    async function batch(owner, form, selected, action) {
      const line = form.querySelector(".scopeMenuStatus");
      const target = form.target.value.trim();
      const a = form.a.value;
      if (!selected.length) { status(line, t("请先选择成员。"), "error"); return; }
      if (!target) { form.target.focus(); return; }
      if (!a) { form.a.focus(); return; }
      if (action === "delete" && !await window.PortalUI.confirm({
        title: t("批量删除表项？"), message: window.PortalI18n.text`删除所选 ${selected.length} 项上 A 节点为 ${a} 的表项（P、A、R 与本次批量完全一致的行）。`,
        confirmLabel: t("删除"), tone: "danger",
      })) return;
      const buttons = [...form.querySelectorAll("button")];
      buttons.forEach(button => { button.disabled = true; });
      let done = 0;
      const failures = [];
      await guarded(owner, line, async () => {
        // Pairs of (holder, resource) this batch touches.
        const pairs = form.mode.value === "resource"
          ? selected.map(id => [target, id])
          : (await residentsIn(owner, selected)).map(id => [id, target]);
        for (const [principal, resource] of pairs) {
          if (owner !== context) return;
          status(line, window.PortalI18n.text`处理中 ${done + failures.length + 1}/${pairs.length}…`);
          try {
            if (action === "grant") {
              await run("CreatePermissionEntry", {p: principal, a, r: resource}, "scope-menu-batch-grant");
            } else {
              const held = (await run("ListPermissionEntries", {p: principal, a, r: resource, temporary: false, limit: 500})).entries || [];
              const ids = held.map(entry => entry.entry_id);
              if (ids.length) await run("DeletePermissionEntry", {entry_ids: ids}, "scope-menu-batch-delete");
            }
            done += 1;
          } catch (error) {
            failures.push(`${principal} → ${resource}：${error.message || error}`);
          }
        }
      });
      buttons.forEach(button => { button.disabled = false; });
      if (owner !== context) return;
      status(line, failures.length
        ? window.PortalI18n.text`完成 ${done} 项，失败 ${failures.length} 项：${failures.slice(0, 3).join("；")}`
        : window.PortalI18n.text`已完成 ${done} 项。`, failures.length ? "error" : "success");
      await changed({type: "permissions", kind: owner.kind, id: owner.id});
    }

    // Work Journal: a Lineage shows its Residents' summaries; a Project shows
    // its summary and what each of its Lineages is for.
    async function renderJournal(owner) {
      const node = panel("journal");
      node.textContent = t("正在读取工作摘要…");
      if (owner.kind === "project") {
        const [project, members] = await (owner.journal ||= Promise.all([
          run("GetProject", {project_id: owner.id}), loadMembers(owner),
        ]));
        if (owner !== context) return;
        const text = String(project.summary?.text || "").trim();
        node.innerHTML = window.PortalI18n.html`<section class="residentMenuJournalSection"><h4>Project 摘要</h4>${text ? `<div class="scopeMenuSummary">${esc(text)}</div>` : `<p class="residentMenuJournalMeta">${esc(t("暂无 summary.md。"))}</p>`}</section>
          <section class="residentMenuJournalSection"><h4>Lineages</h4><ul>${members.map(item => `<li><strong>${esc(item.title)}</strong>${item.meta ? ` — ${esc(item.meta)}` : ""}</li>`).join("") || `<li>${esc(t("暂无成员。"))}</li>`}</ul></section>`;
        return;
      }
      const ids = (owner.record.nodes || []).map(item => item.id);
      const summaries = await (owner.journal ||= (async () => {
        const rows = [];
        for (let index = 0; index < ids.length; index += SUMMARY_BATCH) {
          const result = await run("GetResidentSummaries", {resident_ids: ids.slice(index, index + SUMMARY_BATCH), fields: ["current_focus", "recent_results"]});
          rows.push(...(result.summaries || []));
        }
        return rows;
      })());
      if (owner !== context) return;
      const list = values => (Array.isArray(values) ? values : [values]).filter(value => typeof value === "string" && value.trim()).slice(0, 3);
      node.innerHTML = summaries.map(entry => {
        const focus = list(entry.summary?.current_focus);
        const results = list(entry.summary?.recent_results);
        return `<section class="residentMenuJournalSection"><h4>${esc(entry.title || entry.resident_id)}</h4>${
          focus.length || results.length
            ? `<ul>${[...focus, ...results].map(value => `<li>${esc(value)}</li>`).join("")}</ul>`
            : `<p class="residentMenuJournalMeta">${esc(t("暂无可读取的职责与活动摘要。"))}</p>`}</section>`;
      }).join("") || esc(t("暂无成员。"));
    }

    const identityTitle = owner => String(owner.record.title || owner.record.name || owner.id);

    function renderIdentity(owner) {
      const record = owner.record;
      const title = identityTitle(owner);
      $id("scopeMenuName").textContent = title;
      $id("scopeMenuId").textContent = owner.id;
      $id("scopeMenuKind").textContent = owner.kind === "project" ? "Project" : "Lineage";
      $id("scopeMenuParent").textContent = owner.kind === "lineage" && record.project_id ? window.PortalI18n.text`所属项目 ${record.project_id}` : "";
      $id("scopeMenuParent").hidden = !$id("scopeMenuParent").textContent;
      tabButton("members").textContent = owner.kind === "project" ? "Lineages" : "Residents";
      // The saved avatar, or the title's initial; an unsaved choice in the editor keeps its preview.
      const portrait = $id("scopeMenuPortrait");
      if (!portrait.childNodes.length) {
        portrait.textContent = Array.from(title)[0]?.toUpperCase() || "?";
        portrait.classList.add("is-initial");
      }
      void avatarModule().then(() => owner.avatar).then(avatar => {
        if (owner !== context || owner.avatarEditor?.hasUnsavedChanges()) return;
        window.PortalAvatarEditor.paint(portrait, avatar, {label: title, size: 56});
      }).catch(() => {});
    }

    const dialog = window.PortalUI.createDialogController({
      modal, openClass: "portalDialogOpen", backdropClose: true, escapeClose: true,
      onOpen: () => { modal.hidden = false; },
      beforeClose: () => !busy() && !context?.avatarEditor?.isBusy(),
      onClose: () => { modal.hidden = true; context?.avatarEditor?.destroy(); context = null; TABS.forEach(tab => panel(tab).replaceChildren()); },
    });
    modal.querySelector("[data-portal-detail-close]").addEventListener("click", () => dialog.close());
    TABS.forEach(tab => tabButton(tab).addEventListener("click", () => void switchTab(tab)));
    modal.querySelector('[role="tablist"]').addEventListener("keydown", event => {
      const index = TABS.indexOf(current());
      const next = {ArrowDown: TABS[(index + 1) % TABS.length], ArrowUp: TABS[(index + TABS.length - 1) % TABS.length], Home: TABS[0], End: TABS[TABS.length - 1]}[event.key];
      if (!next) return;
      event.preventDefault();
      void switchTab(next).then(() => tabButton(next).focus());
    });
    window.PortalUIPrimitives.mountIcons(modal);

    return {
      async open(next) {
        if (busy()) { toast(t("权限操作正在处理，请稍后再试。"), "info"); return false; }
        options = next;
        context?.avatarEditor?.destroy();
        // With the permission system off the Package tab and batch grants have nothing to show.
        const permissionsOff = await window.PortalIdentity.permissionSystemOff().catch(() => false);
        context = {kind: next.kind, id: next.id, record: next.record, busy: false, members: null, journal: null, permissionsOff};
        tabButton("package").hidden = permissionsOff;
        context.avatar = avatarModule().then(() => window.PortalAvatarEditor.read(next.kind, next.id, {fresh: true})).catch(() => null);
        $id("scopeMenuPortrait").replaceChildren();
        renderIdentity(context);
        TABS.forEach(tab => panel(tab).replaceChildren());
        dialog.open();
        await switchTab(TABS.includes(next.tab) && !(permissionsOff && next.tab === "package") ? next.tab : "settings");
        return true;
      },
      destroy() { dialog.close({force: true}); dialog.destroy(); surface?.destroy(); modal.remove(); },
    };
  }

  async function load(kind, id) {
    if (kind === "project") {
      const result = await run("SearchResources", {resource_type: "project", q: id, limit: 50});
      const item = (result.items || []).find(entry => entry.resource_id === id);
      if (!item) throw new Error(t("Project 不存在或不可访问"));
      return item;
    }
    const lineage = (await run("GetLineage", {lineage_id: id})).lineage;
    if (!lineage) throw new Error(t("Lineage 不存在或不可访问"));
    return lineage;
  }

  // open({kind: "project"|"lineage", id, tab?, onChanged?, onOpenAdvanced?})
  async function open(options = {}) {
    const kind = options.kind === "project" ? "project" : options.kind === "lineage" ? "lineage" : "";
    const id = String(options.id || "").trim();
    if (!kind || !id) throw new TypeError("kind and id are required");
    // A caller that already holds the record (the workbench's Lineage, the board's Project) passes it.
    const record = options.record ? {...options.record} : await load(kind, id);
    instance ||= createMenu();
    await instance.open({...options, kind, id, record});
    return instance;
  }
  window.PortalScopeMenu = Object.freeze({open});
})();
