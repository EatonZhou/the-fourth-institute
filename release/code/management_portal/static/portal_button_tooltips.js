(function (global) {
  "use strict";

  if (global.PortalButtonTooltips) return;

  const TOOLTIP_ID = "portalButtonTooltip";
  const TRIGGER_SELECTOR = "button, a.portalButton, [data-portal-tooltip]";
  const HOVER_DELAY_MS = 360;
  const VIEWPORT_MARGIN = 10;
  const TARGET_GAP = 9;

  const ACTION_TOOLTIPS = Object.freeze({
    action: {
      "apply-artifact": window.PortalI18n.t("将此产物应用到目标文件"),
      approve: window.PortalI18n.t("批准当前条目"),
      archive: window.PortalI18n.t("归档当前条目并从待处理列表移除"),
      delete: window.PortalI18n.t("删除当前条目"),
      "preview-artifact": window.PortalI18n.t("预览此产物的内容"),
      "undo-artifact": window.PortalI18n.t("撤销此前对目标文件的应用"),
    },
    selectionAction: {
      explain: window.PortalI18n.t("解释所选文本"),
      "find-ref": window.PortalI18n.t("查找所选文本的参考来源"),
      "regist-ref": window.PortalI18n.t("将所选文本注册为参考条目"),
      translate: window.PortalI18n.t("翻译所选文本"),
    },
    experimentFloatingAction: {
      "copy-id": window.PortalI18n.t("复制实验 ID"),
      "copy-path": window.PortalI18n.t("复制实验工作目录路径"),
    },
    forkCreateAction: {
      native: window.PortalI18n.t("从 PostgreSQL 继承当前有效上下文，创建保留完整历史轨迹的普通 Resident"),
      "recent-5": window.PortalI18n.t("以最近 5 轮对话为上下文执行 Fork"),
      "recent-10": window.PortalI18n.t("以最近 10 轮对话为上下文执行 Fork"),
      "compacted": window.PortalI18n.t("继承当前有效上下文，并在新 Session 首次工作前完成压缩"),
    },
  });

  const ID_TOOLTIPS = Object.freeze({
    btnFilter: window.PortalI18n.t("打开筛选条件"),
    btnRefresh: window.PortalI18n.t("重新加载当前页面内容"),
    ccfYearFilterClearBtn: window.PortalI18n.t("清除 CCF 年份筛选"),
    collectorBtn: window.PortalI18n.t("根据输入条件收集论文"),
    filterBtn: window.PortalI18n.t("打开筛选条件"),
    semanticSearchBtn: window.PortalI18n.t("根据输入内容发起论文语义检索"),
    reload: window.PortalI18n.t("重新加载当前页面内容"),
    semanticBtn: window.PortalI18n.t("根据输入内容发起语义检索"),
  });

  const LABEL_TOOLTIPS = Object.freeze({
    "add": window.PortalI18n.t("添加新的条目"),
    "add candidate child": window.PortalI18n.t("为当前候选节点添加子节点"),
    "apply": window.PortalI18n.t("应用当前变更"),
    "approve": window.PortalI18n.t("批准当前条目"),
    "archive": window.PortalI18n.t("归档当前条目"),
    "attach": window.PortalI18n.t("添加文件、图片或其他附件"),
    "auto": window.PortalI18n.t("启用自动继续模式"),
    "cancel": window.PortalI18n.t("取消当前操作"),
    "ccf a/b": window.PortalI18n.t("切换到 CCF A/B 论文目录"),
    "clear": window.PortalI18n.t("清除当前输入或筛选"),
    "close": window.PortalI18n.t("关闭当前面板"),
    "collect": window.PortalI18n.t("根据输入条件收集论文"),
    "confirm candidate": window.PortalI18n.t("确认当前候选节点"),
    "copy": window.PortalI18n.t("复制当前内容"),
    "ctrl-c": window.PortalI18n.t("向终端发送中断信号"),
    "create": window.PortalI18n.t("创建新的条目"),
    "delete": window.PortalI18n.t("删除当前条目"),
    "detail": window.PortalI18n.t("切换到详情视图"),
    "删除": window.PortalI18n.t("删除当前条目"),
    "取消": window.PortalI18n.t("取消当前操作"),
    "关闭": window.PortalI18n.t("关闭当前面板"),
    "保存": window.PortalI18n.t("保存当前修改"),
    "刷新": window.PortalI18n.t("重新加载当前内容"),
    "发送": window.PortalI18n.t("发送当前消息"),
    "edit": window.PortalI18n.t("编辑当前条目"),
    "exp": window.PortalI18n.t("切换到论文实验分析视图"),
    "expand": window.PortalI18n.t("展开当前内容"),
    "feedback": window.PortalI18n.t("打开反馈输入框"),
    "figure": window.PortalI18n.t("切换到论文图表视图"),
    "filter": window.PortalI18n.t("打开筛选条件"),
    "forward": window.PortalI18n.t("转发所选消息到其他 Agent"),
    "library": window.PortalI18n.t("切换到论文库视图"),
    "more": window.PortalI18n.t("打开更多操作"),
    "new": window.PortalI18n.t("创建新的条目"),
    "notes": window.PortalI18n.t("切换到论文笔记视图"),
    "open": window.PortalI18n.t("打开当前条目"),
    "publish": window.PortalI18n.t("发布当前内容"),
    "preview": window.PortalI18n.t("打开预览视图"),
    "refresh": window.PortalI18n.t("重新加载当前内容"),
    "register": window.PortalI18n.t("注册当前条目"),
    "rename": window.PortalI18n.t("重命名当前条目"),
    "resume": window.PortalI18n.t("继续当前任务"),
    "resident menu": window.PortalI18n.t("打开 Resident 操作菜单"),
    "resume branch": window.PortalI18n.t("继续执行当前探索分支"),
    "reader": window.PortalI18n.t("切换到论文阅读视图"),
    "refs": window.PortalI18n.t("切换到论文参考文献视图"),
    "retract": window.PortalI18n.t("撤回当前已发布内容"),
    "review": window.PortalI18n.t("切换到论文 Review 视图"),
    "run": window.PortalI18n.t("执行当前任务"),
    "save": window.PortalI18n.t("保存当前修改"),
    "semantic": window.PortalI18n.t("根据输入内容发起语义检索"),
    "send": window.PortalI18n.t("发送当前消息"),
    "search": window.PortalI18n.t("打开搜索输入"),
    "select all": window.PortalI18n.t("选择当前列表中的全部条目"),
    "show archived": window.PortalI18n.t("显示已归档条目"),
    "start auto": window.PortalI18n.t("开始自动执行当前实验"),
    "stop": window.PortalI18n.t("停止当前任务"),
    "stop auto": window.PortalI18n.t("停止当前实验的自动执行"),
    "stop branch": window.PortalI18n.t("停止当前探索分支"),
    "submit": window.PortalI18n.t("提交当前内容"),
    "today": window.PortalI18n.t("切换到今日论文视图"),
    "terminal": window.PortalI18n.t("打开终端视图"),
    "undo": window.PortalI18n.t("撤销上一次操作"),
    "搜索": window.PortalI18n.t("打开搜索输入"),
    "筛选": window.PortalI18n.t("打开筛选条件"),
  });

  function knownButtonTextTooltip(value) {
    const label = String(value || "").replace(/\s+/g, " ").trim().toLowerCase();
    return LABEL_TOOLTIPS[label] || "";
  }

  function semanticButtonTooltip(button) {
    const fields = [
      ["action", "action"],
      ["selectionAction", "selectionAction"],
      ["experimentFloatingAction", "experimentFloatingAction"],
      ["forkCreateAction", "forkCreateAction"],
    ];
    for (const [datasetKey, tooltipGroup] of fields) {
      const value = String(button.dataset[datasetKey] || "").trim();
      const tooltip = ACTION_TOOLTIPS[tooltipGroup]?.[value];
      if (tooltip) return tooltip;
    }
    return "";
  }

  function buttonLabelTooltip(button) {
    if (button.dataset.pulseRange) return window.PortalI18n.text`查看 ${String(button.dataset.pulseRange).trim()} 时间范围的动态`;
    if (button.dataset.agentStatusRange) return window.PortalI18n.text`查看 ${String(button.dataset.agentStatusRange).trim()} 的 Token 使用统计`;
    const labelTooltip = knownButtonTextTooltip(button.textContent);
    if (labelTooltip) return labelTooltip;

    if (button.dataset.mode) return window.PortalI18n.text`切换到 ${String(button.dataset.mode).trim()} 视图`;
    if (button.dataset.leftFace) return window.PortalI18n.text`切换到 ${String(button.dataset.leftFace).trim()} 视图`;
    if (button.dataset.knowledgeView) return window.PortalI18n.text`切换到 ${String(button.dataset.knowledgeView).trim()} 视图`;
    if (button.hasAttribute("data-agent-gpu-toggle")) return window.PortalI18n.t("展开或收起 GPU 使用情况");
    if (button.hasAttribute("data-agent-disk-toggle")) return window.PortalI18n.t("展开或收起磁盘使用情况");
    return "";
  }

  function buttonDataAttributeTooltip(button) {
    if (button.hasAttribute("data-fork-source-resident-id")) {
      return window.PortalI18n.t("对该来源 Resident 执行 Fork，并把新 Resident 加入对应 Lineage 的 HATG");
    }

    const exact = [
      ["data-paper-id", window.PortalI18n.t("打开该论文")],
      ["data-semantic-select", window.PortalI18n.t("查看此语义检索结果")],
      ["data-collector-select", window.PortalI18n.t("查看此论文采集结果")],
      ["data-collector-delete", window.PortalI18n.t("删除此论文采集记录")],
      ["data-map-select", window.PortalI18n.t("查看此知识条目")],
      ["data-map-open-detail", window.PortalI18n.t("打开此知识条目的详情")],
      ["data-archive-job", window.PortalI18n.t("查看此归档任务")],
      ["data-chatroom-select", window.PortalI18n.t("打开此聊天室")],
      ["data-resident-select", window.PortalI18n.t("打开此 Resident")],
      ["data-resident-member-select", window.PortalI18n.t("打开此 Resident")],
      ["data-archive-remove-attachment", window.PortalI18n.t("移除此归档附件")],
      ["data-remove-attachment", window.PortalI18n.t("移除当前附件")],
      ["data-portal-message-copy", window.PortalI18n.t("复制此消息内容")],
      ["data-workspace-queue-delete", window.PortalI18n.t("从发送队列移除此消息")],
      ["data-workspace-queue-inject", window.PortalI18n.t("立即注入当前执行")],
      ["data-pulse-publish", window.PortalI18n.t("发布此进展摘要")],
      ["data-pulse-unpublish", window.PortalI18n.t("撤回此已发布进展摘要")],
      ["data-agent-status-refresh", window.PortalI18n.t("刷新 Agent 状态和 Token 使用统计")],
      ["data-agent-status-range", window.PortalI18n.t("切换 Token 使用统计时间范围")],
      ["data-ccf-venue", window.PortalI18n.t("展开或收起此 CCF 会议")],
      ["data-ccf-year", window.PortalI18n.t("查看此 CCF 年份或期次")],
      ["data-ccf-category", window.PortalI18n.t("展开或收起此 CCF 分类")],
      ["data-ccf-rank", window.PortalI18n.t("展开或收起此 CCF 等级")],
      ["data-ccf-fetch-years", window.PortalI18n.t("获取此 CCF 会议的年份和期次列表")],
      ["data-fetch-ccf-papers", window.PortalI18n.t("获取此 CCF 条目的 DBLP 论文列表")],
      ["data-register-ccf-paper", window.PortalI18n.t("将此 CCF 论文注册到论文库")],
      ["data-ccf-web-enrich", window.PortalI18n.t("从网页补充此 CCF 条目的信息")],
      ["data-select-figure", window.PortalI18n.t("查看此论文图表")],
      ["data-explain-figure", window.PortalI18n.t("生成此图表的 AI 解读")],
      ["data-generate-figure", window.PortalI18n.t("生成此图表的示意图")],
      ["data-generate-figure-variant", window.PortalI18n.t("基于当前图表生成新的变体")],
      ["data-register-ref", window.PortalI18n.t("将此参考文献注册到论文库")],
    ];
    for (const [attribute, tooltip] of exact) {
      if (button.hasAttribute(attribute)) return tooltip;
    }

    const attributeNames = button.getAttributeNames().filter(
      name => !name.startsWith("data-portal-tooltip"),
    );
    if (attributeNames.some(name => /^data-.*-toggle$/.test(name))) return window.PortalI18n.t("展开或收起此区域");
    if (attributeNames.some(name => /^data-.*-select$/.test(name))) return window.PortalI18n.t("选择此条目");
    if (attributeNames.some(name => /^data-.*-(remove|delete)$/.test(name))) return window.PortalI18n.t("删除此条目");
    if (attributeNames.some(name => /^data-.*-open$/.test(name))) return window.PortalI18n.t("打开此内容");
    const dataNames = attributeNames.join(" ").toLowerCase();
    if (dataNames.includes("filter")) return window.PortalI18n.t("按此条件筛选内容");
    if (/(?:face|mode|tab|view)/.test(dataNames)) return window.PortalI18n.t("切换当前视图");
    if (dataNames.includes("status")) return window.PortalI18n.t("查看或更新当前状态");
    if (/(?:run|start)/.test(dataNames)) return window.PortalI18n.t("运行当前任务");
    if (/(?:stop|interrupt)/.test(dataNames)) return window.PortalI18n.t("停止当前任务");
    if (/(?:attach|upload)/.test(dataNames)) return window.PortalI18n.t("添加文件、图片或其他附件");
    if (dataNames.includes("archive")) return window.PortalI18n.t("归档当前条目");
    if (dataNames.includes("approve")) return window.PortalI18n.t("批准当前条目");
    if (dataNames.includes("reject")) return window.PortalI18n.t("拒绝当前条目");
    if (dataNames.includes("send")) return window.PortalI18n.t("发送当前内容");
    if (dataNames.includes("refs")) return window.PortalI18n.t("选择或管理引用内容");
    if (attributeNames.some(name => name.includes("copy"))) return window.PortalI18n.t("复制当前内容");
    if (attributeNames.some(name => name.includes("fork"))) return window.PortalI18n.t("执行 Fork 操作");
    if (attributeNames.some(name => name.includes("refresh"))) return window.PortalI18n.t("重新加载当前内容");
    if (attributeNames.some(name => name.includes("generate"))) return window.PortalI18n.t("生成当前内容");
    if (attributeNames.some(name => name.includes("save"))) return window.PortalI18n.t("保存当前修改");
    if (attributeNames.some(name => name.includes("edit"))) return window.PortalI18n.t("编辑当前条目");
    return "";
  }

  function inferredTooltipFor(button) {
    const semantic = semanticButtonTooltip(button);
    if (semantic) return semantic;
    const idTooltip = ID_TOOLTIPS[String(button.id || "").trim()];
    if (idTooltip) return idTooltip;
    const labelTooltip = buttonLabelTooltip(button);
    if (labelTooltip) return labelTooltip;
    const ariaLabel = String(button.getAttribute("aria-label") || "").trim();
    const ariaTooltip = knownButtonTextTooltip(ariaLabel);
    if (ariaTooltip) return ariaTooltip;
    if (ariaLabel) return ariaLabel;
    const dataAttributeTooltip = buttonDataAttributeTooltip(button);
    if (dataAttributeTooltip) return dataAttributeTooltip;
    return "";
  }

  function clean(value) {
    return String(value || "").replace(/\s+/g, " ").trim();
  }

  function clearGeneratedTooltip(button) {
    if (button.dataset.portalTooltipSource !== "generated") return;
    delete button.dataset.portalTooltip;
    delete button.dataset.portalTooltipSource;
    delete button.dataset.portalGeneratedTooltip;
  }

  function enhanceButton(button) {
    if (!(button instanceof Element) || !button.matches(TRIGGER_SELECTOR)) return;

    if (button.dataset.portalTooltipOptOut === "1") {
      button.removeAttribute("title");
      delete button.dataset.portalTooltip;
      delete button.dataset.portalTooltipSource;
      delete button.dataset.portalGeneratedTooltip;
      return;
    }

    if (button.hasAttribute("title")) {
      const title = clean(button.getAttribute("title"));
      button.removeAttribute("title");
      if (title) {
        button.dataset.portalTooltip = title;
        button.dataset.portalTooltipSource = "title";
        delete button.dataset.portalGeneratedTooltip;
      } else if (button.dataset.portalTooltipSource === "title") {
        delete button.dataset.portalTooltip;
        delete button.dataset.portalTooltipSource;
      }
      return;
    }

    const existing = clean(button.dataset.portalTooltip);
    if (existing && button.dataset.portalTooltipSource !== "generated") {
      if (!button.dataset.portalTooltipSource) button.dataset.portalTooltipSource = "explicit";
      delete button.dataset.portalGeneratedTooltip;
      return;
    }

    const label = inferredTooltipFor(button);
    if (!label) {
      clearGeneratedTooltip(button);
      return;
    }
    button.dataset.portalTooltip = label;
    button.dataset.portalTooltipSource = "generated";
    button.dataset.portalGeneratedTooltip = "1";
  }

  function collectButtons(root, buttons) {
    if (root instanceof Element && root.matches(TRIGGER_SELECTOR)) buttons.add(root);
    root?.querySelectorAll?.(TRIGGER_SELECTOR).forEach(button => buttons.add(button));
  }

  function enhance(root = document) {
    const buttons = new Set();
    collectButtons(root, buttons);
    buttons.forEach(enhanceButton);
  }

  let surface = null;
  let activeButton = null;
  let showTimer = 0;
  let positionFrame = 0;
  let focusModality = "keyboard";

  function ensureSurface() {
    if (surface?.isConnected) return surface;
    surface = document.getElementById(TOOLTIP_ID);
    if (surface) return surface;
    surface = document.createElement("div");
    surface.id = TOOLTIP_ID;
    surface.className = "portalButtonTooltip";
    surface.dataset.portalTooltipSurface = "";
    surface.dataset.portalOverlayLayer = "popover";
    surface.dataset.open = "false";
    surface.setAttribute("role", "tooltip");
    surface.hidden = true;
    (document.body || document.documentElement).appendChild(surface);
    return surface;
  }

  function setDescribedBy(button, described) {
    if (!button) return;
    const ids = new Set(clean(button.getAttribute("aria-describedby")).split(/\s+/).filter(Boolean));
    if (described) ids.add(TOOLTIP_ID);
    else ids.delete(TOOLTIP_ID);
    if (ids.size) button.setAttribute("aria-describedby", [...ids].join(" "));
    else button.removeAttribute("aria-describedby");
  }

  function overlayLayerFor(button) {
    const rank = { popover: 1, dialog: 2, toast: 3, critical: 4 };
    let layer = "popover";
    for (let element = button; element instanceof Element; element = element.parentElement) {
      const candidate = clean(element.dataset.portalOverlayLayer);
      if ((rank[candidate] || 0) > rank[layer]) layer = candidate;
    }
    return layer;
  }

  function clamp(value, minimum, maximum) {
    return Math.min(Math.max(value, minimum), maximum);
  }

  function position() {
    positionFrame = 0;
    const tooltip = ensureSurface();
    if (!activeButton?.isConnected || tooltip.hidden) return hide();
    const targetBounds = activeButton.getBoundingClientRect();
    if (!targetBounds.width && !targetBounds.height) return hide();

    const tooltipBounds = tooltip.getBoundingClientRect();
    const viewportWidth = document.documentElement.clientWidth;
    const viewportHeight = document.documentElement.clientHeight;
    const preferred = clean(activeButton.dataset.portalTooltipPlacement).toLowerCase();
    const canFitTop = targetBounds.top - TARGET_GAP - tooltipBounds.height >= VIEWPORT_MARGIN;
    const canFitBottom = targetBounds.bottom + TARGET_GAP + tooltipBounds.height <= viewportHeight - VIEWPORT_MARGIN;
    let placement = preferred === "bottom" ? "bottom" : "top";
    if (placement === "top" && !canFitTop && canFitBottom) placement = "bottom";
    if (placement === "bottom" && !canFitBottom && canFitTop) placement = "top";

    let top = placement === "top"
      ? targetBounds.top - tooltipBounds.height - TARGET_GAP
      : targetBounds.bottom + TARGET_GAP;
    let left = targetBounds.left + (targetBounds.width - tooltipBounds.width) / 2;
    top = clamp(top, VIEWPORT_MARGIN, Math.max(VIEWPORT_MARGIN, viewportHeight - tooltipBounds.height - VIEWPORT_MARGIN));
    left = clamp(left, VIEWPORT_MARGIN, Math.max(VIEWPORT_MARGIN, viewportWidth - tooltipBounds.width - VIEWPORT_MARGIN));

    const targetCenter = targetBounds.left + targetBounds.width / 2;
    const arrowX = clamp(targetCenter - left, 12, Math.max(12, tooltipBounds.width - 12));
    tooltip.style.left = `${Math.round(left)}px`;
    tooltip.style.top = `${Math.round(top)}px`;
    tooltip.style.setProperty("--portal-tooltip-arrow-x", `${Math.round(arrowX)}px`);
    tooltip.dataset.placement = placement;
    tooltip.dataset.portalOverlayLayer = overlayLayerFor(activeButton);
    tooltip.style.setProperty("--portal-overlay-order", "90");
    tooltip.dataset.open = "true";
  }

  function queuePosition() {
    if (!activeButton || !surface || surface.hidden || positionFrame) return;
    positionFrame = global.requestAnimationFrame(position);
  }

  function hide() {
    if (showTimer) global.clearTimeout(showTimer);
    if (positionFrame) global.cancelAnimationFrame(positionFrame);
    showTimer = 0;
    positionFrame = 0;
    setDescribedBy(activeButton, false);
    activeButton = null;
    const tooltip = ensureSurface();
    tooltip.dataset.open = "false";
    tooltip.hidden = true;
  }

  function open(button) {
    if (activeButton !== button || !button?.isConnected) return;
    enhanceButton(button);
    const message = clean(button.dataset.portalTooltip);
    if (!message) return hide();
    const tooltip = ensureSurface();
    if (tooltip.textContent !== message) tooltip.textContent = message;
    tooltip.dataset.open = "false";
    tooltip.hidden = false;
    setDescribedBy(button, true);
    queuePosition();
  }

  function show(button, options = {}) {
    if (!(button instanceof Element)) return hide();
    enhanceButton(button);
    if (!clean(button.dataset.portalTooltip)) return hide();
    if (activeButton !== button) setDescribedBy(activeButton, false);
    activeButton = button;
    if (showTimer) global.clearTimeout(showTimer);
    showTimer = global.setTimeout(() => {
      showTimer = 0;
      open(button);
    }, options.immediate ? 0 : HOVER_DELAY_MS);
  }

  function triggerFor(target) {
    if (!(target instanceof Element)) return null;
    const button = target.closest(TRIGGER_SELECTOR);
    if (!button) return null;
    enhanceButton(button);
    return clean(button.dataset.portalTooltip) ? button : null;
  }

  function bindInteractions() {
    document.addEventListener("pointerover", event => {
      if (event.pointerType === "touch") return;
      const button = triggerFor(event.target);
      if (!button || button === activeButton) return;
      show(button);
    }, true);
    document.addEventListener("pointerout", event => {
      const button = triggerFor(event.target);
      if (!button || button !== activeButton) return;
      if (triggerFor(event.relatedTarget) === button) return;
      hide();
    }, true);
    document.addEventListener("focusin", event => {
      const button = triggerFor(event.target);
      if (button && focusModality !== "pointer") show(button, { immediate: true });
    });
    document.addEventListener("focusout", event => {
      const button = triggerFor(event.target);
      if (!button || button !== activeButton) return;
      if (triggerFor(event.relatedTarget) === button) return;
      hide();
    });
    document.addEventListener("pointerdown", () => {
      focusModality = "pointer";
      hide();
    }, true);
    document.addEventListener("keydown", event => {
      focusModality = "keyboard";
      if (event.key === "Escape") hide();
    }, true);
    document.addEventListener("scroll", queuePosition, true);
    global.addEventListener("resize", queuePosition);
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) hide();
    });
  }

  function initialize() {
    const start = () => {
      ensureSurface();
      bindInteractions();
      global.PortalUIPrimitives.registerEnhancer({
        id: "button-tooltips", selector: TRIGGER_SELECTOR,
        attributes: ["title", "aria-label"], content: true,
        enhance(button) {
          enhanceButton(button);
          if (button === activeButton) {
            const message = clean(button.dataset.portalTooltip);
            if (!message) hide();
            else if (surface && !surface.hidden) {
              if (surface.textContent !== message) surface.textContent = message;
              queuePosition();
            }
          }
        },
        remove(button) { if (button === activeButton) hide(); },
      });
    };
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, { once: true });
    else start();
  }

  global.PortalButtonTooltips = Object.freeze({
    enhance,
    hide,
    show,
  });
  initialize();
})(window);
