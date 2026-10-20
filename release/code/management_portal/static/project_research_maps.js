(function (global) {
  "use strict";

  const esc = global.PortalUIPrimitives.escapeHtml;
  const t = key => global.PortalI18n.t(key);
  const html = global.PortalI18n.html;
  const statuses = {
    candidate: "候选", planned: "已规划", doing: "进行中", done: "已完成",
    paused: "已暂停", open: "待完成", active: "待完成", finished: "已完成", dismiss: "已取消", obsolete: "已过时",
  };
  const label = status => t(statuses[status] || status || "未指定");

  function create({ root, pageState, openPreview }) {
    let projectId = "";
    let maps = null;
    let selectedId = "";
    let request = null;
    let generation = 0;

    function reset(id) {
      generation += 1;
      projectId = id;
      maps = null;
      request = null;
      selectedId = pageState.read().researchMapByProject?.[id] || "";
      root.replaceChildren();
    }

    function render() {
      const selected = maps.find(map => map.id === selectedId) || maps[0];
      if (!selected) {
        global.PortalUIPrimitives.renderAsyncState(root, {
          state: "empty", message: t("这个项目还没有 Research Map。"),
        });
        return;
      }
      selectedId = selected.id;
      const directions = selected.directions || [];
      const taskCount = new Set(directions.flatMap(direction => direction.tasks.map(task => task.todo_id))).size;
      const counts = [["Topic", 1], ["Direction", directions.length], ["Task", taskCount],
        ["Synthesis", directions.filter(direction => direction.status === "done").length]];
      root.innerHTML = html`<div class="researchMapView">
        <div class="researchMapToolbar">
          <div><div class="researchMapTitle">${esc(t("Research Map"))}</div>
            <div class="researchMapSub">${esc(projectId)} · ${esc(selected.id)}</div></div>
          ${maps.length > 1 ? html`<select class="formInput formSelect researchMapSelect" aria-label="${esc(t("选择 Research Map"))}">
            ${maps.map(map => html`<option value="${esc(map.id)}" ${map.id === selectedId ? "selected" : ""}>${esc(map.topic_title)}</option>`).join("")}
          </select>` : ""}
        </div>
        <div class="researchFlow">${counts.map(([name, value]) => html`<div class="researchFlowStep" data-research-count="${esc(name)}">
          <div class="researchFlowLabel">${esc(t(name))}</div><div class="researchFlowValue">${value}</div></div>`).join("")}</div>
        <div class="researchAtlas">
          <button class="researchTopicHub" type="button" data-research-resource="${esc(selected.id)}">
            <span class="researchChip">${esc(t("Topic"))}</span>
            <h3 class="researchTopicTitle">${esc(selected.topic_title)}</h3>
            <p class="researchTopicText">${esc(selected.summary)}</p>
            ${selected.topic ? html`<p class="researchTopicText">${esc(selected.topic)}</p>` : ""}
            ${selected.background ? html`<div class="researchTopicBoundary"><strong>${esc(t("背景和边界"))}</strong><p>${esc(selected.background)}</p></div>` : ""}
          </button>
          <div class="researchDirections">${directions.map(direction => html`<article class="researchDirectionCard" data-status="${esc(direction.status)}">
            <div class="researchDirectionHead"><h3 class="researchDirectionTitle">${esc(direction.key)}: ${esc(direction.title)}</h3>
              <span class="researchChip directionStatus">${esc(label(direction.status))}</span></div>
            <p class="researchDirectionDesc">${esc(direction.description)}</p>
            <div class="researchTaskLinks">${direction.tasks.length ? direction.tasks.map(task => html`<button class="researchTaskLink" type="button" data-research-resource="${esc(task.todo_id)}">
              <span class="researchTaskTitle">${esc(task.title)}</span>
              <span class="researchTaskMeta"><span>${esc(task.todo_id)}</span><span>${esc(label(task.status))}</span></span>
            </button>`).join("") : html`<div class="researchNoTask">${esc(t("尚无研究任务"))}</div>`}</div>
          </article>`).join("")}</div>
        </div>
      </div>`;
    }

    async function show() {
      if (!projectId) return;
      if (maps) { render(); return; }
      if (request) return request;
      const current = generation;
      global.PortalUIPrimitives.renderAsyncState(root, {
        state: "loading", message: t("正在加载 Research Map。"),
      });
      request = (async () => {
        try {
          const result = await global.PortalAPI.requestResult(
            `/projects/api/research-maps?project=${encodeURIComponent(projectId)}`,
            { cache: "no-store" },
          );
          const payload = global.PortalAPI.requireJson(result);
          if (!result.response.ok) throw new Error(payload.error || `HTTP ${result.response.status}`);
          if (!Array.isArray(payload.maps)) throw new Error(t("Research Map 数据格式无效。"));
          if (current !== generation) return;
          maps = payload.maps;
          render();
        } catch (error) {
          if (current !== generation) return;
          global.PortalUIPrimitives.renderAsyncState(root, {
            state: "error", title: t("Research Map 加载失败"), message: String(error.message || error),
          });
          const retry = document.createElement("button");
          retry.type = "button";
          retry.className = "portalButton";
          retry.dataset.researchRetry = "";
          retry.textContent = t("重试");
          root.append(retry);
        } finally {
          if (current === generation) request = null;
        }
      })();
      return request;
    }

    root.addEventListener("change", event => {
      if (!event.target.matches(".researchMapSelect")) return;
      selectedId = event.target.value;
      pageState.write({ researchMapByProject: {
        ...pageState.read().researchMapByProject, [projectId]: selectedId,
      } });
      render();
    });
    root.addEventListener("click", event => {
      if (event.target.closest("[data-research-retry]")) { show(); return; }
      const button = event.target.closest("[data-research-resource]");
      if (button) openPreview({
        source: "project-research-map", locatorKind: "resource",
        resourceAddress: button.dataset.researchResource,
      });
    });
    return { reset, show };
  }

  global.PortalProjectResearchMaps = { create };
})(window);
