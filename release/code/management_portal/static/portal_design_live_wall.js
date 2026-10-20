(function (global) {
  "use strict";

  const controls = ["Module", "ImportanceFilter", "MetanessFilter", "ReadFilter", "ReviewFilter", "Cluster", "Sort", "Zoom"];
  const isolatedCapabilities = new Set(["wall.design.point-resolution", "wall.design.read-revision-lifecycle", "wall.design.completed-result-history"]);
  const capabilities = new Set([
    "wall.design.stage-order", "wall.design.horizontal-reading", "wall.design.metaness-filter",
    "wall.design.combined-filter", "wall.design.sort-order", "wall.design.unread-filter",
    "wall.design.review-filter", "wall.design.source-roundtrip",
    ...isolatedCapabilities,
  ]);
  const stageOrder = ["improvement", "dev", "design", "inconsistency"];
  const element = name => document.getElementById(`wall${name}`);
  const cards = () => Array.from(element("Board").querySelectorAll(".wallCard"));
  const cardIds = () => cards().map(card => card.dataset.blockId);
  const category = block => block.status === "dev" ? "dev" : block.kind;
  const stage = block => block.kind === "direction" ? "improvement" : category(block);
  const score = block => Math.max(1, Math.min(10, Number(block.importance) || 1));
  const modulePath = block => Array.isArray(block.module_path) ? block.module_path : [];
  const searchable = block => [block.title, block.preview, block.content, block.source_id, block.source_title, block.domain, ...modulePath(block)].join(" ").toLocaleLowerCase();
  const assert = (condition, message) => { if (!condition) throw new Error(`WALL Live：${message}`); };
  let active = null;
  let probeLoad = null;
  const onTestWorkspace = () => document.body.dataset.portalWorkbenchEntry === "test";

  async function loadProbe(signal) {
    checkSignal(signal);
    if (!global.PortalDesignTourWallProbe) {
      if (!probeLoad) probeLoad = new Promise((resolve, reject) => {
        const script = document.createElement("script");
        script.src = "/static/design_tour_wall_probe.js";
        script.onload = () => resolve();
        script.onerror = () => { script.remove(); probeLoad = null; reject(new Error("WALL 隔离验收组件加载失败")); };
        document.head.append(script);
      });
      await probeLoad;
    }
    checkSignal(signal);
    assert(global.PortalDesignTourWallProbe, "缺少隔离验收组件");
  }

  function checkSignal(signal) {
    if (signal?.aborted) throw new DOMException("WALL Live 已取消", "AbortError");
  }

  async function settle(signal) {
    checkSignal(signal);
    await new Promise(resolve => global.requestAnimationFrame(() => global.requestAnimationFrame(resolve)));
    checkSignal(signal);
  }

  async function until(predicate, message, signal) {
    const started = performance.now();
    while (!predicate()) {
      checkSignal(signal);
      assert(performance.now() - started < 6000, message);
      await new Promise(resolve => global.setTimeout(resolve, 30));
    }
    checkSignal(signal);
  }

  function select(name, value) {
    const control = element(name);
    assert(control && !control.disabled, `${name} 控件不可用`);
    assert(Array.from(control.options).some(option => option.value === String(value)), `${name} 缺少选项 ${value}`);
    if (control.value === String(value)) return;
    control.value = String(value);
    control.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function query(value) {
    const input = element("Query");
    if (input.value === value) return;
    input.value = value;
    input.dispatchEvent(new Event("input", { bubbles: true }));
  }

  function kind(value) {
    const button = element("Filters").querySelector(`[data-kind="${value}"]`);
    assert(button && !button.disabled, `类别 ${value} 不可用`);
    if (button.getAttribute("aria-pressed") !== "true") button.click();
  }

  async function assertIds(expected, message) {
    const board = element("Board");
    const originalLeft = board.scrollLeft;
    const seen = new Set();
    const samples = [];
    const collect = () => {
      const mounted = cardIds();
      assert(new Set(mounted).size === mounted.length, "墙面存在重复点");
      mounted.forEach(id => seen.add(id));
      samples.push(Array.from(board.querySelectorAll(".wallGrid"), grid =>
        Array.from(grid.children, card => card.dataset.blockId)));
    };
    collect();
    try {
      if (seen.size < expected.length) {
        const end = Math.max(0, board.scrollWidth - board.clientWidth);
        const step = Math.max(1, board.clientWidth);
        for (let left = 0; ; left = Math.min(end, left + step)) {
          board.scrollLeft = left;
          await settle(active.signal);
          collect();
          if (left === end) break;
        }
      }
    } finally {
      board.scrollLeft = originalLeft;
      await settle(active.signal);
    }
    const actual = [...seen];
    const identifiers = expected.map(block => block.id);
    assert(actual.length === identifiers.length && actual.every(id => identifiers.includes(id)), message);
    active.visibleBlocks = expected;
    active.samples = samples;
  }

  function assertStages() {
    const stages = Array.from(element("Board").querySelectorAll(".wallStage"));
    const expected = stageOrder.filter(name => active.visibleBlocks.some(block => stage(block) === name));
    assert(JSON.stringify(stages.map(node => node.dataset.stage)) === JSON.stringify(expected), "阶段顺序或阶段集合不正确");
    for (const section of stages) {
      assert(Array.from(section.querySelectorAll(".wallCard")).every(card => stage(active.blocks.get(card.dataset.blockId)) === section.dataset.stage), "点被分组到错误阶段");
    }
  }

  function assertOrder(mode) {
    const ranks = new Map((active.preferences.recentOpenings || []).map((key, index) => [key, index]));
    const compare = (left, right) => {
      if (mode === "recent") {
        const delta = (ranks.get(`${active.project}:${left.id}`) ?? ranks.size) - (ranks.get(`${active.project}:${right.id}`) ?? ranks.size);
        if (delta) return delta;
      }
      const byTitle = String(left.title).localeCompare(String(right.title), "zh-CN");
      return mode === "title" ? byTitle : score(right) - score(left) || byTitle;
    };
    for (const identifiers of active.samples.flat()) {
      const expected = identifiers.map(id => active.blocks.get(id)).sort(compare).map(block => block.id);
      assert(JSON.stringify(identifiers) === JSON.stringify(expected), `${mode} 排序未在聚类内部生效`);
    }
    assertStages();
  }

  async function closeSource() {
    if (element("SourceOverlay") && !element("SourceOverlay").hidden) {
      element("SourceClose").click();
      await until(() => element("SourceOverlay").hidden, "本地来源详情未关闭，无法恢复墙面");
      return;
    }
    if (element("ResourceDetail").hidden) return;
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }));
    await until(() => element("ResourceDetail").hidden, "来源详情未关闭，无法恢复墙面");
  }

  async function capture() {
    assert(!active, "已有 WALL Live 正在执行");
    assert(adapter.ready(), "墙面尚未就绪");
    if (onTestWorkspace()) {
      active = { isolated: true, focus: document.activeElement, windowX: global.scrollX, windowY: global.scrollY };
      return active;
    }
    assert(!new URL(location.href).searchParams.has("semantic"), "请先退出已激活的语义搜索，再播放只读演示");
    assert(!document.querySelector('[data-portal-detail-overlay]:not([hidden])'), "请先关闭现有详情或编辑面板，避免干扰未保存内容");
    const project = element("Project").value;
    let payload;
    const started = performance.now();
    do {
      payload = await global.PortalAPI.get(`/api/wall/board?${new URLSearchParams({ project })}`);
      assert(element("Project").value === project, "读取期间工程已切换");
      if (!payload.loading) break;
      assert(performance.now() - started < 30000, "工程文档扫描未完成，请等待墙面就绪后重试");
      await new Promise(resolve => global.setTimeout(resolve, 250));
    } while (true);
    assert(payload.project?.id === project && !payload.loading && Array.isArray(payload.blocks) && payload.blocks.length, "需要已加载且非空的工程墙面");
    assert(element("Project").value === project, "读取期间工程已切换");
    const user = global.PortalIdentity.username();
    const existingTransaction = global.PortalState.currentTransactionId();
    const snapshot = {
      project, user, blocks: new Map(payload.blocks.map(block => [block.id, block])),
      values: Object.fromEntries(controls.map(name => [name, element(name).value])),
      query: element("Query").value,
      querySelection: { start: element("Query").selectionStart, end: element("Query").selectionEnd, direction: element("Query").selectionDirection },
      kind: element("Filters").querySelector('[aria-pressed="true"]').dataset.kind,
      preferences: structuredClone(global.PortalState.read("wall", user)),
      focus: document.activeElement, scrollLeft: element("Board").scrollLeft, scrollTop: element("Board").scrollTop,
      windowX: global.scrollX, windowY: global.scrollY,
      transaction: existingTransaction || global.PortalState.beginEphemeral(`design-wall:${globalThis.PortalUIPrimitives.randomUUID()}`),
      ownsTransaction: !existingTransaction,
    };
    active = snapshot;
    return snapshot;
  }

  async function prepare(step, signal) {
    checkSignal(signal);
    active.signal = signal;
    assert(capabilities.has(step?.capability), "未实现的 Live 能力");
    if (isolatedCapabilities.has(step.capability)) {
      assert(active, "缺少播放快照");
      assert(!active.probe, "已有隔离验收尚未恢复");
      await loadProbe(signal);
      active.probe = await global.PortalDesignTourWallProbe.create(step.capability, signal);
      return { target: active.probe.frame, capability: step.capability };
    }
    assert(active && element("Project").value === active.project, "缺少当前工程快照");
    query("");
    kind("all");
    for (const [name, value] of Object.entries({ Module: "", ImportanceFilter: "0", MetanessFilter: "all", ReadFilter: "all", ReviewFilter: "all", Cluster: "domain", Sort: "importance", Zoom: "cards" })) select(name, value);
    await settle(signal);
    await assertIds(Array.from(active.blocks.values()), "当前卡片与读取快照不一致，请等待墙面稳定后重试");
    return { target: element("Board"), capability: step.capability };
  }

  async function commit(step, prepared, signal) {
    checkSignal(signal);
    assert(active && prepared?.capability === step?.capability && capabilities.has(step.capability), "Live 步骤与准备结果不一致");
    assert(prepared.target?.isConnected, "Live 目标已脱离页面");
    if (isolatedCapabilities.has(step.capability)) {
      assert(active.probe?.frame === prepared.target, "隔离验收目标不匹配");
      const result = await active.probe.run(signal);
      return { target: result.target, caption: step.caption };
    }
    const blocks = Array.from(active.blocks.values());
    let target = element("Board");
    if (step.capability === "wall.design.stage-order") {
      assert(new Set(blocks.map(stage)).size > 1, "阶段验收需要至少两个阶段的点");
      for (const grouping of ["document", "domain"]) {
        select("Cluster", grouping);
        await settle(signal);
        await assertIds(blocks, "切换聚类丢失或增加了点");
        assertStages();
      }
      for (const categoryName of new Set(blocks.map(category))) {
        kind(categoryName);
        await assertIds(blocks.filter(block => category(block) === categoryName), "类别筛选未隔离对应阶段的点");
        assertStages();
      }
      kind("all");
    } else if (step.capability === "wall.design.horizontal-reading") {
      const board = element("Board");
      const before = new Map(cards().map(card => [card.dataset.blockId, card.offsetWidth * card.offsetHeight]));
      select("Zoom", "detail");
      await settle(signal);
      await assertIds(blocks, "详细阅读缩放改变了点集合");
      assert(cards().some(card => card.offsetWidth * card.offsetHeight > before.get(card.dataset.blockId)), "阅读尺度未改变实际卡片面积");
      assert(board.scrollWidth > board.clientWidth, "当前内容不足以验收横向滚动");
      board.scrollLeft = board.scrollWidth;
      await settle(signal);
      assert(board.scrollLeft > 0, "墙面无法横向滚动");
      const viewport = board.getBoundingClientRect();
      assert(cards().some(card => {
        const rectangle = card.getBoundingClientRect();
        return rectangle.right > viewport.left && rectangle.left < viewport.right;
      }), "滚动后的可视区域没有可读卡片");
      for (const grid of board.querySelectorAll(".wallGrid")) {
        for (const card of grid.children) {
          assert(card.offsetWidth > 0 && card.offsetHeight > 0, "布局产生零面积卡片");
          assert(card.offsetTop >= 0 && card.offsetTop + card.offsetHeight <= grid.offsetHeight + 1, "卡片超出所在分组的可用高度");
        }
      }
    } else if (step.capability === "wall.design.metaness-filter") {
      const valid = block => block.metaness === undefined || (Number.isInteger(block.metaness) && block.metaness >= 1 && block.metaness <= 10);
      const levels = new Set(blocks.filter(valid).map(block => block.metaness ?? 1));
      assert(levels.size > 1, "元层级验收需要不同层级的点");
      for (const level of [1, ...levels]) {
        select("MetanessFilter", String(level));
        await assertIds(blocks.filter(block => valid(block) && (block.metaness ?? 1) === level), `M${level} 筛选集合错误`);
      }
      select("MetanessFilter", "all");
      await assertIds(blocks, "全部元层级未恢复默认值及非法值诊断点");
      target = element("MetanessFilter");
    } else if (step.capability === "wall.design.combined-filter") {
      const candidate = blocks.find(block => modulePath(block).length >= 2 && score(block) >= 2 && String(block.title || "").trim());
      assert(candidate, "组合筛选验收需要具有二级模块及标题的点");
      const path = modulePath(candidate);
      for (const depth of [1, 2]) {
        select("Module", JSON.stringify(path.slice(0, depth)));
        await assertIds(blocks.filter(block => path.slice(0, depth).every((part, index) => modulePath(block)[index] === part)), "模块筛选未包含正确的子模块");
      }
      select("ImportanceFilter", String(score(candidate)));
      query(String(candidate.title));
      const expected = blocks.filter(block => path.slice(0, 2).every((part, index) => modulePath(block)[index] === part) && score(block) >= score(candidate) && searchable(block).includes(String(candidate.title).toLocaleLowerCase()));
      await assertIds(expected, "模块、重要度与关键词未按交集筛选");
      assert(expected.length > 0 && expected.length < blocks.length, "组合筛选没有缩小范围");
      target = element("Query");
    } else if (step.capability === "wall.design.sort-order") {
      assert(Array.from(element("Sort").options).map(option => option.value).join(",") === "importance,title,recent", "排序选项偏离重要、标题、最近打开契约");
      select("Cluster", "document");
      assert(Array.from(element("Board").querySelectorAll(".wallGrid")).some(grid => grid.children.length > 1), "排序验收需要同组中的多个点");
      for (const mode of ["title", "importance", "recent"]) {
        select("Sort", mode);
        await assertIds(blocks, "排序改变了点集合");
        assertOrder(mode);
      }
      target = element("Sort");
    } else if (step.capability === "wall.design.unread-filter") {
      const unread = blocks.filter(block => block.unread === true);
      assert(unread.length && unread.length < blocks.length, "阅读筛选验收需要同时存在已读和未读点");
      select("ReadFilter", "unread");
      await assertIds(unread, "未读筛选与权威状态不一致");
      assert(cards().every(card => !card.querySelector(".wallUnreadDot").hidden), "未读筛选与卡片未读标记不一致");
      select("ReadFilter", "all");
      await assertIds(blocks, "清除阅读筛选未恢复原集合");
      target = element("ReadFilter");
    } else if (step.capability === "wall.design.review-filter") {
      const reviewed = blocks.filter(block => block.reviewed === true);
      assert(reviewed.length && reviewed.length < blocks.length, "审核筛选验收需要同时存在已审核和未审核点");
      for (const value of ["reviewed", "unreviewed"]) {
        select("ReviewFilter", value);
        await assertIds(blocks.filter(block => (block.reviewed === true) === (value === "reviewed")), "审核筛选返回错误的点集合");
        assert(cards().every(card => card.querySelector("input[data-review-block]")?.checked === (value === "reviewed")), "审核筛选与卡片状态不一致");
      }
      select("ReviewFilter", "all");
      await assertIds(blocks, "清除审核筛选未恢复原集合");
      target = element("ReviewFilter");
    } else if (step.capability === "wall.design.source-roundtrip") {
      const source = element("Board").querySelector(".wallCardSource button[data-source]");
      assert(source, "来源验收需要可读取的 Design 或 Plan 来源");
      const identifiers = cardIds();
      const currentURL = location.href;
      const scrollLeft = element("Board").scrollLeft;
      source.click();
      const knowledge = /^(decision|plan|thinking|information|procedure|evaluation|research|discussion)_[a-f0-9]{12}$/.test(source.dataset.source);
      if (knowledge) {
        await until(() => !element("ResourceDetail").hidden && element("ResourceDetail").classList.contains("ready"), "来源资源详情未完成加载", signal);
        const frameURL = new URL(element("ResourceFrame").contentWindow.location.href);
        assert(frameURL.origin === location.origin && frameURL.searchParams.get("item") === source.dataset.source, "来源详情打开了错误资源");
      } else {
        await until(() => !element("SourceOverlay").hidden
          && element("SourceStatus").textContent === source.dataset.source
          && element("SourceContent").childNodes.length > 0, "本地来源详情未完成加载", signal);
        assert(element("SourceError").hidden, "本地来源详情读取失败");
      }
      assert(location.href === currentURL, "打开来源离开了当前墙面");
      await closeSource();
      checkSignal(signal);
      assert(JSON.stringify(cardIds()) === JSON.stringify(identifiers), "关闭来源改变了墙面点序列");
      assert(element("Board").scrollLeft === scrollLeft, "来源往返丢失墙面阅读位置");
      target = source;
    }
    await settle(signal);
    assert(target.isConnected, "验收目标已脱离页面");
    return { target, caption: step.caption };
  }

  async function restore(snapshot) {
    if (!snapshot || active !== snapshot) return;
    if (snapshot.isolated) {
      try {
        snapshot.probe?.dispose();
        global.scrollTo(snapshot.windowX, snapshot.windowY);
        if (snapshot.focus?.isConnected) snapshot.focus.focus({ preventScroll: true });
      } finally { active = null; }
      return;
    }
    try {
      snapshot.probe?.dispose();
      await closeSource();
      assert(element("Project").value === snapshot.project && global.PortalIdentity.username() === snapshot.user, "用户或工程已切换，拒绝覆盖新页面状态");
      for (const [name, value] of Object.entries(snapshot.values)) select(name, value);
      kind(snapshot.kind);
      query(snapshot.query);
      global.PortalState.replace("wall", snapshot.user, snapshot.preferences);
      await settle();
      element("Board").scrollLeft = snapshot.scrollLeft;
      element("Board").scrollTop = snapshot.scrollTop;
      global.scrollTo(snapshot.windowX, snapshot.windowY);
      if (snapshot.focus?.isConnected) snapshot.focus.focus({ preventScroll: true });
      if (snapshot.querySelection.start !== null) element("Query").setSelectionRange(snapshot.querySelection.start, snapshot.querySelection.end, snapshot.querySelection.direction);
    } finally {
      if (snapshot.ownsTransaction) global.PortalState.rollbackEphemeral(snapshot.transaction);
      active = null;
    }
  }

  const adapter = {
    id: "design-wall", version: 1,
    ready: () => Boolean((onTestWorkspace() && global.PortalUI?.createDialogController) || (!onTestWorkspace() && element("Board") && element("Project")?.value && !element("Project").disabled && global.PortalState?.beginEphemeral && global.PortalAPI && global.WallLayout)),
    capture, prepare, commit, restore,
  };
  global.PortalDesignLiveAdapters = global.PortalDesignLiveAdapters || {};
  global.PortalDesignLiveAdapters[adapter.id] = Object.freeze(adapter);
})(window);
