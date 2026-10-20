(function installWorkbenchDesignLive(global) {
  "use strict";

  global.PortalDesignLiveAdapters = global.PortalDesignLiveAdapters || {};
  if (global.PortalDesignLiveAdapters["design-workbench"]) return;

  const adapterId = "design-workbench";
  const editorType = "design-tour-workbench-document";
  const singletonType = "design-tour-workbench-singleton";
  const workbenchId = "design-tour-workbench";
  const runtime = { root: null, host: null, workbench: null, store: null, snapshot: null };
  const copy = value => structuredClone(value);
  const layoutReady = () => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));

  function requireState(condition, message) {
    if (!condition) throw new Error(`Workbench Live 验收失败：${message}`);
  }

  function checkSignal(signal) {
    if (signal?.aborted) throw new DOMException("Workbench Live 已取消", "AbortError");
  }

  function same(actual, expected, message) {
    requireState(JSON.stringify(actual) === JSON.stringify(expected), message);
  }

  function panelPackage(type, instances) {
    return {
      type,
      version: 1,
      presentation: { title: window.PortalI18n.t("只读示例文档"), icon: "file-text" },
      sizing: { minWidth: 180, minHeight: 140 },
      instances,
      capabilities: ["snapshot", "restore", "openResource"],
      behavior: { canHide: true, canCollapse: true, canMove: true, closable: true },
      context: { channels: [] },
      persistence: { schemaVersion: 1 },
      create(options) {
        const root = options.root;
        const heading = document.createElement("h3");
        const input = document.createElement("input");
        input.setAttribute("aria-label", window.PortalI18n.t("演示区本地未提交内容"));
        input.placeholder = window.PortalI18n.t("仅保存在演示内存中");
        const state = { binding: copy(options.binding || {}), dirty: false, connects: 0, disconnects: 0, destroyed: false, opens: 0 };
        root.append(heading, input);
        const render = () => { heading.textContent = state.binding.title || window.PortalI18n.t("只读示例文档"); };
        const edit = () => { state.dirty = true; };
        input.addEventListener("input", edit);
        render();
        return {
          element: root,
          connect() { state.connects += 1; },
          disconnect() { state.disconnects += 1; },
          snapshot() { return { ...copy(state), draft: input.value }; },
          restore(value) {
            state.binding = copy(value.binding || {});
            state.dirty = Boolean(value.dirty);
            input.value = String(value.draft || "");
            render();
          },
          async openResource(resource) {
            if (state.dirty) throw new Error("本地演示 dirty guard：未提交内容保留，拒绝切换绑定");
            state.binding = copy(resource);
            state.opens += 1;
            render();
          },
          destroy() {
            state.destroyed = true;
            input.removeEventListener("input", edit);
            root.remove();
          },
        };
      },
    };
  }

  function registerPackages() {
    const registered = new Set(global.PortalWorkbench.registeredPanelPackages().map(item => item.type));
    for (const [type, instances] of [[editorType, "multiple"], [singletonType, "single"]]) {
      if (!registered.has(type)) global.PortalWorkbench.registerPanelPackage(panelPackage(type, instances));
    }
  }

  function disposeWorkbench() {
    runtime.workbench?.destroy();
    runtime.workbench = null;
    runtime.host?.replaceChildren();
  }

  function dispose() {
    try {
      disposeWorkbench();
    } finally {
      runtime.root?.remove();
      runtime.root = null;
      runtime.host = null;
      runtime.store = null;
    }
  }

  function stage() {
    if (runtime.root?.isConnected) return;
    const root = document.createElement("aside");
    root.className = "portalDetailSurface";
    root.dataset.designTourWorkbench = "stage";
    root.setAttribute("aria-label", window.PortalI18n.t("工作台生产组件只读 Live"));
    root.style.cssText = "position:fixed;inset:12vh 4vw auto;z-index:1200;background:var(--panel,#fff);padding:1rem;box-shadow:0 1rem 3rem #0003";
    const title = document.createElement("h2");
    title.textContent = window.PortalI18n.t("工作台 · 本地只读行为验证");
    const explanation = document.createElement("p");
    explanation.textContent = window.PortalI18n.t("真实 Workbench 与编辑器打开协议；示例文档不连接后端，退出后移除。");
    const host = document.createElement("div");
    host.dataset.designTourWorkbench = "host";
    host.style.cssText = "position:relative;width:100%;height:55vh;min-height:18rem";
    const result = document.createElement("output");
    result.dataset.designTourWorkbench = "result";
    result.setAttribute("aria-live", "polite");
    root.append(title, explanation, host, result);
    document.body.append(root);
    runtime.root = root;
    runtime.host = host;
  }

  function descriptor(id, options = {}) {
    const binding = { kind: options.type || editorType, id, title: options.title || id };
    return { id, type: binding.kind, binding, title: binding.title, options: { binding }, ...options };
  }

  function mount({ initialEditors, layout, store = runtime.store, defaultHidden = [] }) {
    disposeWorkbench();
    runtime.store = store || {
      value: {},
      writes: 0,
      read() { return copy(this.value); },
      replace(value) { this.writes += 1; this.value = copy(value); return copy(value); },
    };
    runtime.workbench = global.PortalWorkbench.create({
      id: workbenchId,
      root: runtime.host,
      label: window.PortalI18n.t("Workbench Live 内存工作区"),
      toolbarPlacement: "tabs",
      store: runtime.store,
      initialEditors,
      defaultLayout: layout,
      defaultPreset: "original",
      presets: [{ id: "original", name: window.PortalI18n.t("默认演示布局"), layout, hidden: defaultHidden }],
      showSingleTabHeader: true,
      createEditorDescriptor: ({ instanceId, type, resource }) => descriptor(instanceId, { type, options: { binding: resource } }),
    });
    return runtime.workbench;
  }

  function tab(panelId) {
    const element = runtime.host.querySelector(`[data-portal-workbench-tab="${CSS.escape(panelId)}"]`);
    requireState(element?.isConnected, `标签 ${panelId} 不存在`);
    return element;
  }

  function findStack(node, panelId) {
    if (node?.type === "stack" && node.children.some(child => child.panelId === panelId)) return node;
    for (const child of node?.children || []) {
      const found = findStack(child, panelId);
      if (found) return found;
    }
    return null;
  }

  function assertBinding(panelId, bindingId) {
    const workbench = runtime.workbench;
    requireState(workbench.getPanel(panelId)?.snapshot().binding.id === bindingId, "运行对象没有打开指定资源");
    requireState(workbench.descriptors.get(panelId)?.binding?.id === bindingId, "面板描述符绑定与运行对象不一致");
  }

  const probeTypes = {
    document: "design-tour-workbench-lifecycle-document",
    graph: "design-tour-workbench-lifecycle-graph",
    observer: "design-tour-workbench-lifecycle-observer",
  };

  function registerProbePackages() {
    const registered = new Set(global.PortalWorkbench.registeredPanelPackages().map(item => item.type));
    for (const [kind, type] of Object.entries(probeTypes)) {
      if (registered.has(type)) continue;
      global.PortalWorkbench.registerPanelPackage({
        ...panelPackage(type, "multiple"),
        context: { channels: kind === "observer" ? ["*"] : [kind === "document" ? "documentTarget" : "graphSelection", "releaseProbe"] },
        create(options) {
          const root = options.root;
          const input = document.createElement("input");
          input.setAttribute("aria-label", window.PortalI18n.text`${options.panelId} 本地草稿`);
          const button = document.createElement("button");
          button.type = "button";
          button.textContent = window.PortalI18n.t("选择示例文档");
          const output = document.createElement("output");
          root.append(input, button, output);
          const events = ["create"];
          const deliveries = [];
          const aborter = new AbortController();
          const temporaryUrl = URL.createObjectURL(new Blob([`local:${options.panelId}`], { type: "text/plain" }));
          const settled = new Promise(resolve => aborter.signal.addEventListener("abort", () => resolve("aborted"), { once: true }));
          let unsubscribe = null;
          let pulses = 0;
          let revoked = false;
          const selection = Object.freeze({ id: options.panelId, title: window.PortalI18n.t("演示区内存文档") });
          const select = () => options.context.select("documentTarget", selection, { source: options.panelId });
          button.addEventListener("click", select);
          const release = () => {
            unsubscribe?.();
            unsubscribe = null;
            aborter.abort();
            if (!revoked) URL.revokeObjectURL(temporaryUrl);
            revoked = true;
            button.removeEventListener("click", select);
          };
          return {
            element: root,
            connect(context) {
              events.push("connect");
              unsubscribe = context.subscribe("releaseProbe", () => { pulses += 1; });
            },
            restore(value) { events.push("restore"); input.value = String(value.draft || ""); },
            setContext(values, _context, change) {
              events.push("setContext");
              deliveries.push({ values: copy(values), channels: [...(change?.changedChannels || [])] });
              output.textContent = JSON.stringify(values);
            },
            show() { events.push("show"); },
            hide() { events.push("hide"); },
            snapshot() { return { draft: input.value }; },
            disconnect() { events.push("disconnect"); release(); },
            destroy() { events.push("destroy"); release(); root.remove(); },
            inspect() {
              return { root, panelId: options.panelId, workbenchId: options.workbenchId, frozen: Object.isFrozen(options.panelPackage), events: [...events], deliveries: copy(deliveries), pulses, aborted: aborter.signal.aborted, temporaryUrl, settled, draft: input.value };
            },
          };
        },
      });
    }
  }

  function mountProbeWorkbench(kind = "instances", replaceSameId = false) {
    registerProbePackages();
    if (!replaceSameId) disposeWorkbench();
    const initialEditors = kind === "context"
      ? [descriptor("probe-a", { type: probeTypes.document }), descriptor("probe-b", { type: probeTypes.graph }), descriptor("probe-all", { type: probeTypes.observer })]
      : [descriptor("probe-a", { type: probeTypes.document, closable: true }), descriptor("probe-b", { type: probeTypes.document, closable: true })];
    const { split, stack } = global.PortalWorkbench.model;
    const layout = split("horizontal", initialEditors.map(item => stack([item.id])));
    const stored = {
      schemaVersion: 2,
      panelStates: {
        "probe-a": { packageType: probeTypes.document, packageVersion: 1, schemaVersion: 1, value: { draft: "来自局部快照的草稿" } },
      },
    };
    runtime.workbench = global.PortalWorkbench.create({
      id: workbenchId,
      root: runtime.host,
      initialEditors,
      defaultLayout: layout,
      context: { documentTarget: "initial-document", graphSelection: "initial-graph" },
      store: { read: () => copy(stored), replace: value => copy(value) },
      showSingleTabHeader: true,
    });
    return runtime.workbench;
  }

  async function assertReleased(probe) {
    const state = probe.inspect();
    requireState(state.events.filter(event => event === "disconnect").length === 1 && state.events.filter(event => event === "destroy").length === 1, "关闭没有恰好执行一次 disconnect/destroy");
    requireState(state.events.indexOf("disconnect") < state.events.indexOf("destroy"), "销毁发生在断开之前");
    requireState(state.aborted && await state.settled === "aborted", "局部异步任务未取消");
    requireState(!state.root.isConnected, "销毁后的 DOM 仍占据页面");
    let readable = false;
    try { readable = (await fetch(state.temporaryUrl)).ok; } catch { readable = false; }
    requireState(!readable, "销毁后的临时 URL 仍可读取");
  }

  async function openResource(resourceId, mode = "reuse", extra = {}) {
    const result = await runtime.workbench.openResource({
      type: editorType,
      resource: { kind: editorType, id: resourceId, title: resourceId },
      mode,
      ...extra,
    });
    requireState(result?.instanceId && runtime.workbench.getPanel(result.instanceId), "打开请求未返回已挂载实例");
    assertBinding(result.instanceId, resourceId);
    return result;
  }

  const capabilities = {
    "workbench.package.instance-lifecycle": {
      setup() { mountProbeWorkbench(); },
      async verify(signal) {
        const original = runtime.workbench;
        const first = original.getPanel("probe-a");
        const peer = original.getPanel("probe-b");
        requireState(first !== peer && first.element !== peer.element, "工厂返回值在实例之间共享");
        for (const [panelId, probe] of [["probe-a", first], ["probe-b", peer]]) {
          original.focusPanel(panelId);
          const state = probe.inspect();
          requireState(state.panelId === panelId && state.workbenchId === original.id && state.root === probe.element && state.frozen, "工厂创建上下文或独立根节点不正确");
          requireState(state.events.indexOf("connect") > state.events.indexOf("create") && state.events.indexOf("setContext") > state.events.indexOf("connect") && state.events.indexOf("show") > state.events.indexOf("setContext"), `初次装配生命周期顺序错误：${panelId} ${state.events.join("→")}`);
        }
        const state = first.inspect();
        requireState(state.events.indexOf("restore") > state.events.indexOf("connect") && state.events.indexOf("restore") < state.events.indexOf("setContext") && state.draft === "来自局部快照的草稿", "局部恢复未在 connect 和 setContext 之间生效");
        first.element.querySelector("input").value = "实例 A 独立输入";
        requireState(peer.snapshot().draft === "", "编辑实例 A 影响了实例 B");
        requireState((await fetch(state.temporaryUrl)).ok, "临时本地资源未实际创建");
        checkSignal(signal);
        const replacement = mountProbeWorkbench("instances", true);
        requireState(replacement !== original && global.PortalWorkbench.get(workbenchId) === replacement, "相同 ID 没有切换到新 Workbench");
        requireState(replacement.getPanel("probe-a") !== first && replacement.getPanel("probe-b") !== peer, "同 ID 重建复用了旧运行对象");
        await assertReleased(first);
        await assertReleased(peer);
        return "实例拥有独立根节点和草稿；connect→restore→setContext 后在聚焦时 show，同 ID 重建释放旧实例并登记新工作台。";
      },
    },
    "workbench.package.close-release": {
      setup() { mountProbeWorkbench(); },
      async verify(signal) {
        const workbench = runtime.workbench;
        const closing = workbench.getPanel("probe-a");
        const survivor = workbench.getPanel("probe-b");
        workbench.patchContext({ releaseProbe: "before-close" });
        requireState(closing.inspect().pulses === 1 && survivor.inspect().pulses === 1, "示例订阅没有实际建立");
        requireState(await (await fetch(closing.inspect().temporaryUrl)).text() === "local:probe-a", "临时 URL 没有实际可读内容");
        checkSignal(signal);
        requireState(workbench.closePanel("probe-a") === true && !workbench.getPanel("probe-a"), "关闭后实例仍在注册表");
        requireState(!workbench.getState().hidden.includes("probe-a") && !findStack(workbench.getLayout(), "probe-a"), "关闭被误作隐藏或未清除布局");
        await assertReleased(closing);
        workbench.patchContext({ releaseProbe: "after-close" });
        requireState(closing.inspect().pulses === 1 && survivor.inspect().pulses === 2, "关闭没有解绑订阅或误清理了相邻面板");
        requireState(workbench.closePanel("probe-a") === false && workbench.getPanel("probe-b") === survivor && survivor.element.isConnected, "重复关闭破坏了相邻实例");
        await assertReleased(closing);
        return "关闭区别于隐藏：实例、布局、订阅、局部异步任务、临时 URL 与 DOM 均释放；重复关闭不影响相邻面板。";
      },
    },
    "workbench.context.channel-routing": {
      setup() { mountProbeWorkbench("context"); },
      async verify() {
        const workbench = runtime.workbench;
        const documentPanel = workbench.getPanel("probe-a");
        const graphPanel = workbench.getPanel("probe-b");
        const observer = workbench.getPanel("probe-all");
        const counts = () => [documentPanel, graphPanel, observer].map(probe => probe.inspect().deliveries.length);
        same(counts(), [1, 1, 1], "初始上下文未恰好投递一次");
        documentPanel.element.querySelector("button").click();
        same(counts(), [2, 1, 2], "documentTarget 未按声明通道路由");
        const selected = workbench.getSelection("documentTarget");
        requireState(selected?.id === "probe-a" && documentPanel.element.querySelector("output").textContent.includes("probe-a"), "面板选择动作没有更新真实 Context 和视图");
        same(documentPanel.inspect().deliveries.at(-1).channels, ["documentTarget"], "投递缺少实际变更通道");
        workbench.patchContext({ documentTarget: selected });
        same(counts(), [2, 1, 2], "未变化通道重复触发 setContext");
        const graph = { ids: ["local-node"] };
        workbench.setSelection("graphSelection", graph, { source: "design-workbench" });
        same(counts(), [2, 2, 3], "graphSelection 污染无关订阅或漏投通配订阅");
        const before = workbench.contextStore.snapshot();
        let rejected = false;
        try { workbench.setSelection("not-a-selection-channel", "must-not-commit"); } catch (error) { rejected = error instanceof TypeError; }
        requireState(rejected, "非法选择通道未拒绝");
        same(workbench.contextStore.snapshot(), before, "非法路由改变了已有 Context");
        const other = global.PortalWorkbenchContext.create({ workbenchId: `${workbenchId}-other` });
        try { other.select("documentTarget", { id: "other-workbench" }); } finally { other.dispose(); }
        same(counts(), [2, 2, 3], "其他工作台的 Context 泄漏到当前面板");
        return "真实选择动作只投递关注通道及通配面板；同值更新不重复投递，非法选择与其他工作台更新不污染当前 Context。";
      },
    },
    "workbench.tabs.retain-runtime": {
      setup() {
        const { stack } = global.PortalWorkbench.model;
        mount({ initialEditors: [descriptor("document-a"), descriptor("document-b")], layout: stack(["document-a", "document-b"]) });
      },
      async verify(signal) {
        const workbench = runtime.workbench;
        const original = workbench.getPanel("document-a");
        const input = original.element.querySelector("input");
        input.value = "切换标签后保留的未提交文本";
        input.dispatchEvent(new Event("input", { bubbles: true }));
        const before = original.snapshot();
        tab("document-b").click();
        requireState(findStack(workbench.getLayout(), "document-b").activePanelId === "document-b", "点击未切换活动标签");
        tab("document-a").click();
        tab("document-a").click();
        await layoutReady();
        checkSignal(signal);
        requireState(workbench.getPanel("document-a") === original && input.isConnected, "标签选择重新挂载了运行对象");
        same(original.snapshot(), before, "标签切换丢失草稿或重新触发生命周期");
        requireState(findStack(workbench.getLayout(), "document-a").activePanelId === "document-a", "未恢复所选标签");
        requireState(original.element.getClientRects().length > 0, "活动面板未显示");
        return "往返切换并重复选择活动标签后，运行对象、挂载次数和未提交文本全部保留。";
      },
    },
    "workbench.panels.hide-restore": {
      setup() {
        const { split, stack } = global.PortalWorkbench.model;
        mount({ initialEditors: [descriptor("required", { required: true, closable: false }), descriptor("optional", { closable: false })], layout: split("horizontal", [stack(["required"]), stack(["optional"])]) });
      },
      async verify() {
        const workbench = runtime.workbench;
        const original = workbench.getPanel("optional");
        requireState(workbench.hidePanel("optional") === true, "可选面板未被隐藏");
        requireState(workbench.getState().hidden.includes("optional") && !workbench.isPanelVisible("optional"), "隐藏状态未更新");
        requireState(!findStack(workbench.getLayout(), "optional"), "隐藏面板仍占据布局");
        requireState(workbench.hidePanel("required") === false, "必需面板可以被隐藏");
        requireState(!workbench.getState().hidden.includes("required"), "必需面板进入了隐藏集合");
        workbench.openCustomizer();
        const toggle = runtime.host.querySelector('[data-workbench-panel-toggle="optional"]');
        requireState(toggle && !toggle.checked && !toggle.disabled, "隐藏面板没有可用恢复入口");
        toggle.click();
        requireState(workbench.isPanelVisible("optional") && findStack(workbench.getLayout(), "optional"), "恢复入口没有恢复布局");
        requireState(workbench.getPanel("optional") === original, "恢复面板重建了运行对象");
        return "可选面板隐藏后不占布局，可从工作台菜单恢复原实例；必需面板始终保持可见。";
      },
    },
    "workbench.layout.restore": {
      setup() {
        const { split, stack } = global.PortalWorkbench.model;
        mount({ initialEditors: [descriptor("navigation"), descriptor("document-a"), descriptor("document-b"), descriptor("preview")], layout: split("horizontal", [stack(["navigation"]), stack(["document-a", "document-b"]), stack(["preview"])]) });
      },
      async verify(signal) {
        const { split, stack } = global.PortalWorkbench.model;
        const initialEditors = [descriptor("navigation"), descriptor("document-a"), descriptor("document-b"), descriptor("preview")];
        const defaultLayout = split("horizontal", [stack(["navigation"]), stack(["document-a", "document-b"]), stack(["preview"])]);
        runtime.workbench.applyWorkspace({ name: window.PortalI18n.t("本地演示命名布局"), layout: split("vertical", [stack(["document-a", "document-b"], "document-b"), stack(["navigation"])], [2, 1]), hidden: ["preview"] });
        runtime.workbench.collapsePanel("navigation");
        tab("document-b").click();
        const original = runtime.workbench.getPanel("document-b");
        const input = original.element.querySelector("input");
        input.value = "重新装配后仍保留的本地草稿";
        input.dispatchEvent(new Event("input", { bubbles: true }));
        const before = runtime.workbench.getState();
        disposeWorkbench();
        requireState(original.snapshot().destroyed, "重建前旧 runtime 未释放");
        mount({ initialEditors, layout: defaultLayout });
        await layoutReady();
        checkSignal(signal);
        const after = runtime.workbench.getState();
        for (const field of ["layout", "hidden", "collapsed", "activeWorkspaceName", "activeOpenTargetPanelId", "placements", "savedWorkspaces"]) {
          same(after[field], before[field], `重新装配丢失 ${field}`);
        }
        requireState(runtime.workbench.getPanel("document-b") !== original, "未真实重新装配 Workbench");
        requireState(runtime.workbench.getPanel("document-b").snapshot().draft === input.value, "面板状态未恢复");
        requireState(!runtime.workbench.isPanelVisible("preview") && runtime.workbench.isPanelCollapsed("navigation"), "隐藏或折叠状态未恢复");
        return "销毁并重新装配生产工作台后，命名布局、活动标签、隐藏、折叠与本地草稿均从内存快照恢复。";
      },
    },
    "workbench.splitter.keyboard": {
      setup() {
        const { split, stack } = global.PortalWorkbench.model;
        mount({ initialEditors: [descriptor("left"), descriptor("right")], layout: split("horizontal", [stack(["left"]), stack(["right"])]) });
      },
      async verify(signal) {
        const handle = runtime.host.querySelector(".portalWorkbenchSplitHandle");
        requireState(handle?.getAttribute("role") === "separator" && handle.tabIndex >= 0, "分隔条缺少键盘与无障碍入口");
        const value = () => Number(handle.getAttribute("aria-valuenow"));
        const press = async (key, shiftKey = false) => {
          checkSignal(signal);
          const writes = runtime.store.writes;
          handle.focus({ preventScroll: true });
          handle.dispatchEvent(new KeyboardEvent("keydown", { key, shiftKey, bubbles: true, cancelable: true }));
          await layoutReady();
          checkSignal(signal);
          requireState(runtime.store.writes > writes, `键盘 ${key} 没有保存布局`);
          same(runtime.store.value.layout, runtime.workbench.getLayout(), "保存布局与可见布局不一致");
          return value();
        };
        const initialWeights = runtime.workbench.getLayout().weights;
        const right = await press("ArrowRight");
        requireState(runtime.workbench.getLayout().weights[0] > initialWeights[0], "方向键没有移动分隔条");
        const left = await press("ArrowLeft");
        requireState(left < right, "反向键没有反向移动分隔条");
        const accelerated = await press("ArrowRight", true);
        requireState(accelerated - left > right - left, "Shift 没有扩大调整步长");
        const minimum = await press("Home");
        requireState(minimum === Number(handle.getAttribute("aria-valuemin")), "Home 未到达允许范围下界");
        const maximum = await press("End");
        requireState(maximum === Number(handle.getAttribute("aria-valuemax")) && maximum > minimum, "End 未到达允许范围上界");
        requireState(runtime.workbench.getLayout().weights.every(weight => Number.isFinite(weight) && weight > 0), "键盘调整产生无效权重");
        return "方向键、Shift 加速、Home/End 边界均生效；每次操作保存布局，范围由生产组件计算。";
      },
    },
    "workbench.editor.safe-reuse": {
      setup() {
        const { stack } = global.PortalWorkbench.model;
        mount({ initialEditors: [descriptor("safe"), descriptor("dirty"), descriptor("locked", { locked: true }), descriptor("pinned", { reusable: false })], layout: stack(["safe", "dirty", "locked", "pinned"], "safe") });
        const input = runtime.workbench.getPanel("dirty").element.querySelector("input");
        input.value = "禁止覆盖的草稿";
        input.dispatchEvent(new Event("input", { bubbles: true }));
      },
      async verify(signal) {
        const workbench = runtime.workbench;
        const safe = workbench.getPanel("safe");
        const protectedState = Object.fromEntries(["dirty", "locked", "pinned"].map(id => [id, workbench.getPanel(id).snapshot()]));
        for (const targetInstanceId of ["dirty", "locked", "pinned"]) {
          checkSignal(signal);
          const result = await openResource(`替换资源-${targetInstanceId}`, "reuse", { targetInstanceId });
          requireState(result.instanceId === "safe" && result.decision.action === "reuse", "普通打开未复用唯一安全实例");
          requireState(workbench.descriptors.size === 4 && workbench.getPanel("safe") === safe, "普通打开创建了多余实例");
          for (const [id, value] of Object.entries(protectedState)) same(workbench.getPanel(id).snapshot(), value, `${id} 被另一个资源覆盖`);
        }
        return "脏、锁定和禁止复用实例均保留原绑定与草稿；三个普通打开请求只复用安全实例。";
      },
    },
    "workbench.editor.new-tab": {
      setup() {
        const { split, stack } = global.PortalWorkbench.model;
        mount({ initialEditors: [descriptor("origin"), descriptor("target")], layout: split("horizontal", [stack(["origin"]), stack(["target"])]) });
      },
      async verify() {
        const workbench = runtime.workbench;
        const targetStackId = findStack(workbench.getLayout(), "target").id;
        const original = workbench.getPanel("origin").snapshot();
        const first = await openResource("同一文档", "new-tab", { targetStackId });
        const second = await openResource("同一文档", "new-tab", { targetStackId });
        requireState(first.instanceId !== second.instanceId && workbench.descriptors.size === 4, "重复新标签请求被错误去重");
        for (const result of [first, second]) {
          requireState(result.decision.action === "create" && findStack(workbench.getLayout(), result.instanceId).id === targetStackId, "新标签未加入指定标签组");
          requireState(tab(result.instanceId).isConnected, "新标签没有可访问入口");
        }
        same(workbench.getPanel("origin").snapshot(), original, "新标签打开覆盖了原实例");
        tab(first.instanceId).click();
        requireState(findStack(workbench.getLayout(), first.instanceId).activePanelId === first.instanceId, "创建的标签不可切换");
        return "同一文档两次新标签打开得到两个独立实例，均进入指定标签组且可切换，原实例未被覆盖。";
      },
    },
    "workbench.editor.open-side": {
      setup() {
        const { stack } = global.PortalWorkbench.model;
        mount({ initialEditors: [descriptor("anchor")], layout: stack(["anchor"]) });
      },
      async verify(signal) {
        const { stack } = global.PortalWorkbench.model;
        for (const side of ["left", "right", "top", "bottom", undefined, "invalid"]) {
          checkSignal(signal);
          mount({ initialEditors: [descriptor("anchor")], layout: stack(["anchor"]), store: { read: () => ({}), replace: value => value } });
          const result = await openResource(`相邻文档-${side || "默认"}`, "side", { targetInstanceId: "anchor", ...(side === undefined ? {} : { side }) });
          const layout = runtime.workbench.getLayout();
          const expectedSide = ["left", "right", "top", "bottom"].includes(side) ? side : "right";
          requireState(layout.type === "split" && layout.children.length === 2, "旁边打开未创建相邻分栏");
          requireState(layout.orientation === (["top", "bottom"].includes(expectedSide) ? "vertical" : "horizontal"), "分栏方向错误");
          const newIndex = ["left", "top"].includes(expectedSide) ? 0 : 1;
          requireState(findStack(layout.children[newIndex], result.instanceId) && findStack(layout.children[1 - newIndex], "anchor"), "新旧实例相邻顺序错误");
          requireState(runtime.workbench.descriptors.size === 2, "旁边打开丢失或多建实例");
        }
        return "四个指定方向均生成正确相邻分栏；缺省及无效方向自动使用右侧，原实例始终保留。";
      },
    },
    "workbench.editor.singleton-guard": {
      setup() {
        const { stack } = global.PortalWorkbench.model;
        mount({ initialEditors: [descriptor("singleton", { type: singletonType })], layout: stack(["singleton"]) });
      },
      async verify(signal) {
        const workbench = runtime.workbench;
        const original = workbench.getPanel("singleton");
        for (const mode of ["reuse", "new-tab", "side"]) {
          checkSignal(signal);
          const result = await openResource(`单例资源-${mode}`, mode, { type: singletonType, resource: { kind: singletonType, id: `单例资源-${mode}`, title: mode } });
          requireState(result.instanceId === "singleton" && workbench.descriptors.size === 1 && workbench.getPanel("singleton") === original, "单实例编辑器被重复创建");
        }
        const input = original.element.querySelector("input");
        input.value = "单例中不能丢失的未提交内容";
        input.dispatchEvent(new Event("input", { bubbles: true }));
        const before = original.snapshot();
        const layout = workbench.getLayout();
        const binding = copy(workbench.descriptors.get("singleton").binding);
        let refusal = null;
        try {
          await workbench.openResource({ type: singletonType, mode: "side", resource: { kind: singletonType, id: "拒绝覆盖" } });
        } catch (error) {
          refusal = error;
        }
        requireState(refusal?.message.includes("dirty guard"), "脏单例未通过 runtime 拒绝切换绑定");
        same(original.snapshot(), before, "dirty guard 后丢失未提交内容");
        same(workbench.getLayout(), layout, "打开被拒绝后布局改变");
        same(workbench.descriptors.get("singleton").binding, binding, "打开被拒绝后描述符绑定改变");
        requireState(workbench.descriptors.size === 1 && workbench.getPanel("singleton") === original, "打开被拒绝后实例丢失");
        return "普通、新标签和旁边打开均复用唯一实例；runtime 拒绝脏绑定切换后，草稿、布局和实例完整保留。";
      },
    },
  };

  global.PortalDesignLiveAdapters[adapterId] = Object.freeze({
    id: adapterId,
    version: 1,
    ready: () => Boolean(global.PortalWorkbench?.create && global.PortalEditorOpenService?.create && global.PortalWorkbenchContext?.create && global.PortalCommandRegistry?.createScope),
    capture() {
      requireState(!runtime.snapshot, "前一次 Live 尚未恢复");
      runtime.snapshot = { focus: document.activeElement, scrollX: global.scrollX, scrollY: global.scrollY, draggingTab: document.body.classList.contains("draggingWorkbenchTab") };
      return runtime.snapshot;
    },
    async prepare(step, signal) {
      checkSignal(signal);
      requireState(runtime.snapshot, "必须先 capture 再准备 Live");
      const capability = capabilities[String(step?.capability || "")];
      requireState(capability, "未登记的工作台能力");
      registerPackages();
      stage();
      delete runtime.root.dataset.verifiedCapability;
      runtime.root.querySelector('[data-design-tour-workbench="result"]').textContent = "";
      runtime.store = null;
      capability.setup();
      await layoutReady();
      checkSignal(signal);
      return { target: runtime.host, capability: step.capability, snapshot: runtime.snapshot };
    },
    async commit(step, prepared, signal) {
      checkSignal(signal);
      requireState(prepared?.snapshot === runtime.snapshot && prepared?.target === runtime.host && runtime.host?.isConnected && prepared.capability === step?.capability, "准备结果已失效");
      const caption = await capabilities[prepared.capability].verify(signal);
      checkSignal(signal);
      runtime.root.querySelector('[data-design-tour-workbench="result"]').textContent = caption;
      runtime.root.dataset.verifiedCapability = prepared.capability;
      return { target: runtime.host, caption };
    },
    async restore(snapshot = runtime.snapshot || {}) {
      if (!runtime.snapshot && !runtime.root) return;
      const origin = runtime.snapshot || snapshot;
      try {
        dispose();
      } finally {
        runtime.snapshot = null;
        document.body.classList.toggle("draggingWorkbenchTab", Boolean(origin.draggingTab));
        global.scrollTo({ left: Number(origin.scrollX || 0), top: Number(origin.scrollY || 0), behavior: "instant" });
        if (origin.focus?.isConnected) origin.focus.focus?.({ preventScroll: true });
      }
    },
  });
})(window);
