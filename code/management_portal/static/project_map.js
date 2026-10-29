(function () {
  "use strict";

  // A Project's research map (decision_9d15ae2053c4): the goal at the centre,
  // the directions around it sized by the people's stance and lit by recent
  // work, the teams on them, a drawer to steer one direction, and a timeline
  // that replays how the map grew.  Reads GetResearchMap and the team's live
  // state; every change is ApplyResearchMapChanges; dragged positions are
  // UpdateResearchMapLayout; handing a direction to a team is a message from
  // the user to that team's Leader.
  if (window.PortalProjectMap) return;

  const common = () => window.PortalProjectCommon;
  const layout = () => window.PortalProjectMapLayout;
  const t = value => window.PortalI18n.t(value);
  const STANCE_LABELS = Object.freeze({ focus: "主攻", explore: "探索", watch: "观望", candidate: "候选", concluded: "已收束", dropped: "放弃" });
  const ZOOM = Object.freeze({ min: 0.25, max: 2.2 });

  function create({ root, onBack, onSelectionChange, onTeam } = {}) {
    if (!root) throw new TypeError("PortalProjectMap.create requires root");
    const { esc, execute, avatarHtml, relativeTime } = common();
    const state = {
      projectId: "", project: null, team: null, map: null, states: new Map(), summaries: new Map(),
      view: { x: 0, y: 0, scale: 1 }, showDropped: false, generation: 0, replay: null, live: true,
    };
    let drawer = null;
    let reloadTimer = 0;

    const teams = () => {
      const current = state.team || { lineages: [] };
      const lineages = [...(current.lineages || [])];
      if (current.project_lineage_id) {
        lineages.unshift({ lineage_id: current.project_lineage_id, title: t("Project 层团队"), leader_resident_id: current.project_resident_id });
      }
      return lineages;
    };
    const shownMap = () => state.replay ? state.replay.map : state.map;

    // -- rendering -------------------------------------------------------------------

    function nodeHtml(direction, position, latest) {
      const lineages = teams();
      const assigned = direction.teams.map(id => lineages.find(item => item.lineage_id === id)).filter(Boolean);
      const running = assigned.some(item => state.states.get(item.leader_resident_id)?.work_state === "running");
      const outputs = (shownMap().outputs || {})[direction.direction_id] || [];
      const notes = (shownMap().notes || []).filter(note => note.direction_id === direction.direction_id);
      const summary = assigned.map(item => state.summaries.get(item.leader_resident_id)?.summary).find(Boolean) || {};
      const result = Array.isArray(summary.recent_results) ? summary.recent_results[0] : "";
      const line = direction.conclusion?.text || notes.find(note => note.kind === "progress")?.text || result || direction.question || "";
      const heat = layout().heat([notes[0]?.noted_at, outputs[0]?.modified_at, direction.updated_at, running ? new Date().toISOString() : ""]);
      const avatars = assigned.slice(0, 3).map(item => `<span class="mapNodeTeam" title="${esc(item.title)}">${avatarHtml(item.title, null, 22)}</span>`).join("");
      const selected = drawer?.selected() === direction.direction_id;
      return `<div class="mapNode${selected ? " is-selected" : ""}${running ? " is-running" : ""}${latest.has(direction.direction_id) ? " is-new" : ""}"
          data-node="${esc(direction.direction_id)}" data-stance="${esc(direction.stance)}" tabindex="0" role="button"
          aria-label="${esc(`${t(STANCE_LABELS[direction.stance])} · ${direction.title}`)}"
          style="left:${position.x}px;top:${position.y}px;--heat:${heat.toFixed(2)}">
        <div class="mapNodeHead"><span class="mapNodeStance">${esc(t(STANCE_LABELS[direction.stance]))}</span>${outputs.length ? `<span class="mapNodeCount" title="${esc(t("产出"))}">${outputs.length}</span>` : ""}</div>
        <strong class="mapNodeTitle">${esc(direction.title)}</strong>
        ${line ? `<p class="mapNodeLine">${esc(line)}</p>` : ""}
        ${direction.stance === "candidate" ? `<div class="mapNodeCandidate"><button type="button" data-accept="${esc(direction.direction_id)}">${esc(t("接纳"))}</button><button type="button" data-discard="${esc(direction.direction_id)}">${esc(t("丢弃"))}</button></div>` : ""}
        ${avatars || running ? `<div class="mapNodeFoot">${avatars}${running ? `<span class="mapNodeLive"><i class="projectPulse" aria-hidden="true"></i>${esc(t("正在工作"))}</span>` : ""}</div>` : ""}
      </div>`;
    }

    function edgesHtml(directions, positions) {
      const curve = (from, to) => {
        const midX = (from.x + to.x) / 2;
        const midY = (from.y + to.y) / 2;
        // Bend away from the centre so the tree reads as rings.
        const bend = 0.12;
        return `M${from.x},${from.y} Q${midX - (to.y - from.y) * bend},${midY + (to.x - from.x) * bend} ${to.x},${to.y}`;
      };
      const tree = directions.map(item => {
        const from = item.parent_id && positions[item.parent_id] ? positions[item.parent_id] : { x: 0, y: 0 };
        const to = positions[item.direction_id];
        return to ? `<path class="mapEdge" data-stance="${esc(item.stance)}" d="${curve(from, to)}" />` : "";
      }).join("");
      const relations = (shownMap().relations || []).map(relation => {
        const from = positions[relation.from];
        const to = positions[relation.to];
        return from && to ? `<path class="mapRelation" data-kind="${esc(relation.kind)}" d="${curve(from, to)}" marker-end="url(#mapArrow)"><title>${esc(t({ builds_on: "建立在……之上", alternative: "互为替代", depends_on: "依赖" }[relation.kind]))}</title></path>` : "";
      }).join("");
      return `<svg class="mapEdges" aria-hidden="true"><defs><marker id="mapArrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" /></marker></defs>${tree}${relations}</svg>`;
    }

    function toolbarHtml(directions) {
      const counts = layout().counts(directions);
      const map = shownMap();
      const summary = ["focus", "explore", "candidate", "watch", "concluded"].filter(stance => counts[stance])
        .map(stance => `<span class="mapCount" data-stance-tone="${stance}">${esc(t(STANCE_LABELS[stance]))} ${counts[stance]}</span>`).join("");
      const revision = map.revision
        ? window.PortalI18n.text`修订 ${map.revision} · ${relativeTime(map.updated_at)} · ${map.updated_by}`
        : t("还没有修订");
      return window.PortalI18n.html`<header class="mapToolbar">
        <a class="mapBack" href="${esc(common().projectHref(state.projectId))}" data-map-back>← ${esc(state.projectId)}</a>
        <div class="mapTitle"><h1>研究地图</h1><span class="mapRevision">${esc(revision)}</span></div>
        <div class="mapCounts">${summary}</div>
        <div class="mapActions">
          ${state.replay ? "" : `<button type="button" class="portalButton is-compact is-primary" data-map-add data-portal-icon="plus"><span>${esc(t("新方向"))}</span></button>`}
          <label class="mapToggle"><input type="checkbox" data-map-dropped${state.showDropped ? " checked" : ""} /> ${esc(t("显示放弃的方向"))}</label>
          <button type="button" class="portalButton is-compact" data-map-fit data-portal-icon="fit-view"><span>${esc(t("适应视图"))}</span></button>
          <button type="button" class="portalButton is-compact${state.replay ? " is-primary" : ""}" data-map-replay data-portal-icon="history" aria-pressed="${Boolean(state.replay)}"><span>${esc(t(state.replay ? "回到现在" : "回放"))}</span></button>
        </div>
      </header>`;
    }

    function replayHtml() {
      const replay = state.replay;
      if (!replay) return "";
      const entries = replay.entries;
      const current = entries[replay.index];
      const titles = new Map(entries.flatMap(entry => entry.directions.map(item => [item.direction_id, item.title])));
      const ticks = entries.map((entry, index) => `<button type="button" class="mapTick${index === replay.index ? " is-current" : ""}${entry.changes.some(change => change.op === "add_note" && change.kind === "decision") ? " is-decision" : ""}" data-replay-index="${index}" style="left:${entries.length > 1 ? (index / (entries.length - 1)) * 100 : 50}%" title="${esc(window.PortalI18n.text`修订 ${entry.revision}`)}"></button>`).join("");
      return window.PortalI18n.html`<footer class="mapReplay" aria-label="回放">
        <button type="button" class="portalButton is-compact" data-replay-play data-portal-icon="${replay.playing ? "stop" : "play"}"><span>${esc(t(replay.playing ? "暂停" : "播放"))}</span></button>
        <div class="mapReplayTrack"><div class="mapReplayLine"></div>${ticks}</div>
        <div class="mapReplayInfo"><strong>${esc(window.PortalI18n.text`修订 ${current?.revision ?? 0}`)}</strong>
          <span>${esc(current ? `${current.changed_by} · ${relativeTime(current.changed_at)}` : "")}</span>
          <p>${esc(current ? layout().describe(current, id => titles.get(id)) : "")}</p></div>
      </footer>`;
    }

    function render() {
      const map = shownMap();
      if (!map) return;
      const directions = layout().visible(map.directions || [], { showDropped: state.showDropped });
      const positions = layout().radial(directions, state.replay ? {} : map.layout || {});
      const latest = state.replay ? new Set(state.replay.entries[state.replay.index]?.changes
        .filter(change => change.op === "add_direction").map(change => change.direction_id)) : new Set();
      const goal = map.goal || state.project?.description || state.projectId;
      const empty = !directions.length ? window.PortalI18n.html`<div class="mapEmpty"><h2>开始绘制研究地图</h2>
          <p>写下这个项目要攻的几条路线：每条一个方向，主攻、探索或观望由你决定。也可以在右侧请 Project Resident 根据项目的知识起草一版候选方向。</p>
          <button type="button" class="portalButton is-primary" data-map-add data-portal-icon="plus"><span>提出第一个方向</span></button></div>` : "";
      root.innerHTML = window.PortalI18n.html`<section class="projectMap${state.replay ? " is-replay" : ""}">
        ${toolbarHtml(directions)}
        <div class="mapStage">
          <div class="mapViewport" data-map-viewport>
            <div class="mapWorld" data-map-world style="transform:translate(${state.view.x}px,${state.view.y}px) scale(${state.view.scale})">
              ${edgesHtml(directions, positions)}
              <div class="mapGoal" data-map-goal>${avatarHtml(state.projectId, state.project?.avatar, 56)}<p>${esc(goal)}</p></div>
              ${directions.map(item => nodeHtml(item, positions[item.direction_id], latest)).join("")}
            </div>
            ${empty}
          </div>
          <aside class="mapDrawerHost" data-map-drawer hidden></aside>
        </div>
        ${replayHtml()}
      </section>`;
      window.PortalUIPrimitives.mountIcons(root);
      const host = root.querySelector("[data-map-drawer]");
      const previous = drawer?.selected();
      drawer = window.PortalProjectMapDrawer.create({
        root: host, apply, assignTeam, formTeam,
        onClose: () => { markSelection(); onSelectionChange?.(null); },
        onSelect: id => select(id),
      });
      drawer.update({ map, teams: teams(), states: state.states, summaries: state.summaries });
      if (previous && !state.replay && directions.some(item => item.direction_id === previous)) drawer.open(previous);
      markSelection();
    }

    function markSelection() {
      root.querySelectorAll("[data-node]").forEach(node => node.classList.toggle("is-selected", node.dataset.node === drawer?.selected()));
    }

    function applyView() {
      const world = root.querySelector("[data-map-world]");
      if (world) world.style.transform = `translate(${state.view.x}px,${state.view.y}px) scale(${state.view.scale})`;
    }

    function fit() {
      const viewport = root.querySelector("[data-map-viewport]");
      const map = shownMap();
      if (!viewport || !map) return;
      const directions = layout().visible(map.directions || [], { showDropped: state.showDropped });
      const box = layout().bounds(layout().radial(directions, state.replay ? {} : map.layout || {}));
      const width = viewport.clientWidth || 1000;
      const height = viewport.clientHeight || 700;
      const scale = Math.max(ZOOM.min, Math.min(1.1, Math.min(width / (box.maxX - box.minX), height / (box.maxY - box.minY))));
      state.view = { scale, x: width / 2 - ((box.minX + box.maxX) / 2) * scale, y: height / 2 - ((box.minY + box.maxY) / 2) * scale };
      applyView();
    }

    // -- data ------------------------------------------------------------------------

    async function loadLive(current) {
      const leaders = [...new Set(teams().filter(item => state.map.directions.some(direction => direction.teams.includes(item.lineage_id)))
        .map(item => item.leader_resident_id).filter(Boolean))].slice(0, 128);
      if (!leaders.length) return;
      const [states, summaries] = await Promise.all([
        execute("ListResidentStates", { resident_ids: leaders, include_viewer_state: false }).catch(() => ({ states: [] })),
        execute("GetResidentSummaries", { resident_ids: leaders, fields: ["current_focus", "recent_results"] }).catch(() => ({ summaries: [] })),
      ]);
      if (current !== state.generation) return;
      state.states = new Map((states.states || []).map(item => [item.resident_id, item.state || {}]));
      state.summaries = new Map((summaries.summaries || []).map(item => [item.resident_id, item]));
      if (!state.replay) render();
    }

    // The directions come back at once and are drawn.  The team (GetProject
    // without its resource count) and the outputs (a search over the Project's
    // resources) take a second or more and fill in when they arrive.
    async function load(projectId, { selected = "" } = {}) {
      const current = ++state.generation;
      const first = state.projectId !== projectId;
      state.projectId = projectId;
      if (first) {
        state.replay = null;
        state.project = null;
        state.team = null;
        root.innerHTML = window.PortalUIPrimitives.asyncStateHtml({ state: "loading", title: t("正在读取研究地图…"), message: "" });
      }
      const project = state.project ? null : execute("GetProject", { project_id: projectId, include_resources: false });
      const complete = execute("GetResearchMap", { project_id: projectId });
      project?.catch(() => null);
      complete.catch(() => null);
      const map = await execute("GetResearchMap", { project_id: projectId, include_outputs: false });
      if (current !== state.generation) return null;
      // Until the outputs arrive the map keeps the ones it showed before.
      state.map = { ...map, outputs: first ? {} : state.map?.outputs || {} };
      if (!state.replay) render();
      if (first) fit();
      if (selected) select(selected);
      if (project) {
        project.then(result => {
          if (current !== state.generation) return;
          state.project = result.project;
          state.team = result.team;
          onTeam?.(result.team);
          if (!state.replay) render();
          void loadLive(current);
        }, () => {});
      } else {
        void loadLive(current);
      }
      complete.then(result => {
        if (current !== state.generation || result.revision < state.map.revision) return;
        state.map = result;
        if (!state.replay) render();
      }, () => {});
      return state.map;
    }

    async function apply(changes, { select: key = "" } = {}) {
      try {
        const result = await execute("ApplyResearchMapChanges", {
          project_id: state.projectId, expected_revision: state.map.revision, changes,
        }, "research-map");
        await load(state.projectId);
        if (key && result.created?.[key]) select(result.created[key]);
        return result;
      } catch (error) {
        if (error?.code === "instruction.version_conflict") {
          await load(state.projectId);
          window.PortalUI.toast(t("地图刚被别人修改过，已刷新，请再操作一次。"), "info");
          return null;
        }
        throw error;
      }
    }

    function brief(direction) {
      const lines = [
        window.PortalI18n.text`研究方向《${direction.title}》（${direction.direction_id}），项目 ${state.projectId} 的研究地图：/projects?direction=${direction.direction_id}`,
        direction.question ? window.PortalI18n.text`要回答的问题：${direction.question}` : "",
        direction.approach ? window.PortalI18n.text`打法：${direction.approach}` : "",
        direction.expectation ? window.PortalI18n.text`我的期望：${direction.expectation}` : "",
      ];
      return lines.filter(Boolean).join("\n");
    }

    // The user hands a direction to a team: the map names the team, and the
    // team's Leader gets the direction from the user, as if typed in its chat.
    async function assignTeam(direction, lineage) {
      await apply([
        { op: "update_direction", direction_id: direction.direction_id, fields: { teams: [...direction.teams, lineage.lineage_id] } },
        { op: "add_note", direction_id: direction.direction_id, kind: "decision", text: window.PortalI18n.text`交给团队「${lineage.title}」` },
      ]);
      if (!lineage.leader_resident_id) return;
      const text = [
        window.PortalI18n.text`请你的团队负责这个研究方向。`, brief(direction), "",
        t("推进时：产出的知识条目在 links.related 写上这个方向的 ID；阶段进展用 ApplyResearchMapChanges 的 add_note（kind 为 progress）记在这个方向上；问题有了答案时在对话里告诉我，由我确认结论。"),
      ].join("\n");
      await execute("DeliverMessage", { resident_id: lineage.leader_resident_id, business_ref: direction.direction_id,
        message: { role: "user", content_parts: [{ type: "text", text }] } }, "research-map-assign");
      window.PortalUI.toast(window.PortalI18n.text`已交给「${lineage.title}」，并把方向发给了它的 Leader`);
    }

    // A new team is formed by the Project Resident, which then names it on the map.
    async function formTeam(direction) {
      const residentId = state.team?.project_resident_id;
      if (!residentId) throw new Error(t("Project Resident 尚未就绪"));
      const text = [
        window.PortalI18n.text`请为这个研究方向组建一个新团队：用 CreateLineage 在项目 ${state.projectId} 中新建一个 Lineage 并设好它的 Leader，再把这个方向以“创建团队：”开头发给这位 Leader，由它在新 Lineage 中组建并启动团队。`,
        brief(direction), "",
        t("团队建好后，用 ApplyResearchMapChanges 把这个 Lineage 登记为该方向的负责团队（update_direction 的 teams），再告诉我团队的成员与分工。"),
      ].join("\n");
      await execute("DeliverMessage", { resident_id: residentId, business_ref: direction.direction_id,
        message: { role: "user", content_parts: [{ type: "text", text }] } }, "research-map-form-team");
      window.PortalUI.toast(t("已请 Project Resident 组建团队，进展会出现在右侧对话中"));
    }

    function select(directionId) {
      if (!drawer || state.replay) return;
      drawer.open(directionId);
      markSelection();
      const direction = state.map.directions.find(item => item.direction_id === directionId);
      onSelectionChange?.(direction || null);
    }

    async function addDirection() {
      const answer = await window.PortalUI.prompt({
        title: t("新方向"), message: t("一条要攻的路线。主攻、探索或观望之后都可以再改。"), label: t("名称"),
        choices: [{ value: "focus", label: t("主攻") }, { value: "explore", label: t("探索") }, { value: "watch", label: t("观望") }],
        choice: "explore", choiceLabel: t("姿态"), confirmLabel: t("添加"),
      });
      const title = String(answer?.value || "").trim();
      if (!title) return;
      await apply([{ op: "add_direction", key: "new", title, stance: answer.choice }], { select: "new" });
    }

    async function startReplay() {
      const entries = [];
      let cursor = "";
      do {
        const page = await execute("ListResearchMapChanges", { project_id: state.projectId, limit: 500, ...(cursor ? { cursor } : {}) });
        entries.push(...(page.changes || []));
        cursor = page.next_cursor || "";
      } while (cursor && entries.length < 5000);
      if (!entries.length) { window.PortalUI.toast(t("这张地图还没有修订可以回放"), "info"); return; }
      drawer?.close();
      state.replay = { entries, index: entries.length - 1, playing: false, timer: 0 };
      showReplayAt(entries.length - 1);
    }

    function showReplayAt(index) {
      const replay = state.replay;
      replay.index = Math.max(0, Math.min(replay.entries.length - 1, index));
      const entry = replay.entries[replay.index];
      replay.map = { revision: entry.revision, updated_at: entry.changed_at, updated_by: entry.changed_by, goal: state.map.goal,
        directions: entry.directions.map(item => ({ ...item, question: "", approach: "", expectation: "", conclusion: null, teams: item.teams || [] })),
        relations: [], layout: {}, notes: [], outputs: {} };
      render();
    }

    function stopReplay() {
      window.clearInterval(state.replay?.timer);
      state.replay = null;
      render();
    }

    function togglePlay() {
      const replay = state.replay;
      if (!replay) return;
      if (replay.playing) {
        window.clearInterval(replay.timer);
        replay.playing = false;
        render();
        return;
      }
      if (replay.index >= replay.entries.length - 1) replay.index = -1;
      replay.playing = true;
      replay.timer = window.setInterval(() => {
        if (!state.replay) return;
        if (replay.index >= replay.entries.length - 1) {
          window.clearInterval(replay.timer);
          replay.playing = false;
          render();
          return;
        }
        showReplayAt(replay.index + 1);
      }, 900);
      showReplayAt(replay.index + 1);
    }

    // -- interaction -----------------------------------------------------------------

    let drag = null;
    root.addEventListener("pointerdown", event => {
      const viewport = event.target.closest("[data-map-viewport]");
      if (!viewport || event.button !== 0 || event.target.closest("button, a, input, select, textarea")) return;
      const node = event.target.closest("[data-node]");
      drag = { node, startX: event.clientX, startY: event.clientY, moved: false,
        origin: node ? { x: parseFloat(node.style.left), y: parseFloat(node.style.top) } : { x: state.view.x, y: state.view.y } };
      viewport.setPointerCapture?.(event.pointerId);
    });
    root.addEventListener("pointermove", event => {
      if (!drag) return;
      const dx = event.clientX - drag.startX;
      const dy = event.clientY - drag.startY;
      if (!drag.moved && Math.hypot(dx, dy) < 4) return;
      drag.moved = true;
      if (drag.node && !state.replay) {
        drag.node.style.left = `${drag.origin.x + dx / state.view.scale}px`;
        drag.node.style.top = `${drag.origin.y + dy / state.view.scale}px`;
      } else if (!drag.node) {
        state.view.x = drag.origin.x + dx;
        state.view.y = drag.origin.y + dy;
        applyView();
      }
    });
    root.addEventListener("pointerup", async () => {
      const finished = drag;
      drag = null;
      if (!finished) return;
      if (!finished.moved) {
        if (finished.node) select(finished.node.dataset.node);
        return;
      }
      if (finished.node && !state.replay) {
        const id = finished.node.dataset.node;
        const position = { x: parseFloat(finished.node.style.left), y: parseFloat(finished.node.style.top) };
        try {
          const result = await execute("UpdateResearchMapLayout", { project_id: state.projectId, positions: { [id]: position } });
          state.map.layout = result.layout;
          render();
        } catch (error) {
          window.PortalUI.toast(window.PortalI18n.text`保存位置失败：${error.message || error}`, "error");
          render();
        }
      }
    });
    root.addEventListener("wheel", event => {
      const viewport = event.target.closest("[data-map-viewport]");
      if (!viewport || event.target.closest("[data-map-drawer]")) return;
      event.preventDefault();
      const rect = viewport.getBoundingClientRect();
      const cx = event.clientX - rect.left;
      const cy = event.clientY - rect.top;
      const next = Math.max(ZOOM.min, Math.min(ZOOM.max, state.view.scale * Math.exp(-event.deltaY * 0.0015)));
      state.view.x = cx - (cx - state.view.x) * (next / state.view.scale);
      state.view.y = cy - (cy - state.view.y) * (next / state.view.scale);
      state.view.scale = next;
      applyView();
    }, { passive: false });
    root.addEventListener("keydown", event => {
      const node = event.target.closest?.("[data-node]");
      if (node && (event.key === "Enter" || event.key === " ")) { event.preventDefault(); select(node.dataset.node); }
      if (event.key === "Escape" && drawer?.selected()) drawer.close();
    });
    root.addEventListener("click", async event => {
      const target = event.target;
      try {
        const back = target.closest("[data-map-back]");
        if (back && common().plainClick(event)) { event.preventDefault(); onBack?.(); return; }
        if (target.closest("[data-map-add]")) return await addDirection();
        if (target.closest("[data-map-fit]")) return fit();
        if (target.closest("[data-map-replay]")) return state.replay ? stopReplay() : await startReplay();
        if (target.closest("[data-replay-play]")) return togglePlay();
        const tick = target.closest("[data-replay-index]");
        if (tick) { window.clearInterval(state.replay.timer); state.replay.playing = false; return showReplayAt(Number(tick.dataset.replayIndex)); }
        const accept = target.closest("[data-accept]");
        if (accept) {
          return await apply([{ op: "update_direction", direction_id: accept.dataset.accept, fields: { stance: "explore" } },
            { op: "add_note", direction_id: accept.dataset.accept, kind: "decision", text: t("接纳为探索") }]);
        }
        const discard = target.closest("[data-discard]");
        if (discard && discard.closest("[data-node]")) return await apply(window.PortalProjectMapDrawer.discardChanges(discard.dataset.discard));
      } catch (error) {
        window.PortalUI.toast(window.PortalI18n.text`保存失败：${error.message || error}`, "error");
      }
    });
    root.addEventListener("change", event => {
      if (event.target.matches("[data-map-dropped]")) { state.showDropped = event.target.checked; render(); }
    });

    // Someone else changed the map: read it again, keeping what is open.
    const onChange = event => {
      const changes = event.detail?.projection_changes?.["research-map"] || {};
      if (!state.projectId || !(state.projectId in changes) || state.replay || drawer?.editing()) return;
      window.clearTimeout(reloadTimer);
      reloadTimer = window.setTimeout(() => void load(state.projectId).catch(() => {}), 400);
    };
    window.addEventListener("portal:change", onChange);

    return {
      root,
      load,
      select,
      selected: () => {
        const id = drawer?.selected();
        return id ? state.map?.directions.find(item => item.direction_id === id) || null : null;
      },
      projectId: () => state.projectId,
      team: () => state.team,
      snapshot: () => JSON.parse(JSON.stringify({ map: state.map, view: state.view, replay: state.replay ? { index: state.replay.index, revision: state.replay.map.revision } : null })),
      destroy() {
        state.generation += 1;
        window.removeEventListener("portal:change", onChange);
        window.clearTimeout(reloadTimer);
        window.clearInterval(state.replay?.timer);
      },
    };
  }

  window.PortalProjectMap = Object.freeze({ create });
})();
