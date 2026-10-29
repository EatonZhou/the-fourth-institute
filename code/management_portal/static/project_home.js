(function () {
  "use strict";

  // One Project's page (decision_46c37026e8ea): who it is, its summary.md,
  // its team, its resources and what changed lately.  GetProject gives the
  // registry, summary, team, counts and changes; the team's live states and
  // work summaries come from ListResidentStates and GetResidentSummaries.
  if (window.PortalProjectHome) return;

  const common = () => window.PortalProjectCommon;
  const WORK_STATES = Object.freeze({ running: "正在工作", queued: "排队中", idle: "空闲" });

  function listOf(value, limit = 1) {
    return (Array.isArray(value) ? value : [value]).filter(item => typeof item === "string" && item.trim()).slice(0, limit);
  }

  function create({ root, onOpenList, onOpenMap, onRenamed, onDeleted, onChanged } = {}) {
    if (!root) throw new TypeError("PortalProjectHome.create requires root");
    const { esc, t } = common();
    let data = null;
    let statusVocab = [];
    let generation = 0;
    let editing = false;
    let menuOpen = false;

    const project = () => data?.project || {};
    const projectId = () => String(project().resource_id || "");
    const team = () => data?.team || { lineages: [] };

    function heroHtml() {
      const item = project();
      const { avatarHtml, typeLabel, statusLabel, formatDeadline, STATUS_LABELS } = common();
      const closed = ["done", "archived"].includes(item.status);
      const deadline = closed ? { label: "" } : formatDeadline(item.ddl);
      const vocab = statusVocab.length ? statusVocab : Object.keys(STATUS_LABELS);
      const statuses = vocab.includes(item.status) ? vocab : [item.status, ...vocab];
      return window.PortalI18n.html`<header class="projectHero">
        <div class="projectHeroAvatar">${avatarHtml(projectId(), item.avatar, 48)}</div>
        <div class="projectHeroMain">
          <div class="projectHeroTitle">
            <h1>${esc(projectId())}</h1>
            <div class="projectHeroMeta">
              ${item.project_type ? `<span class="projectChip">${esc(typeLabel(item.project_type))}</span>` : ""}
              <select class="projectStatusSelect" data-home-status aria-label="项目状态">${statuses.map(value =>
                `<option value="${esc(value)}"${value === item.status ? " selected" : ""}>${esc(statusLabel(value))}</option>`).join("")}</select>
              ${deadline.label ? `<span class="projectDeadline" data-tone="${esc(deadline.tone)}" title="${esc(item.ddl)}">${esc(deadline.label)}</span>` : ""}
              ${(item.tags || []).map(tag => `<span class="projectTag">#${esc(tag)}</span>`).join("")}
            </div>
          </div>
          <p class="projectHeroDescription">${esc(item.description || t("还没有说明"))}</p>
        </div>
        <div class="projectHeroActions">
          <button class="portalButton is-compact" type="button" data-home-copy data-portal-icon="copy"><span>复制链接</span></button>
          <div class="projectMenuHost">
            <button class="portalButton is-compact" type="button" data-home-menu aria-haspopup="menu" aria-expanded="false" data-portal-icon="more-horizontal"><span>更多</span></button>
            <div class="projectMenu" role="menu" data-home-menu-list hidden>
              <button type="button" role="menuitem" data-home-action="edit">编辑信息</button>
              <button type="button" role="menuitem" data-home-action="rename">改名</button>
              <button type="button" role="menuitem" class="is-danger" data-home-action="delete">删除项目</button>
            </div>
          </div>
        </div>
        ${statsHtml()}
      </header>`;
    }

    // "loading" until the full GetProject has counted the Project's resources, then "ready" or "failed".
    const resourcesReady = () => data.resources_state === "ready";

    function statsHtml() {
      const current = team();
      const total = Object.values(data.resource_counts || {}).reduce((sum, count) => sum + count, 0);
      const latest = data.recent_changes?.[0]?.modified_at || "";
      const running = current.running_resident_count || 0;
      return window.PortalI18n.html`<div class="projectStats">
        <div class="projectStat"><strong>${(current.lineages || []).length}</strong><span>团队</span></div>
        <div class="projectStat${running ? " is-live" : ""}"><strong>${current.resident_count || 0}</strong><span>${esc(running ? window.PortalI18n.text`Resident · ${running} 个正在工作` : t("Resident"))}</span></div>
        <div class="projectStat"><strong>${resourcesReady() ? total : "…"}</strong><span>资源</span></div>
        <div class="projectStat"><strong>${esc(!resourcesReady() ? "…" : latest ? common().relativeTime(latest) : "—")}</strong><span>最近变化</span></div>
      </div>`;
    }

    function resourcesPendingHtml() {
      return `<p class="projectEmpty">${esc(t(data.resources_state === "failed" ? "资源统计失败，刷新页面重试。" : "正在统计资源…"))}</p>`;
    }

    function summaryHtml() {
      const text = String(data.summary?.text || "");
      const body = editing
        ? `<textarea class="projectSummaryEditor" data-summary-editor spellcheck="false">${esc(text)}</textarea>`
        : text.trim()
          ? `<div class="projectSummaryBody markdownBody">${window.MD.render(text)}</div>`
          : `<p class="projectEmpty">${esc(t("还没有 summary.md。写下项目的目标、现状和下一步，或请 Project Resident 起草。"))}</p>`;
      const actions = editing
        ? window.PortalI18n.html`<button class="portalButton is-compact" type="button" data-summary-cancel>取消</button>
          <button class="portalButton is-compact is-primary" type="button" data-summary-save>保存</button>`
        : window.PortalI18n.html`<button class="portalButton is-compact" type="button" data-summary-edit data-portal-icon="edit"><span>编辑</span></button>`;
      return window.PortalI18n.html`<section class="projectSection" id="projectSummary" aria-labelledby="projectSummaryTitle">
        <header class="projectSectionHead"><h2 id="projectSummaryTitle">概况</h2><div class="projectSectionActions">${actions}</div></header>
        ${body}
      </section>`;
    }

    // The research map in miniature: the goal, the directions by stance, and a way in.
    function mapHtml() {
      const map = data.research_map;
      const directions = map ? window.PortalProjectMapLayout.visible(map.directions || []) : [];
      const counts = window.PortalProjectMapLayout.counts(directions);
      const labels = { focus: "主攻", explore: "探索", candidate: "候选", watch: "观望", concluded: "已收束" };
      const positions = window.PortalProjectMapLayout.radial(directions);
      const box = window.PortalProjectMapLayout.bounds(positions, 140);
      const tree = directions.map(item => {
        const from = positions[item.parent_id] || { x: 0, y: 0 };
        const to = positions[item.direction_id];
        return `<line x1="${from.x}" y1="${from.y}" x2="${to.x}" y2="${to.y}" />`;
      }).join("");
      const dots = directions.map(item => {
        const at = positions[item.direction_id];
        return `<circle cx="${at.x}" cy="${at.y}" r="${item.stance === "focus" ? 46 : item.stance === "watch" ? 26 : 34}" data-stance="${esc(item.stance)}"><title>${esc(item.title)}</title></circle>`;
      }).join("");
      const preview = directions.length
        ? `<svg class="projectMapPreview" viewBox="${box.minX} ${box.minY} ${box.maxX - box.minX} ${box.maxY - box.minY}" aria-hidden="true"><g class="projectMapPreviewEdges">${tree}</g><circle class="projectMapPreviewGoal" cx="0" cy="0" r="58" />${dots}</svg>`
        : `<div class="projectMapPreview is-empty" aria-hidden="true"><span></span></div>`;
      const summary = Object.entries(labels).filter(([stance]) => counts[stance])
        .map(([stance, label]) => `<span class="mapCount" data-stance-tone="${stance}">${esc(t(label))} ${counts[stance]}</span>`).join("");
      return window.PortalI18n.html`<section class="projectSection projectMapCard" id="projectMap" aria-labelledby="projectMapTitle">
        <header class="projectSectionHead"><h2 id="projectMapTitle">研究地图</h2>
          <a class="portalButton is-compact is-primary" href="${esc(common().projectHref(projectId()))}&view=map" data-home-map data-portal-icon="map"><span>${esc(t(directions.length ? "打开研究地图" : "开始绘制研究地图"))}</span></a></header>
        <a class="projectMapCardBody" href="${esc(common().projectHref(projectId()))}&view=map" data-home-map>
          ${preview}
          <div class="projectMapCardText">
            ${directions.length ? `<div class="mapCounts">${summary}</div>` : ""}
            <p>${esc(directions.length
              ? window.PortalI18n.text`${directions.length} 个方向 · 修订 ${map.revision} · ${common().relativeTime(map.updated_at)}`
              : t("在这里掌控项目的研发方向：定主攻、派团队、看进展、下结论。"))}</p>
          </div>
        </a>
      </section>`;
    }

    function residentSlot(residentId, label, size) {
      return `<span class="projectAvatarSlot" data-avatar-kind="resident" data-avatar-id="${esc(residentId)}" data-avatar-label="${esc(label)}" data-avatar-size="${size}">${common().avatarHtml(label, null, size)}</span>`;
    }

    function teamHtml() {
      const current = team();
      const lead = current.project_resident_id
        ? window.PortalI18n.html`<article class="projectLead" data-team-resident="${esc(current.project_resident_id)}">
            ${residentSlot(current.project_resident_id, "Project Resident", 52)}
            <div class="projectLeadBody">
              <div class="projectLeadTitle"><strong data-team-title>Project Resident</strong><span class="projectWorkState" data-team-state></span></div>
              <p class="projectLeadFocus" data-team-focus>协调这个项目里各 Lineage 的 Leader；右侧对话就是和它说话。</p>
            </div>
            ${current.project_lineage_id ? `<a class="portalButton is-compact" href="${esc(common().lineageHref(current.project_lineage_id))}">${esc(t("打开 HATG"))}</a>` : ""}
          </article>`
        : `<p class="projectEmpty">${esc(t("Project Resident 尚未就绪：项目主体还没有初始化完成。"))}</p>`;
      const cards = (current.lineages || []).map(lineage => `<a class="projectTeamCard" href="${esc(common().lineageHref(lineage.lineage_id))}" data-team-resident="${esc(lineage.leader_resident_id)}">
          <header>
            <span class="projectAvatarSlot" data-avatar-kind="lineage" data-avatar-id="${esc(lineage.lineage_id)}" data-avatar-label="${esc(lineage.title)}" data-avatar-size="40">${common().avatarHtml(lineage.title, null, 40)}</span>
            <div class="projectTeamTitle"><strong>${esc(lineage.title || lineage.lineage_id)}</strong>
              <span>${esc(window.PortalI18n.text`${lineage.member_count} 名成员`)}${lineage.running_count ? ` · <b class="projectRunning"><i class="projectPulse" aria-hidden="true"></i>${esc(window.PortalI18n.text`${lineage.running_count} 个正在工作`)}</b>` : ""}</span>
            </div>
          </header>
          <p class="projectTeamFocus" data-team-focus>${esc(t("正在读取当前重点…"))}</p>
          <p class="projectTeamResult" data-team-result hidden></p>
          <footer>${lineage.leader_resident_id ? `${residentSlot(lineage.leader_resident_id, lineage.title, 22)}<span data-team-title>${esc(t("Leader"))}</span><span class="projectWorkState" data-team-state></span>` : `<span>${esc(t("还没有 Leader"))}</span>`}</footer>
        </a>`).join("");
      return window.PortalI18n.html`<section class="projectSection" id="projectTeam" aria-labelledby="projectTeamTitle">
        <header class="projectSectionHead"><h2 id="projectTeamTitle">团队</h2><a class="projectSectionLink" href="/lineage">在 Society 中管理</a></header>
        ${lead}
        ${cards ? `<div class="projectTeamGrid">${cards}</div>` : `<p class="projectEmpty">${esc(t("还没有 Lineage。在 Society 中为这个项目添加 Lineage，组建团队。"))}</p>`}
      </section>`;
    }

    function resourcesHtml() {
      const counts = Object.entries(data.resource_counts || {}).sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0]));
      const knowledge = common().knowledgeTypes();
      const tile = ([type, count]) => {
        const href = common().resourcesHref(type, projectId());
        const inner = `<strong>${count}</strong><span>${esc(common().resourceLabel(type))}</span>`;
        return href ? `<a class="projectResourceTile" href="${esc(href)}">${inner}</a>` : `<div class="projectResourceTile">${inner}</div>`;
      };
      const groups = [
        [t("知识"), counts.filter(([type]) => knowledge.has(type))],
        [t("研究与工程"), counts.filter(([type]) => !knowledge.has(type))],
      ].filter(([, items]) => items.length);
      return window.PortalI18n.html`<section class="projectSection" id="projectResources" aria-labelledby="projectResourcesTitle">
        <header class="projectSectionHead"><h2 id="projectResourcesTitle">资源</h2></header>
        ${!resourcesReady() ? resourcesPendingHtml()
          : groups.length ? groups.map(([title, items]) => `<div class="projectResourceGroup"><h3>${esc(title)}</h3><div class="projectResourceGrid">${items.map(tile).join("")}</div></div>`).join("")
          : `<p class="projectEmpty">${esc(t("这个项目还没有资源。"))}</p>`}
      </section>`;
    }

    function recentHtml() {
      const items = data.recent_changes || [];
      return window.PortalI18n.html`<section class="projectSection" id="projectRecent" aria-labelledby="projectRecentTitle">
        <header class="projectSectionHead"><h2 id="projectRecentTitle">最近变化</h2></header>
        ${!resourcesReady() ? resourcesPendingHtml() : items.length ? `<ol class="projectRecent">${items.map(item => `<li>
            <span class="projectRecentType">${esc(common().resourceLabel(item.resource_type))}</span>
            ${item.url ? `<a href="${esc(item.url)}">${esc(item.title || item.resource_id)}</a>` : `<span>${esc(item.title || item.resource_id)}</span>`}
            <time datetime="${esc(item.modified_at)}" title="${esc(item.modified_at)}">${esc(common().relativeTime(item.modified_at))}</time>
          </li>`).join("")}</ol>` : `<p class="projectEmpty">${esc(t("还没有变化。"))}</p>`}
      </section>`;
    }

    function render() {
      if (!data) return;
      root.innerHTML = window.PortalI18n.html`<article class="projectHome">
        <nav class="projectCrumbs" aria-label="项目导航"><a href="/projects" data-home-list>项目</a><span aria-hidden="true">/</span><span>${esc(projectId())}</span></nav>
        ${heroHtml()}
        <div class="projectHomeSections">
          ${mapHtml()}
          ${summaryHtml()}
          ${teamHtml()}
          <div class="projectHomeColumns">${resourcesHtml()}${recentHtml()}</div>
        </div>
      </article>`;
      window.PortalUIPrimitives.mountIcons(root);
      document.title = `${projectId()} · ${window.PortalI18n.t("Project")}`;
    }

    function rerenderSummary() {
      const node = root.querySelector("#projectSummary");
      if (!node) return;
      node.outerHTML = summaryHtml();
      window.PortalUIPrimitives.mountIcons(root);
      if (editing) root.querySelector("[data-summary-editor]")?.focus();
    }

    // The Leaders' live states and work summaries, then the avatars.
    async function loadTeamLive(current) {
      const leaders = [team().project_resident_id, ...(team().lineages || []).map(item => item.leader_resident_id)].filter(Boolean);
      if (leaders.length) {
        const unique = [...new Set(leaders)].slice(0, 128);
        const [states, summaries] = await Promise.all([
          common().execute("ListResidentStates", { resident_ids: unique, include_viewer_state: false }).catch(() => ({ states: [] })),
          common().execute("GetResidentSummaries", { resident_ids: unique, fields: ["current_focus", "recent_results"] }).catch(() => ({ summaries: [] })),
        ]);
        if (current !== generation) return;
        const stateOf = new Map((states.states || []).map(item => [item.resident_id, item.state || {}]));
        const summaryOf = new Map((summaries.summaries || []).map(item => [item.resident_id, item]));
        root.querySelectorAll("[data-team-resident]").forEach(card => {
          const residentId = card.dataset.teamResident;
          const state = stateOf.get(residentId) || {};
          const work = state.work_state === "running" ? "running" : Number(state.queued_count || 0) > 0 ? "queued" : "idle";
          card.querySelectorAll("[data-team-state]").forEach(node => {
            node.textContent = stateOf.has(residentId) ? t(WORK_STATES[work]) : "";
            node.dataset.state = work;
          });
          const summary = summaryOf.get(residentId);
          if (summary?.title) card.querySelectorAll("[data-team-title]").forEach(node => { node.textContent = summary.title; });
          const focus = listOf(summary?.summary?.current_focus)[0];
          const result = listOf(summary?.summary?.recent_results)[0];
          const focusNode = card.querySelector("[data-team-focus]");
          if (focusNode && (focus || !card.matches(".projectLead"))) focusNode.textContent = focus || t("还没有工作摘要。");
          const resultNode = card.querySelector("[data-team-result]");
          if (resultNode && result) { resultNode.textContent = result; resultNode.hidden = false; }
        });
      }
      await window.PortalAvatarEditor.loadAssets().catch(() => null);
      if (current !== generation) return;
      await Promise.all([...root.querySelectorAll("[data-avatar-id]")].map(async slot => {
        const avatar = await window.PortalAvatarEditor.read(slot.dataset.avatarKind, slot.dataset.avatarId).catch(() => null);
        if (avatar && current === generation && slot.isConnected) {
          slot.innerHTML = common().avatarHtml(slot.dataset.avatarLabel, avatar, Number(slot.dataset.avatarSize) || 40);
        }
      }));
    }

    async function fillResources(current, counted) {
      const result = await counted.catch(() => null);
      if (current !== generation || !data) return;
      data.resources_state = result ? "ready" : "failed";
      if (result) Object.assign(data, { resource_counts: result.resource_counts, recent_changes: result.recent_changes });
      for (const [selector, html] of [[".projectStats", statsHtml()], ["#projectResources", resourcesHtml()], ["#projectRecent", recentHtml()]]) {
        const node = root.querySelector(selector);
        if (node) node.outerHTML = html;
      }
    }

    async function load(id, { statuses } = {}) {
      const current = ++generation;
      editing = false;
      if (Array.isArray(statuses)) statusVocab = statuses;
      root.innerHTML = window.PortalUIPrimitives.asyncStateHtml({ state: "loading", title: t("正在读取项目…"), message: "" });
      // The registry, summary and team come back in a fraction of a second and
      // the page shows them; counting the Project's resources takes a second or
      // more, so the resources and recent changes fill in when it is done.
      const counted = common().execute("GetProject", { project_id: id });
      counted.catch(() => null);
      try {
        const [result, researchMap] = await Promise.all([
          common().execute("GetProject", { project_id: id, include_resources: false }),
          // Without the Projects feature's map the card offers to start one.
          common().execute("GetResearchMap", { project_id: id, include_outputs: false }).catch(() => null),
        ]);
        if (current !== generation) return null;
        data = { ...result, research_map: researchMap, resources_state: "loading" };
        if (result.status_vocab?.length) statusVocab = result.status_vocab;
        render();
        void loadTeamLive(current);
        void fillResources(current, counted);
        return data;
      } catch (error) {
        if (current !== generation) return null;
        data = null;
        root.innerHTML = window.PortalUIPrimitives.asyncStateHtml({
          state: "error",
          title: error?.code === "instruction.not_found" ? t("项目不存在，或你没有读取它的权限") : t("项目读取失败"),
          message: error?.message || String(error),
          actionsHtml: window.PortalI18n.html`<a class="portalButton is-compact" href="/projects" data-home-list>返回项目列表</a>`,
        });
        throw error;
      }
    }

    async function changeStatus(select) {
      const before = project().status;
      const after = select.value;
      if (before === after) return;
      select.disabled = true;
      try {
        await common().execute("UpdateProject", { project_id: projectId(), changes: { status: after } }, "project-status");
        data.project.status = after;
        render();
        onChanged?.();
      } catch (error) {
        select.value = before;
        window.PortalUI.toast(window.PortalI18n.text`保存失败：${error.message || error}`, "error");
      } finally {
        select.disabled = false;
      }
    }

    async function saveSummary(button) {
      const editor = root.querySelector("[data-summary-editor]");
      const text = editor.value;
      button.disabled = true;
      try {
        await common().execute("UpdateProject", { project_id: projectId(), changes: { summary: text } }, "project-summary");
        data.summary = { ...(data.summary || {}), exists: true, text };
        editing = false;
        rerenderSummary();
      } catch (error) {
        button.disabled = false;
        window.PortalUI.toast(window.PortalI18n.text`保存失败：${error.message || error}`, "error");
      }
    }

    function setMenu(open) {
      menuOpen = open;
      const list = root.querySelector("[data-home-menu-list]");
      const button = root.querySelector("[data-home-menu]");
      if (!list || !button) return;
      list.hidden = !open;
      button.setAttribute("aria-expanded", String(open));
      if (open) list.querySelector("button")?.focus();
    }

    function act(action) {
      setMenu(false);
      const settings = window.PortalProjectSettings;
      const item = { ...project(), project_id: projectId() };
      if (action === "edit") {
        void settings.edit(item, { statusVocab, onSaved: () => { void load(projectId()); onChanged?.(); } });
      } else if (action === "rename") {
        void settings.rename(item, { onRenamed: next => onRenamed?.(next) });
      } else if (action === "delete") {
        void settings.remove(item, { team: team(), onDeleted: () => onDeleted?.(projectId()) });
      }
    }

    root.addEventListener("click", event => {
      const list = event.target.closest("[data-home-list]");
      if (list && common().plainClick(event)) { event.preventDefault(); onOpenList?.(); return; }
      const map = event.target.closest("[data-home-map]");
      if (map && common().plainClick(event)) { event.preventDefault(); onOpenMap?.(projectId()); return; }
      if (event.target.closest("[data-home-menu]")) { setMenu(!menuOpen); return; }
      const action = event.target.closest("[data-home-action]");
      if (action) { act(action.dataset.homeAction); return; }
      if (menuOpen && !event.target.closest("[data-home-menu-list]")) setMenu(false);
      if (event.target.closest("[data-home-copy]")) {
        void window.PortalUI.copyText(new URL(common().projectHref(projectId()), window.location.origin).href)
          .then(() => window.PortalUI.toast(t("已复制项目链接")));
        return;
      }
      if (event.target.closest("[data-summary-edit]")) { editing = true; rerenderSummary(); return; }
      if (event.target.closest("[data-summary-cancel]")) { editing = false; rerenderSummary(); return; }
      const save = event.target.closest("[data-summary-save]");
      if (save) void saveSummary(save);
    });
    root.addEventListener("change", event => {
      if (event.target.matches("[data-home-status]")) void changeStatus(event.target);
    });
    root.addEventListener("keydown", event => {
      if (event.key === "Escape" && menuOpen) { setMenu(false); root.querySelector("[data-home-menu]")?.focus(); }
    });
    document.addEventListener("click", event => {
      if (menuOpen && !root.contains(event.target)) setMenu(false);
    });

    return {
      root,
      load,
      render,
      project: () => (data ? { ...project(), project_id: projectId() } : null),
      team: () => (data ? team() : null),
      hasUnsavedChanges() {
        const editor = root.querySelector("[data-summary-editor]");
        return Boolean(editing && editor && editor.value !== String(data?.summary?.text || ""));
      },
      snapshot: () => (data ? JSON.parse(JSON.stringify(data)) : null),
    };
  }

  window.PortalProjectHome = Object.freeze({ create });
})();
