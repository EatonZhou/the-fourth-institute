(function () {
  "use strict";

  // The Project page's header menu (decision_46c37026e8ea): edit the
  // registered fields and the avatar, rename after a dry run, delete after the
  // user types the Project ID.  Each runs one HATG instruction as the user.
  if (window.PortalProjectSettings) return;

  const common = () => window.PortalProjectCommon;

  function dialog({ title, body, submitLabel, tone = "primary", wide = false }) {
    const { esc } = common();
    const overlay = document.createElement("div");
    overlay.className = "portalDetailOverlay";
    overlay.hidden = true;
    overlay.dataset.portalDetailOverlay = "1";
    overlay.dataset.portalOverlayLayer = "dialog";
    overlay.innerHTML = window.PortalI18n.html`<form class="portalDetailSurface portalPromptSurface projectSettingsSurface${wide ? " is-wide" : ""}" data-portal-detail-surface role="dialog" aria-modal="true" aria-labelledby="projectSettingsTitle">
        <header class="portalDetailHeader">
          <h2 id="projectSettingsTitle">${esc(title)}</h2>
          <button class="portalDetailClose" type="button" data-settings-cancel aria-label="关闭" data-portal-icon="close"></button>
        </header>
        <div class="portalDetailBody portalPromptBody projectSettingsBody">${body}<p class="projectSettingsError" role="alert" data-settings-error hidden></p></div>
        <footer class="portalDetailFooter">
          <button class="portalButton" type="button" data-settings-cancel>取消</button>
          <button class="portalButton ${tone === "danger" ? "is-danger" : "is-primary"}" type="submit" data-settings-submit>${esc(submitLabel)}</button>
        </footer>
      </form>`;
    document.body.appendChild(overlay);
    window.PortalUIPrimitives.mountIcons(overlay);
    const form = overlay.querySelector("form");
    let busy = false;
    let closed;
    const done = new Promise(resolve => { closed = resolve; });
    const controller = window.PortalUI.createDialogController({
      modal: overlay, backdropClose: true, escapeClose: true,
      beforeClose: () => !busy,
      onClose: () => { controller.destroy(); overlay.remove(); closed(); },
    });
    overlay.querySelectorAll("[data-settings-cancel]").forEach(button => button.addEventListener("click", () => controller.close()));
    const error = overlay.querySelector("[data-settings-error]");
    return {
      form, overlay, done,
      open: focus => controller.open({ focusTarget: focus || form.querySelector("input, textarea, select") }),
      close: () => controller.close(),
      setBusy(value) {
        busy = value;
        form.querySelectorAll("button, input, textarea, select").forEach(control => { control.disabled = value; });
      },
      showError(message) { error.textContent = message || ""; error.hidden = !message; },
    };
  }

  function edit(project, { statusVocab = [], onSaved } = {}) {
    const { esc, t, STATUS_LABELS, TYPE_LABELS, statusLabel, typeLabel, execute } = common();
    const statuses = statusVocab.length ? statusVocab : Object.keys(STATUS_LABELS);
    const types = Object.keys(TYPE_LABELS);
    const options = (values, selected, label) => values.map(value =>
      `<option value="${esc(value)}"${value === selected ? " selected" : ""}>${esc(label(value))}</option>`).join("");
    const view = dialog({
      title: window.PortalI18n.text`编辑 ${project.project_id}`, submitLabel: t("保存"), wide: true,
      body: window.PortalI18n.html`<div class="projectSettingsAvatar"><div class="residentMenuPortrait" data-settings-portrait></div><section data-settings-avatar aria-label="项目头像"></section></div>
        <label class="portalField"><span>说明</span><textarea name="description" rows="4">${esc(project.description || "")}</textarea></label>
        <div class="projectSettingsGrid">
          <label class="portalField"><span>状态</span><select name="status">${options(statuses, project.status, statusLabel)}</select></label>
          <label class="portalField"><span>类型</span><select name="project_type"><option value="">未设置</option>${options(types, project.project_type, typeLabel)}</select></label>
          <label class="portalField"><span>截止时间</span><input name="ddl" value="${esc(project.ddl || "")}" placeholder="2026-12-31 18:00" autocomplete="off" /></label>
        </div>
        <label class="portalField"><span>标签</span><input name="tags" value="${esc((project.tags || []).join(", "))}" placeholder="robotics, paper" autocomplete="off" /></label>`,
    });
    const { form } = view;
    let avatarEditor = null;
    const avatarRow = form.querySelector("[data-settings-avatar]");
    void window.PortalAvatarEditor.loadAssets().then(() => {
      if (!avatarRow.isConnected) return;
      avatarEditor = window.PortalAvatarEditor.mount(avatarRow, {
        portrait: form.querySelector("[data-settings-portrait]"), label: project.project_id, clearable: true,
        load: () => window.PortalAvatarEditor.read("project", project.project_id, { fresh: true }),
        save: async avatar => (await execute("UpdateProject", { project_id: project.project_id, changes: { avatar } }, "project-avatar")).project?.avatar || null,
        onSaved: avatar => {
          window.PortalAvatarEditor.remember("project", project.project_id, avatar);
          onSaved?.({ avatar });
        },
      });
    }).catch(() => { avatarRow.parentElement.hidden = true; });
    void view.done.then(() => avatarEditor?.destroy());
    form.addEventListener("submit", async event => {
      event.preventDefault();
      const next = {
        description: form.description.value.trim(),
        status: form.status.value,
        project_type: form.project_type.value,
        ddl: form.ddl.value.trim(),
        tags: [...new Set(form.tags.value.split(/[,，\s]+/).map(tag => tag.trim()).filter(Boolean))],
      };
      const changes = Object.fromEntries(Object.entries(next).filter(([field, value]) =>
        JSON.stringify(value) !== JSON.stringify(field === "tags" ? (project.tags || []) : (project[field] || ""))));
      if (changes.project_type === "") delete changes.project_type;
      if (!Object.keys(changes).length) { view.close(); return; }
      view.setBusy(true);
      view.showError("");
      try {
        await execute("UpdateProject", { project_id: project.project_id, changes }, "project-edit");
        view.setBusy(false);
        view.close();
        onSaved?.(changes);
      } catch (error) {
        view.setBusy(false);
        view.showError(window.PortalI18n.text`保存失败：${error.message || error}`);
      }
    });
    view.open(form.description);
    return view.done;
  }

  function dryRunHtml(result) {
    const { esc, t } = common();
    const blockers = result.blockers || [];
    const busy = result.busy_resident_ids || [];
    const relations = Object.entries(result.relations || {}).filter(([, ids]) => (ids || []).length);
    const rows = Object.entries(result.database?.tables || {}).filter(([, count]) => count);
    const items = [
      window.PortalI18n.text`目录：${result.previous_path} → ${result.path}`,
      window.PortalI18n.text`Portal 数据：${result.database?.rows || 0} 行（${rows.length} 张表）`,
      window.PortalI18n.text`暂停接单的 Resident：${(result.resident_ids || []).length} 个`,
      ...relations.map(([feature, ids]) => window.PortalI18n.text`${feature}：${ids.length} 项随之改用新 ID`),
      ...(result.external_references || []).length
        ? [window.PortalI18n.text`Portal 之外提到旧目录的服务脚本：${result.external_references.length} 个，需另行检查`] : [],
    ];
    return `<ul class="projectDryRun">${items.map(item => `<li>${esc(item)}</li>`).join("")}</ul>${
      busy.length ? `<p class="projectSettingsWarning">${esc(window.PortalI18n.text`${busy.length} 个 Resident 正在执行，等它们结束后再改名。`)}</p>` : ""}${
      blockers.length ? `<p class="projectSettingsWarning">${esc(t("项目目录正被 Portal 之外的挂载或服务使用："))}${esc(blockers.map(item => item.path || item.name || JSON.stringify(item)).join("、"))}</p>` : ""}`;
  }

  function rename(project, { onRenamed } = {}) {
    const { esc, t, execute } = common();
    const view = dialog({
      title: window.PortalI18n.text`为 ${project.project_id} 改名`, submitLabel: t("检查"),
      body: window.PortalI18n.html`<p class="projectSettingsNote">项目 ID 就是项目的名称。改名会移动项目目录，并一起改写 Portal 中的引用；历史消息保留原文。</p>
        <label class="portalField"><span>新的项目 ID</span><input name="new_project_id" value="${esc(project.project_id)}" autocomplete="off" required /></label>
        <div data-rename-report></div>`,
    });
    const { form } = view;
    const report = form.querySelector("[data-rename-report]");
    const submit = form.querySelector("[data-settings-submit]");
    let checked = "";
    form.new_project_id.addEventListener("input", () => {
      checked = "";
      report.replaceChildren();
      submit.textContent = t("检查");
      submit.className = "portalButton is-primary";
    });
    form.addEventListener("submit", async event => {
      event.preventDefault();
      const target = form.new_project_id.value.trim();
      if (!target || target === project.project_id) { form.new_project_id.focus(); return; }
      view.setBusy(true);
      view.showError("");
      try {
        if (checked !== target) {
          const result = await execute("RenameProject", { project_id: project.project_id, new_project_id: target, dry_run: true });
          report.innerHTML = dryRunHtml(result);
          const blocked = (result.blockers || []).length || (result.busy_resident_ids || []).length;
          view.setBusy(false);
          if (blocked) return;
          checked = target;
          submit.textContent = t("确认改名");
          submit.className = "portalButton is-danger";
          return;
        }
        await execute("RenameProject", { project_id: project.project_id, new_project_id: target }, "project-rename");
        view.setBusy(false);
        view.close();
        onRenamed?.(target);
      } catch (error) {
        view.setBusy(false);
        view.showError(window.PortalI18n.text`改名失败：${error.message || error}`);
      }
    });
    view.open(form.new_project_id);
    return view.done;
  }

  function remove(project, { team = {}, onDeleted } = {}) {
    const { esc, t, execute } = common();
    const lineages = (team.lineages || []).length + (team.project_lineage_id ? 1 : 0);
    const view = dialog({
      title: window.PortalI18n.text`删除 ${project.project_id}`, submitLabel: t("确认删除"), tone: "danger",
      body: window.PortalI18n.html`<p class="projectSettingsWarning">删除后不能恢复。</p>
        <ul class="projectDryRun">
          <li>${esc(window.PortalI18n.text`${lineages} 个 Lineage 与其中的 ${team.resident_count || 0} 个 Resident 一起删除`)}</li>
          <li>项目目录中的文件删除；其他 Resident 仍在使用的 Workspace 保留</li>
          <li>项目下的资源登记注销；论文等共享库中的对象保留，只去掉与这个项目的关联</li>
        </ul>
        <label class="portalField"><span>${esc(window.PortalI18n.text`输入项目 ID「${project.project_id}」确认`)}</span><input name="confirm_id" autocomplete="off" spellcheck="false" placeholder="${esc(project.project_id)}" /></label>
        <p class="projectSettingsHint" data-confirm-hint hidden>与项目 ID 不一致，完全相同后才能确认删除</p>`,
    });
    const { form } = view;
    const submit = form.querySelector("[data-settings-submit]");
    const hint = form.querySelector("[data-confirm-hint]");
    const typed = () => form.confirm_id.value.trim();
    // The confirm button stays disabled until the typed text is the Project ID; a mismatch says so.
    const check = () => {
      submit.disabled = typed() !== project.project_id;
      hint.hidden = !typed() || typed() === project.project_id;
    };
    check();
    form.confirm_id.addEventListener("input", check);
    form.addEventListener("submit", async event => {
      event.preventDefault();
      if (typed() !== project.project_id) return;
      view.setBusy(true);
      view.showError("");
      try {
        await execute("DeleteProject", { project_id: project.project_id }, "project-delete");
        view.setBusy(false);
        view.close();
        onDeleted?.();
      } catch (error) {
        view.setBusy(false);
        check();
        view.showError(window.PortalI18n.text`删除失败：${error.message || error}`);
      }
    });
    view.open(form.confirm_id);
    return view.done;
  }

  window.PortalProjectSettings = Object.freeze({ edit, rename, remove });
})();
