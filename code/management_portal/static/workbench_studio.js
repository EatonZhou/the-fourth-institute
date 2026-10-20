(() => {
  "use strict";
  const request = window.PortalProjections.request;
  // The built-in workbenches this Portal installs come with the page (frontend_manifest.py).
  const builtinPaths = Object.freeze(Object.fromEntries(Object.entries(window.__PORTAL_FRONTEND__?.workbenches || {})
    .map(([name, workbench]) => [name, String(workbench?.path || "")])));
  const builtinView = location.pathname === builtinPaths.development ? "development" : "";
  const isView = location.pathname.endsWith("/view") || !!builtinView;
  const societyService = window.PortalSocietyService.createClient();
  const content = document.getElementById("projectionContent");
  const errorBox = document.getElementById("projectionError");
  const params = new URLSearchParams(location.search);
  const element = id => document.getElementById(id);
  const settingsUrl = id => `/workbenches?panel=settings&id=${encodeURIComponent(id)}`;
  const escape = value => window.PortalUIPrimitives.escapeHtml(value);
  const lineageLinks = bindings => Object.entries(bindings || {}).map(([alias, binding]) => window.PortalI18n.html`<a href="/lineage?lineage=${encodeURIComponent(binding.lineage_id)}">${escape(alias)} · ${escape(binding.lineage_id)}</a>`).join(" · ") || window.PortalI18n.markup('<span>尚无已授权的业务团队；不会创建空团队占位。</span>');
  let app, frame, directory, polling, disposed = false, generating = false, writing = false;
  const viewAbort = new AbortController();
  let observationRevision = 0;
  function showError(error) { errorBox.hidden = !error; errorBox.textContent = error?.message || ""; }
  // Changing a workbench asks for UpdateResource (detail allows); others see its settings read-only.
  // Write results carry no allows, and whoever just wrote may keep writing.
  const readOnly = () => app?.allows?.UpdateResource === false;
  function refreshWriteControls() {
    const locked = writing || !app || readOnly();
    element("navigation").disabled = locked;
    element("save").disabled = locked;
    element("publish").disabled = locked || (!app.html && !app.manifest && !app.builtin);
    element("delete").disabled = locked || !!app.builtin;
    element("activationAudit").disabled = locked;
    element("activateBuiltin").disabled = locked || app.published_revision !== app.revision;
    for (const id of ["appTitle", "bindings", "source", "requirements"]) element(id).readOnly = readOnly();
    if (readOnly()) element("generate").disabled = true;
    element("workbenchReadOnly").hidden = !readOnly();
    element("studioContent").setAttribute("aria-busy", String(writing));
    const reverseAnalyze = element("reverseAnalyze");
    if (reverseAnalyze) reverseAnalyze.disabled = locked || generating || (!app.manifest && !String(app.html || "").trim());
  }
  async function busy(button, action, sharedWrite = false) {
    if (button.disabled || (sharedWrite && writing)) return;
    button.disabled = true;
    if (sharedWrite) { writing = true; refreshWriteControls(); }
    showError(null);
    try { await action(); } catch (error) { showError(error); } finally {
      if (sharedWrite) { writing = false; refreshWriteControls(); }
      else button.disabled = button.id === "generate" && generating;
    }
  }
  // Team name, size and current activity for each bound Lineage: one Lineage
  // list read plus the Leaders' states and avatars, after the cards are shown.
  async function catalogTeams(workbenches) {
    const lineageIds = new Set(workbenches.flatMap(window.PortalWorkbenchCatalog.boundLineages));
    if (!lineageIds.size) return new Map();
    let lineages;
    try {
      lineages = ((await societyService.listLineages()).lineages || []).filter(item => lineageIds.has(item.lineage_id));
    } catch {
      return new Map([...lineageIds].map(id => [id, { unavailable: true }]));
    }
    const leaders = [...new Set(lineages.map(item => item.leader_resident_id).filter(Boolean))];
    const [states, avatars] = await Promise.all([
      leaders.length
        ? window.PortalResidentService.loadStates(leaders, { includeViewerState: false }).then(result => result.states || []).catch(() => [])
        : [],
      Promise.all(leaders.map(id => window.PortalAPI.get(`/api/account/avatar?principal=${encodeURIComponent(id)}`)
        .then(result => [id, result.avatar || null]).catch(() => [id, null]))),
    ]);
    const stateOf = new Map(states.map(item => [item.resident_id, item.state || {}]));
    const avatarOf = new Map(avatars);
    const teams = new Map([...lineageIds].map(id => [id, { unavailable: true }]));
    for (const lineage of lineages) {
      const leader = stateOf.get(lineage.leader_resident_id) || {};
      // A Family facade reports its members' work; otherwise count members marked running.
      const family = leader.family_activity || null;
      const statuses = lineage.statuses || {};
      teams.set(lineage.lineage_id, {
        title: lineage.title || lineage.lineage_id,
        member_count: Number(lineage.node_count || (lineage.lineage_node_ids || []).length || 0),
        running: family ? Number(family.running_count || 0) : Number(statuses.running || 0) || (leader.work_state === "running" ? 1 : 0),
        queued: family ? Number(family.queued_count || 0) : Number(leader.queued_count || 0),
        avatar: avatarOf.get(lineage.leader_resident_id) || null,
      });
    }
    return teams;
  }
  async function catalog(previous = directory?.snapshot()) {
    document.body.classList.add("is-catalog");
    directory?.destroy(); directory = null;
    showError(null);
    content.setAttribute("aria-busy", "true");
    content.innerHTML = window.PortalI18n.markup("<p>正在加载工作台…</p>");
    try {
      // One directory read shows the cards. Built-in entries are listed as the server marks
      // them active; their binding checks stay on the pages that use them.
      const result = await request("");
      if (disposed) return;
      content.replaceChildren();
      // 投影工坊 is the designer behind "创建工作台"; its settings open from the create page.
      const workbenches = result.workbenches.filter(item => !item.builtin || (item.native && item.builtin !== "studio"));
      const mountedDirectory = directory = window.PortalWorkbenchCatalog.mount(content, {
        workbenches, favorites: result.favorites, initialState: previous, onRefresh: state => catalog(state),
        onFavorite: async (id, favorite) => (await request("favorite", { id, favorite })).favorites,
      });
      void catalogTeams(workbenches).then(teams => { if (!disposed && directory === mountedDirectory) directory.setTeams(teams); });
    } catch (error) {
      if (disposed) return;
      content.innerHTML = window.PortalI18n.html`<div class="workbenchCatalogEmpty" role="alert">
        <h2>暂时无法加载工作台</h2><p>未能加载完整目录，请重试。</p>
        <button class="portalButton is-primary" id="retryWorkbenchCatalog" type="button">重新加载</button>
        <details class="workbenchCatalogErrorDetails"><summary>错误详情</summary><pre>${escape(error.message)}</pre></details>
      </div>`;
      element("retryWorkbenchCatalog").addEventListener("click", async event => {
        event.currentTarget.disabled = true;
        await catalog();
        content.querySelector('input[type="search"], #retryWorkbenchCatalog')?.focus();
      });
    } finally {
      content.removeAttribute("aria-busy");
    }
  }
  async function editor() {
    document.body.classList.add("is-editor");
    // The page heading becomes the editor's title bar: back link, workbench name, purpose.
    document.querySelector(".projectionHeading").insertAdjacentHTML("afterbegin",
      window.PortalI18n.markup('<nav class="workbenchBreadcrumb" aria-label="工作台导航"><a href="/workbenches">← 所有工作台</a></nav>'));
    content.innerHTML = window.PortalI18n.markup(`<div class="workbenchEditor">
      <section id="projectionStudioModule" aria-labelledby="moduleTitle">
        <h2 id="moduleTitle" class="workbenchVisuallyHidden">创建与预览</h2>
        <div id="studioContent"></div>
      </section></div>`);
    if (params.get("panel") === "studio" && !params.get("id")) void linkStudioSettings();
    await studio();
  }
  // The create page is 投影工坊's own entry, so those who may change it find its settings here.
  async function linkStudioSettings() {
    try {
      const { workbench } = await request("native?name=studio");
      if (disposed || workbench?.allows?.UpdateResource !== true) return;
      const settings = document.createElement("a");
      settings.className = "workbenchStudioSettings";
      settings.href = settingsUrl(workbench.workbench_id);
      settings.textContent = window.PortalI18n.t("投影工坊设置");
      document.querySelector(".workbenchBreadcrumb")?.append(settings);
    } catch { }
  }
  async function preview() {
    frame?.destroy(); frame = null;
    const root = element("previewRoot");
    if (builtinPaths[app?.builtin] && !app.manifest && !app.html) {
      const path = builtinPaths[app.builtin];
      root.innerHTML = window.PortalI18n.html`<div class="projectionEmpty"><h2>${escape(app.title)}</h2><p>内置页面已绑定普通 Lineage，页面配置统一在这里维护。</p><a class="portalButton" href="${path}">打开页面</a></div>`;
    } else if (!app?.html && !app?.manifest) {
      root.innerHTML = window.PortalI18n.markup('<div class="projectionEmpty"><h2>工作台预览</h2><p>选择团队 → 描述需求 → AI 生成 → 预览与发布</p></div>');
    } else frame = await window.PortalProjectionHost.mount(root, app, { preview: true });
  }
  // Fields the user has typed into since they were last filled or submitted.
  // A later refresh (a generation observed at load, a reverse apply) must not
  // overwrite them; submitting them (generate, save, publish) hands them to the
  // stored workbench, whose values are then shown.
  const EDITOR_FIELDS = ["appTitle", "bindings", "source"];
  const typedFields = new Set();
  function trackEditorInput() {
    for (const id of EDITOR_FIELDS) element(id).addEventListener("input", () => typedFields.add(id));
  }
  function setEditorField(id, value, force) {
    if (!force && typedFields.has(id)) return;
    element(id).value = value;
    typedFields.delete(id);
  }
  function refreshEditor({ force = false } = {}) {
    setEditorField("appTitle", app?.title || element("appTitle").value, force);
    setEditorField("bindings", JSON.stringify(app?.bindings || {}, null, 2), force);
    setEditorField("source", app?.manifest ? JSON.stringify({ manifest: app.manifest, panels: app.panels, ...(app.application ? { application: app.application } : {}) }, null, 2) : app?.html || "", force);
    element("source").setAttribute("aria-label", app?.application ? "应用包登记与页面清单 JSON" : app?.manifest ? "页面清单与面板源码 JSON" : "页面 HTML");
    element("navigation").value = app?.navigation || "menu";
    refreshWriteControls();
    element("delete").hidden = !app || !!app.builtin;
    element("openWorkbench").hidden = !(app?.url && (app.builtin || app.published_revision));
    if (app?.url) element("openWorkbench").href = app.url;
    element("builtinActivation").hidden = !app?.builtin;
    element("boundLineages").innerHTML = lineageLinks(app?.bindings) + (app?.binding_notice ? window.PortalI18n.html`<span class="projectionBindingNotice" role="status">${escape(app.binding_notice)}</span>` : "");
    element("versionInfo").textContent = app ? window.PortalI18n.text`草稿 v${app.revision} · ${app.published_revision ? window.PortalI18n.text`已发布 v${app.published_revision}` : window.PortalI18n.t("尚未发布")}` : window.PortalI18n.t("尚未创建");
    element("generationForm").hidden = !!app?.builtin;
    element("generationPanel").hidden = !!app?.builtin;
    element("workbenchSettings").hidden = !app;
    element("workbenchAdvanced").hidden = !app;
    element("workbenchReverse").hidden = !app || !!app.builtin;
    element("studioContent").classList.toggle("is-new", !app);
    element("pageTitle").textContent = app ? app.title : window.PortalI18n.t("创建工作台");
    element("pageDescription").textContent = app
      ? window.PortalI18n.t("管理页面、发布状态与团队绑定。")
      : window.PortalI18n.t("选择一个团队，描述你想怎样工作，让 AI 帮你实现。");
    window.PortalSite.setTitle(() => `${element("pageTitle").textContent} · ${window.PortalI18n.t("Workbenches")}`, " · ");
    element("sourceField").hidden = !!app?.builtin;
    element("moduleTitle").textContent = app ? window.PortalI18n.text`页面与绑定设置 · ${app.title}` : window.PortalI18n.t("投影工坊");
  }
  function renderReversePlan(status, job) {
    const description = document.createElement("p");
    description.textContent = job.plan.summary;
    const target = document.createElement("p");
    target.textContent = window.PortalI18n.text`目标团队：${job.target_lineage_id || job.lineage_id} · 图版本 ${job.target_revision}`;
    const steps = document.createElement("ol");
    steps.className = "projectionPlanSteps";
    for (const step of job.plan.steps) {
      const item = document.createElement("li");
      const heading = document.createElement("strong");
      heading.textContent = `${step.id} · ${step.instruction}`;
      item.append(heading);
      const argumentsList = document.createElement("dl");
      const addArgument = (name, value) => {
        if (value && typeof value === "object" && !Array.isArray(value)) {
          for (const [key, child] of Object.entries(value)) addArgument(`${name}.${key}`, child);
          return;
        }
        const label = document.createElement("dt"); label.textContent = name;
        const text = document.createElement("dd");
        text.textContent = typeof value === "string" ? value : JSON.stringify(value);
        argumentsList.append(label, text);
      };
      for (const [name, value] of Object.entries(step.arguments || {})) addArgument(name, value);
      item.append(argumentsList); steps.append(item);
    }
    const page = document.createElement("p");
    const update = job.plan.page_update;
    page.textContent = update ? window.PortalI18n.text`页面草稿变更：${Object.keys(update).join("、")}；${typeof update.html === "string" ? window.PortalI18n.text`HTML ${update.html.length} 字符；` : ""}不会自动发布。` : window.PortalI18n.t("没有页面草稿变更；不会自动发布。");
    const details = document.createElement("details");
    const summary = document.createElement("summary"); summary.textContent = window.PortalI18n.text`查看完整操作与页面变更（${job.plan.steps.length} 步）`;
    const output = document.createElement("pre"); output.className = "projectionPlanSource";
    output.textContent = JSON.stringify(job.plan, null, 2);
    details.append(summary, output);
    status.append(description, target, steps, page, details);
  }
  async function refreshGeneration(job) {
    clearTimeout(polling);
    const revision = ++observationRevision;
    try {
      const snapshot = await request(`generation?id=${encodeURIComponent(job.id)}`);
      if (disposed || revision !== observationRevision) return;
      showError(null);
      await observe(snapshot);
    } catch (error) {
      if (!disposed && revision === observationRevision) showError(error);
    }
  }
  async function observe(job) {
    if (disposed) return;
    observationRevision += 1;
    clearTimeout(polling);
    const status = element("generationStatus");
    status.hidden = false;
    status.innerHTML = window.PortalI18n.html`<strong>${({ running: window.PortalI18n.t("设计师正在实现工作台"), submitting: window.PortalI18n.t("正在提交实现任务"), completed: window.PortalI18n.t("修改已保存，请预览并发布"), failed: window.PortalI18n.t("实现未完成"), conflict: window.PortalI18n.t("页面在实现期间被修改，请重新协作"), cancelled: window.PortalI18n.t("实现已停止") })[job.status] || escape(job.status)}</strong><p><a href="/lineage?lineage=${encodeURIComponent(job.lineage_id)}&resident=${encodeURIComponent(job.resident_id)}">打开设计师对话</a></p>${job.error ? window.PortalI18n.html`<p>${escape(job.error)}</p>` : ""}`;
    if (job.kind === "reverse" && ["running", "submitting", "failed"].includes(job.status)) status.querySelector("strong").textContent = ({ running: window.PortalI18n.t("AI 正在分析页面与团队流程"), submitting: window.PortalI18n.t("正在提交反投影分析"), failed: window.PortalI18n.t("反投影分析未完成；目标团队未修改") })[job.status];
    if (["running", "submitting"].includes(job.status) && job.input_state === "queued") {
      status.querySelector("strong").textContent = window.PortalI18n.t("任务已排队，尚未开始执行");
      if (job.resident_state?.intake_state === "closed") {
        const notice = document.createElement("p");
        notice.textContent = window.PortalI18n.t("投影工坊 Resident 已关闭接单，请通过上方链接查看状态；当前请求尚未生成产物。");
        status.append(notice);
      }
    }
    if (job.status === "completed") {
      generating = false;
      if (job.kind === "reverse") {
        if (job.applied && job.page_revision) {
          app = await request(`detail?id=${encodeURIComponent(job.workbench_id)}`);
          refreshEditor(); await preview();
        }
        sessionStorage.removeItem("portal.projection.generation");
        element("generate").disabled = false;
        refreshWriteControls();
        // An earlier applied plan says nothing about bindings that broke since.
        const broken = app?.workbench_id === job.workbench_id && app.binding_issues?.length;
        status.querySelector("strong").textContent = job.applied && broken ? window.PortalI18n.text`反投影曾应用；当前绑定不可用：${app.binding_notice}` : job.applied ? window.PortalI18n.t("反投影已应用；页面仍需独立预览和发布") : job.plan_source === "user" ? window.PortalI18n.t("人工修订计划已保存；尚未修改目标团队") : window.PortalI18n.t("反投影分析完成；尚未修改目标团队");
        renderReversePlan(status, job);
        const apply = document.createElement("button");
        apply.className = "portalButton is-primary";
        apply.textContent = window.PortalI18n.t("确认并应用这些增量");
        apply.disabled = !!job.applied;
        apply.onclick = event => busy(event.currentTarget, async () => {
          if (!await window.PortalUI.confirmAction("apply", "反投影计划", { confirmLabel: "确认应用", consequence: `${job.plan.summary}\n${job.plan.steps.map(step => `${step.id}: ${step.instruction}`).join("\n")}\n仅应用此计划；不会自动运行 AI 任务、删除成员、修改权限或发布页面。` })) return;
          const result = await request("reverse/apply", { job_id: job.id, plan_hash: job.plan_hash, confirm: true });
          await observe(result);
        });
        status.append(apply);
        if (!job.applied) planEditor(status, job, apply);
        return;
      }
      // Completion acknowledges a saved page, not the copy already in this
      // editor. Read its current revision even when the baseline is omitted
      // or the local copy has changed since generation began.
      app = await request(`detail?id=${encodeURIComponent(job.workbench_id)}`);
      history.replaceState(null, "", settingsUrl(app.workbench_id));
      sessionStorage.removeItem("portal.projection.generation");
      refreshEditor(); await preview(); element("generate").disabled = false;
      return;
    }
    if (["failed", "cancelled", "conflict"].includes(job.status)) {
      generating = false;
      refreshWriteControls();
      sessionStorage.removeItem("portal.projection.generation");
      element("generate").disabled = false;
      if (job.response) {
        const details = document.createElement("details");
        const summary = document.createElement("summary"); summary.textContent = window.PortalI18n.t("查看设计师回复");
        const output = document.createElement("textarea"); output.value = job.response; output.readOnly = true;
        output.rows = 8;
        output.className = "projectionDesignerReply";
        output.setAttribute("aria-label", summary.textContent);
        details.append(summary, output); status.append(details);
      }
      if (job.kind === "reverse") planEditor(status, job);
      return;
    }
    generating = true;
    element("generate").disabled = true;
    refreshWriteControls();
    const createdAt = Date.parse(job.created_at);
    const elapsed = Number.isFinite(createdAt) ? Math.max(0, Math.floor((Date.now() - createdAt) / 1000)) : null;
    const notice = document.createElement("p");
    notice.setAttribute("role", "status");
    notice.textContent = window.PortalI18n.text`${elapsed === null ? "" : window.PortalI18n.text`本次请求已等待 ${Math.floor(elapsed / 60)} 分 ${elapsed % 60} 秒。`}仍在观察原请求；刷新页面会恢复观察，不会重复提交任务。`;
    const refresh = document.createElement("button");
    refresh.type = "button";
    refresh.className = "portalButton";
    refresh.textContent = window.PortalI18n.t("刷新当前请求状态");
    refresh.onclick = event => busy(event.currentTarget, () => refreshGeneration(job));
    status.append(notice, refresh);
    sessionStorage.setItem("portal.projection.generation", JSON.stringify({ id: job.id, workbench_id: job.workbench_id }));
    polling = setTimeout(() => refreshGeneration(job), 3000);
  }
  function planEditor(container, job, applyButton) {
    const details = document.createElement("details");
    const summary = document.createElement("summary"); summary.textContent = window.PortalI18n.t("人工检查与修订计划");
    const description = document.createElement("p"); description.textContent = window.PortalI18n.t("保存后会标记为人工修订，不会冒充 AI 分析结果；仍需单独确认应用。这里只支持增量创建与手动事件连接。");
    const input = document.createElement("textarea"); input.className = "code"; input.setAttribute("aria-label", window.PortalI18n.t("反投影计划 JSON"));
    input.value = JSON.stringify(job.plan || { summary: "", steps: [] }, null, 2);
    input.oninput = () => { if (applyButton) applyButton.disabled = true; };
    const save = document.createElement("button"); save.className = "portalButton"; save.textContent = window.PortalI18n.t("保存人工修订计划");
    save.onclick = event => busy(event.currentTarget, async () => {
      await observe(await request("reverse/plan", { job_id: job.id, expected_plan_hash: job.plan_hash || "", plan: JSON.parse(input.value) }));
    });
    details.append(summary, description, input, save); container.append(details);
  }
  async function studio() {
    element("studioContent").innerHTML = window.PortalI18n.markup(`
      <div class="projectionStudio">
        <aside class="workbenchEditorControls" aria-label="工作台配置">
          <section class="projectionSection" id="generationPanel">
            <details id="generationDetails" open><summary>让 AI 完善工作台</summary>
              <div id="generationForm">
                <div class="workbenchSectionHeading"><h3>从团队与目标开始</h3>
                <p class="workbenchSectionHint">告诉设计师，你希望在这个工作台里完成什么。</p></div>
                <label class="portalField">选择团队<input id="targetLineage" placeholder="输入或选择团队" list="lineageOptions"></label>
                <datalist id="lineageOptions"></datalist>
                <button class="portalButton is-quiet" id="inspect">读取团队</button>
                <div id="teamInfo" class="projectionStatus" hidden></div>
                <label class="portalField">你希望怎样工作？<textarea id="requirements" rows="6" placeholder="例如：把项目进展与团队讨论放在一起，方便我每天了解工作。"></textarea></label>
                <button id="generate" class="portalButton is-primary">交给设计师实现</button>
              </div>
            </details>
          </section>
          <div id="generationStatus" class="projectionStatus" role="status" hidden></div>
          <section class="projectionSection" id="workbenchSettings" hidden>
            <div class="workbenchSectionHeading"><h3>页面设置</h3><p id="versionInfo"></p></div>
            <p id="workbenchReadOnly" class="projectionBindingNotice" role="status" hidden>权限系统没有给你修改这个工作台的权限。</p>
            <label class="portalField">工作台名称<input id="appTitle" placeholder="为你的工作空间起个名字"></label>
            <label class="portalField">导航位置<select id="navigation"><option value="menu">Workbenches 菜单</option><option value="top">顶部目录</option><option value="hidden">仅通过链接与 Lineage 访问</option></select></label>
            <small>顶部最多显示 4 个自定义工作台，其余可从 Workbenches 菜单访问。</small>
            <p id="boundLineages" class="projectionBindings"></p>
            <div class="projectionActions"><button class="portalButton" id="save" disabled>保存并预览</button><button class="portalButton is-primary" id="publish" disabled>发布工作台</button></div>
            <section id="builtinActivation" hidden><h4>权威业务入口</h4><p>核对团队与流程后，启用此已发布页面。</p><div class="projectionActions"><button class="portalButton" id="activationAudit" type="button">检查迁移结果</button><button class="portalButton" id="activateBuiltin" type="button">启用业务入口</button></div><p id="activationStatus" role="status"></p></section>
          </section>
          <details class="projectionSection" id="workbenchAdvanced" hidden>
            <summary>高级设置 · 页面与团队绑定</summary>
            <p class="workbenchSectionHint">管理团队绑定和页面源码；谁能修改页面由权限系统决定。</p>
            <label class="portalField">绑定 JSON<textarea id="bindings" class="code" spellcheck="false">{}</textarea></label>
            <label class="portalField" id="sourceField">页面 HTML<textarea id="source" class="code" spellcheck="false"></textarea></label>
            <div class="workbenchDangerZone"><button class="portalButton is-danger" id="delete" hidden>删除页面（保留团队）</button></div>
          </details>
          <details class="projectionSection" id="workbenchReverse" hidden>
            <summary>从页面整理团队流程</summary>
            <p class="workbenchSectionHint">分析当前页面，复用团队成员并提出流程调整，审核确认后才会应用。</p>
            <button class="portalButton" id="reverseAnalyze" disabled>分析当前页面与团队</button>
          </details>
        </aside>
        <section class="projectionPreview" aria-label="工作台预览">
          <div class="paneHead workbenchPreviewHeading"><div class="paneHeadText workbenchPreviewHeadingText"><h2 class="paneTitle workbenchPreviewTitle">页面预览</h2><p class="paneNote workbenchPreviewNote">保存后在这里查看效果</p></div>
            <div class="paneHeadActions"><a class="portalButton is-compact" id="openWorkbench" hidden>打开工作台</a></div></div>
          <div id="previewRoot"></div>
        </section>
      </div>`);
    element("targetLineage").value = params.get("lineage") || "";
    element("reverseAnalyze").onclick = event => busy(event.currentTarget, async () => {
      if (!app) throw new Error("请先创建或选择工作台");
      if (Object.entries(fields()).some(([key, value]) => JSON.stringify(value) !== JSON.stringify(app[key]))) throw new Error("请先保存当前页面，再按此版本分析");
      const job = await request("reverse", { id: app.workbench_id, request_id: globalThis.PortalUIPrimitives.randomUUID(), requirements: element("requirements").value.trim() });
      await observe(job);
    });
    element("inspect").onclick = event => busy(event.currentTarget, async () => {
      const target = element("targetLineage").value.trim();
      const graph = await societyService.loadLineage(target);
      const team = element("teamInfo"); team.hidden = false; team.textContent = window.PortalI18n.text`${graph.title} · ${graph.nodes.length} 位成员 / 节点`;
      if (!element("appTitle").value) element("appTitle").value = `${graph.title}工作台`;
    });
    async function loadTeamOptions() {
      try {
        const result = await societyService.listLineages();
        if (disposed) return;
        for (const team of result.lineages || []) {
          const option = document.createElement("option"); option.value = team.lineage_id; option.label = team.title; element("lineageOptions").append(option);
        }
      } catch (error) {
        if (!disposed) showError(error);
      }
    }
    void loadTeamOptions();
    element("generate").textContent = window.PortalI18n.t("交给设计师实现");
    element("generate").onclick = event => busy(event.currentTarget, async () => {
      const lineage_id = element("targetLineage").value.trim();
      const requirements = element("requirements").value.trim();
      if (!lineage_id || !requirements) throw new Error("请选择 Lineage 并描述工作台需求");
      const job = await request("generate", { request_id: globalThis.PortalUIPrimitives.randomUUID(), lineage_id, requirements, title: element("appTitle").value.trim(), workbench_id: app?.workbench_id || "" });
      typedFields.delete("appTitle"); // submitted with the generation request
      app = await request(`detail?id=${encodeURIComponent(job.workbench_id)}`);
      history.replaceState(null, "", settingsUrl(app.workbench_id));
      refreshEditor(); await observe(job);
    });
    trackEditorInput();
    const fields = () => ({ title: element("appTitle").value.trim(), bindings: JSON.parse(element("bindings").value), ...(app?.manifest ? { ...JSON.parse(element("source").value), html: "" } : { html: element("source").value }), navigation: element("navigation").value });
    element("navigation").onchange = event => busy(event.currentTarget, async () => {
      try {
        app = await request("update", { id: app.workbench_id, expected_revision: app.revision, navigation: element("navigation").value });
        element("versionInfo").textContent = window.PortalI18n.text`草稿 v${app.revision} · 已发布 v${app.published_revision || "—"} · 导航位置已生效，无需重新发布`;
        dispatchEvent(new Event("portal:workbenches-changed"));
      } catch (error) { element("navigation").value = app.navigation; throw error; }
    }, true);
    element("save").onclick = event => busy(event.currentTarget, async () => {
      app = await request("update", { id: app.workbench_id, expected_revision: app.revision, ...fields() });
      dispatchEvent(new Event("portal:workbenches-changed"));
      refreshEditor({ force: true }); await preview();
    }, true);
    element("publish").onclick = event => busy(event.currentTarget, async () => {
      if (Object.entries(fields()).some(([key, value]) => JSON.stringify(value) !== JSON.stringify(app[key]))) throw new Error("请先保存并预览当前修改，再发布这个版本");
      app = await request("publish", { id: app.workbench_id, expected_revision: app.revision });
      dispatchEvent(new Event("portal:workbenches-changed"));
      const status = element("generationStatus"); status.hidden = false;
      status.innerHTML = window.PortalI18n.html`<strong>发布成功 · v${app.published_revision}</strong><p><a href="${builtinPaths[app.builtin] || `/workbenches/view?id=${encodeURIComponent(app.workbench_id)}`}">打开已发布工作台 →</a></p>`;
      refreshEditor({ force: true });
    }, true);
    async function checkActivation() {
      const report = await request(`activation-report?id=${encodeURIComponent(app.workbench_id)}`);
      element("activationStatus").textContent = report.ready ? window.PortalI18n.t("绑定检查通过；启用后此页面将作为这个内置入口的统一业务页面。") : report.issues.map(issue => issue.message).join("；");
      return report;
    }
    element("activationAudit").onclick = event => busy(event.currentTarget, checkActivation);
    element("activateBuiltin").onclick = event => busy(event.currentTarget, async () => {
      const report = await checkActivation();
      if (!report.ready) throw new Error("迁移检查未通过，尚未改变业务入口。");
      const active = await request(`native?name=${encodeURIComponent(app.builtin)}`);
      const previousId = active.workbench?.workbench_id || "";
      if (!await window.PortalUI.confirmAction("apply", "权威业务入口", { confirmLabel: "确认启用", consequence: `${app.builtin}：${previousId || "尚未配置"} → ${app.workbench_id}；主团队 ${report.main_lineage_id}。只切换显式入口，不创建、转移或删除团队。` })) return;
      app = await request("update", { id: app.workbench_id, expected_revision: report.revision, activate_builtin: true, previous_app_id: previousId });
      element("activationStatus").textContent = window.PortalI18n.t("权威业务入口已启用。");
      dispatchEvent(new Event("portal:workbenches-changed"));
      refreshEditor();
    }, true);
    element("delete").onclick = event => busy(event.currentTarget, async () => {
      if (!await window.PortalUI.confirmAction("delete", app.title, { consequence: "只删除页面；关联的 Lineage 和 Resident 会完整保留。" })) return;
      await request("delete", { id: app.workbench_id }); location.href = "/workbenches";
    }, true);
    if (params.get("id")) {
      app = await request(`detail?id=${encodeURIComponent(params.get("id"))}`);
      element("targetLineage").value = app.bindings.main?.lineage_id || "";
      element("requirements").value = app.description || "";
    }
    element("generationDetails").open = !app;
    refreshEditor(); await preview();
    let saved;
    try { saved = JSON.parse(sessionStorage.getItem("portal.projection.generation") || "null"); } catch { sessionStorage.removeItem("portal.projection.generation"); }
    if (saved && app?.workbench_id === saved.workbench_id) await observe(await request(`generation?id=${encodeURIComponent(saved.id)}`));
    else if (app?.latest_generation) await observe(app.latest_generation);
  }
  async function view() {
    document.body.classList.add("is-view");
    document.querySelector(".projectionHeading").hidden = true;
    if (params.get("embedded") === "1") document.querySelector(".portalSiteHeader").hidden = true;
    content.innerHTML = window.PortalI18n.markup('<div id="viewRoot"></div>');
    if (builtinView === "development") {
      window.PortalSite.setTitle(() => window.PortalI18n.t("Portal 开发投影"), " · ");
      frame = window.PortalDevelopmentWorkspace.mount(element("viewRoot"), {
        signal: viewAbort.signal,
        async openDevelopment(root, options) {
          const entry = await request("native?name=development" + (options.region === "packaging" ? "&panel=packaging" : ""));
          if (!entry.workbench) throw new Error("Portal 开发投影的内置入口尚未启用");
          return window.PortalProjectionHost.open(root, entry.workbench.workbench_id, { ...options, signal: viewAbort.signal });
        },
      });
      return;
    }
    const id = params.get("id") || "";
    frame = await window.PortalProjectionHost.open(element("viewRoot"), id, { signal: viewAbort.signal });
    app = frame.app;
    if (builtinPaths[app.builtin] && !app.manifest && !app.html) { location.replace(builtinPaths[app.builtin]); return; }
    window.PortalSite.setTitle(() => app.title, " · ");
  }
  addEventListener("pagehide", event => {
    if (event.persisted) return;
    disposed = true;
    viewAbort.abort();
    clearTimeout(polling);
    frame?.destroy();
    directory?.destroy();
  });
  (isView ? view() : params.get("panel") || params.get("id") ? editor() : catalog()).catch(error => {
    if (disposed) return;
    if (isView) content.replaceChildren();
    showError(error);
  });
})();
