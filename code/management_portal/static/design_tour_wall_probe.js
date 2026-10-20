(function (global) {
  "use strict";

  const supported = new Set(["wall.design.point-resolution", "wall.design.read-revision-lifecycle", "wall.design.completed-result-history"]);
  const assert = (condition, message) => { if (!condition) throw new Error(`WALL 隔离验收：${message}`); };
  const check = signal => { if (signal?.aborted) throw new DOMException("WALL Live 已取消", "AbortError"); };
  async function wait(predicate, message, signal) {
    const started = performance.now();
    while (!predicate()) {
      check(signal);
      assert(performance.now() - started < 12000, message);
      await new Promise(resolve => setTimeout(resolve, 25));
    }
    check(signal);
  }

  function installBoundary() {
    const translate = value => window.parent.PortalI18n.t(value);
    history.replaceState(null, "", "/development?view=wall&project=wall-live-probe");
    const memoryStorage = () => {
      const values = new Map();
      return {
        get length() { return values.size; },
        key: index => Array.from(values.keys())[index] ?? null,
        getItem: key => values.get(String(key)) ?? null,
        setItem: (key, value) => values.set(String(key), String(value)),
        removeItem: key => values.delete(String(key)), clear: () => values.clear(),
      };
    };
    Object.defineProperty(window, "localStorage", { value: memoryStorage() });
    Object.defineProperty(window, "sessionStorage", { value: memoryStorage() });
    window.__PORTAL_IDENTITY_BOOTSTRAP__ = { username: "wall-live-probe" };
    const point = (id, body, unread = false) => ({
      id, title: translate("同名设计点 · 隔离验收"), content: body, preview: "仅含摘要，完整正文须读取精确点 ID。",
      kind: "design", status: "design", importance: 7, metaness: 2, domain: "隔离验收",
      source_id: "decision_06defc47e960", source_title: "隔离数据 · WALL 原生产组件",
      module_path: ["WALL", "只读验收"], read_revision: "revision-1", unread, reviewed: false,
      related: [], resident_id: "", job: {},
      test_binding: { status: "bound", case_id: "test_07677b2dd769", title: translate("隔离主测试关系"), url: "/wall?case=test_07677b2dd769" },
    });
    const state = {
      project: { id: "wall-live-probe", title: translate("隔离 WALL 验收"), automation: { enabled: false } },
      points: [point("fixture-wall-a", "# 完整正文 A\n\n只属于第一个精确点的正文。", true), point("fixture-wall-b", "# 完整正文 B\n\n只属于第二个精确点的正文。")],
      requests: [], blocked: [], failRead: false, mismatch: false, failBody: false,
      recent: [
        { id: "fixture-completed-new", title: translate("最近完成 · 可用结果"), completed_at: 2000, summary: "保留工作上下文 A", result_blocks: [{ id: "fixture-wall-b", title: translate("隐藏的同名成果"), kind: "design" }] },
        { id: "fixture-completed-old", title: translate("较早完成 · 已移除结果"), completed_at: 1000, summary: "保留工作上下文 B", result_blocks: [{ id: "fixture-wall-removed", title: translate("已移除的成果"), kind: "design" }] },
      ],
    };
    window.DesignTourWallBoundary = state;
    const originalFetch = window.fetch.bind(window);
    const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
    window.fetch = async (input, options = {}) => {
      const url = new URL(typeof input === "string" ? input : input.url, document.baseURI);
      const method = String(options.method || input?.method || "GET").toUpperCase();
      // Static files and the WALL view's template (the development page mounts it) are read as served.
      if (method === "GET" && url.origin === location.origin
        && (url.pathname.startsWith("/static/") || url.pathname === "/development/native-views")) return originalFetch(input, options);
      const request = { path: url.pathname, method, block: url.searchParams.get("block") || "" };
      if (url.pathname === "/api/instructions/execute") request.instruction = String(JSON.parse(options.body || "{}").instruction || "");
      state.requests.push(request);
      if (url.pathname === "/api/workbenches/initialize" && method === "POST" && ["test", "wall"].includes(JSON.parse(options.body || "{}").name)) {
        request.initializationStub = true;
        return json({});
      }
      // The site navigation reads the resource types for its Resource menu.
      if (request.instruction === "ListResourceTypes") return json({ status: "committed", result: { types: [] } });
      if (url.pathname === "/api/wall/read" && method === "POST") {
        const payload = JSON.parse(options.body || "{}");
        Object.assign(request, { block: payload.block, revision: payload.read_revision, inMemory: true });
        if (state.failRead) return json({ error: "隔离已读回执失败" }, 503);
        const block = state.points.find(item => item.id === payload.block);
        if (!block || block.read_revision !== payload.read_revision) return json({ read: false });
        block.unread = false;
        return json({ read: true, block: block.id, read_revision: block.read_revision });
      }
      if (method !== "GET" || url.origin !== location.origin) {
        state.blocked.push(request);
        return json({ error: "隔离WALL禁止访问真实写入或外部网络" }, 403);
      }
      if (url.pathname === "/api/wall/projects") return json({ projects: [state.project], capabilities: { write: false, register: false } });
      if (url.pathname === "/api/wall/board") return json({ project: state.project, blocks: state.points, loading: false, errors: [], jobs: [], recent_completed: state.recent, capabilities: { write: false, register: false } });
      if (url.pathname === "/api/wall/block") {
        if (state.failBody) return json({ error: "隔离正文读取失败" }, 503);
        const block = state.points.find(item => item.id === request.block);
        if (!block) return json({ error: "隔离成果已删除或无法访问" }, 404);
        return json({ block: { ...block, ...(state.mismatch ? { id: "fixture-wrong-point", content: "不得呈现的错误点正文" } : {}) } });
      }
      if (url.pathname === "/api/me") return json({ username: "wall-live-probe" });
      if (url.pathname === "/api/agent-conversations") return json({ schema: "tfi.portal.agent-conversation-index.v1", entries: [], unread_count: 0, content_signature: "isolated-empty" });
      if (url.pathname === "/api/users") return json([{ id: "wall-live-probe", name: translate("隔离验收"), type: "human" }]);
      if (url.pathname === "/__portal_router/preference") return json({ mode: "dev" });
      if (url.pathname === "/lineage/api/service-status") return json({ schema: "tfi.portal-component-status.v1", component: "lineage", state: "ready", ready: true });
      return json({ resources: [], searches: [], workbenches: [], residents: [], lineages: [], items: [], skills: [], agents: [] });
    };
    window.XMLHttpRequest = class { open() { throw new Error("隔离WALL禁止XHR旁路"); } };
    window.WebSocket = class { constructor() { throw new Error("隔离WALL禁止WebSocket"); } };
    window.EventSource = class { constructor() { throw new Error("隔离WALL禁止EventSource"); } };
    Object.defineProperty(navigator, "sendBeacon", { value: () => false });
    document.addEventListener("submit", event => event.preventDefault(), true);
    document.addEventListener("click", event => { if (event.target.closest("a[href]")) event.preventDefault(); }, true);
  }

  async function create(capability, signal) {
    assert(supported.has(capability), "未知能力");
    check(signal);
    // WALL is a view of the development page, which mounts it from the view's template.
    const response = await global.fetch("/development?view=wall", { signal });
    assert(response.ok && !response.redirected, "无法读取原生产开发投影页面");
    const html = new DOMParser().parseFromString(await response.text(), "text/html");
    assert(html.querySelector('script[src^="/static/development_workspace.js"]'), "开发投影页面未包含 WALL 视图宿主");
    const base = html.createElement("base");
    base.href = `${location.origin}/`;
    const boundary = html.createElement("script");
    boundary.textContent = `(${installBoundary.toString()})();`;
    html.head.prepend(base, boundary);
    const root = document.createElement("section");
    root.className = "portalDetailOverlay";
    root.dataset.designTourWallProbe = "";
    root.hidden = true;
    const surface = document.createElement("section");
    surface.className = "portalDetailSurface";
    surface.style.maxWidth = "96vw";
    const heading = document.createElement("h2");
    heading.textContent = window.PortalI18n.t("WALL 原生产页面 · 隔离数据验收（不写真实工程）");
    const frame = document.createElement("iframe");
    frame.title = heading.textContent;
    frame.style.width = "100%";
    frame.style.height = "72vh";
    frame.setAttribute("sandbox", "allow-scripts allow-same-origin");
    surface.append(heading, frame);
    root.append(surface);
    document.body.append(root);
    const dialog = global.PortalUI.createDialogController({ modal: root, escapeClose: true });
    let disposed = false;
    const dispose = () => {
      if (disposed) return;
      disposed = true;
      frame.contentWindow?.dispatchEvent(new Event("pagehide"));
      dialog.close({ force: true });
      dialog.destroy?.();
      root.remove();
    };
    try {
      dialog.open({ focusTarget: frame });
      frame.src = "/static/design_tour_wall_probe_host.html";
      await wait(() => frame.contentWindow?.location.pathname === "/static/design_tour_wall_probe_host.html" && frame.contentDocument?.readyState === "complete", "隔离加载容器未就绪", signal);
      frame.contentDocument.open();
      frame.contentDocument.write(`<!doctype html>${html.documentElement.outerHTML}`);
      frame.contentDocument.close();
      const view = () => frame.contentWindow;
      const doc = () => frame.contentDocument;
      const node = name => doc().getElementById(`wall${name}`);
      await wait(() => view()?.DesignTourWallBoundary && node("Board")?.querySelectorAll(".wallCard").length === 2, "原生产 WALL 未完成隔离数据加载", signal);
      const state = view().DesignTourWallBoundary;
      const detailClosed = async currentSignal => {
        if (node("Detail").hidden) return;
        node("DetailClose").click();
        await wait(() => node("Detail").hidden, "关闭点详情失败", currentSignal);
      };
      const open = id => {
        const control = Array.from(node("Board").querySelectorAll("button[data-open-block]")).find(button => button.dataset.openBlock === id);
        assert(control, "无法定位精确的点卡片");
        control.click();
      };
      const fullBody = (text, currentSignal) => wait(() => node("DetailContent").textContent.includes(text), "完整正文与精确点不符", currentSignal);
      const failedDetail = currentSignal => wait(() => node("DetailStatus").textContent.includes("失败"), "正文或回执失败未保留明确反馈", currentSignal);
      const refresh = async currentSignal => {
        const count = state.requests.filter(request => request.path === "/api/wall/board").length;
        node("Refresh").click();
        await wait(() => state.requests.filter(request => request.path === "/api/wall/board").length > count, "隔离刷新未读取新的版本", currentSignal);
        await new Promise(resolve => view().requestAnimationFrame(() => view().requestAnimationFrame(resolve)));
        check(currentSignal);
      };
      const unread = id => Array.from(node("Board").querySelectorAll(".wallCard")).find(card => card.dataset.blockId === id)?.classList.contains("unread");
      async function run(currentSignal) {
        check(currentSignal);
        assert(root.isConnected && !root.hidden, "隔离生产页面已关闭");
        if (capability === "wall.design.point-resolution") {
          open("fixture-wall-b");
          await fullBody("完整正文 B", currentSignal);
          assert(!node("DetailContent").textContent.includes("完整正文 A"), "同名点串读了其他正文");
          assert(state.requests.filter(request => request.path === "/api/wall/block").at(-1).block === "fixture-wall-b", "请求没有使用精确点ID");
          const link = doc().querySelector("[data-wall-test-binding] a[data-bound-test]");
          assert(link && link.dataset.boundTest === state.points[1].test_binding.case_id && new URL(link.href).searchParams.get("case") === link.dataset.boundTest, "主测试关系未指向服务端确认的同一Test");
          await detailClosed(currentSignal);
          state.mismatch = true;
          open("fixture-wall-a");
          await failedDetail(currentSignal);
          assert(!node("DetailContent").textContent.includes("不得呈现的错误点正文"), "错误ID响应覆盖了选中点");
          assert(!state.requests.some(request => request.path === "/api/wall/read"), "失败的正文读取不应确认已读");
          await detailClosed(currentSignal);
        } else if (capability === "wall.design.read-revision-lifecycle") {
          assert(unread("fixture-wall-a"), "初始版本缺少未读标记");
          open("fixture-wall-a");
          await fullBody("完整正文 A", currentSignal);
          await wait(() => !unread("fixture-wall-a"), "成功读取并确认后未清除该版本未读", currentSignal);
          assert(state.requests.some(request => request.inMemory && request.revision === "revision-1"), "确认的已读版本不匹配");
          await detailClosed(currentSignal);
          Object.assign(state.points[0], { content: "# 正文 A 第二版", read_revision: "revision-2", unread: true });
          state.failRead = true;
          await refresh(currentSignal);
          assert(unread("fixture-wall-a"), "新版本没有重新提示未读");
          open("fixture-wall-a");
          await fullBody("正文 A 第二版", currentSignal);
          await failedDetail(currentSignal);
          assert(unread("fixture-wall-a"), "回执失败丢失未读提示");
          await detailClosed(currentSignal);
          state.failRead = false;
          state.failBody = true;
          const receipts = state.requests.filter(request => request.inMemory).length;
          open("fixture-wall-a");
          await failedDetail(currentSignal);
          assert(unread("fixture-wall-a") && state.requests.filter(request => request.inMemory).length === receipts, "读取失败错误地确认或移除了未读");
          await detailClosed(currentSignal);
        } else {
          node("Query").value = "此关键词没有任何成果";
          node("Query").dispatchEvent(new (view().Event)("input", { bubbles: true }));
          assert(node("Board").querySelectorAll(".wallCard").length === 0 && !node("Empty").hidden, "空结果缺少可见反馈");
          const query = node("Query").value;
          node("Recent").click();
          await wait(() => !node("RecentOverlay").hidden, "最近完成入口未展开", currentSignal);
          const entries = Array.from(node("RecentList").children);
          assert(entries.length === 2 && entries[0].dataset.completedJob === state.recent[0].id, "完成工作上下文或顺序丢失");
          entries[0].querySelector("button[data-completed-result]").click();
          await fullBody("完整正文 B", currentSignal);
          await detailClosed(currentSignal);
          assert(!node("RecentOverlay").hidden && node("Query").value === query && node("Board").querySelectorAll(".wallCard").length === 0, "查看隐藏成果破坏原筛选或完成列表");
          entries[1].querySelector("button[data-completed-result]").click();
          await failedDetail(currentSignal);
          assert(node("RecentList").textContent.includes("保留工作上下文 B"), "成果不可访问时丢失历史记录");
          await detailClosed(currentSignal);
          node("RecentClose").click();
          assert(node("RecentOverlay").hidden && node("Query").value === query, "关闭历史列表未返回原墙面");
        }
        assert(state.blocked.length === 0, `生产组件尝试了隔离范围外的写操作：${state.blocked.map(request => `${request.method} ${request.path}${request.instruction ? ` ${request.instruction}` : ""}`).join(", ")}`);
        check(currentSignal);
        return { target: frame, requests: state.requests.length };
      }
      return { frame, run, dispose };
    } catch (error) {
      dispose();
      throw error;
    }
  }

  global.PortalDesignTourWallProbe = Object.freeze({ create });
})(window);
