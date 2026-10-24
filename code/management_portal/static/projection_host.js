(() => {
  "use strict";
  const active = new Map();
  // The registry owns global bindings. Frames receive only the current key
  // declarations; execution is rechecked against the registry in this host.
  const globalKeybindings = () => (window.PortalCommandRegistry?.root.list() || [])
    .filter(command => command.enabled && command.keybindings.length)
    .map(command => ({ keybindings: command.keybindings, allowInInput: command.allowInInput }));
  document.addEventListener("portal:commands-change", () => {
    const bindings = globalKeybindings();
    for (const record of active.values()) post(record, "keybindings", { bindings });
  });
  // Sandboxed applications cannot show beforeunload dialogs. Protect their
  // outstanding state writes in the trusted page that owns persistence.
  window.addEventListener("beforeunload", event => {
    if ([...active.values()].some(record => record.stateWrites || record.stateSaveFailed)) {
      event.preventDefault(); event.returnValue = "";
    }
  });
  let sdkPromise, statePromise;
  const request = (...args) => window.PortalProjections.request(...args);
  function confirmOperation(payload, signal, presentation) {
    if (signal?.aborted) return Promise.resolve(false);
    return new Promise(resolve => {
      const dialog = document.createElement("dialog");
      let settled = false;
      const finish = accepted => {
        if (settled) return;
        settled = true;
        signal?.removeEventListener("abort", cancel);
        dialog.remove();
        resolve(accepted);
      };
      const cancel = () => finish(false);
      dialog.className = "projectionConfirm";
      if (presentation) {
        dialog.lang = window.PortalI18n.locale();
        dialog.setAttribute("translate", "no");
        dialog.setAttribute("aria-label", window.PortalI18n.t("把这段想法发给团队？"));
        dialog.innerHTML = window.PortalI18n.markup('<form method="dialog"><h2>把这段想法发给团队？</h2><p>确认后，这段文字会发送到右侧团队对话，回复也在那里查看。</p><dl class="projectionFeedbackSummary"><dt>讨论对象</dt><dd data-feedback-subject></dd><dt>对象状态</dt><dd data-feedback-status></dd><dt>收件团队</dt><dd data-feedback-recipient></dd></dl><h3>你要发送的文字</h3><div class="projectionFeedbackText" data-feedback-text></div><details class="projectionFeedbackDetails"><summary>查看对象与技术情境</summary></details><div class="projectionActions"><button class="portalButton" value="cancel" autofocus>取消</button><button value="confirm" class="portalButton is-primary">确认发送</button></div></form>');
        dialog.querySelector('[data-feedback-subject]').textContent = presentation.subject;
        dialog.querySelector('[data-feedback-status]').textContent = window.PortalI18n.t(statuses[payload.object.status]);
        dialog.querySelector('[data-feedback-recipient]').textContent = presentation.recipientTitle;
        dialog.querySelector('[data-feedback-text]').textContent = payload.arguments.text;
        const details = dialog.querySelector('details');
        const readable = value => {
          if (typeof value !== "string") return JSON.stringify(value, null, 2);
          try { return JSON.stringify(JSON.parse(value), null, 2); } catch { return value; }
        };
        for (const [label, value] of [["对象（五字段）", displayObject(payload.object)], ["收件身份", presentation.target], ["位置（anchor）", payload.arguments.anchor], ["场景（scene）", payload.arguments.scene]]) {
          const heading = document.createElement("h4"), content = document.createElement("pre");
          heading.textContent = window.PortalI18n.t(label); content.textContent = readable(value);
          details.append(heading, content);
        }
      } else {
        dialog.setAttribute("aria-label", window.PortalI18n.t("确认工作台操作"));
        dialog.innerHTML = window.PortalI18n.markup('<form method="dialog"><h2>确认工作台操作</h2><p>此操作将交给绑定的 Lineage 执行。</p><pre></pre><div class="projectionActions"><button class="portalButton" value="cancel">取消</button><button value="confirm" class="portalButton is-primary">确认执行</button></div></form>');
        dialog.querySelector("pre").textContent = JSON.stringify({ instruction: payload.instruction, binding: payload.binding || "main", role: payload.role || "", object: payload.object ? displayObject(payload.object) : undefined, arguments: payload.arguments || {} }, null, 2);
      }
      dialog.addEventListener("close", () => finish(dialog.returnValue === "confirm" && !signal?.aborted), { once: true });
      signal?.addEventListener("abort", cancel, { once: true });
      document.body.append(dialog);
      dialog.showModal();
    });
  }
  let stylesPromise;
  const statuses = { current: "当前实现", proposed: "拟议方案", simulated: "模拟演示", verified: "已验证结果" };
  const displayObject = object => ({ ...object, status: statuses[object.status], source: object.source || "缺少依据" });
  const fail = (code, message) => Object.assign(new Error(message), { code: `projection.${code}` });
  async function feedbackPresentation(record, object, payload) {
    const target = await request("facade", { id: record.app.workbench_id, preview: record.preview, panel: record.panelId });
    const resolve = address => request("resource", { id: record.app.workbench_id, operation: "resolve", arguments: { address } });
    const [recipient, subject] = await Promise.all([
      resolve(target.resident_id), payload.subject ? null : resolve(object.ref),
    ]);
    return {
      target,
      recipientTitle: recipient?.readable && recipient.title ? recipient.title : window.PortalI18n.t("当前工作台绑定的门面团队"),
      subject: payload.subject || (subject?.readable && subject.title) || object.ref,
    };
  }
  function feedbackContextField(value, field, limit) {
    if (value === undefined || value === null) return "";
    const invalid = () => fail("feedback_context", `${field} 必须是字符串或有效 JSON 对象/数组`);
    let encoded;
    if (typeof value === "string") encoded = value;
    else {
      if (typeof value !== "object") throw invalid();
      const ancestors = new Set();
      const validate = item => {
        if (item === null || typeof item === "string" || typeof item === "boolean") return;
        if (typeof item === "number" && Number.isFinite(item)) return;
        if (typeof item !== "object" || ancestors.has(item)) throw invalid();
        const array = Array.isArray(item), prototype = Object.getPrototypeOf(item);
        if (!array && prototype !== Object.prototype && prototype !== null) throw invalid();
        const keys = Object.keys(item);
        if (Reflect.ownKeys(item).length !== keys.length + (array ? 1 : 0)
            || (array && (keys.length !== item.length || keys.some((key, index) => key !== String(index))))) throw invalid();
        ancestors.add(item);
        for (const key of keys) {
          const descriptor = Object.getOwnPropertyDescriptor(item, key);
          if (!Object.hasOwn(descriptor, "value")) throw invalid();
          validate(descriptor.value);
        }
        ancestors.delete(item);
      };
      try { validate(value); encoded = JSON.stringify(value); }
      catch (_error) { throw invalid(); }
    }
    if (encoded.length > limit) throw fail("feedback_context", `${field} 编码后超过 ${limit} 个 UTF-16 码元，请缩减内容后重新确认`);
    return encoded;
  }
  function cancelFeedback(record, message = "当前功能区已取消反馈，请重新检查后发送") {
    const pending = record.pendingFeedback;
    if (!pending || pending.controller.signal.aborted) return false;
    pending.cancellationMessage = message;
    pending.controller.abort();
    return true;
  }
  function checkFeedback(record, pending) {
    if (record.pendingFeedback !== pending || pending.controller.signal.aborted || record.destroyed
        || !record.visible || record.page.snapshot().revision !== pending.revision) {
      throw fail("cancelled", pending.cancellationMessage || "反馈依据或当前对象已变化，请重新检查后发送");
    }
  }
  const post = (record, type, detail) => {
    if (type === "change") cancelFeedback(record, "对象观察结果已更新，请重新检查后发送");
    if (!record.destroyed) record.frame.contentWindow?.postMessage({ type: `portal-projection-${type}`, channel: record.channel, ...detail }, "*");
  };
  const OBSERVE_BATCH_SIZE = 128;
  async function observeReferences(record, refs, isCurrent) {
    if (!Array.isArray(refs)) throw fail("references", "请提供有效对象地址列表");
    const unique = [...new Set(refs)];
    const references = [];
    let observed_at;
    // One logical subscription, with the same authenticated endpoint for every
    // batch. Never install or notify a partially authorized set.
    for (let offset = 0; offset < Math.max(1, unique.length); offset += OBSERVE_BATCH_SIZE) {
      if (!isCurrent()) throw fail("cancelled", "观察请求已失效");
      const batch = unique.slice(offset, offset + OBSERVE_BATCH_SIZE);
      const result = await request("observe", { id: record.app.workbench_id, preview: record.preview, refs: batch });
      if (!isCurrent()) throw fail("cancelled", "观察请求已失效");
      if (!Array.isArray(result.references) || result.references.length !== batch.length
        || result.references.some((item, index) => item.ref !== batch[index])) throw fail("references", "观察响应未包含完整引用");
      references.push(...result.references);
      observed_at = result.observed_at;
    }
    return { references, observed_at };
  }
  function invalidateObservation(record) {
    record.observation = null;
    clearTimeout(record.refreshTimer);
  }
  async function observe(record) {
    if (!record.visible || record.destroyed || !record.refs?.length || record.pendingWatch) return;
    if (record.observation) { record.observation.again = true; return; }
    const observation = record.observation = { again: false };
    const isCurrent = () => !record.destroyed && record.visible && record.observation === observation;
    try {
      const result = await observeReferences(record, record.refs, isCurrent);
      if (isCurrent()) post(record, "change", { refs: result.references.map(item => item.ref), observed_at: result.observed_at });
    } catch (error) {
      if (isCurrent()) post(record, "change", { refs: [], error: error.message, code: error.code || "projection.request_failed" });
    } finally {
      if (record.observation === observation) {
        record.observation = null;
        if (observation.again) void observe(record);
      }
    }
  }
  function setObservationVisibility(record, visible) {
    record.visible = visible;
    if (!visible) { cancelFeedback(record, "反馈功能区已隐藏，请返回并重新检查后发送"); invalidateObservation(record); }
    post(record, "visibility", { visible });
    if (visible) void observe(record);
  }
  function refreshObservers(event) {
    // Portal SSE exposes an authorized projection of notifications, not the
    // internal bus subject. Unrelated execution/chat traffic is not a change
    // to every watched resource.
    const detail = event?.detail || {};
    const ids = value => Array.isArray(value) ? value.filter(id => typeof id === "string") : [];
    const resources = [...ids(detail.changed?.resource), ...Object.values(detail.resource_projection_changes || {}).flatMap(ids)];
    const targets = new Set([...resources, ...["lineage", "resident", "projection"].flatMap(domain => ids(detail.changed?.[domain]))]);
    const snapshot = detail.snapshot_required === true
      || (event?.type === "portal:events-ready" && detail.snapshot_required !== false)
      || ids(detail.resource_projection_snapshots).length > 0
      || (ids(detail.domains).includes("resource") && resources.length === 0);
    for (const record of active.values()) {
      if (!record.visible || !record.refs?.length) continue;
      if (!snapshot && !record.references?.some(item => targets.has(item.root_id))) continue;
      cancelFeedback(record, snapshot ? "资源观察需要重新核对，请等待更新后再发送" : "所观察资源收到更新通知，请重新检查后发送");
      clearTimeout(record.refreshTimer);
      record.refreshTimer = setTimeout(() => observe(record), 150);
    }
  }
  addEventListener("portal:change", refreshObservers);
  addEventListener("portal:events-ready", refreshObservers);
  function themeCss() {
    const style = getComputedStyle(document.documentElement);
    return `:root{${Array.from(style).filter(name => name.startsWith("--")).map(name => `${name}:${style.getPropertyValue(name)};`).join("")}}`;
  }
  function broadcastTheme() {
    const css = themeCss();
    for (const [channel, record] of active) record.frame.contentWindow?.postMessage({ type: "portal-projection-theme", channel, css }, "*");
  }
  new MutationObserver(broadcastTheme).observe(document.documentElement, { attributes: true });
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", broadcastTheme);
  function applicationAssets(app, entry, preview) {
    const build = app.application_build;
    const revision = preview ? app.revision : app.published_revision;
    if (build?.status !== "ready" || !/^[0-9a-f]{64}$/.test(build.artifact_hash) || !Number.isInteger(revision) || revision < 1 || !build.entries?.[entry]) throw fail("application_build", "此版本的工作台页面尚未准备好");
    const release = app.release_scope === "packaging" && !preview;
    const base = new URL(`/api/workbenches/${release ? "release-assets" : "assets"}/${encodeURIComponent(app.workbench_id)}/${revision}/${build.artifact_hash}/`, location.origin);
    const entryPath = build.entries[entry];
    function path(value, relativeTo = entryPath) {
      if (typeof value !== "string" || !value || /[\\\x00?#%]/.test(value) || value.split("/").includes("..")) throw fail("asset", "应用包只能引用本修订的产物");
      const url = new URL(value, new URL(relativeTo, base));
      if (url.origin !== base.origin || !url.href.startsWith(base.href)) throw fail("asset", "应用包只能引用本修订的产物");
      const name = url.pathname.slice(base.pathname.length);
      if (!Object.hasOwn(build.files, name)) throw fail("asset", "此修订没有指定产物");
      return name;
    }
    async function read(value, relativeTo = entryPath) {
      const name = path(value, relativeTo);
      const response = await fetch(new URL(name, base), { credentials: "same-origin", redirect: "error", cache: "no-store" });
      if (!response.ok) throw fail("asset", `工作台页面文件加载失败（${response.status}）`);
      return { content: await response.text(), contentType: build.files[name].content_type, path: name };
    }
    const libraryBase = `/api/workbenches/${release ? "release-libraries" : "libraries"}/${encodeURIComponent(app.workbench_id)}/${revision}`;
    async function libraries() {
      const response = await fetch(libraryBase, { credentials: "same-origin", redirect: "error", cache: "no-store" });
      if (!response.ok) throw fail("library", `登记库清单读取失败（${response.status}）`);
      return response.json();
    }
    async function library(name) {
      const catalog = await libraries();
      if (typeof name !== "string" || !Object.hasOwn(catalog, name)) throw fail("library", "登记库不存在");
      const metadata = catalog[name];
      const entry = metadata.entry;
      const file = metadata.files.find(item => item.path === entry);
      if (typeof entry !== "string" || /[\\\x00?#%]/.test(entry) || entry.split("/").some(part => !part || part === "." || part === "..") || !file || !/^(?:text|application)\/javascript(?:;|$)/.test(file.content_type)) throw fail("library", "登记库入口无效");
      const response = await fetch(`${libraryBase}/${entry.split("/").map(encodeURIComponent).join("/")}`, { credentials: "same-origin", redirect: "error", cache: "no-store" });
      if (!response.ok) throw fail("library", `登记库读取失败（${response.status}）`);
      return { library: { ...metadata, name }, content: await response.text() };
    }
    return { read, entryPath, libraries, library };
  }
  async function applicationDocument(assets) {
    const source = await assets.read(assets.entryPath, "");
    const documentSource = new DOMParser().parseFromString(source.content, "text/html");
    if (documentSource.querySelector("base, iframe, frame, object, embed")) throw fail("application_html", "应用包入口不能包含嵌套文档或 base");
    // Opaque frames cannot send SameSite session cookies even on credentialed
    // module requests. The trusted host reads immutable bundles; only the
    // sandbox executes them. URL scripts remain prohibited, including nonce-
    // bearing scripts. Bundles must include their own imports and Worker code.
    const scripts = [], reads = [];
    for (const script of documentSource.querySelectorAll("script")) {
      const type = script.getAttribute("type") || "";
      if (type && !["module", "text/javascript", "application/javascript"].includes(type)) throw fail("application_html", "应用包脚本类型不受支持");
      const item = { type, content: script.textContent };
      scripts.push(item);
      if (script.hasAttribute("src")) reads.push(async () => { item.content = (await assets.read(script.getAttribute("src"))).content; });
      script.remove();
    }
    for (const link of documentSource.querySelectorAll("link")) {
      if (link.rel !== "stylesheet") { link.remove(); continue; }
      reads.push(async () => {
        const asset = await assets.read(link.getAttribute("href"));
        const style = documentSource.createElement("style"); style.textContent = asset.content; link.replaceWith(style);
      });
    }
    // Only downloads overlap. Keep script execution order and stylesheet DOM
    // positions, and continue using revision-bound, authenticated asset reads.
    let cursor = 0, failed = false;
    await Promise.all(Array.from({ length: Math.min(4, reads.length) }, async () => {
      while (!failed && cursor < reads.length) {
        const read = reads[cursor++];
        try { await read(); } catch (error) { failed = true; throw error; }
      }
    }));
    // JSON escapes '<' so application source cannot end the trusted launch
    // script while the outer srcdoc is parsed. textContent preserves JS bytes.
    const launch = documentSource.createElement("script");
    launch.textContent = `document.addEventListener('DOMContentLoaded', async () => { for (const item of ${JSON.stringify(scripts).replaceAll("<", "\\u003c")}) { const script = document.createElement('script'); script.type = item.type; script.textContent = item.content; if (item.type === 'module') { const loaded = new Promise((resolve, reject) => { script.onload = resolve; script.onerror = reject; }); document.body.append(script); await loaded; } else document.body.append(script); } }, {once:true});`;
    documentSource.body.append(launch);
    return documentSource;
  }
  function stateBootstrap(documentSource, state) {
    const configuration = documentSource.createElement("script");
    // Generated documents never inherit historical identifiers or formats.
    configuration.textContent = 'window.__PORTAL_SITE__ = Object.freeze({compatibility: Object.freeze({browser_identifiers: Object.freeze({}), format_namespaces: Object.freeze([])})});';
    const sharedState = documentSource.createElement("script");
    sharedState.textContent = state;
    return [configuration, sharedState];
  }
  async function mountContent(root, app, options = {}) {
    const assets = options.packageEntry ? applicationAssets(app, options.packageEntry, !!options.preview) : null;
    if (!sdkPromise) sdkPromise = fetch("/static/projection_sdk.js").then(response => { if (!response.ok) throw new Error("工作台 SDK 加载失败"); return response.text(); });
    if (!statePromise) statePromise = fetch("/static/portal_state.js").then(response => { if (!response.ok) throw new Error("Portal 状态组件加载失败"); return response.text(); });
    if (!stylesPromise) stylesPromise = Promise.all(["portal_components.css", "portal_design_system.css", "portal_workbench.css"].map(async name => {
      const response = await fetch(`/static/${name}`);
      if (!response.ok) throw new Error("Portal 基础模板加载失败");
      return response.text();
    })).then(styles => styles.join("\n"));
    const [sdk, state, sharedCss, documentSource] = await Promise.all([sdkPromise, statePromise, stylesPromise,
      assets ? applicationDocument(assets) : new DOMParser().parseFromString(app.html, "text/html")]);
    // A destroyed panel must never inject its late document or register a channel.
    if (options.isCurrent && !options.isCurrent()) throw fail("cancelled", "功能区已关闭");
    const channel = globalThis.PortalUIPrimitives.randomUUID();
    const frame = document.createElement("iframe");
    frame.title = app.title;
    frame.className = "projectionFrame";
    frame.allowFullscreen = true;
    frame.setAttribute("sandbox", "allow-scripts allow-forms");
    frame.referrerPolicy = "no-referrer";
    const shared = documentSource.createElement("style");
    shared.textContent = `${sharedCss}\n*{box-sizing:border-box}html,body{margin:0;min-height:100%;background:var(--bg);color:var(--text);font:14px/1.5 system-ui,sans-serif}body{padding:12px}body [hidden]{display:none!important}h1,h2,h3{font-size:16px;margin:0 0 8px}.paneHead{padding:12px;border-bottom:1px solid var(--border);background:var(--panel-2);display:flex;align-items:center;justify-content:space-between;gap:8px}.paneBody{padding:12px;min-width:0}a{color:var(--accent)}`;
    const theme = documentSource.createElement("style");
    theme.id = "portalProjectionTheme"; theme.textContent = themeCss();
    const policy = documentSource.createElement("meta");
    policy.httpEquiv = "Content-Security-Policy";
    policy.content = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; connect-src 'none'; form-action 'none'; base-uri 'none'; frame-src 'none'; object-src 'none'; worker-src " + (assets ? "blob:; script-src-attr 'none'" : "'none'");
    const bootstrap = documentSource.createElement("script");
    bootstrap.dataset.channel = channel;
    bootstrap.dataset.keybindings = JSON.stringify(globalKeybindings());
    bootstrap.dataset.hostDepth = assets ? "2" : "1";
    bootstrap.dataset.icons = JSON.stringify({ icons: window.PortalUIPrimitives.ICONS, aliases: window.PortalUIPrimitives.ICON_ALIASES });
    bootstrap.textContent = sdk;
    documentSource.head.prepend(policy, shared, theme, ...stateBootstrap(documentSource, state), bootstrap);
    const record = { app, frame, channel, assets, panelId: options.panelId, page: options.page, preview: !!options.preview, panelVisible: options.visible !== false, visible: options.visible !== false && options.page?.visible !== false, busy: false, destroyed: false, refs: [] };
    active.set(channel, record);
    let surface = frame;
    if (assets) {
      // connect-src does not block a document navigating itself. A trusted,
      // empty parent with frame-src 'none' blocks those requests, while srcdoc
      // remains permitted. Application HTML/code is never placed in this
      // same-origin parent; the inner frame retains its opaque sandbox origin.
      surface = document.createElement("iframe");
      surface.title = app.title; surface.className = "projectionFrame";
      surface.allowFullscreen = true;
      surface.referrerPolicy = "no-referrer";
      root.replaceChildren(surface);
      const container = surface.contentDocument;
      const navigationPolicy = container.createElement("meta");
      navigationPolicy.httpEquiv = "Content-Security-Policy";
      navigationPolicy.content = "frame-src 'none'";
      container.head.append(navigationPolicy);
      container.documentElement.style.height = "100%";
      container.body.style.cssText = "margin:0;height:100%;overflow:hidden";
      frame.style.cssText = "display:block;width:100%;height:100%;border:0";
      container.body.append(frame);
    } else root.replaceChildren(frame);
    frame.srcdoc = "<!doctype html>" + documentSource.documentElement.outerHTML;
    frame.addEventListener("load", () => post(record, "visibility", { visible: record.visible }));
    return {
      setVisible(visible) { record.panelVisible = visible; setObservationVisibility(record, visible && record.page?.visible !== false); },
      destroy() { cancelFeedback(record, "反馈功能区已关闭，请重新打开后发送"); record.destroyed = true; invalidateObservation(record); active.delete(channel); surface.remove(); },
    };
  }
  function createContentPanel({ root, app, projectionId, preview, panelId, page, source, regionTitle }) {
    root.classList.add("pane", "projectionContentPane");
    const heading = document.createElement("div"); heading.className = "paneHead portalWorkspaceHead";
    const title = document.createElement("strong"); title.className = "paneTitle"; title.textContent = app?.title || window.PortalI18n.t("工作台"); heading.append(title);
    const body = document.createElement("div"); body.className = "projectionContentBody";
    root.replaceChildren(heading, body);
    let content, disposed = false, visible = false;
    const ready = (async () => {
      const current = app || await request(`detail?id=${encodeURIComponent(projectionId)}&published=true`);
      if (disposed) return;
      title.textContent = regionTitle || current.title;
      const packageEntry = source?.startsWith("package:") ? source.slice(8).trim() : null;
      content = !source && current.manifest ? await mount(body, current, { preview, embedded: true }) : await mountContent(body, source ? { ...current, title: regionTitle, html: packageEntry ? "" : current.panels[source] } : current, { preview, panelId, page, visible, packageEntry, isCurrent: () => !disposed });
      if (disposed) content.destroy();
      else {
        // Application entries own their content heading and controls. The
        // workbench tab remains the region label; snippets retain this heading.
        if (packageEntry) heading.remove();
        content.setVisible?.(visible);
      }
    })().catch(error => {
      if (!disposed) { const alert = document.createElement("p"); alert.setAttribute("role", "alert"); alert.textContent = error.message; body.replaceChildren(alert); }
      throw error;
    });
    ready.catch(() => {});
    return { root, ready, isVisible: () => visible, show() { visible = true; content?.setVisible?.(true); }, hide() { visible = false; content?.setVisible?.(false); }, destroy() { disposed = true; content?.destroy(); } };
  }
  window.PortalWorkbench.registerPanelPackage({
    type: "projection-content", version: 1, component: "PortalProjectionContent",
    presentation: { title: window.PortalI18n.t("工作台"), icon: "window" }, sizing: { minWidth: 220, minHeight: 180 },
    instances: "single", capabilities: [], behavior: { canHide: false, canCollapse: false, canMove: false, closable: false },
    context: { channels: [] }, persistence: { schemaVersion: 1 },
    create: createContentPanel,
  });
  window.PortalWorkbench.registerPanelPackage({
    type: "projection-region", version: 2, component: "PortalProjectionRegion",
    presentation: { title: window.PortalI18n.t("功能区"), icon: "window" }, sizing: { minWidth: 220, minHeight: 180 },
    instances: "multiple", capabilities: [], behavior: { canHide: true, canCollapse: true, canMove: typeof Element.prototype.moveBefore === "function", closable: false },
    context: { channels: [] }, persistence: { schemaVersion: 1 }, create: createContentPanel,
  });
  function pageController(app, options) {
    const objects = new Map();
    const readObject = value => request("object", { id: app.workbench_id, preview: !!options.preview, object: value });
    const rememberObject = object => {
      if (objects.has(object.ref) && objects.get(object.ref).status !== object.status) throw fail("status_conflict", "同一对象在各功能区的状态必须一致");
      objects.set(object.ref, object);
      return object;
    };
    let focusRequest = 0;
    const chatId = panelId => app.manifest.sidebar.find(item => item.id === panelId)?.chat ? `chat-${panelId}` : "facade";
    let workbench, openRegion, feedbackContext = null, feedbackGuard = null;
    const channel = app.manifest.context?.find(item => item.channel === "focus");
    const api = {
      visible: options.isVisible?.() !== false,
      setVisible(visible) {
        api.visible = visible;
        for (const record of active.values()) if (record.page === api) {
          setObservationVisibility(record, visible && record.panelVisible === true);
        }
      },
      attach(value, navigate) { workbench = value; openRegion = navigate; },
      openChat(panelId) {
        const id = chatId(panelId);
        if (app.release_scope === "packaging" && panelId === "packaging" && !workbench.getPanel(id)) {
          // A release-only entry does not preload ordinary conversation data.
          // Explicit navigation still uses the existing facade authorization.
          return request("facade", { id: app.workbench_id, panel: panelId }).then(target => {
            if (!/^resident_[a-zA-Z0-9]+$/.test(target.resident_id || "")) throw fail("resident_unbound", "发布团队绑定不可用");
            return window.PortalNav.openPage(`/lineage?resident=${encodeURIComponent(target.resident_id)}`);
          });
        }
        if (!workbench.getPanel(id)) throw fail("resident_unbound", "此功能区没有可用的团队对话");
        const layout = workbench.getLayout();
        const stackFor = (node, target) => node?.type === "stack"
          ? (node.children.some(child => child.panelId === target) ? node : null)
          : node?.children?.map(child => stackFor(child, target)).find(Boolean);
        const content = stackFor(layout, panelId), chat = stackFor(layout, id);
        // Restored layouts put newly introduced panels in their first stack.
        // Place a region conversation alongside its content before focusing it.
        if (id !== "facade" && (!chat || chat === content)) {
          const facade = stackFor(layout, "facade");
          const target = facade && facade !== content ? facade : content;
          if (target) return workbench.showPanel(id, { stackId: target.id, zone: target === content ? "right" : "center" });
        }
        return workbench.focusPanel(id);
      },
      canRead: record => app.manifest.sidebar.some(item => item.id === record.panelId),
      openPanel(panelId) {
        if (typeof panelId !== "string" || !app.manifest.sidebar.some(item => item.id === panelId)) throw fail("panel_unknown", "只能打开清单中的功能区");
        return openRegion(panelId);
      },
      openPage(target) {
        const builtinPage = app.builtin && app.url && target.pathname === new URL(app.url, location.origin).pathname;
        if (!builtinPage && (target.pathname !== "/workbenches/view" || target.searchParams.get("id") !== app.workbench_id)) return null;
        if (target.hash || [...target.searchParams.keys()].some(key => !["id", "region"].includes(key))
            || (builtinPage ? target.searchParams.has("id") : target.searchParams.getAll("id").length !== 1) || target.searchParams.getAll("region").length > 1) {
          throw fail("page_target", "当前工作台入口只支持 id 和 region");
        }
        const panelId = target.searchParams.get("region") ?? "facade";
        if (panelId !== "facade" && !app.manifest.sidebar.some(item => item.id === panelId)) throw fail("page_target", "体验入口指向未知功能区");
        if (!workbench.getPanel(panelId)) throw fail("page_target", "体验入口对应的面板未装配");
        return openRegion(panelId);
      },
      current: () => workbench?.contextStore.get("focus"),
      lookup: ref => objects.get(ref),
      snapshot: () => ({ object: api.current() || null, revision: workbench?.contextStore.revision || 0 }),
      async object(value) {
        return rememberObject(await readObject(value));
      },
      async focus(record, object) {
        if (!channel?.publishers.includes(record.panelId)) throw fail("context_forbidden", "此功能区没有发布 focus 的权限");
        const sequence = ++focusRequest;
        for (const target of active.values()) if (target.page === api) cancelFeedback(target, "讨论对象已重新选择，请核对当前对象后发送");
        const checked = await readObject(object);
        // Request order belongs to the whole page, before asynchronous authorization.
        // A late result must not change either the registry or the shared revision.
        if (record.destroyed || sequence !== focusRequest) throw fail("cancelled", "焦点请求已失效");
        rememberObject(checked);
        workbench.contextStore.set("focus", checked, { source: record.panelId });
        const snapshot = api.snapshot();
        for (const target of active.values()) if (target.page === api && channel.subscribers.includes(target.panelId)) post(target, "focus", snapshot);
        return snapshot;
      },
      async context(submittedTarget, panelId = "") {
        feedbackGuard?.();
        const target = await request("facade", { id: app.workbench_id, preview: !!options.preview, panel: panelId });
        feedbackGuard?.();
        if (submittedTarget?.resident_id !== target.resident_id || submittedTarget?.lineage_id !== target.lineage_id) throw fail("resident_unbound", "门面绑定已变化，请重新打开工作台");
        if (panelId) {
          const state = await request(`state?id=${encodeURIComponent(app.workbench_id)}&panel=${encodeURIComponent(panelId)}`);
          return { context_text: JSON.stringify({ workbench_id: app.workbench_id, panel: panelId, saved_state: state }) };
        }
        const object = feedbackContext?.object || api.current();
        if (!object) return null;
        await api.object(object);
        feedbackGuard?.();
        return { context_text: JSON.stringify({ workbench_id: app.workbench_id, ...feedbackContext, object: displayObject(object) }) };
      },
      async feedback(record, payload, guard) {
        guard();
        const object = objects.get(payload.ref);
        if (!object) throw fail("object_unknown", "请先聚焦反馈对象");
        if (object.status === "simulated") throw fail("simulated", "模拟演示不能触发真实操作");
        if (typeof payload.text !== "string" || !payload.text.trim() || payload.text.length > 20000) throw fail("feedback", "请填写反馈内容（最多 20000 字）");
        if (feedbackContext) throw fail("feedback_busy", "正在发送上一条反馈");
        await api.object(object);
        guard();
        await request("facade", { id: app.workbench_id, preview: !!options.preview, panel: record.panelId });
        guard();
        const chat = workbench.getPanel(chatId(record.panelId));
        if (!chat) throw fail("resident_unbound", "工作台没有门面对话");
        feedbackContext = { object, anchor: payload.anchor, scene: payload.scene };
        feedbackGuard = guard;
        try {
          const result = await chat.send({ text: payload.text, refs: [], attachments: [] }, { clear: false, focus: false });
          api.openChat(record.panelId);
          return result;
        } finally { feedbackContext = null; feedbackGuard = null; }
      },
    };
    return api;
  }
  async function readFacade(id, preview = false) {
    try { return { target: await request("facade", { id, preview }) }; }
    catch (error) { return { error }; }
  }
  async function open(root, id, options = {}) {
    const { signal } = options;
    let disposed = signal?.aborted === true, openingConversation;
    const dispose = () => { disposed = true; openingConversation?.destroy(); openingConversation = null; };
    const checkActive = () => { if (disposed) throw fail("cancelled", "工作台已关闭"); };
    signal?.addEventListener("abort", dispose, { once: true });
    try {
      checkActive();
      // Binding authorization and page definition are independent reads. Start
      // Chat as soon as the server has authorized its target, not after detail
      // or application assets. The native stores own caching and coalescing.
      const definition = request(`detail?id=${encodeURIComponent(id)}&published=true${options.region === "packaging" ? "&panel=packaging" : ""}`);
      const facade = (options.region ? Promise.resolve(null) : readFacade(id)).then(result => {
        if (!disposed && result?.target) {
          openingConversation = window.PortalResidentProtocol.acquire(result.target.resident_id, () => {}, { load: false });
          void window.PortalChatWorkspace.loadResidentConversation(result.target).catch(() => {});
        }
        return result;
      }).catch(error => ({ error }));
      const app = await definition;
      checkActive();
      if (app.builtin && !app.manifest && !app.html) return { app, destroy() {} };
      const resolved = await facade;
      checkActive();
      const mounted = await mountWorkbench(root, app, options, resolved);
      if (disposed) { mounted.destroy(); checkActive(); }
      return { ...mounted, app };
    } finally {
      // Keep the native snapshot alive until the Chat panel owns a subscription.
      dispose();
      signal?.removeEventListener("abort", dispose);
    }
  }
  async function mount(root, app, options = {}) {
    const facade = app.manifest?.chat && !options.region ? await readFacade(app.workbench_id, !!options.preview) : null;
    return mountWorkbench(root, app, options, facade);
  }
  async function mountWorkbench(root, app, options, facade) {
    const shell = document.createElement("div"); shell.className = "portalWorkbenchPageLayout portalWorkbenchViewportShell projectionWorkbenchShell";
    const railRoot = document.createElement("aside"); railRoot.className = "portalActivityRail";
    const panelRoot = document.createElement("div"); panelRoot.className = "portalWorkbenchViewportRoot projectionWorkbenchRoot";
    shell.append(railRoot, panelRoot); root.replaceChildren(shell);
    const showRail = !options.externalRail && !options.embedded;
    if (!showRail) railRoot.hidden = true;
    const id = `projection-${app.workbench_id}-${options.preview ? "preview" : "published"}${options.region ? `-${options.region}` : ""}${options.embedded ? "-embedded" : ""}`;
    const page = app.manifest ? pageController(app, options) : null;
    const regions = (app.manifest?.sidebar || [{ id: "content", title: app.title, icon: "window" }])
      .map(item => app.builtin === "development" ? { ...item, title: window.PortalI18n.t(item.title) } : item)
      .filter(item => options.region ? item.id === options.region : !options.excludeRegions?.includes(item.id));
    if (!regions.length) throw fail("panel_unknown", "工作台没有此页面");
    const requested = options.region || new URLSearchParams(location.search).get("region");
    const selected = regions.some(item => item.id === requested) ? requested : regions[0].id;
    const editors = regions.map(item => ({ id: item.id, type: page ? "projection-region" : "projection-content", title: item.title, icon: item.icon || "window", options: { app, preview: !!options.preview, source: item.source, regionTitle: item.title, page } }));
    let facadeError, rail, facadeDetail;
    const updateFacade = detail => {
      facadeDetail = detail;
      options.onFacadeDetail?.(detail);
      const state = detail?.state || {};
      const unread = detail?.snapshot?.viewer_state?.unread === true;
      const statusLabel = ["开发团队对话", state.working === true ? "运行中" : "", state.queued === true ? "排队中" : "", unread ? "有未读回复" : ""].filter(Boolean).map(value => window.PortalI18n.t(value)).join(window.PortalI18n.locale() === "en" ? ", " : "，");
      rail?.setItems([{ key: "facade", label: window.PortalI18n.t("开发团队对话"), glyph: window.PortalI18n.t("聊"), dismissible: false,
        title: statusLabel, ariaLabel: statusLabel, running: state.working === true, queued: state.queued === true, unread }]);
    };
    if (app.manifest?.chat && !options.region) {
      try {
        if (facade?.error) throw facade.error;
        const target = facade?.target;
        const binding = app.bindings[app.manifest.chat.binding];
        if (!target || target.resident_id !== binding?.residents?.facade || target.lineage_id !== binding?.lineage_id) {
          throw fail("conflict", "工作台的门面绑定已变化，请重新打开工作台");
        }
        editors.push({ id: "facade", type: "chat", title: window.PortalI18n.t("开发团队对话"), closable: false, options: { target, submissionContext: submittedTarget => page.context(submittedTarget), onDetail: updateFacade } });
      } catch (error) { facadeError = error; }
    }
    const regionChats = await Promise.all(regions.filter(item => item.chat).map(async item => {
      const panelId = `chat-${item.id}`;
      try {
        const target = await request("facade", { id: app.workbench_id, preview: !!options.preview, panel: item.id });
        const binding = app.bindings[item.chat.binding];
        if (target.resident_id !== binding?.residents?.facade || target.lineage_id !== binding?.lineage_id) throw fail("resident_unbound", "分栏门面绑定已变化，请刷新");
        return { id: panelId, type: "chat", title: `${item.title} · ${window.PortalI18n.t("团队对话")}`, closable: false,
          options: { target, submissionContext: submittedTarget => page.context(submittedTarget, item.id), onDetail: options.region ? options.onFacadeDetail : undefined } };
      } catch (error) {
        const alert = document.createElement("p"); alert.setAttribute("role", "alert"); alert.textContent = `${item.title}：${error.message}`; root.prepend(alert);
        return null;
      }
    }));
    editors.push(...regionChats.filter(Boolean));
    const chatIds = editors.filter(item => item.type === "chat").map(item => item.id);
    const selectedChat = chatIds.includes(`chat-${selected}`) ? `chat-${selected}` : chatIds[0];
    const contentLayout = window.PortalWorkbench.model.stack(regions.map(item => item.id), selected);
    const workbench = window.PortalWorkbench.create({
      id, root: panelRoot, label: app.title, insets: { top: 0, right: 0, bottom: 0, left: 0 },
      initialEditors: editors,
      // An independent Panel uses its host's slot. Keep every region and Chat
      // available as tabs instead of nesting a second rail and split workspace.
      defaultLayout: options.embedded ? window.PortalWorkbench.model.stack(editors.map(item => item.id), selected)
        : chatIds.length ? window.PortalWorkbench.model.split("horizontal", [contentLayout, window.PortalWorkbench.model.stack(chatIds, selectedChat)], [2, 1]) : contentLayout,
    });
    const openRegion = panelId => {
      if (!workbench.focusPanel(panelId)) return false;
      const region = regions.find(item => item.id === panelId);
      if (region && workbench.getPanel(region.chat ? `chat-${panelId}` : "facade")) page.openChat(panelId);
      if (panelId !== "facade") rail?.setActiveView(panelId);
      if (!options.preview && !options.region) { const url = new URL(location.href); url.searchParams.set("region", panelId); history.replaceState(null, "", url); }
      return true;
    };
    page?.attach(workbench, openRegion);
    if (facadeError) { const alert = document.createElement("p"); alert.setAttribute("role", "alert"); alert.textContent = `${facadeError.code || "projection.resident_unbound"}：${facadeError.message}`; root.prepend(alert); }
    if (showRail) rail = window.PortalWorkbench.createActivityRail({ id, root: railRoot, label: app.title, activeView: selected, itemsLabel: window.PortalI18n.t("门面对话"), onItemSelect: () => workbench.showPanel("facade"), views: regions.map(item => ({ id: item.id, icon: item.icon || "window", label: item.title })), onViewSelect: view => {
      openRegion(typeof view === "string" ? view : view.id);
    } });
    if (editors.some(item => item.id === "facade")) updateFacade(facadeDetail);
    window.PortalUIPrimitives?.mountIcons?.(railRoot);
    let destroyed = false;
    const destroy = () => {
      if (destroyed) return;
      destroyed = true;
      options.signal?.removeEventListener("abort", destroy);
      rail?.destroy(); workbench.destroy(); shell.remove();
    };
    options.signal?.addEventListener("abort", destroy, { once: true });
    if (options.signal?.aborted) { destroy(); throw fail("cancelled", "工作台已关闭"); }
    const controller = { workbench, destroy, focusChat: () => options.region ? page.openChat(options.region) : openRegion("facade"),
      setVisible(visible) {
        page?.setVisible(visible);
        workbench.setVisible(visible);
      },
    };
    options.onMounted?.(controller);
    if (requested) openRegion(requested === "facade" ? "facade" : selected);
    // Workbench owns restored tabs, split layouts and outer visibility. Hidden
    // regions keep loading and expose their own ready/error; they must not hold
    // the selected view open or tear it down on a background asset failure.
    const shown = regions.map(item => workbench.getPanel(item.id)).filter(panel => panel.isVisible());
    try { await Promise.all(shown.map(panel => panel.ready)); }
    catch (error) { destroy(); throw error; }
    if (destroyed) throw fail("cancelled", "工作台已关闭");
    return controller;
  }
  addEventListener("message", async event => {
    const message = event.data;
    if (message?.type === "portal-projection-keybinding") {
      const record = active.get(message.channel);
      if (!record || record.destroyed || !record.visible || event.source !== record.frame.contentWindow
        || record.frame.ownerDocument.activeElement !== record.frame) return;
      const key = message.key;
      if (!key || typeof key.key !== "string" || key.key.length > 32
        || typeof key.code !== "string" || key.code.length > 32 || key.isComposing || key.repeat) return;
      const keyboard = new KeyboardEvent("keydown", { key: key.key, code: key.code,
        ctrlKey: key.ctrlKey === true, metaKey: key.metaKey === true,
        altKey: key.altKey === true, shiftKey: key.shiftKey === true, cancelable: true });
      window.PortalCommandRegistry?.dispatchGlobalKeybinding(keyboard, message.editable === true);
      return;
    }
    if (message?.type !== "portal-projection-request") return;
    const record = active.get(message.channel);
    if (!record || event.source !== record.frame.contentWindow || record.destroyed || typeof message.requestId !== "string") return;
    const { app, frame } = record;
    const payload = message.payload || {};
    const reply = { type: "portal-projection-result", channel: message.channel, requestId: message.requestId };
    try {
      if (message.method === "context") reply.result = { workbench_id: app.workbench_id, title: app.title, bindings: app.bindings, preview: record.preview, panel_id: record.panelId, visible: record.visible, themes: window.PortalTheme?.options?.() || null, focus: record.page?.canRead(record) ? record.page.snapshot() : undefined };
      else if (message.method === "focus") reply.result = await record.page?.focus(record, payload.object);
      else if (message.method === "focus-read") reply.result = record.page?.canRead(record) ? record.page.snapshot() : undefined;
      else if (message.method === "open-chat") {
        if (!record.page) throw fail("resident_unbound", "此工作台没有分栏对话");
        reply.result = await record.page.openChat(record.panelId);
      }
      else if (message.method === "open-panel") {
        if (!record.page) throw fail("panel_unknown", "此工作台没有清单功能区");
        reply.result = record.page.openPanel(payload.panelId);
      }
      else if (message.method === "open-page") {
        if (!window.PortalNav?.openPage) throw fail("page_unavailable", "页面导航宿主未就绪");
        try {
          reply.result = await window.PortalNav.openPage(payload.url, { handleCurrent: target => {
            if (record.destroyed) throw fail("cancelled", "功能区已关闭");
            return record.page?.openPage(target) ?? null;
          } });
        } catch (error) {
          if (error.code?.startsWith("portal.")) error.code = error.code.replace("portal.", "projection.");
          throw error;
        }
      }
      else if (message.method === "watch") {
        const revision = record.watchRevision = (record.watchRevision || 0) + 1;
        record.pendingWatch = revision;
        invalidateObservation(record);
        try {
          const result = await observeReferences(record, payload.refs, () => !record.destroyed && revision === record.watchRevision);
          record.refs = result.references.map(item => item.ref);
          record.references = result.references;
          reply.result = result;
        } finally { if (record.pendingWatch === revision) record.pendingWatch = null; }
      }
      else if (message.method === "cancel-feedback") reply.result = { cancelled: cancelFeedback(record) };
      else if (message.method === "feedback") {
        if (!record.page) throw fail("resident_unbound", "工作台没有门面对话");
        const object = record.page.lookup(payload.ref);
        if (!object) throw fail("object_unknown", "请先聚焦反馈对象");
        if (object.status === "simulated") throw fail("simulated", "模拟演示不能触发真实操作");
        if (record.busy) throw fail("feedback_busy", "已有操作等待确认");
        if (!record.visible || record.page.snapshot().object?.ref !== payload.ref) throw fail("cancelled", "反馈对象已不在当前情境，请重新选择");
        if (payload.subject !== undefined && (typeof payload.subject !== "string" || !payload.subject.trim() || payload.subject.length > 240)) throw fail("feedback", "讨论对象名称须为非空文字（最多 240 个 UTF-16 码元）");
        if (typeof payload.text !== "string" || !payload.text.trim() || payload.text.length > 20000) throw fail("feedback", "请填写反馈内容（最多 20000 字）");
        const feedbackPayload = { ...payload,
          anchor: feedbackContextField(payload.anchor, "anchor", 2048),
          scene: feedbackContextField(payload.scene, "scene", 8000),
        };
        const pending = { controller: new AbortController(), revision: record.page.snapshot().revision };
        record.pendingFeedback = pending;
        record.busy = true;
        try {
          const presentation = await feedbackPresentation(record, object, feedbackPayload);
          checkFeedback(record, pending);
          post(record, "confirmation", { requestId: message.requestId, phase: "pending" });
          const accepted = await confirmOperation({ instruction: "feedback", object, arguments: feedbackPayload }, pending.controller.signal, presentation);
          const guard = () => checkFeedback(record, pending);
          guard();
          if (!accepted) throw fail("cancelled", "已取消确认，反馈尚未发送");
          reply.result = await record.page.feedback(record, feedbackPayload, guard);
        } finally {
          if (record.pendingFeedback === pending) record.pendingFeedback = null;
          record.busy = false;
          post(record, "confirmation", { requestId: message.requestId, phase: "resolved" });
        }
      }
      else if (message.method === "state") {
        if (Object.hasOwn(payload, "value")) {
          record.stateWrites = (record.stateWrites || 0) + 1;
          try {
            reply.result = await request("state", { id: app.workbench_id, panel: record.page ? record.panelId : "", value: payload.value });
            record.stateSaveFailed = false;
          } catch (error) { record.stateSaveFailed = true; throw error; }
          finally { record.stateWrites--; }
        } else reply.result = await request(`state?id=${encodeURIComponent(app.workbench_id)}&panel=${encodeURIComponent(record.page ? record.panelId : "")}`);
      }
      else if (message.method === "resource") reply.result = await request("resource", { id: app.workbench_id, operation: payload.operation, arguments: payload.arguments });
      else if (message.method === "directories") reply.result = await request("directories", { id: app.workbench_id, arguments: payload.arguments });
      else if (message.method === "release") {
        // A bounded business service, never an arbitrary URL/ISA proxy. The
        // service owns authorization and the config/run idempotency ledger.
        if (record.panelId !== "packaging" || record.preview) throw fail("release_context", "请在打包发布页面使用发布服务");
        const allowed = new Set(["configs.list", "configs.get", "configs.save", "runs.list", "runs.get", "runs.start", "candidates", "targets"]);
        if (!allowed.has(payload.operation)) throw fail("release_operation", "不支持的发布操作");
        reply.result = await request("release", { id: app.workbench_id, panel: record.panelId, operation: payload.operation, arguments: payload.arguments || {} });
      }
      else if (message.method === "worker-source") {
        if (!record.assets) throw fail("worker", "Worker 需要应用包修订");
        const asset = await record.assets.read(payload.path, "");
        if (!/\.(?:m?js)$/.test(asset.path) || !/^(?:text|application)\/javascript(?:;|$)/.test(asset.contentType)) throw fail("worker", "Worker 必须是本修订的 JavaScript 产物");
        reply.result = asset.content;
      }
      else if (message.method === "libraries" || message.method === "library-source") {
        if (!record.assets) throw fail("library", "登记库需要应用包修订");
        reply.result = message.method === "libraries" ? await record.assets.libraries() : await record.assets.library(payload.name);
      }
      else if (message.method === "hatg") {
        const operation = { ...payload, id: app.workbench_id, preview: record.preview, confirmed: false };
        operation.object = record.page?.current();
        if (operation.object?.status === "simulated") throw fail("simulated", "模拟演示不能触发真实指令");
        try { reply.result = await request("isa", operation); }
        catch (error) {
          if (error.code !== "projection.confirmation_required") throw error;
          if (record.busy) throw new Error("已有操作等待确认，请完成后重试");
          record.busy = true;
          try {
            // The SDK's response budget covers host work, not the user's
            // time reviewing an explicit approval. Keep the same request.
            frame.contentWindow.postMessage({ type: "portal-projection-confirmation", channel: message.channel, requestId: message.requestId, phase: "pending" }, "*");
            let accepted;
            try { accepted = await confirmOperation(operation); }
            finally {
              if (!record.destroyed) frame.contentWindow.postMessage({ type: "portal-projection-confirmation", channel: message.channel, requestId: message.requestId, phase: "resolved" }, "*");
            }
            if (!accepted || record.destroyed) throw Object.assign(new Error("操作已取消"), { code: "projection.cancelled" });
            reply.result = await request("isa", { ...operation, confirmed: true, idempotency_key: operation.idempotency_key || globalThis.PortalUIPrimitives.randomUUID() });
          } finally { record.busy = false; }
        }
      } else if (message.method === "open-lineage") {
        const binding = app.bindings[payload.binding || "main"];
        if (!binding) throw new Error("Lineage 未绑定");
        window.open(`/lineage?lineage=${encodeURIComponent(binding.lineage_id)}`, "_blank", "noopener");
        reply.result = true;
      } else throw new Error("未知工作台能力");
    } catch (error) { reply.error = error.message; reply.code = error.code || "projection.request_failed"; }
    if (!record.destroyed) frame.contentWindow.postMessage(reply, "*");
  });
  window.PortalProjectionHost = Object.freeze({ open, mount, createPanel: createContentPanel });
})();
