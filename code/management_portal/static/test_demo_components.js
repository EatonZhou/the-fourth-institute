(() => {
  const semanticDetailSurface = PortalDetailSurface.mount(
    document.querySelector("#componentDetail [data-portal-detail-surface]"),
  );
  semanticDetailSurface.setState({
    state: "error",
    title: window.PortalI18n.t("无法加载"),
    message: window.PortalI18n.t("请重试"),
  });
  const tallDetailSurface = PortalDetailSurface.mount(
    document.querySelector("#componentTallDetail [data-portal-detail-surface]"),
  );
  const projectSummarySurface = PortalDetailSurface.mount(
    document.querySelector("#componentProjectSummary [data-portal-detail-surface]"),
  );
  projectSummarySurface.setState({
    state: "empty",
    title: window.PortalI18n.t("尚未创建项目摘要"),
    message: window.PortalI18n.t("创建 summary.md 后会在这里显示。"),
  });
  const dialogA = PortalUI.createDialogController({
    modal: "componentDialogA",
    initialFocus: "componentDialogAFocus",
    backdropClose: true,
    escapeClose: true,
  });
  const dialogB = PortalUI.createDialogController({
    modal: "componentDialogB",
    initialFocus: "componentDialogBFocus",
    backdropClose: true,
    escapeClose: true,
  });
  const detail = PortalUI.createDialogController({
    modal: "componentDetail",
    initialFocus: "componentDetailTitle",
    backdropClose: true,
    escapeClose: true,
  });
  const tallDetail = PortalUI.createDialogController({
    modal: "componentTallDetail",
    initialFocus: "componentTallDetailTitle",
    backdropClose: true,
    escapeClose: true,
  });
  let dirtyDemoDirty = true;
  const dirtyDialog = PortalUI.createDialogController({
    modal: "componentDirtyDialog",
    initialFocus: "componentDirtyInput",
    backdropClose: true,
    escapeClose: true,
    dirty: () => dirtyDemoDirty,
  });
  const projectSummaryDialog = PortalUI.createDialogController({
    modal: "componentProjectSummary",
    initialFocus: "componentProjectSummaryTitle",
    backdropClose: true,
    escapeClose: true,
  });
  let guardedDirty = true;
  let guardedConfirmCount = 0;
  const guardedStatus = document.getElementById("componentGuardedStatus");
  const guardedDialog = PortalUI.createDialogController({
    modal: "componentGuardedDialog",
    initialFocus: "componentGuardedInput",
    backdropClose: true,
    escapeClose: true,
    dirty: () => guardedDirty,
    confirmDiscard: () => {
      guardedConfirmCount += 1;
      guardedStatus.textContent = window.PortalI18n.text`guarded:rejected · confirms:${guardedConfirmCount}`;
      return false;
    },
    onClose: () => {
      guardedStatus.textContent = window.PortalI18n.text`guarded:closed · confirms:${guardedConfirmCount}`;
    },
  });
  let nativeDirty = true;
  let nativeConfirmCount = 0;
  const nativeStatus = document.getElementById("componentNativeStatus");
  const nativeDialog = PortalUI.createDialogController({
    modal: "componentNativeDialog",
    initialFocus: "componentNativeInput",
    escapeClose: true,
    dirty: () => nativeDirty,
    confirmDiscard: () => {
      nativeConfirmCount += 1;
      nativeStatus.textContent = window.PortalI18n.text`native:rejected · confirms:${nativeConfirmCount}`;
      return false;
    },
    onClose: () => {
      nativeStatus.textContent = window.PortalI18n.text`native:closed · confirms:${nativeConfirmCount}`;
    },
  });
  const feedbackResult = document.getElementById("componentFeedbackResult");
  const markAfterLayout = callback => requestAnimationFrame(
    () => requestAnimationFrame(callback),
  );
  const anchoredPanel = document.getElementById("componentAnchoredPanel");
  const anchoredTrigger = document.getElementById("componentAnchoredOpen");
  const anchoredBoundary = document.getElementById("componentPopoverBoundary");
  const anchoredPopover = PortalOverlay.bindPopover({
    trigger: anchoredTrigger,
    panel: anchoredPanel,
    boundary: anchoredBoundary,
    placement: "bottom-end",
    margin: 8,
    portal: true,
    onClose: () => anchoredBoundary.classList.remove("is-demo-pinned"),
  });
  const floatingPanel = document.getElementById("componentFloatingPanel");
  const floatingPopover = PortalOverlay.bindFloatingPopover({
    panel: floatingPanel,
    placement: "top",
    align: "center",
    closeOnOutside: true,
    closeOnEscape: true,
  });
  const dynamicPanel = document.getElementById("componentDynamicPanel");
  const dynamicStatus = document.getElementById("componentDynamicStatus");
  let dynamicCloseCount = 0;
  const dynamicPopover = PortalOverlay.bindDynamicPopover({
    panel: dynamicPanel,
    placement: "bottom-start",
    closeOnOutside: true,
    closeOnEscape: true,
    resolveTriggerAfterClose: previous => document.getElementById(previous.id),
    onClose: context => {
      dynamicCloseCount += 1;
      const trigger = document.getElementById("componentDynamicB");
      if (context instanceof KeyboardEvent && context.key === "Escape" && trigger) {
        trigger.replaceWith(trigger.cloneNode(true));
      }
      dynamicStatus.textContent = window.PortalI18n.text`close:${dynamicCloseCount}`;
    },
  });
  const bindDynamicTrigger = trigger => {
    trigger.addEventListener("click", () => {
      if (
        dynamicPopover.isOpen()
        && dynamicPopover.trigger() === trigger
      ) {
        trigger.remove();
        return;
      }
      dynamicPopover.open(trigger);
    });
  };
  bindDynamicTrigger(document.getElementById("componentDynamicA"));
  bindDynamicTrigger(document.getElementById("componentDynamicB"));
  document.getElementById("componentDynamicSwitch").addEventListener("click", () => {
    dynamicPopover.open(document.getElementById("componentDynamicB"));
  });
  const stackHost = document.getElementById("componentStackHost");
  const stackPanel = document.getElementById("componentStackPanel");
  const stackTrigger = document.getElementById("componentStackTrigger");
  const stackPopover = PortalOverlay.bindPopover({
    trigger: stackTrigger,
    panel: stackPanel,
    placement: "bottom-start",
  });
  stackTrigger.addEventListener("click", () => {
    if (stackPopover.isOpen()) {
      stackTrigger.replaceWith(stackTrigger.cloneNode(true));
      return;
    }
    stackPopover.open();
  });
  document.getElementById("openLayeredDemo").addEventListener("click", () => {
    dialogA.open();
    dialogB.open();
  });
  document.querySelectorAll("#componentDialogA [data-portal-detail-close]")
    .forEach(button => button.addEventListener("click", () => dialogA.close()));
  document.querySelectorAll("#componentDialogB [data-portal-detail-close]")
    .forEach(button => button.addEventListener("click", () => dialogB.close()));
  document.querySelectorAll("#componentDetail [data-portal-detail-close]")
    .forEach(button => button.addEventListener("click", () => detail.close()));
  document.querySelectorAll("#componentTallDetail [data-portal-detail-close]")
    .forEach(button => button.addEventListener("click", () => tallDetail.close()));
  document.querySelectorAll("#componentProjectSummary [data-portal-detail-close]")
    .forEach(button => button.addEventListener("click", () => projectSummaryDialog.close()));
  document.getElementById("componentDetailOpen")
    .addEventListener("click", () => detail.open());
  document.getElementById("componentTallDetailOpen")
    .addEventListener("click", () => tallDetail.open());
  document.getElementById("componentProjectSummaryOpen")
    .addEventListener("click", () => projectSummaryDialog.open());
  document.getElementById("componentDirtyOpen")
    .addEventListener("click", () => {
      dirtyDemoDirty = true;
      dirtyDialog.open();
    });
  document.getElementById("componentDirtyRequestClose")
    .addEventListener("click", () => dirtyDialog.close());
  document.getElementById("componentDirtyDiscardLocal")
    .addEventListener("click", () => {
      dirtyDemoDirty = false;
      dirtyDialog.close();
    });
  anchoredTrigger.addEventListener("click", () => {
    anchoredBoundary.classList.add("is-demo-pinned");
    anchoredPopover.toggle();
    markAfterLayout(() => {
      const panel = anchoredPanel.getBoundingClientRect();
      const trigger = anchoredTrigger.getBoundingClientRect();
      const boundary = anchoredBoundary.getBoundingClientRect();
      const clamped = (
        panel.left >= boundary.left + 7
        && panel.right <= boundary.right - 7
        && panel.bottom <= trigger.top
      );
      anchoredPanel.dataset.demoPositioned = clamped ? "true" : "false";
    });
  });
  document.getElementById("componentFloatingOpen").addEventListener("click", () => {
    floatingPopover.open({ x: 3, y: 3 });
    markAfterLayout(() => {
      const bounds = floatingPanel.getBoundingClientRect();
      const clamped = (
        bounds.left >= 8
        && bounds.top >= 8
        && bounds.right <= document.documentElement.clientWidth - 8
        && bounds.bottom <= document.documentElement.clientHeight - 8
      );
      floatingPanel.dataset.demoPositioned = clamped ? "true" : "false";
    });
  });
  document.getElementById("componentConfirmOpen").addEventListener("click", event => {
    const trigger = event.currentTarget;
    PortalUI.confirm({
      title: window.PortalI18n.t("删除演示对象？"),
      message: window.PortalI18n.t("这里只验证确认 Surface，不会执行删除。"),
      description: window.PortalI18n.t("Escape 会返回 false，并把焦点还给刚才的按钮。"),
      confirmLabel: window.PortalI18n.t("删除"),
      tone: "danger",
    }).then(result => {
      feedbackResult.textContent = window.PortalI18n.text`confirm:${String(result)}`;
      trigger.focus({ preventScroll: true });
    });
  });
  document.getElementById("componentPromptOpen").addEventListener("click", event => {
    const trigger = event.currentTarget;
    PortalUI.prompt({
      title: window.PortalI18n.t("重命名演示对象"),
      label: window.PortalI18n.t("名称"),
      value: "before",
      confirmLabel: "保存",
    }).then(result => {
      feedbackResult.textContent = result === null
        ? "prompt:cancelled"
        : window.PortalI18n.text`prompt:${result}`;
      trigger.focus({ preventScroll: true });
    });
  });
  document.getElementById("componentToast").addEventListener("click", () => {
    PortalUI.toast(window.PortalI18n.t("第一次状态"), "info", { key: "component-demo", duration: 12000 });
    PortalUI.toast(window.PortalI18n.t("同一 key 已更新，没有叠加重复反馈"), "success", {
      key: "component-demo",
      duration: 12000,
    });
  });
  document.getElementById("componentErrorToast").addEventListener("click", () => {
    PortalUI.toast(window.PortalI18n.t("Review failed"), "error", {
      key: "component-demo-error",
      duration: 12000,
    });
  });
  document.getElementById("componentToolbarLiveToggle").addEventListener("click", event => {
    const active = event.currentTarget.getAttribute("aria-pressed") !== "true";
    event.currentTarget.setAttribute("aria-pressed", active ? "true" : "false");
    document.getElementById("componentToolbarLiveStatus").textContent = (
      active ? "live:on" : "live:off"
    );
  });
  let keyboardSize = 160;
  let keyboardStored = null;
  const keyboardWorkbench = document.getElementById("componentKeyboardWorkbench");
  const keyboardStatus = document.getElementById("componentKeyboardStatus");
  const syncKeyboardStatus = () => {
    keyboardWorkbench.style.setProperty("--demo-pane-size", `${keyboardSize}px`);
    keyboardStatus.textContent = window.PortalI18n.text`size:${keyboardSize} · stored:${keyboardStored ?? "—"}`;
    keyboardStatus.dataset.demoSize = String(keyboardSize);
    keyboardStatus.dataset.demoStored = keyboardStored === null
      ? ""
      : String(keyboardStored);
  };
  PortalWorkbench.layout.bindSplitter({
    handle: "#componentKeyboardSplitter",
    min: 100,
    max: 260,
    keyboardStep: 20,
    readSize: () => keyboardSize,
    applySize: size => {
      keyboardSize = Math.round(size);
      syncKeyboardStatus();
    },
    store: {
      write: patch => {
        keyboardStored = Number(patch.paneWidth);
        syncKeyboardStatus();
      },
    },
    stateKey: "paneWidth",
  });
  syncKeyboardStatus();

  const semanticSearch = PortalSemanticSearch.create({
    input: "componentSemanticInput",
    button: "componentSemanticButton",
    panel: "componentSemanticPanel",
    client: {
      get: async () => ({
        searches: [{
          search_id: "search_0123456789ab",
          query: "lineage",
          status: "done",
          result_count: 1,
        }],
      }),
      post: async () => ({}),
    },
    urls: { list: "/fixture/searches" },
  });
  const semanticPanel = document.getElementById("componentSemanticPanel");
  const semanticStatus = document.getElementById("componentSemanticStatus");
  semanticSearch.load().then(() => {
    const initialTrigger = semanticPanel.querySelector(
      "[data-semantic-owner-menu]",
    );
    if (!initialTrigger) throw new Error("Semantic owner trigger was not rendered");
    initialTrigger.dataset.demoTriggerGeneration = "initial";
    const observer = new MutationObserver(() => {
      const replacement = semanticPanel.querySelector(
        "[data-semantic-owner-menu]",
      );
      if (!replacement || replacement === initialTrigger) return;
      replacement.dataset.demoTriggerGeneration = "replacement";
      semanticStatus.textContent = window.PortalI18n.t("trigger:replaced · previous:detached");
      semanticStatus.dataset.demoTriggerReplaced = String(
        !initialTrigger.isConnected,
      );
    });
    observer.observe(semanticPanel, { childList: true, subtree: true });
  }).catch(error => {
    semanticPanel.textContent = window.PortalI18n.text`semantic error: ${String(
      error?.message || error,
    )}`;
  });

  const resourceCopyStatus = document.getElementById("componentResourceCopyStatus");
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: {
      writeText: async value => {
        resourceCopyStatus.textContent = `copied:${String(value)}`;
        resourceCopyStatus.dataset.demoCopied = String(value);
      },
    },
  });
  PortalResourceHeader.mount("componentResourceIdentity", {
    title: window.PortalI18n.t("Architecture quality"),
    type: "insight",
    resource_id: "insight_ee85097d560b",
  });

  document.getElementById("componentImagePreviewOpen").addEventListener("click", () => {
    const svg = window.PortalI18n.html`<svg data-portal-visualization="image-preview-fixture" xmlns="http://www.w3.org/2000/svg" width="1920" height="1080" viewBox="0 0 1920 1080">
          <rect width="1920" height="1080" fill="#e7eef2"/>
          <rect x="80" y="80" width="1760" height="920" rx="48" fill="#f8fafc" stroke="#8abbb5" stroke-width="12"/>
          <text x="960" y="540" text-anchor="middle" dominant-baseline="middle" fill="#173a3a" font-size="96" font-family="sans-serif">Preview</text>
        </svg>`;
    PortalChatWorkspace.openImagePreview(
      `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`,
      "/workspace/results/lineage-overview.png",
    );
    const image = document.querySelector(".portalChatImagePreviewBody img");
    const markPreview = () => markAfterLayout(() => {
      const panel = document.querySelector(".portalChatImagePreviewPanel");
      const body = document.querySelector(".portalChatImagePreviewBody");
      if (!panel || !body || !image) return;
      const panelBounds = panel.getBoundingClientRect();
      const bodyBounds = body.getBoundingClientRect();
      const imageBounds = image.getBoundingClientRect();
      const contained = (
        panelBounds.width <= document.documentElement.clientWidth + 1
        && panelBounds.height <= document.documentElement.clientHeight + 1
        && imageBounds.width <= bodyBounds.width + 1
        && imageBounds.height <= bodyBounds.height + 1
      );
      const status = document.getElementById("componentImagePreviewStatus");
      status.textContent = `${contained ? "preview:contained" : "preview:overflow"} · panel:${Math.round(
        panelBounds.width,
      )} · image:${Math.round(imageBounds.width)}`;
      status.dataset.demoContained = contained ? "true" : "false";
      status.dataset.demoPanelWidth = String(Math.round(panelBounds.width));
      status.dataset.demoImageWidth = String(Math.round(imageBounds.width));
      status.dataset.demoViewportWidth = String(
        document.documentElement.clientWidth,
      );
      image.dataset.demoNaturalWidth = String(image.naturalWidth);
    });
    if (image?.complete) markPreview();
    else image?.addEventListener("load", markPreview, { once: true });
  });

  document.getElementById("componentGuardedOpen").addEventListener("click", () => {
    guardedDirty = true;
    guardedConfirmCount = 0;
    guardedStatus.textContent = "guarded:open";
    guardedDialog.open();
  });
  document.getElementById("componentGuardedClose")
    .addEventListener("click", () => guardedDialog.close());
  document.getElementById("componentGuardedAllowClose").addEventListener("click", () => {
    guardedDirty = false;
    guardedDialog.close();
  });
  document.getElementById("componentNativeOpen").addEventListener("click", () => {
    nativeDirty = true;
    nativeConfirmCount = 0;
    nativeStatus.textContent = "native:open";
    nativeDialog.open();
  });
  document.getElementById("componentNativeMarkClean").addEventListener("click", () => {
    nativeDirty = false;
    nativeStatus.textContent = window.PortalI18n.text`native:clean · confirms:${nativeConfirmCount}`;
  });

  document.getElementById("componentRetryToastOpen").addEventListener("click", () => {
    PortalUI.toast(window.PortalI18n.t("保存失败"), "error", {
      duration: 0,
      key: "component-demo-retry-toast",
      retry: () => {
        const status = document.getElementById("componentRetryToastStatus");
        status.textContent = "retried:1";
        status.dataset.demoRetried = "1";
      },
    });
  });

  document.getElementById("componentMutationDedupe").addEventListener("click", async event => {
    const button = event.currentTarget;
    const status = document.getElementById("componentMutationDedupeStatus");
    let calls = 0;
    let finish;
    const run = () => PortalUI.runMutation({
      key: "component-demo-save-one",
      button,
      busyLabel: "保存中…",
      run: () => new Promise(resolve => {
        calls += 1;
        finish = resolve;
      }),
    });
    const first = run();
    const second = run();
    status.textContent = window.PortalI18n.text`calls:${calls} · busy`;
    status.dataset.demoCalls = String(calls);
    setTimeout(() => finish({ ok: true }), 80);
    await Promise.all([first, second]);
    const restoredText = String(button.textContent || "").trim();
    const restoredDisabled = button.disabled;
    const restoredBusy = button.getAttribute("aria-busy");
    const restored = (
      restoredText === "保存"
      && restoredDisabled === false
      && restoredBusy === null
    );
    status.textContent = window.PortalI18n.text`calls:${calls} · idle · text:${restoredText} · disabled:${restoredDisabled} · aria-busy:${restoredBusy ?? "none"}`;
    status.dataset.demoCalls = String(calls);
    status.dataset.demoIdle = "true";
    status.dataset.demoRestored = String(restored);
  });

  let mutationRetryCalls = 0;
  document.getElementById("componentMutationRetryStart").addEventListener("click", async () => {
    const target = document.getElementById("componentMutationRetryTarget");
    const status = document.getElementById("componentMutationRetryStatus");
    await PortalUI.runMutation({
      key: "component-demo-publish-one",
      button: target,
      busyLabel: "发布中…",
      run: async () => {
        mutationRetryCalls += 1;
        status.textContent = window.PortalI18n.text`calls:${mutationRetryCalls} · disabled:${target.disabled}`;
        status.dataset.demoCalls = String(mutationRetryCalls);
        if (mutationRetryCalls === 1) throw new Error("临时失败");
        return { ok: true };
      },
      successMessage: "发布成功",
    });
    status.textContent = window.PortalI18n.text`calls:${mutationRetryCalls} · disabled:${target.disabled}`;
    status.dataset.demoCalls = String(mutationRetryCalls);
    status.dataset.demoDisabled = String(target.disabled);
  });
  const markAsyncFits = () => markAfterLayout(() => {
    const host = document.getElementById("componentAsyncPanel");
    const content = host.firstElementChild;
    host.dataset.demoFits = (
      content
      && content.scrollWidth <= host.clientWidth
      && host.scrollWidth <= host.clientWidth
    ) ? "true" : "false";
  });
  const renderAsyncReady = () => {
    PortalUIPrimitives.renderAsyncState("componentAsyncPanel", {
      state: "success",
      title: window.PortalI18n.t("Panel ready"),
      message: window.PortalI18n.t("重试完成；很长的状态说明仍然限制在这个窄容器中。"),
    });
    PortalUIPrimitives.renderAsyncState("componentAsyncCompact", {
      state: "empty",
      density: "compact",
      title: window.PortalI18n.t("Compact empty"),
      message: window.PortalI18n.t("没有匹配记录。"),
    });
    PortalUIPrimitives.renderAsyncState("componentAsyncInline", {
      state: "loading",
      density: "inline",
      title: window.PortalI18n.t("Inline loading"),
      message: window.PortalI18n.t("正在同步。"),
    });
    markAsyncFits();
  };
  document.getElementById("componentAsyncError").addEventListener("click", () => {
    PortalUIPrimitives.renderAsyncState("componentAsyncPanel", {
      state: "error",
      title: window.PortalI18n.t("读取失败"),
      message: window.PortalI18n.t("保留当前页面，并允许原地重试；这段很长的错误说明不会溢出窄容器。"),
      onRetry: renderAsyncReady,
    });
    markAsyncFits();
  });
  renderAsyncReady();
  PortalFormControls.bindFilterDropdown({
    button: "componentFilterButton",
    panel: "componentFilterPanel",
  });
  PortalToolbar.mount(document.getElementById("componentToolbar"));
  const mountDynamicControls = () => setTimeout(() => {
    const input = document.createElement("input");
    input.id = "componentSearch";
    input.type = "search";
    input.dataset.clearableInput = "1";
    input.placeholder = window.PortalI18n.t("输入测试关键词…");
    document.getElementById("componentDynamicSearchHost").appendChild(input);

    const tooltipAction = document.createElement("button");
    tooltipAction.className = "portalButton is-compact";
    tooltipAction.id = "componentTooltipAction";
    tooltipAction.dataset.action = "delete";
    tooltipAction.type = "button";
    tooltipAction.textContent = window.PortalI18n.t("删除");
    document.getElementById("componentDynamicTooltipHost").appendChild(
      tooltipAction,
    );
  }, 0);
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mountDynamicControls, {
      once: true,
    });
  } else {
    mountDynamicControls();
  }
})();
