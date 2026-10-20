(function (global) {
  "use strict";

  if (global.PortalResourceHeader) return;

  const elementOf = ref => window.PortalUIPrimitives.elementOf(ref);

  const escapeHtml = global.PortalUIPrimitives.escapeHtml;

  function resourceTypeLabel(value = "", resourceId = "") {
    const raw = String(value || "").trim();
    const inferred = String(resourceId || "").trim().split("_", 1)[0];
    const key = (raw || inferred).toLowerCase().replace(/[\s-]+/g, "_");
    const known = {
      article: window.PortalI18n.t("Article"),
      decision: window.PortalI18n.t("Decision"),
      device: window.PortalI18n.t("Device"),
      experiment: window.PortalI18n.t("Experiment"),
      idea: window.PortalI18n.t("Idea"),
      information: window.PortalI18n.t("Information"),
      insight: window.PortalI18n.t("Insight"),
      issue: window.PortalI18n.t("Issue"),
      knowledge: window.PortalI18n.t("Knowledge"),
      lineage: "Lineage",
      paper: window.PortalI18n.t("Paper"),
      repo: window.PortalI18n.t("Repo"),
      resident: "Resident",
      review: window.PortalI18n.t("Review"),
      search: window.PortalI18n.t("Search"),
      skill: window.PortalI18n.t("Skill"),
      task: "TODO",
      thinking: window.PortalI18n.t("Thinking"),
      todo: "TODO",
      workspace: window.PortalI18n.t("Workspace"),
    };
    if (known[key]) return known[key];
    return (raw || inferred)
      .split(/[\s_-]+/)
      .filter(Boolean)
      .map(part => `${part.charAt(0).toUpperCase()}${part.slice(1)}`)
      .join(" ");
  }

  function resourceCopyLabel(resourceId = "", explicit = "") {
    if (String(explicit || "").trim()) return String(explicit).trim();
    const prefix = String(resourceId || "").trim().split("_", 1)[0];
    if (!prefix) return window.PortalI18n.t("Resource ID");
    if (["thinking", "decision", "idea", "insight", "information"].includes(prefix)) return "kb_id";
    return `${prefix}_id`;
  }

  function identityHtml(identity = {}, options = {}) {
    const title = String(identity.title || "").trim() || window.PortalI18n.t("Untitled");
    const resourceId = String(identity.resource_id || identity.resourceId || identity.id || "").trim();
    const type = resourceTypeLabel(identity.type || identity.resource_type || "", resourceId);
    const headingTag = ["h1", "h2", "h3", "div", "strong", "span"].includes(String(options.headingTag || "").toLowerCase())
      ? String(options.headingTag).toLowerCase()
      : "div";
    const titleId = String(options.titleId || "").trim();
    const typeId = String(options.typeId || "").trim();
    const idId = String(options.idId || "").trim();
    const titleClass = String(options.titleClass || "").trim();
    const copyLabel = resourceCopyLabel(resourceId, identity.copy_label || identity.copyLabel);
    const typeHtml = type
      ? window.PortalI18n.html`<span class="portalResourceIdentitySeparator" aria-hidden="true">|</span><span class="portalResourceIdentityType"${typeId ? ` id="${escapeHtml(typeId)}"` : ""}>${escapeHtml(type)}</span>`
      : "";
    const idHtml = resourceId
      ? window.PortalI18n.html`<div class="portalResourceIdentityId"${idId ? ` id="${escapeHtml(idId)}"` : ""} data-portal-resource-id="${escapeHtml(resourceId)}" data-portal-resource-copy-label="${escapeHtml(copyLabel)}" title="双击复制 ${escapeHtml(copyLabel)}">${escapeHtml(resourceId)}</div>`
      : "";
    return window.PortalI18n.html`<div class="portalResourceIdentity${options.compact ? " compact" : ""}" data-portal-resource-identity>
      <div class="portalResourceIdentityTitleRow">
        <${headingTag} class="portalResourceIdentityTitle${titleClass ? ` ${escapeHtml(titleClass)}` : ""}"${titleId ? ` id="${escapeHtml(titleId)}"` : ""} title="${escapeHtml(title)}">${escapeHtml(title)}</${headingTag}>
        ${typeHtml}
      </div>
      ${idHtml}
    </div>`;
  }

  function mount(rootRef, identity = {}, options = {}) {
    const root = elementOf(rootRef);
    if (!root) return null;
    root.innerHTML = identityHtml(identity, options);
    return root.querySelector("[data-portal-resource-identity]");
  }

  document.addEventListener("dblclick", event => {
    const target = event.target instanceof Element
      ? event.target.closest("[data-portal-resource-id]")
      : null;
    if (!target) return;
    const value = String(target.dataset.portalResourceId || "").trim();
    if (!value) return;
    event.preventDefault();
    event.stopPropagation();
    const label = String(target.dataset.portalResourceCopyLabel || window.PortalI18n.t("Resource ID")).trim() || window.PortalI18n.t("Resource ID");
    global.PortalUI?.copyText?.(value, label);
  });

  global.PortalResourceHeader = Object.freeze({
    identityHtml,
    mount,
  });
})(window);
