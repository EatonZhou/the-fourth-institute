(function () {
  "use strict";

  function layout(weights, { height, unitArea, minWidth, minHeight, strictHeight = false }) {
    if (!weights.length) return { width: 0, height: 0, tiles: [] };
    if (weights.some(weight => !Number.isInteger(weight) || weight < 1 || weight > 10)) throw new RangeError("metaness 必须是 1–10 的整数");
    if ([height, unitArea, minWidth, minHeight].some(value => !Number.isFinite(value) || value <= 0) || unitArea * 2 / 3 < minWidth * minHeight) throw new RangeError("无效的 WALL 布局边界");
    const areas = weights.map(weight => unitArea * (weight + 1) / 3);
    const totals = [0];
    const logs = [0];
    const squares = [0];
    for (const area of areas) {
      const value = Math.log(area);
      totals.push(totals[totals.length - 1] + area);
      logs.push(logs[logs.length - 1] + value);
      squares.push(squares[squares.length - 1] + value * value);
    }
    const canvasHeight = strictHeight ? height : Math.max(minHeight, height);
    const widths = new Float64Array(weights.length + 1).fill(Infinity);
    const distortions = new Float64Array(weights.length + 1).fill(Infinity);
    const choices = Array(weights.length);
    widths[weights.length] = distortions[weights.length] = 0;
    for (let start = weights.length - 1; start >= 0; start--) {
      for (let end = start + 1; end <= Math.min(weights.length, start + 16); end++) {
        if (!Number.isFinite(widths[end])) continue;
        const area = totals[end] - totals[start];
        const width = Math.max(minWidth, area / canvasHeight);
        const panelHeight = area / width;
        if (strictHeight && panelHeight < canvasHeight - .001) continue;
        const totalWidth = width + widths[end];
        if (totalWidth > widths[start] + .001) continue;
        const costs = new Float64Array(end - start + 1).fill(Infinity);
        const next = new Uint8Array(end - start);
        costs[end - start] = 0;
        const limit = Math.floor(width / minWidth + .001);
        for (let rowStart = end - 1; rowStart >= start; rowStart--) {
          let smallest = Infinity;
          for (let rowEnd = rowStart + 1; rowEnd <= Math.min(end, rowStart + limit); rowEnd++) {
            smallest = Math.min(smallest, areas[rowEnd - 1]);
            const rowHeight = (totals[rowEnd] - totals[rowStart]) / width;
            if (smallest / rowHeight < minWidth - .001) break;
            if (rowHeight < minHeight - .001 || !Number.isFinite(costs[rowEnd - start])) continue;
            const scale = Math.log(rowHeight * rowHeight * minWidth / minHeight);
            const distortion = squares[rowEnd] - squares[rowStart] - 2 * scale * (logs[rowEnd] - logs[rowStart]) + (rowEnd - rowStart) * scale * scale;
            const cost = costs[rowEnd - start] + Math.max(0, distortion);
            if (cost < costs[rowStart - start]) { costs[rowStart - start] = cost; next[rowStart - start] = rowEnd - start; }
          }
        }
        if (!Number.isFinite(costs[0])) continue;
        const distortion = costs[0] + distortions[end];
        if (totalWidth < widths[start] - .001 || distortion < distortions[start]) {
          widths[start] = totalWidth;
          distortions[start] = distortion;
          choices[start] = { end, width, height: panelHeight, next };
        }
      }
    }
    if (!Number.isFinite(widths[0])) return null;
    const tiles = [];
    let left = 0;
    let usedHeight = 0;
    for (let start = 0; start < weights.length; start = choices[start].end) {
      const panel = choices[start];
      for (let rowStart = start; rowStart < panel.end; rowStart = start + panel.next[rowStart - start]) {
        const rowEnd = start + panel.next[rowStart - start];
        const rowHeight = (totals[rowEnd] - totals[rowStart]) / panel.width;
        const top = (totals[rowStart] - totals[start]) / panel.width;
        for (let index = rowStart; index < rowEnd; index++) {
          const offset = (totals[index] - totals[rowStart]) / rowHeight;
          const right = (totals[index + 1] - totals[rowStart]) / rowHeight;
          tiles.push({ left: left + offset, top, width: right - offset, height: rowHeight });
        }
      }
      left += panel.width;
      usedHeight = Math.max(usedHeight, panel.height);
    }
    return { width: left, height: usedHeight, tiles };
  }

  function layoutGroups(groups, { height, headerHeight, ...bounds }) {
    const natural = groups.map(weights => layout(weights, { ...bounds, height: height - headerHeight }));
    const areas = natural.map(group => group.tiles.reduce((total, tile) => total + tile.width * tile.height, 0));
    const totals = [0];
    for (const area of areas) totals.push(totals[totals.length - 1] + area);
    const costs = new Float64Array(groups.length + 1);
    const choices = Array(groups.length);
    for (let start = groups.length - 1; start >= 0; start--) {
      costs[start] = natural[start].width + costs[start + 1];
      choices[start] = { end: start + 1, width: natural[start].width, layouts: [natural[start]] };
      const limit = Math.min(groups.length, start + 8, start + Math.floor(height / (bounds.minHeight + headerHeight)));
      for (let end = start + 2; end <= limit; end++) {
        const width = (totals[end] - totals[start]) / (height - (end - start) * headerHeight);
        if (width < bounds.minWidth || width + costs[end] >= costs[start] - .001) continue;
        const layouts = [];
        for (let index = start; index < end; index++) {
          const groupHeight = areas[index] / width;
          if (groupHeight < bounds.minHeight) break;
          const result = layout(groups[index], { ...bounds, height: groupHeight, strictHeight: true });
          if (!result) break;
          layouts.push(result);
        }
        if (layouts.length !== end - start) continue;
        costs[start] = width + costs[end];
        choices[start] = { end, width, layouts };
      }
    }
    const positions = [];
    let left = 0;
    for (let start = 0; start < groups.length; start = choices[start].end) {
      const choice = choices[start];
      let top = 0;
      for (const group of choice.layouts) {
        positions.push({ left, top, layout: group });
        top += headerHeight + group.height;
      }
      left += choice.width;
    }
    return { width: left, groups: positions };
  }

  window.WallLayout = Object.freeze({ layout, layoutGroups });
}());

(function () {
  "use strict";

  const element = name => document.getElementById(`wall${name}`);
  const labels = { design: window.PortalI18n.t("设计"), improvement: window.PortalI18n.t("改进"), dev: window.PortalI18n.t("Dev 已完成"), inconsistency: window.PortalI18n.t("不一致"), direction: window.PortalI18n.t("方向") };
  const actionLabels = { implement: window.PortalI18n.t("请实现"), publish: window.PortalI18n.t("发布"), "publish-all": window.PortalI18n.t("全量发布"), reject: window.PortalI18n.t("拒绝"), retire: window.PortalI18n.t("抛弃"), "align-design": window.PortalI18n.t("改规范"), "align-code": window.PortalI18n.t("改实现"), "approve-direction": window.PortalI18n.t("开始推进"), discuss: window.PortalI18n.t("讨论"), analyse: window.PortalI18n.t("分析"), verify: window.PortalI18n.t("全面回归"), stop: window.PortalI18n.t("停止") };
  const jobLabels = { queued: window.PortalI18n.t("已排队"), routing: window.PortalI18n.t("分配 Resident 中"), preparing: window.PortalI18n.t("准备工作区与任务中"), running: window.PortalI18n.t("进行中"), completed: window.PortalI18n.t("已完成"), blocked: window.PortalI18n.t("需要处理"), failed: window.PortalI18n.t("失败"), cancelled: window.PortalI18n.t("已停止"), stopping: window.PortalI18n.t("停止中") };
  const activeStatuses = new Set(["queued", "routing", "preparing", "running", "stopping"]);
  const stageOrder = ["improvement", "dev", "design", "inconsistency"];
  const stageFor = block => block.kind === "direction" ? "improvement" : category(block);
  const cardSignatures = new WeakMap();
  const stageLayouts = new WeakMap();
  const cardPreviews = new WeakMap();
  let wallGeometry = [];
  const state = {
    projects: [], projectCapabilities: {}, project: "", board: null, blocks: new Map(), cards: new Map(), groups: new Map(), groupBlocks: new Map(), stages: new Map(), visibleCount: 0,
    account: "", query: "", kind: "all", module: "", minImportance: 0, metanessFilter: "all", readFilter: "all", reviewFilter: "all", cluster: "domain", sort: "importance", zoom: "cards", expanded: new Set(), semanticSearch: null,
    pending: new Set(), receipts: new Map(), readReceipts: new Map(), reviewReceipts: new Map(), reviewPending: new Map(), recentOpenings: [], actionErrors: new Map(), error: "", scan: false,
    detail: null, detailRevision: 0, detailRequest: null, detailLoading: false, detailError: "", chats: new Map(), chatTab: "discussion", form: null,
  };
  const pageState = window.PortalPageState.create("wall", { user: () => window.PortalIdentity.username() });
  const viewFields = { ImportanceFilter: "minImportance", MetanessFilter: "metanessFilter", ReadFilter: "readFilter", ReviewFilter: "reviewFilter", Cluster: "cluster", Sort: "sort", Zoom: "zoom" };
  function saveViewPreferences() {
    if (state.project && window.PortalIdentity.username()) pageState.write({
      project: state.project, module: state.module, account: state.account,
      ...Object.fromEntries(Object.values(viewFields).map(key => [key, state[key]])),
    });
  }
  function savedModuleFilter(project, saved) {
    if (saved.project !== project || typeof saved.module !== "string") return "";
    if (saved.module === "unassigned") return saved.module;
    try {
      const path = JSON.parse(saved.module);
      return Array.isArray(path) && path.length > 0 && path.length <= 2 && path.every(part => typeof part === "string" && part.trim()) ? JSON.stringify(path) : "";
    } catch { return ""; }
  }
  function restoreViewPreferences(project) {
    const saved = window.PortalIdentity.username() ? pageState.read() : {};
    state.module = savedModuleFilter(project, saved);
    state.account = typeof saved.account === "string" ? saved.account : "";
    state.recentOpenings = recentOpenings(saved);
    for (const [name, key] of Object.entries(viewFields)) {
      const control = element(name);
      const value = saved[key];
      if (typeof value === typeof state[key] && Array.from(control.options).some(option => option.value === String(value))) state[key] = value;
      control.value = String(state[key]);
    }
  }
  function recentOpenings(saved) {
    if (!Array.isArray(saved.recentOpenings)) return [];
    return [...new Set(saved.recentOpenings.filter(key => typeof key === "string" && key.trim()))].slice(0, 1000);
  }
  function recordBlockOpen(project, id) {
    if (!window.PortalIdentity.username()) return;
    const key = actionKey(project, id);
    state.recentOpenings = [key, ...recentOpenings(pageState.read()).filter(item => item !== key)].slice(0, 1000);
    pageState.write({ recentOpenings: state.recentOpenings });
    if (state.sort === "recent") renderBoard();
  }
  const text = (target, value) => {
    const next = String(value ?? "");
    if (target.textContent !== next) target.textContent = next;
  };
  const category = block => block.status === "dev" ? "dev" : block.kind;
  const importance = block => Math.max(1, Math.min(10, Number(block.importance) || 1));
  const validMetaness = block => block.metaness === undefined || (Number.isInteger(block.metaness) && block.metaness >= 1 && block.metaness <= 10);
  const metaness = block => validMetaness(block) ? block.metaness ?? 1 : 1;
  const matchesImportance = block => importance(block) >= state.minImportance;
  const matchesMetaness = block => state.metanessFilter === "all" || (validMetaness(block) && metaness(block) === Number(state.metanessFilter));
  const writable = () => state.board?.capabilities?.write === true;
  const actionKey = (project, block = "") => `${project}:${block}`;
  const isUnread = block => block.unread === true && (!block.read_revision || state.readReceipts.get(actionKey(state.project, block.id)) !== block.read_revision);
  const matchesReadState = block => state.readFilter !== "unread" || isUnread(block);
  const isReviewed = block => {
    const receipt = state.reviewReceipts.get(actionKey(state.project, block.id));
    const current = state.blocks.get(block.id);
    const displayed = current && current.read_revision === block.read_revision ? current : block;
    return receipt && receipt.read_revision === block.read_revision ? receipt.reviewed : displayed.reviewed === true;
  };
  const matchesReviewState = block => state.reviewFilter === "all" || isReviewed(block) === (state.reviewFilter === "reviewed");
  const relatedIds = block => Array.isArray(block.related) ? block.related : [];
  const modulePath = block => Array.isArray(block.module_path) ? block.module_path : [];
  function matchesModule(block) {
    if (!state.module) return true;
    const path = modulePath(block);
    if (state.module === "unassigned") return !path.length;
    return JSON.parse(state.module).every((part, index) => part === path[index]);
  }

  function renderModuleFilter() {
    const select = element("Module");
    const modules = new Map();
    const paths = state.board?.modules || Array.from(state.blocks.values(), modulePath);
    for (const path of paths) {
      if (!path.length) continue;
      if (!modules.has(path[0])) modules.set(path[0], new Set());
      if (path[1]) modules.get(path[0]).add(path[1]);
    }
    const options = [["", "全部模块"]];
    for (const [parent, children] of modules) {
      options.push([JSON.stringify([parent]), parent]);
      for (const child of children) options.push([JSON.stringify([parent, child]), `\u00a0\u00a0${child}`]);
    }
    if (Array.from(state.blocks.values()).some(block => !modulePath(block).length)) options.push(["unassigned", "未归入 SPEC.md"]);
    if (!options.some(([value]) => value === state.module)) {
      if ((!state.board || state.board.loading) && state.module) options.push([state.module, state.module === "unassigned" ? "未归入 SPEC.md" : JSON.parse(state.module).join(" / ")]);
      else { state.module = ""; saveViewPreferences(); }
    }
    const signature = JSON.stringify(options);
    if (select.dataset.optionsSignature !== signature) {
      select.replaceChildren(...options.map(([value, label]) => new Option(label, value)));
      select.dataset.optionsSignature = signature;
    }
    select.value = state.module;
    select.title = state.module && state.module !== "unassigned" ? JSON.parse(state.module).join(" / ") : select.selectedOptions[0]?.textContent || window.PortalI18n.t("全部模块");
  }
  const jobFor = block => {
    const receipt = state.receipts.get(actionKey(state.project, block.id));
    if (receipt) return receipt;
    const current = state.blocks.get(block.id);
    if (current) return current.job || {};
    return (state.board?.jobs || []).reduce((latest, job) =>
      job.block === block.id && Number(job.created_at || 0) >= Number(latest.created_at || 0) ? job : latest,
    block.job || {});
  };
  const completedSourceRemoved = block => !state.blocks.has(block.id) && jobFor(block).status === "completed";
  const api = (path, payload) => payload === undefined
    ? window.PortalAPI.get(`/api/wall/${path}`)
    : window.PortalAPI.post(`/api/wall/${path}`, payload);

  function initializeSemanticSearch(activeId) {
    const project = state.project;
    state.semanticSearch = window.PortalSemanticSearch.create({
      input: element("Query"), button: element("SemanticButton"), panel: element("SemanticPanel"), identity: element("SemanticIdentity"),
      projectionScope: "wall",
      initialActiveId: activeId,
      urls: {
        list: `/api/wall/semantic-searches?project=${encodeURIComponent(project)}`,
        start: "/api/wall/semantic-search/start", refresh: "/api/wall/semantic-search/refresh", delete: "/api/wall/semantic-search/delete",
      },
      startPayload: () => ({ project }),
      actionPayload: () => ({ project }),
      isDisabled: () => !state.project,
      onStart: () => {
        state.query = "";
        element("Query").value = "";
        renderBoard();
      },
      onActiveChange: searchId => {
        if (searchId) { state.query = ""; element("Query").value = ""; }
        const url = new URL(location.href);
        if (searchId) url.searchParams.set("semantic", searchId);
        else url.searchParams.delete("semantic");
        window.history.replaceState(null, "", url);
        element("Board").scrollLeft = 0;
        renderBoard();
      },
      onJobsChange: jobs => { element("SemanticPanel").hidden = !jobs.length; },
    });
    const search = state.semanticSearch;
    if (project) search.load().catch(error => {
      if (state.semanticSearch === search) window.PortalUI.toast(error.message || window.PortalI18n.t("读取语义搜索失败"), "error");
    });
  }

  function semanticBlocks() {
    const blocks = Array.from(state.blocks.values());
    return state.semanticSearch ? state.semanticSearch.filterAndSort(blocks, block => block.id) : blocks;
  }

  const formHost = element("Form").parentElement;
  const documentation = { generation: 0, result: null, busy: false };
  const documentationDialog = window.PortalUI.createDialogController({
    modal: element("DocumentationOverlay"), escapeClose: true, backdropClose: true,
    onClose: () => {
      documentation.generation += 1;
      documentation.result = null;
      documentation.busy = false;
      element("DocumentationContent").textContent = "";
      element("DocumentationDownload").disabled = true;
      element("Documentation").disabled = !state.project;
      element("Documentation").setAttribute("aria-busy", "false");
    },
  });

  async function generateDocumentation() {
    if (!state.project || documentation.busy) return;
    const generation = ++documentation.generation;
    const project = state.project;
    documentation.busy = true;
    documentation.result = null;
    element("Documentation").disabled = true;
    element("Documentation").setAttribute("aria-busy", "true");
    element("DocumentationDownload").disabled = true;
    element("DocumentationContent").textContent = "";
    element("DocumentationError").hidden = true;
    text(element("DocumentationStatus"), "正在鉴权读取 SPEC 与 Design 正文…");
    documentationDialog.open({ focusTarget: element("DocumentationClose") });
    try {
      const result = await api("documentation/preview", { project });
      if (generation !== documentation.generation || project !== state.project) return;
      documentation.result = result;
      element("DocumentationContent").textContent = result.markdown;
      text(element("DocumentationStatus"), `已生成 ${result.manifest.sources.length} 份 Design · SPEC SHA256 ${result.manifest.spec.sha256} · 文档 SHA256 ${result.document_sha256}`);
      element("DocumentationDownload").disabled = false;
    } catch (error) {
      if (generation !== documentation.generation) return;
      text(element("DocumentationStatus"), "未生成文档");
      text(element("DocumentationError"), error.message || "读取失败，请重试");
      element("DocumentationError").hidden = false;
    } finally {
      if (generation === documentation.generation) {
        documentation.busy = false;
        element("Documentation").disabled = !state.project;
        element("Documentation").setAttribute("aria-busy", "false");
      }
    }
  }

  function downloadDocumentation() {
    const result = documentation.result;
    if (!result) return;
    const url = URL.createObjectURL(new Blob([result.markdown], { type: "text/markdown;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = result.filename;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  const recentDialog = window.PortalUI.createDialogController({
    modal: element("RecentOverlay"), escapeClose: true, backdropClose: true,
  });
  const detailDialog = window.PortalUI.createDialogController({
    modal: element("Detail"),
    escapeClose: true,
    backdropClose: true,
    dirty: () => state.form?.mode === "edit" && state.form.initial !== formSignature(),
    beforeClose: () => !(state.form?.mode === "edit" && state.form.saving),
    onClose: () => {
      state.detailRevision += 1;
      state.detailRequest?.abort();
      state.detail = null;
      resetDetailEditor();
      destroyChat();
    },
  });
  const formDialog = window.PortalUI.createDialogController({
    modal: element("FormOverlay"),
    escapeClose: true,
    backdropClose: true,
    dirty: () => !!state.form && state.form.initial !== formSignature(),
    beforeClose: () => !state.form?.saving,
    onClose: () => { state.form = null; element("FormFields").replaceChildren(); },
  });
  const resourceDetail = window.PortalResourceDetail.createFrameHost({
    overlay: element("ResourceDetail"), frame: element("ResourceFrame"), loading: element("ResourceLoading"),
    title: window.PortalI18n.t("Design / Plan"), loadingText: window.PortalI18n.t("正在打开资源详情…"),
    onClose: closed => {
      if (!closed.changed) return;
      state.scan = true;
      coordinator.refresh({ force: true }).catch(() => {});
    },
  });
  const isKnowledgeSource = source => /^(decision|plan|thinking|information|procedure|evaluation|research|discussion)_[a-f0-9]{12}$/.test(source || "");
  const sourceDetail = window.PortalResourceDetail.createLocalHost({
    modal: element("SourceOverlay"),
    load: (resource, { signal }) => window.PortalAPI.get(`/api/wall/source?${new URLSearchParams({ project: resource.project, source: resource.resourceId })}`, { signal }),
    onLoading: resource => {
      element("SourceTitle").textContent = resource.title || resource.resourceId;
      text(element("SourceStatus"), "正在读取完整正文…");
      element("SourceContent").replaceChildren();
      element("SourceError").hidden = true;
    },
    present: payload => {
      element("SourceTitle").textContent = payload.title;
      element("SourceStatus").textContent = payload.source_id;
      element("SourceContent").innerHTML = window.MD.render(payload.content);
    },
    onError: error => {
      element("SourceStatus").textContent = "";
      element("SourceError").textContent = error.message;
      element("SourceError").hidden = false;
    },
    onClose: () => element("SourceContent").replaceChildren(),
  });
  element("SourceClose").addEventListener("click", () => sourceDetail.close());

  function renderSource(host, block, showId = false) {
    if (!block.source_id) {
      text(host, block.source_title || block.source_id || "未提供出处");
      return;
    }
    let control = host.querySelector("button[data-source]");
    if (!control) { control = button("", { source: block.source_id }); host.replaceChildren(control); }
    control.dataset.source = block.source_id;
    text(control, `${block.source_title || block.source_id}${showId ? ` · ${block.source_id}` : ""}`);
    control.title = block.source_id;
  }

  function sizeWall(savedScroll) {
    const board = element("Board");
    const headerHeight = board.querySelector(".wallGroupHeader")?.offsetHeight || 0;
    const viewportWidth = board.clientWidth;
    if (!viewportWidth || !board.clientHeight) return;
    const minHeight = state.zoom === "detail" ? 300 : 120;
    // Reserve readable source text beside the English primary/reject actions.
    // The weighted layout still owns every tile's area; no per-card DOM probe.
    const minWidth = Math.min(viewportWidth, 280);
    const baseWidth = minWidth * (viewportWidth <= 600 ? 1.4 : 1.2);
    const bounds = { height: Math.max(minHeight, board.clientHeight - headerHeight), minHeight, minWidth, unitArea: baseWidth * minHeight * 1.5 };
    const scrollLeft = Number.isFinite(savedScroll) ? savedScroll : board.scrollLeft;
    const anchor = scrollLeft > 0 ? wallGeometry.find(tile => tile.left + tile.width > scrollLeft && tile.left < scrollLeft + viewportWidth) : null;
    const geometry = [];
    let offset = 0;
    for (const stage of board.querySelectorAll(".wallStageBody")) {
      const groups = Array.from(stage.children, section => {
        const grid = section.querySelector(".wallGrid");
        const blocks = state.groupBlocks.get(section.dataset.groupKey) || [];
        return { section, grid, blocks, weights: blocks.map(metaness) };
      });
      const signature = JSON.stringify([bounds, headerHeight, groups.map(group => [group.section.dataset.groupKey, group.blocks.map(block => block.id), group.weights])]);
      let saved = stageLayouts.get(stage);
      if (saved?.signature !== signature) {
        const layout = window.WallLayout.layoutGroups(groups.map(group => group.weights), { ...bounds, height: bounds.height + headerHeight, headerHeight });
        saved = { signature, layout };
        stageLayouts.set(stage, saved);
      }
      // Project switches can recreate groups with the same IDs and weights.
      // Reuse the calculation, but apply it to the current sections.
      stage.style.width = `${saved.layout.width}px`;
      groups.forEach(({ section, grid, blocks }, groupIndex) => {
        const position = saved.layout.groups[groupIndex];
        const rectangle = position.layout;
        section.style.left = `${position.left}px`;
        section.style.top = `${position.top}px`;
        section.style.width = `${rectangle.width}px`;
        section.style.height = `${rectangle.height + headerHeight}px`;
        grid.style.width = `${rectangle.width}px`;
        grid.style.height = `${rectangle.height}px`;
        blocks.forEach((block, index) => {
          const tile = rectangle.tiles[index];
          geometry.push({ id: block.id, groupKey: section.dataset.groupKey, tile,
            left: offset + position.left + tile.left, width: tile.width });
        });
      });
      offset += saved.layout.width;
    }
    wallGeometry = geometry;
    const movedAnchor = anchor && geometry.find(tile => tile.id === anchor.id);
    board.scrollLeft = movedAnchor ? scrollLeft + movedAnchor.left - anchor.left : scrollLeft;
    renderVisibleCards();
  }

  function renderVisibleCards() {
    const board = element("Board");
    const left = board.scrollLeft - board.clientWidth;
    const right = board.scrollLeft + board.clientWidth * 2;
    const virtualize = state.visibleCount > 500;
    const visible = new Map();
    const visibleIds = new Set();
    for (const item of wallGeometry) {
      if (virtualize && (item.left + item.width < left || item.left > right)) continue;
      if (!visible.has(item.groupKey)) visible.set(item.groupKey, []);
      visible.get(item.groupKey).push(item);
      visibleIds.add(item.id);
    }
    for (const section of board.querySelectorAll(".wallGroup")) {
      const items = visible.get(section.dataset.groupKey) || [];
      const cards = items.map(item => {
        const card = renderCard(state.blocks.get(item.id));
        const tile = item.tile;
        card.style.left = `${tile.left}px`;
        card.style.top = `${tile.top}px`;
        card.style.width = `${tile.width}px`;
        card.style.height = `${tile.height}px`;
        card.style.setProperty("--wall-preview-lines", Math.max(2, Math.floor((tile.height - 90) / 15)));
        return card;
      });
      reconcile(section.querySelector(".wallGrid"), cards);
    }
    if (virtualize) for (const [id, card] of state.cards) {
      if (visibleIds.has(id)) continue;
      card.remove();
      state.cards.delete(id);
    }
  }

  function reconcile(parent, nodes) {
    const wanted = new Set(nodes);
    for (const child of Array.from(parent.children)) if (!wanted.has(child)) child.remove();
    let cursor = parent.firstElementChild;
    for (const node of nodes) {
      if (node !== cursor) parent.insertBefore(node, cursor);
      cursor = node.nextElementSibling;
    }
  }

  function button(label, dataset = {}) {
    const control = document.createElement("button");
    control.type = "button";
    control.textContent = label;
    Object.assign(control.dataset, dataset);
    return control;
  }

  function actionsFor(block) {
    if (completedSourceRemoved(block)) return [];
    const kind = category(block);
    if (activeStatuses.has(jobFor(block).status)) return ["stop"];
    if (kind === "dev") return ["publish"];
    if (kind === "improvement") return ["implement", "reject"];
    if (kind === "inconsistency") return ["align-design", "align-code", "reject"];
    if (kind === "direction") return ["approve-direction", "reject"];
    return ["retire"];
  }

  function renderActions(host, block, detail = false) {
    const names = writable() ? actionsFor(block) : [];
    if (!detail && block.kind === "direction") names.push("expand");
    const existing = new Map(Array.from(host.children).map(control => [control.dataset.action, control]));
    const controls = names.map((name, index) => {
      const retry = name === "implement" && jobFor(block).status === "failed";
      const label = retry ? window.PortalI18n.t("重试实现") : name === "expand" ? window.PortalI18n.text`${state.expanded.has(block.id) ? window.PortalI18n.t("收起") : window.PortalI18n.t("展开")}关联点 · ${relatedIds(block).length}` : actionLabels[name];
      const control = existing.get(name) || button(label, { action: name, block: block.id });
      control.dataset.block = block.id;
      text(control, label);
      control.title = retry ? window.PortalI18n.t("重试当前卡片；已分配且仍可访问的 Resident 会继续复用") : name === "publish" ? window.PortalI18n.t("将此成果及必要依赖同步到 Release，校验 commit 后完成当前成果") : label;
      control.dataset.primary = String(index === 0);
      control.disabled = name !== "expand" && state.pending.has(actionKey(state.project, block.id));
      if (detail) control.disabled ||= state.detailLoading || !!state.detailError;
      if (name === "expand") control.setAttribute("aria-expanded", String(state.expanded.has(block.id)));
      return control;
    });
    reconcile(host, controls);
  }

  function operationErrorText(receipt) {
    if (!receipt || typeof receipt !== "object") return "";
    const creation = receipt.details?.resident_creation;
    const commitLabels = { committed: window.PortalI18n.t("已提交"), unknown: window.PortalI18n.t("提交结果未知"), not_committed: window.PortalI18n.t("未提交") };
    return [receipt.code, commitLabels[receipt.commit_state],
      creation?.request_id ? `原请求 ${creation.request_id}` : "",
      receipt.commit_state === "committed" && creation?.resident_id ? `已提交 Resident ${creation.resident_id}` : "",
    ].filter(Boolean).join(" · ");
  }

  function jobText(job) {
    if (!job?.id) return "";
    return [actionLabels[job.action] || "任务", jobLabels[job.status] || job.status,
      job.publication_commit ? `commit ${job.publication_commit.slice(0, 12)}` : "",
      job.error || job.operation_error?.message || job.summary,
      operationErrorText(job.operation_error)].filter(Boolean).join(" · ");
  }

  function renderJob(host, block, detailError = "") {
    const key = actionKey(state.project, block.id);
    const job = jobFor(block);
    const message = detailError || state.actionErrors.get(key) || (state.pending.has(key) ? window.PortalI18n.t("正在提交…") : jobText(job));
    const error = !!(detailError || state.actionErrors.get(key) || job.error);
    const signature = JSON.stringify([message, job.resident_id, job.resident_available, error]);
    if (host.dataset.signature === signature) return;
    host.dataset.signature = signature;
    text(host, message);
    if (job.resident_id && job.resident_available === false) {
      host.append(document.createTextNode(" · 原 Resident 已不可用，可重新发起任务。"));
    } else if (job.resident_id) {
      const link = document.createElement("a");
      link.href = `/r/${encodeURIComponent(job.resident_id)}`;
      link.textContent = window.PortalI18n.text` · 打开 Resident · ${job.resident_id}`;
      host.append(link);
    }
    host.dataset.error = String(error);
    host.hidden = !host.textContent;
  }

  function renderRelated(host, block, expanded = true) {
    host.hidden = !expanded || !relatedIds(block).length;
    if (host.hidden) return;
    const signature = JSON.stringify(relatedIds(block).map(id => [id, state.blocks.get(id)?.title]));
    if (host.dataset.signature === signature) return;
    host.dataset.signature = signature;
    host.replaceChildren(...relatedIds(block).map(id => {
      const related = state.blocks.get(id);
      const control = button(related?.title || `${id} · 当前墙面不可用`, { openBlock: id });
      control.title = control.textContent;
      control.disabled = !related;
      return control;
    }));
  }

  function cardPreview(card, block) {
    // WALL already supplies a plain-text preview for every parsed point.
    // Parsing thousands of full Markdown bodies again blocks the first paint.
    if (typeof block.preview === "string") return block.preview;
    const content = block.content || block.preview || "";
    const previous = cardPreviews.get(card);
    if (previous?.content === content && previous.title === block.title) return previous.text;
    const template = document.createElement("template");
    template.innerHTML = window.MD.render(content);
    const first = template.content.firstElementChild;
    if (first && /^H[1-6]$/.test(first.tagName) && first.textContent.trim() === block.title?.trim()) first.remove();
    for (const node of template.content.querySelectorAll("p, li, h1, h2, h3, h4, h5, h6, pre, blockquote, tr, br")) node.append(document.createTextNode("\n"));
    const preview = template.content.textContent.replace(/\n{3,}/g, "\n\n").trim();
    cardPreviews.set(card, { content, title: block.title, text: preview });
    return preview;
  }

  function renderCard(block) {
    let card = state.cards.get(block.id);
    if (!card) {
      card = document.createElement("article");
      card.className = "wallCard";
      card.dataset.blockId = block.id;
      card.tabIndex = 0;
      card.innerHTML = window.PortalI18n.markup('<div class="wallCardTop"><span class="wallCardCategory"></span><a class="wallCardResident" hidden></a><label class="wallReview"></label><span class="wallMetaness"></span><span class="wallImportance"></span><span class="wallUnreadDot" role="img" aria-label="未读" title="未读"></span></div><h3><button type="button" class="wallCardTitle"></button></h3><p class="wallCardPreview"></p><div class="wallRelated"></div><div class="wallCardFooter"><div class="wallCardSource"></div><div class="wallActions"></div></div>');
      card.querySelector(".wallCardTitle").dataset.openBlock = block.id;
      state.cards.set(block.id, card);
    }
    const preview = cardPreview(card, block);
    const key = actionKey(state.project, block.id);
    const job = jobFor(block);
    const creationUnconfirmed = job.operation_error?.details?.resident_creation
      && !job.message_id && !activeStatuses.has(job.status);
    const workStates = [job.status, creationUnconfirmed ? "" : block.resident_work_state];
    const workState = workStates.includes("running") ? "running"
      : workStates.find(status => activeStatuses.has(status)) || job.status || (state.pending.has(key) ? "submitting" : "");
    const unread = isUnread(block);
    const residentId = block.resident_id || job.resident_id || "";
    const signature = JSON.stringify([
      block.title, block.kind, category(block), importance(block), block.metaness, block.metaness_source, block.document_scope, block.recommendation, block.importance_source, preview, block.source_title, block.source_id,
      writable(), unread, isReviewed(block), block.read_revision, state.reviewPending.get(key), state.pending.has(key), state.actionErrors.get(key), state.expanded.has(block.id),
      jobText(job), job.status, workState, residentId, job.lineage_id, !!job.error, relatedIds(block).map(id => [id, state.blocks.get(id)?.title]),
    ]);
    if (cardSignatures.get(card) === signature) return card;
    cardSignatures.set(card, signature);
    card.dataset.kind = category(block);
    card.dataset.direction = String(block.kind === "direction");
    card.dataset.metaness = String(metaness(block));
    const metaLabel = card.querySelector(".wallMetaness");
    text(metaLabel, validMetaness(block) ? `M${metaness(block)}` : "M?");
    metaLabel.title = validMetaness(block) ? window.PortalI18n.text`Metaness ${metaness(block)} / 10 · 面积表示元层级${block.metaness_source === "default" || block.metaness === undefined ? window.PortalI18n.t("（未填写，显示默认 1）") : ""}` : window.PortalI18n.t("metaness 无效：必须是 1–10 的整数，请检查来源文档");
    metaLabel.setAttribute("aria-label", metaLabel.title);
    card.dataset.workState = workState;
    card.classList.toggle("unread", unread);
    card.querySelector(".wallUnreadDot").setAttribute("aria-hidden", String(!unread));
    renderReview(card.querySelector(".wallReview"), block);
    card.style.setProperty("--wall-depth", `${importance(block) * 6.4 - 3}%`);
    text(card.querySelector(".wallCardCategory"), labels[category(block)] || block.kind);
    const residentLink = card.querySelector(".wallCardResident");
    text(residentLink, residentId);
    residentLink.hidden = !residentId;
    residentLink.title = residentId;
    if (residentId) residentLink.href = `/r/${encodeURIComponent(residentId)}`;
    else residentLink.removeAttribute("href");
    text(card.querySelector(".wallImportance"), `${importance(block)} / 10`);
    card.querySelector(".wallImportance").title = block.recommendation || (block.importance_source === "document" ? window.PortalI18n.t("文档默认重要度") : window.PortalI18n.t("点的重要度"));
    card.setAttribute("aria-label", block.title || window.PortalI18n.t("未命名点"));
    text(card.querySelector(".wallCardTitle"), block.title || "未命名点");
    text(card.querySelector(".wallCardPreview"), preview);
    card.querySelector(".wallCardPreview").hidden = !preview || (state.expanded.has(block.id) && relatedIds(block).length > 0);
    renderSource(card.querySelector(".wallCardSource"), block);
    renderActions(card.querySelector(".wallActions"), block);
    renderRelated(card.querySelector(".wallRelated"), block, state.expanded.has(block.id));
    return card;
  }

  function sortedBlocks() {
    const query = state.query.trim().toLocaleLowerCase();
    const expanded = new Set(Array.from(state.expanded).flatMap(id => relatedIds(state.blocks.get(id) || {})));
    const blocks = semanticBlocks().filter(block => {
      if (!matchesModule(block) || !matchesImportance(block) || !matchesMetaness(block) || !matchesReadState(block) || !matchesReviewState(block)) return false;
      if (state.kind !== "all" && category(block) !== state.kind) return false;
      if (state.zoom === "overview" && block.kind !== "direction" && !expanded.has(block.id)) return false;
      return !query || [block.title, block.preview, block.content, block.source_id, block.source_title, block.domain, ...modulePath(block)].join(" ").toLocaleLowerCase().includes(query);
    });
    const recentRanks = new Map(state.recentOpenings.map((key, index) => [key, index]));
    blocks.sort((left, right) => {
      const titleOrder = String(left.title).localeCompare(String(right.title), "zh-CN");
      if (state.sort === "title") return titleOrder;
      if (state.sort === "recent") {
        const leftRank = recentRanks.get(actionKey(state.project, left.id)) ?? recentRanks.size;
        const rightRank = recentRanks.get(actionKey(state.project, right.id)) ?? recentRanks.size;
        if (leftRank !== rightRank) return leftRank - rightRank;
      }
      return importance(right) - importance(left) || titleOrder;
    });
    return blocks;
  }

  function renderBoard() {
    const scrollLeft = element("Board").scrollLeft;
    renderModuleFilter();
    const blocks = sortedBlocks();
    state.visibleCount = blocks.length;
    const groups = new Map();
    const directions = Array.from(state.blocks.values()).filter(block => block.kind === "direction");
    for (const block of blocks) {
      let key = modulePath(block).join(" / ") || block.domain || "未分类";
      let title = key;
      if (state.cluster === "document") { key = block.source_id || "未提供出处"; title = block.source_title || key; }
      if (state.cluster === "direction") {
        const direction = block.kind === "direction" ? block : directions.find(item => relatedIds(item).includes(block.id) || relatedIds(block).includes(item.id));
        key = direction?.id || "unlinked";
        title = direction?.title || "未关联方向";
      }
      key = `${stageFor(block)}:${key}`;
      if (!groups.has(key)) groups.set(key, { title, stage: stageFor(block), blocks: [] });
      groups.get(key).blocks.push(block);
    }
    state.groupBlocks = new Map(Array.from(groups, ([key, group]) => [key, group.blocks]));
    const stageSections = new Map(stageOrder.map(stage => [stage, []]));
    for (const [key, group] of groups) {
      let section = state.groups.get(key);
      if (!section) {
        section = document.createElement("section");
        section.className = "wallGroup";
        section.dataset.groupKey = key;
        section.innerHTML = window.PortalI18n.markup('<header class="wallGroupHeader"><div class="wallGroupHeading"><span class="wallGroupStage"></span><h2></h2><span class="wallGroupCount"></span></div></header><div class="wallGrid"></div>');
        state.groups.set(key, section);
      }
      text(section.querySelector("h2"), group.title);
      section.querySelector("h2").title = group.title;
      text(section.querySelector(".wallGroupStage"), state.kind === "direction" ? "演化方向" : labels[group.stage]);
      text(section.querySelector(".wallGroupCount"), `${group.blocks.length} 点`);
      stageSections.get(group.stage)?.push(section);
    }
    const stages = [];
    for (const stage of stageOrder) {
      const sections = stageSections.get(stage);
      if (!sections.length) continue;
      let section = state.stages.get(stage);
      if (!section) {
        section = document.createElement("section");
        section.className = "wallStage";
        section.dataset.stage = stage;
        section.dataset.kind = stage;
        section.innerHTML = window.PortalI18n.markup('<div class="wallStageBody"></div>');
        state.stages.set(stage, section);
      }
      section.setAttribute("aria-label", state.kind === "direction" ? window.PortalI18n.t("演化方向") : labels[stage]);
      reconcile(section.querySelector(".wallStageBody"), sections);
      stages.push(section);
    }
    reconcile(element("Board"), stages);
    element("Board").dataset.zoom = state.zoom;
    for (const [key, card] of state.cards) if (!state.blocks.has(key)) { card.remove(); state.cards.delete(key); }
    const empty = element("Empty");
    empty.hidden = !!blocks.length;
    empty.dataset.state = state.error && !state.board ? "error" : !state.board || state.board.loading ? "loading" : "empty";
    text(empty, !state.project ? "暂无可访问的工程。" : !state.board ? state.error || "正在读取墙面…" : state.board.loading && !state.blocks.size ? "正在扫描工程文档…" : state.blocks.size ? state.zoom === "overview" ? "没有匹配的方向。" : "没有匹配的点。" : "工程中暂无设计点。");
    if (!blocks.length && state.board && !state.board.loading) {
      const hint = state.blocks.size ? window.PortalI18n.t("请检查筛选、搜索和缩放条件；未识别的文档会显示来源与原因。") : state.board.format_hint;
      if (hint) text(empty, `${empty.textContent}\n${hint}`);
    }
    renderChrome(blocks.length);
    sizeWall(scrollLeft);
    renderDetailStatus();
  }

  function renderChrome(visibleCount) {
    const counts = { all: 0 };
    for (const block of semanticBlocks()) if (matchesModule(block) && matchesImportance(block) && matchesMetaness(block) && matchesReadState(block) && matchesReviewState(block)) {
      counts.all++;
      counts[category(block)] = (counts[category(block)] || 0) + 1;
    }
    element("Filters").querySelectorAll("button").forEach(control => {
      control.setAttribute("aria-pressed", String(control.dataset.kind === state.kind));
      text(control.querySelector("span"), counts[control.dataset.kind] || 0);
    });
    const project = state.board?.project || state.projects.find(item => item.id === state.project);
    element("Project").title = project?.root || project?.title || "";
    element("Recent").disabled = !state.project || !state.board;
    element("Documentation").disabled = !state.project || documentation.busy;
    if (recentDialog.isOpen()) renderRecentCompleted();
    text(element("Meta"), state.board ? `${visibleCount} / ${state.blocks.size} 点${state.board.loading ? " · 扫描中" : ""}${writable() ? "" : " · 只读"}` : state.error ? "读取失败" : "正在读取…");
    for (const name of ["Create", "Analyse", "Verify", "PublishAll", "Settings"]) element(name).disabled = !writable();
    for (const [name, action] of [["Analyse", "analyse"], ["Verify", "verify"], ["PublishAll", "publish-all"]]) {
      element(name).disabled ||= state.pending.has(actionKey(state.project, action)) ||
        (state.board?.jobs || []).some(job => !job.block && job.action === action && activeStatuses.has(job.status));
    }
    element("Register").disabled = (state.board?.capabilities || state.projectCapabilities).register !== true;
    element("Unregister").hidden = !state.project || state.project === "portal";
    element("Unregister").disabled = (!writable() && !state.board?.project?.deleting) || element("Register").disabled || state.pending.has(actionKey(state.project, "unregister"));
    const account = element("Account");
    const accounts = state.board?.codex_accounts || [];
    const choices = ["", ...accounts];
    if (state.account && !choices.includes(state.account)) choices.push(state.account);
    const signature = JSON.stringify(choices);
    if (account.dataset.choices !== signature) {
      account.replaceChildren(...choices.map(id => new Option(id ? `Codex · ${id}` : "账号：沿用配置", id)));
      account.dataset.choices = signature;
    }
    account.value = state.account;
    account.disabled = !writable();
    text(element("Settings"), project?.automation?.enabled || Object.values(project?.schedules || {}).some(plan => plan.enabled) ? "自动推进 · 开" : "自动推进 · 关");
    const errors = [state.error, ...(state.board?.errors || []).map(error => typeof error === "string" ? error : `${error.source_id ? `${error.source_id}：` : ""}${error.message || error.error || JSON.stringify(error)}`)].filter(Boolean);
    for (const block of state.blocks.values()) if (!validMetaness(block)) errors.push(`${block.source_id || block.id}：metaness 无效，应为 1–10 的整数；请检查来源文档。`);
    text(element("Errors"), errors.join("\n"));
    element("Errors").hidden = !errors.length;
    const diagnostics = state.board?.warnings || [];
    const warnings = diagnostics.map(warning => `${warning.source_id ? `${warning.source_id}：` : ""}${warning.message}${warning.preview ? `\n正文预览：${warning.preview}` : ""}`);
    if (diagnostics.some(warning => warning.code === "no_points") && state.board.format_hint) warnings.push(state.board.format_hint);
    text(element("Warnings"), warnings.join("\n\n"));
    element("Warnings").hidden = !warnings.length;
    renderProjectJobs();
  }

  function renderProjectJobs() {
    const visible = job => !job.block || (!state.blocks.has(job.block) && activeStatuses.has(job.status));
    const jobs = new Map((state.board?.jobs || []).filter(visible).map(job => [job.id, job]));
    for (const [key, job] of state.receipts) if (key.startsWith(`${state.project}:`) && visible(job)) jobs.set(job.id, job);
    const existing = new Map(Array.from(element("Jobs").children).map(node => [node.dataset.jobId, node]));
    const nodes = Array.from(jobs.values()).sort((a, b) => Number(a.created_at || 0) - Number(b.created_at || 0)).slice(-5).map(job => {
      const node = existing.get(job.id) || document.createElement("div");
      node.className = "wallProjectJob";
      node.dataset.jobId = job.id;
      const pending = state.pending.has(actionKey(state.project, job.block || job.id));
      const signature = JSON.stringify([jobText(job), job.title, job.block, job.resident_id, job.resident_available, job.tau_run_id, job.test_run_ids, pending, writable()]);
      if (node.dataset.signature !== signature) {
        node.dataset.signature = signature;
        const summary = document.createElement("span");
        summary.textContent = [job.block ? job.title : "", jobText(job)].filter(Boolean).join(" · ");
        node.replaceChildren(summary);
        if (job.resident_id && job.resident_available === false) {
          const unavailable = document.createElement("span");
          unavailable.textContent = window.PortalI18n.t("原 Resident 已不可用，可从上方操作重新发起任务。");
          node.append(unavailable);
        } else if (job.resident_id) {
          const link = document.createElement("a");
          link.href = `/r/${encodeURIComponent(job.resident_id)}`;
          link.textContent = window.PortalI18n.t("查看 Resident");
          node.append(link);
        }
        for (const runId of new Set([...(job.test_run_ids || []), job.tau_run_id].filter(Boolean))) {
          const report = document.createElement("a");
          report.href = `/wall?run=${encodeURIComponent(runId)}`;
          report.textContent = window.PortalI18n.t("测试报告");
          node.append(report);
        }
        if (activeStatuses.has(job.status) && writable()) {
          const stop = button("停止", { action: "stop", block: job.block, jobId: job.id });
          stop.disabled = pending;
          node.append(stop);
        }
      }
      return node;
    });
    reconcile(element("Jobs"), nodes);
    element("Jobs").hidden = !nodes.length;
  }

  function renderRecentCompleted() {
    const records = state.board?.recent_completed || [];
    text(element("RecentMeta"), `${state.board?.project?.title || ""} · 我的最近 ${records.length} 项工作`);
    element("RecentEmpty").hidden = !!records.length;
    const existing = new Map(Array.from(element("RecentList").children).map(node => [node.dataset.completedJob, node]));
    const nodes = records.map(record => {
      const row = existing.get(record.id) || document.createElement("li");
      row.className = "wallCompletedEntry";
      row.dataset.completedJob = record.id;
      const signature = JSON.stringify(record);
      if (row.dataset.signature === signature) return row;
      row.dataset.signature = signature;
      const title = document.createElement("h3");
      title.textContent = record.title || actionLabels[record.action] || window.PortalI18n.t("已完成的工作");
      const meta = document.createElement("div");
      meta.className = "wallCompletedMeta";
      const action = document.createElement("span");
      action.textContent = actionLabels[record.action] || record.action || window.PortalI18n.t("工作");
      const time = document.createElement("time");
      const completedAt = new Date(Number(record.completed_at) * 1000);
      if (record.completed_at && Number.isFinite(completedAt.getTime())) {
        time.dateTime = completedAt.toISOString();
        time.textContent = window.PortalUI.formatDateTime(completedAt, {
                    style: "compact",
        });
        time.title = window.PortalUI.formatDateTime(completedAt, {});
      } else time.textContent = window.PortalI18n.t("完成时间未记录");
      meta.append(action, time);
      const summary = document.createElement("p");
      summary.className = "wallCompletedSummary";
      summary.textContent = record.summary || "";
      summary.hidden = !record.summary;
      const links = document.createElement("div");
      links.className = "wallCompletedLinks";
      for (const result of record.result_blocks || []) {
        const control = button(record.result_blocks.length === 1 ? "查看结果" : result.title || "查看结果", { completedResult: result.id });
        control.className = "portalButton is-compact";
        control.title = result.title || window.PortalI18n.t("查看结果");
        links.append(control);
      }
      if (record.resident_id) {
        const link = document.createElement("a");
        link.href = `/r/${encodeURIComponent(record.resident_id)}`;
        link.textContent = window.PortalI18n.t("打开 Resident");
        links.append(link);
      }
      for (const runId of new Set([...(record.test_run_ids || []), record.tau_run_id].filter(Boolean))) {
        const link = document.createElement("a");
        link.href = `/wall?run=${encodeURIComponent(runId)}`;
        link.textContent = window.PortalI18n.t("测试报告");
        links.append(link);
      }
      row.replaceChildren(title, meta, summary, links);
      return row;
    });
    reconcile(element("RecentList"), nodes);
  }

  let active = false;
  let initialized = false;
  const coordinator = window.PortalRefreshCoordinator.create({
    key: () => `wall:${state.project}`,
    interval: () => state.board?.loading && !state.board?.signature ? 1500 : 5000,
    pauseWhenHidden: true,
    shouldRefresh: () => active && !!state.project,
    fetch: ({ signal, force }) => {
      const query = new URLSearchParams({ project: state.project });
      if (state.scan) { query.set("refresh", "1"); state.scan = false; }
      else if (!force && state.board?.signature) query.set("signature", state.board.signature);
      return window.PortalAPI.get(`/api/wall/board?${query}`, { signal });
    },
    apply: payload => {
      if (payload?.unchanged === true) {
        if (state.error) { state.error = ""; renderChrome(state.visibleCount); }
        return;
      }
      if (!payload || !Array.isArray(payload.blocks)) throw new Error("墙面响应缺少 blocks");
      state.board = payload;
      state.error = "";
      state.blocks = new Map(payload.blocks.map(block => [block.id, block]));
      for (const block of payload.blocks) {
        const key = actionKey(state.project, block.id);
        if (!block.unread || state.readReceipts.get(key) !== block.read_revision) state.readReceipts.delete(key);
        const review = state.reviewReceipts.get(key);
        if (review && (review.read_revision !== block.read_revision || review.reviewed === (block.reviewed === true))) state.reviewReceipts.delete(key);
      }
      const receivedJobs = [...(payload.jobs || []), ...payload.blocks.map(block => block.job).filter(Boolean)];
      for (const [key, receipt] of state.receipts) if (receivedJobs.some(job => job.id === receipt.id)) state.receipts.delete(key);
      renderBoard();
    },
    onError: error => { state.error = error.message || "读取墙面失败"; renderBoard(); },
  });

  async function selectProject(project, restorePreferences = false) {
    coordinator.stop();
    const activeSearch = !state.project ? new URLSearchParams(location.search).get("semantic") || "" : "";
    state.semanticSearch?.destroy();
    state.semanticSearch = null;
    detailDialog.close({ force: true });
    recentDialog.close({ force: true });
    documentationDialog.close({ force: true });
    state.project = project;
    state.board = null;
    if (restorePreferences) restoreViewPreferences(project);
    else state.module = "";
    saveViewPreferences();
    state.blocks.clear();
    state.expanded.clear();
    state.error = "";
    state.scan = false;
    element("Project").value = project;
    const url = new URL(window.location.href);
    url.searchParams.set("project", project);
    if (!activeSearch) url.searchParams.delete("semantic");
    window.history.replaceState(null, "", url);
    renderBoard();
    initializeSemanticSearch(activeSearch);
    if (!project) return;
    if (active) { coordinator.start(); await coordinator.refresh().catch(() => {}); }
  }

  async function loadProjects(preferred = "") {
    try {
      const [payload] = await Promise.all([api("projects"), window.PortalIdentity.load().catch(() => null)]);
      state.projects = payload.projects || [];
      state.projectCapabilities = payload.capabilities || {};
      element("Project").replaceChildren(...state.projects.map(project => new Option(project.title || project.id, project.id)));
      element("Project").disabled = !state.projects.length;
      const desired = preferred || new URLSearchParams(location.search).get("project") || "portal";
      await selectProject(state.projects.some(project => project.id === desired) ? desired : state.projects[0]?.id || "", !state.project);
      const blockId = new URLSearchParams(location.search).get("block");
      if (blockId && !state.openedInitialBlock) {
        state.openedInitialBlock = true;
        await openDetail(blockId, { id: blockId, title: window.PortalI18n.t("正在读取设计点"), kind: "design" });
      }
    } catch (error) {
      state.error = error.message;
      renderBoard();
    }
  }

  function destroyChat() {
    for (const entry of state.chats.values()) entry.panel.destroy();
    state.chats.clear();
    state.chatTab = "discussion";
    element("Chat").replaceChildren();
    element("WorkChat").replaceChildren();
  }

  function showChatDraft(entry, error = "") {
    entry.panel.showState({
      schema: "portal.chat_workspace.v3",
      title: window.PortalI18n.t("Chat Workspace"), subtitle: error || (entry.kind === "work" ? window.PortalI18n.t("正在连接实现 Resident…") : window.PortalI18n.t("未开始")),
      rendering: { empty_text: "暂无对话。" },
      composer: {
        can_send: writable(), can_edit: writable(), can_attach: writable(), refs_enabled: writable(), can_stop: false, requires_text: true,
        placeholder: window.PortalI18n.t("输入消息"), history_scope: entry.historyScope,
      },
    });
  }

  async function attachChat(kind, residentId = "") {
    if (!state.detail) return;
    let entry = state.chats.get(kind);
    if (entry?.preparing) return;
    if (entry && kind === "work" && entry.residentId !== residentId) {
      entry.panel.destroy();
      state.chats.delete(kind);
      element("WorkChat").replaceChildren();
      entry = null;
    }
    if (entry) {
      if (residentId && residentId !== entry.residentId && entry.unavailable !== residentId) {
        await bindChat(entry, { resident_id: residentId });
      }
      return;
    }
    const project = state.project;
    const point = state.detail;
    const workingDirectory = state.board?.project?.root || "";
    entry = { kind, residentId: "", preparing: false, unavailable: "", historyScope: kind === "discussion" ? `wall:${project}:${point.id}` : `wall-work:${project}:${point.id}:${residentId}` };
    const current = () => state.chats.get(kind) === entry;
    const panel = window.PortalChatWorkspace.panelPackage.create({
      root: element(kind === "discussion" ? "Chat" : "WorkChat"), id: `wallResidentChat-${kind}`, panelId: `wall-resident-chat-${kind}`, autoLoad: false,
      pendingHistoryScope: entry.historyScope,
      resolveBeforeSend: async () => {
        if (!current() || !writable()) throw new Error("当前条目不可用");
        if (kind !== "discussion") throw new Error("实现 Resident 尚未就绪");
        let identity;
        try {
          identity = await api("action", { project, block: point.id, action: "discuss", backend_route_id: state.account });
        } catch (error) {
          error.message = [error.message, operationErrorText(error.data)].filter(Boolean).join(" · ");
          throw error;
        }
        if (!current()) throw new Error("已切换 WALL 条目");
        entry.residentId = identity.resident_id;
        state.detail.discussion = identity;
        state.detail.resident_id = identity.resident_id;
        state.detail.lineage_id = identity.lineage_id;
        return identity;
      },
      submissionContext: async () => {
        if (!current() || state.project !== project || state.detail?.id !== point.id || state.detailLoading) {
          throw new Error("当前条目正文尚未就绪，请等待读取完成后发送");
        }
        let block = state.detail;
        if (state.blocks.has(point.id)) {
          const query = new URLSearchParams({ project, block: point.id });
          const payload = await window.PortalAPI.get(`/api/wall/block?${query}`);
          if (!payload.block || payload.block.id !== point.id) throw new Error("详情响应与当前点不匹配");
          block = payload.block;
        }
        if (!current() || state.project !== project || state.detail?.id !== point.id) {
          throw new Error("已切换 WALL 条目");
        }
        const currentPoint = Object.fromEntries(["id", "title", "kind", "resource_id", "source_id", "source_title", "content", "read_revision"]
          .filter(key => block[key] !== undefined).map(key => [key, block[key]]));
        return { context_text: `WALL 条目：${JSON.stringify(currentPoint)}\n工作目录：${workingDirectory}` };
      },
      onSent: () => { if (state.project === project) coordinator.invalidate(); },
    });
    entry.panel = panel;
    state.chats.set(kind, entry);
    showChatDraft(entry);
    if (residentId) await bindChat(entry, { resident_id: residentId }).catch(() => {});
  }

  async function bindChat(entry, identity, saved = null) {
    const panel = entry.panel;
    const current = () => state.chats.get(entry.kind) === entry;
    const draft = saved || { draft: panel.workspace.draftText(), refs: panel.workspace.refs(), attachments: panel.workspace.attachments() };
    const carryDraft = saved || draft.draft || draft.attachments?.length || window.PortalChatWorkspace.refCount(draft.refs);
    entry.residentId = identity.resident_id;
    try {
      await panel.setResident({ resident_id: identity.resident_id }, { load: true, force: true });
      if (current()) entry.unavailable = "";
    } catch (error) {
      if (current()) {
        entry.unavailable = identity.resident_id;
        showChatDraft(entry, `对话暂不可用：${error.message}`);
      }
      throw error;
    } finally {
      if (current() && carryDraft) {
        panel.workspace.setDraftText(draft.draft, draft.attachments);
        panel.workspace.setRefs(draft.refs);
      }
    }
  }

  function renderChatTabs(block) {
    const job = jobFor(block);
    const hasWork = !!job.resident_id && !!job.message_id;
    if (!hasWork) state.chatTab = "discussion";
    element("ChatTabs").hidden = !hasWork;
    text(element("WorkTab"), activeStatuses.has(job.status) ? "实现中" : "实现记录");
    element("WorkTab").title = job.resident_id || "";
    const discussion = block.discussion?.resident_id ? block.discussion : state.detail.discussion?.resident_id ? state.detail.discussion : !Object.hasOwn(block, "discussion") && block.resident_id !== job.resident_id ? block : {};
    element("DiscussionTab").title = discussion.resident_id || window.PortalI18n.t("首次发送时创建讨论 Resident");
    for (const [kind, tab, host] of [["discussion", "DiscussionTab", "Chat"], ["work", "WorkTab", "WorkChat"]]) {
      const active = state.chatTab === kind;
      element(tab).setAttribute("aria-selected", String(active));
      element(tab).tabIndex = active ? 0 : -1;
      element(host).hidden = !active;
    }
    attachChat("discussion", discussion.resident_id || "").catch(() => {});
    if (state.chatTab === "work") attachChat("work", job.resident_id).catch(() => {});
  }

  function renderDetailStatus() {
    if (!state.detail) return;
    renderReview(element("DetailReview"), state.detail, true);
    if (state.form?.mode === "edit") {
      const disabled = !writable() || state.detailLoading || state.form.saving || completedSourceRemoved(state.detail);
      element("FormSave").disabled = disabled;
      element("FormFields").inert = disabled;
    }
    const block = state.blocks.get(state.detail.id) || state.detail;
    renderActions(element("DetailActions"), block, true);
    renderJob(element("DetailStatus"), block, state.detailError);
    renderChatTabs(block);
  }

  function renderDetail(block) {
    state.detail = block;
    text(element("DetailTitle"), block.title || "未命名点");
    text(element("DetailKind"), `${labels[category(block)] || block.kind} · 重要度 ${importance(block)} / 10`);
    renderSource(element("DetailSource"), block, true);
    let bindingHost = element("DetailSource").parentElement.querySelector("[data-wall-test-binding]");
    if (!bindingHost) {
      bindingHost = document.createElement("div");
      bindingHost.dataset.wallTestBinding = "";
      bindingHost.setAttribute("role", "status");
      element("DetailSource").after(bindingHost);
    }
    bindingHost.hidden = block.kind !== "design";
    const binding = block.test_binding;
    bindingHost.replaceChildren();
    bindingHost.dataset.bindingState = binding?.status || "loading";
    if (binding?.status === "bound") {
      const link = document.createElement("a");
      link.href = binding.url;
      link.textContent = window.PortalI18n.text`主测试 · ${binding.title || binding.case_id}${binding.lifecycle === "retired" ? window.PortalI18n.t("（已退役）") : ""}`;
      link.dataset.boundTest = binding.case_id;
      bindingHost.append(link);
    } else {
      bindingHost.textContent = binding?.status === "conflict" ? window.PortalI18n.text`测试绑定冲突：${binding.reason}` : binding?.status === "unbound" ? window.PortalI18n.t("此设计点尚未绑定主测试。") : window.PortalI18n.t("正在读取设计点的测试绑定…");
    }
    text(element("DetailMetaness"), `${block.document_scope ? "整份改进计划 · " : ""}${validMetaness(block) ? `Metaness ${metaness(block)} / 10${block.metaness_source === "default" || block.metaness === undefined ? "（未填写，显示默认 1）" : ""} · 面积表示元层级，颜色深浅表示重要度` : "metaness 无效：请在来源文档中填写 1–10 的整数"}`);
    element("DetailContent").innerHTML = state.detailLoading && writable() ? "正在读取完整正文…" : window.MD.render(block.content || block.preview || "暂无正文");
    renderRelated(element("DetailRelated"), block);
    if (!state.detailLoading && writable()) openForm("edit", block);
    renderDetailStatus();
  }

  async function openDetail(id, reference = null) {
    const block = state.blocks.get(id) || reference;
    if (!block) return;
    if (state.form?.mode === "edit") {
      if (state.form.saving) return;
      if (state.form.initial !== formSignature()) {
        const previousRevision = state.detailRevision;
        const approved = await window.PortalUI.confirm({ title: window.PortalI18n.t("放弃未保存的修改？"), message: window.PortalI18n.t("打开其他点后将丢失尚未保存的修改。"), confirmLabel: window.PortalI18n.t("放弃修改"), tone: "danger" });
        if (!approved || previousRevision !== state.detailRevision) return;
      }
      resetDetailEditor();
    }
    const revision = ++state.detailRevision;
    const project = state.project;
    state.detailRequest?.abort();
    state.detailRequest = new AbortController();
    state.detailLoading = true;
    state.detailError = "";
    destroyChat();
    state.detail = block;
    renderDetail(block);
    detailDialog.open({ focusTarget: element("DetailClose") });
    try {
      const query = new URLSearchParams({ project: state.project, block: id });
      const payload = await window.PortalAPI.get(`/api/wall/block?${query}`, { signal: state.detailRequest.signal });
      if (revision !== state.detailRevision) return;
      if (!payload.block || payload.block.id !== id) throw new Error("详情响应与当前点不匹配");
      state.detailLoading = false;
      renderDetail(payload.block);
      recordBlockOpen(project, id);
      markBlockRead(project, payload.block).catch(error => {
        if (revision !== state.detailRevision) return;
        state.detailError = `未读状态保存失败：${error.message}`;
        renderDetailStatus();
      });
    } catch (error) {
      if (revision === state.detailRevision) {
        state.detailLoading = false;
        state.detailError = `完整正文读取失败：${error.message}`;
        if (writable()) text(element("DetailContent"), "完整正文暂不可用，请关闭详情后重试。");
        renderDetailStatus();
      }
    }
  }

  async function markBlockRead(project, block) {
    if (!block.unread || !block.read_revision) return;
    const result = await api("read", { project, block: block.id, read_revision: block.read_revision });
    if (!result.read || project !== state.project) return;
    state.readReceipts.set(actionKey(project, block.id), block.read_revision);
    const current = state.blocks.get(block.id);
    if (current) {
      if (state.readFilter === "unread") renderBoard();
      else renderCard(current);
    }
  }

  async function runAction(name, id = "", jobId = "") {
    if (!writable()) return;
    const project = state.project;
    const key = actionKey(project, id || jobId || name);
    if (state.pending.has(key)) return;
    if (["retire", "reject"].includes(name)) {
      const block = state.blocks.get(id);
      const descriptions = {
        improvement: window.PortalI18n.t("从 Plan 中移除这项建议，保留现有代码。"),
        inconsistency: window.PortalI18n.t("移除这条不一致记录，保留规范和现有代码。"),
        direction: window.PortalI18n.t("移除这个方向，保留关联的设计点、改进点和现有代码。"),
      };
      const approved = await window.PortalUI.confirm({
        title: window.PortalI18n.text`${actionLabels[name]}这个点？`, message: block?.title || id,
        description: name === "retire" ? window.PortalI18n.t("移除对应功能实现并更新规范。") : descriptions[block?.kind],
        confirmLabel: actionLabels[name], tone: "danger",
      });
      if (!approved || project !== state.project || state.pending.has(key)) return;
    }
    state.pending.add(key);
    state.actionErrors.delete(key);
    renderBoard();
    try {
      const result = await api("action", { project, ...(id ? { block: id } : {}), ...(jobId ? { job_id: jobId } : {}), action: name, backend_route_id: state.account });
      if (name === "stop") {
        if (!result.accepted) throw new Error("服务端未接受停止请求");
        window.PortalUI.toast(window.PortalI18n.t("停止请求已接收，等待运行状态更新。"), "success");
      } else if (name === "reject" && result.accepted === true && result.removed === true) {
        state.receipts.delete(key);
        window.PortalUI.toast(window.PortalI18n.t(result.cleanup === "pending" ? "已拒绝，身份清理将在空闲后完成。" : "已拒绝"), "success");
        if (project === state.project) {
          if (state.detail?.id === id) detailDialog.close();
          await coordinator.refresh({ force: true }).catch(() => {});
        }
        return;
      } else {
        if (!result.job?.id) throw new Error("服务端未返回任务回执");
        state.receipts.set(key, { ...result.job, action: name, block: id });
        window.PortalUI.toast(`${actionLabels[name]} · ${jobText(result.job)}`, result.job.error || result.job.status === "blocked" || result.job.status === "failed" ? "error" : "success");
      }
      if (project === state.project) coordinator.invalidate();
    } catch (error) {
      const message = [window.PortalI18n.text`${actionLabels[name]}失败：${error.message}`, operationErrorText(error.data)].filter(Boolean).join(" · ");
      state.actionErrors.set(key, message);
      window.PortalUI.toast(message, "error");
      if (project === state.project) coordinator.invalidate();
    } finally {
      state.pending.delete(key);
      if (project === state.project) renderBoard();
    }
  }

  async function unregisterProject() {
    if (element("Unregister").disabled || !state.project || state.project === "portal") return;
    const project = state.project;
    const key = actionKey(project, "unregister");
    state.pending.add(key);
    renderChrome(state.visibleCount);
    try {
      const approved = await window.PortalUI.confirm({ title: window.PortalI18n.t("取消工程登记？"), message: state.board.project.title || project, description: window.PortalI18n.t("保留工程源码。"), confirmLabel: window.PortalI18n.t("取消登记"), tone: "danger" });
      if (!approved) return;
      const result = await api("unregister", { project });
      if (result.removed !== true) throw new Error("服务端未确认取消登记");
      window.PortalUI.toast(window.PortalI18n.t("已取消登记"), "success");
      await loadProjects(state.project === project ? "portal" : state.project);
    } catch (error) {
      window.PortalUI.toast(window.PortalI18n.text`取消登记失败：${error.message}`, "error");
      if (project === state.project) coordinator.invalidate();
    } finally {
      state.pending.delete(key);
      renderChrome(state.visibleCount);
    }
  }

  function formSignature() {
    return JSON.stringify(Array.from(new FormData(element("Form")).entries()));
  }

  function resetDetailEditor() {
    if (state.form?.mode === "edit") {
      state.form = null;
      formHost.append(element("Form"));
      element("FormFields").replaceChildren();
    }
    element("DetailEditor").hidden = true;
    element("DetailContent").hidden = false;
    element("DetailRelated").hidden = false;
  }

  function openForm(mode, block = null) {
    const register = mode === "register";
    if (register ? (state.board?.capabilities || state.projectCapabilities).register !== true : !writable()) return;
    const project = state.board?.project;
    state.form = { mode, project: state.project, block, initial: "", saving: false };
    text(element("FormTitle"), mode === "create" ? "新建点" : mode === "edit" ? "编辑点" : register ? "登记工程" : "自动推进" );
    const fields = element("FormFields");
    if (register) {
      fields.innerHTML = window.PortalI18n.markup('<label>工程根目录<input name="root" placeholder="/absolute/path/to/project" required pattern="/.*" /></label><label>权限 Project ID<input name="project_id" required placeholder="AI员工" /></label><label>工程名称<input name="title" required maxlength="200" /></label>');
    } else if (mode === "automation") {
      const automation = project.automation || {};
      fields.innerHTML = window.PortalI18n.html`<label class="wallCheck"><input type="checkbox" name="enabled" ${automation.enabled ? "checked" : ""} />启用自动推进</label><label>分析间隔（分钟）<input type="number" name="interval_minutes" min="10" max="10080" step="1" value="${Number(automation.interval_minutes) || 60}" required /></label><label class="wallCheck"><input type="checkbox" name="auto_fix" ${automation.auto_fix ? "checked" : ""} />授权自动修复日常错误</label>`;
      const accountField = document.createElement("label");
      accountField.textContent = window.PortalI18n.t("自动任务 Codex 账号");
      const accountSelect = document.createElement("select");
      accountSelect.name = "backend_route_id";
      accountSelect.add(new Option("沿用配置", ""));
      for (const id of state.board?.codex_accounts || []) accountSelect.add(new Option(id, id));
      accountSelect.value = automation.backend_route_id || state.account;
      accountField.append(accountSelect);
      fields.prepend(accountField);
      for (const [action, label] of [["verify", "全面回归"], ["publish-all", "全量发布"]]) {
        const plan = project.schedules?.[action] || {};
        const disabled = !writable();
        const group = document.createElement("fieldset");
        group.disabled = disabled;
        group.innerHTML = window.PortalI18n.html`<legend>${label}独立计划</legend><label class="wallCheck"><input type="checkbox" name="${action}_enabled" ${plan.enabled ? "checked" : ""} />定期${label}</label><label>${label}间隔（分钟）<input type="number" name="${action}_interval" min="10" max="10080" step="1" value="${Number(plan.interval_minutes) || 1440}" required /></label>`;
        fields.append(group);
      }
    } else {
      const choices = Object.entries(labels).filter(([kind]) => kind !== "dev");
      fields.innerHTML = window.PortalI18n.html`${mode === "create" ? window.PortalI18n.html`<label>类别<select name="kind" aria-label="类别">${choices.map(([kind, label]) => window.PortalI18n.html`<option value="${kind}">${label}</option>`).join("")}</select></label>` : ""}<label>标题<input name="title" required maxlength="240" /></label><label>正文（Markdown）<textarea name="content" required></textarea></label><label>重要度（1–10）<input type="number" name="importance" min="1" max="10" step="1" required value="${block ? importance(block) : 5}" /></label><label>关联点<select name="related" multiple aria-label="关联点"></select></label>`;
      fields.querySelector('[name="title"]').value = block?.title || "";
      fields.querySelector('[name="content"]').value = block?.content || "";
      const related = fields.querySelector('[name="related"]');
      const choicesById = new Map(Array.from(state.blocks.values()).filter(item => item.id !== block?.id).map(item => [item.id, item.title]));
      for (const id of relatedIds(block || {})) if (!choicesById.has(id)) choicesById.set(id, `${id} · 当前墙面不可用`);
      for (const [id, title] of choicesById) related.add(new Option(title, id, false, relatedIds(block || {}).includes(id)));
      const search = document.createElement("div");
      search.dataset.portalSearchField = "";
      search.dataset.portalSearchDensity = "compact";
      search.innerHTML = window.PortalI18n.markup('<input data-portal-search-input type="search" aria-label="搜索关联点" placeholder="搜索关联点" autocomplete="off" />');
      related.parentElement.before(search);
      window.PortalFormControls.mountSearchField(search);
      search.querySelector("input").addEventListener("input", event => {
        const query = event.target.value.trim().toLocaleLowerCase();
        for (const option of related.options) {
          option.hidden = !option.selected && !`${option.text} ${option.value}`.toLocaleLowerCase().includes(query);
        }
      });
    }
    element("FormError").hidden = true;
    element("FormSave").disabled = false;
    element("FormFields").inert = false;
    state.form.initial = formSignature();
    if (mode === "edit") {
      element("DetailEditor").append(element("Form"));
      element("DetailEditor").hidden = false;
      element("DetailContent").hidden = true;
      element("DetailRelated").hidden = true;
    } else formDialog.open();
  }

  async function saveForm(event) {
    event.preventDefault();
    const form = state.form;
    if (!form || form.saving || !element("Form").reportValidity()) return;
    if (form.project !== state.project || (form.mode === "register" ? (state.board?.capabilities || state.projectCapabilities).register !== true : !writable())) {
      text(element("FormError"), "工程或权限已变化，请关闭后重新打开表单。");
      element("FormError").hidden = false;
      return;
    }
    form.saving = true;
    element("FormError").hidden = true;
    element("FormSave").disabled = true;
    text(element("FormSave"), "保存中…");
    const data = new FormData(element("Form"));
    element("FormFields").inert = true;
    let payload;
    if (form.mode === "register") payload = { root: data.get("root").trim(), project_id: data.get("project_id").trim(), title: data.get("title").trim() };
    else if (form.mode === "automation") {
      const schedules = {};
      for (const action of ["verify", "publish-all"]) {
        if (data.has(`${action}_interval`)) schedules[action] = {
          enabled: data.has(`${action}_enabled`), interval_minutes: Number(data.get(`${action}_interval`)),
        };
      }
      payload = { project: form.project, enabled: data.has("enabled"), interval_minutes: Number(data.get("interval_minutes")), auto_fix: data.has("auto_fix"), backend_route_id: data.get("backend_route_id"), schedules };
    }
    else payload = { project: form.project, ...(form.mode === "create" ? { kind: data.get("kind") } : { block: form.block.id }), title: data.get("title").trim(), content: data.get("content"), importance: Number(data.get("importance")), related: data.getAll("related") };
    try {
      const result = await api(form.mode, payload);
      if (form.mode === "edit") form.initial = formSignature();
      else formDialog.close({ force: true });
      window.PortalUI.toast(window.PortalI18n.t("已保存"), "success");
      if (form.mode === "register") await loadProjects(result.project?.id || result.id || state.project);
      else if (form.project === state.project) {
        if (result.block?.id) {
          state.blocks.set(result.block.id, result.block);
          renderBoard();
        }
        if (form.mode === "edit" && state.detail?.id === form.block.id) {
          renderDetail({ ...state.detail, ...payload, ...(result.block || {}) });
        }
        await coordinator.refresh({ force: true }).catch(() => {});
      }
    } catch (error) {
      text(element("FormError"), error.message);
      element("FormError").hidden = false;
    } finally {
      form.saving = false;
      element("FormFields").inert = false;
      element("FormSave").disabled = false;
      text(element("FormSave"), "保存");
      renderDetailStatus();
    }
  }

  function renderReview(host, block, detail = false) {
    let input = host.querySelector("input");
    if (!input) {
      input = document.createElement("input");
      input.type = "checkbox";
      host.append(input, document.createTextNode("已审核"));
    }
    const pending = state.reviewPending.get(actionKey(state.project, block.id));
    const dirty = detail && state.form?.mode === "edit" && (state.form.saving || state.form.initial !== formSignature());
    input.dataset.reviewBlock = block.id;
    input.checked = pending && pending.read_revision === block.read_revision ? pending.reviewed : isReviewed(block);
    input.disabled = !block.read_revision || !!pending || completedSourceRemoved(block) || (detail && (state.detailLoading || !!state.detailError || dirty));
    host.title = dirty ? window.PortalI18n.t("请先保存修改，再审核当前内容") : pending ? window.PortalI18n.t("正在保存审核状态") : window.PortalI18n.t("已审核当前内容");
  }

  async function changeReview(event) {
    const input = event.target.closest("input[data-review-block]");
    if (!input || input.disabled) return;
    const project = state.project;
    const id = input.dataset.reviewBlock;
    const block = element("Detail").contains(input) ? state.detail : state.blocks.get(id);
    if (!block || block.id !== id || !block.read_revision) return;
    const key = actionKey(project, id);
    if (state.reviewPending.has(key)) return;
    const pending = { reviewed: input.checked, read_revision: block.read_revision };
    state.reviewPending.set(key, pending);
    renderBoard();
    try {
      const result = await api("review", { project, block: id, ...pending });
      if (result.block !== id || result.read_revision !== pending.read_revision || result.reviewed !== pending.reviewed) throw new Error("服务端未确认审核状态");
      if (project === state.project) state.reviewReceipts.set(key, result);
    } catch (error) {
      if (project === state.project) {
        window.PortalUI.toast(window.PortalI18n.text`审核状态保存失败：${error.message}`, "error");
        coordinator.refresh({ force: true }).catch(() => {});
      }
    } finally {
      state.reviewPending.delete(key);
      if (project === state.project) renderBoard();
    }
  }

  function handleBlockClick(event) {
    if (event.target.closest(".wallReview")) return;
    const control = event.target.closest("button");
    if (!control) {
      if (event.target.closest("a, input, textarea, select, [contenteditable]")) return;
      if (window.getSelection()?.isCollapsed === false) return;
      const card = event.target.closest(".wallCard");
      if (card) openDetail(card.dataset.blockId);
      return;
    }
    if (control.disabled) return;
    if (control.dataset.source) {
      if (isKnowledgeSource(control.dataset.source)) {
        resourceDetail.open({ resourceId: control.dataset.source, detailKind: "knowledge" });
      } else {
        sourceDetail.open({ resourceId: control.dataset.source, project: state.project, title: control.textContent });
      }
      return;
    }
    if (control.dataset.openBlock) { openDetail(control.dataset.openBlock); return; }
    const { action, block: id, jobId } = control.dataset;
    if (!action || (!id && !jobId)) return;
    if (action === "expand") {
      if (state.expanded.has(id)) state.expanded.delete(id); else state.expanded.add(id);
      renderBoard();
    } else runAction(action, id, jobId);
  }

  element("Board").addEventListener("click", handleBlockClick);
  element("Board").addEventListener("change", changeReview);
  element("Board").addEventListener("keydown", event => {
    if (event.target.matches(".wallCard") && ["Enter", " "].includes(event.key)) {
      event.preventDefault();
      openDetail(event.target.dataset.blockId);
    }
  });
  element("Jobs").addEventListener("click", handleBlockClick);
  element("Recent").addEventListener("click", () => {
    renderRecentCompleted();
    recentDialog.open({ focusTarget: element("RecentClose") });
  });
  element("RecentClose").addEventListener("click", () => recentDialog.close());
  element("RecentList").addEventListener("click", event => {
    const control = event.target.closest("button[data-completed-result]");
    if (!control) return;
    const record = (state.board?.recent_completed || []).find(item => item.id === control.closest("[data-completed-job]")?.dataset.completedJob);
    const result = record?.result_blocks?.find(item => item.id === control.dataset.completedResult);
    if (result) openDetail(result.id, result);
  });
  element("Detail").addEventListener("click", handleBlockClick);
  element("ChatTabs").addEventListener("click", event => {
    const tab = event.target.closest("[data-chat-tab]");
    if (!tab || !state.detail) return;
    state.chatTab = tab.dataset.chatTab;
    renderDetailStatus();
  });
  element("ChatTabs").addEventListener("keydown", event => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const target = event.key === "Home" ? "DiscussionTab" : event.key === "End" ? "WorkTab" : state.chatTab === "discussion" ? "WorkTab" : "DiscussionTab";
    element(target).click();
    element(target).focus();
  });
  element("Detail").addEventListener("change", changeReview);
  element("DetailClose").addEventListener("click", () => detailDialog.close());
  element("FormClose").addEventListener("click", () => formDialog.close());
  element("FormCancel").addEventListener("click", () => state.form?.mode === "edit" ? detailDialog.close() : formDialog.close());
  element("Form").addEventListener("submit", saveForm);
  element("Form").addEventListener("input", () => {
    if (state.detail) renderReview(element("DetailReview"), state.detail, true);
  });
  element("Create").addEventListener("click", () => openForm("create"));
  element("Documentation").addEventListener("click", generateDocumentation);
  element("DocumentationClose").addEventListener("click", () => documentationDialog.close());
  element("DocumentationDownload").addEventListener("click", downloadDocumentation);
  element("Register").addEventListener("click", () => openForm("register"));
  element("Unregister").addEventListener("click", unregisterProject);
  element("Account").addEventListener("change", () => { state.account = element("Account").value; saveViewPreferences(); });
  element("Settings").addEventListener("click", () => openForm("automation"));
  element("Analyse").addEventListener("click", () => runAction("analyse"));
  element("Verify").addEventListener("click", () => runAction("verify"));
  element("PublishAll").addEventListener("click", () => runAction("publish-all"));
  element("Refresh").addEventListener("click", () => {
    if (!state.project) { loadProjects(); return; }
    state.scan = true;
    coordinator.refresh({ force: true }).catch(() => {});
  });
  element("Project").addEventListener("change", event => selectProject(event.target.value));
  element("Query").addEventListener("input", event => {
    state.query = event.target.value;
    if (state.semanticSearch?.activeId()) state.semanticSearch.clearActive();
    renderBoard();
  });
  element("Query").addEventListener("keydown", event => {
    if (event.key !== "Enter" || event.isComposing) return;
    event.preventDefault();
    state.semanticSearch?.start().catch(error => window.PortalUI.toast(error.message || window.PortalI18n.t("语义搜索失败"), "error"));
  });
  element("Module").addEventListener("change", event => {
    state.module = event.target.value;
    saveViewPreferences();
    element("Board").scrollLeft = 0;
    renderBoard();
  });
  element("Filters").addEventListener("click", event => {
    const control = event.target.closest("button[data-kind]");
    if (control) { state.kind = control.dataset.kind; renderBoard(); }
  });
  for (const [name, key] of Object.entries(viewFields)) element(name).addEventListener("change", event => {
    state[key] = key === "minImportance" ? Number(event.target.value) : event.target.value;
    saveViewPreferences();
    if (["minImportance", "metanessFilter", "readFilter", "reviewFilter"].includes(key) || (key === "sort" && state.sort === "recent")) element("Board").scrollLeft = 0;
    renderBoard();
  });
  const wallSize = new ResizeObserver(sizeWall);
  wallSize.observe(element("Board"));
  let scrollFrame = 0;
  element("Board").addEventListener("scroll", () => {
    if (scrollFrame) return;
    scrollFrame = requestAnimationFrame(() => { scrollFrame = 0; renderVisibleCards(); });
  }, { passive: true });
  element("Board").addEventListener("wheel", event => {
    if (event.ctrlKey || event.shiftKey || Math.abs(event.deltaX) >= Math.abs(event.deltaY)) return;
    if (document.documentElement.scrollHeight > innerHeight + 1) return;
    const board = element("Board");
    if (board.scrollWidth <= board.clientWidth) return;
    event.preventDefault();
    board.scrollLeft += event.deltaY * (event.deltaMode === 1 ? 20 : event.deltaMode === 2 ? board.clientWidth : 1);
  }, { passive: false });
  window.addEventListener("pagehide", () => { coordinator.stop(); state.detailRequest?.abort(); destroyChat(); wallSize.disconnect(); sourceDetail.destroy(); });
  window.addEventListener("pageshow", event => { if (event.persisted && active && state.project) { coordinator.start(); wallSize.observe(element("Board")); } });
  window.PortalWall = Object.freeze({
    activate() {
      active = true;
      if (!initialized) { initialized = true; return loadProjects(); }
      coordinator.start();
      sizeWall();
    },
    deactivate() { active = false; coordinator.stop(); },
  });
  window.PortalDevelopmentWorkspace?.registerView("wall", window.PortalWall);
}());
