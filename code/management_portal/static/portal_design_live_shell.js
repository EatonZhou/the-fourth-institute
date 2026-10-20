(function installDesignShell(global) {
  "use strict";

  global.PortalDesignLiveAdapters = global.PortalDesignLiveAdapters || {};
  if (global.PortalDesignLiveAdapters["design-shell"]) return;
  const capabilities = new Set([
    "shell.navigation-contract", "shell.secondary-reachability", "shell.secondary-escape",
    "shell.search-results", "shell.page-state-restore",
    "shell.refresh-followup", "shell.inbox-closed",
    "shell.draft-batch-ownership",
  ]);
  let runtime = null;

  function verify(condition, message) {
    if (!condition) throw new Error(`Shell Live: ${message}`);
  }

  function checkSignal(signal) {
    if (signal?.aborted) throw new DOMException("Shell Live cancelled", "AbortError");
  }

  async function until(predicate, signal, message) {
    const deadline = performance.now() + 6000;
    while (performance.now() < deadline) {
      checkSignal(signal);
      const result = predicate();
      if (result) return result;
      await new Promise(resolve => setTimeout(resolve, 25));
    }
    throw new Error(`Shell Live: ${message}`);
  }

  const paletteInput = () => document.getElementById("portalCommandInput");
  const paletteRows = () => Array.from(document.querySelectorAll("#portalCommandList [data-command-index]"));
  const commandKind = row => row.querySelector(".portalCommandKind")?.textContent.trim();
  const navRoot = () => document.querySelector("[data-portal-nav]");
  const navTrigger = () => document.querySelector('[data-module-link="resource"]');
  const userMenu = () => document.querySelector("[data-portal-user-menu]");

  // The navigation state a demonstration may change: Resource's secondary
  // menu, the focused main entry and the account menu.
  function captureNavigation() {
    return {
      focusModule: document.activeElement?.dataset?.moduleLink || "",
      openModule: global.PortalNav.secondaryNav.openModule(),
      userMenuOpen: userMenu()?.querySelector("[data-portal-user-trigger]")?.getAttribute("aria-expanded") === "true",
    };
  }

  function restoreNavigation(snapshot = {}) {
    global.PortalNav.secondaryNav.close();
    document.querySelectorAll("[data-portal-user-menu]").forEach(menu => global.PortalNav.userMenu.close(menu));
    if (snapshot.openModule === "resource") global.PortalNav.secondaryNav.open(navTrigger(), "resource");
    if (snapshot.focusModule === "resource") navTrigger()?.focus({ preventScroll: true });
    if (snapshot.userMenuOpen === true) global.PortalNav.userMenu.open(userMenu());
  }

  function setQuery(value) {
    const input = paletteInput();
    verify(input, "搜索输入框尚未挂载");
    input.value = value;
    input.dispatchEvent(new Event("input", { bubbles: true }));
  }

  function stage(title) {
    if (!runtime.root) {
      const root = document.createElement("section");
      root.dataset.designTourShell = "stage";
      root.className = "portalDetailSurface";
      root.setAttribute("aria-label", window.PortalI18n.t("Shell 生产组件只读验证"));
      Object.assign(root.style, {
        position: "fixed", inset: "20% 10% auto", zIndex: "1200", padding: "1.5rem",
        maxHeight: "65vh", overflow: "auto", background: "var(--panel, white)",
      });
      document.body.appendChild(root);
      runtime.root = root;
    }
    runtime.root.replaceChildren();
    const heading = document.createElement("h2");
    heading.textContent = title;
    runtime.root.appendChild(heading);
    return runtime.root;
  }

  async function loadPageState(signal) {
    if (global.PortalPageState) return;
    await new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = "/static/portal_page_state.js";
      const finish = error => {
        clearTimeout(timer);
        signal?.removeEventListener("abort", onAbort);
        script.onload = null;
        script.onerror = null;
        if (error) reject(error);
        else resolve();
      };
      const onAbort = () => finish(new DOMException("Shell Live cancelled", "AbortError"));
      const timer = setTimeout(() => finish(new Error("Shell Live: PageState 组件加载超时")), 6000);
      script.onload = () => finish();
      script.onerror = () => finish(new Error("Shell Live: PageState 组件加载失败"));
      signal?.addEventListener("abort", onAbort, { once: true });
      runtime.cleanups.push(() => script.remove());
      document.head.appendChild(script);
    });
    checkSignal(signal);
    verify(global.PortalPageState?.create, "PageState 生产 API 不可用");
  }

  function capture() {
    verify(!runtime, "已有演示尚未恢复，拒绝重复启动");
    const input = paletteInput();
    const snapshot = {
      nav: captureNavigation(),
      palette: { open: global.PortalCommandPalette.isOpen() },
      query: input?.value || "",
      selected: Number(document.querySelector("#portalCommandList .active")?.dataset.commandIndex || 0),
      paletteScroll: document.getElementById("portalCommandList")?.scrollTop || 0,
      focus: document.activeElement,
      selection: input ? [input.selectionStart, input.selectionEnd] : null,
      scroll: [global.scrollX, global.scrollY],
      navScroll: Array.from(document.querySelectorAll(".portalModuleNav, .portalNavMain"))
        .map(node => [node, node.scrollLeft, node.scrollTop]),
      inboxScroll: document.querySelector("[data-agent-conversation-list]")?.scrollTop || 0,
    };
    runtime = { snapshot, root: null, cleanups: [], signals: new Map(), inboxToggled: false, transaction: "" };
    return snapshot;
  }

  async function restore(snapshot) {
    const current = runtime;
    if (!current || current.snapshot !== snapshot) return;
    runtime = null;
    current.signals.forEach((listener, signal) => signal.removeEventListener("abort", listener));
    for (const cleanup of current.cleanups.reverse()) cleanup();
    current.root?.remove();
    if (current.transaction) global.PortalState.rollbackEphemeral(current.transaction);
    if (current.inboxToggled) document.querySelector("[data-agent-conversation-trigger]")?.dispatchEvent(new PointerEvent("click", { bubbles: true, pointerType: "touch" }));
    const inboxList = document.querySelector("[data-agent-conversation-list]");
    if (inboxList) inboxList.scrollTop = snapshot.inboxScroll;
    restoreNavigation(snapshot.nav);
    global.PortalCommandPalette.close();
    if (snapshot.palette.open) global.PortalCommandPalette.open();
    if (snapshot.palette.open) {
      setQuery(snapshot.query);
      let restoredIndex = snapshot.selected;
      try {
        await until(() => {
          const placeholder = document.querySelector("#portalCommandList .portalCommandEmpty");
          return !placeholder?.textContent.includes("Loading") && (paletteRows()[snapshot.selected] || placeholder);
        }, null, "原搜索选择不再可用");
      } catch {
        restoredIndex = 0;
      }
      const selected = paletteRows()[restoredIndex];
      selected?.dispatchEvent(new MouseEvent("mouseenter"));
      const list = document.getElementById("portalCommandList");
      if (list) list.scrollTop = snapshot.paletteScroll;
    }
    snapshot.navScroll.forEach(([node, left, top]) => { if (node.isConnected) { node.scrollLeft = left; node.scrollTop = top; } });
    if (snapshot.focus?.isConnected) snapshot.focus.focus({ preventScroll: true });
    if (snapshot.focus === paletteInput() && snapshot.selection?.[0] !== null) {
      paletteInput().setSelectionRange(...snapshot.selection);
    }
    global.scrollTo(...snapshot.scroll);
  }

  async function prepare(step, signal) {
    verify(runtime, "必须先 capture 再执行");
    checkSignal(signal);
    verify(capabilities.has(step?.capability), `未登记能力 ${step?.capability}`);
    if (signal && !runtime.signals.has(signal)) {
      const snapshot = runtime.snapshot;
      const listener = () => { void restore(snapshot); };
      runtime.signals.set(signal, listener);
      signal.addEventListener("abort", listener, { once: true });
    }
    let target = navRoot();
    if (step.capability.startsWith("shell.secondary")) {
      target = await secondary(signal, step.capability === "shell.secondary-reachability");
    }
    if (step.capability === "shell.search-results") {
      target = document.querySelector(".portalCommandLauncher");
    }
    if (step.capability === "shell.page-state-restore") {
      await loadPageState(signal);
      target = stage("页面偏好保存与恢复：隔离本地状态");
    }
    if (step.capability === "shell.draft-batch-ownership") {
      await loadPageState(signal);
      target = stage("本地草稿批次：隔离样例身份与页面");
    }
    if (step.capability === "shell.refresh-followup") {
      verify(global.PortalRefreshCoordinator?.create, "刷新协调器尚未加载");
      target = stage("刷新中的新变化：合并读取并保留后续更新");
    }
    if (step.capability === "shell.inbox-closed") {
      await until(() => global.PortalAgentInbox?.entries && document.querySelector("[data-agent-conversation-trigger]"), signal, "Inbox 未就绪");
      // Hydrate the read-only ISA state before commit installs its POST guard.
      target = await inboxClosed(signal);
    }
    verify(target?.isConnected, "准备阶段缺少连接到页面的生产组件");
    return { target, capability: step.capability };
  }

  async function secondary(signal, hover = false) {
    if (hover) {
      restoreNavigation();
      navTrigger().dispatchEvent(new MouseEvent("mouseenter"));
    } else {
      const trigger = navTrigger();
      verify(trigger?.isConnected, "资源导航入口尚未就绪");
      global.PortalNav.secondaryNav.open(trigger, "resource");
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      verify(trigger.getAttribute("aria-expanded") === "true", "resource 二级导航没有按预期打开");
    }
    return until(() => {
      const popover = document.getElementById("portalSecondaryNavPopover");
      return popover && !popover.hidden && popover;
    }, signal, "二级导航未打开");
  }

  function navigationContract() {
    const roots = Array.from(document.querySelectorAll("[data-portal-nav]"));
    const expected = global.PortalNav.items;
    verify(expected.length > 1 && roots.length, "导航定义为空");
    let order;
    for (const root of roots) {
      const links = Array.from(root.querySelectorAll("[data-module-link]"));
      const ids = links.map(link => link.dataset.moduleLink);
      verify(new Set(ids).size === ids.length, "主导航出现重复模块");
      for (const item of expected) {
        const link = links.find(node => node.dataset.moduleLink === item.id);
        verify(link && link.href === new URL(item.href, location.origin).href, `主模块 ${item.id} 缺失或地址错误`);
      }
      const active = links.filter(link => link.getAttribute("aria-current") === "page");
      // An open built-in or top workbench marks its own entry instead of the
      // page's declared module (decision_e486671af066).
      const isOpen = item => {
        const target = new URL(item.href, location.origin);
        return target.pathname === location.pathname
          && [...target.searchParams].every(([key, value]) => new URLSearchParams(location.search).get(key) === value);
      };
      const openWorkbench = expected.find(item => (item.builtin || item.projection) && isOpen(item));
      const activeId = openWorkbench?.id || root.dataset.portalNavActive || "";
      verify(active.length === (activeId ? 1 : 0) && (!activeId || active[0].dataset.moduleLink === activeId), "活动模块与页面声明不一致");
      verify(!order || order === ids.join("|"), "多个导航宿主顺序不一致");
      order = ids.join("|");
    }
    return roots[0];
  }

  async function searchResults(signal) {
    global.PortalCommandPalette.open();
    setQuery("Portal");
    const rows = await until(() => {
      const entries = paletteRows();
      return entries.some(row => commandKind(row) !== "Command") && entries;
    }, signal, "Portal 查询没有可验证的资源结果，请确认当前用户可发现至少一个匹配资源");
    const commands = rows.filter(row => commandKind(row) === "Command");
    const resources = rows.filter(row => commandKind(row) !== "Command");
    verify(commands.length === 0 && resources.length > 0, "全局资源搜索混入了页面命令或没有资源结果");
    verify(resources.every(row => /[a-z][a-z0-9_]*_[0-9a-f]{12}/.test(row.querySelector(".portalCommandSub")?.textContent || "")), "资源结果缺少可定位地址");
    const last = rows[rows.length - 1];
    last.dispatchEvent(new MouseEvent("mouseenter"));
    verify(last.classList.contains("active"), "指针选择没有作用于资源结果");
    return document.getElementById("portalCommandList");
  }

  function pageStateRestore(target) {
    const pageId = `design-shell:${globalThis.PortalUIPrimitives.randomUUID()}`;
    if (!global.PortalState.currentTransactionId()) {
      runtime.transaction = pageId;
      global.PortalState.beginEphemeral(pageId);
    }
    const options = { user: "design-shell-local", defaults: { query: "", view: "list" } };
    const create = () => {
      const controller = global.PortalPageState.create(pageId, options);
      runtime.cleanups.push(() => controller.destroy());
      return controller;
    };
    let controller = create();
    const input = document.createElement("input");
    input.setAttribute("aria-label", window.PortalI18n.t("演示筛选，仅临时事务"));
    input.value = controller.read().query;
    input.addEventListener("input", () => controller.debounceWrite({ query: input.value }, 10000));
    target.appendChild(input);
    input.value = "Portal 草稿";
    input.dispatchEvent(new Event("input", { bubbles: true }));
    controller.write({ view: "cards" });
    controller.destroy();
    input.remove();
    controller = create();
    const restored = controller.read();
    verify(restored.query === "Portal 草稿" && restored.view === "cards", "重建页面实例后丢失了筛选或视图偏好");
    const nextInput = document.createElement("input");
    nextInput.setAttribute("aria-label", window.PortalI18n.t("重建实例后的筛选"));
    nextInput.value = restored.query;
    target.appendChild(nextInput);
    const other = global.PortalPageState.create(pageId, { ...options, user: "design-shell-other" });
    runtime.cleanups.push(() => other.destroy());
    verify(other.read().query === "", "页面偏好泄漏到另一用户");
    verify(nextInput.value === restored.query, "恢复的状态未进入实际控件");
    return nextInput;
  }

  async function refreshFollowup(target, signal) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = window.PortalI18n.t("读取隔离快照");
    const output = document.createElement("output");
    output.setAttribute("aria-live", "polite");
    target.append(button, output);
    const pending = [];
    const applied = [];
    let reads = 0;
    let concurrent = 0;
    let maximum = 0;
    const coordinator = global.PortalRefreshCoordinator.create({
      key: "design-shell-refresh", interval: false, pauseWhenHidden: false,
      fetch: ({ signal: requestSignal }) => new Promise(resolve => {
        reads += 1;
        concurrent += 1;
        maximum = Math.max(maximum, concurrent);
        const version = reads;
        let completed = false;
        const release = () => {
          if (completed) return;
          completed = true;
          concurrent -= 1;
          requestSignal.removeEventListener("abort", release);
          resolve({ version });
        };
        pending.push(release);
        requestSignal.addEventListener("abort", release, { once: true });
      }),
      apply: update => { applied.push(update.version); output.value = `快照版本 ${update.version}`; },
    });
    runtime.cleanups.push(() => coordinator.destroy());
    const requests = [];
    button.addEventListener("click", () => {
      const request = coordinator.refresh();
      requests.push(request);
      button.setAttribute("aria-busy", "true");
      request.finally(() => button.setAttribute("aria-busy", String(coordinator.isRefreshing())));
    });
    coordinator.start();
    button.click();
    button.click();
    await until(() => pending.length === 1, signal, "首次读取未开始");
    verify(reads === 1 && coordinator.isRefreshing() && button.getAttribute("aria-busy") === "true", "重复刷新没有合并或缺少忙碌反馈");
    coordinator.invalidate();
    pending[0]();
    await until(() => pending.length === 2, signal, "读取中的新变化被丢弃，没有后续读取");
    pending[1]();
    await until(() => !coordinator.isRefreshing(), signal, "后续读取没有结束");
    await Promise.all(requests);
    verify(reads === 2 && maximum === 1 && applied.join(",") === "1,2", "刷新没有按顺序应用两版数据");
    verify(output.value === "快照版本 2" && button.getAttribute("aria-busy") === "false", "最新快照或完成反馈没有显示");
    return output;
  }

  async function inboxClosed(signal) {
    const trigger = document.querySelector("[data-agent-conversation-trigger]");
    if (trigger.getAttribute("aria-expanded") !== "true") {
      trigger.dispatchEvent(new PointerEvent("click", { bubbles: true, pointerType: "touch" }));
      runtime.inboxToggled = true;
    }
    const entries = await until(() => {
      const entries = global.PortalAgentInbox.entries();
      if (!entries.some(entry => entry.intake_state === "closed")) return false;
      // Store notifications schedule rendering; data arrival precedes DOM ready.
      return entries.every(entry => {
        const card = document.querySelector(`[data-agent-conversation-key="${CSS.escape(entry.key)}"]`);
        return card && Boolean(card.querySelector(".portalAgentInboxStatus.closed")) === (entry.intake_state === "closed");
      }) && entries;
    }, signal, "当前 Inbox 没有 closed 样本；未改动真实 Resident 状态");
    let target;
    for (const entry of entries) {
      const card = document.querySelector(`[data-agent-conversation-key="${CSS.escape(entry.key)}"]`);
      verify(card, "会话索引中的条目没有渲染到 Inbox");
      const badge = card.querySelector(".portalAgentInboxStatus.closed");
      verify(!!badge === (entry.intake_state === "closed"), "closed 标记与接收状态不一致");
      if (!badge) continue;
      verify(badge.textContent.trim() === "closed" && badge.getAttribute("aria-label") === "消息接收已关闭", "closed 标记缺少明确文字或辅助阅读语义");
      verify(!card.disabled && card.dataset.status !== "closed", "接收状态覆盖工作状态或使历史入口不可用");
      if (entry.unread) verify(card.querySelector(".portalAgentInboxUnreadDot"), "closed 卡片丢失未读提示");
      target = card;
    }
    target.scrollIntoView({ block: "nearest" });
    return target;
  }

  async function draftBatchOwnership(target, signal) {
    const pageId = `design-shell-batch:${globalThis.PortalUIPrimitives.randomUUID()}`;
    if (!global.PortalState.currentTransactionId()) {
      runtime.transaction = pageId;
      global.PortalState.beginEphemeral(pageId);
    }
    let actor = "shell-local-a";
    const defaults = { draft: "", view: "list" };
    const create = (page, options) => {
      const controller = global.PortalPageState.create(page, { defaults, ...options });
      runtime.cleanups.push(() => controller.destroy());
      return controller;
    };
    const controller = create(pageId, { getUser: () => actor });
    const firstUser = create(pageId, { user: "shell-local-a" });
    const secondUser = create(pageId, { user: "shell-local-b" });
    const otherPage = create(`${pageId}:other`, { user: "shell-local-a" });
    const input = document.createElement("input");
    input.setAttribute("aria-label", window.PortalI18n.t("隔离样例草稿"));
    input.addEventListener("input", () => controller.debounceWrite({ draft: input.value }, 10000));
    const view = document.createElement("select");
    view.setAttribute("aria-label", window.PortalI18n.t("隔离样例视图"));
    for (const value of ["list", "cards"]) {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = value;
      view.appendChild(option);
    }
    view.addEventListener("change", () => controller.debounceWrite({ view: view.value }, 10000));
    const switchUser = document.createElement("button");
    switchUser.type = "button";
    switchUser.textContent = window.PortalI18n.t("切换隔离样例身份");
    switchUser.addEventListener("click", () => { actor = "shell-local-b"; });
    const result = document.createElement("output");
    result.setAttribute("aria-live", "polite");
    target.append(input, view, switchUser, result);
    const type = value => { input.value = value; input.dispatchEvent(new Event("input", { bubbles: true })); };
    type("A 初稿");
    view.value = "cards";
    view.dispatchEvent(new Event("change", { bubbles: true }));
    type("A 最新草稿");
    await Promise.resolve();
    checkSignal(signal);
    switchUser.click();
    type("B 初稿");
    const first = firstUser.read();
    verify(first.draft === "A 最新草稿" && first.view === "cards", "身份变化时旧批次归属错误或合并字段丢失");
    verify(secondUser.read().draft === "", "新身份批次被提前落入错误的状态");
    type("B 最新草稿");
    controller.flush();
    const second = secondUser.read();
    verify(second.draft === "B 最新草稿" && second.view === "list", "新批次继承了另一身份的字段或丢失最后一次输入");
    verify(firstUser.read().draft === first.draft && otherPage.read().draft === "", "当前编辑覆盖了另一身份或页面的草稿");
    result.value = `A: ${first.draft} / ${first.view}；B: ${second.draft} / ${second.view}；另一页面未改动`;
    verify(input.value === second.draft, "最新草稿没有保留在实际输入控件中");
    return result;
  }

  async function commit(step, prepared, signal) {
    checkSignal(signal);
    verify(runtime && prepared?.target?.isConnected && prepared.capability === step.capability, "目标已失效或步骤与准备结果不匹配");
    let target = prepared.target;
    if (step.capability === "shell.navigation-contract") target = navigationContract();
    else if (step.capability === "shell.secondary-reachability") {
      target = await secondary(signal, true);
      const links = Array.from(target.querySelectorAll("a[href]"));
      verify(links.length === global.PortalNav.secondaryItems("resource").length && links.length > 0, "二级导航入口不完整");
      for (const link of links) {
        link.scrollIntoView({ block: "nearest", inline: "nearest" });
        const rect = link.getBoundingClientRect();
        const viewport = document.documentElement;
        verify(rect.width > 0 && rect.height > 0 && rect.left >= 0 && rect.right <= viewport.clientWidth + 1 && rect.top >= 0 && rect.bottom <= viewport.clientHeight + 1, "当前视口中二级入口被裁切且不可达");
        verify(link.contains(document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2)), "二级入口被其他元素遮挡");
      }
    } else if (step.capability === "shell.secondary-escape") {
      const popover = await secondary(signal);
      popover.querySelector("a")?.focus();
      document.activeElement.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }));
      await until(() => popover.hidden, signal, "Escape 没有关闭二级导航");
      verify(document.activeElement === navTrigger() && navTrigger().getAttribute("aria-expanded") === "false", "菜单关闭后焦点未返回触发入口");
      await secondary(signal);
      document.body.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      verify(popover.hidden && navTrigger().getAttribute("aria-expanded") === "false", "外部点击没有关闭二级导航");
      target = navTrigger();
    }
    else if (step.capability === "shell.search-results") target = await searchResults(signal);
    else if (step.capability === "shell.page-state-restore") target = pageStateRestore(target);
    else if (step.capability === "shell.refresh-followup") target = await refreshFollowup(target, signal);
    else if (step.capability === "shell.inbox-closed") target = await inboxClosed(signal);
    else if (step.capability === "shell.draft-batch-ownership") target = await draftBatchOwnership(target, signal);
    else throw new Error(`Shell Live: 未登记能力 ${step.capability}`);
    checkSignal(signal);
    verify(target?.isConnected, "验收目标未连接到页面");
    return { target, caption: step.caption };
  }

  global.PortalDesignLiveAdapters["design-shell"] = {
    id: "design-shell", version: 1,
    ready: () => !!(global.PortalNav?.secondaryNav && navTrigger() && global.PortalCommandPalette?.isOpen
      && document.querySelector(".portalCommandLauncher") && document.getElementById("portalCommandOverlay")
      && global.PortalState),
    capture, prepare, commit, restore,
  };
})(window);
