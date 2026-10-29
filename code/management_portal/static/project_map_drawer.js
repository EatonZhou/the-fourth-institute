(function () {
  "use strict";

  // The drawer of one research direction (decision_9d15ae2053c4): what it asks
  // and how, the people's stance and expectation, its team, its timeline of
  // progress, decisions and outputs, its relations and its place in the tree.
  // Every change goes back to the map as ApplyResearchMapChanges.
  if (window.PortalProjectMapDrawer) return;

  const common = () => window.PortalProjectCommon;
  const STANCES = Object.freeze({
    focus: { label: "主攻", hint: "集中力量推进" },
    explore: { label: "探索", hint: "小步试探" },
    watch: { label: "观望", hint: "暂不投入，保持关注" },
    candidate: { label: "候选", hint: "等你决定是否接纳" },
    concluded: { label: "已收束", hint: "问题已有结论" },
    dropped: { label: "放弃", hint: "不再推进" },
  });
  const RELATIONS = Object.freeze({ builds_on: "建立在……之上", alternative: "互为替代", depends_on: "依赖" });
  const NOTE_KINDS = Object.freeze({ progress: "进展", decision: "决定", note: "批注" });

  const t = value => window.PortalI18n.t(value);
  const stanceLabel = stance => t(STANCES[stance]?.label || stance);
  // A discarded candidate stays on the map as dropped, so whoever proposed it sees the answer.
  const discardChanges = id => [
    { op: "update_direction", direction_id: id, fields: { stance: "dropped" } },
    { op: "add_note", direction_id: id, kind: "decision", text: t("丢弃了这个候选方向") },
  ];

  function create({ root, apply, assignTeam, formTeam, onClose, onSelect } = {}) {
    if (!root) throw new TypeError("PortalProjectMapDrawer.create requires root");
    const { esc, relativeTime } = common();
    let current = null;
    let context = null;
    let editing = false;

    const directionsById = () => new Map((context?.map.directions || []).map(item => [item.direction_id, item]));
    const titleOf = id => directionsById().get(id)?.title || id;

    function stanceButtons(direction) {
      if (direction.stance === "candidate") {
        return window.PortalI18n.html`<div class="mapStanceRow" role="group" aria-label="接纳候选方向">
          <span class="mapCandidateNote">${esc(direction.proposal_reason || t("Resident 提出的候选方向"))}</span>
          <button type="button" class="portalButton is-compact is-primary" data-stance="focus">接纳为主攻</button>
          <button type="button" class="portalButton is-compact" data-stance="explore">接纳为探索</button>
          <button type="button" class="portalButton is-compact" data-stance="watch">先观望</button>
          <button type="button" class="portalButton is-compact" data-discard>丢弃</button>
        </div>`;
      }
      const choices = ["focus", "explore", "watch"].map(stance => `<button type="button" class="mapStanceButton${direction.stance === stance ? " is-active" : ""}" data-stance="${stance}" data-stance-tone="${stance}" aria-pressed="${direction.stance === stance}" title="${esc(t(STANCES[stance].hint))}">${esc(stanceLabel(stance))}</button>`).join("");
      return window.PortalI18n.html`<div class="mapStanceRow" role="group" aria-label="方向的姿态">
        <div class="mapStanceGroup">${choices}</div>
        <button type="button" class="mapStanceButton${direction.stance === "concluded" ? " is-active" : ""}" data-conclude data-stance-tone="concluded">收束…</button>
        <button type="button" class="mapStanceButton${direction.stance === "dropped" ? " is-active" : ""}" data-stance="dropped" data-stance-tone="dropped">放弃</button>
      </div>`;
    }

    function fieldsHtml(direction) {
      if (editing) {
        return window.PortalI18n.html`<form class="mapEditForm" data-edit-form>
          <label class="portalField"><span>名称</span><input name="title" value="${esc(direction.title)}" required maxlength="200" /></label>
          <label class="portalField"><span>要回答的问题</span><textarea name="question" rows="3">${esc(direction.question)}</textarea></label>
          <label class="portalField"><span>打法</span><textarea name="approach" rows="3">${esc(direction.approach)}</textarea></label>
          <label class="portalField"><span>我对团队的期望</span><textarea name="expectation" rows="3">${esc(direction.expectation)}</textarea></label>
          <div class="mapEditActions"><button type="button" class="portalButton is-compact" data-edit-cancel>取消</button>
            <button type="submit" class="portalButton is-compact is-primary">保存</button></div>
        </form>`;
      }
      const block = (label, text, empty) => `<div class="mapField"><h4>${esc(t(label))}</h4><p${text ? "" : ' class="is-empty"'}>${esc(text || t(empty))}</p></div>`;
      return `${block("要回答的问题", direction.question, "还没有写下问题")}${block("打法", direction.approach, "还没有写下打法")}${block("我对团队的期望", direction.expectation, "还没有写下期望")}`;
    }

    function teamHtml(direction) {
      const lineages = context.teams;
      const assigned = direction.teams.map(id => lineages.find(item => item.lineage_id === id) || { lineage_id: id, title: id });
      const free = lineages.filter(item => !direction.teams.includes(item.lineage_id));
      const rows = assigned.map(lineage => {
        const state = context.states.get(lineage.leader_resident_id) || {};
        const summary = context.summaries.get(lineage.leader_resident_id)?.summary || {};
        const focus = (Array.isArray(summary.current_focus) ? summary.current_focus[0] : summary.current_focus) || "";
        const result = Array.isArray(summary.recent_results) ? summary.recent_results[0] : "";
        return `<li class="mapTeam">
          <div class="mapTeamHead"><a href="${esc(common().lineageHref(lineage.lineage_id))}"><strong>${esc(lineage.title || lineage.lineage_id)}</strong></a>
            ${state.work_state === "running" ? `<span class="mapTeamLive"><i class="projectPulse" aria-hidden="true"></i>${esc(t("正在工作"))}</span>` : ""}
            <button type="button" class="mapIconButton" data-team-remove="${esc(lineage.lineage_id)}" aria-label="${esc(t("不再由它负责"))}" title="${esc(t("不再由它负责"))}">×</button></div>
          ${focus ? `<p class="mapTeamFocus">${esc(focus)}</p>` : ""}
          ${result ? `<p class="mapTeamResult">${esc(result)}</p>` : ""}
        </li>`;
      }).join("");
      return window.PortalI18n.html`<section class="mapSection"><h4>负责团队</h4>
        ${rows ? `<ul class="mapTeams">${rows}</ul>` : `<p class="is-empty">${esc(t("还没有团队负责这个方向。"))}</p>`}
        <div class="mapTeamActions">
          ${free.length ? `<select data-team-pick aria-label="${esc(t("选择 Lineage"))}"><option value="">${esc(t("交给已有团队…"))}</option>${free.map(item => `<option value="${esc(item.lineage_id)}">${esc(item.title || item.lineage_id)}</option>`).join("")}</select>` : ""}
          <button type="button" class="portalButton is-compact" data-form-team>组建新团队</button>
        </div>
      </section>`;
    }

    function timelineHtml(direction) {
      const notes = (context.map.notes || []).filter(note => note.direction_id === direction.direction_id)
        .map(note => ({ at: note.noted_at, kind: note.kind, label: t(NOTE_KINDS[note.kind] || note.kind), text: note.text, by: note.noted_by, refs: note.refs || [] }));
      const outputs = (context.map.outputs?.[direction.direction_id] || [])
        .map(item => ({ at: item.modified_at, kind: "output", label: common().resourceLabel(item.resource_type), text: item.title, url: item.url, refs: [] }));
      const entries = [...notes, ...outputs].sort((left, right) => String(right.at).localeCompare(String(left.at)));
      const items = entries.map(entry => `<li class="mapTimelineItem" data-kind="${esc(entry.kind)}">
          <span class="mapTimelineDot" aria-hidden="true"></span>
          <div><div class="mapTimelineMeta"><span class="mapTimelineKind">${esc(entry.label)}</span>
            <time datetime="${esc(entry.at)}" title="${esc(entry.at)}">${esc(relativeTime(entry.at))}</time>${entry.by ? `<span>${esc(entry.by)}</span>` : ""}</div>
          ${entry.url ? `<a href="${esc(entry.url)}">${esc(entry.text)}</a>` : `<p>${esc(entry.text)}</p>`}
          ${entry.refs.length ? `<div class="mapRefs">${entry.refs.map(ref => `<a href="/r/${encodeURIComponent(ref)}">${esc(ref)}</a>`).join("")}</div>` : ""}</div>
        </li>`).join("");
      return window.PortalI18n.html`<section class="mapSection"><h4>时间线</h4>
        <form class="mapNoteForm" data-note-form><input name="text" placeholder="写一条批注，团队和 Project Resident 都会看到" autocomplete="off" />
          <button type="submit" class="portalButton is-compact">记下</button></form>
        ${items ? `<ol class="mapTimeline">${items}</ol>` : `<p class="is-empty">${esc(t("还没有进展、决定或产出。团队在知识条目的 links.related 写上这个方向的 ID，产出就会出现在这里。"))}</p>`}
      </section>`;
    }

    function relationsHtml(direction) {
      const relations = (context.map.relations || []).filter(item => item.from === direction.direction_id || item.to === direction.direction_id);
      const others = (context.map.directions || []).filter(item => item.direction_id !== direction.direction_id);
      const describe = relation => relation.from === direction.direction_id
        ? window.PortalI18n.text`${t(RELATIONS[relation.kind])}「${titleOf(relation.to)}」`
        : window.PortalI18n.text`「${titleOf(relation.from)}」${t(RELATIONS[relation.kind])}这个方向`;
      return window.PortalI18n.html`<section class="mapSection"><h4>关系</h4>
        ${relations.length ? `<ul class="mapRelations">${relations.map(relation => `<li><button type="button" class="mapLink" data-select="${esc(relation.from === direction.direction_id ? relation.to : relation.from)}">${esc(describe(relation))}</button>
          <button type="button" class="mapIconButton" data-relation-remove='${esc(JSON.stringify(relation))}' aria-label="${esc(t("去掉这条关系"))}">×</button></li>`).join("")}</ul>` : ""}
        ${others.length ? `<form class="mapRelationForm" data-relation-form>
          <select name="kind">${Object.entries(RELATIONS).map(([kind, label]) => `<option value="${kind}">${esc(t(label))}</option>`).join("")}</select>
          <select name="to"><option value="">${esc(t("选择方向"))}</option>${others.map(item => `<option value="${esc(item.direction_id)}">${esc(item.title)}</option>`).join("")}</select>
          <button type="submit" class="portalButton is-compact">${esc(t("连接"))}</button></form>` : ""}
      </section>`;
    }

    function structureHtml(direction) {
      const descendants = new Set([direction.direction_id]);
      let grew = true;
      while (grew) {
        grew = false;
        for (const item of context.map.directions) {
          if (!descendants.has(item.direction_id) && descendants.has(item.parent_id)) { descendants.add(item.direction_id); grew = true; }
        }
      }
      const parents = context.map.directions.filter(item => !descendants.has(item.direction_id));
      return window.PortalI18n.html`<section class="mapSection"><h4>位置</h4>
        <div class="mapStructure">
          <button type="button" class="portalButton is-compact" data-add-child data-portal-icon="plus"><span>添加子方向</span></button>
          <select data-move aria-label="移到">
            <option value="__root"${direction.parent_id ? "" : " selected"}>${esc(t("顶层方向"))}</option>
            ${parents.map(item => `<option value="${esc(item.direction_id)}"${item.direction_id === direction.parent_id ? " selected" : ""}>${esc(window.PortalI18n.text`在「${item.title}」之下`)}</option>`).join("")}
          </select>
          <button type="button" class="portalButton is-compact is-danger" data-remove>删除方向</button>
        </div>
      </section>`;
    }

    function render() {
      if (!current || !context) { root.hidden = true; root.replaceChildren(); return; }
      const direction = directionsById().get(current);
      if (!direction) { close(); return; }
      root.hidden = false;
      const conclusion = direction.conclusion?.text
        ? `<div class="mapConclusion"><h4>${esc(t("结论"))}</h4><p>${esc(direction.conclusion.text)}</p><small>${esc(`${direction.conclusion.by} · ${relativeTime(direction.conclusion.at)}`)}</small></div>` : "";
      root.innerHTML = window.PortalI18n.html`<article class="mapDrawer" data-stance="${esc(direction.stance)}">
        <header class="mapDrawerHead">
          <span class="mapStanceBadge" data-stance-tone="${esc(direction.stance)}">${esc(stanceLabel(direction.stance))}</span>
          <h3>${esc(direction.title)}</h3>
          <button type="button" class="mapIconButton" data-edit aria-label="编辑方向" title="编辑方向" data-portal-icon="edit"></button>
          <button type="button" class="mapIconButton" data-close aria-label="关闭" data-portal-icon="close"></button>
        </header>
        <div class="mapDrawerBody">
          ${stanceButtons(direction)}
          ${conclusion}
          ${fieldsHtml(direction)}
          ${teamHtml(direction)}
          ${timelineHtml(direction)}
          ${relationsHtml(direction)}
          ${structureHtml(direction)}
          <p class="mapDrawerMeta">${esc(window.PortalI18n.text`${direction.proposed_by} 提出 · ${relativeTime(direction.created_at)}；最近由 ${direction.updated_by} 修改`)}</p>
        </div>
      </article>`;
      window.PortalUIPrimitives.mountIcons(root);
    }

    function close() {
      current = null;
      editing = false;
      render();
      onClose?.();
    }

    async function decide(direction, stance) {
      const changes = [{ op: "update_direction", direction_id: direction.direction_id, fields: { stance } }];
      changes.push({ op: "add_note", direction_id: direction.direction_id, kind: "decision",
        text: direction.stance === "candidate" ? window.PortalI18n.text`接纳为${stanceLabel(stance)}` : window.PortalI18n.text`设为${stanceLabel(stance)}` });
      await apply(changes);
    }

    async function conclude(direction) {
      const text = await window.PortalUI.prompt({ title: t("收束这个方向"), message: t("写下这个方向得出的结论，团队与 Project Resident 都会看到。"),
        label: t("结论"), value: direction.conclusion?.text || "", multiline: true, confirmLabel: t("收束") });
      if (text === null || !String(text).trim()) return;
      await apply([
        { op: "update_direction", direction_id: direction.direction_id, fields: { stance: "concluded", conclusion: String(text).trim() } },
        { op: "add_note", direction_id: direction.direction_id, kind: "decision", text: window.PortalI18n.text`收束：${String(text).trim()}` },
      ]);
    }

    root.addEventListener("click", async event => {
      const direction = directionsById().get(current);
      if (!direction) return;
      const target = event.target;
      try {
        if (target.closest("[data-close]")) return close();
        if (target.closest("[data-edit]")) { editing = !editing; return render(); }
        if (target.closest("[data-edit-cancel]")) { editing = false; return render(); }
        const select = target.closest("[data-select]");
        if (select) return onSelect?.(select.dataset.select);
        const stance = target.closest("[data-stance]");
        if (stance && stance.dataset.stance !== direction.stance) return await decide(direction, stance.dataset.stance);
        if (target.closest("[data-conclude]")) return await conclude(direction);
        if (target.closest("[data-discard]")) return await apply(discardChanges(direction.direction_id));
        const removeTeam = target.closest("[data-team-remove]");
        if (removeTeam) {
          return await apply([{ op: "update_direction", direction_id: direction.direction_id,
            fields: { teams: direction.teams.filter(id => id !== removeTeam.dataset.teamRemove) } }]);
        }
        if (target.closest("[data-form-team]")) return await formTeam?.(direction);
        const removeRelation = target.closest("[data-relation-remove]");
        if (removeRelation) return await apply([{ op: "remove_relation", ...JSON.parse(removeRelation.dataset.relationRemove) }]);
        if (target.closest("[data-add-child]")) {
          const title = await window.PortalUI.prompt({ title: t("添加子方向"), label: t("名称"), confirmLabel: t("添加") });
          if (title && String(title).trim()) {
            await apply([{ op: "add_direction", key: "child", title: String(title).trim(), parent_id: direction.direction_id }], { select: "child" });
          }
          return;
        }
        if (target.closest("[data-remove]")) {
          if (await window.PortalUI.confirm({ title: t("删除这个方向？"), message: t("它的子方向会上移到它的位置；历史修订中仍能看到它。"), confirmLabel: t("删除"), tone: "danger" })) {
            await apply([{ op: "remove_direction", direction_id: direction.direction_id }]);
          }
        }
      } catch (error) {
        window.PortalUI.toast(window.PortalI18n.text`保存失败：${error.message || error}`, "error");
      }
    });
    root.addEventListener("change", async event => {
      const direction = directionsById().get(current);
      if (!direction) return;
      try {
        if (event.target.matches("[data-team-pick]") && event.target.value) {
          const lineage = context.teams.find(item => item.lineage_id === event.target.value);
          if (lineage) await assignTeam?.(direction, lineage);
        }
        if (event.target.matches("[data-move]")) {
          const parent = event.target.value === "__root" ? "" : event.target.value;
          if (parent !== direction.parent_id) await apply([{ op: "move_direction", direction_id: direction.direction_id, parent_id: parent }]);
        }
      } catch (error) {
        window.PortalUI.toast(window.PortalI18n.text`保存失败：${error.message || error}`, "error");
        render();
      }
    });
    root.addEventListener("submit", async event => {
      event.preventDefault();
      const direction = directionsById().get(current);
      if (!direction) return;
      const form = event.target;
      try {
        if (form.matches("[data-edit-form]")) {
          const fields = Object.fromEntries(["title", "question", "approach", "expectation"]
            .map(name => [name, form.elements[name].value.trim()]).filter(([name, value]) => value !== (direction[name] || "")));
          if (Object.keys(fields).length) await apply([{ op: "update_direction", direction_id: direction.direction_id, fields }]);
          editing = false;
          render();
        } else if (form.matches("[data-note-form]")) {
          const text = form.elements.text.value.trim();
          if (text) await apply([{ op: "add_note", direction_id: direction.direction_id, kind: "note", text }]);
        } else if (form.matches("[data-relation-form]")) {
          if (form.elements.to.value) {
            await apply([{ op: "add_relation", from: direction.direction_id, to: form.elements.to.value, kind: form.elements.kind.value }]);
          }
        }
      } catch (error) {
        window.PortalUI.toast(window.PortalI18n.text`保存失败：${error.message || error}`, "error");
      }
    });

    return {
      root,
      open(directionId) { if (current !== directionId) editing = false; current = directionId; render(); },
      close,
      update(next) { context = next; render(); },
      selected: () => current,
      editing: () => editing,
    };
  }

  window.PortalProjectMapDrawer = Object.freeze({ create, STANCES, RELATIONS, discardChanges });
})();
