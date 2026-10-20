(function (global) {
  "use strict";
  const esc = global.PortalUIPrimitives.escapeHtml;
  const text = (value, fallback = "") => String(value ?? "").trim() || fallback;
  const array = value => Array.isArray(value) ? value : [];
  const object = global.PortalUIPrimitives.objectOf;

  function metricHtml(label, value, options = {}) {
    const display = text(value, "—");
    return global.PortalI18n.html`<div class="${options.compact ? "projectBriefSignal" : "projectPageMetric"}" data-tone="${esc(options.tone || "neutral")}">
      <strong title="${esc(options.title || display)}">${esc(display)}</strong><span>${esc(label)}</span>
    </div>`;
  }

  function cardHtml(label, value, kind, options = {}) {
    const content = esc(text(value, global.PortalI18n.t("Project State 尚未给出这一信息。")));
    return global.PortalI18n.html`<article class="${options.compact ? "projectBriefItem" : "projectOverviewCard"}" data-kind="${esc(kind)}">
      <span>${esc(label)}</span>${options.compact ? `<p>${content}</p>` : `<h4>${content}</h4>`}
      ${options.note ? `<p>${esc(options.note)}</p>` : ""}${options.action || ""}
    </article>`;
  }

  global.PortalProjectSummary = Object.freeze({text, array, object, metricHtml, cardHtml});
})(window);
