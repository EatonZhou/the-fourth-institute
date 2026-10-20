(function installResourceDesignLive(global) {
  "use strict";

  global.PortalDesignLiveAdapters = global.PortalDesignLiveAdapters || {};
  if (global.PortalDesignLiveAdapters["design-resource"]) return;

  const capabilities = new Set([
    "resource.catalog.filters",
    "resource.catalog.category",
    "resource.references.canonical",
    "resource.references.locators",
    "resource.detail.latest",
    "resource.detail.dirty-guard",
    "resource.preview.renderers",
    "resource.files.preview-host",
    "resource.preview.typed-routing",
    "resource.detail.frame-readiness",
    "resource.detail.external-history",
    "resource.preview.cancel-updates",
  ]);
  const assets = [
    ["PortalResourceRoutes", "/static/portal_resource_routes.js"],
    ["PortalResourceChips", "/static/portal_resource_chips.js"],
    ["PortalFileLinks", "/static/portal_file_links.js"],
    ["MD", "/static/markdown.js"],
    ["PortalPreviewContract", "/static/portal_preview_contract.js"],
    ["PortalPreview", "/static/portal_preview_runtime.js"],
    ["PortalPdfPreview", "/static/portal_pdf_preview.js"],
    ["PortalPreviewRenderers", "/static/portal_preview_renderers.js"],
  ];
  const loads = new Map();
  let active = null;

  function requireContract(condition, message) {
    if (!condition) throw new Error(`Resource Live 验收失败：${message}`);
  }

  function check(run, signal) {
    if (signal?.aborted || run.aborter.signal.aborted || active !== run) {
      throw new DOMException("Resource Live 已取消", "AbortError");
    }
  }

  async function until(predicate, run, signal) {
    const deadline = performance.now() + 10000;
    while (performance.now() < deadline) {
      check(run, signal);
      const result = predicate();
      if (result) return result;
      await new Promise(resolve => global.setTimeout(resolve, 25));
    }
    throw new Error("Resource Live 等待生产组件完成超时");
  }

  function loadAsset(name, path) {
    if (global[name]) return Promise.resolve();
    if (loads.has(path)) return loads.get(path);
    const promise = new Promise((resolve, reject) => {
      const script = document.createElement("script");
      const timer = global.setTimeout(() => finish(new Error(`加载超时：${path}`)), 10000);
      const finish = error => {
        global.clearTimeout(timer);
        script.onload = null;
        script.onerror = null;
        if (error) {
          script.remove();
          reject(error);
        } else resolve();
      };
      script.src = path;
      script.onload = () => finish(global[name] ? null : new Error(`组件未注册：${name}`));
      script.onerror = () => finish(new Error(`加载失败：${path}`));
      document.head.appendChild(script);
    }).catch(error => {
      loads.delete(path);
      throw error;
    });
    loads.set(path, promise);
    return promise;
  }

  function stage(run) {
    if (run.root?.isConnected) return run.root;
    const root = document.createElement("aside");
    root.dataset.designLiveResource = "stage";
    root.className = "portalDetailSurface";
    root.setAttribute("aria-label", window.PortalI18n.t("资源设计点只读验证"));
    Object.assign(root.style, {
      position: "fixed", inset: "12% 8% auto", maxHeight: "70vh", overflow: "auto",
      zIndex: "1000", padding: "1rem", background: "var(--panel, white)",
    });
    const heading = document.createElement("h2");
    heading.textContent = window.PortalI18n.t("Resource Live · 只读组件验证");
    const note = document.createElement("p");
    note.textContent = window.PortalI18n.t("目录读取当前授权集合；详情、引用与 Preview 使用本地临时示例，不保存业务数据。");
    root.append(heading, note);
    const content = document.createElement("div");
    content.dataset.designLiveResourceContent = "1";
    root.appendChild(content);
    document.body.appendChild(root);
    run.root = root;
    run.content = content;
    return root;
  }

  function sampleRoot(run) {
    const root = document.createElement("section");
    run.content.appendChild(root);
    return root;
  }

  function observeQueries(run) {
    if (run.fetchWrapper) return;
    const original = global.fetch;
    const wrapper = function (input, options) {
      const url = new URL(typeof input === "string" || input instanceof URL ? input : input.url, global.location.href);
      const method = String(options?.method || input?.method || "GET").toUpperCase();
      const record = url.pathname === "/api/resource-index/query" && method === "GET"
        ? { url, done: false, ok: false, payload: null } : null;
      if (record) run.queries.push(record);
      const result = original.call(this, input, options);
      if (record) result.then(async response => {
        record.ok = response.ok;
        try { record.payload = await response.clone().json(); } catch { record.ok = false; }
        record.done = true;
      }, () => { record.done = true; });
      return result;
    };
    run.originalFetch = original;
    run.fetchWrapper = wrapper;
    global.fetch = wrapper;
  }

  function replaceCatalogLocation(run, url) {
    run.locationChanged = true;
    global.history.replaceState(global.history.state, "", url);
    global.dispatchEvent(new PopStateEvent("popstate", { state: global.history.state }));
  }

  async function queryResult(run, signal, start, expected) {
    const record = await until(() => run.queries.slice(start).find(item =>
      item.done && Object.entries(expected).every(([key, value]) => item.url.searchParams.get(key) === value)), run, signal);
    requireContract(record.ok && Array.isArray(record.payload?.resources), "目录查询没有返回成功的资源集合");
    await until(() => !/Loading|正在加载/.test(document.querySelector("#resourceCount")?.textContent || "Loading"), run, signal);
    requireContract(!document.querySelector('#resourceResults [data-state="error"]'), "目录仍处于错误状态");
    const results = document.querySelector("#resourceResults");
    const rendered = new Set(Array.from(results.querySelectorAll("[data-resource-open]"), node => node.dataset.resourceOpen));
    const expectedIds = new Set(record.payload.resources.map(item => item.resource_id));
    requireContract(rendered.size === expectedIds.size && Array.from(expectedIds).every(id => rendered.has(id)), "目录呈现的资源与当前查询响应不一致");
    requireContract(results.textContent.trim(), "成功的目录结果或空集合没有可见反馈");
    return record;
  }

  async function catalogFilters(run, signal) {
    observeQueries(run);
    const url = new URL(global.location.href);
    url.searchParams.delete("item");
    url.searchParams.delete("semantic");
    url.searchParams.set("q", "resource-live-probe");
    const project = document.querySelector('#resourceProjectOptions input[name="resourceProject"]:not([value=""])')?.value;
    requireContract(project, "请先加载至少一个带 Project 的资源；Live 不会创建示例项目");
    url.searchParams.set("project", project);
    const start = run.queries.length;
    replaceCatalogLocation(run, url);
    const category = document.body.dataset.resourceCategory;
    const expected = { resource_type: category, include_attributes: "1", q: "resource-live-probe", project };
    const first = await queryResult(run, signal, start, expected);
    requireContract(Number(first.url.searchParams.get("limit")) > 0, "目录未携带分页上限");
    requireContract(document.querySelector("#resourceQuery").value === expected.q, "搜索框未同步受管 URL");
    const beforeReload = run.queries.length;
    document.querySelector("#resourceReload").click();
    await queryResult(run, signal, beforeReload, expected);
    requireContract(new URL(global.location.href).searchParams.get("project") === expected.project, "Reload 丢失 Project 筛选");
    run.content.textContent = window.PortalI18n.t("目录已按资源类型、搜索词和 Project 读取；Reload 保留相同筛选与分页参数。");
  }

  async function catalogCategory(run, signal) {
    observeQueries(run);
    requireContract(document.body.dataset.resourceCategory === "skill", "类别验证应从 /resource/skill 启动");
    const category = "skill";
    for (const path of ["/resource/skill?category=article", "/resource?category=skill", "/resource"]) {
      check(run, signal);
      const start = run.queries.length;
      replaceCatalogLocation(run, path);
      document.querySelector("#resourceReload").click();
      const response = await queryResult(run, signal, start, { resource_type: category, include_attributes: "1" });
      requireContract(document.body.dataset.resourceCategory === category, "路径、查询参数与默认类别优先级不一致");
      requireContract(document.querySelector("#resourceQuery").placeholder.toLowerCase().includes(category), "搜索入口仍显示旧资源类型");
      requireContract(response.payload.resources.every(record => record.resource_type === category), "查询结果混入其他资源类型");
    }
    run.content.textContent = window.PortalI18n.t("Skill 路径优先于冲突的 Article 参数；移除路径或采用默认入口时，页面与查询仍一致使用 Skill。");
  }

  function referencesCanonical(run) {
    const root = sampleRoot(run);
    const valid = ["repo_0123456789ab", "article_abcdef012345/section/method"];
    const invalid = ["repo_0123456789a", "repo_0123456789abc", "Repo_0123456789ab", "repo_0123456789AB", "repo_0123456789ag"];
    root.textContent = window.PortalI18n.text`请阅读 ${valid.join(window.PortalI18n.t(" 与 "))}。无效地址：${invalid.join("，")}。`;
    global.PortalResourceChips.enhance(root);
    const addresses = Array.from(root.querySelectorAll("[data-resource-address]"), node => node.dataset.resourceAddress);
    requireContract(JSON.stringify(addresses) === JSON.stringify(valid), "canonical 小写地址识别或嵌套地址边界错误");
    requireContract(invalid.every(address => !global.PortalResourceChips.normalizeResourceAddress(address)), "畸形或大写 Resource ID 被接受");
    requireContract(valid.every(address => global.PortalResourceChips.normalizeResourceAddress(address) === address), "合法地址被改写或丢失");
    root.dataset.verified = "canonical-addresses";
  }

  function referencesLocators(run) {
    const root = sampleRoot(run);
    const canonical = document.createElement("p");
    canonical.textContent = window.PortalI18n.t("引用 repo_0123456789ab。");
    root.appendChild(canonical);
    const locators = [
      "/share/repo_0123456789ab/README.md",
      "https://example.invalid/repo_0123456789ab/README.md",
      "file:///share/repo_0123456789ab/README.md",
    ];
    for (const locator of locators) {
      const paragraph = document.createElement("p");
      paragraph.dataset.locatorExample = "1";
      paragraph.textContent = window.PortalI18n.text`文件定位符 ${locator}`;
      root.appendChild(paragraph);
    }
    global.PortalResourceChips.enhance(root);
    requireContract(canonical.querySelectorAll("[data-resource-address]").length === 1, "普通正文中的引用没有生成 Chip");
    const examples = Array.from(root.querySelectorAll("[data-locator-example]"));
    requireContract(examples.every((node, index) => !node.querySelector("[data-resource-address]") && node.textContent.includes(locators[index])), "文件路径或 URL 被误拆成 Resource 引用");
    root.dataset.verified = "locator-boundaries";
  }

  function detailHost(run, options = {}) {
    const modal = document.createElement("div");
    modal.className = "portalDetailOverlay";
    modal.dataset.designLiveResource = "detail";
    modal.hidden = true;
    const surface = document.createElement("section");
    surface.className = "portalDetailSurface";
    const body = document.createElement("div");
    body.className = "portalDetailBody";
    const close = document.createElement("button");
    close.type = "button";
    close.textContent = window.PortalI18n.t("关闭临时详情");
    surface.append(close, body);
    modal.appendChild(surface);
    document.body.appendChild(modal);
    let host;
    run.cleanups.push(() => { host?.destroy(); modal.remove(); });
    host = global.PortalResourceDetail.createLocalHost({
      modal,
      present: payload => { body.textContent = payload.content; },
      onLoading: () => { body.textContent = window.PortalI18n.t("正在读取临时详情"); },
      onError: error => { body.textContent = error.message; body.setAttribute("role", "alert"); },
      ...options,
    });
    close.addEventListener("click", () => host.close({ source: "toolbar" }));
    return { host, modal, body, close };
  }

  async function detailLatest(run, signal) {
    const background = sampleRoot(run);
    background.textContent = window.PortalI18n.t("背景集合保持不变");
    let release;
    const delayed = new Promise(resolve => { release = resolve; });
    run.cleanups.push(() => release({ content: "过期读取" }));
    const { host, body } = detailHost(run, {
      load: resource => resource.resourceId === "article_0123456789ab" ? delayed : { content: "当前资源 B" },
    });
    const earlier = host.open({ resourceId: "article_0123456789ab", resourceType: "article" });
    await host.open({ resourceId: "article_abcdef012345", resourceType: "article" });
    release({ content: "过期读取" });
    const stale = await earlier;
    check(run, signal);
    requireContract(stale.stale && body.textContent === "当前资源 B" && host.snapshot().resourceId === "article_abcdef012345", "较早请求覆盖了新选择");
    await host.open({ resourceId: "article_111111111111", resourceType: "article" }, {
      load: async () => { throw new Error("只读示例：资源读取失败"); },
    });
    check(run, signal);
    requireContract(host.snapshot().status === "error" && body.textContent.includes("资源读取失败"), "当前读取失败没有在详情显示错误");
    requireContract(background.isConnected && background.textContent === "背景集合保持不变", "失败详情破坏了背景集合");
    run.content.dataset.verified = "latest-detail-and-error-isolation";
    return body;
  }

  async function detailDirtyGuard(run, signal) {
    let dirty = false;
    let discards = 0;
    let locationWrites = 0;
    const href = global.location.href;
    const { host, modal, body, close } = detailHost(run, {
      dirty: () => dirty,
      confirmDiscard: () => { discards += 1; return false; },
      updateLocation: () => { locationWrites += 1; },
      load: () => ({ content: "未保存的本地草稿" }),
    });
    await host.open({ resourceId: "article_0123456789ab", resourceType: "article" });
    dirty = true;
    const opened = await host.open({ resourceId: "article_abcdef012345", resourceType: "article" });
    requireContract(opened.accepted === false, "取消 dirty guard 后仍切换了资源");
    for (const action of [
      () => close.click(),
      () => document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })),
      () => {
        const pointer = { bubbles: true, button: 0, pointerId: 1, isPrimary: true };
        modal.dispatchEvent(new PointerEvent("pointerdown", pointer));
        modal.dispatchEvent(new PointerEvent("pointerup", pointer));
      },
    ]) {
      const count = discards;
      action();
      await until(() => discards > count, run, signal);
      requireContract(host.snapshot().resourceId === "article_0123456789ab" && body.textContent === "未保存的本地草稿", "关闭入口绕过了 dirty guard");
    }
    requireContract(locationWrites === 1 && global.location.href === href, "拒绝操作仍写入了 URL");
    requireContract(discards === 4 && host.snapshot().status === "ready", "不同关闭入口没有共享同一保护逻辑");
    run.content.dataset.verified = "dirty-guard";
    return body;
  }

  async function previewRenderers(run, signal) {
    const root = sampleRoot(run);
    run.cleanups.push(() => global.PortalPreview.dispose(root));
    const session = global.PortalDocumentSession.create();
    let release;
    const pending = new Promise(resolve => { release = resolve; });
    run.cleanups.push(() => { release(null); session.destroy?.(); });
    const loading = session.open({ key: "resource-live-document", load: () => pending });
    requireContract(session.snapshot().status === "loading", "Document Session 没有暴露 loading 状态");
    root.innerHTML = global.PortalUIPrimitives.asyncStateHtml({ state: "loading", message: window.PortalI18n.t("正在加载只读 Preview") });
    release({ path: "resource-live/result.json", content: '{"verified":true,"count":2}', capabilities: { edit: false, save: false } });
    await loading;
    check(run, signal);
    const rendered = global.PortalPreview.render({ root, document: session.snapshot().payload });
    await rendered.promise;
    requireContract(rendered.renderer === "text" && root.textContent.includes('"verified"') && root.dataset.previewLanguage === "json", "JSON 文档未选择生产代码 renderer");
    const unsupported = global.PortalPreview.render({ root, document: {
      path: "resource-live/archive.unknown", kind: "binary", binary: true,
      downloadUrl: "/api/resources/content?address=article_0123456789ab",
    } });
    await unsupported.promise;
    check(run, signal);
    requireContract(unsupported.renderer === "unsupported" && root.textContent.includes("不支持"), "未支持格式留下空白 Preview");
    const download = root.querySelector("a[download]");
    requireContract(download && download.getAttribute("href") === "/api/resources/content?address=article_0123456789ab", "不支持格式没有保留下载入口");
    root.dataset.verified = "preview-renderer-fallback";
    return root;
  }

  async function filePreviewHost(run, signal) {
    const root = sampleRoot(run);
    const preview = sampleRoot(run);
    const path = "/resource-live/example.py#L2";
    const href = global.location.href;
    root.innerHTML = window.PortalI18n.markup('<button type="button" data-portal-file-path="/resource-live/example.py#L2">查看 Python 文件第 2 行</button>');
    let handled = 0;
    let received;
    let renderError;
    let rendering;
    const source = "design-resource-live-file";
    const unregister = global.PortalPreview.registerHost(source, request => {
      if (request.source !== source) return false;
      handled += 1;
      received = request;
      try {
        rendering = global.PortalPreview.render({ root: preview, document: {
          path: "/resource-live/example.py", content: 'name = "Portal"\nprint(name)\n',
        } });
      } catch (error) { renderError = error; }
      return true;
    }, { priority: Number.MAX_SAFE_INTEGER });
    run.cleanups.push(unregister, () => global.PortalPreview.dispose(preview));
    global.PortalFileLinks.bind(root, { source });
    root.querySelector("[data-portal-file-path]").click();
    await until(() => handled > 0, run, signal);
    if (renderError) throw renderError;
    await rendering.promise;
    check(run, signal);
    requireContract(handled === 1 && received.path === "/resource-live/example.py" && received.locator === "#L2" && received.path + received.locator === path, "文件链接没有向当前 Preview 提交唯一且完整的定位符");
    requireContract(preview.textContent.includes("print(name)") && preview.querySelector('[data-preview-line="2"]'), "当前 Preview 没有显示文件内容与行号");
    requireContract(global.location.href === href, "文件链接替换了当前 Portal 页面");
    preview.dataset.verified = "file-preview-host";
    return preview;
  }

  function afterFrame(run, signal) {
    check(run, signal);
    return new Promise((resolve, reject) => {
      const abort = () => {
        global.cancelAnimationFrame(frame);
        finish(new DOMException("Resource Live 已取消", "AbortError"));
      };
      const finish = error => {
        run.aborter.signal.removeEventListener("abort", abort);
        signal?.removeEventListener("abort", abort);
        if (error) reject(error);
        else resolve();
      };
      const frame = global.requestAnimationFrame(() => finish());
      run.aborter.signal.addEventListener("abort", abort, { once: true });
      signal?.addEventListener("abort", abort, { once: true });
    });
  }

  async function previewTypedRouting(run, signal) {
    const root = sampleRoot(run);
    const preview = sampleRoot(run);
    const source = "design-resource-typed-routing";
    const opened = [];
    run.cleanups.push(global.PortalPreview.registerHost(source, request => {
      if (request.source !== source) return false;
      opened.push(request);
      global.PortalPreview.render({ root: preview, document: {
        path: "readonly-routing.json",
        content: JSON.stringify({ locator: request.path, resource: request.resourceAddress || null }, null, 2),
        capabilities: { edit: false, save: false },
      } });
      return true;
    }, { priority: Number.MAX_SAFE_INTEGER }));
    run.cleanups.push(() => global.PortalPreview.dispose(preview));
    const address = "article_0123456789ab/section/method";
    const probes = [
      [{ resourceAddress: address }, address],
      [{ href: `/r/${encodeURIComponent(address)}` }, address],
      [{ href: "notes/article_0123456789ab.md" }, ""],
      [{ href: `https://example.invalid/r/${address}` }, ""],
      [{ href: address, title: address }, ""],
      [{ href: `https://untrusted@${global.location.host}/r/${address}` }, ""],
    ];
    for (const [probe, expected] of probes) {
      check(run, signal);
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = probe.href || probe.resourceAddress;
      let completion;
      button.addEventListener("click", () => {
        completion = global.PortalPreview.request({ ...probe, source });
      });
      root.appendChild(button);
      const previous = opened.length;
      button.click();
      requireContract(await completion, "Preview 未接管只读分类结果");
      requireContract(opened.length === previous + 1, "一次打开产生了重复 Preview 请求");
      requireContract(opened.at(-1).resourceAddress === expected, "文件、外站或名称被误提升为可信 Resource，或完整地址被截断");
      requireContract(preview.textContent.includes(expected || "null"), "Preview 没有呈现本次路由结果");
    }
    return preview;
  }

  async function frameReadiness(run, signal) {
    const kind = "design-resource-live-frame";
    const resource = { resourceId: "article_0123456789ab", resourceType: "article", detailKind: kind };
    const overlay = document.createElement("div");
    overlay.className = "portalDetailOverlay";
    overlay.dataset.designLiveResource = "frame";
    overlay.hidden = true;
    const transport = document.createElement("div");
    const frame = document.createElement("iframe");
    frame.title = window.PortalI18n.t("隔离的生产 Resource Guest 握手探针");
    const loading = document.createElement("div");
    loading.setAttribute("role", "status");
    transport.append(loading, frame);
    overlay.appendChild(transport);
    document.body.appendChild(overlay);
    let host;
    run.cleanups.push(() => { host?.destroy(); overlay.remove(); });
    run.cleanups.push(global.PortalResourceDetail.registerAdapter(kind, {
      location: () => "/static/design_tour_resource_guest.html",
    }));
    host = global.PortalResourceDetail.createFrameHost({ overlay, frame, frameHost: transport, loading, readyTimeout: 1200 });
    requireContract(host?.open && host?.snapshot, "生产 FrameHost 未提供可用控制器");
    const retry = document.createElement("button");
    retry.type = "button";
    retry.textContent = window.PortalI18n.t("重试隔离详情握手");
    retry.addEventListener("click", () => host.open(resource, { updateLocation: false }));
    const close = document.createElement("button");
    close.type = "button";
    close.textContent = window.PortalI18n.t("关闭未就绪详情");
    close.addEventListener("click", () => host.close({ updateLocation: false }));
    run.content.append(retry, close);
    retry.click();
    const firstRequest = host.snapshot().requestId;
    await until(() => host.snapshot().status === "error", run, signal);
    requireContract(loading.textContent.includes("超时") && !overlay.hidden, "未就绪详情没有可关闭的超时反馈");
    close.click();
    requireContract(host.snapshot().status === "empty" && overlay.hidden, "超时详情不能关闭");
    for (const message of ["无权限：隔离 Guest 拒绝读取", "不存在：隔离 Guest 没有该资源", null]) {
      check(run, signal);
      const loaded = new Promise((resolve, reject) => {
        const finish = error => {
          frame.removeEventListener("load", complete);
          run.aborter.signal.removeEventListener("abort", abort);
          signal?.removeEventListener("abort", abort);
          if (error) reject(error);
          else resolve();
        };
        const complete = () => finish();
        const abort = () => finish(new DOMException("Resource Live 已取消", "AbortError"));
        frame.addEventListener("load", complete, { once: true });
        run.aborter.signal.addEventListener("abort", abort, { once: true });
        signal?.addEventListener("abort", abort, { once: true });
      });
      retry.click();
      await loaded;
      await until(() => frame.contentWindow?.PortalResourceDetail, run, signal);
      const requestId = host.snapshot().requestId;
      requireContract(requestId !== firstRequest && host.snapshot().status === "loading", "重试未生成新请求或未恢复 loading");
      const guest = frame.contentWindow.PortalResourceDetail.createGuest({ embedded: true, resourceId: resource.resourceId, requestId });
      run.cleanups.push(() => guest.destroy());
      const stale = frame.contentWindow.PortalResourceDetail.createGuest({ embedded: true, resourceId: resource.resourceId, requestId: firstRequest });
      stale.ready();
      stale.destroy();
      await afterFrame(run, signal);
      requireContract(host.snapshot().status === "loading", "旧请求的就绪事件接管了重试详情");
      if (message) {
        guest.failed({ error: message });
        await until(() => host.snapshot().status === "error", run, signal);
        requireContract(loading.textContent === message && getComputedStyle(loading).display !== "none", "Guest 失败没有呈现原始原因");
        close.click();
        requireContract(overlay.hidden && host.snapshot().status === "empty", "失败详情关闭没有清理状态");
      } else {
        guest.ready();
        await until(() => host.snapshot().status === "ready", run, signal);
        requireContract(overlay.classList.contains("ready"), "有效 Guest 就绪后仍停在加载占位");
        await guest.requestClose();
        await until(() => host.snapshot().status === "empty", run, signal);
      }
    }
    run.content.appendChild(document.createTextNode("超时可关闭；重试拒绝旧 ready；权限/不存在原因可见；有效握手可打开并关闭。"));
    return run.content;
  }

  async function externalDetailHistory(run, signal) {
    const parameter = "resourceLiveSelection";
    const history = global.PortalResourceDetail.createDetailHistory({ owner: "design-resource-history", parameter });
    const origin = new URL(global.location.href);
    const length = global.history.length;
    run.locationChanged = true;
    global.history.replaceState({ ...global.history.state, designResourceExternal: { selection: "keep" } }, "", origin);
    const state = JSON.stringify(global.history.state);
    let closeMode;
    const detail = detailHost(run, {
      load: resource => ({ content: `只读外部深链：${resource.resourceId}` }),
      updateLocation: resourceId => {
        if (resourceId) history.replace({ [parameter]: resourceId });
        else closeMode = history.close();
      },
    });
    for (const address of ["article_0123456789ab", "article_abcdef012345/section/method"]) {
      await detail.host.open({ resourceId: address, resourceType: "article" });
      check(run, signal);
      requireContract(history.current() === address && !history.isManaged(), "外部深链被误当作宿主 push 的历史层级");
      requireContract(detail.body.textContent.includes(address), "详情内容与当前地址不一致");
      detail.close.click();
      requireContract(closeMode === "replace" && detail.host.snapshot().status === "empty", "关闭外部详情错误回退了浏览器历史");
      requireContract(global.location.href === origin.href && global.history.length === length, "关闭详情改变了集合入口或历史长度");
      requireContract(JSON.stringify(global.history.state) === state, "关闭详情破坏了外部历史状态");
      const unchanged = history.close();
      requireContract(unchanged === "replace" && global.location.href === origin.href, "重复关闭再次离开集合页");
    }
    run.content.textContent = window.PortalI18n.t("外部根资源和嵌套深链关闭后回到原集合，浏览器历史、筛选与外部状态均保持；重复关闭安全。");
    return run.content;
  }

  async function cancelPreviewUpdates(run, signal) {
    const surface = sampleRoot(run);
    surface.textContent = window.PortalI18n.t("等待合并 Preview 更新");
    const updates = [];
    const coalescer = global.PortalPreview.createFrameCoalescer(payload => {
      updates.push(payload);
      surface.style.transform = `scale(${payload.scale})`;
      surface.textContent = payload.label;
    });
    run.cleanups.push(() => coalescer.dispose());
    for (let index = 1; index <= 20; index += 1) coalescer.schedule({ scale: index / 10, label: window.PortalI18n.text`更新 ${index}` });
    await afterFrame(run, signal);
    requireContract(updates.length === 1 && surface.textContent === "更新 20" && surface.style.transform === "scale(2)", "同一帧没有仅提交最后一次位置");
    coalescer.schedule({ scale: 8, label: window.PortalI18n.t("应被取消") });
    coalescer.cancel();
    await afterFrame(run, signal);
    requireContract(updates.length === 1 && coalescer.pending() === null, "取消后旧更新仍然改写 Preview");
    coalescer.schedule({ scale: 1, label: window.PortalI18n.t("显式完成") });
    coalescer.flush();
    requireContract(updates.length === 2 && surface.textContent === "显式完成", "flush 未立即提交最后一次位置");
    coalescer.schedule({ scale: 9, label: window.PortalI18n.t("切换文档后禁止回写") });
    coalescer.dispose();
    requireContract(coalescer.schedule({ scale: 10, label: window.PortalI18n.t("已销毁") }) === false, "销毁后的旧手势仍被接受");
    await afterFrame(run, signal);
    requireContract(updates.length === 2 && surface.textContent === "显式完成", "销毁后发生延迟回写或重复 flush");
    let failure;
    const failing = global.PortalPreview.createFrameCoalescer(() => { throw new Error("隔离 Preview 更新失败"); }, error => { failure = error; });
    run.cleanups.push(() => failing.dispose());
    failing.schedule({ scale: 1 });
    requireContract(failing.flush() === false && failure?.message === "隔离 Preview 更新失败", "更新异常没有反馈给组件调用者");
    return surface;
  }

  async function restore(snapshot) {
    const run = snapshot?.run;
    if (!run || active !== run) return;
    run.aborter.abort();
    active = null;
    const errors = [];
    for (const dispose of run.cleanups.splice(0).reverse()) {
      try { await dispose(); } catch (error) { errors.push(error); }
    }
    run.root?.remove();
    if (run.fetchWrapper && global.fetch === run.fetchWrapper) global.fetch = run.originalFetch;
    const query = document.querySelector("#resourceQuery");
    const userQuery = run.queryEdited && query ? { value: query.value, start: query.selectionStart, end: query.selectionEnd } : null;
    if (run.locationChanged) {
      global.history.replaceState(snapshot.history, "", snapshot.url);
      global.dispatchEvent(new PopStateEvent("popstate", { state: snapshot.history }));
    }
    if (query) {
      query.value = userQuery?.value ?? snapshot.query;
      query.setSelectionRange(userQuery?.start ?? snapshot.selectionStart, userQuery?.end ?? snapshot.selectionEnd);
    }
    if (!userQuery && snapshot.focus?.isConnected) snapshot.focus.focus({ preventScroll: true });
    global.scrollTo({ left: snapshot.scrollX, top: snapshot.scrollY, behavior: "instant" });
    if (errors.length) throw new AggregateError(errors, "Resource Live 临时组件清理失败");
  }

  const handlers = {
    "resource.catalog.filters": catalogFilters,
    "resource.catalog.category": catalogCategory,
    "resource.references.canonical": referencesCanonical,
    "resource.references.locators": referencesLocators,
    "resource.detail.latest": detailLatest,
    "resource.detail.dirty-guard": detailDirtyGuard,
    "resource.preview.renderers": previewRenderers,
    "resource.files.preview-host": filePreviewHost,
    "resource.preview.typed-routing": previewTypedRouting,
    "resource.detail.frame-readiness": frameReadiness,
    "resource.detail.external-history": externalDetailHistory,
    "resource.preview.cancel-updates": cancelPreviewUpdates,
  };

  global.PortalDesignLiveAdapters["design-resource"] = Object.freeze({
    id: "design-resource",
    version: 1,
    ready: () => Boolean(document.querySelector("#resourceQuery") && global.PortalResourceDetail && global.PortalDocumentSession),
    async capture(context = {}) {
      requireContract(!active, "已有资源 Live 运行，不能覆盖其恢复记录");
      requireContract(!new URL(global.location.href).searchParams.get("item"), "请先关闭正在查看的资源详情，Live 不会替换用户文档");
      requireContract(!global.PortalOverlay?.top()?.element?.matches(".portalDetailOverlay, dialog"), "请先关闭已有对话框，Live 不会处理用户的未保存修改");
      for (const [name, path] of assets) await loadAsset(name, path);
      requireContract(!active, "已有资源 Live 运行，不能覆盖其恢复记录");
      const query = document.querySelector("#resourceQuery");
      requireContract(query, "当前页面不是 Resource Catalog");
      const run = { context, aborter: new AbortController(), cleanups: [], queries: [], root: null };
      const edited = () => { run.queryEdited = true; };
      query.addEventListener("input", edited);
      run.cleanups.push(() => query.removeEventListener("input", edited));
      const snapshot = {
        run, url: global.location.href, history: global.history.state,
        query: query.value, selectionStart: query.selectionStart, selectionEnd: query.selectionEnd,
        focus: document.activeElement, scrollX: global.scrollX, scrollY: global.scrollY,
      };
      run.snapshot = snapshot;
      active = run;
      return snapshot;
    },
    prepare(step, signal) {
      const run = active;
      requireContract(run && capabilities.has(step?.capability), `未登记的能力：${step?.capability}`);
      check(run, signal);
      return { target: stage(run), run, capability: step.capability };
    },
    async commit(step, prepared, signal) {
      const run = active;
      requireContract(run && prepared?.run === run && prepared.capability === step?.capability && capabilities.has(step?.capability), "步骤与当前恢复记录不匹配");
      try {
        check(run, signal);
        const target = await handlers[step.capability](run, signal) || run.root;
        check(run, signal);
        requireContract(target?.isConnected, "验收结果没有留在可见页面中");
        run.root.dataset.verifiedCapability = step.capability;
        return { target, caption: step.caption };
      } catch (error) {
        await restore(run.snapshot);
        throw error;
      }
    },
    restore,
  });
})(window);
