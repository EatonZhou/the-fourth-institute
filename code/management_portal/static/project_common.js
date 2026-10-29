(function () {
  "use strict";

  // What the Project list and the Project page share: labels, deadlines,
  // relative times, avatars and the pages a Project's resources open in
  // (decision_46c37026e8ea).
  if (window.PortalProjectCommon) return;

  const esc = value => window.PortalUIPrimitives.escapeHtml(value);
  const t = (value, values) => window.PortalI18n.t(value, values);
  const STATUS_LABELS = Object.freeze({
    not_started: "未开始", active: "进行中", paused: "已暂停", done: "已完成", archived: "已归档",
  });
  const TYPE_LABELS = Object.freeze({
    company: "公司项目", academic: "学术项目", personal: "个人项目", other: "其他",
  });
  const RESOURCE_LABELS = Object.freeze({
    decision: "Design", insight: "洞察", information: "资料", thinking: "思考", plan: "计划", idea: "想法",
    concept: "概念", question: "问题", material: "材料", todo: "待办", review: "评审", meeting: "会议",
    risk: "风险", patent: "专利", paper: "论文", repo: "仓库", experiment: "实验", skill: "Skill",
    article: "文章", workbench: "工作台", device: "设备", dataset: "数据集", model: "模型", test: "测试",
    document: "文档", chatroom: "讨论组", direction: "研究方向",
  });
  // Resource pages that filter by Project.
  const CATALOG_TYPES = new Set(["skill", "repo", "article", "experiment", "device"]);
  const DAY = 86_400_000;

  function knowledgeTypes() {
    return new Set((window.PortalResourceRoutes?.routes || [])
      .filter(route => route.detail_kind === "knowledge").map(route => route.resource_type));
  }

  function execute(name, args = {}, keyPrefix = "") {
    const options = keyPrefix ? { idempotencyKey: `${keyPrefix}:${window.PortalUIPrimitives.randomUUID()}` } : {};
    return window.PortalInstructionClient.execute(name, args, options);
  }

  const statusLabel = status => t(STATUS_LABELS[status] || status || "未设置");
  const typeLabel = type => (type ? t(TYPE_LABELS[type] || type) : "");
  const resourceLabel = type => t(RESOURCE_LABELS[type] || type);

  // "2026-08-30 23:59:59", "2026-08-30" or ISO; a date alone means the end of that day.
  function parseDeadline(value) {
    const raw = String(value || "").trim();
    if (!raw) return null;
    const dateOnly = /^\d{4}-\d{2}-\d{2}$/.test(raw);
    const date = new Date(dateOnly ? `${raw}T23:59:59` : raw.replace(" ", "T"));
    return Number.isNaN(date.getTime()) ? undefined : date;
  }

  function startOfDay(date) {
    return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
  }

  // {label, tone}: tone is "", "warning" or "danger"; no deadline gives an empty label.
  function formatDeadline(value, now = new Date()) {
    const date = parseDeadline(value);
    if (date === null) return { label: "", tone: "" };
    if (date === undefined) return { label: t("日期无效"), tone: "danger" };
    const days = Math.round((startOfDay(date) - startOfDay(now)) / DAY);
    if (days === 0) return { label: t("今天截止"), tone: "warning" };
    if (days > 0) {
      return { label: days === 1 ? t("还剩 1 天") : window.PortalI18n.text`还剩 ${days} 天`, tone: days <= 7 ? "warning" : "" };
    }
    const overdue = -days;
    return { label: overdue === 1 ? t("已逾期 1 天") : window.PortalI18n.text`已逾期 ${overdue} 天`, tone: "danger" };
  }

  function relativeTime(value, now = Date.now()) {
    const timestamp = new Date(String(value || "")).getTime();
    if (!Number.isFinite(timestamp)) return "";
    const seconds = Math.max(0, Math.floor((now - timestamp) / 1000));
    if (seconds < 60) return t("刚刚");
    if (seconds < 3600) return window.PortalI18n.text`${Math.floor(seconds / 60)} 分钟前`;
    if (seconds < 86_400) return window.PortalI18n.text`${Math.floor(seconds / 3600)} 小时前`;
    if (seconds < 30 * 86_400) return window.PortalI18n.text`${Math.floor(seconds / 86_400)} 天前`;
    return window.PortalUI.formatDateTime(timestamp, { format: { year: "numeric", month: "numeric", day: "numeric" } });
  }

  // 1 for a change just now, fading to 0 over about a month.
  function heat(value, now = Date.now()) {
    const timestamp = new Date(String(value || "")).getTime();
    if (!Number.isFinite(timestamp)) return 0;
    const hours = Math.max(0, (now - timestamp) / 3_600_000);
    return Math.max(0, Math.min(1, 1 - Math.log1p(hours) / Math.log1p(24 * 30)));
  }

  function hue(text) {
    let value = 0;
    for (const char of String(text || "")) value = (value * 31 + char.codePointAt(0)) % 360;
    return value;
  }

  // The saved avatar, or the name's initial on a colour of its own.
  function avatarHtml(name, avatar, size = 40) {
    if (avatar && window.PortalUserAvatar) {
      return `<span class="projectAvatar" style="--avatar-box:${size}px">${window.PortalUserAvatar.markup(avatar, { label: name, size })}</span>`;
    }
    const initial = Array.from(String(name || "?"))[0]?.toUpperCase() || "?";
    return `<span class="projectAvatar is-initial" style="--avatar-box:${size}px;--avatar-hue:${hue(name)}" aria-hidden="true">${esc(initial)}</span>`;
  }

  function projectHref(projectId) {
    return `/projects?project=${encodeURIComponent(projectId)}`;
  }

  // The page listing a Project's resources of one type, or "" when no page filters by Project.
  function resourcesHref(type, projectId) {
    const project = encodeURIComponent(projectId);
    if (knowledgeTypes().has(type)) return `/resource/knowledge?type=${encodeURIComponent(type)}&project=${project}`;
    if (CATALOG_TYPES.has(type)) return `/resource/${type}?project=${project}`;
    if (type === "workbench") return `/workbenches?project=${project}`;
    return "";
  }

  function lineageHref(lineageId) {
    return `/lineage?lineage=${encodeURIComponent(lineageId)}`;
  }

  // A link the page follows itself unless the user asks for a new tab.
  function plainClick(event) {
    return event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey;
  }

  window.PortalProjectCommon = Object.freeze({
    STATUS_LABELS, TYPE_LABELS, RESOURCE_LABELS, esc, t, execute, statusLabel, typeLabel, resourceLabel,
    knowledgeTypes, parseDeadline, formatDeadline, relativeTime, heat, avatarHtml, projectHref, resourcesHref,
    lineageHref, plainClick,
  });
})();
